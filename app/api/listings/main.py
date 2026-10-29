import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any

import redis
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("listings-service")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "rental")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
EVENT_STREAM = "domain-events"

mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
event_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
database = mongo[MONGO_DATABASE]
listings: Collection = database["listings"]
reservations: Collection = database["reservations"]
publisher_stop = threading.Event()
publisher_thread: threading.Thread | None = None

PUBLISH_EVENT_SCRIPT = """
local known = redis.call('HGET', KEYS[2], ARGV[1])
if known then
    return known
end
local stream_id = redis.call('XADD', KEYS[1], '*', 'eventId', ARGV[1], 'payload', ARGV[2])
redis.call('HSET', KEYS[2], ARGV[1], stream_id)
return stream_id
"""


class ListingInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=10, max_length=3000)
    city: str = Field(min_length=2, max_length=100)
    country_code: str = Field(default="FR", alias="countryCode", min_length=2, max_length=2)
    host_id: str = Field(default="usr_demo_host", alias="hostId", min_length=1, max_length=100)
    nightly_price: float = Field(alias="nightlyPrice", gt=0, le=100000)
    guests: int = Field(default=2, ge=1, le=50)
    image_url: str | None = Field(default=None, alias="imageUrl", max_length=1000)


class ListingPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=4, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=3000)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    country_code: str | None = Field(default=None, alias="countryCode", min_length=2, max_length=2)
    nightly_price: float | None = Field(default=None, alias="nightlyPrice", gt=0, le=100000)
    guests: int | None = Field(default=None, ge=1, le=50)
    image_url: str | None = Field(default=None, alias="imageUrl", max_length=1000)


class ReservationInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    listing_id: str = Field(alias="listingId", min_length=1)
    guest_id: str = Field(default="usr_demo_guest", alias="guestId", min_length=1)
    check_in: date = Field(alias="checkIn")
    check_out: date = Field(alias="checkOut")
    guest_count: int = Field(default=1, alias="guestCount", ge=1, le=50)

    @model_validator(mode="after")
    def check_dates(self) -> "ReservationInput":
        if self.check_out <= self.check_in:
            raise ValueError("checkOut doit être après checkIn")
        return self


class ReservationStatusInput(BaseModel):
    status: str

    @model_validator(mode="after")
    def validate_status(self) -> "ReservationStatusInput":
        if self.status not in {"CONFIRMED", "REJECTED", "CANCELLED"}:
            raise ValueError("status doit être CONFIRMED, REJECTED ou CANCELLED")
        return self


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result.pop("_id", None)
    result.pop("pendingEvents", None)
    return result


def make_event(event_type: str, aggregate_type: str, aggregate_id: str, version: int,
               payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "eventVersion": 1,
        "occurredAt": now_iso(),
        "aggregateType": aggregate_type,
        "aggregateId": aggregate_id,
        "aggregateVersion": version,
        "payload": payload,
    }


def enqueue_event(collection: Collection, selector: dict[str, Any],
                  event: dict[str, Any]) -> None:
    result = collection.update_one(selector, {"$push": {"pendingEvents": event}})
    if result.modified_count != 1:
        raise RuntimeError(f"Impossible d'enregistrer l'événement {event['eventId']} dans l'outbox")


def publish_outbox_once() -> None:
    event_client.ping()
    for collection in (listings, reservations):
        for document in collection.find({"pendingEvents.0": {"$exists": True}}).limit(100):
            for event in document.get("pendingEvents", []):
                event_client.eval(
                    PUBLISH_EVENT_SCRIPT,
                    2,
                    EVENT_STREAM,
                    "published-event-ids",
                    event["eventId"],
                    json.dumps(event, separators=(",", ":"), ensure_ascii=False),
                )
                collection.update_one(
                    {"_id": document["_id"]},
                    {"$pull": {"pendingEvents": {"eventId": event["eventId"]}}},
                )


