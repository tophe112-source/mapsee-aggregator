#!/usr/bin/env python3
"""Offline checks for Epiphany's official concert-page reader and save path."""
from datetime import date
import json
from pathlib import Path
import tempfile

from mapsee_ingest import make_fingerprint
from mapsee_ingest_recitals import parse_concerts, run


URL = "https://epiphanyseattle.org/music-arts/concerts/"
SOURCE = {
    "id": "epiphany-concerts",
    "name": "Epiphany Seattle Concerts",
    "url": URL,
    "timezone": "America/Los_Angeles",
    "currency": "USD",
    "venue": {
        "name": "Epiphany Parish of Seattle",
        "address": "1805 38th Avenue",
        "city": "Seattle",
        "region": "Washington",
        "country": "US",
        "postal_code": "98122",
        "lat": 47.61,
        "lon": -122.30,
    },
}

MARKUP = """<!doctype html><html><body>
<p>Tickets to the series can be purchased online. This page mentions free music generally.</p>
<div class="block accordion"><h2>2026-2027 Concert Series</h2>
  <div class="accordion"><a class="question collapsed">October 16 – American Seasons</a>
    <div class="answer collapse text"><h4>American Seasons: <em>Chapel Organ Anniversary Concert</em></h4>
      <p><strong>Friday, October 16, 2026 | 7:00 pm | Chapel<br /> FREE</strong></p>
      <p>Free music should not leak into another listing.</p>
    </div>
  </div>
  <div class="accordion"><a class="question collapsed">November 1 – All Souls</a>
    <div class="answer collapse text"><h4>All Souls&#8217; Requiem: <em>Fauré</em></h4>
      <p><strong>Sunday, November 1, 2026 | 5:30 pm<br /></strong></p>
      <p>The choir performs the Requiem during a meditative service.</p>
    </div>
  </div>
  <div class="accordion"><a class="question collapsed">December 4 &amp; 5 – Messiah</a>
    <div class="answer collapse text"><h4>Handel&#8217;s <em>Messiah</em></h4>
      <p><strong>Friday, December 4, 2026 | 7:30 pm<br />Saturday, December 5, 2026 | 7:30 pm<br /></strong>
        <strong>Tickets: </strong>$48 | <a href="https://www.eventbrite.com/e/messiah">tickets here</a></p>
      <p>Baroque orchestra and soloists.</p>
    </div>
  </div>
  <div class="accordion"><a class="question collapsed">April 30 &amp; May 1 – Zelenka</a>
    <div class="answer collapse text"><h4>Zelenka: <em>Missa Votiva</em></h4>
      <p><strong>Friday, April 30, 2027 | 7:30 pm<br />Saturday, May 1, 2027 | 2:00 pm<br /></strong>
        <strong>Tickets: </strong>$38 | <a href="https://www.eventbrite.com/e/zelenka">tickets here</a></p>
      <p>Baroque orchestra and soloists.</p>
    </div>
  </div>
</div></body></html>"""


def refuses(fn):
    try:
        fn()
    except (ValueError, TypeError):
        return
    raise AssertionError("invalid concert page accepted")


