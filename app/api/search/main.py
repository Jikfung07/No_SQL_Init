import asyncio
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from typing import Any

import redis
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("search-service")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
EVENT_STREAM = "domain-events"
DEAD_LETTER_STREAM = "domain-events:dead-letter"
CONSUMER_GROUP = "search-projection"
CONSUMER_NAME = os.getenv("CONSUMER_NAME", socket.gethostname())
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)

APPLY_LISTING_EVENT_SCRIPT = """
local current_version = redis.call('HGET', KEYS[1], ARGV[1])
if current_version and tonumber(current_version) >= tonumber(ARGV[2]) then
    return 0
end
local previous_city = redis.call('HGET', KEYS[2], ARGV[1])
if previous_city then
    redis.call('SREM', 'projection:city:' .. previous_city, ARGV[1])
end
local listing = cjson.decode(ARGV[3])
if listing['status'] == 'REMOVED' then
    redis.call('DEL', 'projection:listing:' .. ARGV[1])
    redis.call('HDEL', KEYS[2], ARGV[1])
else
    redis.call('SET', 'projection:listing:' .. ARGV[1], ARGV[3])
    redis.call('HSET', KEYS[2], ARGV[1], ARGV[4])
    redis.call('SADD', 'projection:city:' .. ARGV[4], ARGV[1])
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 1
"""

consumer_task: asyncio.Task[None] | None = None


def decode_message(fields: dict[str, str]) -> dict[str, Any]:
    return json.loads(fields["payload"])


def project_event(event: dict[str, Any]) -> None:
    event_type = event.get("eventType")
    if event_type not in {"ListingPublished", "ListingUpdated", "ListingDeleted"}:
        return
    payload = event.get("payload")
    listing_id = event.get("aggregateId")
    version = event.get("aggregateVersion")
    if not isinstance(payload, dict) or not listing_id or not isinstance(version, int):
        raise ValueError("Événement d'annonce invalide: payload ou version manquante")
    redis_client.eval(
        APPLY_LISTING_EVENT_SCRIPT,
        2,
        "projection:versions",
        "projection:listing-cities",
        listing_id,
        version,
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        payload.get("city", "").casefold(),
    )


def ensure_consumer_group() -> None:
    try:
        redis_client.xgroup_create(EVENT_STREAM, CONSUMER_GROUP, id="0-0", mkstream=True)
    except redis.exceptions.ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


async def handle_entries(entries: list[tuple[str, dict[str, str]]]) -> None:
    for stream_id, fields in entries:
        try:
            event = decode_message(fields)
            project_event(event)
            redis_client.xack(EVENT_STREAM, CONSUMER_GROUP, stream_id)
        except Exception as error:
            logger.exception("Échec du traitement de l'événement %s; envoi en file d'erreur", stream_id)
            redis_client.xadd(
                DEAD_LETTER_STREAM,
                {"sourceStreamId": stream_id, "error": str(error), "payload": fields.get("payload", "")},
            )
            redis_client.xack(EVENT_STREAM, CONSUMER_GROUP, stream_id)


async def consume_forever() -> None:
    ensure_consumer_group()
    while True:
        try:
            claimed = await asyncio.to_thread(
                redis_client.xautoclaim,
                EVENT_STREAM,
                CONSUMER_GROUP,
                CONSUMER_NAME,
                30000,
                "0-0",
                10,
            )
            await handle_entries(claimed[1])
            batches = await asyncio.to_thread(
                redis_client.xreadgroup,
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {EVENT_STREAM: ">"},
                10,
                1000,
            )
            for _, entries in batches:
                await handle_entries(entries)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Consommateur d'événements indisponible; nouvel essai dans 2 secondes")
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global consumer_task
    redis_client.ping()
    ensure_consumer_group()
    consumer_task = asyncio.create_task(consume_forever())
    yield
    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
    redis_client.close()


app = FastAPI(
    title="Service Recherche",
    description="Projection Redis des annonces et statistiques de recherche.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def demo_page() -> FileResponse:
    return FileResponse("search/index.html")


@app.get("/health")
def health() -> dict[str, str]:
    redis_client.ping()
    pending = redis_client.xpending(EVENT_STREAM, CONSUMER_GROUP)
    return {"status": "ok", "service": "recherche", "pendingEvents": str(pending["pending"])}


@app.get("/search")
def search_listings(
    city: str | None = None,
    guests: int = Query(default=1, ge=1, le=50),
    min_price: float | None = Query(default=None, alias="minPrice", ge=0),
    max_price: float | None = Query(default=None, alias="maxPrice", ge=0),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=400, detail="minPrice doit être inférieur ou égal à maxPrice")
    normalized_city = city.strip().casefold() if city and city.strip() else None
    listing_ids = (
        redis_client.smembers(f"projection:city:{normalized_city}")
        if normalized_city
        else redis_client.hkeys("projection:listing-cities")
    )

    listings: list[dict[str, Any]] = []
    term = q.strip().casefold() if q and q.strip() else None
    for listing_id in listing_ids:
        raw = redis_client.get(f"projection:listing:{listing_id}")
        if not raw:
            continue
        listing = json.loads(raw)
        if listing.get("status") != "PUBLISHED" or listing.get("guests", 1) < guests:
            continue
        price = float(listing.get("nightlyPrice", 0))
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        if term and term not in (listing.get("title", "") + " " + listing.get("description", "")).casefold():
            continue
        listings.append(listing)

    listings.sort(key=lambda listing: listing.get("createdAt", ""), reverse=True)
    stats_field = normalized_city or "all"
    redis_client.hincrby("aggregate:search-queries", stats_field, 1)
    return {
        "source": "Redis projection",
        "count": len(listings[:limit]),
        "results": listings[:limit],
    }


@app.get("/stats")
def search_stats() -> dict[str, Any]:
    counters = redis_client.hgetall("aggregate:search-queries")
    return {
        "aggregate": "SearchQueryStats",
        "database": "Redis",
        "queriesByCity": {city: int(count) for city, count in counters.items()},
        "totalQueries": sum(int(count) for count in counters.values()),
    }


@app.get("/projection")
def get_projection() -> dict[str, Any]:
    listing_ids = redis_client.hkeys("projection:listing-cities")
    records = []
    for listing_id in listing_ids:
        raw = redis_client.get(f"projection:listing:{listing_id}")
        if raw:
            records.append(json.loads(raw))
    return {"source": "Redis projection", "count": len(records), "listings": records}


@app.get("/events")
def get_consumed_events() -> list[dict[str, Any]]:
    entries = redis_client.xrevrange(EVENT_STREAM, count=20)
    return [
        {"streamId": stream_id, **decode_message(fields)}
        for stream_id, fields in entries
    ]
