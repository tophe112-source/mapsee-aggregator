"""Can one malformed record still cost an entire Events Calendar site?

It could, and it did. The plugin's REST API is not consistent about the shape of
its own object fields, and the same site returns all three:

    "venue": {...}        105 of 109 on bicyclecolorado.org
    "venue": []             - when no venue is assigned
    "venue": [{...}]        4 of 109 - the dict, wrapped in a list
    "image": {...} | false  72 of 109 events answer the bare boolean

`ev.get("venue") or {}` handles the EMPTY list, because `[]` is falsy. That is
why this survived review: the guard looks right and is right for the common
absent case. A POPULATED list sails straight through it and raises
AttributeError on the first `.get`.

The cost is not one event. `ingest_site` had no per-record try, so the exception
unwound to the per-SITE handler in main() and the whole calendar was abandoned —
printing "FAILED: 'list' object has no attribute 'get'", which reads like the
host went down. Bicycle Colorado ingested 0 of its 105 placeable events on the
first run, and would have kept doing so silently every night.

Two things are pinned here: that every shape the plugin emits is read, and that
an unreadable record costs that record and nothing more.

Pure functions and literal payloads: no network, no store, no database.
"""
import io
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_tribe as T
from mapsee_supabase_sync import build_rows, to_row

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


SITE = {"name": "test", "base_url": "https://example.org", "category": "fitness"}

VENUE = {"venue": "Valmont Bike Park", "address": "3160 Airport Rd",
         "city": "Boulder", "state": "CO", "zip": "80301",
         "country": "United States", "geo_lat": "40.0284", "geo_lng": "-105.2266"}


def row(**over):
    r = {"id": 4242, "title": "Wednesday Morning Velo",
         "start_date": "2026-08-19 06:30:00", "end_date": "2026-08-19 08:00:00",
         "timezone": "America/Denver", "url": "https://example.org/event/velo/",
         "description": "<p>Weekly group ride.</p>",
         "venue": dict(VENUE), "categories": [{"slug": "rides"}],
         "image": {"url": "https://example.org/velo.jpg"}}
    r.update(over)
    return r


# --------------------------------------------------------------------------- #
# every shape the plugin emits for `venue`
# --------------------------------------------------------------------------- #
ev = T.to_event(row(), SITE)
check("a dict venue is read", ev is not None and ev.city == "Boulder", ev and ev.city)
check("a dict venue's coordinates are read",
      ev is not None and ev.latitude == 40.0284, ev and ev.latitude)

# Admission is a separate exact-facts channel. Only an explicit universal zero
# adds free prose for 0227; absence stays absent, and source links remain intact.
free_ev = T.to_event(row(cost="Free"), dict(SITE, currency="USD"))
check("an explicit zero-cost Tribe event is marked free",
      free_ev is not None and free_ev.source_details is not None
      and free_ev.source_details.get("free") is True
      and free_ev.source_details.get("offer", {}).get("price") == "0"
      and free_ev.description.startswith("Free to attend."),
      free_ev and (free_ev.source_details, free_ev.description))
check("free classification preserves the event's selected ticket URL",
      free_ev is not None and free_ev.ticket_url == "https://example.org/event/velo/",
      free_ev and free_ev.ticket_url)
check("adding admission facts does not change the event fingerprint",
      free_ev is not None and free_ev.fingerprint == T.to_event(row(), SITE).fingerprint,
      free_ev and free_ev.fingerprint)
free_row = to_row(free_ev.as_record("now"), "fixture-host")
check("exact free facts survive the real adapter-to-row mapping",
      free_row.get("source_details") == free_ev.source_details,
      free_row.get("source_details"))
free_record = free_ev.as_record("now")
check("publisher host stays local to ownership and is not serialized as an event field",
      free_record.get("_admission_source") == {"source": "tribe", "source_id": "4242",
                                               "publisher": "example.org"}
      and "admission_publisher" not in free_record
      and "admission_publisher" not in free_row,
      (free_record.get("_admission_source"), free_record.get("admission_publisher"),
       free_row.get("admission_publisher")))
paid_ev = T.to_event(row(cost="12.50"), dict(SITE, currency="USD"))
check("a numeric Tribe cost needs the configured currency hint",
      paid_ev is not None and paid_ev.source_details is not None
      and paid_ev.source_details.get("free") is False
      and paid_ev.source_details.get("offer", {}).get("price") == "12.5"
      and paid_ev.source_details.get("offer", {}).get("currency") == "USD",
      paid_ev and paid_ev.source_details)
unknown_ev = T.to_event(row(), SITE)
check("a missing Tribe cost stays unknown, not an empty details object",
      unknown_ev is not None and unknown_ev.source_details is None, unknown_ev and unknown_ev.source_details)
member_free = T.to_event(row(cost="Free", description="Free for members only."), SITE)
check("member-only free text cannot become a universal free admission claim",
      member_free is not None and member_free.source_details == {"free": False, "restricted": True}
      and "Free to attend." not in (member_free.description or ""),
      member_free and (member_free.source_details, member_free.description))

ev = T.to_event(row(venue=[dict(VENUE)]), SITE)
check("a venue wrapped in a LIST is read, not fatal",
      ev is not None and ev.city == "Boulder", ev and ev.city)
check("the wrapped venue's coordinates survive",
      ev is not None and ev.latitude == 40.0284, ev and ev.latitude)
check("the wrapped venue's name survives",
      ev is not None and ev.venue_name == "Valmont Bike Park", ev and ev.venue_name)

ev = T.to_event(row(venue=[]), SITE)
check("an empty-list venue is absent, not fatal", ev is not None and ev.city is None,
      ev and ev.city)
ev = T.to_event(row(venue=None), SITE)
check("a null venue is absent, not fatal", ev is not None, ev)
ev = T.to_event(row(venue=["not-a-dict"]), SITE)
check("a list of non-dicts is absent, not fatal", ev is not None, ev)

# --------------------------------------------------------------------------- #
# `image` answers a bare boolean on two thirds of records
# --------------------------------------------------------------------------- #
check("image: false yields no poster rather than an exception",
      (T.to_event(row(image=False), SITE) or T).poster_image_url is None)
check("a dict image still yields its url",
      T.to_event(row(), SITE).poster_image_url == "https://example.org/velo.jpg")
check("a list-wrapped image is read too",
      T.to_event(row(image=[{"url": "https://example.org/x.jpg"}]),
                 SITE).poster_image_url == "https://example.org/x.jpg")

# --------------------------------------------------------------------------- #
# the site's own taxonomy must never crash the record or reach the database
# --------------------------------------------------------------------------- #
check("a malformed categories list does not raise",
      T.to_event(row(categories=["rides", None, {"slug": "outdoors"}]), SITE) is not None)
check("a non-list categories value does not raise",
      T.to_event(row(categories="rides"), SITE) is not None)
ev = T.to_event(row(categories=[{"slug": "outdoors"}, {"slug": "bike-maintenance"}]), SITE)
check("a slug outside mapsee's vocabulary is dropped",
      ev is not None and "bike-maintenance" not in (ev.categories or []), ev and ev.categories)
check("a slug inside it is kept as a secondary",
      ev is not None and "outdoors" in (ev.categories or []), ev and ev.categories)

# --------------------------------------------------------------------------- #
# the config defaults only fill what the record left empty
# --------------------------------------------------------------------------- #
site = dict(SITE, default_city="Denver", default_region="CO", default_country="United States")
ev = T.to_event(row(venue=[]), site)
check("a defaulted region fills a venue-less record",
      ev is not None and ev.region == "CO", ev and ev.region)
ev = T.to_event(row(), site)
check("a default never overwrites what the record actually says",
      ev is not None and ev.city == "Boulder", ev and ev.city)

