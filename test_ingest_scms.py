#!/usr/bin/env python3
"""Offline checks for the Seattle Chamber Music Society calendar reader.

The two fixtures are verbatim excerpts of the live listing, read 2026-11-28:
12 cards on /events/, 5 on /events/page/2/, and the embedded calendar block
that dates all 17 by post id.
"""
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import time

import mapsee_ingest_scms as scms
from mapsee_ingest_scms import _Tree, build_events, next_page, parse_calendar, parse_cards, read_listing, run

ROOT = Path(__file__).resolve().parent
PAGE1_URL = "https://www.seattlechambermusic.org/events/"
PAGE2_URL = "https://www.seattlechambermusic.org/events/page/2/"
PAGE1 = (ROOT / "fixtures/scms-events-page1.html").read_text(encoding="utf-8")
PAGE2 = (ROOT / "fixtures/scms-events-page2.html").read_text(encoding="utf-8")
CONFIG = json.loads((ROOT / "scms_sources.json").read_text(encoding="utf-8"))
SOURCE = CONFIG["sources"][0]
TODAY = date(2026, 11, 28)
FREE_POSTS = {"6395", "6079", "6082", "6400", "6130"}   # the five cards tagged events-tags-free


class Robots:
    @staticmethod
    def can_fetch(_ua, _url):
        return True


class FakeFetcher:
    """Serves the fixtures; anything else is a failed request."""
    pages = {PAGE1_URL: PAGE1, PAGE2_URL: PAGE2}

    def __init__(self, max_seconds=60):
        self.deadline = time.monotonic() + max_seconds
        self.last_fetch = {}
        self.got = []

    @staticmethod
    def _robots_for(_origin):
        return Robots()

    def get_text(self, url):
        self.got.append(url)
        if url not in self.pages:
            raise ValueError(f"fixture has no page {url}")
        return self.pages[url]


def refuses(fn, what):
    try:
        fn()
    except (ValueError, TypeError):
        return
    raise AssertionError(f"accepted {what}")


def cards_and_calendar():
    cards, calendar = read_listing(FakeFetcher(), {**SOURCE, "crawl_delay": 0})
    return cards, calendar


def one_card(**changes):
    """The first fixture card, altered, with its own calendar entry."""
    cards, calendar = cards_and_calendar()
    card = dict(cards[0], **changes)
    return [card], {card["post_id"]: calendar[cards[0]["post_id"]]}


