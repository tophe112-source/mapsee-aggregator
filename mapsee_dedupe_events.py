#!/usr/bin/env python3
"""
mapsee_dedupe_events.py — hide the second row of one imported event, and record
which row it copied.

WHAT IT DOES (since 2026-10-13). One event is sometimes on the map twice: a
source renamed it ("Halloween Pet Portraits" became "... Fundraiser" on Discover
SLU, and the fingerprint is hashed from the title, so the sync inserted a second
row beside the first), two adapters read the same library programme, or two
ticket sites list the same show. The upsert cannot delete, so nothing at import
can take the first row away. This job finds those pairs in the table and HIDES
the copy.

  • It lists ONE UTC DAY of LIVE imported rows at a time (visible, not standing,
    not pin_only, not private, not yet ended): yesterday's rows still running,
    every day of --days, then the rows that started up to --lookback days ago
    and are still running, last (window_order says why). Every listing is a
    keyset with a starts_at lower bound the index can use; never offset=, never
    count=; the part of a day that has already started is listed in slices
    that halve on a statement timeout (list_window). --max-seconds covers the
    listing, the reads and the writes.
  • Two rows are one event when they are at most RADIUS_M apart, neither title
    is a ticket add-on, and one of three rules holds (match()):
      same-page     the same event page ("Tickets / info:" link, not a listing
                    page), the same start instant, similar titles, and rows
                    created at least TOGETHER_H apart (one read that lists two
                    records on one page is two records);
      same-slot     the same start instant, on different pages or none: equal
                    titles with no link, an anchored or typo-level title match
                    across two publishers, word-for-word containment within one;
      all-day-copy  the same event page, one row a one-day span (23-25 h) that
                    holds the other's start: a feed that gives a date where the
                    other gives a clock.
    Category is NOT compared: 305 of 1,012 matched pairs are filed under two.
  • One row of each cluster survives: claimed, then one people are on (an RSVP,
    chat, a live promotion), then a row with a clock over a clockless copy, then
    the newest import (the source's current title), then id. Only a member that
    matched the survivor DIRECTLY, is unclaimed and has nobody on it is hidden.
  • A hidden copy gets hidden_at AND duplicate_of = the survivor's id (../mapsee
    migration 0236). Nothing is deleted: a delete cascades RSVPs and chat, and
    the next sync's existence read would have re-inserted the key. A hidden row
    still counts as existing to fetch_import_state, so --only-new never brings
    it back; to_row writes neither column, so Wednesday's refresh leaves it; the
    sync lifts only its own cancellation (cancelled_at == hidden_at).
  • REPORT ONLY unless --apply, and --apply is refused (a report, exit 1) while
    events.duplicate_of does not exist. In CI the switch is the repository
    variable DEDUPE_APPLY. --unhide --stamp S reverses exactly one run.

WHY IT CHANGED. Measured 2026-10-13 (census of 25,369 live imported rows in 11
metros): 1,153 redundant rows (4.5%; Chicago 11.1%). The old rule (same
normalized title + category + start, 1.5 km single-link) reached 282 of 1,373
true pairs and none of the renames or all-day copies, and its listing had no
starts_at floor (~1.49M planned rows, sized for 193k), so the step was killed at
its 12-minute timeout on 2026-09-29..10-07 and hit 57014 from 10-08 on: it
removed nothing for weeks while its step showed green. The rules above, run over
the census's 15,632 labelled candidate pairs: 1,005 matched, 1,000 true, 5 on a
listing link, 0 sessions or branches; 854 rows hidden, 74% of the redundancy.
Deliberately left out: re-timed and re-dated copies (the shape of a matinee or a
weekly occurrence), cross-posts whose titles name different things, standing
rows, and the 6 cross-source pairs whose starts differ (which includes the
market whose sources disagree about the clock: the market adapter's name +
geohash identity collapses those at import now).

HISTORY — what this file was for before, kept because the reasons still hold.

A market reaches the map from up to three places: a curated city list,
OpenStreetMap's `amenity=marketplace`, and the USDA Local Food Directories. They
agree on the name and on the coordinates to within a few hundred metres, and
disagree on the address string. The import fingerprint used to be keyed on that
address, so one market was filed three times and drew three pins.
mapsee_ingest_markets.py keys identity on the name plus a ~5km geohash cell,
which collapses the vast majority at import. The database is still the one place
where every source's rows meet (sources run on different weekdays and the store
is rebuilt each run, so two sources' duplicates are never in memory together).

NOT ONLY MARKETS, since 2026-08-12: 407 clusters, 428 redundant rows in thirteen
categories over the 20,000 nearest upcoming rows. A MATINEE IS NOT THE EVENING
SHOW: everything keys on the exact start instant, never on a date, because two
performances of one show share a title, a venue and a day. That is still why
same-slot and same-page compare instants, and why the one rule that does not
(all-day-copy) needs one row to be a whole-day span and both to share a page.

STILL RUNNING COUNTS AS CURRENT (2026-08-12: 1,354 redundant rows were rows that
had started and not ended): the --lookback windows are that lesson.

SAFETY. Scoped to `external_source = 'mapsee'`, asserted rather than trusted, so
a user's own events can never be matched. Every write carries that scope plus
claimed_at=is.null and hidden_at=is.null, so the database decides at write time.
A window refuses if one cluster exceeds --max-cluster (the census's largest true
cluster was 7) or its hides exceed max(SHARE_FLOOR, --max-share of its rows).

EXIT CODE. Non-zero when anything is left owed: a window the budget never
reached (BACKLOG), a window whose listing or reads failed, a refusal, a write
that failed, or --apply asked for without the column. It used to print that and
exit 0 under `|| true`, so a job that had stopped keeping up looked exactly like
a clean one. Same reasoning as runCronTask in ../mapsee.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; MAPSEE_TODAY=YYYYMMDD fixes now
      (that day at 12:00Z), for tests.
Run:  python mapsee_dedupe_events.py                            # report
      python mapsee_dedupe_events.py --apply --report r.json    # hide
      python mapsee_dedupe_events.py --unhide --stamp 2026-10-14T06:30:00+00:00
      python mapsee_dedupe_events.py --unhide --stamp ... --apply
"""
import argparse
import difflib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import normalize_text
# The sync's own title helpers, so the audience guard ("ages 6+" is not
# "ages 60+", "[Goalies]" is not the skaters' session) stays ONE copy.
from mapsee_supabase_sync import _AUDIENCE_WORD, _ident_words, _missing_column, _similar_titles