# --------------------------------------------------------------------------- #
# 0,0 is the Atlantic — the plugin writes it instead of null
# --------------------------------------------------------------------------- #
ev = T.to_event(row(venue=dict(VENUE, geo_lat="0", geo_lng="0")), SITE)
check("0,0 coordinates are treated as absent",
      ev is not None and ev.latitude is None and ev.longitude is None,
      ev and (ev.latitude, ev.longitude))

# --------------------------------------------------------------------------- #
# one bad record costs one record
# --------------------------------------------------------------------------- #
class _Resp:
    status_code = 200

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"events": self._rows, "total_pages": 1}


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def get(self, *a, **k):
        return _Resp(self._rows)


class _Store:
    def __init__(self):
        self.seen = []

    def upsert(self, ev):
        self.seen.append(ev)


def ingest(store, session, site):
    """One site, the way main() runs it: the store is read before the site and
    written after it, never by the reader itself (it runs in a worker thread)."""
    events = T.ingest_site(session, site, T.site_admission_ids(store, site))
    for ev in events:
        store.upsert(ev)
    return len(events)


class _CalendarSession(_Session):
    def get(self, *a, **k):
        self.params = dict(k["params"])
        return super().get(*a, **k)


from datetime import datetime as RealDateTime, timezone as RealTimezone
clock = RealDateTime(2026, 10, 2, 0, 15, tzinfo=RealTimezone.utc)
for calendar_zone, expected_start, expected_end in (
        ("Pacific/Honolulu", "2026-10-01", "2026-10-15"),
        (None, "2026-10-02", "2026-10-16")):
    calendar_session = _CalendarSession([])
    calendar_site = dict(SITE, within_days=14, crawl_delay=0)
    if calendar_zone:
        calendar_site["timezone"] = calendar_zone
    with patch.object(T, "datetime") as mocked_clock, redirect_stdout(io.StringIO()):
        mocked_clock.now.return_value = clock
        ingest(_Store(), calendar_session, calendar_site)
    check("calendar window keeps the current publisher-local day" if calendar_zone else
          "unconfigured publishers retain the UTC window",
          calendar_session.params["start_date"] == expected_start and
          calendar_session.params["end_date"] == expected_end,
          calendar_session.params)


# `free_only` is opt-in and rejects unknown/paid/conditional rows before the
# converter can resolve a venue or apply any configured place fallback.
scoped_rows = [row(id=41, cost="Free"), row(id=42, cost=None),
               row(id=43, cost="12.50"),
               row(id=44, cost="0", description="Free for members only.")]
scope_store = _Store()
mapped_ids = []
real_to_event = T.to_event


def _spy_to_event(raw, site):
    mapped_ids.append(raw["id"])
    return real_to_event(raw, site)


scope_log = io.StringIO()
with patch.object(T, "to_event", side_effect=_spy_to_event), redirect_stdout(scope_log):
    scope_kept = ingest(scope_store, _Session(scoped_rows),
                        dict(SITE, crawl_delay=0, free_only=True, currency="USD"))
check("free_only keeps only the explicit universal-zero row",
      scope_kept == 1 and [e.source_id for e in scope_store.seen] == ["41"],
      [e.source_id for e in scope_store.seen])
check("unknown, paid and member-only rows never reach venue normalization",
      mapped_ids == [41], mapped_ids)
check("free_only reports its rejected supply",
      "3 outside explicit-free admission scope" in scope_log.getvalue(),
      scope_log.getvalue())


def persisted_free_site_refresh(updated_row, initial_overrides=None):
    """Exercise the opt-in refresh through the real JSON store and sync mapper."""
    with tempfile.TemporaryDirectory(prefix="tribe-free-refresh-") as tmp:
        path = os.path.join(tmp, "events.json")
        site = dict(SITE, crawl_delay=0, free_only=True, currency="USD")
        initial = row(id=541, start_date="2099-08-19 06:30:00", cost="Free",
                      **(initial_overrides or {}))
        store = T.EventStore(path)
        ingest(store, _Session([initial]), site)
        store.save()
        store = T.EventStore(path)
        initial_fp = next(iter(store.records))
        initial_record = deepcopy(store.records[initial_fp])

        mapped_ids = []
        real = T.to_event

        def spy(raw, config):
            mapped_ids.append(raw.get("id"))
            return real(raw, config)

        with patch.object(T, "to_event", side_effect=spy), redirect_stdout(io.StringIO()):
            kept = ingest(store, _Session([updated_row]), site)
        store.save()
        rows = build_rows(path, "fixture-host")
        refreshed = store.records[initial_fp]
        sync_row = next((item for item in rows if item["external_id"] == initial_fp), None)
        return initial_record, refreshed, sync_row, kept, mapped_ids


paid_refresh = row(id=541, start_date="2099-08-19 06:30:00", cost="12.50",
                   description="Updated publisher admission.")
old, refreshed, sync_row, kept, mapped_ids = persisted_free_site_refresh(paid_refresh)
check("free_only refreshes a persisted exact-site free source when it turns paid",
      old.get("_admission_source") == {"source": "tribe", "source_id": "541",
                                       "publisher": "example.org"}
      and kept == 1 and mapped_ids == [541]
      and refreshed.get("source_details", {}).get("free") is False
      and refreshed.get("source_details", {}).get("offer", {}).get("price") == "12.5"
      and sync_row is not None and sync_row.get("source_details", {}).get("free") is False
      and sync_row["description"].startswith("Some admission options are not free."),
      (old.get("_admission_source"), refreshed.get("source_details"), sync_row))

external_ticket = "https://tickets.example.test/checkout/541"
old, refreshed, sync_row, kept, mapped_ids = persisted_free_site_refresh(
    paid_refresh, {"website": external_ticket})
check("external ticket URL does not hide the Tribe publisher owner during paid refresh",
      old.get("sources", [{}])[0].get("url") == external_ticket
      and old.get("_admission_source", {}).get("publisher") == "example.org"
      and kept == 1 and mapped_ids == [541]
      and refreshed.get("source_details", {}).get("free") is False
      and sync_row is not None and sync_row.get("source_details", {}).get("free") is False,
      (old.get("sources"), old.get("_admission_source"), refreshed.get("source_details")))

unknown_refresh = row(id=541, start_date="2099-08-19 06:30:00", cost=None,
                      description="Publisher no longer lists admission.")
old, refreshed, sync_row, kept, mapped_ids = persisted_free_site_refresh(unknown_refresh)
check("free_only refreshes a persisted exact-site free source when price becomes unknown",
      kept == 1 and mapped_ids == [541]
      and refreshed.get("source_details") == {}
      and refreshed.get("description") == "Publisher no longer lists admission."
      and sync_row is not None and sync_row.get("source_details") is None
      and "Free to attend." not in sync_row["description"],
      (refreshed.get("source_details"), refreshed.get("description"), sync_row))

old, refreshed, sync_row, kept, mapped_ids = persisted_free_site_refresh(
    unknown_refresh, {"website": external_ticket})
check("external ticket URL does not hide the Tribe publisher owner during unknown refresh",
      old.get("sources", [{}])[0].get("url") == external_ticket
      and old.get("_admission_source", {}).get("publisher") == "example.org"
      and kept == 1 and mapped_ids == [541]
      and refreshed.get("source_details") == {}
      and sync_row is not None and sync_row.get("source_details") is None
      and "Free to attend." not in sync_row["description"],
      (old.get("sources"), old.get("_admission_source"), refreshed.get("source_details")))

