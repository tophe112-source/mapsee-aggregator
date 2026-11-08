#!/usr/bin/env python3
"""
mapsee_ingest_ods.py - import public community-event data from Opendatasoft
portals into the Mapsee store.

Opendatasoft (public.opendatasoft.com and hundreds of city/region portals) is the
dominant INTERNATIONAL open-data platform, with a single uniform Explore v2.1
records API and geo-tagged event datasets - the closest international equivalent
to the US .gov/Socrata civic feeds. Flagship: `evenements-publics-openagenda`
(OpenAgenda public community events, ~1.2M rows with {lon,lat} coordinates).

Each dataset uses its own column names, so you describe it in ods_sources.json
(same idea as opendata_sources.json for Socrata). Events carry coordinates, so no
geocoding is needed. Data is open-licensed - we keep the source link.

    python mapsee_ingest_ods.py --config ods_sources.json --store feeds_events.json

Config per source:
    name        provenance label (source = "ods:<name>")
    domain      portal host, e.g. "public.opendatasoft.com"
    dataset     dataset id, e.g. "evenements-publics-openagenda"
    category    fixed Mapsee category key for the dataset (community/…)
    within_days upcoming window (default 90)
    countries   optional list of ISO country codes to keep (location_countrycode)
    max         row cap (default 6000)
    window      optional: read past the 10,000 offset cap in date windows (below).
                true, or {"max_windows": N, "max_requests": N}
    attribution optional credit line, <= 200 characters, written as the LAST
                paragraph of every row's description (below)
    map         column names: id,title,title_alt,description,description_alt,
                start,end,venue,address,city,region,postal,country,url,geo
                (geo = a {lon,lat} object column) OR lat,lon (numeric columns),
                image (or image_alt) for a poster URL.

THE 10,000 OFFSET CAP, AND WHAT IT HID (measured 2026-11-01..07 from CI logs).
The Explore v2.1 records endpoint serves offset + limit up to 10,000 and refuses
anything deeper. The reading loop asks for 100 rows at a time, ordered by start,
and stops at offset 10,000. On six daily runs (2026-11-01, -03, -04, -05, -06,
-07) "OpenAgenda France" kept 9,982-9,990 rows, every one of them after exactly
100 requests and with no failed page: the window held MORE than that, and
because the order is by start date ascending, everything past row 10,000 - the
far end of the 90-day window - was never read, silently, every day. How far
it reached: the civic job's recovery artifact of 2026-11-07 holds 8,617 France
records (9,987 kept, merged by fingerprint), and they start on 12 days,
2026-11-07..18, of the 90 the query asks for. How much more there is: ONE
count probe at this module's User-Agent on 2026-11-08 (a review's, HTTP 200
in 0.7 s) said total_count 28,495 for the 90-day French query. It is not
reproduced here: two probes from the sandbox this was written in were reset
after 10.3 s each, as every earlier one was. It agrees with the config's older
"~29k upcoming" and sits under the ledger's 41,760 future records of
2026-11-01 (every country, no horizon). Every other source here is far under
the cap: the ten-country OpenAgenda entry kept 715-976 (2026-10-21..11-07),
the largest city dataset 1,933.

So a source with `window` set, whose first page reports total_count at or
above the cap, is read in consecutive date windows instead (ingest_windowed):
the window is halved by its start-date column until each half is under the
cap, earliest first. Each window is the ORIGINAL query plus one or two
`date'YYYY-MM-DD'` bounds on the same start column, half-open, so the windows
partition the original query exactly whatever date this machine thinks it is
(a wrong clock or MAPSEE_TODAY only costs extra splits; it cannot pull in a
past row or drop one at a boundary). `date'...'` is the literal verify_ods
already sends to this dataset, which is how it is known to parse. A window is
never split below one day; a single day still over the cap is read to the cap
and the loss is printed with its number. Rows are deduped across windows by
the adapter's own id (source_id), and the walk has a hard bound on windows and
on requests. Only the `>=` form of that literal has been seen to parse here;
if the portal answers 400 to a windowed query, the plain read of the original
query runs from the page already in hand, so a window never keeps less than
the plain read did (a 403, a 429 or a dead connection still ends the walk).
A 403 or a 429, in either read, is the operator refusing this reader, not
one query: the host goes into main()'s `refused` and no later source on it
is asked in that run. Both OpenAgenda entries are on public.opendatasoft.com,
and France's windows raise its share of that host from about 100 requests
to up to 500 (test_ingest_ods.py, 12).
A source WITHOUT `window`, or one under the cap, takes the old path with the
same requests as before; an over-cap source without `window` now says how
many rows it did not read instead of saying nothing.

THE BUDGET, PART ONE: THIS STEP (arithmetic, from the same six runs). The step
took 165-182 s for about 210 requests; France's 100 requests finished 75-84 s
into it (including loading a store that held 187,252-212,141 rows after the
step), so a page costs at most 0.84 s with the 0.3 s pause. A windowed read
costs one request per 100 rows, plus one discarded first page per split and
one short last page per window: test_ingest_ods.py's front-loaded 25,090-row
fixture took 257 requests in 7 windows (3 splits). At the request bound France
is configured with (500), the step grows by at most 400 x 0.84 s = 5.6 min, to
about 9 min of its 60-minute timeout; the civic job ran 113-167 min of its 300
(2026-10-21..11-07). At the probe's 28,495 the read is ~300 requests, about
2.8 min more.

THE BUDGET, PART TWO: THE SYNC, WHICH IS WHERE THE TIME GOES. Every row read
here is written by the civic job's "Sync feeds to Supabase" step, and that
step, not this one, is the limit: `timeout-minutes` was 60 for civic; a step
cut off at it is a failure that turns the job red and loses the rest of that
day's writes. Measured from CI logs, civic group: the Wednesday full refresh
took 43.7 (2026-10-21), 22.6 (10-28) and 39.8 min (11-04); only-new days
4.4-11.8 min (11-01..11-07). Upserts ran at 14.4-70.8 ms a row (70.8 on 11-04,
16,290 rows in 1,152.7 s with ReadTimeouts and 50 rows lost; 42.3 on 11-06,
10,390 in 439.7 s); preparing and comparing a row on a full refresh at
6.2-11.8 ms. What windowing adds, once: France's new rows, up to max - 9,982
(about 18,500 at the probe's 28,495, 20,018 at max 30,000), inserted on the
first windowed run; and on the FIRST Wednesday, every OpenAgenda row already
stored rewritten for its new credit line (France 9,982-9,990 plus the other
entry's 715-976 kept; 8,617 + 706 store records on 11-07), because the
description is a compared column. Taking the worst of each measurement:
  - first windowed run on a weekday: 11.8 + 20,018 x (70.8 + 7.4) ms = 38 min
  - the first Wednesday after it: 43.7 + 10,966 x 70.8 ms + 20,018 x 11.8 ms
    = 60.6 min (each of the three Wednesdays with its own rates: 27-55 min)
  - first windowed run ON a Wednesday: the line above plus the 20,018 inserts
    at 70.8 ms = 84.2 min (each Wednesday with its own rates: 32-79 min).
So France's max is 30,000, not more. The same step also inserts, once, the
rows Mobilizon's change of the same commit adds (START_TIME_ASC, max_pages
55/25/20): its captured full run of 2026-11-08, replayed offline with the
committed config, stores 11,657 records, 8,117 of them with a fingerprint the
civic job's 2026-11-07 store (run 42375501479, sync "success") lacks, an upper
bound (test_ingest_ods.py's MOBILIZON_NEW_ROWS). With both, the three cases
above are 48.5, 62.2 and 95.4 min, so the civic sync step has 105 minutes;
section 11 reads that timeout from the workflow and fails while any case does
not fit. At 105 the largest France max that fits is about 37,000; at 90 about
26,100. After the first windowed Wednesday the steady cost is small: the
rows are stored and identical, and each day's new far end is about one day of
the window. That steady cost, and the first run's, are UNMEASURED
until the first windowed run's log.

The exports endpoint (/exports/json on the same Explore v2.1 API) has no offset
cap and would be one request, but its size, time and any throttling on a
dataset this large are UNMEASURED, and the session asks for uncompressed
responses (Accept-Encoding: identity), so it is not used.

ATTRIBUTION. Before 2026-11-08 no row carried a credit: the map had no rights
or licensor column and the description was the source's text alone. A source's
`attribution` is now the LAST, short paragraph of every description, which the
sync's _cap_prose keeps when it trims (TAIL_KEEP_MAX = 200), the convention
mapsee_ingest_barcelona, _lcsd and _linkedevents follow. It is a fixed line:
which of the OpenAgenda mirror's columns name the originating agenda and the
last update is UNMEASURED from here, so neither is read.

The User-Agent below is this module's own and predates the house's plain
"MapseeAggregator/1.0 (...)" string; it is reported, not changed.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "Mozilla/5.0 (compatible; MapseeAggregator/1.0; +https://mapsee.me; events@mapsee.me)"

# Explore v2.1 serves offset + limit up to this and refuses deeper pages. The
# measured proof that the boundary is inclusive: France's 100th request
# (offset 9,900, limit 100) was served on all six runs, kept 9,982-9,990.
OFFSET_CAP = 10000
PAGE = 100                       # the records endpoint's largest page
PAUSE_S = 0.3                    # between pages of one source (tests set 0)
# A windowed read's hard bounds, when the source's `window` names none. 500
# requests is 5.6 min at the measured 0.84 s a page (see the header).
DEFAULT_MAX_WINDOWS = 32
DEFAULT_MAX_REQUESTS = 500
# mapsee_supabase_sync.TAIL_KEEP_MAX: the longest final paragraph _cap_prose
# protects from its cut. A longer attribution would be cut off the richest rows.
ATTRIBUTION_MAX = 200


def _clean(s: Any) -> Optional[str]:
    if not s or not isinstance(s, str):
        return None
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _coords(rec: Dict[str, Any], m: Dict[str, str]):
    if m.get("geo") and isinstance(rec.get(m["geo"]), dict):
        g = rec[m["geo"]]
        lat, lon = g.get("lat") or g.get("latitude"), g.get("lon") or g.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    if m.get("lat") and m.get("lon"):
        try:
            return float(rec[m["lat"]]), float(rec[m["lon"]])
        except (TypeError, ValueError, KeyError):
            pass
    return None, None


def _attribution(src: Dict[str, Any]) -> Optional[str]:
    """The source's credit line, or None. Refuses one the sync would cut off."""
    a = src.get("attribution")
    if a is None:
        return None
    if not isinstance(a, str) or not a.strip() or "\n" in a or len(a.strip()) > ATTRIBUTION_MAX:
        raise ValueError(f"{src.get('name', '?')}: attribution must be one line of 1-"
                         f"{ATTRIBUTION_MAX} characters (the sync keeps a final paragraph "
                         f"only that short)")
    return a.strip()


