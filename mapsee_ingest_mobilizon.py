#!/usr/bin/env python3
"""
mapsee_ingest_mobilizon.py - import events from Mobilizon instances.

    python mapsee_ingest_mobilizon.py --config mobilizon_sources.json --store feeds_events.json

Mobilizon (https://joinmobilizon.org) is federated, AGPL event software - the
fediverse's answer to Facebook Events. Instances are run by collectives, unions,
municipalities and activist networks, mostly across France, Germany, Switzerland,
Belgium and Italy. Public GraphQL, no key, no account.

    POST {base}/api   { "query": "...", "variables": {...} }

FOUR THINGS THAT WILL CATCH YOU OUT, all found the hard way:

1. `geom` IS "lon;lat", LONGITUDE FIRST, semicolon-delimited. Lausanne comes back
   as "6.6327025;46.5218269". Read it the other way round and every event in
   Europe lands in the Gulf of Guinea or Somalia.
2. Use `searchEvents(beginsOn:)`, NOT `events`. The latter caps out and happily
   returns records from 2006, so a naive sweep spends its budget on a decade of
   dead listings.
3. `elements` is typed `EventSearchResult`, a union. Every field beyond the
   shared ones must sit inside `... on Event { }` or the whole query errors on
   every instance.
4. Ask for `sortBy: START_TIME_ASC`. The default answers latest start first,
   so on a big instance the page budget was spent 4-24 months out and 0 of the
   rows kept from the seven biggest fell within 90 days (2026-11-08). See SORT.

The category enum is the richest of any source here - 30 values that map almost
one-to-one onto mapsee's own vocabulary, so these arrive correctly filed rather
than guessed at from keywords.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import (NormalizedEvent, EventStore, make_fingerprint, norm_categories,
                           notice_reason, strip_notice)
from mapsee_spam import MAX_SPAN_DAYS, implausible_end

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")

# A YEAR AHEAD, AND NO FURTHER, unless a site's `within_days` says otherwise:
# mapsee_ingest_ics's HORIZON_DAYS. The 2026-10-26 civic store held 522 of its
# 4,938 Mobilizon rows starting more than 365 days out (538 of 4,906 on 10-21),
# 441 of them one association's weekly "Offener Techniktreff", every week to 2038.
HORIZON_DAYS = 365

# ...AND NOT ALREADY OVER. searchEvents(beginsOn: now) still answers events that
# began long ago, and 240 of them sat in that store with no end (238 on 10-21,
# the same set), median 796 days old and the oldest 6,835, casino and IPTV ads
# among them. The sync gives a row with no end its start plus a typical length,
# already past; the janitor deletes it as a week over, and the next --only-new
# sync inserts it again. Begun more than this long ago with no end, or with an
# end that has passed, it can never be shown, so it is counted and not kept.
# An event still running (an endsOn ahead) is kept, however long ago it began,
# UNLESS THAT END IS ONE THE STORE DROPS. Those 240 rows had no end IN THE
# STORE; the server sends an end. As first written (10-27) this rule read the
# raw endsOn, and it never skipped a row: 0 of 1,320 rows on the 66 first
# pages of 2026-11-08 had no endsOn or a past one, and the full run that day
# logged 0 "already over" on all 66 sites, yet its store held 269 rows begun
# more than a day before with no end (32 with coordinates; median 482 days
# old, the oldest 6,848). Each came with an endsOn ahead more than
# MAX_SPAN_DAYS after its start, which EventStore.upsert drops
# (mapsee_spam.implausible_end), so it reached the sync endless and churned
# as above. Among the 32 placed: exam dumps, a casino, "AK74 RIFLES FOR SALE"
# and loan adverts with an e-mail in the title. So an end the store will drop
# counts as no end here (end_dropped); a running event whose end lies within
# MAX_SPAN_DAYS of its start is kept as before.
STALE_AFTER = timedelta(days=1)


def _instant(value: Optional[str]) -> Optional[datetime]:
    """An ISO-8601 instant as an aware datetime, or None."""
    try:
        dt = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def end_dropped(ev: Dict[str, Any]) -> bool:
    """True when the event's endsOn is one EventStore.upsert will drop: more
    than MAX_SPAN_DAYS after its beginsOn (mapsee_spam.implausible_end)."""
    return implausible_end(ev.get("beginsOn"), ev.get("endsOn")) is not None


def stale_or_far(ev: Dict[str, Any], now: datetime, horizon: datetime) -> Optional[str]:
    """'over' for an event that began more than STALE_AFTER ago and has no end
    ahead of `now` that the store will keep, 'far' for one that begins after
    `horizon`, else None."""
    begins = _instant(ev.get("beginsOn"))
    if begins is None:
        return None                                    # to_event refuses it anyway
    if begins > horizon:
        return "far"
    ends = _instant(ev.get("endsOn"))
    if ends is not None and end_dropped(ev):
        ends = None                                    # see STALE_AFTER: the store drops it
    if begins < now - STALE_AFTER and (ends is None or ends < now):
        return "over"
    return None

# THE ORDER IS ASKED FOR, BECAUSE THE DEFAULT IS THE WRONG END. Without sortBy,
# searchEvents answers latest start first, so an instance with more upcoming
# events than limit x max_pages (100 x 5) gave us only its far future and never
# the coming weeks: ticketzon.it's pages 1-5 spanned 2027-05-07..2028-11-30 and
# mobilisons.ch's 2027-03-04..2030-09-15 (2026-11-07), and di.day's 500 rows on
# 11-06 were all beyond the 365-day horizon, 0 kept, of 1,235. START_TIME_ASC
# starts the walk at today. Asked of all 66 configured instances on 2026-11-08
# (limit 20, page 1): 65 accepted it and every one of their pages came back
# ascending; one (HTU Events) answered `Unknown argument "sortBy" on field
# "searchEvents"`, an older Mobilizon, and reads the old way (ingest_site).
SORT = "sortBy:START_TIME_ASC"

_QUERY = """
query($b:DateTime,$l:Int,$p:Int){
  searchEvents(beginsOn:$b, limit:$l, page:$p@SORT@){
    total
    elements{
      ... on Event {
        uuid title description beginsOn endsOn url category onlineAddress status
        physicalAddress{ description street locality postalCode region country geom }
      }
    }
  }
}"""
QUERY = _QUERY.replace("@SORT@", ", " + SORT)
QUERY_UNSORTED = _QUERY.replace("@SORT@", "")

# What an instance that predates the argument says. Absinthe names the argument
# ("Unknown argument \"sortBy\""); one that knows the argument but not the value
# names the value or its enum type. Any other error is not about the order and
# ends the site's walk exactly as before.
_SORT_REFUSED = re.compile(r"sortBy|START_TIME_ASC|SearchEventSortOptions")


def sort_refused(errors: Any) -> bool:
    """True when a GraphQL `errors` answer is about the sortBy argument."""
    if isinstance(errors, list):
        text = " ".join(str(e.get("message") if isinstance(e, dict) else e) for e in errors)
    else:
        text = str(errors or "")
    return bool(_SORT_REFUSED.search(text))


# ASCENDING ORDER PUTS WHAT BEGAN LONG AGO FIRST, so a page can be spent on
# rows STALE_AFTER refuses. Measured 2026-11-08 on the 66 first pages of 20:
# 0 rows with no endsOn or a past one, and 312 begun with an endsOn ahead -
# mobilizon.fr, agenda.collectifs.net, libretic and adminForge each 20 of 20,
# event.portes-imaginaire.org's first beginning in the year 25. Many of those
# ends are ones the store drops, which makes the row "over" (STALE_AFTER): at
# limit 100, page 1 of event.portes-imaginaire.org held 100 begun rows of
# 100, 71 lasting more than 365 days (the 400-day count was not taken), loan and "ritual"
# adverts among them. The full run of 2026-11-08 02:54 UTC refused 402 rows
# as over on 24 of 66 sites (335 distinct events, 70 placed; span median
# 1,009 days), every one for an end the store drops, none for a missing end;
# its store held 0 rows begun over a day before with no end, against 269 at
# 01:04 under the raw-endsOn rule. One page held nothing else (Social Org
# Mx, a one-row instance). A page that kept nothing, had nothing beyond
# the horizon and held at least one row already over is not counted against
# max_pages, so those rows cannot spend the budget meant for the coming weeks
# - at most this many such pages a site, so a server that answers nothing
# else cannot walk us forever. Both counts are printed, and so is the running
# head the order brought within reach: rows begun over a day ago whose end,
# within MAX_SPAN_DAYS of the start, is still ahead, kept as before.
OVER_PAGES_MAX = 5

# Mobilizon's 30-value EventCategory -> mapsee's 15 keys. The ones with no honest
# home (BUSINESS, NETWORKING, PETS, FASHION_BEAUTY, PHOTOGRAPHY, SPIRITUALITY,
# AUTO_BOAT_AIR) fall through to the instance default rather than being forced
# into a lens where nobody looking for them would find them.
_CATEGORY = {
    "ARTS": "arts", "PERFORMING_VISUAL_ARTS": "arts", "FILM_MEDIA": "arts", "CRAFTS": "arts",
    "THEATRE": "theater", "COMEDY": "theater",
    "MUSIC": "music", "PARTY": "party",
    "FOOD_DRINK": "food",
    "SPORTS": "sports", "HEALTH": "fitness",
    "OUTDOORS_ADVENTURE": "outdoors",
    "LEARNING": "learning", "BOOK_CLUBS": "learning", "SCIENCE_TECH": "learning",
    "LANGUAGE_CULTURE": "learning", "FAMILY_EDUCATION": "kids",
    "CAUSES": "volunteer", "MOVEMENTS_POLITICS": "community", "COMMUNITY": "community",
    "MEETING": "community", "LGBTQ": "community",
    "GAMES": "party",
}


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_geom(geom: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """'lon;lat' -> (lat, lon). Longitude comes FIRST on the wire; see the header."""
    if not geom or ";" not in str(geom):
        return None, None
    lon_s, _, lat_s = str(geom).partition(";")
    try:
        lon, lat = float(lon_s), float(lat_s)
    except ValueError:
        return None, None
    # 0,0 is the null island the software writes for "no location", and anything
    # outside these ranges is a parse that went wrong rather than a real place.
    if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None
    return lat, lon


def to_event(ev: Dict[str, Any], site: Dict[str, Any], *,
             tombstone: bool = False) -> Optional[NormalizedEvent]:
    """The live row, or with tombstone=True the record of an event whose status
    is CANCELLED (for EventStore.cancel); None for the other kind.

    The query did not ask for `status` until 2026-10-05, so a CANCELLED event was
    imported as a live one. Asked of all 65 instances that answer (none errored
    on the field): 3,558 CONFIRMED, 29 TENTATIVE, 7 CANCELLED on the first page.
    TENTATIVE stays, as in the ics adapter: unconfirmed is not off."""
    name = _clean(ev.get("title"))
    begins = (ev.get("beginsOn") or "").strip()
    if not name or len(begins) < 10:
        return None
    if (str(ev.get("status") or "").upper() == "CANCELLED") != tombstone:
        return None
    if tombstone and notice_reason(name):
        name = strip_notice(name)
        if not name:
            return None
    pa = ev.get("physicalAddress") or {}
    lat, lon = parse_geom(pa.get("geom"))
    primary = _CATEGORY.get(str(ev.get("category") or "").upper(), site.get("category", "community"))
    extras = norm_categories(primary, [])
    street = _clean(pa.get("street"))
    nev = NormalizedEvent(
        source="mobilizon",
        source_id=str(ev.get("uuid") or ev.get("url") or make_fingerprint(name, begins[:10], pa.get("description"))),
        name=name,
        description=_clean(ev.get("description")),
        # beginsOn is ISO-8601 UTC, so it is an instant and belongs in start_utc.
        start_utc=begins.replace("+00:00", "Z"),
        end_utc=((ev.get("endsOn") or "").strip().replace("+00:00", "Z") or None),
        venue_name=_clean(pa.get("description")),
        latitude=lat, longitude=lon,
        address=street,
        city=_clean(pa.get("locality")) or site.get("default_city"),
        region=_clean(pa.get("region")) or site.get("default_region"),
        country=_clean(pa.get("country")) or site.get("default_country"),
        postal_code=_clean(pa.get("postalCode")),
        category=primary,
        categories=extras,
        ticket_url=ev.get("onlineAddress") or ev.get("url"),
    )
    nev.fingerprint = make_fingerprint(name, nev.start_utc[:10], nev.venue_name, nev.city)
    return nev


def _post(session, api: str, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(the decoded answer to one POST, None), or (None, why not)."""
    try:
        r = session.post(api, data=json.dumps(payload), timeout=45)
    except Exception as exc:
        return None, f"failed: {exc}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json(), None
    except Exception as exc:
        return None, f"bad JSON: {exc}"