# Some legacy rows have publisher provenance on the source ref but no separate
# admission owner. That ref is enough to refresh an external-ticket event.
with tempfile.TemporaryDirectory(prefix="tribe-ownerless-admission-refresh-") as tmp:
    path = os.path.join(tmp, "events.json")
    site = dict(SITE, crawl_delay=0, free_only=True, currency="USD")
    raw_free = row(id=547, start_date="2099-08-19 06:30:00", cost="Free",
                   website=external_ticket)
    store = T.EventStore(path)
    ingest(store, _Session([raw_free]), site)
    fp = next(iter(store.records))
    del store.records[fp]["_admission_source"]
    store.save()
    store = T.EventStore(path)
    raw_unknown = row(id=547, start_date="2099-08-19 06:30:00", cost=None,
                      website=external_ticket,
                      description="Publisher no longer lists admission.")
    mapped = []
    real = T.to_event

    def ownerless_spy(raw, config):
        mapped.append(raw.get("id"))
        return real(raw, config)

    with patch.object(T, "to_event", side_effect=ownerless_spy), redirect_stdout(io.StringIO()):
        ingest(store, _Session([raw_unknown]), site)
    ownerless_record = store.records[fp]
check("legacy source-ref publisher refreshes ownerless free facts behind external ticket URLs",
      mapped == [547]
      and ownerless_record.get("source_details") == {}
      and ownerless_record.get("description") == "Publisher no longer lists admission."
      and ownerless_record.get("_admission_source", {}).get("publisher") == "example.org",
      (mapped, ownerless_record.get("source_details"), ownerless_record.get("description"),
       ownerless_record.get("_admission_source")))

# Tribe numeric IDs collide across installations. A matching ID with another
# host's persisted source URL must not make an otherwise unknown row bypass the
# opt-in free filter.
with tempfile.TemporaryDirectory(prefix="tribe-free-host-scope-") as tmp:
    path = os.path.join(tmp, "events.json")
    store = T.EventStore(path)
    other_site = dict(SITE, base_url="https://elsewhere.example")
    other_host = T.to_event(row(id=542, cost="Free",
                                url="https://elsewhere.example/event/velo/"), other_site)
    store.upsert(other_host)
    store.save()
    store = T.EventStore(path)
    mapped = []
    real = T.to_event

    def host_spy(raw, config):
        mapped.append(raw.get("id"))
        return real(raw, config)

    with patch.object(T, "to_event", side_effect=host_spy), redirect_stdout(io.StringIO()):
        ingest(store, _Session([row(id=542, cost=None)]),
               dict(SITE, crawl_delay=0, free_only=True))
check("same Tribe ID from another publisher host cannot bypass free_only",
      mapped == [], mapped)

# A different Tribe installation can publish the same numeric ID and logical
# event fingerprint. Its free claim must not override the first host's price.
with tempfile.TemporaryDirectory(prefix="tribe-owner-host-conflict-") as tmp:
    path = os.path.join(tmp, "events.json")
    site_a = dict(SITE, base_url="https://site-a.example", currency="USD")
    site_b = dict(SITE, base_url="https://site-b.example", currency="USD",
                  free_only=True, crawl_delay=0)
    paid_from_a = row(id=543, start_date="2099-08-19 06:30:00", cost="12.50",
                      url="https://site-a.example/event/velo/")
    free_from_b = row(id=543, start_date="2099-08-19 06:30:00", cost="Free",
                      url="https://site-b.example/event/velo/")
    store = T.EventStore(path)
    store.upsert(T.to_event(paid_from_a, site_a))
    store.save()
    store = T.EventStore(path)
    old_fp = next(iter(store.records))
    with redirect_stdout(io.StringIO()):
        collision_kept = ingest(store, _Session([free_from_b]), site_b)
    store.save()
    collision_row = next((r for r in build_rows(path, "fixture-host")
                          if r["external_id"] == old_fp), None)
    collision_record = store.records[old_fp]
check("same Tribe numeric ID from another host cannot replace paid admission with free",
      collision_kept == 1
      and collision_record.get("_admission_source", {}).get("publisher") == "site-a.example"
      and collision_record.get("source_details", {}).get("free") is False
      and collision_record.get("source_details", {}).get("offer", {}).get("price") == "12.5"
      and collision_row is not None
      and collision_row.get("source_details", {}).get("free") is False
      and {ref.get("publisher") for ref in collision_record.get("sources", [])
           if ref.get("source") == "tribe"} == {"site-a.example", "site-b.example"}
      and "Free to attend." not in collision_row["description"],
      (collision_record.get("_admission_source"), collision_record.get("source_details"),
       collision_row))

# Different publishers can reuse numeric IDs for unrelated events. Persist and
# reload between reads so this covers the exact lookup keys used by production.
# The public event link can be a third-party ticket URL: its host must not be
# mistaken for the publisher used to scope Tribe's locally unique ID.
with tempfile.TemporaryDirectory(prefix="tribe-scoped-source-ids-") as tmp:
    path = os.path.join(tmp, "events.json")
    site_a = dict(SITE, base_url="https://unexpectedproductions.org", crawl_delay=0)
    site_b = dict(SITE, base_url="https://blessingtonparish.ie", crawl_delay=0)
    site_c = dict(SITE, base_url="https://lockleazent.co.uk", crawl_delay=0)
    first = row(id=544, title="Astronomy Night", start_date="2099-08-19 19:00:00",
                url="https://unexpectedproductions.org/events/astronomy-night/",
                website="https://tickets.example.test/astronomy-night")
    second = row(id=544, title="Garden Work Party", start_date="2099-08-20 10:00:00",
                 venue={**VENUE, "venue": "Blessington Community Hall", "city": "Blessington",
                        "address": "Main Street", "state": "Wicklow", "country": "Ireland",
                        "geo_lat": "53.1700", "geo_lng": "-6.5300"},
                 url="https://blessingtonparish.ie/events/garden-work-party/",
                 website="https://tickets.example.test/garden-work-party")
    third = row(id=544, title="Harbour Jazz Evening", start_date="2099-08-21 18:00:00",
                venue={**VENUE, "venue": "Lockleaze Community Centre", "city": "Bristol",
                       "address": "Barnstaple Road", "state": "Somerset", "country": "United Kingdom",
                       "geo_lat": "51.4900", "geo_lng": "-2.5600"},
                url="https://lockleazent.co.uk/events/harbour-jazz-evening/",
                website="https://tickets.example.test/harbour-jazz-evening")
    store = T.EventStore(path)
    ingest(store, _Session([first]), site_a)
    store.save()
    store = T.EventStore(path)
    ingest(store, _Session([second]), site_b)
    ingest(store, _Session([third]), site_c)
    rekeyed_total = store.stats["rekeyed"]
    store.save()
    store = T.EventStore(path)
    names = sorted(rec["name"] for rec in store.records.values())
    source_keys = set(store.source_to_fp)
    rows_by_id = {r["external_id"]: r for r in build_rows(path, "fixture-host")}
    expected = (("Astronomy Night", first, site_a),
                ("Garden Work Party", second, site_b),
                ("Harbour Jazz Evening", third, site_c))
    expected_ids = {name: T.to_event(event, site).fingerprint
                    for name, event, site in expected}
check("three Tribe hosts with the same numeric ID keep separate rows and publisher-owned ticket links",
      names == ["Astronomy Night", "Garden Work Party", "Harbour Jazz Evening"]
      and rekeyed_total == 0
      and ("tribe", "unexpectedproductions.org", "544") in source_keys
      and ("tribe", "blessingtonparish.ie", "544") in source_keys
      and ("tribe", "lockleazent.co.uk", "544") in source_keys
      and {(ref.get("publisher"), ref.get("source_id"))
           for rec in store.records.values() for ref in rec.get("sources", [])
           if ref.get("source") == "tribe"} == {
               ("unexpectedproductions.org", "544"),
               ("blessingtonparish.ie", "544"),
               ("lockleazent.co.uk", "544")}
      and set(rows_by_id) == set(expected_ids.values())
      and all(f"Tickets / info: {event['website']}" in rows_by_id[expected_ids[name]]["description"]
              for name, event, _site in expected)
      and all(next(ref for ref in store.records[expected_ids[name]]["sources"]
                  if ref.get("source") == "tribe").get("publisher") == site["base_url"].split("//", 1)[1]
              and next(ref for ref in store.records[expected_ids[name]]["sources"]
                       if ref.get("source") == "tribe").get("url") == event["website"]
              for name, event, site in expected)
      and all("admission_publisher" not in rec for rec in store.records.values()),
      (names, rekeyed_total, source_keys, rows_by_id))

