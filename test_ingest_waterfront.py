"""Offline regressions for Friends of Waterfront Park's one-page calendar."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import html
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from mapsee_ingest import EventStore
import mapsee_ingest_waterfront as waterfront
import offers_0227


ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "waterfront" / "calendar.html"
ZONE = ZoneInfo("America/Los_Angeles")
TODAY = datetime(2026, 10, 27, 12, tzinfo=ZONE)
SITE = {
    "name": "Waterfront fixture",
    "url": "https://waterfrontparkseattle.org/events/",
    "timezone": "America/Los_Angeles",
    "venues": {
        "Pier 62": {"name": "Pier 62", "lat": 47.6084999, "lon": -122.3448246,
                    "address": "1951 Alaskan Way", "coords_exact": True},
        "Pier 66": {"name": "Pier 66", "lat": 47.6106073, "lon": -122.3489456,
                    "address": "2211 Alaskan Way", "coords_exact": True},
        "Pier 66 (2211 Alaskan Way)": {
            "name": "Pier 66", "lat": 47.6106073, "lon": -122.3489456,
            "address": "2211 Alaskan Way", "coords_exact": True},
    },
}


def fixture_cards():
    return waterfront.parse_cards(FIXTURE.read_text(encoding="utf-8"))


def occurrence(cards, stamp):
    return next(card for card in cards if card.datetime == stamp)


class FakeRobots:
    def check(self, url):
        return {"allowed": True, "status": "ok", "crawl_delay": None}


class FakeResponse:
    status_code = 200
    url = SITE["url"]
    headers = {}

    def __init__(self, text=None):
        self.content = (text if text is not None else FIXTURE.read_text(encoding="utf-8")).encode("utf-8")
        self.text = self.content.decode("utf-8")

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, text=None):
        self.calls = []
        self.text = text

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.text)


class WaterfrontTests(unittest.TestCase):
    def test_fixture_covers_all_42_grid_items_and_40_event_cards(self):
        cards = fixture_cards()
        self.assertEqual(42, len(cards))
        self.assertEqual(40, sum(bool(card.title and card.href) for card in cards))
        self.assertTrue(all(card.title and card.href for card in cards[:40]))
        self.assertEqual("2026-10-31T15:00:00-07:00", waterfront.to_event(
            occurrence(cards, "202610311500"), SITE, TODAY)[0].start_local)

    def test_dst_offsets_and_explicit_displayed_end(self):
        cards = fixture_cards()
        before = waterfront.to_event(occurrence(cards, "202610311500"), SITE, TODAY)[0]
        after = waterfront.to_event(occurrence(cards, "202611011500"), SITE, TODAY)[0]
        self.assertEqual("2026-10-31T15:00:00-07:00", before.start_local)
        self.assertEqual("2026-10-31T18:00:00-07:00", before.end_local)
        self.assertEqual("2026-11-01T15:00:00-08:00", after.start_local)
        self.assertEqual("2026-11-01T18:00:00-08:00", after.end_local)
        self.assertIn("Time as listed: 3‑6 pm.", before.description)

    def test_ambiguous_or_nonexistent_dst_clocks_are_not_guessed(self):
        self.assertIsNone(waterfront._as_datetime("202611010130", ZONE))
        self.assertIsNone(waterfront._as_datetime("202603080230", ZONE))

    def test_time_correction_updates_the_same_identity(self):
        card = occurrence(fixture_cards(), "202610311500")
        original = waterfront.to_event(card, SITE, TODAY)[0]
        changed = deepcopy(card)
        changed.datetime = "202610311600"
        changed.href = changed.href.replace("202610311500", changed.datetime)
        changed.headings = [(kind, "4-7 pm" if kind == "time" else value)
                            for kind, value in changed.headings]
        correction = waterfront.to_event(changed, SITE, TODAY)[0]
        self.assertEqual(original.fingerprint, correction.fingerprint)
        self.assertNotEqual(original.start_utc, correction.start_utc)
        self.assertEqual(original.source_id, correction.source_id)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            store = EventStore(path)
            waterfront.store_event(store, original)
            store.save()
            store = EventStore(path)
            self.assertEqual("updated", waterfront.store_event(store, correction))
            store.save()
            record = EventStore(path).records[original.fingerprint]
            self.assertEqual(correction.start_utc, record["start_utc"])
            self.assertEqual("2026-10-31T19:00:00-07:00", record["end_local"])
            self.assertIn("Time as listed: 4-7 pm.", record["description"])
            self.assertEqual(1, len(record["sources"]))
            self.assertEqual(changed.href, record["sources"][0]["url"])
            # A subsequent page that no longer supplies an end clears the old one.
            correction.end_local = correction.end_utc = None
            waterfront.store_event(store, correction)
            self.assertIsNone(store.records[original.fingerprint]["end_local"])

    def test_robots_refusal_prevents_the_listing_request(self):
        session = FakeSession()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(FakeRobots, "check", return_value={"allowed": False, "status": "disallowed"}):
                with self.assertRaisesRegex(RuntimeError, "robots.txt"):
                    waterfront.ingest_site(EventStore(str(Path(temp) / "events.json")),
                                           session, FakeRobots(), SITE, time.monotonic() + 30, TODAY)
        self.assertEqual([], session.calls)

    def test_end_is_only_derived_when_displayed_start_matches_authoritative_clock(self):
        card = occurrence(fixture_cards(), "202610311500")
        for display in ("4‑6 pm", "3‑1 am", "All day"):
            changed = deepcopy(card)
            changed.headings = [(kind, display if kind == "time" else value)
                                for kind, value in changed.headings]
            event, reason = waterfront.to_event(changed, SITE, TODAY)
            self.assertIsNone(event.end_local, (display, reason))
            self.assertIn(f"Time as listed: {display}.", event.description)

    def test_explicit_free_only_and_unknown_admission_stays_unknown(self):
        card = occurrence(fixture_cards(), "202610311500")
        self.assertTrue(waterfront.to_event(card, SITE, TODAY)[0].source_details["free"])
        unknown = deepcopy(card)
        unknown.headings = [(kind, "Pier 62 | Ticketed" if kind == "place" else value)
                            for kind, value in unknown.headings]
        event, reason = waterfront.to_event(unknown, SITE, TODAY)
        self.assertIsNotNone(event, reason)
        self.assertEqual({}, event.source_details)
        self.assertTrue(event.admission_checked)
        self.assertIn("Admission: Ticketed.", event.description)

    def test_changed_admission_clears_same_sources_old_free_fact_after_reload(self):
        original = occurrence(fixture_cards(), "202610311500")
        ticketed = deepcopy(original)
        ticketed.headings = [(kind, "Pier 62 | Ticketed" if kind == "place" else value)
                             for kind, value in ticketed.headings]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            store = EventStore(str(path))
            free_event = waterfront.to_event(original, SITE, TODAY)[0]
            ticketed_event = waterfront.to_event(ticketed, SITE, TODAY)[0]
            self.assertEqual("added", store.upsert(free_event))
            self.assertEqual("updated", store.upsert(ticketed_event))
            store.save()
            record = next(iter(EventStore(str(path)).records.values()))
            self.assertEqual({}, record["source_details"])
            self.assertIn("Admission: Ticketed.", record["description"])
            self.assertNotIn("Free", record["description"])

    def test_venue_aliases_share_configured_fingerprint_and_bad_coords_fail_closed(self):
        card = occurrence(fixture_cards(), "202610311500")
        alias_site = deepcopy(SITE)
        alias_site["venues"]["Pier 62 (alternate label)"] = deepcopy(alias_site["venues"]["Pier 62"])
        alternate = deepcopy(card)
        alternate.headings = [(kind, "Pier 62 (alternate label)" if kind == "place" else value)
                              for kind, value in alternate.headings]
        event_a = waterfront.to_event(card, SITE, TODAY)[0]
        event_b = waterfront.to_event(alternate, alias_site, TODAY)[0]
        self.assertEqual(event_a.fingerprint, event_b.fingerprint)
        malformed = deepcopy(SITE)
        malformed["venues"]["Pier 62"]["lat"] = float("nan")
        self.assertIn("invalid coordinates", waterfront.to_event(card, malformed, TODAY)[1])

    def test_unknown_venue_mismatched_link_and_program_rows_are_skipped(self):
        cards = fixture_cards()
        tour = occurrence(cards, "202611031000")
        event, reason = waterfront.to_event(tour, SITE, TODAY)
        self.assertIsNone(event)
        self.assertTrue(reason.startswith("unknown venue"))
        mismatch = deepcopy(occurrence(cards, "202610311500"))
        mismatch.href = mismatch.href.replace("202610311500", "202610311501")
        self.assertIn("mismatched", waterfront.to_event(mismatch, SITE, TODAY)[1])
        off_host = deepcopy(occurrence(cards, "202610311500"))
        off_host.href = off_host.href.replace("waterfrontparkseattle.org", "evil.example")
        self.assertIn("off-host", waterfront.to_event(off_host, SITE, TODAY)[1])
        self.assertIn("invalid or missing", waterfront.to_event(cards[-1], SITE, TODAY)[1])

    def test_cancelled_notice_is_not_written_as_an_event(self):
        card = deepcopy(occurrence(fixture_cards(), "202610311500"))
        card.title = "CANCELLED - Waterfront Park Firepit Socials"
        event, reason = waterfront.to_event(card, SITE, TODAY)
        self.assertIsNotNone(event, reason)
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(str(Path(temp) / "events.json"))
            store.upsert(event)
            self.assertEqual(1, store.stats["notices"])
            self.assertEqual({}, store.records)

    def test_one_listing_request_populates_and_reloads_the_real_store(self):
        session = FakeSession()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            store = EventStore(str(path))
            with patch.object(waterfront.time, "sleep"):
                result = waterfront.ingest_site(
                    store, session, FakeRobots(), SITE, time.monotonic() + 30, TODAY)
            self.assertEqual(1, len(session.calls))
            self.assertEqual(33, result["kept"])
            store.save()
            reloaded = EventStore(str(path))
            self.assertEqual(33, len(reloaded.records))
            records = list(reloaded.records.values())
            self.assertTrue(all(record["timezone"] == "America/Los_Angeles" for record in records))
            self.assertTrue(all("Time as listed:" in record["description"] for record in records))
            self.assertTrue(all(record["source_details"]["free"] is True for record in records))
            self.assertTrue(all(offers_0227.tagged_free(record["name"], record["description"])
                                for record in records))
            self.assertTrue(any(record["end_local"] == "2026-10-31T18:00:00-07:00"
                                for record in records))

    def test_duplicate_occurrence_card_is_not_upserted_twice(self):
        fixture_html = FIXTURE.read_text(encoding="utf-8")
        card = occurrence(fixture_cards(), "202610311500")
        original = (
            f'<div class="grid-item" data-datetime="{card.datetime}">'
            f'<a href="{html.escape(card.href, quote=True)}"><div class="inner-wrap">'
            f'<h3 class="date-time">{html.escape(card.time_text)}</h3>'
            f'<h3>{html.escape(card.venue_line)}</h3>'
            f'<h4>{html.escape(card.title)}</h4>'
            f'<p class="brief">{html.escape(card.description)}</p>'
            '</div></a></div>')
        session = FakeSession(fixture_html.replace("</main>", original + "</main>"))
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(str(Path(temp) / "events.json"))
            with patch.object(waterfront.time, "sleep"):
                result = waterfront.ingest_site(
                    store, session, FakeRobots(), SITE, time.monotonic() + 30, TODAY)
            self.assertEqual(1, result["duplicates"])
            self.assertEqual(33, result["kept"])

    def test_publisher_poster_is_limited_to_same_host_raster_uploads(self):
        card = occurrence(fixture_cards(), "202610311500")
        for value, expected in (("https://waterfrontparkseattle.org/wp-content/uploads/a.webp", True),
                                ("https://evil.example/wp-content/uploads/a.jpg", False),
                                ("data:image/png,abc", False),
                                ("https://waterfrontparkseattle.org/wp-content/uploads/a.svg", False)):
            card.poster_image_url = value
            event, reason = waterfront.to_event(card, SITE, TODAY)
            self.assertIsNotNone(event, reason)
            self.assertEqual(expected, event.poster_image_url is not None)

    def test_page_size_and_empty_usable_markup_fail_visibly(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "2 MiB"):
                with patch.object(waterfront.time, "sleep"):
                    waterfront.ingest_site(EventStore(str(Path(temp) / "events.json")),
                                           FakeSession("x" * (2 * 1024 * 1024 + 1)),
                                           FakeRobots(), SITE, time.monotonic() + 30, TODAY)
            empty = '<div class="grid-item"></div>'
            with self.assertRaisesRegex(RuntimeError, "no usable dated event cards"):
                with patch.object(waterfront.time, "sleep"):
                    waterfront.ingest_site(EventStore(str(Path(temp) / "events.json")),
                                           FakeSession(empty), FakeRobots(), SITE,
                                           time.monotonic() + 30, TODAY)


if __name__ == "__main__":
    unittest.main()
