#!/usr/bin/env python3
"""
mapsee_ingest_osm_food.py — takeaway places that can actually take an order.

WHY THIS IS DIFFERENT FROM EVERY OTHER ADAPTER HERE, and read this before
extending it. The other 32 adapters ingest EVENTS: things a venue published a
listing for. This one ingests BUSINESSES, from OpenStreetMap, which nobody
published as a listing at all.

That distinction is the whole reason venue outreach can honestly open with "they
came from your public listings; nobody at your end put them there". A restaurant
existing is not a listing. So this adapter is deliberately narrow, and the
narrowness IS the design:

  * ONLY places with a resolvable ORDER link. A restaurant we cannot send an
    order to is a pin with nothing behind it — it makes the map bigger and no
    more useful, and it puts a business on our map for our benefit rather than
    theirs. No order link, no import.
  * ONLY places whose opening hours parse cleanly. We do not invent hours. A
    "hungry right now" map is worse than useless if the place is shut, and OSM's
    opening_hours grammar is deep enough that a confident half-parse is the most
    likely way to get that wrong.
  * NO menu items are created. We are not taking the order — the button goes to
    the shop's own ordering page. `has_storefront` (../mapsee 0148) reads
    event_menu_items, so these correctly read as NOT having a storefront, which
    means a claimed restaurant still gets offered "Sell food for pickup here" at
    0%. That is the onboarding hook, and it only works because we did not
    pretend to be their till.

ATTRIBUTION. OSM data is ODbL. Every record carries the source in its
description and its source ref, and the product's map already credits
OpenStreetMap on every tile.

Env:  none (Overpass is public; be polite with --delay)
Run:  python mapsee_ingest_osm_food.py --config osm_food_sources.json \
          --store feeds_events.json [--dry-run] [--only seattle]
"""
import argparse
import hashlib
import html as html_lib
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
from mapsee_menu_links import (
    looks_like_ordering, order_link_on,
    looks_like_booking, booking_link_on,
    destination_verdict, refine_storefront,
    NOT_A_VENUE_SITE, fetch as fetch_page,
)

UA = "mapsee-aggregator/1.0 (+https://mapsee.me; OSM takeaway discovery)"
OVERPASS = "https://overpass-api.de/api/interpreter"

# amenity values worth asking about. cafe and bar are in because a great many
# of them do takeaway coffee and food; fast_food obviously; restaurant obviously.
AMENITIES = ("restaurant", "fast_food", "cafe", "bakery")

DOW = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
_DAYSPEC = re.compile(r"^(mo|tu|we|th|fr|sa|su)(?:\s*-\s*(mo|tu|we|th|fr|sa|su))?$", re.I)
_TIMESPAN = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
# A whole rule: a day spec, then one or more comma-separated spans. The span
# group is anchored to the end and the day group is non-greedy, which is what
# lets "mo,we,fr 09:00-17:00" (a day list) and "mo-fr 11:00-14:00,17:00-22:00"
# (two windows) both split in the right place.
# 0156 already used 23:59 as "the end of today" for 24/7, so a spill closes
# the first day the same way rather than inventing a second marker.
END_OF_DAY = "23:59"
_SPAN_RX = r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}"
_RULE = re.compile(rf"^(.*?)\s+({_SPAN_RX}(?:\s*,\s*{_SPAN_RX})*)$")