# An old external checkout URL cannot prove which Tribe installation owned the
# numeric ID. Keep that ref unbound on load so a different event cannot rekey it.
with tempfile.TemporaryDirectory(prefix="tribe-legacy-unbound-id-") as tmp:
    path = os.path.join(tmp, "events.json")
    legacy_site = dict(SITE, base_url="https://legacy-calendar.example")
    legacy_raw = row(id=546, title="Legacy Ticketed Night", cost="Free",
                     website="https://tickets.example/checkout/546")
    store = T.EventStore(path)
    store.upsert(T.to_event(legacy_raw, legacy_site))
    legacy_fp = next(iter(store.records))
    del store.records[legacy_fp]["_admission_source"]["publisher"]
    del store.records[legacy_fp]["sources"][0]["publisher"]
    store.save()
    store = T.EventStore(path)
    legacy_unbound = ("tribe", None, "546") not in store.source_to_fp
    second = row(id=546, title="Unrelated Community Talk", cost=None,
                 start_date="2099-08-20 18:00:00",
                 url="https://new-calendar.example/events/community-talk/")
    with redirect_stdout(io.StringIO()):
        ingest(store, _Session([second]),
               dict(SITE, base_url="https://new-calendar.example", crawl_delay=0))
    persisted_names = sorted(rec["name"] for rec in store.records.values())
    unbound_rekeys = store.stats["rekeyed"]
check("legacy external-ticket Tribe IDs stay unbound and cannot steal unrelated events",
      legacy_unbound and len(persisted_names) == 2
      and persisted_names == ["Legacy Ticketed Night", "Unrelated Community Talk"]
      and unbound_rekeys == 0,
      (legacy_unbound, persisted_names, unbound_rekeys, store.source_to_fp))

# The publisher-scoped lookup still rekeys a moved/renamed event from that
# same installation, preserving the preexisting same-source update behavior.
with tempfile.TemporaryDirectory(prefix="tribe-same-host-rekey-") as tmp:
    path = os.path.join(tmp, "events.json")
    site = dict(SITE, base_url="https://same-calendar.example", crawl_delay=0)
    original = row(id=545, title="First Program Date", start_date="2099-08-19 19:00:00",
                   url="https://same-calendar.example/events/program/")
    moved = row(id=545, title="Updated Program Date", start_date="2099-08-26 19:00:00",
                url="https://same-calendar.example/events/program/")
    expected_moved_fp = T.to_event(moved, site).fingerprint
    store = T.EventStore(path)
    ingest(store, _Session([original]), site)
    store.save()
    store = T.EventStore(path)
    ingest(store, _Session([moved]), site)
    rekeyed_total = store.stats["rekeyed"]
    store.save()
    store = T.EventStore(path)
    final_fingerprints = list(store.records)
check("same-host Tribe ID changes still update and rekey one event",
      len(final_fingerprints) == 1 and final_fingerprints == [expected_moved_fp]
      and rekeyed_total == 1
      and store.source_to_fp.get(("tribe", "same-calendar.example", "545")) == expected_moved_fp,
      (final_fingerprints, expected_moved_fp, rekeyed_total, store.source_to_fp))


# A record whose start_date is an object rather than a string. `(x or "").strip()`
# raises on it, which is the same shape as the venue bug: a guard that reads as
# defensive, and is not.
poison = row(id=99, start_date={"date": "2026-08-19 06:30:00"})
store = _Store()
buf = io.StringIO()
with redirect_stdout(buf):
    kept = ingest(store, _Session([row(id=1), poison, row(id=2, title="Second Ride",
                                                         start_date="2026-08-26 06:30:00")]),
                  dict(SITE, crawl_delay=0))
out = buf.getvalue()
check("a poison record does not take the other events with it", kept == 2, kept)
check("the surviving events are the good ones", len(store.seen) == 2, len(store.seen))
check("the skip is REPORTED, not swallowed", "unreadable" in out, out.strip()[:120])

# --- the config's venue block, which is what places a non-US calendar ---------
# The sync's only geocoder is the US Census batch service and a coordless row is
# DROPPED there, so a site outside the US whose organiser never filled in the
# venue map fields cannot reach the map at all — "kept 43 events" and "put 0 on
# the map" read identically from the adapter. Calgary Buddhist Temple is the live
# case. catalog_discover_osm proposes every candidate with the surveyed point OSM
# holds, so the answer is already in the config.
VENUE = {"name": "Calgary Buddhist Temple", "address": "38 Ave SW",
         "city": "Calgary", "region": "AB", "country": "CA",
         "lat": 51.0535059, "lon": -114.0876}
SITE_CA = dict(SITE, venue=VENUE)

bare = {"id": 9, "title": "Seated Meditation", "start_date": "2026-09-06 09:00:00",
        "venue": {}}
ev = T.to_event(bare, SITE_CA)
check("a coordless event is placed from the config's venue block",
      (ev.latitude, ev.longitude) == (VENUE["lat"], VENUE["lon"]), (ev.latitude, ev.longitude))
check("and picks up the address the API left blank",
      (ev.city, ev.region, ev.country) == ("Calgary", "AB", "CA"), (ev.city, ev.region, ev.country))
check("without a venue block it is still coordless — nothing is invented",
      T.to_event(bare, SITE).latitude is None, T.to_event(bare, SITE).latitude)

real = {"id": 10, "title": "Golf Tournament", "start_date": "2026-08-29 14:00:00",
        "venue": {"venue": "Elbow Springs", "geo_lat": 51.02, "geo_lng": -114.28,
                  "city": "Calgary", "address": "100 Elbow Dr"}}
rev = T.to_event(real, SITE_CA)
check("the SITE's own coordinates still win — the block fills, it never overrides",
      (rev.latitude, rev.longitude) == (51.02, -114.28), (rev.latitude, rev.longitude))
check("and so does its own venue name and street",
      (rev.venue_name, rev.address) == ("Elbow Springs", "100 Elbow Dr"),
      (rev.venue_name, rev.address))

# --- a block that names itself fills only its own events ----------------------
# UMFA's off-site trips name "Rozel Point" with no address, beside rows that say
# only "UMFA"; a Tampere centre lists events in Oulu. With `names`, the block
# fills what is plainly its own and nothing else - coordinates OR street, since a
# borrowed street is geocoded straight back to the block by the Census pass.
UMFA = {"name": "Utah Museum of Fine Arts", "address": "410 Campus Center Dr",
        "city": "Salt Lake City", "region": "UT", "country": "US",
        "lat": 40.760243, "lon": -111.8431702, "names": ["UMFA", "Utah Museum of Fine Arts"]}
SITE_UMFA = dict(SITE, venue=UMFA)
own = T.to_event(dict(bare, venue={"venue": "UMFA"}), SITE_UMFA)
check("a block with names fills an event that names it by its alias",
      (own.latitude, own.address) == (UMFA["lat"], "410 Campus Center Dr"), (own.latitude, own.address))
room = T.to_event(dict(bare, venue={"venue": "Lintuaura, Kuurosokeiden Toimintakeskus"}),
                  dict(SITE, venue={"name": "Kuurosokeiden toimintakeskus", "lat": 61.45, "lon": 23.84,
                                    "names": ["Kuurosokeiden toimintakeskus"]}))
