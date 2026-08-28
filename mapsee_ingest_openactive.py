#!/usr/bin/env python3
"""
mapsee_ingest_openactive.py — UK physical-activity sessions, from OpenActive.

WHY THIS SOURCE. `running`, `fitness` and `volunteer` are the three thinnest
columns the catalog has. Measured from coverage_history.jsonl on 2026-08-25:

    volunteer 11    running 22    kids 25    outdoors 26    fitness 34
    ...against       market 510   community 378

parkrun filled `running` the same way this fills the rest of that row: one
adapter, many countries' worth of supply, every record carrying its own
coordinates. OpenActive is a Sport England-backed open standard — leisure
operators, national governing bodies and community clubs publish their sessions
as machine-readable feeds under a common vocabulary.

MEASURED 2026-08-26, walking all five catalogs in the published collection
(https://openactive.io/data-catalogs/data-catalog-collection.jsonld):

    175 dataset landing pages, 127 of which parse
    127 / 127 are CC-BY 4.0                        <- a whole national catalogue
     34 publish dated sessions or events              on one licence
     93 publish ONLY bookable facility slots (courts, pitches, halls)

The 93 are not events and this adapter does not read them: a bookable badminton
court at 19:00 is an empty room somebody may or may not take, and putting one on
a map of what is happening would be the Traders Village mistake — the feed
works, and it is not what it looks like.

IT BRINGS ITS OWN COORDINATES, WHICH IS THE WHOLE REASON IT IS CHEAP. The only
geocoder in this pipeline is US Census, so outside the United States a source
must carry lat/lon or the sync silently drops the row (see
mapsee_ingest_tribe's 43 coordless Calgary events, which reported "kept 43" and
placed none). Measured on one publisher: 499/500 records carry `location.geo`
and 500/500 carry a structured PostalAddress; across 16 feeds, 1,952/2,137
(91%) carry coordinates. Anything without one is dropped HERE, where the count
can still be printed.

    python mapsee_ingest_openactive.py --config openactive_sources.json \
        --store feeds_events.json [--dry-run] [--only goodgym]

Env: none. Every feed is public and unauthenticated.

------------------------------------------------------------------------------
THE FOUR THINGS THAT WILL BITE YOU
------------------------------------------------------------------------------

1. RPDE'S FIRST PAGE IS THE OLDEST DATA, so reading one page tells you nothing.

   These are RPDE feeds (Realtime Paged Data Exchange): items are ordered by
   MODIFICATION TIME ASCENDING and you follow `next` until the page repeats.
   Page one is therefore the oldest corner of the archive. Measured 2026-08-26:

       Our Parks         page 1 says 0 future   walked to the end: 854 future
       England Netball   page 1 says 0 future   walked to the end:   0 future

   Our Parks is a live feed of free outdoor fitness classes in London parks and
   looks stone dead from page one. England Netball looks equally dead and IS —
   14,798 records, latest start date 2019, still answering 200 and still listed
   in the official catalog seven years later. A verifier that reads page one
   gets both of those wrong, in opposite directions. Walk to the end. This is
   the same family as BikeReg's search endpoint answering 200 with a silent
   hundred-row ceiling, and as "counting records is not checking dates".

2. A ScheduledSession DOES NOT KNOW ITS OWN NAME. It carries the occurrence's
   startDate and a `superEvent` URL, and nothing else a human could read — no
   title, no place, no price. Those live on the SessionSeries the superEvent
   points at. So a publisher on that shape needs BOTH feeds read and joined, and
   a ScheduledSession whose series never arrives is dropped rather than guessed
   at. The plain `Event` publishers (GoodGym, Our Parks) carry everything on one
   record and need no join.

3. A SOURCE CAN HAND YOU A SENTINEL DATE. British Cycling's Let's Ride returns
   172 rides dated in the YEAR 2500. That is the "recurring event with no end
   date projects pins forever" trap arriving pre-made from the publisher, and
   mapsee_cleanup.py can never remove it — a 2500 ride is not past and never
   will be. `horizon_days` drops them here, where the count that was dropped can
   still be printed.

4. CC-BY IS A LICENCE WITH A CONDITION, and the condition is the attribution.
   Every row carries its publisher's name and the licence in the description,
   the way the OSM adapters carry ODbL. That line is not decoration; it is the
   term on which we are allowed to hold the data at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mapsee_ingest import EventStore, NormalizedEvent

UA = "mapsee-aggregator/1.0 (+https://mapsee.me; OpenActive session import)"

# The kinds this adapter can turn into a dated row. Anything else in a
# publisher's distribution list is ignored by name rather than by guess —
# FacilityUse and Slot are bookable rooms, not occasions.
EVENT_KINDS = ("Event", "OnDemandEvent")
SERIES_KINDS = ("SessionSeries", "CourseInstance")
OCCURRENCE_KINDS = ("ScheduledSession", "ScheduledSessions")

# A page cap, so one publisher cannot hold a run open for ever. It is LOUD when
# it bites (see _walk): a silent cap reads as "we read the whole feed" when we
# did not, which is the one thing an RPDE reader must never imply.
MAX_PAGES = 250

# How far ahead a session may start before we treat the date as furniture rather
# than a plan. See lesson 3 — Let's Ride ships the year 2500.
#
# Measured before changing it, because it looked like the obvious lever for the
# London pool and it is NOT: in a ±0.03 central-London box, every WEEK inside 42
# days fills the 800-row cap, while 42-120 days holds 82, 87 and 80 rows and
# beyond 120 holds one. Cutting this to 42 like its sibling adapters would drop
# ~250 rows out of thousands and fix nothing. The density is near-term and real;
# what is not real is the booking grid — see collapse_booking_grids.
DEFAULT_HORIZON_DAYS = 120

# How many rows one title at one venue may have in ONE day before the publisher
# is describing a booking grid rather than a schedule. Six is comfortably above
# a real programme — the measurement that prompted this found 308 of 321
# title/venue pairs occurring exactly once in a week, and the busiest genuine
# one four times — and far below a grid, which ran 110 in a day at ten-minute
# spacing. Per-source override: `grid_min_per_day`.
GRID_MIN_PER_DAY = 6


# ---------------------------------------------------------------------------
# RPDE
# ---------------------------------------------------------------------------
def _get(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def walk(url: str, max_pages: int = MAX_PAGES, delay: float = 0.3,
         sleep=time.sleep) -> Tuple[Dict[str, dict], str]:
    """Read an RPDE feed to its end and return {id: data} plus why we stopped.

    THE TERMINATION RULE IS THE SPEC'S, NOT A GUESS. An RPDE feed is exhausted
    when a page comes back with an empty `items` array, or when its `next` is
    the URL we just asked for. Both are checked: an implementation that returns
    a self-referential `next` alongside an empty page (several do) would
    otherwise spin until the cap.

    `state: "deleted"` is a TOMBSTONE and must remove the record, not add it —
    that is how a publisher cancels a session. Dropping the tombstone on the
    floor leaves a cancelled class on the map, which is worse than never having
    listed it.
    """
    seen: Dict[str, dict] = {}
    prev: Optional[str] = None
    pages = 0
    while url and pages < max_pages:
        try:
            payload = _get(url)
        except Exception as exc:            # noqa: BLE001 - reported, not raised
            # Partial is honest: return what we read and SAY it was partial. A
            # feed that dies on page nine has still told us about eight pages.
            return seen, f"stopped after {pages} page(s): {type(exc).__name__}: {exc}"
        pages += 1
        items = payload.get("items") or []
        if not items:
            return seen, f"end of feed ({pages} page(s))"
        for item in items:
            key = str(item.get("id"))
            if item.get("state") == "deleted":
                seen.pop(key, None)
            else:
                seen[key] = item.get("data") or {}
        nxt = payload.get("next")
        if not nxt or nxt == url or nxt == prev:
            return seen, f"end of feed ({pages} page(s))"
        prev, url = url, nxt
        if delay:
            sleep(delay)
    # LOUD. See MAX_PAGES.
    return seen, (f"PAGE CAP HIT at {max_pages} pages — this feed was NOT read to "
                  f"the end and its newest sessions are missing")


# ---------------------------------------------------------------------------
# Reading one record
# ---------------------------------------------------------------------------
def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def coords(place: Any) -> Optional[Tuple[float, float]]:
    """(lat, lon) from an OpenActive Place, or None.

    A geo block with a zero or absent pair is NOT a location — same rule as
    Squarespace's default Manhattan pin and WP Event Manager's "-" placeholder.
    0,0 is in the Atlantic and is what an unfilled form serialises to.
    """
    if not isinstance(place, dict):
        return None
    geo = place.get("geo")
    if not isinstance(geo, dict):
        return None
    try:
        lat = float(geo.get("latitude"))
        lon = float(geo.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    if abs(lat) < 1e-7 and abs(lon) < 1e-7:
        return None
    return lat, lon


def address_parts(place: Any) -> Dict[str, Optional[str]]:
    """Street / city / region / postcode from a PostalAddress.

    OpenActive publishes a STRUCTURED address, so nothing here has to guess a
    boundary the way _split_maplink does for Squarespace's glued-together line.
    The one judgement is that `streetAddress` may legitimately be absent, and a
    city name must never be promoted into the street field — a city in `address`
    gets geocoded as a street and pins the session on a real road nobody is
    standing in (the MyListing lesson).
    """
    out = {"address": None, "city": None, "region": None, "postal_code": None}
    if not isinstance(place, dict):
        return out
    addr = place.get("address")
    if isinstance(addr, str):
        # A few publishers ship the address as a bare string. It is a street
        # line often enough to keep, and never a city on its own.
        out["address"] = addr.strip() or None
        return out
    if not isinstance(addr, dict):
        return out
    out["address"] = (addr.get("streetAddress") or "").strip() or None
    out["city"] = (addr.get("addressLocality") or "").strip() or None
    out["region"] = (addr.get("addressRegion") or "").strip() or None
    out["postal_code"] = (addr.get("postalCode") or "").strip() or None
    return out


_TAGS = re.compile(r"<[^>]+>")


def _text(value: Any, limit: int = 900) -> Optional[str]:
    if not value:
        return None
    text = _TAGS.sub(" ", str(value))
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&quot;", '"'))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def is_free(record: dict) -> bool:
    """Does every published offer cost nothing?

    Deliberately ALL and not ANY: a session with a free taster and a paid block
    is not a free session, and saying so on the map is the kind of well-formed,
    plausible, wrong this pipeline works hardest to avoid.
    """
    offers = record.get("offers")
    if not isinstance(offers, list) or not offers:
        return False
    prices = []
    for offer in offers:
        if not isinstance(offer, dict) or "price" not in offer:
            return False
        try:
            prices.append(float(offer["price"]))
        except (TypeError, ValueError):
            return False
    return bool(prices) and all(p == 0 for p in prices)


def activity_names(record: dict) -> List[str]:
    out = []
    for act in (record.get("activity") or []):
        if isinstance(act, dict):
            label = (act.get("prefLabel") or act.get("name") or "").strip()
        else:
            label = str(act or "").strip()
        if label and label not in out:
            out.append(label)
    return out


# ---------------------------------------------------------------------------
# One publisher's records -> NormalizedEvents
# ---------------------------------------------------------------------------
def merge_occurrence(occurrence: dict, series: dict) -> dict:
    """A ScheduledSession wearing its SessionSeries' clothes.

    Lesson 2 in the header: the occurrence owns the DATE and the series owns
    everything a person could read. The occurrence wins on dates because that is
    the only fact it has and the whole reason it exists — a series carries an
    `eventSchedule` describing the pattern, and reading THAT as the occurrence
    would give every session in a fifty-week block the same Monday, which is the
    MyListing two-dates bug exactly.
    """
    merged = dict(series)
    merged.update({k: v for k, v in occurrence.items()
                   if v is not None and k not in ("@type", "@context", "superEvent")})
    # An occurrence rarely carries these; when it does not, the series' stand.
    for key in ("name", "description", "location", "offers", "activity", "organizer",
                "url", "image", "genderRestriction", "ageRange"):
        if not merged.get(key) and series.get(key) is not None:
            merged[key] = series[key]
    return merged


def _series_key(occurrence: dict) -> Optional[str]:
    sup = occurrence.get("superEvent")
    if isinstance(sup, str):
        return sup
    if isinstance(sup, dict):
        return sup.get("@id") or sup.get("id")
    return None


def to_event(record: dict, source: dict, now: datetime,
             horizon_days: int) -> Tuple[Optional[NormalizedEvent], Optional[str]]:
    """One dated row, or (None, why-not). The reasons are counted by the caller.

    Every refusal here is a refusal the header argues for. None of them is a
    tidy-up: a session with no coordinates cannot be placed, a session past the
    horizon is furniture, and a session with no name is not a listing.
    """
    start = _parse_dt(record.get("startDate"))
    if not start:
        return None, "no start date"
    if start < now:
        return None, "past"
    if start > now + timedelta(days=horizon_days):
        return None, "beyond horizon"
    name = _text(record.get("name"), 160)
    if not name:
        return None, "no name"

    place = record.get("location")
    latlon = coords(place)
    if not latlon:
        # The honest count. Outside the US nothing downstream can rescue this.
        return None, "no coordinates"
    lat, lon = latlon
    parts = address_parts(place)

    end = _parse_dt(record.get("endDate"))
    venue = None
    if isinstance(place, dict):
        venue = _text(place.get("name"), 120)

    lines = []
    body = _text(record.get("description"))
    if body:
        lines.append(body)
    acts = activity_names(record)
    if acts:
        lines.append("🏃 Activity: " + " · ".join(acts[:4]))
    if is_free(record):
        lines.append("🎟 Free to attend.")
    instructions = _text(record.get("attendeeInstructions"), 300)
    if instructions:
        lines.append("ℹ️ " + instructions)
    organizer = record.get("organizer") or record.get("organiser")
    org_name = None
    if isinstance(organizer, dict):
        org_name = _text(organizer.get("name"), 120)
    # THE LICENCE CONDITION, not a footer. See lesson 4.
    lines.append(f"Session data published by {source['name']} via OpenActive, "
                 f"licensed CC-BY 4.0.")
    description = "\n\n".join(lines)

    url = record.get("url") or record.get("@id") or record.get("id")
    url = url if isinstance(url, str) and url.startswith("http") else None

    image = None
    img = record.get("image")
    if isinstance(img, list) and img:
        img = img[0]
    if isinstance(img, dict):
        image = img.get("url")
    elif isinstance(img, str):
        image = img
    image = image if isinstance(image, str) and image.startswith("http") else None

    # IDENTITY IS THE OCCURRENCE, NOT THE SERIES. A weekly class publishes one
    # series and fifty-two occurrences; keying on the series id would make each
    # week delete the one before it, which is BikeReg's collision bug exactly
    # (1,269 ingested, 1,148 surviving, every casualty an earlier date).
    ident = str(record.get("@id") or record.get("id") or f"{name}|{venue}")
    ref = f"{source['name']}|{ident}|{start.date().isoformat()}"
    return NormalizedEvent(
        source=f"openactive:{source['name']}",
        source_id=ident[:200],
        fingerprint=hashlib.sha1(f"openactive|{ref}".encode("utf-8")).hexdigest(),
        name=name,
        description=description,
        start_utc=start.isoformat(),
        end_utc=end.isoformat() if end and end > start else None,
        venue_name=venue,
        latitude=lat, longitude=lon,
        address=parts["address"],
        city=parts["city"],
        region=parts["region"] or source.get("region"),
        country=source.get("country") or "GB",
        postal_code=parts["postal_code"],
        category=source.get("category") or "fitness",
        categories=list(source.get("secondary") or []),
        promoter=org_name or source["name"],
        ticket_url=url,
        poster_image_url=image,
        # OpenActive publishes a surveyed point for the venue; the address text
        # is derived from it, not the other way round. Never geocode over it.
        coords_exact=True,
    ), None


def _grid_key(ev: NormalizedEvent) -> Tuple[str, float, float, str]:
    """What makes two rows the same thing on the same day.

    The title, the point, and the LOCAL date. `start_utc` holds the offset the
    publisher sent, so its first ten characters are already the local day —
    which is the day a person would be looking at, and not the UTC one that
    puts a 00:30 session on the previous date.
    """
    return ((ev.name or "").strip().lower(),
            round(ev.latitude or 0.0, 5), round(ev.longitude or 0.0, 5),
            (ev.start_utc or "")[:10])


def collapse_booking_grids(events: List[NormalizedEvent], min_per_day: int
                           ) -> Tuple[List[NormalizedEvent], int, List[str]]:
    """A session published every ten minutes is a BOOKING GRID, not a schedule.

    This repo already refuses `FacilityUse` and `Slot`, on the grounds that a
    bookable badminton court at 19:00 is an empty room somebody may or may not
    take. The same thing arrives through the front door as `ScheduledSession`
    and passes that refusal: measured 2026-08-28 in a ±0.03 box on central
    London, ONE pool published "Swim For Fitness" 255 times in a week — 110 of
    them on a single day, at ten-minute spacing, from 05:40. Three title/venue
    pairs like it were about half of the 800 rows events_near will return for
    that viewport, and events_near sorts every candidate in the box to take its
    top N, so that grid is most of why central London exceeds the API role's ~3s
    statement timeout while Seattle and New York do not.

    Nothing here is a tidy-up and nothing is thrown away silently. A grid still
    reaches the map — as ONE row for the day, opening when the first slot opens
    and closing when the last one closes, saying in its own description how many
    bookable slots it stands for. That is a truer listing than any single
    ten-minute slice of it: "the pool is open 05:40–21:00 and you book a lane"
    is the fact, and 110 rows saying so is the map shouting.

    The threshold is per DAY at ONE venue for ONE title, because that is the
    shape a grid has and a real programme does not: the same measurement found
    "PT Taster Session" four times in a WEEK, and 308 of 321 distinct
    title/venue pairs occurred exactly once. A publisher whose classes really do
    run six times a day can raise `grid_min_per_day` in its config entry.
    """
    if min_per_day <= 1:
        return events, 0, []
    groups: Dict[Tuple[str, float, float, str], List[NormalizedEvent]] = {}
    for ev in events:
        groups.setdefault(_grid_key(ev), []).append(ev)
    out: List[NormalizedEvent] = []
    dropped = 0
    notes: List[str] = []
    for key, rows in groups.items():
        if len(rows) < min_per_day:
            out.extend(rows)
            continue
        rows.sort(key=lambda e: e.start_utc or "")
        first = rows[0]
        # The day's real window: the first slot's start to the LAST slot's end,
        # falling back to the last start when a slot carries no end.
        last_end = max((e.end_utc or e.start_utc or "") for e in rows)
        first.end_utc = last_end if last_end > (first.start_utc or "") else first.end_utc
        # Say what it stands for, in the row itself — the count that was dropped
        # has to survive somewhere a reader can see it.
        first.description = (
            f"🎟 {len(rows)} bookable slots on this day, from "
            f"{(first.start_utc or '')[11:16]} to {last_end[11:16]}.\n\n"
            + (first.description or "")).strip()
        # IDENTITY IS THE DAY NOW, not the slot that happened to be first. Keyed
        # on the slot's own id, a pool opening at 05:50 instead of 05:40
        # tomorrow would write a second row and orphan today's.
        name, lat, lon, day = key
        ref = f"grid|{first.source}|{name}|{lat},{lon}|{day}"
        first.source_id = ref[:200]
        first.fingerprint = hashlib.sha1(ref.encode("utf-8")).hexdigest()
        out.append(first)
        dropped += len(rows) - 1
        notes.append(f"{first.name} @ {first.venue_name or f'{lat},{lon}'} {day}: "
                     f"{len(rows)} slots -> 1")
    return out, dropped, notes


def read_source(source: dict, horizon_days: int, max_pages: int,
                delay: float, now: Optional[datetime] = None) -> Tuple[List[NormalizedEvent], Dict[str, Any]]:
    """Every dated row one publisher has, plus a report of what was refused."""
    now = now or datetime.now(timezone.utc)
    feeds = {str(f.get("kind")): f.get("url") for f in (source.get("feeds") or []) if f.get("url")}
    notes: List[str] = []
    records: List[dict] = []

    for kind in EVENT_KINDS:
        if kind in feeds:
            data, why = walk(feeds[kind], max_pages, delay)
            notes.append(f"{kind}: {len(data)} record(s), {why}")
            records.extend(data.values())

    occurrence_kind = next((k for k in OCCURRENCE_KINDS if k in feeds), None)
    series_kind = next((k for k in SERIES_KINDS if k in feeds), None)
    if occurrence_kind:
        if not series_kind:
            # A ScheduledSession feed with no series feed is unreadable, not
            # empty. Saying so is the point — see lesson 2.
            notes.append(f"{occurrence_kind}: SKIPPED — no SessionSeries feed to join "
                         f"against, so no occurrence has a name or a place")
        else:
            series_data, why_s = walk(feeds[series_kind], max_pages, delay)
            occ_data, why_o = walk(feeds[occurrence_kind], max_pages, delay)
            notes.append(f"{series_kind}: {len(series_data)} record(s), {why_s}")
            notes.append(f"{occurrence_kind}: {len(occ_data)} record(s), {why_o}")
            by_id = {}
            for rec in series_data.values():
                key = rec.get("@id") or rec.get("id")
                if key:
                    by_id[str(key)] = rec
            orphans = 0
            for occ in occ_data.values():
                key = _series_key(occ)
                series = by_id.get(str(key)) if key else None
                if not series:
                    orphans += 1
                    continue
                records.append(merge_occurrence(occ, series))
            if orphans:
                notes.append(f"{occurrence_kind}: {orphans} occurrence(s) dropped — "
                             f"superEvent names a series this feed did not carry")
    elif series_kind and not any(k in feeds for k in EVENT_KINDS):
        # A SessionSeries on its own describes a PATTERN, not occurrences. Some
        # publishers put a real startDate on it; those are readable. The rest
        # fail the "no start date" test below and are counted, not guessed at.
        data, why = walk(feeds[series_kind], max_pages, delay)
        notes.append(f"{series_kind} (no occurrence feed): {len(data)} record(s), {why}")
        records.extend(data.values())

    kept: List[NormalizedEvent] = []
    refused: Dict[str, int] = {}
    for rec in records:
        event, why = to_event(rec, source, now, horizon_days)
        if event:
            kept.append(event)
        else:
            refused[why or "?"] = refused.get(why or "?", 0) + 1
    kept, gridded, grid_notes = collapse_booking_grids(
        kept, int(source.get("grid_min_per_day", GRID_MIN_PER_DAY)))
    if gridded:
        notes.append(f"booking grids: {gridded} slot row(s) collapsed into "
                     f"{len(grid_notes)} day row(s)")
        notes.extend("  " + n for n in grid_notes[:8])
        if len(grid_notes) > 8:
            notes.append(f"  ...and {len(grid_notes) - 8} more")
    return kept, {"notes": notes, "refused": refused, "records": len(records),
                  "gridded": gridded}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Import UK physical-activity sessions from OpenActive feeds.")
    ap.add_argument("--config", default="openactive_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one publisher by name (substring)")
    ap.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS,
                    help="drop sessions starting further ahead than this. The guard "
                         "exists because Let's Ride publishes rides dated 2500 and "
                         "nothing downstream can ever delete a pin that is not past.")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES,
                    help="per feed. Hitting it is reported loudly, never silently.")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="between RPDE pages. These are small charities' servers.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    sources = [s for s in cfg.get("sources", [])
               if not s.get("skip")
               and (not a.only or a.only.lower() in str(s.get("name", "")).lower())]
    if not sources:
        print("[openactive] no sources selected", flush=True)
        return 0

    store = None if a.dry_run else EventStore(a.store)
    total = 0
    for source in sources:
        label = source.get("name", "?")
        try:
            events, report = read_source(source, a.horizon_days, a.max_pages, a.delay)
        except Exception as exc:                      # noqa: BLE001
            # One publisher must never cost the sweep. Same rule as ingest_site
            # in the Tribe adapter, learned when one malformed venue record cost
            # Bicycle Colorado all 105 of its events.
            print(f"[openactive] {label}: FAILED — {type(exc).__name__}: {exc}", flush=True)
            continue
        for note in report["notes"]:
            print(f"[openactive] {label}: {note}", flush=True)
        if report["refused"]:
            detail = ", ".join(f"{n} {why}" for why, n in
                               sorted(report["refused"].items(), key=lambda kv: -kv[1]))
            print(f"[openactive] {label}: dropped {sum(report['refused'].values())} "
                  f"of {report['records']} ({detail})", flush=True)
        print(f"[openactive] {label}: kept {len(events)} upcoming session(s)", flush=True)
        total += len(events)
        if store is not None:
            for event in events:
                store.upsert(event)

    if store is not None:
        store.save()
        print(f"[openactive] wrote {total} session(s) to {a.store}", flush=True)
    else:
        print(f"[openactive] dry run — {total} session(s) would be written", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