def to_event(rec: Dict[str, Any], src: Dict[str, Any]) -> Optional[NormalizedEvent]:
    m = src["map"]
    title = _clean(rec.get(m.get("title"))) or _clean(rec.get(m.get("title_alt", "")))
    start = rec.get(m.get("start"))
    if not title or not start:
        return None
    lat, lon = _coords(rec, m)
    if lat is None:
        return None                                        # nowhere to pin it
    desc = _clean(rec.get(m.get("description", ""))) or _clean(rec.get(m.get("description_alt", "")))
    # The credit is the LAST paragraph, after the source's own text, so the
    # sync's _cap_prose trims the prose and keeps it. _clean has already folded
    # every newline in the prose, so this "\n\n" is the only paragraph break.
    credit = _attribution(src)
    if credit:
        desc = f"{desc}\n\n{credit}" if desc else credit
    img = None
    for k in (m.get("image"), m.get("image_alt")):
        v = rec.get(k) if k else None
        if isinstance(v, str) and v.startswith("http"):
            img = v; break
    date_key = str(start)[:10]
    venue = _clean(rec.get(m.get("venue", "")))
    nev = NormalizedEvent(
        source="ods:" + src["name"],
        source_id=str(rec.get(m.get("id")) or make_fingerprint(title, date_key, venue)),
        name=title,
        description=desc,
        start_utc=str(start) if str(start).endswith("Z") or "+" in str(start) else None,
        start_local=None if (str(start).endswith("Z") or "+" in str(start)) else str(start),
        end_utc=str(rec.get(m.get("end")) or "") or None if rec.get(m.get("end")) else None,
        venue_name=venue,
        latitude=lat, longitude=lon,
        address=_clean(rec.get(m.get("address", ""))),
        city=_clean(rec.get(m.get("city", ""))),
        region=_clean(rec.get(m.get("region", ""))),
        country=rec.get(m.get("country", "")) or None,
        postal_code=rec.get(m.get("postal", "")) or None,
        category=src.get("category", "community"),
        poster_image_url=img,
        ticket_url=rec.get(m.get("url", "")) or src.get("url_home") or None,
    )
    nev.fingerprint = make_fingerprint(title, date_key, venue)
    return nev