PAGE = 1000                    # rows per listing page
ID_BATCH = 100                 # ids per id=in.(...) read or write
WORKERS = 8                    # concurrent hide PATCHes
SHARE_FLOOR = 25               # a window may always hide this many
TIMEOUT_CODE = "57014"         # postgres: canceling statement due to statement timeout
SELECT = "id,title,lat,lon,starts_at,ends_at,created_at,claimed_at,last_message_at,promoted_until"
LIVE = "&hidden_at=is.null&pin_only=not.is.true&recurring_hours=is.null&is_private=is.false"

RADIUS_M = 150.0          # census: 287 of the old rule's 728 drops were >150 m (branches, centres)
TOGETHER_H = 12.0         # census: 0 false positives; NYC Tennis, Status Mic were made in one read
GENERIC_PATHS = {"", "events", "event", "calendar", "whats-on", "agenda"}
GENERIC_TITLES = 2        # a link carrying more than this many distinct titles is a listing page
ALL_DAY_MIN_H, ALL_DAY_MAX_H = 23.0 - 1.0 / 60.0, 25.0
GRID_DEG = 0.002          # candidate grid: 222 m of latitude, wider than RADIUS_M
RUNNING_SLICE = timedelta(hours=6)       # first slice of the part of a window that has started
RUNNING_FLOOR = timedelta(minutes=15)    # a statement timeout halves the slice down to this
LINK_RX = re.compile(r"Tickets / info: (https?://\S+)")
NOT_AN_EVENT = re.compile(r"not a (concert )?ticket|not an event|\badd[ -]?on\b|parking|"
                          r"express entry|amplified access", re.I)
STOP = {"the", "a", "an", "and", "at", "vs", "v", "versus", "with", "w", "featuring", "feat",
        "ft", "presents", "live", "in", "of", "de", "la", "le"}
ARMS = ("same-page", "same-slot", "all-day-copy")

_sleep = time.sleep            # patched by the tests
_clock = time.monotonic


def _timed_out(resp) -> bool:
    return resp.status_code >= 500 and TIMEOUT_CODE in (resp.text or "")


def _p(*args) -> None:
    print(*args, flush=True)


# --------------------------------------------------------------------------- #
# THE RULES (pure; test_dedupe_events.py)
# --------------------------------------------------------------------------- #
def utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def metres(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, h)))


def canonical_link(description: Optional[str]) -> Optional[str]:
    """The row's own 'Tickets / info:' URL as host+path(+query): no scheme,
    no www., no fragment, path lower-cased without a trailing slash."""
    m = LINK_RX.search(description or "")
    if not m:
        return None
    u = urlparse(m.group(1).rstrip(".,)"))
    host = u.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return None
    return host + u.path.rstrip("/").lower() + (f"?{u.query}" if u.query else "")


def link_host(link: str) -> str:
    return link.split("/", 1)[0]


def same_publisher(la: str, lb: str) -> bool:
    ha, hb = link_host(la), link_host(lb)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def is_generic(link: str, titles_by_link: Dict[str, Set[str]]) -> bool:
    """A listing page rather than this event's page: its path is a calendar's
    front (/events, /calendar ...), or it carries more than GENERIC_TITLES
    distinct titles among the rows read this run."""
    path = link.split("/", 1)[1] if "/" in link else ""
    return path.strip("/") in GENERIC_PATHS or len(titles_by_link.get(link, ())) > GENERIC_TITLES


def _toks(title: str) -> List[str]:
    return [w for w in _ident_words(title).split() if w not in STOP]


def title_relation(a: str, b: str) -> Dict[str, bool]:
    """eq: the same title. sim: one contains the other word for word, with the
    sync's audience guard. typo: same length, 1-2 words differ slightly and none
    is an audience word. anchored: the shorter's words are a subset of the
    longer's and sit at its start, its end, or share its first word. loose: any
    of those, or token Jaccard >= 0.5 (only ever used with the same page)."""
    ia, ib = _ident_words(a), _ident_words(b)
    eq = normalize_text(a) == normalize_text(b) or (bool(ia) and ia.replace(" ", "") == ib.replace(" ", ""))
    sim = _similar_titles(ia, ib)
    ta, tb = _toks(a), _toks(b)
    typo = False
    if ta and len(ta) == len(tb):
        diff = [(x, y) for x, y in zip(ta, tb) if x != y]
        typo = 0 < len(diff) <= 2 and all(
            difflib.SequenceMatcher(None, x, y).ratio() >= 0.8
            and not _AUDIENCE_WORD.fullmatch(x) and not _AUDIENCE_WORD.fullmatch(y) for x, y in diff)
    anchored = False
    if ta and tb:
        s, l = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if set(s) <= set(l):
            n = len(s)
            anchored = set(s) == set(l) or l[:n] == s or l[-n:] == s or s[0] == l[0]
    sa, sb = set(ia.split()), set(ib.split())
    jacc = len(sa & sb) / len(sa | sb) if sa | sb else 0.0
    return {"eq": eq, "sim": sim, "typo": typo, "anchored": anchored,
            "loose": eq or sim or typo or anchored or jacc >= 0.5}


