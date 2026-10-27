#!/usr/bin/env python3
"""
mapsee_ingest_meetup.py — import public Meetup events near a point via Meetup's
GraphQL API into the Mapsee store, for the community / learning layers.

Meetup's original /gql GraphQL endpoint was retired with the February 2025 API
release (it now returns an HTML 404 page); the current endpoint is /gql-ext and
the old keywordSearch query was split into eventSearch + groupSearch. Verified
against live schema introspection (July 2026):

  eventSearch(filter: EventSearchFilter!, first: Int)
    EventSearchFilter: query String! (REQUIRED — an empty keyword returns
    nothing, so we sweep a set of broad keywords per metro and dedupe via the
    store's fingerprint upsert), lat Float!, lon Float!, radius Float,
    startDateRange/endDateRange DateTime ("...T00:00:00Z" accepted)
  Event: venue.lon (was lng), featuredEventPhoto (was image)

An OAuth bearer token (MEETUP_OAUTH_TOKEN, see meetup_token.py) is sent when
present but eventSearch also answers unauthenticated — so a missing token logs
a note and proceeds instead of skipping.

    python mapsee_ingest_meetup.py --latlong 47.6062,-122.3321 --radius 25 --store mapsee_events.json

NOTE: Meetup's GraphQL schema evolves; if a field is renamed again, the printed
GraphQL errors from a run name the offending field — adjust QUERY / to_event,
the rest of the pipeline is unaffected.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import (NormalizedEvent, EventStore, make_fingerprint,
                           looks_online_only, venue_is_only_a_plus_code,
                           venue_is_only_a_city, notice_reason, strip_notice)
from mapsee_admission import normalize_admission_facts, admission_description

GQL = "https://api.meetup.com/gql-ext"

# eventSearch REQUIRES a keyword; these broad ones together approximate the old
# "everything nearby" sweep. Dupes across keywords collapse in store.upsert.
# Each entry is keyword:mapsee-category — the keyword that FOUND an event is a
# decent guess at its layer. Within a run the first (metro, keyword) pair to see
# it wins; ACROSS runs mapsee_meetup_category_cache holds that first guess until
# the event passes, because the store is rebuilt every run and the first pair
# to see an event moves (top-200 drift, the international leg's daily country
# rotation): category blamed 4.1k-4.8k UPDATEs per leg per day, 10-22..10-26.
# Edit this list and the held guesses start over, on purpose (the cache is
# versioned by it). The sync's keyword pass can still promote volunteer. Bare
# keywords default to community.
# One broad keyword per Mapsee layer — trimmed from 12 to 8 by dropping the
# redundant ones (cooking/popup fold into food, games into social, learning into
# tech), which cuts ~33% of the per-metro Meetup requests with ~no coverage loss.
# eventSearch REQUIRES a keyword, so coverage is the union of these sweeps: one
# GraphQL call per keyword per metro (~0.3s apart). The list was trimmed to 8
# when Actions minutes were scarce; the public repo removed that constraint, so
# FOOD and MARKET are now swept properly - they feed the oneday.cafe lens, which
# measured just 4.9% of the pool while a single "food" keyword had to carry it.
# Duplicates across keywords collapse on the store fingerprint, so overlap is
# free. Terms are English on purpose: Meetup's international listings are
# heavily English-titled, and per-language variants would multiply calls for
# thin marginal yield.
#
# The sweep is organised BY LENS, because each lens filters the shared pool to
# its own categories and is only as good as the supply behind those categories:
#   mapsee.me    everything
#   bar.ventures party + music + food
#   oneday.cafe  food + market
#   plansie.com  community + kids + outdoors + arts + learning
DEFAULT_KEYWORDS = ("social:community,music:music,tech:learning,"
                    "outdoors:outdoors,art:arts,sports:sports,volunteer:volunteer,"
                    # food + drink (oneday.cafe)
                    "food:food,dinner:food,cooking:food,tasting:food,brunch:food,"
                    "potluck:food,supper club:food,food truck:food,"
                    # markets (oneday.cafe)
                    "farmers market:market,night market:market,"
                    # going out / meeting people (bar.ventures). "20s and 30s"
                    # and "young professionals" are how Meetup's own social
                    # groups actually self-describe, so they out-yield the
                    # generic terms by a wide margin.
                    "20s and 30s:party,young professionals:party,nightlife:party,"
                    "happy hour:party,trivia:party,karaoke:party,board games:party,"
                    "dance:party,singles:party,"
                    # neighbourhood life (plansie.com)
                    "hiking:outdoors,book club:learning,language exchange:learning,"
                    "craft:arts,photography:arts,museum:arts,"
                    "family:kids,storytime:kids,workshop:learning,"
                    # moving your body (wegosie.com). Missing entirely until now,
                    # which is why the movement lens had almost no Meetup supply:
                    # the only thing that surfaced fitness events was "dance",
                    # and that filed them under party.
                    "fitness:fitness,workout:fitness,yoga:fitness,pilates:fitness,"
                    "bootcamp:fitness,climbing:fitness,martial arts:fitness,"
                    "swimming:fitness,"
                    "running:running,run club:running,"
                    "cycling:fitness,bike ride:fitness")

QUERY = """
query($query: String!, $lat: Float!, $lon: Float!, $radius: Float,
      $start: DateTime, $end: DateTime, $first: Int!) {
  eventSearch(filter: {query: $query, lat: $lat, lon: $lon, radius: $radius,
                       startDateRange: $start, endDateRange: $end}, first: $first) {
    edges { node {
      id
      title
      eventUrl
      dateTime
      endTime
      status
      description
      venue { name lat lon address city state postalCode country }
      group { name urlname }
      featuredEventPhoto { standardUrl highResUrl }
    } }
  }
}
"""


# Event.status is a NON_NULL EventStatus enum — verified by live introspection
# 2026-09-13, values: ACTIVE, AUTOSCHED, AUTOSCHED_CANCELLED, AUTOSCHED_DRAFT,
# AUTOSCHED_FINISHED, BLOCKED, CANCELLED, CANCELLED_PERM, DRAFT, PAST, PENDING,
# PROPOSED, TEMPLATE. Meetup was answering eventSearch with cancelled events and
# we were importing every one: measured the same day over 502 Meetup-sourced
# rows sampled from mapsee.me's live sitemaps and re-probed at their own source
# URLs, 47 said EventCancelled and 18 more had been deleted outright (HTTP 404)
# — 12.9% of the Meetup events on the map were not happening.
#
# A DENYLIST, not an allowlist, matching mapsee_ingest_dice_venue's DEAD_STATUSES.
# Meetup has renamed fields on us twice already; if it adds a new LIVE state an
# allowlist would silently drop the whole feed, while a denylist lets it in and
# costs nothing. Everything listed here is a state in which the event is not a
# thing the public can turn up to.
DEAD_STATUSES = {"cancelled", "canceled", "cancelled_perm", "autosched_cancelled",
                 "draft", "autosched_draft", "template", "proposed", "blocked", "past"}
# The subset that is the organiser CALLING THE EVENT OFF, which is a tombstone
# (EventStore.cancel) and not just a refusal: the row we stored while it was
# ACTIVE is still on the map otherwise, and that is the 12.9% above. The rest of
# DEAD_STATUSES stay plain refusals — DRAFT/TEMPLATE/PROPOSED were never public,
# PAST happened, and BLOCKED is Meetup's moderation rather than the organiser's
# word (mapsee_prune_cancelled still re-probes that row's own page). A tombstone
# hides a row until the same event is written live again, so it is kept to the
# states whose own name says the event is off.
# The yield is small and that is expected: live 2026-10-05 over Seattle,
# eventSearch answered 9,700 hits (1,022 rows) and 4 were CANCELLED — 2 events,
# one of them a tombstone, the other with no venue (it never had a row). A
# search is not a ledger; the post-hoc sweep still does most of this work.
CANCELLED_STATUSES = {"cancelled", "canceled", "cancelled_perm", "autosched_cancelled"}

_MEETUP_GROUP_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MEETUP_GROUP_NAME_PLACEHOLDERS = {"", "group", "meetup", "meetup group", "unknown",
                                  "unnamed", "no name", "n/a", "none", "null"}


def to_event(ev: Dict[str, Any], category: str = "community",
             centre: Optional[Tuple[float, float]] = None) -> Optional[NormalizedEvent]:
    """The live row, or None — a cancelled event is None here too; to_record
    is what tells the two apart."""
    nev, cancelled = to_record(ev, category, centre)
    return None if cancelled else nev


_HERE = os.path.dirname(os.path.abspath(__file__))
_LATLON_RX = re.compile(r"^-?[0-9.]+,-?[0-9.]+$")


def _point(text: str) -> Optional[Tuple[float, float]]:
    s = re.sub(r"\s", "", text)
    if not _LATLON_RX.match(s):
        return None
    try:
        la, lo = s.split(",")
        return float(la), float(lo)
    except ValueError:
        return None


@functools.lru_cache(maxsize=None)
def sweep_centres(us_path: str = os.path.join(_HERE, "metros_us.txt"),
                  global_path: str = os.path.join(_HERE, "metros_global.json")
                  ) -> Tuple[Tuple[float, float], ...]:
    """Every point a Meetup leg sweeps, read as the workflow reads metros_us.txt
    (comments and spaces stripped, `lat,lon` lines) and as mapsee_sweep_global
    reads metros_global.json. The US and international legs write separate
    stores, but each leg's circles reach the other's cities (Seattle's 100 mi
    holds Victoria, Detroit's holds Windsor, Tijuana is 15.6 mi from San Diego)
    and every centre within one leg overlaps its neighbours, so a city point
    refused by its own metro's sweep must be refused by any sweep that meets it
    (see venue_is_only_a_city). A missing or broken file, or
    line, adds nothing; it never stops an import."""
    lines = []
    try:
        with open(us_path, encoding="utf-8") as fh:
            lines += [line.split("#", 1)[0] for line in fh]
    except (OSError, ValueError):
        pass
    try:
        with open(global_path, encoding="utf-8") as fh:
            countries = json.load(fh).get("countries") or []
        lines += [str((m or {}).get("latlong") or "")
                  for c in countries if isinstance(c, dict) for m in (c.get("metros") or [])]
    except (OSError, ValueError, AttributeError):
        pass
    return tuple(p for p in map(_point, lines) if p)


def only_a_city(ev: Dict[str, Any], centre: Optional[Tuple[float, float]] = None) -> bool:
    """The venue is the city or "TBA", pinned on a city's own point: the sweep's
    `centre` or any other centre a Meetup leg sweeps (see venue_is_only_a_city)."""
    v = ev.get("venue") or {}
    centres = ((centre,) if centre else ()) + sweep_centres()
    return venue_is_only_a_city(v.get("name"), v.get("city"), v.get("lat"), v.get("lon"), centres)


def _called_off(ev: Dict[str, Any]) -> Optional[str]:
    """Why Meetup says the event is off ("status:CANCELLED"), or None."""
    status = str(ev.get("status") or "").strip().lower()
    return f"status:{status.upper()}" if status in CANCELLED_STATUSES else None


def to_record(ev: Dict[str, Any], category: str = "community",
              centre: Optional[Tuple[float, float]] = None
              ) -> Tuple[Optional[NormalizedEvent], Optional[str]]:
    """(event, why it is cancelled). With a reason, the event is the record the
    live row WOULD have been — every refusal below still applies, so the
    fingerprint is the one a stored row can actually carry — and it goes to
    EventStore.cancel, never to upsert. `centre` is the point the sweep asked
    about; it is checked beside every other sweep centre (sweep_centres), and
    away from all of them a bare-city venue cannot be told from a real pin."""
    title = (ev.get("title") or "").strip()
    start = ev.get("dateTime")
    if not title or not start:
        return None, None
    status = str(ev.get("status") or "").strip().lower()
    cancelled = _called_off(ev)
    if status in DEAD_STATUSES and not cancelled:
        return None, None                                  # draft/blocked/past — not on
    if cancelled and notice_reason(title):
        # Called off AND retitled "CANCELLED: Board Game Night": the row we
        # stored was written under the old title, so the tombstone must be too.
        # A closure title names no event (strip_notice gives None) — refused.
        title = strip_notice(title) or ""
        if not title:
            return None, None
    v = ev.get("venue") or {}
    lat, lon = v.get("lat"), v.get("lon")
    if lat is None or lon is None:
        return None, None                                  # online / no venue -> can't map it
    group_data = ev.get("group") or {}
    group = group_data.get("name")
    desc = (ev.get("description") or "").strip() or None
    if desc:
        desc = " ".join(desc.split())
    # THE LINE ABOVE HAS A HOLE AN ORGANISER CAN WALK THROUGH. "no coordinates =
    # online" is only true while the venue box is empty; Meetup lets a Zoom event
    # be filed as eventType PHYSICAL with anything at all typed into it, and then
    # it has coordinates and sails past. The reported row did exactly that —
    # "Seattle Gay Virtual Speed Dating on Zoom … Join from home", venue and
    # address both the Plus Code "JP7Q+33 Mercer Island", city "Seattle" — so the
    # map showed a street corner for a video call, and the sync's Census pass
    # then moved the pin several km by geocoding the fake address.
    #
    # Measured 2026-09-13 over 2,000 live event pages from mapsee.me's sitemaps:
    # 40 rows say in their own words that they are on Zoom, 35 of them online
    # only, and ALL 35 came through this adapter — one commercial speed-dating
    # network reposting the same template city by city, into unrelated groups
    # (a taekwondo group, a calligraphy club, a vegan cookery crew). The other 5
    # are a sangha, a church and a meditation group that really do run a room as
    # well as a stream, and looks_online_only leaves every one of them alone.
    if looks_online_only(title, desc):
        return None, None                                  # a Zoom call is not somewhere to go
    if venue_is_only_a_plus_code(v.get("name"), v.get("address")):
        return None, None                                  # a dropped pin is not a venue
    # A LIVE row only: this refusal is newer than the rows on the city point we
    # already stored, and an upsert cannot delete, so a cancellation of one
    # still has to reach its tombstone (as SeatGeek's undecided games do).
    if only_a_city(ev, centre) and not cancelled:
        return None, None                                  # the city's own point is not a venue
    # The native record's complete prose can veto conditional free entry even
    # when no ticket prices are published. Retain that before sync truncation.
    admission = normalize_admission_facts(None, context=f"{title} {desc or ''}")
    details = dict(admission or {})
    slug = group_data.get("urlname")
    if (isinstance(group, str) and 0 < len(group.strip()) <= 200
            and group.strip().casefold() not in _MEETUP_GROUP_NAME_PLACEHOLDERS
            and isinstance(slug, str) and len(slug.strip()) <= 200
            and _MEETUP_GROUP_SLUG.fullmatch(slug.strip())):
        details["organizer"] = {"type": "Organization", "name": group.strip(),
                                "url": f"https://www.meetup.com/{slug.strip()}/"}
    nev = NormalizedEvent(
        source="meetup",
        source_id=str(ev.get("id")),
        name=title,
        description=admission_description(desc, admission),
        start_local=start,
        venue_name=v.get("name") or group,
        latitude=float(lat), longitude=float(lon),
        address=v.get("address"), city=v.get("city"), region=v.get("state"),
        country=v.get("country"), postal_code=v.get("postalCode"),
        category=category,                                 # from the keyword that found it
        promoter=group,
        source_details=details or None,
        admission_checked=True,
        # standardUrl/highResUrl are complete image URLs; baseUrl is just a
        # meetupstatic path PREFIX (a broken link if used directly)
        poster_image_url=(ev.get("featuredEventPhoto") or {}).get("standardUrl")
                      or (ev.get("featuredEventPhoto") or {}).get("highResUrl"),
        ticket_url=ev.get("eventUrl"),
    )
    nev.fingerprint = make_fingerprint(title, str(start)[:10], v.get("name") or group)
    return nev, cancelled


def ingest(store: EventStore, session, token: str, lat: str, lon: str, radius: int,
           first: int, keywords: list, within_days: int) -> int:
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=within_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    kept = seen = 0
    centre = (float(lat), float(lon))
    # DISTINCT fingerprints: the keyword sweep overlaps (9,700 hits for 1,022
    # rows over Seattle on 2026-10-05) and one cancelled event came back 3 times.
    cancelled: set = set()
    unplaced: set = set()                                  # distinct ids, for the same reason
    for kw, category in keywords:
        r = session.post(
            GQL,
            headers=headers,
            json={"query": QUERY, "variables": {
                "query": kw, "lat": float(lat), "lon": float(lon),
                "radius": float(radius), "start": start, "end": end, "first": int(first)}},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[meetup] {lat},{lon} '{kw}' HTTP {r.status_code}: {r.text[:200]}")
            continue
        data = r.json()
        if data.get("errors"):
            print(f"[meetup] '{kw}' GraphQL errors: {str(data['errors'])[:200]}")
        edges = (((data.get("data") or {}).get("eventSearch") or {}).get("edges")) or []
        seen += len(edges)
        for e in edges:
            node = e.get("node") or {}
            if only_a_city(node, centre) and not _called_off(node):
                unplaced.add(str(node.get("id")))
                continue
            nev, why = to_record(node, category, centre)
            if nev is None:
                continue
            if why:
                # Never upsert AND cancel: the tombstone is the whole answer.
                if store.cancel(nev, why) == "cancelled":
                    cancelled.add(nev.fingerprint)
            else:
                store.upsert(nev)
                kept += 1
        time.sleep(0.3)                                    # be polite across the keyword sweep
    print(f"[meetup] {lat},{lon}: kept {kept} of {seen} across {len(keywords)} keywords"
          f"; {len(cancelled)} cancelled by the organiser"
          f"; {len(unplaced)} with only the city for a venue")
    return kept


def parse_keywords(text: str) -> list:
    """`keyword[:category],...` -> [(keyword, category)], a bare keyword filed
    under community. The ONE reading of --keywords: mapsee_meetup_category_cache
    versions its held categories by this list, so the two must never differ."""
    return [(k.split(":")[0].strip(), (k.split(":")[1].strip() if ":" in k else "community"))
            for k in text.split(",") if k.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import public Meetup events near a point into the Mapsee store.")
    ap.add_argument("--latlong", required=True, help="lat,lon  e.g. 47.6062,-122.3321")
    ap.add_argument("--radius", type=int, default=25, help="miles")
    ap.add_argument("--first", type=int, default=200)
    ap.add_argument("--within-days", type=int, default=90)
    ap.add_argument("--keywords", default=DEFAULT_KEYWORDS,
                    help="comma-separated keyword[:category] sweep (eventSearch requires a keyword)")
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--token", default=os.environ.get("MEETUP_OAUTH_TOKEN"))
    a = ap.parse_args(argv)
    if not a.token:
        print("[meetup] no MEETUP_OAUTH_TOKEN — proceeding unauthenticated (eventSearch allows it)")
    try:
        lat, lon = a.latlong.split(",")
    except ValueError:
        sys.exit("--latlong must look like  47.6062,-122.3321")
    keywords = parse_keywords(a.keywords)
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    try:
        ingest(store, session, a.token or "", lat.strip(), lon.strip(), a.radius,
               a.first, keywords, a.within_days)
    except Exception as exc:
        print(f"[meetup] {a.latlong} FAILED: {exc}")
    store.save()
    print(f"[meetup] store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