def parse_opening_hours(spec: str):
    """OSM opening_hours -> {weekday: [(open, close), …]} or None if unreadable.

    A day maps to a LIST OF WINDOWS, because a great many places shut in the
    middle of it — lunch service and dinner service, or a Spanish siesta.

    THIS USED TO REFUSE SPLIT SERVICE, and the refusal was right for as long as
    storage could not express it: one span per day meant collapsing
    "11:00-14:00,17:00-22:00" into 11:00-22:00, which advertises a restaurant
    through the three hours its kitchen is shut. ../mapsee 0188 makes a day a
    list of windows, so the honest answer is now representable and the listing
    no longer has to be thrown away.

    What that refusal cost, measured 2026-08-20 on the second-hand sweep: Madrid
    yielded 9 usable rows from 126 shops and Barcelona 8 from 125, against
    Brussels 315 from 794. Spain publishes the siesta; we were discarding the
    country for saying so.

    Still supported and unchanged — "Mo-Fr 11:00-22:00", "Mo-Su 10:00-20:00; Su
    off", "24/7", day lists, several rules separated by ';'.

    Still refused, because these remain ways to be confidently wrong:
      * PH / SH / seasonal / week-number / month rules
      * "sunrise", "sunset", open-ended "11:00+"
      * a span crossing midnight, either written plainly ("22:00-02:00") or as
        an hour >= 24 ("11:00-27:00"). One window is one day; two days is two
        windows and OSM is not saying which
      * windows within a day that OVERLAP. Sorted and merged when they merely
        touch, refused when they genuinely overlap — that is a malformed rule
        and guessing at the intent is the thing this function does not do
    A place we cannot read stays off the map, which is the right way round: the
    cost of skipping it is one missing pin, and the cost of guessing is telling
    somebody hungry to walk to a locked door.
    """
    if not spec:
        return None
    s = spec.strip().lower()
    if s in ("24/7", "24x7"):
        return {d: [("00:00", "23:59")] for d in range(7)}
    if re.search(r"\b(ph|sh|easter|sunrise|sunset|week\s|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", s):
        return None
    out, spilled = {}, {}
    for rule in s.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        if rule.endswith("off"):
            # _days_in returns None for anything it cannot read, and iterating
            # that crashed the whole run on the first real Overpass response.
            # An unreadable closure is the WORST thing to shrug at: it is the
            # rule that says "shut", so ignoring it would leave the place
            # advertised as open. Refuse the listing outright.
            ds = _days_in(rule[:-3].strip())
            if ds is None:
                return None
            for d in ds:
                out.pop(d, None)
            continue
        # ONE RULE IS A DAY SPEC AND ONE OR MORE TIME SPANS, and the only thing
        # separating them is which side of the space the comma falls on:
        #
        #   "mo,we,fr 09:00-17:00"            -> a DAY list, one window
        #   "mo-fr 11:00-14:00,17:00-22:00"   -> one day range, TWO windows
        #
        # The old expression allowed exactly one span, so the second line did
        # not match and the listing was refused. `.*?` is non-greedy and the
        # span group is anchored to the end, so the split lands in the right
        # place for both.
        m = _RULE.match(rule)
        if not m:
            return None
        days, span_text = m.group(1), m.group(2)
        windows, spills = [], []
        for span in span_text.split(","):
            got = _one_span(span)
            if got is None:
                return None
            o, c, spill = got
            windows.append((o, c))
            if spill:
                spills.append(spill)
        windows = _tidy_windows(windows)
        if windows is None:
            return None
        ds = _days_in(days)
        if ds is None:
            return None
        for d in ds:
            # REPLACE, not extend — a later rule for the same day overrides an
            # earlier one, which is OSM's own precedence and is what makes
            # "Mo-Su 10:00-20:00; Su off" mean what it says. Split service is
            # written with a comma inside ONE rule, which is handled above.
            out[d] = windows
            for spill in spills:
                spilled.setdefault((d + 1) % 7, []).append(("00:00", spill))

    # SPILLS LAND LAST, and that ordering is the whole reason they are collected
    # rather than written as we go.
    #
    # Days are REPLACED by later rules, so a spill written during rule 1 would be
    # wiped by any rule 2 that mentions the same day — and the day a late-night
    # session spills onto is almost always a day with its own rule. Applying them
    # after every rule has been read is the only order that survives that.
    #
    # It also puts them on the right side of `off`. "Mo-Sa 18:00-26:00; Su off"
    # means no Sunday SERVICE, and the Saturday night session still runs until
    # 02:00 on Sunday morning. Sunday is closed and has a window, which sounds
    # contradictory and is exactly what the sign on the door says.
    for d, extra in spilled.items():
        merged = _union_windows(out.get(d, []) + extra)
        if merged is None:
            return None
        out[d] = merged
    return out or None


# A single "HH:MM-HH:MM", validated. Split out of the rule loop when a rule
# gained the ability to carry several, so every window is checked the same way.
def _one_span(span: str):
    """"HH:MM-HH:MM" -> (open, close, spill) or None.

    `spill` is the part of the window that lands on the NEXT day, or None. A
    kitchen open 11:00-27:00 is open until 03:00 tomorrow, and OSM writes that
    two ways — an hour >= 24, or a close that sorts before the open ("22:00-
    02:00"). Both mean the same thing and both spill.

    THIS USED TO REFUSE BOTH, and the refusal is why ../mapsee is holding 251
    rows whose recurring_hours contain "24:00", "25:00", "26:00" or "27:00":
    they were written before the refusal existed and nothing has overwritten
    them since, because the parser now declines those venues outright. Postgres
    rejects '2026-08-21 27:00'::timestamp, so under 0156's unguarded roller one
    of those rows aborted the entire hourly roll — for every venue — and only
    0188's per-row handler made that survivable.

    The refusal was correct while a day held ONE window: 11:00-27:00 is two
    days and one span could not say so. A day is a list now, so it can:
    11:00-23:59 today and 00:00-03:00 tomorrow. Nothing about the storage
    changes; the second half is simply written onto the day it belongs to.

    Still refused: an OPENING past midnight (meaningless), a close more than a
    full day out, and a zero-length window.
    """
    tm = _TIMESPAN.match(span.strip().replace(" ", ""))
    if not tm:
        return None
    oh, ch = int(tm.group(1)), int(tm.group(3))
    om, cm = tm.group(2), tm.group(4)
    if oh > 23:
        return None                  # an opening time past midnight is nonsense
    o = f"{oh:02d}:{om}"

    if ch > 23:
        # 24:00 is midnight EXACTLY — the end of today, not a minute of
        # tomorrow. Spilling it would write a zero-length 00:00-00:00 window.
        if ch == 24 and cm == "00":
            return (o, END_OF_DAY, None)
        if ch > 47:
            return None              # more than a day out; we are not guessing
        return (o, END_OF_DAY, f"{ch - 24:02d}:{cm}")

    c = f"{ch:02d}:{cm}"
    if c == o:
        return None                  # zero length
    if c < o:
        # "22:00-02:00". Same meaning as 22:00-26:00, written the other way.
        if c == "00:00":
            return (o, END_OF_DAY, None)      # open until midnight, no spill
        return (o, END_OF_DAY, c)
    return (o, c, None)


def _tidy_windows(windows):
    """Sort a day's windows, merge ones that touch, refuse ones that overlap.

    ORDER MATTERS DOWNSTREAM. ../mapsee 0188's roller takes the FIRST window of
    the day that has not finished yet, so out-of-order windows would advertise
    the evening sitting while the lunch one was still running.

    A genuine overlap ("10:00-14:00,12:00-18:00") is a malformed rule, and
    working out which of the two the mapper meant is exactly the guessing this
    function exists not to do. Touching windows ("10:00-14:00,14:00-20:00") are
    not malformed, they are just one window written as two, so they merge —
    otherwise the row would claim a zero-length closure.
    """
    if not windows:
        return None
    windows = sorted(windows)
    out = [windows[0]]
    for o, c in windows[1:]:
        po, pc = out[-1]
        if o < pc:
            return None                  # genuine overlap: refuse the listing
        if o == pc:
            out[-1] = (po, c)            # touching: one window written as two
        else:
            out.append((o, c))
    return out


