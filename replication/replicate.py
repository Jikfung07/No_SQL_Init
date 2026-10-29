#!/usr/bin/env python3
"""
Réplication des réservations dans Redis (Personne 4).

Le service « Transport » ne lit jamais la base MongoDB des réservations :
il garde sa propre copie (le réplica) dans Redis et la met à jour
uniquement en écoutant les événements publiés par l'autre service.

Deux façons de recevoir les événements :
  --source file    rejoue events_demo.json (démo autonome, pas besoin des autres)
  --source stream  lit le vrai Redis Stream (quand la Personne 3 a fini)

Exemples :
  python replicate.py --source file --fake          # démo sans rien installer d'autre que Python
  python replicate.py --source file                 # démo avec un vrai Redis local
  python replicate.py --source stream               # branché sur le stream de l'équipe
  python replicate.py --show                        # affiche le réplica
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import redis

log = logging.getLogger("replication")

# --- Clés Redis du réplica -------------------------------------------------
RESERVATION_KEY = "replica:reservation:{}"      # un hash par réservation
RESERVATION_INDEX = "replica:reservations"      # set de tous les reservationId
PROCESSED_EVENTS = "replica:events:processed"   # set des eventId déjà traités
DEAD_LETTER = "replica:dead-letter"             # liste des événements inexploitables

STREAM_NAME = os.getenv("EVENT_STREAM", "domain-events")
GROUP_NAME = os.getenv("REPLICATION_GROUP", "replication-reservations")
CONSUMER_NAME = os.getenv("REPLICATION_CONSUMER", "replicator-1")

# Ce que chaque type d'événement change dans le réplica.
# ReservationEffectuee crée la copie, les autres la mettent à jour.
EVENT_EFFECTS = {
    "ReservationEffectuee": {"status": "CONFIRMED_BY_HOST"},
    "TaxiReserve": {"taxiStatus": "RESERVED"},
    "TaxiConfirme": {"taxiStatus": "CONFIRMED"},
    "TaxiAnnule": {"taxiStatus": "CANCELLED"},
}
CREATING_EVENTS = {"ReservationEffectuee"}
REQUIRED_FIELDS = ("eventId", "type", "reservationId", "version")


def get_client(fake: bool = False):
    """Client Redis : vrai serveur, ou faux (en mémoire) pour tester seul."""
    if fake:
        import fakeredis  # pip install fakeredis
        return fakeredis.FakeRedis(decode_responses=True)
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _to_str(value):
    """Redis ne stocke que des chaînes : on convertit proprement."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def apply_event(r, event: dict) -> str:
    """
    Applique UN événement au réplica. Retourne ce qui s'est passé :
    applied | duplicate | stale | orphan | invalid
    Rejouer deux fois le même événement ne change rien (idempotent).
    """
    missing = [f for f in REQUIRED_FIELDS if f not in event]
    if missing or event.get("type") not in EVENT_EFFECTS:
        reason = f"champs manquants {missing}" if missing else f"type inconnu {event.get('type')}"
        r.rpush(DEAD_LETTER, json.dumps({"event": event, "reason": reason}, ensure_ascii=False))
        log.warning("Événement invalide mis de côté : %s", reason)
        return "invalid"

    event_id, etype = event["eventId"], event["type"]
    key = RESERVATION_KEY.format(event["reservationId"])

    # 1) Déjà vu ? On ignore (le broker peut livrer deux fois).
    if r.sismember(PROCESSED_EVENTS, event_id):
        log.info("[%s] %s déjà traité, ignoré", event_id, etype)
        return "duplicate"

    current = r.hgetall(key)

    # 2) Une mise à jour qui arrive avant la création : on ne peut rien en faire.
    if not current and etype not in CREATING_EVENTS:
        r.rpush(DEAD_LETTER, json.dumps(
            {"event": event, "reason": "réservation inconnue dans le réplica"}, ensure_ascii=False))
        log.warning("[%s] %s reçu mais la réservation %s n'existe pas encore",
                    event_id, etype, event["reservationId"])
        return "orphan"

    # 3) Événement en retard (version plus ancienne que celle qu'on a déjà) : on ignore.
    if current and int(event["version"]) <= int(current.get("version", 0)):
        log.info("[%s] %s en retard (v%s <= v%s), ignoré",
                 event_id, etype, event["version"], current.get("version"))
        r.sadd(PROCESSED_EVENTS, event_id)
        return "stale"

    # 4) On construit les nouvelles valeurs : données de l'événement + effet du type.
    changes = {k: _to_str(v) for k, v in (event.get("data") or {}).items()}
    changes.update(EVENT_EFFECTS[etype])
    changes.update({
        "reservationId": event["reservationId"],
        "version": _to_str(event["version"]),
        "lastEventId": event_id,
        "lastEventType": etype,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    # 5) Écriture atomique : la copie et le « déjà traité » bougent ensemble.
    pipe = r.pipeline(transaction=True)
    pipe.hset(key, mapping=changes)
    pipe.sadd(RESERVATION_INDEX, event["reservationId"])
    pipe.sadd(PROCESSED_EVENTS, event_id)
    pipe.execute()
    log.info("[%s] %s appliqué sur %s (v%s)", event_id, etype, event["reservationId"], event["version"])
    return "applied"


# --- Source 1 : fichier (démo autonome) ------------------------------------
def replay_file(r, path: Path) -> dict:
    events = json.loads(path.read_text(encoding="utf-8"))
    stats = {}
    for event in events:
        outcome = apply_event(r, event)
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats


# --- Source 2 : Redis Stream (intégration) ---------------------------------
def _decode_stream_message(fields: dict) -> dict:
    """
    Le stream de l'équipe peut contenir soit un champ 'payload' (JSON complet),
    soit les champs de l'événement à plat. On gère les deux ;
    à ajuster quand la Personne 3 aura figé son format.
    """
    if "payload" in fields:
        return json.loads(fields["payload"])
    event = dict(fields)
    if isinstance(event.get("data"), str):
        event["data"] = json.loads(event["data"])
    return event


def consume_stream(r, once: bool = False, block_ms: int = 5000):
    try:
        r.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    log.info("En écoute sur '%s' (groupe %s). Ctrl+C pour arrêter.", STREAM_NAME, GROUP_NAME)
    while True:
        batches = r.xreadgroup(GROUP_NAME, CONSUMER_NAME, {STREAM_NAME: ">"}, count=20, block=block_ms)
        if not batches:
            if once:
                return
            continue
        for _stream, messages in batches:
            for msg_id, fields in messages:
                try:
                    event = _decode_stream_message(fields)
                except (ValueError, TypeError):
                    r.rpush(DEAD_LETTER, json.dumps({"raw": fields, "reason": "JSON illisible"}))
                    r.xack(STREAM_NAME, GROUP_NAME, msg_id)
                    continue
                if event.get("type") in EVENT_EFFECTS:
                    apply_event(r, event)
                # On acquitte seulement après l'écriture : si on plante avant, le message revient.
                r.xack(STREAM_NAME, GROUP_NAME, msg_id)


# --- Affichage -------------------------------------------------------------
def show_replica(r):
    ids = sorted(r.smembers(RESERVATION_INDEX))
    if not ids:
        print("Le réplica est vide.")
        return
    for rid in ids:
        print(f"\n● {rid}")
        for k, v in sorted(r.hgetall(RESERVATION_KEY.format(rid)).items()):
            print(f"    {k:<14} {v}")
    print(f"\n{len(ids)} réservation(s) répliquée(s), "
          f"{r.scard(PROCESSED_EVENTS)} événement(s) traité(s), "
          f"{r.llen(DEAD_LETTER)} en dead-letter.")


def main():
    p = argparse.ArgumentParser(description="Réplication des réservations dans Redis")
    p.add_argument("--source", choices=["file", "stream"], default="file")
    p.add_argument("--file", default=str(Path(__file__).with_name("events_demo.json")))
    p.add_argument("--fake", action="store_true", help="Redis en mémoire (fakeredis), sans serveur")
    p.add_argument("--reset", action="store_true", help="Vide le réplica avant de commencer")
    p.add_argument("--show", action="store_true", help="Affiche le réplica et quitte")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    r = get_client(args.fake)

    if args.reset:
        for k in r.scan_iter("replica:*"):
            r.delete(k)
    if args.show:
        show_replica(r)
        return

    if args.source == "file":
        stats = replay_file(r, Path(args.file))
        print("\nBilan :", stats)
        show_replica(r)
    else:
        try:
            consume_stream(r)
        except KeyboardInterrupt:
            print("\nArrêt demandé, à bientôt.")


if __name__ == "__main__":
    sys.exit(main())