def fetch_page(session, api: str, payload: Dict[str, Any],
               site: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    """One searchEvents page: (body, query, why).

    `body` is the decoded answer, or None and `why` says why not ("HTTP 403",
    "failed: ...", "bad JSON: ..."). When the answer to QUERY refuses sortBy
    (see SORT), the same page is asked once more without it, crawl_delay
    later, and `query` comes back as QUERY_UNSORTED: the caller sends that for
    the rest of the site. Nothing else is asked twice: a 403 or 429 is the
    operator's refusal, and comes back as `why` after one request.

    Public because mapsee_spam_audit reads Mobilizon through this module's
    QUERY, and an instance that refuses the order (HTU Events) answers that
    query with an error and no rows."""
    query = payload["query"]
    body, why = _post(session, api, payload)
    if body is not None and body.get("errors") and query == QUERY and sort_refused(body["errors"]):
        # An older Mobilizon (HTU Events, 2026-11-08): asked again ONCE, the old way.
        query = QUERY_UNSORTED
        print(f"[mobilizon] {site.get('name')}: sortBy refused ({str(body['errors'])[:120]});"
              f" reading it in the server's own order, latest first")
        delay = float(site.get("crawl_delay", 1))
        if delay:
            time.sleep(delay)
        body, why = _post(session, api, {**payload, "query": query})
    return body, query, why


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        print(f"[mobilizon] {site.get('name','?')}: no base_url"); return 0
    api = f"{base}/api"
    limit = int(site.get("limit", 100))
    max_pages = int(site.get("max_pages", 5))
    delay = float(site.get("crawl_delay", 1))
    now = datetime.now(timezone.utc)
    begins = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    horizon_days = int(site.get("within_days") or HORIZON_DAYS)
    horizon = now + timedelta(days=horizon_days)
    kept = cancelled = 0
    skipped = {"over": 0, "far": 0}
    dropped = 0                   # of the "over", those over only because the store drops their end (STALE_AFTER)
    running = 0                   # kept, begun over STALE_AFTER ago with an end ahead (OVER_PAGES_MAX)
    query = QUERY                 # until the instance refuses sortBy; then QUERY_UNSORTED, for the rest of it
    counted = free = page = 0     # pages against max_pages; only-over pages that were not (OVER_PAGES_MAX)
    over_pages: List[int] = []    # the pages that held nothing kept or far, and something over
    while counted < max_pages:
        page += 1
        payload = {"query": query, "variables": {"b": begins, "l": limit, "p": page}}
        body, query, why = fetch_page(session, api, payload, site)
        if body is None:
            print(f"[mobilizon] {site.get('name')} p{page} {why}"); break
        if body.get("errors"):
            print(f"[mobilizon] {site.get('name')} query errors: {str(body['errors'])[:180]}"); break
        node = (body.get("data") or {}).get("searchEvents") or {}
        rows = [e for e in (node.get("elements") or []) if e]
        if not rows:
            break
        here = {"kept": 0, "over": 0, "far": 0}
        for ev in rows:
            off = str(ev.get("status") or "").upper() == "CANCELLED"
            nev = to_event(ev, site, tombstone=off)
            if nev and off:
                # Never imported. Mobilizon is open registration, so EventStore
                # returns "untrusted" and records no tombstone: a stranger could
                # otherwise publish a cancelled copy of somebody else's event
                # (same title, day and place) and take the real one off the map.
                cancelled += 1
                store.cancel(nev, "status:CANCELLED")
            elif nev:
                why = stale_or_far(ev, now, horizon)
                if why:
                    skipped[why] += 1                  # see HORIZON_DAYS and STALE_AFTER
                    here[why] += 1
                    ends = _instant(ev.get("endsOn"))
                    if why == "over" and ends is not None and ends >= now and end_dropped(ev):
                        dropped += 1                   # an end ahead, but one the store drops
                    continue
                store.upsert(nev)
                kept += 1
                here["kept"] += 1
                began = _instant(ev.get("beginsOn"))
                if began is not None and began < now - STALE_AFTER:
                    running += 1                       # see OVER_PAGES_MAX: the head of the order
        only_over = here["over"] and not here["kept"] and not here["far"]
        if only_over:
            over_pages.append(page)
        if only_over and free < OVER_PAGES_MAX:
            free += 1                                  # see OVER_PAGES_MAX
        else:
            counted += 1
        if page * limit >= int(node.get("total") or 0):
            break
        if delay:
            time.sleep(delay)
    print(f"[mobilizon] {site.get('name')}: kept {kept} events"
          + (f"; {cancelled} CANCELLED not imported" if cancelled else "")
          + (f"; {skipped['over']} already over (begun, no end ahead"
             + (f"; {dropped} of them with an end ahead over {MAX_SPAN_DAYS} days after the start,"
                f" which the store drops" if dropped else "") + ")" if skipped["over"] else "")
          + (f"; {skipped['far']} beyond the {horizon_days}-day horizon" if skipped["far"] else "")
          + (f"; {running} kept begun over a day ago with an end ahead" if running else "")
          + (f"; {len(over_pages)} page(s) held nothing but rows already over"
             f" (p{','.join(map(str, over_pages))}), {free} of them not counted against"
             f" max_pages {max_pages}" if over_pages else "")
          + ("; unsorted (the instance refused sortBy)" if query == QUERY_UNSORTED else ""))
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Mobilizon instances into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in str(site.get("name", "")).lower():
            continue
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[mobilizon] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[mobilizon] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