def main():
    events = parse_concerts(MARKUP, SOURCE, today=date(2026, 10, 17))
    assert len(events) == 5, len(events)  # past, explicitly free Oct 16 is excluded
    assert [event.name for event in events] == [
        "All Souls’ Requiem: Fauré",
        "Handel’s Messiah", "Handel’s Messiah",
        "Zelenka: Missa Votiva", "Zelenka: Missa Votiva",
    ]
    assert events[0].start_local == "2026-11-01T17:30"
    # Real Los Angeles wall times become the correct UTC instant on both sides
    # of the winter/summer offset change.
    assert events[0].start_utc == "2026-11-02T01:30:00Z"
    assert events[1].start_utc == "2026-12-05T03:30:00Z"
    assert events[3].start_utc == "2027-05-01T02:30:00Z"
    assert events[0].source_details is None and events[0].admission_checked is False
    assert all(e.source_details["free"] is False for e in events[1:])
    assert events[1].source_details["offer"] == {
        "price": "48", "currency": "USD", "url": "https://www.eventbrite.com/e/messiah"}
    assert events[3].source_details["offer"]["price"] == "38"
    assert events[1].ticket_url == "https://www.eventbrite.com/e/messiah"
    assert all("Official source: " + URL in e.description for e in events)
    assert "FREE" not in events[0].description
    assert events[1].latitude == SOURCE["venue"]["lat"]
    assert events[1].longitude == SOURCE["venue"]["lon"]
    assert events[1].coords_exact is True
    assert len({event.fingerprint for event in events}) == 5
    assert events[0].fingerprint == make_fingerprint(
        events[0].name, "2026-11-01", events[0].venue_name, "Seattle")

    # A future FREE token in the performance's own date/admission block is a
    # positive offer; unrelated page prose does not set the flag.
    free_markup = MARKUP.replace("October 16, 2026", "October 16, 2027")
    free_event = parse_concerts(free_markup, SOURCE, today=date(2026, 10, 17))[0]
    assert free_event.source_details == {
        "free": True, "offer": {"price": "0", "currency": "USD"}}
    assert free_event.admission_checked is True

    def one_event(admission_text):
        markup = f"""<div class="block accordion"><h2>2026-2027 Concert Series</h2>
          <div class="accordion"><a class="question">Future performance</a><div class="answer">
            <h4>Admission Test Concert</h4>
            <p>Friday, December 31, 2026 | 7:00 pm<br />{admission_text}</p>
          </div></div></div>"""
        return parse_concerts(markup, SOURCE, today=date(2026, 12, 1))[0]

    mixed = one_event("Tickets: $48; free for children")
    assert mixed.source_details["free"] is False
    assert mixed.source_details["offer"]["price"] == "48"
    assert mixed.source_details["restricted"] is True
    members = one_event("FREE; members only")
    assert members.source_details == {"free": False, "restricted": True}
    paid_not_free = one_event("Tickets: $48; NOT FREE")
    assert paid_not_free.source_details["free"] is False
    unknown_not_free = one_event("NOT FREE")
    assert unknown_not_free.source_details is None
    assert unknown_not_free.admission_checked is False

    # Explicit years work across a year boundary, while daylight-saving rules
    # are resolved through the shared strict local-time converter.
    year_markup = """<div class="block accordion"><h2>2026-2027 Concert Series</h2>
      <div class="accordion"><a class="question">New Year</a><div class="answer">
        <h4>New Year Concert</h4><p>Thursday, December 31, 2026 | 11:30 pm<br />Friday, January 1, 2027 | 1:00 am</p>
      </div></div></div>"""
    year_events = parse_concerts(year_markup, SOURCE, today=date(2026, 12, 1))
    assert [e.start_utc for e in year_events] == ["2027-01-01T07:30:00Z", "2027-01-01T09:00:00Z"]
    duplicate_day_markup = year_markup.replace(
        "Thursday, December 31, 2026 | 11:30 pm<br />Friday, January 1, 2027 | 1:00 am",
        "Friday, January 1, 2027 | 1:00 am<br />Friday, January 1, 2027 | 2:00 am")
    same_day = parse_concerts(duplicate_day_markup, SOURCE, today=date(2026, 12, 1))
    assert [e.source_id.rsplit(":", 1)[-1] for e in same_day] == ["0100", "0200"]
    assert len({e.fingerprint for e in same_day}) == 2
    refuses(lambda: parse_concerts(year_markup.replace("December 31, 2026", "February 30, 2027"),
                                  SOURCE, today=date(2026, 12, 1)))
    refuses(lambda: parse_concerts(year_markup.replace(", 2026", ""), SOURCE,
                                  today=date(2026, 12, 1)))
    refuses(lambda: parse_concerts(MARKUP.replace("Concert Series", "Schedule"), SOURCE,
                                  today=date(2026, 10, 17)))
    refuses(lambda: parse_concerts(MARKUP, {**SOURCE, "venue": {"name": "Epiphany"}},
                                  today=date(2026, 10, 17)))

    # A failed source is reported after successful sources have been saved.
    class Robots:
        @staticmethod
        def can_fetch(_ua, _url):
            return True

    class FakeFetcher:
        def __init__(self, max_seconds):
            import time
            self.deadline = time.monotonic() + max_seconds
            self.last_fetch = {}

        @staticmethod
        def _robots_for(_origin):
            return Robots()

        @staticmethod
        def get_text(url):
            if "bad.example" in url:
                raise ValueError("fixture failure")
            return MARKUP

    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / "sources.json"
        store_path = Path(temp) / "events.json"
        config.write_text(json.dumps({"recitals": [
            SOURCE, {**SOURCE, "id": "broken", "name": "Broken", "url": "https://bad.example/"},
        ]}), encoding="utf-8")
        failures = run(config, store_path, max_seconds=30,
                       today=date(2026, 10, 17), fetcher_factory=FakeFetcher)
        saved = json.loads(store_path.read_text(encoding="utf-8"))
        assert failures == 1
        assert len(saved["events"]) == 5

    print("test_ingest_recitals: all checks passed")


if __name__ == "__main__":
    main()