check("...and one that names a room inside it", room.latitude == 61.45, room.latitude)
none = T.to_event(dict(bare, venue={}), SITE_UMFA)
check("...and one that names no venue at all", none.latitude == UMFA["lat"], none.latitude)
away = T.to_event(dict(bare, venue={"venue": "Rozel Point"}), SITE_UMFA)
check("but not one held somewhere else: no coordinates, and not the block's street",
      (away.latitude, away.address, away.venue_name) == (None, None, "Rozel Point"),
      (away.latitude, away.address, away.venue_name))
check("'UMFA' does not match inside another word",
      T.to_event(dict(bare, venue={"venue": "Umfang Hall"}), SITE_UMFA).latitude is None)
plain = T.to_event(dict(bare, venue={"venue": "Rozel Point"}), dict(SITE, venue=dict(UMFA, names=None)))
check("a block WITHOUT names still fills every coordless event, as before",
      plain.latitude == UMFA["lat"], plain.latitude)

# --- a town hall's calendar is two calendars ----------------------------------
# Sherwood, OR and Dormont, PA carried council meetings and "CLOSED:" rows beside
# their programme. Refused only where `_found` says the source is a town hall's.
civic_rows = [row(id=1, title="City Council Meeting"), row(id=2, title="Fall Festival"),
              row(id=3, title="CLOSED: Borough Building"), row(id=4, title="Closed Captioned Movie Night")]
store = _Store()
with redirect_stdout(io.StringIO()) as buf:
    kept = ingest(store, _Session(civic_rows),
                  dict(SITE, crawl_delay=0, _found="civic:city -> tribe (hand-curated)"))
check("a civic source refuses its town-hall rows and keeps its programme",
      sorted(e.name for e in store.seen) == ["Closed Captioned Movie Night", "Fall Festival"],
      [e.name for e in store.seen])
check("...and says how many it refused", "2 town-hall row(s) refused" in buf.getvalue(), buf.getvalue())
store = _Store()
with redirect_stdout(io.StringIO()):
    kept = ingest(store, _Session(civic_rows), dict(SITE, crawl_delay=0))
check("a non-civic source keeps every row", kept == 4, kept)

# --- skip_title, as in ics and jsonld -----------------------------------------
# Haydon Wick Parish Council (2026-10-04): "Planning & Highways Meeting" beside
# its memory café, under titles CIVIC_TITLE_RX does not know.
parish_rows = [row(id=1, title="Planning & Highways Meeting"), row(id=2, title="Memory Café"),
               row(id=3, title="Finance & Policy meeting"), row(id=4, title="Meeting Point Walk")]
store = _Store()
with redirect_stdout(io.StringIO()) as buf:
    kept = ingest(store, _Session(parish_rows), dict(SITE, crawl_delay=0, skip_title=r"\bMeeting$"))
check("skip_title drops the titles it matches, case-insensitively, and only those",
      kept == 2 and sorted(e.name for e in store.seen) == ["Meeting Point Walk", "Memory Café"],
      [e.name for e in store.seen])
check("...and says how many", "2 title-filtered" in buf.getvalue(), buf.getvalue())
try:
    ingest(_Store(), _Session(parish_rows), dict(SITE, crawl_delay=0, skip_title="(unclosed"))
    refused = False
except ValueError:
    refused = True
check("an invalid skip_title fails loudly instead of filtering nothing", refused)

# --- a Zoom talk is not at the J ------------------------------------------------
# Peninsula JCC (2026-10-04): 18 of 200 rows were a Zoom discussion group that
# named no venue, so the venue block pinned each one at the centre.
zoom = "<p>Join us on Zoom for a lively conversation.</p>"
online_rows = [row(id=1, title="Virtual Discussion Group", description=zoom, venue=[]),
               row(id=2, title="Book Talk", description=zoom, venue={"venue": "Zoom"}),
               row(id=3, title="Watch Party", description=zoom),                 # has a street
               row(id=4, title="Hybrid Shabbat", venue=[],
            description="<p>In-person in the hall, or on Zoom.</p>"),
               row(id=5, title="Pickleball", venue=[])]                            # says nothing
store = _Store()
with redirect_stdout(io.StringIO()) as buf:
    kept = ingest(store, _Session(online_rows),
                  dict(SITE, crawl_delay=0, venue={"name": "The J", "lat": 37.5, "lon": -122.2}))
check("an online-only row with no place of its own, or a place called Zoom, is refused",
      sorted(e.name for e in store.seen) == ["Hybrid Shabbat", "Pickleball", "Watch Party"],
      [e.name for e in store.seen])
check("...and the run says how many", "2 online with no place refused" in buf.getvalue(), buf.getvalue())

# --- a website without a scheme is no link -------------------------------------
# BCUT Timisoara's rows said "www.bcut.ro"; the event's own page is whole.
check("a scheme-less website falls back to the event's own page",
      T.to_event(row(website="www.bcut.ro"), SITE).ticket_url == "https://example.org/event/velo/")
check("a real website still wins over the event page",
      T.to_event(row(website="https://tickets.example.com/x"), SITE).ticket_url
      == "https://tickets.example.com/x")
check("no website at all keeps the event page, as before",
      T.to_event(row(), SITE).ticket_url == "https://example.org/event/velo/")

# --- the deadline resumes where it stopped, instead of starving the tail -----
# Run 36317891716 (2026-09-27) stopped "before Autodromo Nazionale di Monza: 65
# of 673 sites not started", and every run started at site one, so it was the
# same 65 every night.
sites = [dict(SITE, name=n, base_url=f"https://{n}.example") for n in ("a", "b", "c", "d")]


class _MainStore:
    def __init__(self, _path):
        self.records = {}

    def save(self):
        pass


handed = []                              # the deadline main gives each site


def run_main(tmp, clock_steps, *extra, workers="1"):
    order, clock = [], [1000.0]

    def fake_ingest(_session, site, _ids, deadline, _stop):
        order.append(site["name"])
        handed.append(deadline)
        clock[0] += clock_steps
        return []

    with patch.object(T, "EventStore", _MainStore), patch.object(T, "ingest_site", fake_ingest), \
         patch.object(T.time, "time", lambda: clock[0]), patch.object(T.requests, "Session"), \
         redirect_stdout(io.StringIO()):
        T.main(["--config", os.path.join(tmp, "cfg.json"), "--store", os.path.join(tmp, "s.json"),
                "--deadline", "1015", "--cursor", os.path.join(tmp, "cursor.json"),
                "--workers", workers, *extra])
    return order


# One worker is the old loop, site for site.
with tempfile.TemporaryDirectory() as tmp:
    json.dump({"sites": sites}, open(os.path.join(tmp, "cfg.json"), "w"))
    first = run_main(tmp, 10)            # a, b; the clock passes 1015 before c
    saved = json.load(open(os.path.join(tmp, "cursor.json")))
    second = run_main(tmp, 1)            # resumes at c and walks the list round
check("a run that meets the deadline stops before the next site", first == ["a", "b"], first)
check("...and names that site in the cursor", saved == {"site": "https://c.example"}, saved)
check("the next run starts there and walks round to the front",
      second == ["c", "d", "a", "b"], second)
with tempfile.TemporaryDirectory() as tmp:
    json.dump({"sites": sites}, open(os.path.join(tmp, "cfg.json"), "w"))
    json.dump({"site": "https://gone.example"}, open(os.path.join(tmp, "cursor.json"), "w"))
    check("a cursor naming a removed site starts at the top", run_main(tmp, 1) == ["a", "b", "c", "d"])