def _union_windows(windows):
    """Sort and MERGE, including genuine overlaps. For spills only.

    _tidy_windows refuses an overlap because two authored spans that overlap are
    a malformed rule and picking one would be a guess. A spill is not authored —
    it is derived from the previous day's closing time — so an overlap there is
    just the same late session described twice ("Fr 18:00-26:00; Sa 00:00-03:00"
    on a bar that says both). Refusing would throw away a listing over an
    agreement, so these union instead.
    """
    if not windows:
        return None
    windows = sorted(windows)
    out = [windows[0]]
    for o, c in windows[1:]:
        po, pc = out[-1]
        if o <= pc:
            out[-1] = (po, max(pc, c))
        else:
            out.append((o, c))
    return out


def _days_in(spec: str):
    """'Mo-Fr' / 'Mo,We,Fr' / 'Mo' -> [0,1,2,3,4]. None if unreadable."""
    spec = spec.strip()
    if not spec:
        return None
    days = []
    for part in spec.split(","):
        m = _DAYSPEC.match(part.strip())
        if not m:
            return None
        a = DOW[m.group(1).lower()]
        b = DOW[m.group(2).lower()] if m.group(2) else a
        d = a
        while True:
            days.append(d)
            if d == b:
                break
            d = (d + 1) % 7
    return days


CURSOR_PATH = "osm_food_cursor.json"


def load_cursor(path=CURSOR_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_cursor(cur, path=CURSOR_PATH):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, indent=1, sort_keys=True)
    except Exception as exc:
        print(f"[osm-food] could not write cursor: {exc}", flush=True)


def window_at(cands, start, max_places):
    """`max_places` candidates from `start`, wrapping — and NEVER more than exist.

    THE WRAP MUST NOT DUPLICATE. The obvious version — slice, then top up from
    the front with however many are missing — examines every candidate TWICE
    whenever there are fewer of them than --max-places:

        cands=52, max_places=60 -> cands[0:60] is 52, then + cands[:8] = 60

    which is 60 places examined at 52 shops, the first eight of them done twice.
    Nothing downstream says so: EventStore keys on the fingerprint, so the
    duplicates collapse and the only visible symptom is a summary line that
    contradicts its own detail ("52 with hours ... 60 slots").

    Latent in this adapter rather than active — --max-places defaults to 60 and
    Seattle holds 3,134 candidates — but any small --radius-miles run reaches
    it, and it was ACTIVE in mapsee_ingest_osm_secondhand.py, whose default is
    400. Caught there on its first live run: Edinburgh at a 4-mile radius
    reported 104 rows over 52 shops.

    A modular walk cannot express the bug: `take` is capped at n, so an index
    can only be visited once per call.
    """
    n = len(cands)
    if not n:
        return []
    start %= n
    take = min(max_places, n)
    return [cands[(start + i) % n] for i in range(take)]


def area_bbox(area):
    """(s, w, n, e) for an area, from an explicit bbox or a centre + radius.

    centre+radius is the form worth writing new hubs in: "Seattle, 50 miles" is
    a thing somebody can check, and a bbox is four numbers nobody can.
    """
    if area.get("bbox"):
        return tuple(area["bbox"])
    lat, lon = area["center"]
    mi = float(area.get("radius_miles", 50))
    dlat = (mi * 1.609) / 111.0
    dlon = (mi * 1.609) / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def tiles(bbox, max_deg=0.35):
    """Split a bbox into a grid no cell of which is wider than max_deg.

    A 50-mile radius is a 1.45° box, and Overpass will not serve that in one
    piece for four amenity types: the COUNT alone took 76 seconds, and the same
    box asking for tags is far heavier. The service already tells us this — a
    wide box answers 504, which is how Seattle and later New York dropped out of
    whole runs. Tiling turns one request it will refuse into a handful it will
    answer, and each one is small enough to retry cheaply.
    """
    s, w, n, e = bbox
    rows = max(1, math.ceil((n - s) / max_deg))
    cols = max(1, math.ceil((e - w) / max_deg))
    dh, dw = (n - s) / rows, (e - w) / cols
    return [(s + r * dh, w + c * dw, s + (r + 1) * dh, w + (c + 1) * dw)
            for r in range(rows) for c in range(cols)]


def _overpass_one(bbox, delay=2.0, tries=4):
    """One tile. Backs off rather than re-asking harder.

    Overpass is a free, shared, volunteer-run service and it says so: measured
    while building this, a third city in quick succession answered 429, and a
    wide box answers 504. Both are it asking us to slow down.
    """
    s, w, n, e = bbox
    parts = "".join(
        f'nwr["amenity"="{a}"]["name"]({s},{w},{n},{e});' for a in AMENITIES)
    q = f"[out:json][timeout:180];({parts});out tags center;"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            time.sleep(delay)
            return data.get("elements", [])
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(delay * (3 ** attempt))
    raise last


