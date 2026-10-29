import json
import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from listings.main import (
    ListingInput,
    ReservationInput,
    make_event,
    public_document,
    publish_outbox_once,
)
from pydantic import ValidationError
from search.main import project_event, search_listings


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def limit(self, _: int) -> "FakeCursor":
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeCollection:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.updates: list[tuple[dict, dict]] = []

    def find(self, _: dict) -> FakeCursor:
        return FakeCursor(self.documents)

    def update_one(self, selector: dict, update: dict) -> None:
        self.updates.append((selector, update))


class EventContractTests(unittest.TestCase):
    def test_listing_accepts_api_field_names(self) -> None:
        listing = ListingInput.model_validate({
            "title": "Appartement calme",
            "description": "Logement lumineux près des transports.",
            "city": "Lyon",
            "hostId": "host-1",
            "nightlyPrice": 82,
            "guests": 3,
        })

        payload = listing.model_dump(by_alias=True)
        self.assertEqual(payload["hostId"], "host-1")
        self.assertEqual(payload["nightlyPrice"], 82)
        self.assertEqual(payload["countryCode"], "FR")

    def test_listing_rejects_non_positive_price(self) -> None:
        with self.assertRaises(ValidationError):
            ListingInput.model_validate({
                "title": "Appartement calme",
                "description": "Logement lumineux près des transports.",
                "city": "Lyon",
                "nightlyPrice": 0,
            })

    def test_reservation_rejects_checkout_before_checkin(self) -> None:
        with self.assertRaises(ValidationError):
            ReservationInput.model_validate({
                "listingId": "listing-1",
                "checkIn": "2026-10-25",
                "checkOut": "2026-10-24",
            })

    def test_event_has_versioned_aggregate_envelope(self) -> None:
        event = make_event("ListingPublished", "Listing", "listing-1", 1, {"city": "Lyon"})

        self.assertTrue(event["eventId"])
        self.assertEqual(event["eventType"], "ListingPublished")
        self.assertEqual(event["aggregateId"], "listing-1")
        self.assertEqual(event["aggregateVersion"], 1)
        self.assertEqual(event["payload"], {"city": "Lyon"})

    def test_public_document_hides_mongo_and_outbox_fields(self) -> None:
        document = public_document({"_id": "mongo-id", "listingId": "listing-1", "pendingEvents": [1]})

        self.assertEqual(document, {"listingId": "listing-1"})

    def test_outbox_publishes_and_removes_only_the_sent_event(self) -> None:
        event = make_event("ListingPublished", "Listing", "listing-1", 1, {"city": "Lyon"})
        listing_collection = FakeCollection([{
            "_id": "mongo-id",
            "pendingEvents": [event],
        }])
        reservation_collection = FakeCollection([])
        redis_spy = Mock()

        with (
            patch("listings.main.event_client", redis_spy),
            patch("listings.main.listings", listing_collection),
            patch("listings.main.reservations", reservation_collection),
        ):
            publish_outbox_once()

        redis_spy.ping.assert_called_once()
        self.assertEqual(redis_spy.eval.call_args.args[1:4], (
            2,
            "domain-events",
            "published-event-ids",
        ))
        self.assertIn(event["eventId"], redis_spy.eval.call_args.args[4])
        self.assertEqual(
            listing_collection.updates[0][1],
            {"$pull": {"pendingEvents": {"eventId": event["eventId"]}}},
        )

    def test_projection_consumer_passes_versioned_listing_to_redis(self) -> None:
        redis_spy = Mock()
        event = {
            "eventType": "ListingUpdated",
            "aggregateId": "listing-1",
            "aggregateVersion": 2,
            "payload": {"listingId": "listing-1", "city": "Évry", "status": "PUBLISHED"},
        }

        with patch("search.main.redis_client", redis_spy):
            project_event(event)

        arguments = redis_spy.eval.call_args.args
        self.assertEqual(arguments[:5], (
            ANY,
            2,
            "projection:versions",
            "projection:listing-cities",
            "listing-1",
        ))
        self.assertEqual(arguments[5], 2)
        self.assertEqual(arguments[7], "évry")

    def test_projection_ignores_non_listing_events(self) -> None:
        redis_spy = Mock()

        with patch("search.main.redis_client", redis_spy):
            project_event({
                "eventType": "ReservationRequested",
                "aggregateId": "reservation-1",
                "aggregateVersion": 1,
                "payload": {},
            })

        redis_spy.eval.assert_not_called()

    def test_search_reads_city_projection_and_updates_aggregate(self) -> None:
        redis_spy = Mock()
        redis_spy.smembers.return_value = {"listing-1"}
        redis_spy.get.return_value = json.dumps({
            "listingId": "listing-1",
            "title": "Appartement calme",
            "description": "Logement près des transports.",
            "city": "Évry",
            "nightlyPrice": 82,
            "guests": 3,
            "status": "PUBLISHED",
            "createdAt": "2026-10-01T10:00:00+00:00",
        })

        with patch("search.main.redis_client", redis_spy):
            result = search_listings(
                city="ÉVRY",
                guests=2,
                min_price=None,
                max_price=100,
                q="calme",
                limit=100,
            )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["listingId"], "listing-1")
        redis_spy.smembers.assert_called_once_with("projection:city:évry")
        redis_spy.hincrby.assert_called_once_with("aggregate:search-queries", "évry", 1)


if __name__ == "__main__":
    unittest.main()