def publish_outbox_forever() -> None:
    while not publisher_stop.is_set():
        try:
            publish_outbox_once()
        except Exception:
            logger.exception("Publication de l'outbox impossible; nouvel essai dans 2 secondes")
        publisher_stop.wait(2)


def create_listing(payload: dict[str, Any]) -> dict[str, Any]:
    listing_id = f"lst_{uuid.uuid4().hex[:12]}"
    document = {
        "listingId": listing_id,
        **payload,
        "currency": "EUR",
        "status": "PUBLISHED",
        "createdAt": now_iso(),
        "updatedAt": None,
        "version": 1,
        "pendingEvents": [],
    }
    event = make_event("ListingPublished", "Listing", listing_id, 1, public_document(document))
    document["pendingEvents"].append(event)
    listings.insert_one(document)
    return public_document(document)


def seed_demo_listing() -> None:
    if listings.count_documents({}) > 0:
        return
    create_listing({
        "title": "Studio lumineux près du canal",
        "description": "Un studio calme et équipé, à quelques minutes des transports.",
        "city": "Paris",
        "countryCode": "FR",
        "hostId": "usr_demo_host",
        "nightlyPrice": 95.0,
        "guests": 2,
        "imageUrl": None,
    })


@asynccontextmanager
async def lifespan(_: FastAPI):
    global publisher_thread
    mongo.admin.command("ping")
    event_client.ping()
    listings.create_index("listingId", unique=True)
    listings.create_index([("city", 1), ("status", 1)])
    reservations.create_index("reservationId", unique=True)
    reservations.create_index([("guestId", 1), ("createdAt", -1)])
    seed_demo_listing()
    publisher_stop.clear()
    publisher_thread = threading.Thread(target=publish_outbox_forever, daemon=True)
    publisher_thread.start()
    yield
    publisher_stop.set()
    if publisher_thread:
        publisher_thread.join(timeout=5)
    event_client.close()
    mongo.close()