def sweep_tiles(cells, fetch_one, label, delay=2.0, sleep=time.sleep):
    """(elements, complete) — every tile, then a SECOND PASS over the ones that lost.

    WHY A SECOND PASS IS NOT THE SAME AS MORE RETRIES. _overpass_one already
    tries a tile four times, backing off 2s, 6s, 18s — and that is the right
    shape for a tile that is momentarily rate-limited. It is the wrong shape for
    Overpass being busy, because eighteen seconds later it is still busy. The
    second pass happens AFTER the rest of the area, which is minutes rather than
    seconds, and minutes is the timescale on which load actually moves.

    Worth doing because the losses are not rare and they are not small. Measured
    across two production reparses on 2026-08-20: seven of thirty-two hubs lost
    a tile in the second-hand sweep (run 32385196784) and six of eight in the
    food sweep (run 32406174018) — New York lost NINE of thirty, which is 30% of
    the metro and made its numbers unusable. Nothing was corrupted, because an
    incomplete area is never cached and an upsert never deletes; it simply meant
    every sweep quietly under-covered a few cities.

    `sleep` is injectable so the retry can be tested without waiting, which is
    the other half of why this is a function rather than a loop inside
    overpass(): a fetcher passed in can be made to fail on demand, and the
    inline version could only be exercised against the live service.
    """
    out, seen = [], set()

    def pass_over(cell_list, tag):
        lost = []
        for i, cell in enumerate(cell_list, 1):
            try:
                els = fetch_one(cell)
            except Exception as exc:
                lost.append(cell)
                print(f"{label} tile {i}/{len(cell_list)}{tag} failed: {exc}", flush=True)
                continue
            for el in els:
                k = (el.get("type"), el.get("id"))
                if k not in seen:
                    seen.add(k)
                    out.append(el)
            if len(cell_list) > 1:
                print(f"{label} tile {i}/{len(cell_list)}{tag}: "
                      f"+{len(els)} ({len(out)} unique)", flush=True)
        return lost

    lost = pass_over(cells, "")
    if lost:
        print(f"{label} {len(lost)} of {len(cells)} tiles did not answer — "
              f"second pass in a moment", flush=True)
        sleep(delay * 5)
        lost = pass_over(lost, " [retry]")
    if lost:
        # STILL the old rule, and it is the important one: a partial answer is
        # fine to USE and never fine to KEEP. Caching one for thirty days hides
        # the gap behind a run that reports itself healthy.
        print(f"{label} {len(lost)} of {len(cells)} tiles did not answer TWICE — "
              f"this area is INCOMPLETE this run and will NOT be cached", flush=True)
    return out, not lost


def overpass(area, delay=2.0, tries=4):
    """(elements, complete) — every named food place in an area, tile by tile.

    A tile that will not answer after its retries is REPORTED and skipped rather
    than failing the area: losing one square of a metro is a smaller loss than
    losing the metro, and a silent partial would be worse than either.

    `complete` exists because of what the first live 50-mile run did: 2 of
    Seattle's 35 tiles timed out, the run correctly SAID so — and then cached
    the partial list for thirty days, which would have quietly under-covered two
    squares of the metro until September while every subsequent run reported
    itself perfectly healthy. A partial answer is fine to USE and not fine to
    KEEP.
    """
    cells = tiles(area_bbox(area))
    return sweep_tiles(cells, lambda c: _overpass_one(c, delay=delay, tries=tries),
                       f"[osm-food]   {area['name']}", delay=delay)


# ---------------------------------------------------------------------------
# The places cache
# ---------------------------------------------------------------------------
# A restaurant's EXISTENCE does not change weekly, and until this cache the job
# re-downloaded every place in every area on every run to examine sixty of them:
# 18,282 places pulled from a volunteer-run service to look at 480. That was
# tolerable at a 2-mile radius and is not at 50, where Seattle alone holds 9,765.
#
# So the expensive half is now monthly and the cheap half stays weekly. The
# weekly run walks the cursor through a cached list; Overpass is only asked when
# the cache is older than --cache-days or missing.
def cache_path(cache_dir, area_name):
    safe = re.sub(r"[^a-z0-9]+", "-", area_name.lower()).strip("-")
    return os.path.join(cache_dir, f"{safe}.json")


def load_places(cache_dir, area, max_age_days, bbox=None):
    """The cached list for this area, but ONLY if it covers the same ground.

    The cache is keyed on the area NAME, and the name is not the query: "Seattle"
    at 20 miles and "Seattle" at 50 miles are different sets of restaurants. A
    cache that ignored the box would let a narrow run poison a wide one for
    thirty days — silently, since a smaller list looks exactly like a quiet week.
    So the box is stored and compared, and a mismatch is a miss.
    """
    p = cache_path(cache_dir, area["name"])
    want = tuple(round(v, 6) for v in (bbox if bbox is not None else area_bbox(area)))
    try:
        with open(p, encoding="utf-8") as fh:
            blob = json.load(fh)
        age = (time.time() - float(blob.get("fetched_at", 0))) / 86400.0
        got = blob.get("bbox")
        got = tuple(round(v, 6) for v in got) if got else None
        if got != want:
            print(f"[osm-food] {area['name']}: cached list covers a different box "
                  f"— refetching", flush=True)
            return None, False
        if age <= max_age_days and blob.get("elements"):
            print(f"[osm-food] {area['name']}: {len(blob['elements'])} places from cache "
                  f"({age:.1f}d old)", flush=True)
            return blob["elements"], True
    except Exception:
        pass
    return None, False


def save_places(cache_dir, area, elements, bbox=None):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        bb = list(bbox if bbox is not None else area_bbox(area))
        with open(cache_path(cache_dir, area["name"]), "w", encoding="utf-8") as fh:
            json.dump({"area": area["name"], "fetched_at": time.time(),
                       "bbox": bb, "elements": elements}, fh)
    except Exception as exc:
        print(f"[osm-food] could not cache {area['name']}: {exc}", flush=True)


