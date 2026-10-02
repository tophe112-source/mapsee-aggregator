"""Persisted lifecycle of native admission facts through the real EventStore.

No network or database calls: normalized facts are the same helper output the
Tribe and JSON-LD adapters store, then the real JSON store and sync row builder
exercise refresh, ownership, clearing and serialization behavior.
"""
import tempfile
import unittest
from pathlib import Path

from mapsee_admission import admission_description, normalize_admission_facts
from mapsee_ingest import EventStore, NormalizedEvent
from mapsee_supabase_sync import needs_detail_sync, to_row


FINGERPRINT = "same-event-fingerprint"
URL = "https://events.example.test/event/one/"
START = "2099-10-03T16:00:00Z"


def native_event(cost=None, *, source="tribe", source_id=URL, publisher=None, url=URL,
                 context="Community Art Night", description="Community Art Night details."):
    facts = normalize_admission_facts(
        cost, url=url, currency_hint="USD", context=context)
    return NormalizedEvent(
        source=source,
        source_id=source_id,
        name="Community Art Night",
        fingerprint=FINGERPRINT,
        description=admission_description(description, facts),
        start_utc=START,
        end_utc="2099-10-03T17:00:00Z",
        venue_name="Sample Community Hall",
        latitude=47.60,
        longitude=-122.33,
        address="100 Main Street, Sample City",
        city="Sample City",
        region="WA",
        country="United States",
        category="community",
        ticket_url=url,
        source_details=facts,
        admission_checked=True,
        admission_publisher=publisher,
    )


class AdmissionRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.json"
        self.store = EventStore(str(self.path))

    def tearDown(self):
        self.tmp.cleanup()

    def save_and_reload(self):
        self.store.save()
        self.store = EventStore(str(self.path))

    def record(self):
        return self.store.records[FINGERPRINT]

    def row(self):
        # The stored record is the exact object build_rows passes to to_row;
        # coordinates are already present, so no geocoder/session is involved.
        return to_row(self.record(), "fixture-host")

    def test_zero_to_paid_replaces_price_and_description_without_rekeying(self):
        free = native_event("Free")
        self.store.upsert(free)
        self.save_and_reload()
        old_id = self.row()["external_id"]
        self.assertEqual(self.record()["_admission_source"],
                         {"source": "tribe", "source_id": URL})
        self.assertNotIn("admission_checked", self.record())

        self.store.upsert(native_event("12.50"))
        self.save_and_reload()

        facts = self.record()["source_details"]
        self.assertIs(facts["free"], False)
        self.assertEqual(facts["offer"], {"price": "12.5", "currency": "USD", "url": URL})
        self.assertTrue(self.record()["description"].startswith(
            "Some admission options are not free. Admission: USD 12.5."))
        row = self.row()
        self.assertEqual(row["external_id"], old_id)
        self.assertEqual(row["source_details"], facts)
        self.assertIn("Some admission options are not free.", row["description"])

    def test_zero_to_restricted_clears_public_free_claim(self):
        self.store.upsert(native_event("Free"))
        self.save_and_reload()

        restricted = native_event("Free", context="Free for members only")
        self.store.upsert(restricted)
        self.save_and_reload()

        self.assertEqual(self.record()["source_details"], {"free": False, "restricted": True})
        self.assertTrue(self.record()["description"].startswith(
            "Not a free public admission offer. Eligibility restrictions apply."))
        row = self.row()
        self.assertIs(row["source_details"]["free"], False)
        self.assertNotIn("Free to attend.", row["description"])

    def test_same_owner_unknown_clears_price_but_preserves_other_detail_facts(self):
        self.store.upsert(native_event("Free"))
        self.record()["source_details"]["performers"] = [
            {"type": "Person", "name": "Community Ensemble"}]
        self.save_and_reload()
        before_id = self.row()["external_id"]

        refreshed = native_event(None, description="Updated publisher event text.")
        self.store.upsert(refreshed)
        self.save_and_reload()

        details = self.record()["source_details"]
        self.assertNotIn("free", details)
        self.assertNotIn("offer", details)
        self.assertNotIn("restricted", details)
        self.assertEqual(details["performers"], [{"type": "Person", "name": "Community Ensemble"}])
        self.assertEqual(self.record()["description"], "Updated publisher event text.")
        self.assertEqual(self.row()["external_id"], before_id)
        self.assertEqual(self.row()["source_details"], details)

    def test_unrelated_unknown_read_cannot_erase_native_zero_or_description(self):
        free = native_event("Free")
        self.store.upsert(free)
        self.save_and_reload()
        old_description = self.record()["description"]

        unrelated = native_event(None, source="jsonld",
                                 source_id="https://other.example.test/event/one/",
                                 description="Other publisher summary.")
        self.store.upsert(unrelated)

        self.assertIs(self.record()["source_details"]["free"], True)
        self.assertEqual(self.record()["description"], old_description)
        self.assertEqual(self.record()["_admission_source"],
                         {"source": "tribe", "source_id": URL})

    def test_blind_reader_preserves_native_facts_and_explicit_empty_still_clears(self):
        free = native_event("Free")
        self.store.upsert(free)
        self.save_and_reload()
        old_description = self.record()["description"]

        blind = NormalizedEvent(source="ics", source_id="other-feed-id",
                                name="Community Art Night", fingerprint=FINGERPRINT,
                                description="ICS summary with no admission fields.")
        self.store.upsert(blind)
        self.assertIs(self.record()["source_details"]["free"], True)
        self.assertEqual(self.record()["description"], old_description)

        # This is the existing successful-detail contract (e.g. RAMart): an
        # explicit empty dict clears its old details and refreshes the prose.
        detail_clear = NormalizedEvent(
            source="tribe", source_id=URL, name="Community Art Night",
            fingerprint=FINGERPRINT, description="Publisher no longer lists details.",
            start_utc=START, venue_name="Sample Community Hall", source_details={})
        self.store.upsert(detail_clear)
        self.save_and_reload()
        self.assertEqual(self.record()["source_details"], {})
        self.assertEqual(self.record()["description"], "Publisher no longer lists details.")
        self.assertIsNone(self.row().get("source_details"))

    def test_unknown_new_row_stays_thin_and_is_not_queued_for_detail_sync(self):
        unknown = native_event(None)
        self.store.upsert(unknown)
        self.save_and_reload()

        rec = self.record()
        self.assertNotIn("source_details", rec)
        self.assertNotIn("_admission_source", rec)
        self.assertFalse(needs_detail_sync(rec, {FINGERPRINT: False}))
        row = self.row()
        self.assertNotIn("source_details", row)
        self.assertEqual(row["lat"], 47.60)
        self.assertEqual(row["lon"], -122.33)
        self.assertEqual(row["external_id"], FINGERPRINT)

    def test_cross_source_price_conflict_is_not_free_in_either_order(self):
        paid = native_event("12.50", source="jsonld",
                            source_id="https://other.example.test/event/one/")
        free = native_event("Free", source="tribe")

        # The paid owner survives a save/reload before the other publisher's
        # free claim arrives, so the conflict check also covers persisted state.
        self.store.upsert(paid)
        self.save_and_reload()
        paid_offer = self.record()["source_details"]["offer"]
        paid_owner = self.record()["_admission_source"]
        self.store.upsert(free)
        self.save_and_reload()
        self.assertIs(self.record()["source_details"]["free"], False)
        self.assertEqual(self.record()["source_details"]["offer"], paid_offer)
        self.assertEqual(self.record()["_admission_source"], paid_owner)
        self.assertIn("Some admission options are not free.", self.row()["description"])

        reverse = EventStore(str(self.path.parent / "reverse.json"))
        reverse.upsert(free)
        reverse.upsert(paid)
        reverse.save()
        reverse = EventStore(str(self.path.parent / "reverse.json"))
        self.assertIs(reverse.records[FINGERPRINT]["source_details"]["free"], False)
        self.assertEqual(reverse.records[FINGERPRINT]["source_details"]["offer"], paid_offer)
        self.assertEqual(reverse.records[FINGERPRINT]["_admission_source"], paid_owner)
        self.assertIn("Some admission options are not free.",
                      reverse.records[FINGERPRINT]["description"])

    def test_generic_positive_offer_without_free_flag_vetoes_native_zero(self):
        generic_offer = {"url": URL, "price": "25", "currency": "USD"}
        generic = NormalizedEvent(
            source="ics", source_id="generic-feed-row", name="Community Art Night",
            fingerprint=FINGERPRINT, description="Publisher event with admission details.",
            start_utc=START, venue_name="Sample Community Hall",
            source_details={"offer": generic_offer})
        self.store.upsert(generic)
        self.save_and_reload()

        self.store.upsert(native_event("Free", source="tribe"))
        self.save_and_reload()

        details = self.record()["source_details"]
        self.assertIs(details["free"], False)
        self.assertEqual(details["offer"], generic_offer)
        self.assertNotIn("_admission_source", self.record())
        self.assertIn("Some admission options are not free.", self.row()["description"])

    def test_same_owner_paid_to_zero_can_resolve_back_to_free(self):
        self.store.upsert(native_event("12.50"))
        self.save_and_reload()
        owner = self.record()["_admission_source"]

        self.store.upsert(native_event("Free"))
        self.save_and_reload()

        self.assertIs(self.record()["source_details"]["free"], True)
        self.assertEqual(self.record()["_admission_source"], owner)
        self.assertTrue(self.record()["description"].startswith("Free to attend."))
        row = self.row()
        self.assertIs(row["source_details"]["free"], True)
        self.assertNotIn("Some admission options are not free.", row["description"])

    def test_legacy_tribe_owner_needs_publisher_proof_for_same_source_equivalence(self):
        host_a = "tribe-a.example"
        host_b = "tribe-b.example"
        source_id = "numeric-73"
        host_a_url = f"https://{host_a}/event/73/"

        same_site = EventStore(str(self.path.parent / "legacy-same-site.json"))
        same_site.upsert(native_event("12.50", source_id=source_id,
                                      publisher=host_a, url=host_a_url))
        same_site.save()
        same_site = EventStore(str(self.path.parent / "legacy-same-site.json"))
        del same_site.records[FINGERPRINT]["_admission_source"]["publisher"]
        same_site.save()
        same_site = EventStore(str(self.path.parent / "legacy-same-site.json"))
        same_site.upsert(native_event("Free", source_id=source_id,
                                      publisher=host_a, url=host_a_url))
        same_site.save()
        same_details = same_site.records[FINGERPRINT]["source_details"]

        other_site = EventStore(str(self.path.parent / "legacy-other-site.json"))
        other_site.upsert(native_event("12.50", source_id=source_id,
                                       publisher=host_a, url=host_a_url))
        other_site.save()
        other_site = EventStore(str(self.path.parent / "legacy-other-site.json"))
        del other_site.records[FINGERPRINT]["_admission_source"]["publisher"]
        other_site.save()
        other_site = EventStore(str(self.path.parent / "legacy-other-site.json"))
        other_site.upsert(native_event("Free", source_id=source_id,
                                       publisher=host_b,
                                       url=f"https://{host_b}/event/73/"))
        other_site.save()
        other_details = other_site.records[FINGERPRINT]["source_details"]

        self.assertIs(same_details["free"], True)
        self.assertEqual(same_site.records[FINGERPRINT]["_admission_source"],
                         {"source": "tribe", "source_id": source_id,
                          "publisher": host_a})
        self.assertIs(other_details["free"], False)
        self.assertEqual(other_details["offer"]["price"], "12.5")
        self.assertEqual(other_site.records[FINGERPRINT]["_admission_source"],
                         {"source": "tribe", "source_id": source_id})

    def test_native_refresh_targets_existing_rekey_destination(self):
        publisher = "tribe-a.example"
        source_id = "numeric-91"
        event_url = f"https://{publisher}/events/community-night/"
        old = native_event("Free", source_id=source_id, publisher=publisher, url=event_url)
        old.name = "Community Art Night"
        old.fingerprint = "old-event-fingerprint"
        destination = NormalizedEvent(
            source="ics", source_id="ical:updated-event", name="Community Art Night Revised",
            fingerprint="destination-fingerprint", description="Free to attend. Publisher details.",
            start_utc=START, venue_name="Sample Community Hall",
            source_details={"free": True})
        self.store.upsert(old)
        self.store.upsert(destination)
        self.save_and_reload()

        refreshed = native_event("12.50", source_id=source_id,
                                 publisher=publisher, url=event_url)
        refreshed.name = destination.name
        refreshed.fingerprint = destination.fingerprint
        self.store.upsert(refreshed)
        self.save_and_reload()

        rec = self.store.records[destination.fingerprint]
        self.assertNotIn("old-event-fingerprint", self.store.records)
        self.assertIs(rec["source_details"]["free"], False)
        self.assertEqual(rec["_admission_source"], {
            "source": "tribe", "source_id": source_id, "publisher": publisher})
        self.assertTrue(rec["description"].startswith("Some admission options are not free."))
        self.assertEqual(self.store.source_to_fp[("tribe", publisher, source_id)],
                         destination.fingerprint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