app = FastAPI(
    title="Service Locations",
    description="Source de vérité MongoDB pour les annonces et réservations.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    mongo.admin.command("ping")
    event_client.ping()
    return {"status": "ok", "service": "locations"}


@app.get("/listings")
def get_listings(response: Response, limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    response.headers["X-Data-Source"] = "MongoDB"
    return [public_document(item) for item in listings.find({"status": "PUBLISHED"}).sort("createdAt", -1).limit(limit)]


@app.post("/listings", status_code=201)
def post_listing(payload: ListingInput) -> dict[str, Any]:
    try:
        return create_listing(payload.model_dump(by_alias=True))
    except Exception as error:
        logger.exception("Création d'annonce impossible")
        raise HTTPException(status_code=503, detail="Création indisponible") from error


@app.get("/listings/{listing_id}")
def get_listing(listing_id: str) -> dict[str, Any]:
    document = listings.find_one({"listingId": listing_id, "status": "PUBLISHED"})
    if not document:
        raise HTTPException(status_code=404, detail="Annonce introuvable")
    return public_document(document)


@app.patch("/listings/{listing_id}")
def patch_listing(listing_id: str, payload: ListingPatch) -> dict[str, Any]:
    changes = payload.model_dump(by_alias=True, exclude_none=True, exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Aucun champ à modifier")

    current = listings.find_one({"listingId": listing_id, "status": "PUBLISHED"})
    if not current:
        raise HTTPException(status_code=404, detail="Annonce introuvable")

    next_version = current["version"] + 1
    snapshot = {**public_document(current), **changes, "version": next_version, "updatedAt": now_iso()}
    event = make_event("ListingUpdated", "Listing", listing_id, next_version, snapshot)
    updated = listings.find_one_and_update(
        {"listingId": listing_id, "version": current["version"], "status": "PUBLISHED"},
        {"$set": {**changes, "updatedAt": event["occurredAt"]},
         "$inc": {"version": 1},
         "$push": {"pendingEvents": event}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="L'annonce a été modifiée simultanément; réessayez")
    return public_document(updated)


@app.delete("/listings/{listing_id}", status_code=204)
def delete_listing(listing_id: str) -> Response:
    current = listings.find_one({"listingId": listing_id, "status": "PUBLISHED"})
    if not current:
        raise HTTPException(status_code=404, detail="Annonce introuvable")

    next_version = current["version"] + 1
    snapshot = {**public_document(current), "status": "REMOVED", "version": next_version}
    event = make_event("ListingDeleted", "Listing", listing_id, next_version, snapshot)
    updated = listings.find_one_and_update(
        {"listingId": listing_id, "version": current["version"], "status": "PUBLISHED"},
        {"$set": {"status": "REMOVED", "updatedAt": event["occurredAt"]},
         "$inc": {"version": 1},
         "$push": {"pendingEvents": event}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="L'annonce a été modifiée simultanément; réessayez")
    return Response(status_code=204)


@app.get("/reservations")
def get_reservations(guest_id: str | None = None, host_id: str | None = None) -> list[dict[str, Any]]:
    selector: dict[str, Any] = {}
    if guest_id:
        selector["guestId"] = guest_id
    if host_id:
        selector["hostId"] = host_id
    return [public_document(item) for item in reservations.find(selector).sort("createdAt", -1).limit(200)]


@app.post("/reservations", status_code=201)
def post_reservation(payload: ReservationInput) -> dict[str, Any]:
    listing = listings.find_one({"listingId": payload.listing_id, "status": "PUBLISHED"})
    if not listing:
        raise HTTPException(status_code=404, detail="Annonce introuvable")
    if payload.guest_count > listing["guests"]:
        raise HTTPException(status_code=400, detail="Le nombre de voyageurs dépasse la capacité de l'annonce")

    nights = (payload.check_out - payload.check_in).days
    reservation_id = f"res_{uuid.uuid4().hex[:12]}"
    document = {
        "reservationId": reservation_id,
        "listingId": payload.listing_id,
        "listingTitle": listing["title"],
        "hostId": listing["hostId"],
        "guestId": payload.guest_id,
        "checkIn": payload.check_in.isoformat(),
        "checkOut": payload.check_out.isoformat(),
        "guestCount": payload.guest_count,
        "total": round(nights * listing["nightlyPrice"], 2),
        "currency": listing["currency"],
        "status": "PENDING_HOST",
        "createdAt": now_iso(),
        "version": 1,
        "pendingEvents": [],
    }
    event = make_event("ReservationRequested", "Reservation", reservation_id, 1, public_document(document))
    document["pendingEvents"].append(event)
    reservations.insert_one(document)
    return public_document(document)


@app.patch("/reservations/{reservation_id}/status")
def update_reservation_status(reservation_id: str, payload: ReservationStatusInput) -> dict[str, Any]:
    current = reservations.find_one({"reservationId": reservation_id})
    if not current:
        raise HTTPException(status_code=404, detail="Réservation introuvable")
    if current["status"] != "PENDING_HOST":
        raise HTTPException(status_code=409, detail="Seule une demande en attente peut changer de statut")

    version = current["version"] + 1
    snapshot = {**public_document(current), "status": payload.status, "version": version}
    event = make_event(f"Reservation{payload.status.title()}", "Reservation", reservation_id, version, snapshot)
    updated = reservations.find_one_and_update(
        {"reservationId": reservation_id, "version": current["version"], "status": "PENDING_HOST"},
        {"$set": {"status": payload.status},
         "$inc": {"version": 1},
         "$push": {"pendingEvents": event}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="La réservation a changé simultanément")
    return public_document(updated)


@app.get("/events")
def get_published_events() -> list[dict[str, Any]]:
    try:
        entries = event_client.xrevrange(EVENT_STREAM, count=20)
    except redis.exceptions.ResponseError as error:
        if "no such key" in str(error).lower():
            return []
        raise HTTPException(status_code=503, detail="Flux d'événements indisponible") from error
    return [
        {"streamId": stream_id, **json.loads(fields["payload"])}
        for stream_id, fields in entries
    ]