def main():
    checks = 0

    # The listing is read whole by following its pager, and the year comes only
    # from the calendar block, joined by post id.
    fetcher = FakeFetcher()
    cards, calendar = read_listing(fetcher, {**SOURCE, "crawl_delay": 0})
    assert fetcher.got == [PAGE1_URL, PAGE2_URL], fetcher.got
    assert len(cards) == 17 and len(calendar) == 17, (len(cards), len(calendar))
    assert {c["post_id"] for c in cards} == set(calendar)
    checks += 1

    events, cancelled, problems = build_events(cards, calendar, SOURCE, today=TODAY)
    assert (len(events), cancelled, problems) == (17, [], []), (len(events), cancelled, problems)
    by_id = {e.source_id: e for e in events}
    hamelin = by_id["4830"]
    assert hamelin.name == "Balourdet Quartet & Marc-André Hamelin"
    assert hamelin.start_local == "2026-12-06T14:00"
    assert hamelin.start_utc == "2026-12-06T22:00:00Z"          # PST
    assert by_id["4932"].start_utc == "2027-03-21T21:00:00Z"     # PDT from 2027-03-14
    assert by_id["5711"].start_local == "2027-01-22T19:30"        # the year is the calendar's
    assert by_id["5711"].start_utc == "2027-01-23T03:30:00Z"
    assert hamelin.venue_name == "Nordstrom Recital Hall at Benaroya Hall"
    assert (hamelin.latitude, hamelin.longitude) == (47.6081167, -122.3369704)
    assert hamelin.coords_exact is True and hamelin.city == "Seattle"
    assert hamelin.ticket_url == "https://www.seattlechambermusic.org/events/balourdet-quartet-marc-andre-hamelin/"
    assert hamelin.poster_image_url.startswith("https://www.seattlechambermusic.org/wp-content/uploads/")
    assert hamelin.promoter == "Seattle Chamber Music Society"
    assert hamelin.source == "venue:seattlechambermusic.org"
    checks += 1

    # Categories follow the publisher's own taxonomy.
    assert {e.source_id for e in events if e.category == "learning"} == {"6395", "6079", "6082", "6400"}
    assert all(by_id[i].categories == ["music"] for i in ("6395", "6079", "6082", "6400"))
    assert (by_id["6130"].category, by_id["6130"].categories) == ("music", ["kids"])   # Azure
    assert all(e.category == "music" and e.categories == [] for e in events
               if e.source_id not in {"6395", "6079", "6082", "6400", "6130"})
    checks += 1

    # Admission: the per-event `free` tag is a free claim; no tag clears one.
    for e in events:
        assert e.admission_checked is True
        if e.source_id in FREE_POSTS:
            assert e.source_details == {"free": True, "offer": {
                "price": "0", "currency": "USD", "url": e.ticket_url}}, e.source_details
            assert e.description.startswith("Free to attend.")
            assert "Register on the event page." in e.description
        else:
            assert e.source_details == {}, (e.source_id, e.source_details)
            assert "Free" not in e.description
    assert hamelin.description == "Seattle Chamber Music Society · Signature Series"
    checks += 1

    # Identity is the post id: a retitled or re-dated performance is the same row.
    assert hamelin.fingerprint == hashlib.sha1(
        b"www.seattlechambermusic.org\n4830").hexdigest()
    assert len({e.fingerprint for e in events}) == 17
    moved_cards = [dict(c, title="Balourdet & Hamelin (new date)", day="7") if c["post_id"] == "4830" else c
                   for c in cards]
    moved_calendar = dict(calendar, **{"4830": date(2026, 12, 7)})
    moved = {e.source_id: e for e in build_events(moved_cards, moved_calendar, SOURCE, today=TODAY)[0]}
    assert moved["4830"].fingerprint == hamelin.fingerprint
    assert moved["4830"].start_local == "2026-12-07T14:00"
    checks += 1

    # Past events are not imported.
    later = build_events(cards, calendar, SOURCE, today=date(2026, 12, 7))[0]
    assert {e.source_id for e in later} == set(by_id) - {"6395", "6079", "6082", "4830"}
    checks += 1

    # A card is never given a year by guessing.
    lone, lone_calendar = one_card()
    events_, _, problems = build_events(lone, {}, SOURCE, today=TODAY)
    assert events_ == [] and "no dated calendar entry" in problems[0]
    events_, _, problems = build_events([dict(lone[0], day="5")], lone_calendar, SOURCE, today=TODAY)
    assert events_ == [] and "calendar reads 2026-12-04" in problems[0], problems
    # ...but the same day written another way is the same day.
    for month, day in (("Dec", "04"), ("DEC.", "4"), ("december", "4")):
        events_, _, problems = build_events([dict(lone[0], month=month, day=day)], lone_calendar,
                                            SOURCE, today=TODAY)
        assert len(events_) == 1 and problems == [], (month, day, problems)
    checks += 1

    # Venues: configured or skipped, never geocoded from the label.
    for label in ("Center for Chamber Music – Lower Level", "center for  chamber music - lower level"):
        e = build_events([dict(lone[0], venue=label)], lone_calendar, SOURCE, today=TODAY)[0][0]
        assert e.latitude == 47.6104516 and e.venue_name == label
    bellevue = build_events([dict(lone[0], venue="Meydenbauer Center")], lone_calendar, SOURCE, today=TODAY)[0][0]
    assert (bellevue.city, bellevue.postal_code) == ("Bellevue", "98004")
    for label in ("Private Home", "SCMS Virtual Concert Hall"):
        assert build_events([dict(lone[0], venue=label)], lone_calendar, SOURCE, today=TODAY) == ([], [], [])
    events_, _, problems = build_events([dict(lone[0], venue="Somewhere New")], lone_calendar, SOURCE, today=TODAY)
    assert events_ == [] and "'Somewhere New' is not configured" in problems[0]
    checks += 1

    # A title that says the event is off cancels that post's row, by the same key.
    events_, cancels, problems = build_events(
        [dict(lone[0], title="CANCELLED: " + lone[0]["title"])], lone_calendar, SOURCE, today=TODAY)
    assert events_ == [] and problems == []
    assert cancels[0].name == lone[0]["title"].strip()
    assert cancels[0].fingerprint == scms.fingerprint(lone[0]["post_id"])
    checks += 1

    # A card with no clock claims a day, not a minute; a bad clock is refused.
    dayonly = build_events([dict(lone[0], time="")], lone_calendar, SOURCE, today=TODAY)[0][0]
    assert (dayonly.start_local, dayonly.start_utc) == ("2026-12-04", None)
    events_, _, problems = build_events([dict(lone[0], time="13:15 PM")], lone_calendar, SOURCE, today=TODAY)
    assert events_ == [] and "invalid clock" in problems[0]
    checks += 1

    # Layout changes fail loudly rather than reading as an empty season.
    refuses(lambda: parse_calendar(_Tree(PAGE1.replace("events_info_s", "events_info_x")).root), "no calendar")
    refuses(lambda: parse_calendar(_Tree(PAGE1.replace('"date":"12/04/2026"', '"date":"2026-12-04"')).root),
            "a reformatted calendar date")
    refuses(lambda: parse_calendar(_Tree(PAGE1.replace('"date":"12/04/2026"', '"date":"02/30/2027"')).root),
            "an impossible calendar date")

    class Renamed(FakeFetcher):
        pages = {PAGE1_URL: PAGE1.replace("slider_event_item", "event_card"), PAGE2_URL: PAGE2}
    refuses(lambda: read_listing(Renamed(), {**SOURCE, "crawl_delay": 0}), "cards without their event body")

    class Empty(FakeFetcher):
        pages = {PAGE1_URL: PAGE1.replace('data-elementor-type="loop-item"', 'data-elementor-type="x"'),
                 PAGE2_URL: PAGE2}
    refuses(lambda: read_listing(Empty(), {**SOURCE, "crawl_delay": 0}), "a listing with no cards")
    refuses(lambda: read_listing(FakeFetcher(), {**SOURCE, "url": "https://example.org/events/"}),
            "another host's listing")
    refuses(lambda: build_events(cards, calendar, {**SOURCE, "venues": [{"name": "X"}]}, today=TODAY),
            "a venue without coordinates")
    checks += 1

    # The pager is followed only within the listing on the publisher's host,
    # a loop ends, and a runaway pager is a failure, not an endless read.
    root1 = _Tree(PAGE1).root
    assert next_page(root1, PAGE1_URL, PAGE1_URL) == PAGE2_URL
    assert next_page(_Tree(PAGE2).root, PAGE2_URL, PAGE1_URL) is None
    elsewhere = PAGE1.replace('class="page-numbers next" href="https://www.seattlechambermusic.org/events/page/2/"',
                              'class="page-numbers next" href="https://example.org/events/page/2/"')
    assert next_page(_Tree(elsewhere).root, PAGE1_URL, PAGE1_URL) is None

    class Loop(FakeFetcher):
        pages = {PAGE1_URL: PAGE1, PAGE2_URL: PAGE2.replace(
            '<span class="page-numbers next">',
            '<a class="page-numbers next" href="https://www.seattlechambermusic.org/events/">')}
    assert len(read_listing(Loop(), {**SOURCE, "crawl_delay": 0})[0]) == 17
    refuses(lambda: read_listing(FakeFetcher(), {**SOURCE, "crawl_delay": 0}, max_pages=1), "an unbounded pager")
    checks += 1

    # Every listing page waits out the publisher's configured crawl delay.
    delays = []
    original = scms.fetch_page
    try:
        scms.fetch_page = lambda fetcher, url, delay: (delays.append(delay), fetcher.get_text(url))[1]
        read_listing(FakeFetcher(), SOURCE)
    finally:
        scms.fetch_page = original
    assert delays == [10, 10], delays
    checks += 1

    # run() saves every source that parsed, records a title cancellation as a
    # tombstone, and counts a failed source.
    class WithNotice(FakeFetcher):
        pages = {PAGE1_URL: PAGE1.replace("<h4>Masterclass with Balourdet Quartet</h4>",
                                          "<h4>CANCELLED - Masterclass with Balourdet Quartet</h4>"),
                 PAGE2_URL: PAGE2}
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / "sources.json"
        store_path = Path(temp) / "events.json"
        config.write_text(json.dumps({"sources": [
            {**SOURCE, "crawl_delay": 0},
            {**SOURCE, "name": "Broken", "crawl_delay": 0, "url": "https://www.seattlechambermusic.org/missing/"},
        ]}), encoding="utf-8")
        failures = run(config, store_path, max_seconds=30, today=TODAY, fetcher_factory=WithNotice)
        saved = json.loads(store_path.read_text(encoding="utf-8"))
        assert failures == 1
        assert len(saved["events"]) == 16, len(saved["events"])
        tombs = {t["fingerprint"]: t for t in saved["tombstones"]}
        assert tombs[scms.fingerprint("6079")]["source_id"] == "6079", tombs
        assert tombs[scms.fingerprint("6079")]["name"] == "Masterclass with Balourdet Quartet"
        assert all(e["_admission_source"]["source"] == scms.SOURCE for e in saved["events"])

    # Every upcoming card refused is a failure, not a quiet season.
    class Moved(FakeFetcher):
        pages = {url: page.replace("Nordstrom Recital Hall at Benaroya Hall", "Somewhere New")
                           .replace("Center for Chamber Music", "Somewhere Else")
                 for url, page in FakeFetcher.pages.items()}
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / "sources.json"
        config.write_text(json.dumps({"sources": [{**SOURCE, "crawl_delay": 0}]}), encoding="utf-8")
        assert run(config, Path(temp) / "events.json", max_seconds=30, today=TODAY,
                   fetcher_factory=Moved) == 1
    checks += 1

    # The configuration names a checked point for every venue and only lens keys.
    lens_keys = {"music", "learning", "kids", "community", "arts", "theater"}
    for venue in SOURCE["venues"]:
        assert venue["coordinate_source"].startswith("OpenStreetMap "), venue["name"]
        assert 47.0 < venue["lat"] < 48.0 and -123.0 < venue["lon"] < -122.0, venue["name"]
    for rule in SOURCE["categories"].values():
        assert {rule["category"], *rule.get("categories", [])} <= lens_keys
    checks += 1

    print(f"test_ingest_scms: all {checks} checks passed")


if __name__ == "__main__":
    main()
