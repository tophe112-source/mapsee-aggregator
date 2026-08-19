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
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_tribe as T

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

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