def clean_public_phone(value):
    """One callable public business number, preserving its published display."""
    for raw in str(value or "").split(";"):
        phone = re.sub(r"\s+", " ", raw).strip()
        digits = re.sub(r"\D", "", phone)
        if 7 <= len(digits) <= 15 and len(phone) <= 40:
            return phone
    return None


def phone_from_official_html(page):
    """A public phone explicitly published by the venue's official website."""
    if not page:
        return None
    for raw in re.findall(
            r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>",
            page, re.I):
        try:
            blob = json.loads(html_lib.unescape(raw.strip()))
        except Exception:
            continue
        stack = list(blob if isinstance(blob, list) else [blob])
        while stack:
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            raw_type = node.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type or ""]
            typ = " ".join(str(x) for x in types).lower()
            if re.search(r"localbusiness|organization|place|restaurant|cafe|bakery|store", typ):
                phone = clean_public_phone(node.get("telephone"))
                if phone:
                    return phone
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    for value in re.findall(r"href=[\"']tel:([^\"']+)", page, re.I):
        phone = clean_public_phone(urllib.parse.unquote(html_lib.unescape(value)))
        if phone:
            return phone
    return None


def business_detail_lines(tags, website_phone=None):
    """Stable marker lines consumed by Mapsee's business details card."""
    lines = []
    phone = clean_public_phone(tags.get("phone") or tags.get("contact:phone")) or clean_public_phone(website_phone)
    if phone:
        lines.append(f"☎ Phone: {phone}")
    cuisine = []
    for value in re.split(r"[;,]", str(tags.get("cuisine") or "")):
        value = re.sub(r"[_\s]+", " ", value).strip()
        if value and value.lower() not in {x.lower() for x in cuisine}:
            cuisine.append(value)
    if cuisine:
        lines.append("🍴 Cuisine: " + " · ".join(cuisine[:5]))
    wheelchair = str(tags.get("wheelchair") or "").lower()
    accessibility = {"yes": "Wheelchair accessible", "limited": "Limited wheelchair access",
                     "no": "Not wheelchair accessible"}.get(wheelchair)
    if accessibility:
        lines.append(f"♿ Accessibility: {accessibility}")
    services = []
    for key, label in [("takeaway", "Takeaway"), ("delivery", "Delivery"),
                       ("outdoor_seating", "Outdoor seating"), ("internet_access", "Wi-Fi"),
                       ("diet:vegetarian", "Vegetarian options"), ("diet:vegan", "Vegan options")]:
        value = str(tags.get(key) or "").lower()
        if ((key == "internet_access" and value in {"wlan", "yes"}) or
                (key != "internet_access" and value in {"yes", "only"})):
            services.append(label)
    if services:
        lines.append("✓ Services: " + " · ".join(services))
    return lines


def links_for(tags, session_delay=0.5):
    """(order_url, booking_url, site, website_phone) from one page fetch.

    All three come out of the same request on purpose. The page fetch is the
    expensive and impolite half of this job — we are a guest on somebody's
    server — so asking twice to answer two questions about the same restaurant
    would double the cost of the thing we should be minimising.

    Never a guess, for either link. See looks_like_ordering / looks_like_booking.

    THE SITE is returned as well, and it is the reason the whole claim story
    works: it is the venue's OWN domain, the only evidence an independent
    restaurant has that the place is theirs. It was being fetched and thrown
    away — measured 2026-08-12, 86% of imported restaurants had no way to claim
    themselves while we held their website in a local variable. ../mapsee 0153
    is the other half.
    """
    order = None
    booking = None
    website_phone = None

    # OSM sometimes carries either outright. Cheapest possible answer, no fetch.
    for k in ("website:menu", "contact:menu", "menu:url", "order:url"):
        v = tags.get(k)
        if v and looks_like_ordering(v):
            order = v
            break
    # Measured across 1,086 Seattle places: `website:reservation` appears ONCE
    # and `reservation` 13 times as a yes/no/required FLAG carrying no link. So
    # tags are close to useless for booking and the page scan below is where the
    # yield is — but reading them costs nothing and they are the most reliable
    # signal when present, because a mapper put them there deliberately.
    for k in ("website:reservation", "reservation:website", "booking:website",
              "contact:booking", "reservation:url"):
        v = tags.get(k)
        if v and looks_like_booking(v):
            booking = v
            break

    site = tags.get("website") or tags.get("contact:website")
    if site and not site.startswith("http"):
        site = "https://" + site
    if not site:
        return order, booking, None, None

    # A Facebook page is not a domain a restaurant can prove it owns, and
    # emitting it as "Website:" would put facebook.com in front of the claim
    # machinery for no benefit (spread would reject it anyway — but a line that
    # can only ever be rejected is a line not worth writing).
    try:
        host = urllib.parse.urlparse(site).hostname or ""
    except Exception:
        host = ""
    own_site = site if host and not NOT_A_VENUE_SITE.search(host) else None

    if not order and looks_like_ordering(site):
        order = site
    if not booking and looks_like_booking(site):
        booking = site
    # Read the official page once for links AND its explicitly published phone.
    html, final = fetch_page(site)
    time.sleep(session_delay)
    if own_site:
        website_phone = phone_from_official_html(html)
    if not order:
        order = order_link_on(final, html)
    if not booking:
        booking = booking_link_on(final, html)
    # VERIFY THE DESTINATION, once, before promising anything about it. Both
    # failures reported on 2026-08-12 were 200s: a ChowNow location that serves
    # an empty React shell, and a Square store whose landing block is gift cards
    # with the menus one level in. Neither a status code nor the URL itself can
    # see either, so the only honest check is to look at the page.
    # Only a destination we genuinely fetched and found EMPTY is dropped.
    # destination_verdict keeps "unknown" separate from "dead" for a reason worth
    # restating here: the big ordering hosts block scrapers, so treating an
    # unfetchable page as dead would delete most of the map's order links.
    if order and destination_verdict(order) == "dead":
        order = None
    elif order:
        # Refinement needs the page, so it only happens when we could read it —
        # which is exactly when refine_storefront has anything to work with.
        ohtml, ofinal = fetch_page(order)
        time.sleep(session_delay)
        if ohtml:
            refined = refine_storefront(ofinal or order, ohtml)
            # NEVER LET A DERIVED URL REPLACE A VALIDATED ONE UNLESS IT IS ALSO
            # VALID. `ofinal` is the URL after redirects, and a redirect can land
            # somewhere that is not an ordering page at all.
            #
            # Live example: Chipotle's own location page links
            # chipotle.com/order#menu — a good link, correctly matched — and
            # fetching it redirects to chipotle.com/, the bare homepage. Handing
            # that to refine_storefront made it the stored link, and the client
            # then rightly refused to call the homepage "Order pickup" and fell
            # through to a generic "Tickets & info" button on a burrito shop.
            # The client's re-validation caught my bad write, which is what it is
            # for; the write should not have happened.
            if refined and looks_like_ordering(refined):
                order = refined
    if booking and destination_verdict(booking) == "dead":
        booking = None
    return order, booking, own_site, website_phone