def _today() -> date:
    """Today's UTC date, or MAPSEE_TODAY=YYYYMMDD. Only places window boundaries:
    every window also carries the original now() bounds, so a wrong date costs
    splits, never rows."""
    env = os.environ.get("MAPSEE_TODAY")
    if env and re.fullmatch(r"\d{8}", env):
        return date(int(env[:4]), int(env[4:6]), int(env[6:8]))
    return datetime.now(timezone.utc).date()


def _window_limits(src: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """(max_windows, max_requests) when the source reads past the cap, else None."""
    w = src.get("window")
    if not w:
        return None
    if w is True:
        w = {}
    if not isinstance(w, dict):
        raise ValueError(f"{src.get('name', '?')}: window must be true or an object")
    if not (src.get("map") or {}).get("start"):
        raise ValueError(f"{src.get('name', '?')}: window needs a start column to split on")
    max_windows = int(w.get("max_windows", DEFAULT_MAX_WINDOWS))
    max_requests = int(w.get("max_requests", DEFAULT_MAX_REQUESTS))
    if max_windows < 1 or max_requests < 2:
        raise ValueError(f"{src.get('name', '?')}: window bounds must allow a read "
                         f"(max_windows >= 1, max_requests >= 2)")
    return max_windows, max_requests


def _base_conds(src: Dict[str, Any]) -> List[str]:
    m = src["map"]
    # events STARTING within the window (not merely still-running) - a recurring
    # event whose first occurrence was years ago would otherwise land with a
    # stale past start date and get hidden by the client. Optional country filter.
    start_col = m.get("start")
    within = int(src.get("within_days", 90))
    conds = [f"{start_col}>=now()", f"{start_col}<=now(days={within})"]
    countries = src.get("countries") or []
    if countries and m.get("country"):
        vals = ",".join(f"'{c}'" for c in countries)
        conds.append(f"{m['country']} in ({vals})")
    return conds


# A 403 or a 429 is the operator refusing this reader, not one query (header).
REFUSED_STATUSES = (403, 429)


def _refuse(refused: Optional[Dict[str, str]], src: Dict[str, Any], status: Any) -> None:
    """Record the source's host as refused for the rest of the run, when the
    failure was a 403 or a 429 and the caller keeps a `refused` map."""
    if refused is not None and status in REFUSED_STATUSES:
        refused.setdefault(src["domain"], f"HTTP {status} to {src['name']}")


def ingest_source(store: EventStore, session, src: Dict[str, Any],
                  refused: Optional[Dict[str, str]] = None) -> int:
    """Read one source into the store. `refused` (host -> why), when given,
    gains the source's host on a 403 or a 429 in either read path."""
    m = src["map"]
    base = f"https://{src['domain']}/api/explore/v2.1/catalog/datasets/{src['dataset']}/records"
    _attribution(src)                                  # a bad credit fails the source up front
    win = _window_limits(src)
    conds = _base_conds(src)
    where = " and ".join(conds)
    cap = int(src.get("max", 6000))
    kept = offset = 0
    total: Optional[int] = None
    order = m.get("start")
    while offset < min(cap, OFFSET_CAP):                   # ODS caps offset at 10000
        params = {"where": where, "limit": PAGE, "offset": offset}
        if order:
            params["order_by"] = order
        r = None
        try:
            r = session.get(base + "?" + urllib.parse.urlencode(params), timeout=45)
            r.raise_for_status()
        except Exception as exc:
            print(f"[ods] {src['name']} offset {offset} failed: {exc}")
            _refuse(refused, src, getattr(r, "status_code", None))
            break
        payload = r.json() or {}
        if offset == 0:
            n = payload.get("total_count")
            total = n if isinstance(n, int) else None
            # The first page already says how many rows the query holds, so the
            # decision costs no request. Under the cap nothing below changes.
            if win and total is not None and total >= OFFSET_CAP:
                return ingest_windowed(store, session, src, base, conds, total, win,
                                       payload.get("results") or [], refused=refused)
        rows = payload.get("results") or []
        if not rows:
            break
        for rec in rows:
            nev = to_event(rec, src)
            if nev:
                store.upsert(nev)
                kept += 1
        offset += len(rows)
        if len(rows) < PAGE:
            break
        time.sleep(PAUSE_S)
    if total is not None and total >= OFFSET_CAP and offset < total:
        # What this said nothing about for months: France kept 9,982-9,990 of a
        # larger window every day (header). Now the log carries the number.
        print(f"[ods] {src['name']}: the query holds {total}; {total - offset} rows past "
              f"the {offset} read are NOT read (max {cap}, offset cap {OFFSET_CAP}; set "
              f"'window' in ods_sources.json to read them in date windows)")
    print(f"[ods] {src['name']}: kept {kept} events")
    return kept


class _Stop(Exception):
    """Ends a windowed walk early; the message is the bound or failure that did."""


def ingest_windowed(store: EventStore, session, src: Dict[str, Any], base: str,
                    conds: List[str], total: int, limits: Tuple[int, int],
                    first_rows: Optional[List[Dict[str, Any]]] = None,
                    refused: Optional[Dict[str, str]] = None) -> int:
    """Read an over-cap query in consecutive date windows, earliest first.

    A window is (lo, hi): day offsets from today on the start column, None for
    unbounded. (None, None) is the whole query, whose total the caller has
    already read off its first page (`first_rows`). A window at or over
    OFFSET_CAP is halved at its middle day, without reading it, when it spans
    two days or more and the window bound has room for both halves; otherwise
    it is read to the cap and what is past the cap is printed as lost.
    """
    name = src["name"]
    start_col = src["map"]["start"]
    within = int(src.get("within_days", 90))
    cap = int(src.get("max", 6000))
    max_windows, max_requests = limits
    today = _today()
    # WHERE THE OPEN ENDS SIT, for choosing a middle day. Not at today's index
    # 0: the query's ends are the PORTAL's now() and now(days=within), and the
    # first page, ordered by start, already names the earliest row. Anchored
    # there, a machine date 23 days late reads all 25,090 rows of the test's
    # front-loaded fixture in 257 requests, as the right date does; anchored at
    # "today" it left the first 23 days in one open window that could not be
    # split, held 12,023, and read 10,000 of them (test_ingest_ods.py, 8).
    anchor = 0
    try:
        first = str((first_rows or [{}])[0].get(start_col) or "")[:10]
        anchor = (date.fromisoformat(first) - today).days
    except (ValueError, TypeError, AttributeError):
        pass
    end_index = anchor + within + 1

    def day(i: int) -> str:
        return (today + timedelta(days=i)).isoformat()

    def where_for(lo: Optional[int], hi: Optional[int]) -> str:
        c = list(conds)                                  # the ORIGINAL bounds, always
        if lo is not None:
            c.append(f"{start_col}>=date'{day(lo)}'")
        if hi is not None:
            c.append(f"{start_col}<date'{day(hi)}'")
        return " and ".join(c)

    def label(lo: Optional[int], hi: Optional[int]) -> str:
        return f"{'now' if lo is None else day(lo)}..{'end' if hi is None else day(hi)}"

    requests_made = 1                                    # the caller's first page
    windows = splits = 0
    kept = dupes = rows_read = leaf_total = lost = 0
    seen: set = set()

    def page(where: str, offset: int) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        nonlocal requests_made
        if kept >= cap:
            raise _Stop(f"row cap ({cap})")
        if requests_made >= max_requests:
            raise _Stop(f"request bound ({max_requests})")
        time.sleep(PAUSE_S)
        params = {"where": where, "limit": PAGE, "offset": offset, "order_by": start_col}
        requests_made += 1
        r = None
        try:
            r = session.get(base + "?" + urllib.parse.urlencode(params), timeout=45)
            r.raise_for_status()
            payload = r.json() or {}
        except Exception as exc:
            # One failure ends the walk, as one ends the plain read. A 403 or a
            # 429 is a refusal: the caller puts the host in `refused`, so main()
            # asks no later source on it this run (the other OpenAgenda entry is
            # on the same host), with or without a window.
            stop = _Stop(f"failure at offset {offset}: {exc}")
            stop.status = getattr(r, "status_code", None)
            raise stop
        n = payload.get("total_count")
        return payload.get("results") or [], (n if isinstance(n, int) else None)

    pos = 0                                              # offset reached, for the log

    def read(where: str, rows: Optional[List[Dict[str, Any]]]) -> None:
        """Every page of one query from offset 0, `rows` being page 0 if in hand."""
        nonlocal kept, dupes, rows_read, pos
        pos = 0
        while True:
            if rows is None:
                rows, _ = page(where, pos)
            if not rows:
                return
            for rec in rows:
                rows_read += 1
                nev = to_event(rec, src)
                if not nev:
                    continue
                # One row per id across windows: a row whose start moved
                # between two requests, or a tie re-served across a page
                # boundary, is counted once and stored once.
                if nev.source_id in seen:
                    dupes += 1
                    continue
                seen.add(nev.source_id)
                store.upsert(nev)
                kept += 1
            pos += len(rows)
            if len(rows) < PAGE or pos >= OFFSET_CAP:
                return
            rows = None

    stack: List[Tuple[Optional[int], Optional[int], Optional[int]]] = [(None, None, total)]
    lo = hi = None
    stopped: Optional[_Stop] = None
    try:
        while stack:
            lo, hi, known = stack.pop()
            windows += 1
            where = where_for(lo, hi)
            pos = 0
            rows: Optional[List[Dict[str, Any]]] = None
            wtotal = known
            if wtotal is None:
                rows, wtotal = page(where, 0)
            if wtotal is not None and wtotal >= OFFSET_CAP:
                lo_i = anchor if lo is None else lo
                hi_i = end_index if hi is None else hi
                if hi_i - lo_i >= 2 and windows + len(stack) + 2 <= max_windows:
                    mid = (lo_i + hi_i) // 2
                    stack.append((mid, hi, None))            # later half, read second
                    stack.append((lo, mid, None))            # earlier half, read next
                    splits += 1
                    continue
                why = "one day" if hi_i - lo_i < 2 else f"window bound {max_windows}"
                lost += wtotal - OFFSET_CAP
                print(f"[ods] {name}: window {label(lo, hi)} holds {wtotal}, over the "
                      f"{OFFSET_CAP} offset cap and not split ({why}); "
                      f"{wtotal - OFFSET_CAP} rows in it are NOT read")
            leaf_total += wtotal or 0
            read(where, rows)
    except _Stop as stop:
        stopped = stop
        print(f"[ods] {name}: windowed read stopped by the {stop} in window "
              f"{label(lo, hi)} at offset {pos}; {len(stack)} later window(s) not read")
    print(f"[ods] {name}: the query holds {total} (offset cap {OFFSET_CAP}); read {rows_read} "
          f"rows in {windows} windows ({splits} split) with {requests_made} requests; "
          f"the windows read hold {leaf_total}; {dupes} repeated ids dropped"
          + (f"; {lost} rows over the cap in unsplittable windows" if lost else ""))
    # NEVER WORSE THAN THE PLAIN READ. The date literal is known to parse from
    # verify_ods's `>=`; the `<` and the windowed query as a whole have not been
    # sent to the live portal (it resets this sandbox's connections). A 400 is
    # the portal rejecting the QUERY, not refusing the reader, so when one stops
    # the walk short of what the plain read would have kept, the plain read of
    # the original query runs, from the page already in hand and deduped against
    # what the windows stored. A 403, a 429 or a dead connection does not.
    plain = min(cap, OFFSET_CAP)
    if stopped is not None and getattr(stopped, "status", None) == 400 and kept < plain:
        print(f"[ods] {name}: the portal answered 400 to a windowed query; reading the "
              f"first {plain} the plain way instead")
        try:
            read(" and ".join(conds), first_rows or None)
        except _Stop as stop:
            print(f"[ods] {name}: the plain read stopped by the {stop} at offset {pos}")
            _refuse(refused, src, getattr(stop, "status", None))
    elif stopped is not None:
        _refuse(refused, src, getattr(stopped, "status", None))
    print(f"[ods] {name}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Opendatasoft public event datasets into the Mapsee store.")
    ap.add_argument("--config", default="ods_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    a = ap.parse_args(argv)
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "identity"})
    store = EventStore(a.store)
    total = 0
    refused: Dict[str, str] = {}                           # host -> why, for the rest of the run
    for src in cfg.get("sources", []):
        host = src.get("domain")
        if host in refused:
            print(f"[ods] {src.get('name', '?')}: not asked; {host} refused this run "
                  f"({refused[host]}), and a refusal is not asked again")
            continue
        try:
            total += ingest_source(store, session, src, refused)
        except Exception as exc:                           # one dataset failing must not abort the rest
            print(f"[ods] {src.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[ods] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
