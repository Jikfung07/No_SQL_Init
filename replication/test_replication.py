import unittest
import fakeredis
import replicate as rep


def ev(eid, etype, rid="res_1", version=1, **data):
    return {"eventId": eid, "type": etype, "reservationId": rid, "version": version, "data": data}


class ReplicationTest(unittest.TestCase):
    def setUp(self):
        self.r = fakeredis.FakeRedis(decode_responses=True)

    def get(self, rid="res_1"):
        return self.r.hgetall(rep.RESERVATION_KEY.format(rid))

    def test_creation_puis_mise_a_jour(self):
        self.assertEqual(rep.apply_event(self.r, ev("e1", "ReservationEffectuee", city="Lyon")), "applied")
        self.assertEqual(rep.apply_event(self.r, ev("e2", "TaxiReserve", version=2, taxiId="t1")), "applied")
        self.assertEqual(rep.apply_event(self.r, ev("e3", "TaxiConfirme", version=3)), "applied")
        res = self.get()
        self.assertEqual(res["city"], "Lyon")
        self.assertEqual(res["taxiStatus"], "CONFIRMED")
        self.assertEqual(res["version"], "3")

    def test_doublon_ignore(self):
        rep.apply_event(self.r, ev("e1", "ReservationEffectuee"))
        self.assertEqual(rep.apply_event(self.r, ev("e1", "ReservationEffectuee")), "duplicate")

    def test_evenement_en_retard_ignore(self):
        rep.apply_event(self.r, ev("e1", "ReservationEffectuee"))
        rep.apply_event(self.r, ev("e3", "TaxiAnnule", version=3))
        self.assertEqual(rep.apply_event(self.r, ev("e2", "TaxiReserve", version=2)), "stale")
        self.assertEqual(self.get()["taxiStatus"], "CANCELLED")

    def test_orphelin_va_en_dead_letter(self):
        self.assertEqual(rep.apply_event(self.r, ev("e1", "TaxiConfirme", rid="inconnue", version=2)), "orphan")
        self.assertEqual(self.r.llen(rep.DEAD_LETTER), 1)

    def test_evenement_invalide(self):
        self.assertEqual(rep.apply_event(self.r, {"eventId": "x", "type": "ReservationEffectuee"}), "invalid")
        self.assertEqual(rep.apply_event(self.r, ev("y", "Bidule")), "invalid")

    def test_lecture_du_stream(self):
        import json
        self.r.xadd(rep.STREAM_NAME, {"payload": json.dumps(ev("e1", "ReservationEffectuee", city="Nantes"))})
        rep.consume_stream(self.r, once=True, block_ms=10)
        self.assertEqual(self.get()["city"], "Nantes")


if __name__ == "__main__":
    unittest.main()