with tempfile.TemporaryDirectory() as tmp:
    json.dump({"sites": sites}, open(os.path.join(tmp, "cfg.json"), "w"))
    check("--only reads its one site with one worker or three",
          run_main(tmp, 1, "--only", "C") == ["c"] == run_main(tmp, 1, "--only", "C", workers="3"))
# The step's deadline reaches every site's reader, or a site goes on sleeping
# toward pages past it (the 86.9-minute step of 2026-10-23).
check("every site's reader is handed the step's deadline",
      handed and set(handed) == {1015.0}, sorted(set(handed)))

# A publisher can ignore the category query. Private sessions must be rejected
# before a source's venue fallback turns them into plausible public map pins.
class _CategorySession:
    def __init__(self, pages):
        self.pages = pages
        self.params = []

    def get(self, url, *, params, **kwargs):
        self.params.append(dict(params))
        pages = self.pages
        class Response:
            status_code = 200

            def json(self):
                return {"events": pages[params["page"] - 1], "total_pages": len(pages)}
        return Response()


pages = [
    [row(id=1, title="Community Open House", venue={}, categories=[{"id": 123}]),
     row(id=2, title="Private school session", venue={}, categories=[{"id": 127}]),
     row(id=3, venue={}, categories="123")],
    [row(id=4, title="Public welcome", venue={}, categories=[None, {"slug": "community-open"}]),
     row(id=5, venue={}, categories=[]),
     row(id=6, start_date={}, venue={}, categories=[{"slug": "members-only"}])],
]
session = _CategorySession(pages)
store = _Store()
with redirect_stdout(io.StringIO()) as buf:
    kept = ingest(store, session, dict(SITE_CA, crawl_delay=0,
                                       include_categories=[123, "community-open"]))
check("an ignored category query cannot publish private or unlabelled sessions",
      kept == 2 and [e.source_id for e in store.seen] == ["1", "4"],
      [e.source_id for e in store.seen])
check("both public occurrences retain the source's usable venue pin",
      all(e.latitude == SITE_CA["venue"]["lat"] for e in store.seen))
check("category IDs and slugs are sent on every page to reduce the download",
      len(session.params) == 2 and all(p["categories"] == "123,community-open" for p in session.params),
      session.params)
check("excluded rows are counted without trying to normalize a private poison record",
      "4 outside configured categories" in buf.getvalue() and "unreadable" not in buf.getvalue(),
      buf.getvalue())

session = _CategorySession([[row(id=1), row(id=2, categories=[])]])
store = _Store()
with redirect_stdout(io.StringIO()):
    ingest(store, session, dict(SITE, crawl_delay=0))
check("existing unfiltered sources keep their events and omit the category query",
      len(store.seen) == 2 and "categories" not in session.params[0], session.params)
for invalid in ([], "123", [None], [True], [""], ["public,private"]):
    session = _CategorySession(pages)
    try:
        ingest(_Store(), session, dict(SITE, include_categories=invalid))
        refused = False
    except ValueError:
        refused = True
    check(f"invalid category scope {invalid!r} fails before any request",
          refused and not session.params)

# --- three sites at once, never two on one host (2026-10-23) ------------------
# 586 of 1,408 sites fit in the 80-minute deadline one at a time, at 8.2 s a
# site, most of it each site's own Crawl-delay; the step stopped at the deadline
# in 15 of 15 runs. Workers only read: the store is written in the main thread.
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

# The deadline is checked before every page, and a page that could not start
# before it is not slept for: Haydon Wick's 15 pages at 30 s are ~7 min, which
# is how an 80-minute deadline became an 86.9-minute step.
paged = [[row(id=n, title=f"Ride {n}")] for n in (1, 2, 3)]
for deadline, want_pages, want_sleeps in ((1005.0, [1], []), (2000.0, [1, 2, 3], [10.0, 10.0])):
    session, slept = _CategorySession(paged), []
    with patch.object(T.time, "time", lambda: 1000.0), patch.object(T.time, "sleep", slept.append), \
         redirect_stdout(io.StringIO()) as buf:
        got = T.ingest_site(session, dict(SITE, crawl_delay=10), deadline=deadline)
    asked = [p["page"] for p in session.params]
    if deadline < 2000:
        check("a deadline inside the Crawl-delay ends the site with the page it has, unslept",
              asked == want_pages and slept == want_sleeps and [e.name for e in got] == ["Ride 1"],
              (asked, slept, [e.name for e in got]))
        check("...and its line says where it stopped, as does what it hands back",
              "kept 1 events (stopped at the deadline before page 2) in " in buf.getvalue()
              and got.cut == "stopped at the deadline before page 2", (buf.getvalue(), got.cut))
    else:
        check("with time left every page is read, Crawl-delay apart, and none slept after the last",
              asked == want_pages and slept == want_sleeps and len(got) == 3 and got.cut == "",
              (asked, slept, len(got), got.cut))
stopped_early = threading.Event()
stopped_early.set()
session = _CategorySession(paged)
with redirect_stdout(io.StringIO()):
    got = T.ingest_site(session, dict(SITE, crawl_delay=0), stop=stopped_early)
check("an interrupted run's worker makes no further request", got == [] and session.params == [],
      session.params)
check("...and says it was cut, so main keeps the cursor on it", got.cut == "interrupted before page 1",
      got.cut)


# A page this cannot read ends the site and keeps the pages before it, as the
# rows upserted as they came did until 2026-10-23: an exception that left the
# reader would take every page with it, under main's "FAILED" line.
class _BodySession:
    def __init__(self, bodies):
        self.bodies, self.asked = bodies, []

    def get(self, url, *, params, **kwargs):
        self.asked.append(params["page"])
        body = self.bodies[params["page"] - 1]

        class Response:
            status_code = 200

            def json(self):
                return body
        return Response()


first = {"events": [row(id=1, title="Ride 1"), row(id=2, title="Ride 2")], "total_pages": 3}
for label, second, want in (
        ("a total_pages that is not a number", {"events": [row(id=3, title="Ride 3")],
                                                "total_pages": "3 pages"}, ["Ride 1", "Ride 2", "Ride 3"]),
        ("a body that is JSON null", None, ["Ride 1", "Ride 2"]),
        ("a body that is a list", [row(id=3)], ["Ride 1", "Ride 2"])):
    session = _BodySession([first, second, {"events": [row(id=9, title="Ride 9")], "total_pages": 3}])
    with redirect_stdout(io.StringIO()) as buf:
        got = T.ingest_site(session, dict(SITE, crawl_delay=0))
    check(f"{label} on page 2 keeps the pages before it and asks no further",
          [e.name for e in got] == want and session.asked == [1, 2] and got.cut == ""
          and "test p2 unreadable page: " in buf.getvalue(),
          ([e.name for e in got], session.asked, got.cut, buf.getvalue()))
session = _BodySession([{"events": [["not", "a", "record"], "nor this", row(id=5, title="Ride 5")],
                         "total_pages": 1}])
with redirect_stdout(io.StringIO()) as buf:
    got = T.ingest_site(session, dict(SITE, crawl_delay=0))
check("a record that is not an object costs that record, and its line still prints",
      [e.name for e in got] == ["Ride 5"]
      and "skipping unreadable record <list> (AttributeError" in buf.getvalue()
      and "(2 unreadable record(s) skipped)" in buf.getvalue(), buf.getvalue())


def run_pool(tmp, config_sites, fake, *extra, store=None, clock=None):
    json.dump({"sites": config_sites}, open(os.path.join(tmp, "cfg.json"), "w"))
    with patch.object(T, "EventStore", store or _MainStore), patch.object(T, "ingest_site", fake), \
         patch.object(T.time, "time", (lambda: clock[0]) if clock else T.time.time), \
         patch.object(T.requests, "Session"), redirect_stdout(io.StringIO()) as buf:
        rc = T.main(["--config", os.path.join(tmp, "cfg.json"), "--store", os.path.join(tmp, "s.json"),
              "--cursor", os.path.join(tmp, "cursor.json"), *extra])
    return rc, buf.getvalue()


