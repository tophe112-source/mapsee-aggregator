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
import sys
import tempfile
from copy import deepcopy
from contextlib import redirect_stdout
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
        T.ingest_site(_Store(), calendar_session, calendar_site)
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
    scope_kept = T.ingest_site(scope_store, _Session(scoped_rows),
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
        T.ingest_site(store, _Session([initial]), site)
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
            kept = T.ingest_site(store, _Session([updated_row]), site)
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
    T.ingest_site(store, _Session([raw_free]), site)
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
        T.ingest_site(store, _Session([raw_unknown]), site)
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
        T.ingest_site(store, _Session([row(id=542, cost=None)]),
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
        collision_kept = T.ingest_site(store, _Session([free_from_b]), site_b)
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
with tempfile.TemporaryDirectory(prefix="tribe-scoped-source-ids-") as tmp:
    path = os.path.join(tmp, "events.json")
    site_a = dict(SITE, base_url="https://calendar-a.example", crawl_delay=0)
    site_b = dict(SITE, base_url="https://calendar-b.example", crawl_delay=0)
    first = row(id=544, title="Astronomy Night", start_date="2099-08-19 19:00:00",
                url="https://calendar-a.example/events/astronomy-night/")
    second = row(id=544, title="Garden Work Party", start_date="2099-08-20 10:00:00",
                 url="https://calendar-b.example/events/garden-work-party/")
    store = T.EventStore(path)
    T.ingest_site(store, _Session([first]), site_a)
    store.save()
    store = T.EventStore(path)
    T.ingest_site(store, _Session([second]), site_b)
    rekeyed_total = store.stats["rekeyed"]
    store.save()
    store = T.EventStore(path)
    names = sorted(rec["name"] for rec in store.records.values())
    source_keys = set(store.source_to_fp)
check("different Tribe hosts with the same numeric ID keep two persisted events without rekeying",
      names == ["Astronomy Night", "Garden Work Party"]
      and rekeyed_total == 0
      and ("tribe", "calendar-a.example", "544") in source_keys
      and ("tribe", "calendar-b.example", "544") in source_keys
      and {(ref.get("publisher"), ref.get("source_id"))
           for rec in store.records.values() for ref in rec.get("sources", [])
           if ref.get("source") == "tribe"} == {
               ("calendar-a.example", "544"), ("calendar-b.example", "544")}
      and all("admission_publisher" not in rec for rec in store.records.values()),
      (names, rekeyed_total, source_keys))

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
        T.ingest_site(store, _Session([second]),
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
    T.ingest_site(store, _Session([original]), site)
    store.save()
    store = T.EventStore(path)
    T.ingest_site(store, _Session([moved]), site)
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
    kept = T.ingest_site(store, _Session([row(id=1), poison, row(id=2, title="Second Ride",
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
    kept = T.ingest_site(store, _Session(civic_rows),
                         dict(SITE, crawl_delay=0, _found="civic:city -> tribe (hand-curated)"))
check("a civic source refuses its town-hall rows and keeps its programme",
      sorted(e.name for e in store.seen) == ["Closed Captioned Movie Night", "Fall Festival"],
      [e.name for e in store.seen])
check("...and says how many it refused", "2 town-hall row(s) refused" in buf.getvalue(), buf.getvalue())
store = _Store()
with redirect_stdout(io.StringIO()):
    kept = T.ingest_site(store, _Session(civic_rows), dict(SITE, crawl_delay=0))
check("a non-civic source keeps every row", kept == 4, kept)

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


def run_main(tmp, clock_steps):
    order, clock = [], [1000.0]

    def fake_ingest(_store, _session, site):
        order.append(site["name"])
        clock[0] += clock_steps
        return 1

    with patch.object(T, "EventStore", _MainStore), patch.object(T, "ingest_site", fake_ingest), \
         patch.object(T.time, "time", lambda: clock[0]), patch.object(T.requests, "Session"), \
         redirect_stdout(io.StringIO()):
        T.main(["--config", os.path.join(tmp, "cfg.json"), "--store", os.path.join(tmp, "s.json"),
                "--deadline", "1015", "--cursor", os.path.join(tmp, "cursor.json")])
    return order


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
    kept = T.ingest_site(store, session, dict(SITE_CA, crawl_delay=0,
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
    T.ingest_site(store, session, dict(SITE, crawl_delay=0))
check("existing unfiltered sources keep their events and omit the category query",
      len(store.seen) == 2 and "categories" not in session.params[0], session.params)
for invalid in ([], "123", [None], [True], [""], ["public,private"]):
    session = _CategorySession(pages)
    try:
        T.ingest_site(_Store(), session, dict(SITE, include_categories=invalid))
        refused = False
    except ValueError:
        refused = True
    check(f"invalid category scope {invalid!r} fails before any request",
          refused and not session.params)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