def hours(a: Dict[str, Any]) -> float:
    s = utc(a["starts_at"])
    e = utc(a["ends_at"]) if a.get("ends_at") else s
    return (e - s).total_seconds() / 3600.0


def is_all_day(r: Dict[str, Any]) -> bool:
    return ALL_DAY_MIN_H <= hours(r) <= ALL_DAY_MAX_H


def within_day(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """One row is a one-day span (23-25 h) and the other, shorter, starts
    inside its first 24 hours. Needs no time zone: the span is the day."""
    for d, t in ((a, b), (b, a)):
        if is_all_day(d) and not is_all_day(t):
            s = utc(d["starts_at"])
            if s <= utc(t["starts_at"]) < s + timedelta(hours=24):
                return True
    return False


def gap_hours(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    if not (a.get("created_at") and b.get("created_at")):
        return 0.0                                # unknown: treat as made together
    return abs((utc(a["created_at"]) - utc(b["created_at"])).total_seconds()) / 3600.0


def match(a: Dict[str, Any], b: Dict[str, Any], titles_by_link: Dict[str, Set[str]]) -> Optional[str]:
    """'same-page' | 'same-slot' | 'all-day-copy' | None. Rows are listed
    (visible, imported, not standing, not pin_only) and carry 'link'."""
    return _decide(a, b, titles_by_link)[0]


def why_not(a: Dict[str, Any], b: Dict[str, Any], titles_by_link: Dict[str, Set[str]]) -> Optional[str]:
    """The reason a candidate pair was NOT matched (the report's near misses)."""
    return _decide(a, b, titles_by_link)[1]


def _decide(a, b, titles_by_link) -> Tuple[Optional[str], Optional[str]]:
    if NOT_AN_EVENT.search(a.get("title") or "") or NOT_AN_EVENT.search(b.get("title") or ""):
        return None, "ticket add-on"
    if a.get("lat") is None or b.get("lat") is None or metres(a, b) > RADIUS_M:
        return None, "distance"
    t = title_relation(a.get("title") or "", b.get("title") or "")
    la, lb = a.get("link"), b.get("link")
    if utc(a["starts_at"]) == utc(b["starts_at"]):
        if la and lb and la == lb:
            if gap_hours(a, b) < TOGETHER_H:
                return None, "same page, made together"     # one read listed both: two records
            if is_generic(la, titles_by_link):
                return None, "same page, a listing page"    # not this event's page
            return ("same-page", None) if t["loose"] else (None, "same page, titles")
        if not (la and lb):
            return ("same-slot", None) if t["eq"] else (None, "same slot, no link, titles")
        if not same_publisher(la, lb):
            return (("same-slot", None) if (t["eq"] or t["anchored"] or t["typo"])
                    else (None, "same slot, two publishers, titles"))
        return (("same-slot", None) if (t["eq"] or t["sim"] or t["typo"])
                else (None, "same slot, one publisher, titles"))
    if within_day(a, b):
        if not (la and lb and la == lb):
            return None, "all-day, different pages"
        if is_generic(la, titles_by_link):
            return None, "all-day, a listing page"
        return ("all-day-copy", None) if t["loose"] else (None, "all-day, titles")
    return None, "other start"


def engaged(r: Dict[str, Any], rsvp_ids: Set[str], now: datetime) -> bool:
    return (r["id"] in rsvp_ids or bool(r.get("last_message_at"))
            or bool(r.get("promoted_until") and utc(r["promoted_until"]) > now))


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def survivor_key(r: Dict[str, Any], rsvp_ids: Set[str], now: datetime) -> Tuple:
    """Claimed, then people on it, then a row with a clock over a clockless
    copy, then the newest import (the source's current title), then id.

    The clock term sits before age on purpose: in 193 of 219 all-day copies the
    clockless row is the NEWER one, because both keys are still emitted (by two
    adapters), so 'newest' would keep the row without a time 88% of the time."""
    made = utc(r["created_at"]) if r.get("created_at") else _EPOCH
    return (bool(r.get("claimed_at")), engaged(r, rsvp_ids, now), not is_all_day(r), made, r["id"])


def plan(rows: Dict[str, Dict[str, Any]], edges: Dict[Tuple[str, str], str],
         rsvp_ids: Set[str], now: datetime, max_cluster: int = 12) -> List[Dict[str, Any]]:
    """Union-find over matched pairs; one survivor per component; hide only
    members with a DIRECT match to the survivor, unclaimed and with nobody
    on them (a star, not a chain). Raises ValueError on a component over
    max_cluster."""
    par: Dict[str, str] = {}

    def find(x: str) -> str:
        while par.setdefault(x, x) != x:
            par[x] = par.get(par[x], par[x])
            x = par[x]
        return x
    adj: Dict[str, Dict[str, str]] = {}
    for (x, y), arm in sorted(edges.items()):
        par[find(x)] = find(y)
        adj.setdefault(x, {})[y] = arm
        adj.setdefault(y, {})[x] = arm
    comps: Dict[str, Set[str]] = {}
    for x in adj:
        comps.setdefault(find(x), set()).add(x)
    out = []
    for comp in sorted(comps.values(), key=lambda c: min(c)):
        if len(comp) > max_cluster:
            raise ValueError(f"cluster of {len(comp)} rows (limit {max_cluster}): "
                             f"{sorted(str(rows[i].get('title')) for i in comp)[:3]}")
        s = max(comp, key=lambda i: survivor_key(rows[i], rsvp_ids, now))
        hide, left = [], []
        for i in sorted(comp - {s}):
            r = rows[i]
            if i in adj[s] and not r.get("claimed_at") and not engaged(r, rsvp_ids, now):
                hide.append((i, adj[s][i]))
            else:
                left.append(i)
        out.append({"survivor": s, "hide": hide, "left": left})
    return out


def candidate_pairs(rows: List[Dict[str, Any]], carry: Iterable[Dict[str, Any]] = ()
                    ) -> List[Tuple[str, str]]:
    """Pairs worth reading links for: the same start instant, or a one-day row
    and a shorter row starting inside it; at most RADIUS_M apart; titles at
    least loosely alike. `carry` is the previous day's one-day rows (a Sydney
    all-day row starts at 13:00Z the day before its timed twin); a pair of two
    carried rows was already seen in their own window."""
    own = {r["id"] for r in rows}
    carried = [c for c in carry if c["id"] not in own]
    carry_ids = {c["id"] for c in carried}
    meta: Dict[str, Tuple[datetime, bool]] = {}
    by_start: Dict[Tuple[int, int], Dict[datetime, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    timed: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    cell_of: Dict[str, Tuple[int, int]] = {}
    for r in list(rows) + carried:
        if r.get("lat") is None or r.get("lon") is None or not r.get("starts_at"):
            continue
        start, allday = utc(r["starts_at"]), is_all_day(r)
        meta[r["id"]] = (start, allday)
        cell = (math.floor(r["lat"] / GRID_DEG), math.floor(r["lon"] / GRID_DEG))
        cell_of[r["id"]] = cell
        by_start[cell][start].append(r)
        if not allday:
            timed[cell].append(r)
    out: Set[Tuple[str, str]] = set()
    titles: Dict[Tuple[str, str], bool] = {}

    def consider(r: Dict[str, Any], s: Dict[str, Any]) -> None:
        if r["id"] == s["id"] or (r["id"] in carry_ids and s["id"] in carry_ids):
            return
        key = (r["id"], s["id"]) if r["id"] < s["id"] else (s["id"], r["id"])
        if key in out or key in titles:
            return
        if metres(r, s) > RADIUS_M:
            return
        titles[key] = title_relation(r.get("title") or "", s.get("title") or "")["loose"]
        if titles[key]:
            out.add(key)

    for r in list(rows) + carried:
        if r["id"] not in meta:
            continue
        start, allday = meta[r["id"]]
        ci, cj = cell_of[r["id"]]
        span = RADIUS_M / (111320.0 * max(0.01, math.cos(math.radians(abs(r["lat"]) + 0.01))))
        k = int(math.ceil(span / GRID_DEG))
        for di in (-1, 0, 1):
            for dj in range(-k, k + 1):
                cell = (ci + di, cj + dj)
                if cell not in by_start:
                    continue
                for s in by_start[cell].get(start, ()):
                    if s["id"] > r["id"]:
                        consider(r, s)
                if allday:
                    for s in timed.get(cell, ()):
                        if start <= meta[s["id"]][0] < start + timedelta(hours=24):
                            consider(r, s)
    return sorted(out)


def window_order(days: int, lookback: int) -> List[int]:
    """Day offsets from today's UTC midnight: yesterday's rows still running
    (its one-day rows are carried into today), then every day forward, then
    the older rows still running, LAST. Those are the dearest to list (a past
    day's whole range is scanned for the few rows still on) and the least
    productive: 3 of the census's 854 hides, all Chicago Park District season
    programmes that started 21-29 days before; so a budget that runs out costs
    them, not tomorrow."""
    return ([-1] if lookback >= 1 else []) + list(range(days)) + [-d for d in range(2, lookback + 1)]


# --------------------------------------------------------------------------- #
# THE DATABASE (PostgREST; every URL carries external_source=eq.mapsee)
# --------------------------------------------------------------------------- #
def Z(dt: datetime) -> str:
    """A timestamp for a filter: UTC, microseconds, a 'Z', quoted."""
    return quote(dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), safe="")


class Db:
    def __init__(self, url: str, key: str, http):
        self.url = url.rstrip("/")
        self.base = self.url + "/rest/v1/events?external_source=eq.mapsee"
        # SAFETY: imported events only. Everything below appends to this string.
        assert "external_source=eq.mapsee" in self.base, "refusing an unscoped read or write"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.http = http
        self.calls = Counter()

    def get(self, url: str, tries: int = 3, waits=(2.0, 4.0), retry_timeouts: bool = True):
        """A GET retried on a statement timeout, a 5xx, a 429 or a dropped
        connection. -> the last response, or None if every try raised. With
        retry_timeouts=False a 57014 comes straight back: the caller shrinks
        the range instead (list_window)."""
        resp = None
        for attempt in range(tries):
            self.calls["GET"] += 1
            try:
                resp = self.http.get(url, headers=self.headers, timeout=60)
            except Exception as exc:                  # noqa: BLE001
                _p(f"  read failed ({type(exc).__name__})")
                resp = None
            else:
                if not retry_timeouts and _timed_out(resp):
                    self.calls["57014"] += 1
                    return resp
                if not (_timed_out(resp) or resp.status_code >= 500 or resp.status_code == 429):
                    return resp
            if attempt + 1 < tries:
                _sleep(waits[min(attempt, len(waits) - 1)])
        return resp

    def patch(self, url: str, body: Dict[str, Any], tries: int = 3, waits=(1.5, 3.0)):
        assert "external_source=eq.mapsee" in url, "refusing an unscoped write"
        headers = dict(self.headers, Prefer="return=representation")
        headers["Content-Type"] = "application/json"
        resp = None
        for attempt in range(tries):
            self.calls["PATCH"] += 1
            try:
                resp = self.http.patch(url, headers=headers, data=json.dumps(body), timeout=60)
            except Exception as exc:                  # noqa: BLE001
                _p(f"  write failed ({type(exc).__name__})")
                resp = None
            else:
                if resp.status_code < 300:
                    return resp
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp                       # a refusal: retrying will not help
            if attempt + 1 < tries:
                _sleep(waits[min(attempt, len(waits) - 1)])
        return resp


def _ok(resp) -> bool:
    return resp is not None and resp.status_code < 300


def _why(resp) -> str:
    if resp is None:
        return "no response"
    return f"[{resp.status_code}] {(resp.text or '')[:200]}"


def column_exists(db: Db, column: str) -> Optional[bool]:
    """True/False, or None when the probe itself failed."""
    resp = db.get(f"{db.base}&select={column}&limit=0")
    if _ok(resp):
        return True
    if resp is not None and resp.status_code == 400 and _missing_column(resp):
        return False
    return None


def _list_range(db: Db, lo: datetime, hi: datetime, now: datetime, budget, retry_timeouts: bool
                ) -> Tuple[List[Dict[str, Any]], str, int]:
    """Every live row starting in [lo, hi), paged.
    -> (rows, 'ok'|'timeout'|'failed'|'budget', pages).

    A KEYSET WITH A LOWER BOUND THE INDEX CAN USE: the cursor is
    starts_at >= s AND (starts_at > s OR id > last), not the or() alone. The
    bare or() climbed 0.81 -> 2.03 s over five pages of one day on the anon
    key; with the gte beside it, 0.45-1.01 s over six. No count: count=exact
    hit 57014 at 3.2-3.6 s on three of four days."""
    rows: List[Dict[str, Any]] = []
    lower = f"starts_at.gte.{Z(lo)}"
    pages = 0
    while True:
        if budget():
            return rows, "budget", pages
        url = (f"{db.base}{LIVE}&ends_at=gte.{Z(now)}&select={SELECT}"
               f"&order=starts_at.asc,id.asc&limit={PAGE}&and=({lower},starts_at.lt.{Z(hi)})")
        resp = db.get(url, retry_timeouts=retry_timeouts)
        pages += 1
        if not _ok(resp):
            if resp is not None and _timed_out(resp):
                return rows, "timeout", pages
            _p(f"  listing failed on page {pages}: {_why(resp)}")
            return rows, "failed", pages
        page = resp.json() or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows, "ok", pages
        last = page[-1]
        s = Z(utc(last["starts_at"]))
        lower = f"starts_at.gte.{s},or(starts_at.gt.{s},id.gt.{last['id']})"


def list_window(db: Db, lo: datetime, hi: datetime, now: datetime, budget
                ) -> Tuple[List[Dict[str, Any]], List[Tuple[datetime, datetime, str]], int]:
    """Every live row starting in [lo, hi). -> (rows, [(from, to, why) not
    listed], pages).

    A WINDOW THAT HAS STARTED IS TWO LISTINGS. From `now` on every row is live,
    so the plain keyset returns what it scans. Before `now` the index walks
    every row that started in the range to return the few still running: on
    2026-10-13 (anon key, 3 s ceiling) the whole of today and of yesterday hit
    57014 on their first page, as did five of six 3-hour slices of today, while
    the day after listed 28,759 rows in 29 pages. So the started part goes in
    slices of RUNNING_SLICE, and a statement timeout HALVES the slice (the cost
    is the rows the range scans, not the rows it returns) down to
    RUNNING_FLOOR, rather than re-asking the same range. A part that still
    fails is reported and the rest of the window is used: a row missing from a
    window can only lose a match, never cause a wrong hide."""
    rows: List[Dict[str, Any]] = []
    missed: List[Tuple[datetime, datetime, str]] = []
    pages = 0
    t, step, end = lo, RUNNING_SLICE, min(now, hi)
    while t < end:
        b = min(t + step, end)
        got, status, n = _list_range(db, t, b, now, budget, retry_timeouts=False)
        pages += n
        if status == "timeout" and step > RUNNING_FLOOR:
            step = max(RUNNING_FLOOR, step / 2)          # re-ask a smaller slice
            continue
        rows.extend(got)
        if status != "ok":
            stop = end if status == "budget" else b
            if missed and missed[-1][1] == t and missed[-1][2] == status:
                missed[-1] = (missed[-1][0], stop, status)      # one part, not 96 slices
            else:
                missed.append((t, stop, status))
            if status == "budget":
                return rows, missed, pages
        t = b
    if hi > now:
        a = max(lo, now)
        got, status, n = _list_range(db, a, hi, now, budget, retry_timeouts=True)
        pages += n
        rows.extend(got)
        if status != "ok":
            missed.append((a, hi, status))
    return rows, missed, pages


def _chunks(ids: Iterable[str], n: int = ID_BATCH) -> Iterable[List[str]]:
    ids = sorted(set(ids))
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def read_links(db: Db, ids: Iterable[str]) -> Optional[Dict[str, Optional[str]]]:
    """{id: canonical link or None} read from each row's description."""
    out: Dict[str, Optional[str]] = {}
    for chunk in _chunks(ids):
        resp = db.get(f"{db.base}&select=id,description&id=in.({','.join(chunk)})")
        if not _ok(resp):
            _p(f"  link read failed: {_why(resp)}")
            return None
        for r in resp.json() or []:
            out[r["id"]] = canonical_link(r.get("description"))
    return out


def read_rsvp_ids(db: Db, ids: Iterable[str]) -> Optional[Set[str]]:
    """The ids among these that anybody has RSVPed to, whatever the status."""
    out: Set[str] = set()
    for chunk in _chunks(ids):
        offset = 0
        while True:
            resp = db.get(f"{db.url}/rest/v1/event_rsvps?select=event_id&event_id=in.({','.join(chunk)})"
                          f"&order=event_id.asc&limit=1000&offset={offset}")
            if not _ok(resp):
                _p(f"  RSVP read failed: {_why(resp)}")
                return None
            got = resp.json() or []
            out.update(r["event_id"] for r in got)
            if len(got) < 1000:
                break
            offset += len(got)                        # bounded by one chunk's RSVPs
    return out


def visible_ids(db: Db, ids: Iterable[str]) -> Optional[Set[str]]:
    out: Set[str] = set()
    for chunk in _chunks(ids):
        resp = db.get(f"{db.base}&select=id&hidden_at=is.null&id=in.({','.join(chunk)})")
        if not _ok(resp):
            _p(f"  survivor read failed: {_why(resp)}")
            return None
        out.update(r["id"] for r in resp.json() or [])
    return out


def write(db: Db, plans: List[Dict[str, Any]], stamp: str, budget, workers: int = WORKERS
          ) -> Dict[str, int]:
    """Hide each plan's copies, naming the survivor. Re-checked at write time:
    the survivor must still be visible, and a copy somebody RSVPed to since the
    listing is skipped. The filter carries claimed_at=is.null and
    hidden_at=is.null, so the database has the last word."""
    res = {"hidden": 0, "skipped": 0, "owed": 0, "survivor_gone": 0, "late_rsvp": 0}
    todo = [p for p in plans if p["hide"]]
    if not todo:
        return res
    every = [h for p in todo for h, _ in p["hide"]]
    alive = visible_ids(db, [p["survivor"] for p in todo])
    late = read_rsvp_ids(db, every) if alive is not None else None
    if alive is None or late is None:
        res["owed"] = len(every)
        return res
    jobs = []
    for p in todo:
        if p["survivor"] not in alive:
            res["survivor_gone"] += len(p["hide"])
            continue
        ids = [h for h, _ in p["hide"] if h not in late]
        res["late_rsvp"] += len(p["hide"]) - len(ids)
        if ids:
            jobs.append((p["survivor"], ids))

    def one(job: Tuple[str, List[str]]) -> Tuple[str, int, int]:
        survivor, ids = job
        if budget():
            return "owed", 0, len(ids)
        url = (f"{db.base}&claimed_at=is.null&hidden_at=is.null&select=id"
               f"&id=in.({','.join(ids)})")
        resp = db.patch(url, {"hidden_at": stamp, "duplicate_of": survivor})
        if not _ok(resp):
            _p(f"  hide failed for {len(ids)} row(s) of {survivor}: {_why(resp)}")
            return "owed", 0, len(ids)
        n = len(resp.json() or [])
        return "ok", n, len(ids) - n

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for status, n, rest in pool.map(one, jobs):
            if status == "ok":
                res["hidden"] += n
                res["skipped"] += rest
            else:
                res["owed"] += rest
    return res


def orphaned_copies(db: Db, now: datetime) -> Optional[int]:
    """Live copies this job hid whose survivor another writer has hidden since
    (a cancellation, a take-down, a prune). They stay hidden, which is the
    owner's rule that nobody is sent to an event that is off; counted so a
    person can see it."""
    targets: Set[str] = set()
    last = None
    while True:
        after = f"&and=(id.gt.{last})" if last else ""
        resp = db.get(f"{db.base}&duplicate_of=not.is.null&hidden_at=not.is.null&ends_at=gte.{Z(now)}"
                      f"&select=id,duplicate_of&order=id.asc&limit={PAGE}{after}")
        if not _ok(resp):
            return None
        got = resp.json() or []
        targets.update(r["duplicate_of"] for r in got if r.get("duplicate_of"))
        if len(got) < PAGE:
            break
        last = got[-1]["id"]
    if not targets:
        return 0
    alive = visible_ids(db, targets)
    return None if alive is None else len(targets - alive)


def unhide(db: Db, stamp: Optional[str], ids: List[str], apply: bool, show: int) -> int:
    """Put back what one run hid (--stamp), or named rows (--id). Only rows this
    job hid (duplicate_of set) and nobody cancelled since."""
    # One filter per column: hidden_at=eq.<stamp> already implies not null.
    scope = (f"{db.base}&duplicate_of=not.is.null&cancelled_at=is.null"
             + (f"&hidden_at=eq.{quote(stamp, safe='')}" if stamp else "&hidden_at=not.is.null"))
    flt = scope + (f"&id=in.({','.join(sorted(set(ids)))})" if ids else "")
    rows: List[Dict[str, Any]] = []
    last = None
    while True:
        after = f"&and=(id.gt.{last})" if last else ""
        resp = db.get(f"{flt}&select=id,title,starts_at,duplicate_of&order=id.asc&limit={PAGE}{after}")
        if not _ok(resp):
            _p(f"Couldn't list the hidden copies: {_why(resp)}")
            return 1
        got = resp.json() or []
        rows.extend(got)
        if len(got) < PAGE:
            break
        last = got[-1]["id"]
    _p(f"Copies this job hid that match: {len(rows)}")
    for r in rows[:show]:
        _p(f"  {r['id']}  {str(r.get('starts_at') or '')[:16]}  {r.get('title')!r}  copy of {r.get('duplicate_of')}")
    if len(rows) > show:
        _p(f"  ... and {len(rows) - show} more")
    if not apply:
        _p("Report only: nothing un-hidden (add --apply).")
        return 0
    done = owed = 0
    for chunk in _chunks(r["id"] for r in rows):
        resp = db.patch(f"{scope}&select=id&id=in.({','.join(chunk)})", {"hidden_at": None, "duplicate_of": None})
        if _ok(resp):
            done += len(resp.json() or [])
        else:
            owed += len(chunk)
            _p(f"  un-hide failed for {len(chunk)} row(s): {_why(resp)}")
    _p(f"Un-hid {done} row(s) (hidden_at and duplicate_of cleared).")
    if owed:
        _p(f"OWED: {owed} row(s) not un-hidden; re-run the same command.")
    return 1 if owed else 0


# --------------------------------------------------------------------------- #
# THE RUN
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").replace(hour=12, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def main(argv: Optional[List[str]] = None, http=None) -> int:
    ap = argparse.ArgumentParser(description="Hide duplicate imported events in Supabase (report unless --apply).")
    ap.add_argument("--apply", action="store_true",
                    help="Hide the copies. Default: report what would be hidden; write nothing.")
    ap.add_argument("--days", type=int, default=21, help="Forward horizon in UTC days (default 21).")
    ap.add_argument("--lookback", type=int, default=60,
                    help="Also rows that started up to this many days ago and are still running (default 60).")
    ap.add_argument("--max-seconds", type=int, default=1200,
                    help="Budget for the whole run: listing, reads and writes (default 1200).")
    # The census's largest true cluster was 7 and this rule's largest is 6; a
    # grouping bug looks like hundreds in one cluster.
    ap.add_argument("--max-cluster", type=int, default=12,
                    help="Refuse a window in which one event clusters more rows than this (default 12).")
    ap.add_argument("--max-share", type=float, default=0.15,
                    help=f"Refuse a window whose hides exceed max({SHARE_FLOOR}, this share of its rows) "
                         "(default 0.15; the census measured 3.4% overall, Chicago 11.1%).")
    ap.add_argument("--report", default=None, help="Write the full report (every pair) as JSON here.")
    ap.add_argument("--show", type=int, default=15, help="Examples to print per rule (default 15).")
    ap.add_argument("--unhide", action="store_true",
                    help="Reverse: un-hide copies this job hid. Needs --stamp or --id; reports unless --apply.")
    ap.add_argument("--stamp", default=None, help="With --unhide: the hidden_at stamp of one run (from its report).")
    ap.add_argument("--id", action="append", default=[], help="With --unhide: one row id (repeatable).")
    a = ap.parse_args(argv)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    db = Db(url, key, http if http is not None else requests.Session())

    if a.unhide:
        if not (a.stamp or a.id):
            sys.exit("--unhide needs --stamp (one run) or --id (named rows); it never un-hides everything.")
        if column_exists(db, "duplicate_of") is not True:
            sys.exit("events.duplicate_of is missing (../mapsee 0236 not applied) or unreadable: "
                     "nothing this job hid can be told apart.")
        return unhide(db, a.stamp, a.id, a.apply, a.show)

    now = _now()
    started = _clock()
    budget = lambda: _clock() - started > a.max_seconds   # noqa: E731
    has_col = column_exists(db, "duplicate_of")
    apply = bool(a.apply and has_col)
    if a.apply and not has_col:
        _p("::warning::events.duplicate_of is missing (../mapsee 0236 not applied) or unreadable: "
           "reporting only")
    stamp = now.replace(microsecond=0).isoformat()
    _p(f"Mode: {'APPLY (hidden_at=' + stamp + ')' if apply else 'REPORT ONLY'}; "
       f"duplicate_of {'present' if has_col else 'missing' if has_col is False else 'unknown'}; "
       f"{a.days} days ahead, {a.lookback} back, budget {a.max_seconds}s")

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    titles_by_link: Dict[str, Set[str]] = defaultdict(set)
    allday_by_day: Dict[int, List[Dict[str, Any]]] = {}
    backlog: List[str] = []
    owed_windows: List[str] = []
    refused: List[str] = []
    owed = 0
    totals: Counter = Counter()
    by_arm: Counter = Counter()
    near: Counter = Counter()
    hidden_out: List[Dict[str, Any]] = []
    left_out: List[Dict[str, Any]] = []
    windows: List[Dict[str, Any]] = []
    examples: Dict[str, List[str]] = defaultdict(list)

    for d in window_order(a.days, a.lookback):
        lo = midnight + timedelta(days=d)
        hi = lo + timedelta(days=1)
        day = lo.date().isoformat()
        w: Dict[str, Any] = {"day": day, "offset": d}
        windows.append(w)
        if budget():
            backlog.append(day)
            w["status"] = "backlog"
            continue
        t0 = _clock()
        rows, missed, pages = list_window(db, lo, hi, now, budget)
        w.update(rows=len(rows), pages=pages)
        for a_, b_, why in missed:
            part = f"{day} {a_:%H:%M}-{'24:00' if b_ >= hi else format(b_, '%H:%M')}Z ({why})"
            (backlog if why == "budget" else owed_windows).append(part)
            w.setdefault("missed", []).append(part)
            _p(f"[{day} {d:+d}] not listed: {part}")
        if not rows:
            w["status"] = "nothing listed" if missed else "ok"
            continue
        totals["rows"] += len(rows)
        allday_by_day[d] = [r for r in rows if r.get("starts_at") and is_all_day(r)]
        carry = allday_by_day.get(d - 1, [])
        pairs = candidate_pairs(rows, carry)
        by_id = {c["id"]: c for c in carry}
        by_id.update({r["id"]: r for r in rows})
        need = {i for p in pairs for i in p if "link" not in by_id[i]}
        links = read_links(db, need) if need else {}
        if links is None:
            owed_windows.append(day)
            w["status"] = "link read failed"
            continue
        for i in need:
            r = by_id[i]
            r["link"] = links.get(i)
            if r["link"]:
                titles_by_link[r["link"]].add(normalize_text(r.get("title")))
        edges: Dict[Tuple[str, str], str] = {}
        w_near: Counter = Counter()
        for x, y in pairs:
            arm, why = _decide(by_id[x], by_id[y], titles_by_link)
            if arm:
                edges[(x, y)] = arm
            else:
                w_near[why] += 1
        members = {i for e in edges for i in e}
        rsvp = read_rsvp_ids(db, members) if members else set()
        if rsvp is None:
            owed_windows.append(day)
            w["status"] = "RSVP read failed"
            continue
        try:
            plans = plan(by_id, edges, rsvp, now, a.max_cluster)
        except ValueError as exc:
            refused.append(f"{day}: {exc}")
            w["status"] = f"refused: {exc}"
            _p(f"[{day} {d:+d}] REFUSED: {exc}")
            continue
        n = sum(len(p["hide"]) for p in plans)
        ceiling = max(SHARE_FLOOR, a.max_share * len(rows))
        if n > ceiling:
            msg = f"{n} hides in {len(rows)} rows (limit {ceiling:.0f})"
            refused.append(f"{day}: {msg}")
            w["status"] = f"refused: {msg}"
            _p(f"[{day} {d:+d}] REFUSED: {msg}")
            continue
        arms = Counter(arm for p in plans for _, arm in p["hide"])
        left = sum(len(p["left"]) for p in plans)
        w.update(status="partial" if missed else "ok", candidates=len(pairs), links_read=len(need), matched=dict(Counter(edges.values())),
                 clusters=len(plans), hide=n, hide_by_arm=dict(arms), left=left, near_misses=dict(w_near))
        totals.update(candidates=len(pairs), links_read=len(need), matched=len(edges), clusters=len(plans),
                      hide=n, left=left)
        by_arm.update(arms)
        near.update(w_near)
        for p in plans:
            s = by_id[p["survivor"]]
            for h, arm in p["hide"]:
                r = by_id[h]
                hidden_out.append({"id": h, "title": r.get("title"), "starts_at": r.get("starts_at"),
                                   "link": r.get("link"), "created_at": r.get("created_at"), "arm": arm,
                                   "survivor": s["id"], "survivor_title": s.get("title"),
                                   "survivor_starts_at": s.get("starts_at"), "survivor_link": s.get("link"),
                                   "metres": round(metres(r, s), 1), "day": day})
                if len(examples[arm]) < a.show:
                    examples[arm].append(f"hide {r.get('title')!r} ({h[:8]}) -> keep {s.get('title')!r} "
                                         f"({s['id'][:8]}) {str(s.get('starts_at') or '')[:16]}")
            for i in p["left"]:
                left_out.append({"id": i, "title": by_id[i].get("title"), "survivor": s["id"],
                                 "why": ("claimed" if by_id[i].get("claimed_at") else
                                         "people on it" if engaged(by_id[i], rsvp, now) else
                                         "no direct match to the survivor"), "day": day})
        hidden_now = {h for p in plans for h, _ in p["hide"]}
        allday_by_day[d] = [r for r in allday_by_day[d] if r["id"] not in hidden_now]
        line = (f"[{day} {d:+d}] {len(rows)} rows" + (" (PARTIAL)" if missed else "")
                + f", {pages} page(s), {_clock() - t0:.1f}s | "
                f"{len(pairs)} candidates, {len(need)} links read | "
                + (", ".join(f"{k} {v}" for k, v in sorted(arms.items())) or "nothing")
                + f" to hide in {len(plans)} cluster(s)" + (f", {left} left" if left else ""))
        if apply and n:
            res = write(db, plans, stamp, budget)
            owed += res["owed"]
            w["written"] = res
            totals.update(hidden=res["hidden"], skipped_at_write=res["skipped"] + res["survivor_gone"]
                          + res["late_rsvp"])
            line += (f" | hid {res['hidden']}" + (f", skipped {res['skipped']}" if res["skipped"] else "")
                     + (f", survivor gone {res['survivor_gone']}" if res["survivor_gone"] else "")
                     + (f", late RSVP {res['late_rsvp']}" if res["late_rsvp"] else "")
                     + (f", OWED {res['owed']}" if res["owed"] else ""))
        _p(line)

    _p("")
    _p(f"Rows listed: {totals['rows']} | candidates {totals['candidates']} | links read {totals['links_read']} "
       f"| matched pairs {totals['matched']} | clusters {totals['clusters']}")
    _p(f"Copies {'hidden' if apply else 'to hide'}: "
       + (f"{totals['hidden']} of {totals['hide']} planned" if apply else f"{totals['hide']}")
       + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(by_arm.items())) + ")" if by_arm else "")
       + (f"; {totals['left']} left for a person" if totals["left"] else ""))
    if totals["rows"]:
        _p(f"  that is {totals['hide'] / totals['rows']:.1%} of the rows listed")
    for arm in ARMS:
        for e in examples.get(arm, []):
            _p(f"  [{arm}] {e}")
    if near:
        _p("Near misses (candidates no rule took): "
           + ", ".join(f"{k} {v}" for k, v in near.most_common()))
    orphans = orphaned_copies(db, now) if has_col else None
    if orphans:
        _p(f"Copies whose survivor another writer has hidden since: {orphans} (they stay hidden)")
    for day in backlog:
        _p(f"BACKLOG: {day} not reached inside {a.max_seconds}s")
    for day in owed_windows:
        _p(f"OWED: {day} could not be read; it runs again tomorrow")
    for msg in refused:
        _p(f"REFUSED: {msg}")
    if owed:
        _p(f"OWED: {owed} copies not hidden this run (write failures or the budget)")
    code = 1 if (backlog or owed_windows or refused or owed or (a.apply and not has_col)) else 0
    _p(f"Requests: {dict(db.calls)} | {_clock() - started:.0f}s | exit {code}")
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"now": now.isoformat(), "stamp": stamp if apply else None,
                       "mode": "apply" if apply else "report", "duplicate_of_column": has_col,
                       "args": vars(a), "totals": dict(totals), "hidden_by_arm": dict(by_arm),
                       "near_misses": dict(near), "orphaned_copies": orphans, "backlog": backlog,
                       "owed_windows": owed_windows, "refused": refused, "owed": owed, "exit": code,
                       "requests": dict(db.calls), "windows": windows, "hidden": hidden_out,
                       "left": left_out}, fh, indent=1, default=str)
        _p(f"Report: {a.report}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