# Two calendars on one host are never read at once; the next site in list order
# goes instead, and the three distinct hosts ARE read at once (the barrier
# breaks if they are not).
hosted = [dict(SITE, name="a", base_url="https://one.example"),
          dict(SITE, name="b", base_url="https://ONE.example/second-calendar"),
          dict(SITE, name="c", base_url="https://two.example"),
          dict(SITE, name="d", base_url="https://three.example")]
together, guard = threading.Barrier(3, timeout=5), threading.Lock()
live, overlap, starts = {}, [], []


def host_fake(_session, site, _ids, _deadline, _stop):
    host = T._http_host(site["base_url"])
    with guard:
        starts.append(site["name"])
        if live.get(host):
            overlap.append(site["name"])
        live[host] = live.get(host, 0) + 1
    try:
        if site["name"] != "b":
            together.wait()
    finally:
        with guard:
            live[host] -= 1
    return []


with tempfile.TemporaryDirectory() as tmp:
    rc, out = run_pool(tmp, hosted, host_fake, "--workers", "3")
check("three hosts are read at once", not together.broken and sorted(starts[:3]) == ["a", "c", "d"],
      (together.broken, starts))
check("...and a second calendar on a busy host waits for it", starts[3:] == ["b"] and not overlap,
      (starts, overlap))
check("the done line says how many at once", "from 4 of 4 sites (3 at once," in out, out)


# Sites finish out of order. The cursor saved with the store is the first site
# in list order that has not finished, even while later ones have, and no site
# starts once the deadline has passed.
class _RecStore(_MainStore):
    last = None

    def __init__(self, path):
        super().__init__(path)
        self.names, self.saves = [], 0
        _RecStore.last = self

    def upsert(self, ev):
        self.names.append(ev.name)

    def save(self):
        self.saves += 1


letters = [dict(SITE, name=n, base_url=f"https://{n}.example") for n in "abcde"]
clock, go, saves = [1000.0], threading.Event(), []


def ooo_fake(_session, site, _ids, _deadline, _stop):
    if site["name"] == "b":
        clock[0] += 200                  # b finishes first, a checkpoint later
    elif site["name"] == "d":
        clock[0] += 400                  # past the deadline at 1500
        go.set()
    else:
        go.wait(5)                       # a and c are still reading
    return [SimpleNamespace(name=site["name"])]


real_save_cursor = T._save_cursor


def spy_cursor(path, base_url):
    saves.append((base_url, list(_RecStore.last.names), _RecStore.last.saves))
    real_save_cursor(path, base_url)


with tempfile.TemporaryDirectory() as tmp, patch.object(T, "_save_cursor", spy_cursor):
    rc, out = run_pool(tmp, letters, ooo_fake, "--workers", "3", "--deadline", "1500",
                store=_RecStore, clock=clock)
    final = json.load(open(os.path.join(tmp, "cursor.json")))
check("a checkpoint while a is still reading names a, though b has finished",
      saves[:1] == [("https://a.example", ["b"], 1)], saves)
check("...and every checkpoint names the first site not yet written",
      all(url == f"https://{next(n for n in 'abcde' if n not in done)}.example"
          for url, done, _ in saves), saves)
check("no site starts after the deadline", sorted(_RecStore.last.names) == ["a", "b", "c", "d"]
      and "deadline reached before e: 1 of 5 sites not started" in out, (_RecStore.last.names, out))
check("the end saves the store and names the first site never started",
      final == {"site": "https://e.example"} and saves[-1][2] == _RecStore.last.saves, (final, saves))
check("the checkpoints come no more often than SAVE_EVERY_SECONDS",
      _RecStore.last.saves == 3, _RecStore.last.saves)          # 1200, 1600 and the end


# A SITE THE DEADLINE CUT SHORT IS NOT FINISHED. Counted as finished, a cut site
# never held the cursor, so once every site started before the deadline the
# cursor stayed where the run began and the same tail lost its later pages every
# run (a stand-in server, 6 sites and 3 workers: three runs, the same three sites
# "stopped at the deadline before page 3"). The real reader, through main: the
# step's deadline reaches it, so c's page 2 is never asked for, and its page 1
# is still written. Each request moves the step's clock on 2 s.
class _HostPages:
    asked, clock = [], [1000.0]

    def __init__(self, pages):
        self.headers, self.pages = {}, pages

    def get(self, url, *, params, **kwargs):
        host, page = urlsplit(url).hostname, params["page"]
        _HostPages.asked.append((host[0], page))
        _HostPages.clock[0] += 2
        rows, total = [row(id=page, title=f"{host[0]} p{page}", start_date="2099-08-19 06:30:00")], self.pages[host]

        class Response:
            status_code = 200

            def json(self):
                return {"events": rows, "total_pages": total}
        return Response()

    def close(self):
        pass


cut_pages = {"a.example": 1, "b.example": 1, "c.example": 2, "d.example": 1}


def cut_run(tmp):
    _HostPages.asked, _HostPages.clock[0] = [], 1000.0
    cfg = [dict(SITE, name=h[0], base_url=f"https://{h}", crawl_delay=10) for h in cut_pages]
    json.dump({"sites": cfg}, open(os.path.join(tmp, "cfg.json"), "w"))
    store_path = os.path.join(tmp, "store.json")
    with patch.object(T.requests, "Session", lambda: _HostPages(cut_pages)), \
         patch.object(T.time, "time", lambda: _HostPages.clock[0]), redirect_stdout(io.StringIO()) as buf:
        T.main(["--config", os.path.join(tmp, "cfg.json"), "--store", store_path,
                "--cursor", os.path.join(tmp, "cursor.json"), "--deadline", "1005", "--workers", "1"])
    names = sorted(r["name"] for r in T.EventStore(store_path).records.values())
    return json.load(open(os.path.join(tmp, "cursor.json"))), names, buf.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    # a 1002, b 1004, c starts at 1004 and its page 1 ends at 1006; d never starts.
    first = cut_run(tmp)
    first_asked = list(_HostPages.asked)
    # From c: c is cut again, but it HAD the whole step; d, a run, b never starts.
    second = cut_run(tmp)
    second_asked = list(_HostPages.asked)
check("a site the deadline cut short keeps the cursor, though a later site never started",
      first[0] == {"site": "https://c.example"}, first[0])
check("...its page 2 is never asked for, and its page 1 is written",
      first_asked == [("a", 1), ("b", 1), ("c", 1)] and first[1] == ["a p1", "b p1", "c p1"],
      (first_asked, first[1]))
check("...and the done line says the deadline stopped the step",
      "from 3 of 4 sites" in first[2] and "(stopped at the step deadline) (1 site(s) cut short)" in first[2],
      first[2])
check("the next run starts at the cut site",
      second_asked[:1] == [("c", 1)] and "resuming at c (site 3 of 4)" in second[2], (second_asked, second[2]))
check("...and a site cut short with the whole step in front of it lets the cursor go on",
      second_asked == [("c", 1), ("d", 1), ("a", 1)] and second[0] == {"site": "https://b.example"},
      (second_asked, second[0]))


# EVERY SITE STARTED, AND THE LAST ONES IN FLIGHT WERE CUT. That is the case that
# stalled: no site was refused a start, so nothing but the cut sites themselves
# could move the cursor, and the run's done line said nothing of a deadline.
def cut_fake(cut_names, seen):
    def fake(_session, site, _ids, _deadline, _stop):
        seen.append(site["name"])
        got = T.SiteEvents([SimpleNamespace(name=site["name"])])
        if site["name"] in cut_names:
            got.cut = "stopped at the deadline before page 3"
        return got
    return fake