def to_events(el, area, order_url, hours, days_ahead, booking_url=None, site=None, website_phone=None):
    """One NormalizedEvent per open-day in the window ahead.

    Each is a real, bounded slot rather than a permanent pin, because that is
    what the product's map and its "on now / next up" reading expect — and
    because a takeaway place that has closed for the night should stop being
    offered to somebody hungry at 1am.
    """
    tags = el.get("tags", {})
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    if lat is None or lon is None:
        return []
    name = (tags.get("name") or "").strip()
    if not name:
        return []
    addr = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x) or None
    kind = tags.get("amenity", "restaurant").replace("_", " ")
    # NO LEAD SENTENCE. This used to open with a claim about what the listing can
    # do and who is not taking the money — "Order for pickup on their own site;
    # mapsee.me is not taking the order." — which is a paragraph explaining a
    # button that is sitting right there saying Order, and which goes to a
    # domain that is plainly not ours. Nobody needs to be told that a link is a
    # link. It also had to be kept in agreement with the links (three variants,
    # and mapsee_prune_links rewriting it when one died), so it was a sentence
    # that could go wrong and never had anything to say.
    lines = []
    if order_url:
        lines.append(f"🛒 Order: {order_url}")
    # The product turns this into a "Reserve a table" button, exactly as it does
    # the Order line — and re-validates the URL before rendering either.
    if booking_url:
        lines.append(f"🍽️ Reserve: {booking_url}")
    # Not decoration: this is the line ../mapsee 0153's event_link_domain reads,
    # and it is the ONLY thing that lets the owner of an imported restaurant
    # claim it. Written last so it never out-ranks a transactional link visually,
    # and only when it is the venue's own domain (links_for filters socials).
    if site:
        lines.append(f"🌐 Website: {site}")
    lines.extend(business_detail_lines(tags, website_phone))
    body = ("\n".join(lines) + "\n\n") if lines else ""
    # THE HUB'S NAME IS NOT THE VENUE'S TOWN — here too. `city=` below stopped
    # inventing one, and this sentence went on doing it: the description is
    # built from area["name"], so every place in the 50-mile Seattle hub said
    # "in Seattle" no matter where it was. Reported live 2026-08-17 — Sisters
    # Restaurant, 2804 Grand Avenue, EVERETT, described as a "restaurant in
    # Seattle" and pinned twenty-seven miles south of itself. The pin came from
    # the invented city being geocoded; the sentence came from here, and fixing
    # only the field would have left the prose asserting the wrong town.
    #
    # OSM's own city when it has one, and NO phrase when it does not. This
    # restaurant carries no addr:city at all, so it now reads "Sisters
    # Restaurant — restaurant." A missing town is a gap; a confident wrong one
    # is what somebody drives to.
    town = (tags.get("addr:city") or tags.get("addr:suburb") or "").strip()
    desc = (f"{name} — {kind}{f' in {town}' if town else ''}.\n\n"
            f"{body}"
            f"Public business details from OpenStreetMap contributors (ODbL). "
            f"Hours and details can change; the business can claim this listing to correct them.")
    # ONE ROW PER VENUE, not one per open day.
    #
    # A business is open every Tuesday; it is not holding 52 Tuesday events.
    # Writing a row per day cost 6.1 rows per venue (measured across Seattle,
    # Chicago, Portland and London: 1,547 rows for 254 places) and would be
    # ~30,500 rows at full coverage of eight hubs alone.
    #
    # The row carries the WEEKLY PATTERN, and ../mapsee 0156's hourly roller
    # moves its window forward. Its starts_at/ends_at is only ever the NEXT open
    # window — so the map answers "can I eat there now, or when next", which is
    # the question, and the row never expires the way a dated clone does.
    #
    # It also gives the venue a STABLE id. Under the per-day model every day was
    # a different event, so a claim, a share link — or an order, when we are
    # their till — attached to a row that stopped existing tomorrow.
    today = datetime.now(timezone.utc).date()
    slot = None
    for i in range(max(days_ahead, 8)):     # 8 guarantees every weekday is seen
        d = today + timedelta(days=i)
        # THE FIRST WINDOW OF THE FIRST OPEN DAY. A day is a list now, and this
        # deliberately does not try to pick the window that is CURRENT: the
        # adapter holds naive local strings with no timezone (the sync attaches
        # one), so "has this window already ended?" is a question it cannot
        # answer correctly. ../mapsee 0188's roller runs hourly, knows the tz,
        # and moves the row onto the right window — which is 0156's design and
        # the reason starts_at/ends_at is only ever "the next window".
        spans = hours.get(d.weekday())
        if spans:
            slot = (d, spans[0][0], spans[0][1])
            break
    if not slot:
        return []
    d, o, c = slot

    # The fingerprint is the OSM identity, with NO date in it — that is what
    # makes re-running update the same row instead of adding another. It also
    # sidesteps make_fingerprint's name|date|place basis, which would collapse
    # two Chipotles in one city into a single row now that the date is gone.
    osm_ref = f"{el.get('type','n')}/{el.get('id')}"
    return [NormalizedEvent(
        source="osm-food",
        source_id=osm_ref,
        fingerprint=hashlib.sha1(f"osm-food|{osm_ref}".encode("utf-8")).hexdigest(),
        name=name,
        description=desc,
        start_local=f"{d.isoformat()}T{o}:00",
        end_local=f"{d.isoformat()}T{c}:00",
        venue_name=name,
        latitude=float(lat), longitude=float(lon),
        # THE CITY IS THE VENUE'S, NEVER THE HUB'S.
        #
        # This used to be area["city"] for every place in the box, which was
        # roughly true when a box was 2 miles across and is badly false at 20 or
        # 50: the Seattle hub now covers Renton, Bellevue, Kent and Puyallup, and
        # every one of them was being labelled "Seattle". Reported live —
        # a Renton Chipotle and Chick-fil-A on Rainier Ave S shown as Seattle
        # 98057, a Puyallup bistro as Seattle 98372, a SEQUIM diner (sixty miles
        # away, across the water) as Seattle 98382. The POSTCODES were right the
        # whole time, because those come from OSM; only the city was invented.
        #
        # Worse than a wrong label: _addr_parts feeds (street, city, region) to
        # the geocoder, so "439 Rainier Avenue South, Seattle, WA" matched
        # SEATTLE's Rainier Ave S and the pin moved eleven miles. See
        # coords_exact below.
        #
        # OSM's own addr:city when it has one; otherwise nothing. A missing city
        # is a gap; a confidently wrong one moves the restaurant.
        address=addr,
        city=(tags.get("addr:city") or tags.get("addr:suburb") or "").strip() or None,
        region=area.get("region"),
        country=area.get("country"), postal_code=tags.get("addr:postcode"),
        category="food",
        ticket_url=order_url or booking_url,
        # OSM's point is surveyed; the address text is derived from it, not
        # the other way round. Never geocode over it — see NormalizedEvent.
        coords_exact=True,
        # 0=Monday…6=Sunday, exactly as parse_opening_hours produced it.
        # A LIST OF WINDOWS PER DAY: {"0": [["11:00","14:00"],["17:00","22:00"]]}.
        # ../mapsee 0188 reads both this and 0156's flat ["11:00","22:00"], so
        # rows written before this change keep rolling until they are rewritten.
        recurring_days={str(k): [[o, c] for o, c in v] for k, v in sorted(hours.items())},
    )]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import OSM takeaway places that have a real order link.")
    ap.add_argument("--config", default="osm_food_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one area by name (substring)")
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--max-places", type=int, default=60, help="per area, per run")
    ap.add_argument("--ignore-cursor", action="store_true",
                    help="start at the first candidate and do not advance the cursor "
                         "(backfill: re-examine places already imported)")
    ap.add_argument("--places-cache", default="osm_places_cache",
                    help="where the per-area OSM place lists live")
    ap.add_argument("--cache-days", type=float, default=30.0,
                    help="re-ask Overpass only when the cached list is older than this")
    ap.add_argument("--radius-miles", type=float,
                    help="override every area's radius for THIS run (config stays as it is). "
                         "The cache keys on the resulting box, so a narrow run cannot "
                         "poison a wide one.")
    ap.add_argument("--warm-cache", action="store_true",
                    help="fetch and cache each area's place list, then stop. Examines no "
                         "places and fetches no venue websites.")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    areas = [x for x in cfg.get("areas", [])
             if not a.only or a.only.lower() in str(x.get("name", "")).lower()]
    # Warming touches neither the store nor the cursor: it is a fetch, not a run.
    # Creating an EventStore would rewrite feeds_events.json with nothing in it,
    # and moving the cursor would skip places nobody examined.
    store = None if (a.dry_run or a.warm_cache) else EventStore(a.store)
    cursor = load_cursor()
    tot_seen = tot_hours = tot_order = tot_booking = tot_events = 0

    for area in areas:
        if a.radius_miles:
            area = {**area, "radius_miles": a.radius_miles}
            area.pop("bbox", None)          # a radius override beats a stored box
        bbox = area_bbox(area)
        els, from_cache = load_places(a.places_cache, area, a.cache_days, bbox)
        if els is None:
            try:
                els, complete = overpass(area, a.delay)
            except Exception as exc:
                print(f"[osm-food] {area['name']} overpass FAILED: {exc}")
                continue
            # Use a partial list, never keep one. Caching an incomplete pull for
            # thirty days would hide the gap behind a run that looks healthy.
            if els and complete and not a.dry_run:
                save_places(a.places_cache, area, els, bbox)

        # WARM AND STOP.
        #
        # Every matrix job needs the same place lists, and when each fetches its
        # own they all hit Overpass at the same moment — so we rate-limit
        # OURSELVES and then politely back off against our own traffic (2s, 6s,
        # 18s per tile). Watched live on the first matrix run: London, the
        # second-densest hub at 24,970 places, was still pulling tiles long
        # after every other area had finished its whole job.
        #
        # Warming here is one SEQUENTIAL pass before the fan-out, which is both
        # faster wall-clock and a great deal more considerate to a service that
        # is free and volunteer-run. It examines no places and fetches nobody's
        # website — that is the expensive half and it stays in the matrix.
        if a.warm_cache:
            # Count before returning, or the summary reports 0 places while the
            # per-area lines report thousands — a total that contradicts its own
            # detail is worse than no total.
            tot_seen += len(els)
            print(f"[osm-food] {area['name']}: cache warm "
                  f"({len(els)} places){' [from cache]' if from_cache else ''}", flush=True)
            continue
        # Only the ones worth a fetch: a website AND hours we can read. Doing the
        # cheap filters first is what keeps this to a few dozen page loads.
        cands = []
        for el in els:
            t = el.get("tags", {})
            if not (t.get("website") or t.get("contact:website")
                    or any(t.get(k) for k in ("website:menu", "contact:menu", "menu:url", "order:url"))):
                continue
            hrs = parse_opening_hours(t.get("opening_hours", ""))
            if not hrs:
                continue
            cands.append((el, hrs))
        # STABLE ORDER, then a CURSOR. Without both, --max-places made this
        # feature permanently capped: every run examined cands[:60], the same
        # sixty, so 480 of 3,134 candidates would have been the entire ceiling
        # forever and the other 2,654 would never have been looked at once.
        # Nothing would have reported that — the store dedupes, so a weekly run
        # re-examining identical places just adds nothing and looks idle.
        # Same pattern as curation_cursor.json, for the same reason.
        cands.sort(key=lambda c: (c[0].get("type", ""), c[0].get("id", 0)))
        tot_seen += len(els)
        tot_hours += len(cands)

        # --ignore-cursor starts at the beginning and does not move the cursor,
        # so a backfill re-examines places already imported instead of marching
        # past them. Needed because the weekly run can only ever go FORWARD: a
        # change to what we write (the Website line, a verified order link) never
        # reaches a row that was imported before it, and --only-new means the
        # sync would skip it even if it did. It leaves the cursor untouched so a
        # backfill does not also cost the normal rotation its place.
        start = 0 if a.ignore_cursor else int(cursor.get(area["name"], 0)) % max(len(cands), 1)
        window = window_at(cands, start, a.max_places)
        # ADVANCE BY WHAT WAS ACTUALLY EXAMINED, not by --max-places. They differ
        # exactly when the area has fewer candidates than the cap, and asking for
        # 60 of 52 used to leave the cursor at 8 — so the next run re-walked the
        # first eight it had just finished instead of starting cleanly again.
        if not a.dry_run and not a.ignore_cursor:
            cursor[area["name"]] = (start + len(window)) % max(len(cands), 1)

        made = 0
        for el, hrs in window:
            try:
                url, booking, site, website_phone = links_for(el.get("tags", {}))
            except Exception:
                url, booking, site, website_phone = None, None, None, None
            # A PUBLISHED WAY TO TRANSACT is still the bar — a restaurant merely
            # existing is not a listing, which is what lets outreach honestly say
            # nobody at the venue put this here. Booking a table meets that bar
            # exactly as ordering does: it is a time, a seat and a commitment,
            # and it is arguably more of an event than a pickup order. So the
            # gate widens by one door rather than loosening.
            if not (url or booking):
                continue
            if url:
                tot_order += 1
            if booking:
                tot_booking += 1
            for nev in to_events(el, area, url, hrs, a.days_ahead,
                                 booking_url=booking, site=site, website_phone=website_phone):
                # to_events sets the fingerprint from the OSM identity, with no
                # date in it — that is what makes a re-run UPDATE the venue's
                # row instead of adding another. Overwriting it with
                # make_fingerprint (name|date|place) would restore the per-day
                # multiplication AND collapse two Chipotles in one city into one
                # row, since the date is what used to keep them apart.
                if not nev.fingerprint:
                    nev.fingerprint = make_fingerprint(
                        nev.name, (nev.start_local or "")[:10], nev.venue_name, nev.city)
                if store:
                    store.upsert(nev)
                made += 1
        tot_events += made
        # flush=True because this job runs for tens of minutes and Python buffers
        # stdout when it is not a TTY: a local run of all eight areas printed
        # NOTHING for ten minutes and looked hung. In CI that is worse — the log
        # is the only window into a job whose whole cost is being a polite guest
        # on other people's servers.
        print(f"[osm-food] {area['name']}: {len(els)} places, {len(cands)} with hours+site, "
              f"examined {start}-{start + len(window)}, {made} slots", flush=True)

    if store:
        store.save()
    if not a.dry_run and not a.warm_cache:
        save_cursor(cursor)
    if a.warm_cache:
        print(f"[osm-food] cache warm for {len(areas)} area(s) · {tot_seen} places · "
              f"no websites fetched", flush=True)
        return 0
    print(f"[osm-food] done: {tot_seen} places seen · {tot_hours} readable · "
          f"{tot_order} with an order link · {tot_booking} bookable · {tot_events} slots"
          + (" (dry run, nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