six_letters = [dict(SITE, name=n, base_url=f"https://{n}.example") for n in "abcdef"]
with tempfile.TemporaryDirectory() as tmp:
    seen_cut, seen_next = [], []
    _, cut_out = run_pool(tmp, six_letters, cut_fake({"d", "e", "f"}, seen_cut), "--workers", "3",
                          "--deadline", str(T.time.time() + 3600), store=_RecStore)
    cut_cursor = json.load(open(os.path.join(tmp, "cursor.json")))
    cut_written = sorted(_RecStore.last.names)
    _, next_out = run_pool(tmp, six_letters, cut_fake(set(), seen_next), "--workers", "3",
                           "--deadline", str(T.time.time() + 3600), store=_RecStore)
    next_cursor = json.load(open(os.path.join(tmp, "cursor.json")))
check("every site started and the last three in flight were cut: the cursor names the first of them",
      sorted(seen_cut) == list("abcdef") and cut_cursor == {"site": "https://d.example"}
      and cut_written == list("abcdef"), (seen_cut, cut_cursor, cut_written))
check("...and the done line says the deadline stopped the step, though every site started",
      "from 6 of 6 sites" in cut_out and "(stopped at the step deadline) (3 site(s) cut short)" in cut_out
      and "not started" not in cut_out, cut_out)
check("the next run starts at the first cut site and skips none of them",
      "resuming at d (site 4 of 6)" in next_out and sorted(seen_next) == list("abcdef")
      and "(stopped at the step deadline)" not in next_out, (seen_next, next_out))
check("...and having read every site whole, leaves the cursor where it began",
      next_cursor == {"site": "https://d.example"}, next_cursor)


# An interrupt saves what was read; the site still reading is not finished, so
# the cursor stays on it, and its worker stops before another request.
class _InterruptedStore(_RecStore):
    def upsert(self, ev):
        if ev.name == "b":
            raise KeyboardInterrupt
        super().upsert(ev)


a_stopped, a_out = [], threading.Event()


def interrupt_fake(_session, site, _ids, _deadline, stop):
    if site["name"] == "a":
        a_stopped.append(stop.wait(5))
        a_out.set()
    return [SimpleNamespace(name=site["name"])]


with tempfile.TemporaryDirectory() as tmp:
    rc, out = run_pool(tmp, letters, interrupt_fake, "--workers", "2",
                "--deadline", str(T.time.time() + 3600),
                store=_InterruptedStore)
    a_out.wait(5)
    final = json.load(open(os.path.join(tmp, "cursor.json")))
check("an interrupt saves the store and keeps the cursor on the site still reading",
      rc == 130 and final == {"site": "https://a.example"} and _RecStore.last.saves == 1,
      (rc, final, _RecStore.last.saves))
check("...stops that site's worker, and starts nothing more",
      a_stopped == [True] and "from 2 of 5 sites" in out, (a_stopped, out))


# The whole path, real reader and real store: one Session per worker thread,
# every site's events written, the per-site time on every line, the store moved
# into place whole, and `free_only` still sees the rows the store holds.
class _ThreadSession:
    made = []

    def __init__(self, rows=None):
        self.headers, self.threads, self.closed, self.rows = {}, set(), False, rows
        _ThreadSession.made.append(self)

    def get(self, url, *, params, **kwargs):
        self.threads.add(threading.get_ident())
        rows = self.rows or [row(id=7, title=f"Ride from {urlsplit(url).hostname}",
                          start_date="2099-08-19 06:30:00")]

        class Response:
            status_code = 200

            def json(self):
                return {"events": rows, "total_pages": 1}
        return Response()

    def close(self):
        self.closed = True


six = [dict(SITE, name=f"site {n}", base_url=f"https://s{n}.example", crawl_delay=0) for n in range(6)]
with tempfile.TemporaryDirectory() as tmp:
    json.dump({"sites": six}, open(os.path.join(tmp, "cfg.json"), "w"))
    store_path = os.path.join(tmp, "store.json")
    with patch.object(T.requests, "Session", _ThreadSession), redirect_stdout(io.StringIO()) as buf:
        rc = T.main(["--config", os.path.join(tmp, "cfg.json"), "--store", store_path, "--workers", "3"])
    names = sorted(r["name"] for r in T.EventStore(store_path).records.values())
    leftovers = sorted(f for f in os.listdir(tmp) if f.endswith(".tmp"))
kept_lines = [ln for ln in buf.getvalue().splitlines() if ": kept " in ln]
check("every site's events reach the store", rc == 0 and names == sorted(
      f"Ride from s{n}.example" for n in range(6)), names)
check("each worker has its own Session, and closes it",
      0 < len(_ThreadSession.made) <= 3 and all(len(s.threads) <= 1 and s.closed for s in _ThreadSession.made),
      [(len(s.threads), s.closed) for s in _ThreadSession.made])
check("every site's line ends with its time",
      len(kept_lines) == 6 and all(re.search(r"kept 1 events in \d+\.\ds$", ln) for ln in kept_lines),
      kept_lines)
check("the store is written beside itself and moved into place", leftovers == [], leftovers)


# ...and a save that dies half-way leaves the last whole store, which is what the
# move is for: the store is now written every SAVE_EVERY_SECONDS, not once.
class _HalfStore:
    def __init__(self, path, dies):
        self.path, self.dies, self.wrote_to = Path(path), dies, []

    def save(self):
        self.wrote_to.append(self.path.name)
        self.path.write_text('{"events": [' if self.dies else '{"events": []}', encoding="utf-8")
        if self.dies:
            raise OSError("No space left on device")


with tempfile.TemporaryDirectory() as tmp:
    store_path = os.path.join(tmp, "feeds_events.json")
    open(store_path, "w").write('{"events": [{"name": "kept"}]}')
    half = _HalfStore(store_path, dies=True)
    try:
        T._save_store(half)
        raised = False
    except OSError:
        raised = True
    survived = open(store_path).read()
    whole = _HalfStore(store_path, dies=False)
    T._save_store(whole)
    replaced = open(store_path).read()
    leftovers = sorted(f for f in os.listdir(tmp) if f.endswith(".tmp"))
check("a save that dies half-way leaves the last whole store",
      raised and half.wrote_to == ["feeds_events.json.tmp"] and survived == '{"events": [{"name": "kept"}]}'
      and half.path == Path(store_path), (raised, half.wrote_to, survived, half.path))
check("...and the next whole save takes its place",
      whole.wrote_to == ["feeds_events.json.tmp"] and replaced == '{"events": []}' and leftovers == [],
      (whole.wrote_to, replaced, leftovers))

with tempfile.TemporaryDirectory() as tmp:
    site = dict(SITE, crawl_delay=0, free_only=True, currency="USD")
    json.dump({"sites": [site]}, open(os.path.join(tmp, "cfg.json"), "w"))
    store_path = os.path.join(tmp, "store.json")
    for cost in ("Free", "12.50"):
        rows = [row(id=541, start_date="2099-08-19 06:30:00", cost=cost)]
        with patch.object(T.requests, "Session", lambda: _ThreadSession(rows)), \
             redirect_stdout(io.StringIO()) as buf:
            T.main(["--config", os.path.join(tmp, "cfg.json"), "--store", store_path])
    details = [r.get("source_details") for r in T.EventStore(store_path).records.values()]
check("main reads a free_only site's known rows first, so a turn to paid still lands",
      len(details) == 1 and details[0].get("free") is False and "kept 1 events" in buf.getvalue(),
      (details, buf.getvalue()))

with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TRIBE_WORKERS": "1"}):
    rc, out = run_pool(tmp, sites, lambda *a: [])
check("TRIBE_WORKERS sets the default", "(1 at once," in out, out)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
