#!/usr/bin/env python3
"""
mapsee_ingest_bibliocommons.py - public library programmes from BiblioCommons.

    python mapsee_ingest_bibliocommons.py --config bibliocommons_sources.json --store feeds_events.json

BiblioCommons runs the catalogue and events site for a lot of large North
American public library systems. The events page is backed by a plain JSON
gateway with no key and no account:

    GET https://gateway.bibliocommons.com/v2/libraries/{slug}/events?limit=200&page=N

Measured 2026-09-03 across the six systems in the config: 28,314 upcoming
programmes. This is the civic long tail no ticketing API indexes - storytimes,
ESL and citizenship classes, job-search help, computer basics, teen makerspace,
tax help, book groups - and it is nearly all free to attend.

WHY IT IS WORTH A WHOLE ADAPTER RATHER THAN AN iCal ENTRY. The gateway hands
over a BRANCH with a surveyed coordinate for every event (see below), which is
the expensive half of every other civic feed we read: `ics_sources.json` entries
carry a `geocode_suffix` and pay ~1.1s of Photon per venue, and a library system
is 40-80 branches. It also carries audience bands, event types, cancellation and
a stable id, none of which survive a VEVENT.

FIVE THINGS MEASURED THE HARD WAY

1. `startDate` / `endDate` ARE ACCEPTED AND IGNORED. Asking for a three-day
   window returns the same 4,584 rows as asking for nothing. This is exactly the
   Mapas Culturais trap (`filter_honoured` there exists for it) and the failure
   mode is the same: a query that looks filtered, a 200, and the entire archive.
   So the horizon is applied HERE, on rows we have already read, and the page
   loop stops early once a page is entirely beyond it.

2. THE COORDINATE IS THE FACT AND THE ADDRESS IS DERIVED, so `coords_exact` is
   set. `mapLocation.centrePoint` is a surveyed branch point; the address is a
   `{number, street, city, state, zip}` object that is sometimes partial. Spot
   checked over 47 Chicago branches, the points are right on the buildings.
   Re-geocoding them would be the Renton restaurant all over again (CLAUDE.md:
   a hub's city name plus a street name moved a pin eleven miles).

3. THE TIMES ARE NAIVE LOCAL CLOCK TIMES - `"start": "2026-09-04T09:30"`, no
   offset, no Z. They go in `start_local` and `start_utc` stays None, and the
   sync resolves the zone from the coordinates. Stamping them UTC would serve a
   10:00 storytime at 03:00.

4. AN EVENT CAN BE OFF-SITE. `branchLocationId` points into `entities.locations`;
   `nonBranchLocationId` points into `entities.places`, which is a community
   centre, a park or a school with the same address+coordinate shape. Reading
   only the first silently drops the outreach programmes, which are the ones
   least likely to be findable anywhere else.

5. MOST EVENT IMAGES ARE STOCK. `featuredImageId` usually resolves to an image
   tagged `EventType` - one generic "Author Event" tile shared by every author
   event in the system. That is the thin-artwork problem
   (`mapsee_retire_thin_artwork.py` exists because of it), so only an image the
   event actually owns is taken.

The type vocabulary is per-library free text - 73 distinct names across six
systems, "Storytime" and "Story Time" and "Storytimes" among them - so the
category is matched on KEYWORDS rather than an enum, and the AUDIENCE decides
first where it can: a system that files a baby lapsit under "Books & Reading"
still tells you the audience is 0-18 months.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
GATEWAY = "https://gateway.bibliocommons.com/v2/libraries/{slug}/events"
# 200 is the largest the gateway honours; it silently clamps above that. Chicago
# is 23 pages at this size and 46 at 100, so it halves the round trips.
PAGE_LIMIT = 200
# A 5xx ON A 200-ROW PAGE IS THE GATEWAY FAILING TO BUILD THAT PAGE, and the
# same rows come back when asked for in smaller pieces. Measured 2026-09-24:
# Santa Clara County's page 2 answers 500 at limit=200 on every try, and rows
# 201-400 answer 200 as four pages of 50. It is not a bad record and not a
# refusal. The loop used to stop at the first non-200, so a system lost
# everything after that page - Boston Public Library "p13 HTTP 500" in the
# 2026-09-24 CI run, 1,856 kept of the 4,051 measured on 2026-09-03 - and seven
# of 38 systems probed the same day stopped the same way (Pima kept 450 of
# ~4,000). A 5xx page is now re-read at SPLIT_LIMIT; a 403/404/410 still stops
# the system, because those are the library's answer, not a server's bad
# moment.
SPLIT_LIMIT = 50
# Nine months of programming is published in places (a fortnightly book group
# booked to 2027-06). Six is enough to be useful and short enough that nothing
# becomes furniture; the same reasoning as MyListing's horizon_days.
DEFAULT_HORIZON_DAYS = 180

_TAG = re.compile(r"<[^>]+>")


# A tag becomes a SPACE and not nothing, or `<p>one</p><p>two</p>` reads as
# "onetwo". These descriptions are real rich text - paragraphs, <b>, <u>, mailto
# links - so the joins matter.
#
# ...which then leaves the space in front of whatever punctuation followed the
# tag: "Bring a <b>laptop</b>." collapses to "Bring a laptop ." That is most
# rows here, because marking up the last word of a sentence is the commonest
# thing an editor does, and it shows on every sheet and every share card.
# Plain string work rather than a regex with a backreference: there is nothing
# here a group buys, and the literal pairs are easier to read and to extend.
_TIGHTEN_BEFORE = tuple(",.;:!?%)]}")
_TIGHTEN_AFTER = tuple("([{")


def _clean(s: Optional[str], limit: int = 4000) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    for ch in _TIGHTEN_BEFORE:
        s = s.replace(" " + ch, ch)
    for ch in _TIGHTEN_AFTER:
        s = s.replace(ch + " ", ch)
    return s.strip()[:limit] or None


# Keyword -> mapsee key. ORDER IS PRIORITY, not exclusivity: every rule a name
# matches contributes a key, and the earliest becomes the primary (see
# categorise). Matched against the lowercased type name because the vocabulary is
# per-library free text - "Storytime", "Story Time" and "Storytimes" are three
# libraries spelling one thing, and an exact-match table would file two as
# `other`.
_TYPE_RULES: Tuple[Tuple[str, str], ...] = (
    ("volunteer", "volunteer"), ("nonprofit", "volunteer"), ("human services", "volunteer"),
    ("story time", "kids"), ("storytime", "kids"), ("early literacy", "kids"),
    ("parenting", "kids"), ("parent and teacher", "kids"), ("homework", "kids"),
    ("sing, sign", "kids"), ("children", "kids"),
    ("performing arts", "theater"), ("theatre", "theater"), ("theater", "theater"),
    ("film", "arts"), ("movie", "arts"), ("visual arts", "arts"), ("art education", "arts"),
    ("arts", "arts"), ("craft", "arts"), ("poetry", "arts"), ("writing", "arts"),
    ("writers", "arts"), ("digital creation", "arts"), ("maker", "arts"),
    ("health", "fitness"), ("wellness", "fitness"),
    ("gaming", "party"), ("games", "party"),
    ("meetup", "community"), ("lgbtq", "community"), ("community", "community"),
    ("citizenship", "learning"), ("language", "learning"), ("esol", "learning"),
    ("book", "learning"), ("reading", "learning"), ("computer", "learning"),
    ("technology", "learning"), ("career", "learning"), ("jobs", "learning"),
    ("business", "learning"), ("genealogy", "learning"), ("stem", "learning"),
    ("steam", "learning"), ("workshop", "learning"), ("class", "learning"),
    ("education", "learning"), ("author", "learning"), ("science", "learning"),
)
# Audience bands whose presence means this is FOR children. Matched as
# substrings of the audience name for the same free-text reason as the types.
_CHILD_AUDIENCE = ("babies", "toddler", "preschool", "kids", "children", "tween", "family")
_TEEN_AUDIENCE = ("teen", "young adult")


def categorise(type_names: List[str], audience_names: List[str],
               default: str) -> Tuple[str, List[str]]:
    """(primary, extras).

    THE AUDIENCE IS CONSULTED FIRST AND IT IS THE STRONGER SIGNAL. A baby lapsit
    filed by its library under "Books & Reading" is a `learning` row by its type
    and a `kids` row to everyone looking for one, and the audience band says so
    unambiguously - "Babies: 0 to 18 months" - where the type never will.

    ...but only when the audience is EXCLUSIVELY young. A "Family Movie Night"
    tagged for kids AND adults is a community event that children are welcome
    at, and filing it under kids would take it off the door where most of its
    audience is looking. Teens count as young here; "Adults" and "Seniors" do
    not.
    """
    keys: List[str] = []
    for name in type_names:
        low = (name or "").lower()
        # EVERY rule a type name matches, not the first. Libraries lump: Chicago
        # files chess night under "Crafts, Games and Play", which is honestly
        # both `arts` and `party`, and stopping at the first match sent a chess
        # club to the arts door and nowhere else. Breadth across lenses is the
        # point of `categories` (see EventStore._fill_missing), and a type name
        # that names two things is the source telling us so.
        for needle, key in _TYPE_RULES:
            if needle in low and key not in keys:
                keys.append(key)
    aud = [(a or "").lower() for a in audience_names]
    young = [a for a in aud if any(n in a for n in _CHILD_AUDIENCE + _TEEN_AUDIENCE)]
    if aud and len(young) == len(aud):
        primary = "kids"
        extras = norm_categories(primary, keys)
    else:
        primary = keys[0] if keys else default
        extras = norm_categories(primary, keys[1:])
    return primary, extras


def _iso_local(s: Optional[str]) -> Optional[str]:
    """'2026-09-04T09:30' -> '2026-09-04T09:30:00'; a bare date stays a date.

    Naive on purpose - see the header. Anything already carrying an offset or a
    Z is returned untouched so a future change at the source cannot be silently
    reinterpreted as local time.
    """
    s = (s or "").strip()
    if not s:
        return None
    if s.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?", s)
    return (m.group(1) + (m.group(2) or ":00")) if m else None


def _address(place: Dict[str, Any]) -> Dict[str, Optional[str]]:
    a = place.get("address") or {}
    number, street = _clean(a.get("number"), 20), _clean(a.get("street"), 120)
    # Joined only when BOTH halves are there. A bare street with no number
    # geocodes to the middle of the road, and a bare number is not an address at
    # all - and neither matters here, because the coordinate is what places the
    # pin (coords_exact). This text is for the sheet and the share card.
    line = f"{number} {street}" if (number and street) else (street or None)
    return {"address": line, "city": _clean(a.get("city"), 80),
            "region": _clean(a.get("state") or a.get("province"), 80),
            "country": _clean(a.get("country"), 60),
            "postal_code": _clean(a.get("zip") or a.get("postalCode"), 20)}


def _point(place: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    mp = place.get("mapLocation") or {}
    c = mp.get("centrePoint") or {}
    try:
        lat, lon = float(c["lat"]), float(c["lng"])
    except (KeyError, TypeError, ValueError):
        return None, None, mp.get("timeZone")
    # 0,0 is the null island every geocoder writes for "no idea", and out of
    # range is a parse that went wrong rather than a place.
    if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None, mp.get("timeZone")
    return lat, lon, mp.get("timeZone")


def _image_url(image: Optional[Dict[str, Any]]) -> Optional[str]:
    """The event's OWN picture, or nothing.

    `tag: "EventType"` is a stock tile the library attached to a CATEGORY - one
    "Author Event" graphic behind every author event in the system. Importing it
    gives four hundred rows the same picture and is the exact shape
    mapsee_retire_thin_artwork.py was written to undo.
    """
    if not image or image.get("tag") == "EventType":
        return None
    url = image.get("url")
    return url if isinstance(url, str) and url.startswith("http") else None


def to_event(ev: Dict[str, Any], ent: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    dfn = ev.get("definition") or {}
    name = _clean(dfn.get("title"), 300)
    start = _iso_local(dfn.get("start"))
    if not name or not start:
        return None
    if dfn.get("isCancelled"):
        return None

    # Branch first, then off-site. No place at all means no pin, and a library
    # programme with no location is not something we can put on a map.
    locs, places = ent.get("locations") or {}, ent.get("places") or {}
    where = locs.get(str(dfn.get("branchLocationId"))) if dfn.get("branchLocationId") else None
    if where is None and dfn.get("nonBranchLocationId"):
        where = places.get(str(dfn.get("nonBranchLocationId")))
    if not where:
        return None
    lat, lon, tz = _point(where)
    if lat is None:
        return None
    addr = _address(where)

    types = [(ent.get("eventTypes") or {}).get(str(t), {}).get("name")
             for t in (dfn.get("typeIds") or [])]
    auds = [(ent.get("eventAudiences") or {}).get(str(a), {}).get("name")
            for a in (dfn.get("audienceIds") or [])]
    primary, extras = categorise([t for t in types if t], [a for a in auds if a],
                                 site.get("category", "learning"))

    slug = site["slug"]
    end = _iso_local(dfn.get("end"))
    nev = NormalizedEvent(
        source="bibliocommons",
        source_id=f"{slug}:{ev.get('id')}",
        name=name,
        description=_clean(dfn.get("description")),
        start_local=start,
        # An end at or before the start is a typo, not a negative event - the
        # same refusal mapsee_ingest_mapasculturais makes.
        end_local=end if (end and end > start) else None,
        timezone=tz,
        venue_name=_clean(where.get("name"), 160),
        latitude=lat, longitude=lon,
        coords_exact=True,
        category=primary, categories=extras,
        poster_image_url=_image_url((ent.get("images") or {}).get(str(dfn.get("featuredImageId")))),
        ticket_url=f"https://{slug}.bibliocommons.com/events/{ev.get('id')}",
        **addr,
    )
    # TWO SESSIONS OF ONE PROGRAMME ON ONE DAY ARE TWO EVENTS, and the shared
    # key does not know that. make_fingerprint is a CROSS-SOURCE key and is
    # deliberately date-granular (name | YYYY-MM-DD | venue) - exactly right for
    # "is this the same gig in two ticketing feeds", and wrong here: a branch
    # runs Family Storytime at 10:15 and again at 11:15, they are registered for
    # separately, and the second one vanished. Measured on one page of Vancouver,
    # 9 rows of 197 merged into another and were never written.
    #
    # So the clock time joins the BASIS - never the stored name - the way
    # mapsee_ingest_affiliates folds " pickup" into its own. That makes the key
    # strictly narrower, and the only thing a narrower key can cost is failing to
    # merge a library programme with a copy of itself in some other feed. No
    # other source we read carries these.
    nev.fingerprint = make_fingerprint(f"{name} {start[11:16]}".strip(),
                                       start[:10], nev.venue_name, nev.city)
    return nev


def _split_page(session, url: str, page: int, delay: float) -> Optional[Dict[str, Any]]:
    """Page `page` of PAGE_LIMIT rows, read as PAGE_LIMIT // SPLIT_LIMIT pages of
    SPLIT_LIMIT and merged back into one body of the same shape.

    The merge is per ENTITY KIND, not just events: a row names its branch,
    audience and type by id, and those live in `entities.locations` etc. of the
    page that carried the row. A small page that still fails costs its own
    SPLIT_LIMIT rows and nothing after it. None when every one of them failed.
    """
    per = PAGE_LIMIT // SPLIT_LIMIT
    merged: Dict[str, Dict[str, Any]] = {}
    count, ok = None, 0
    for k in range(1, per + 1):
        if delay:
            time.sleep(delay)
        try:
            r = session.get(url, params={"limit": SPLIT_LIMIT, "page": (page - 1) * per + k},
                            timeout=45)
            if r.status_code != 200:
                continue
            b = r.json()
        except Exception:  # noqa: BLE001
            continue
        ok += 1
        for kind, objs in (b.get("entities") or {}).items():
            if isinstance(objs, dict):
                merged.setdefault(kind, {}).update(objs)
        c = ((b.get("events") or {}).get("pagination") or {}).get("count")
        if c:
            count = int(c)
    if not ok:
        return None
    # `pages` in the units the caller pages in, so its last-page test still holds.
    pages = -(-count // PAGE_LIMIT) if count else 0
    return {"entities": merged, "events": {"pagination": {"pages": pages, "count": count}},
            "split_ok": ok}


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    slug = (site.get("slug") or "").strip()
    label = site.get("name") or slug
    if not slug:
        print(f"[bibliocommons] {label}: no slug")
        return 0
    horizon = int(site.get("horizon_days", DEFAULT_HORIZON_DAYS))
    today = date.today().isoformat()
    limit_day = (date.today() + timedelta(days=horizon)).isoformat()
    delay = float(site.get("crawl_delay", 1))
    max_pages = int(site.get("max_pages", 40))
    url = GATEWAY.format(slug=slug)

    kept = past = beyond = unplaceable = 0
    for page in range(1, max_pages + 1):
        try:
            r = session.get(url, params={"limit": PAGE_LIMIT, "page": page}, timeout=45)
        except Exception as exc:  # noqa: BLE001
            print(f"[bibliocommons] {label} p{page} failed: {exc}")
            break
        if r.status_code >= 500:
            # The gateway failing to build a big page - see SPLIT_LIMIT.
            body = _split_page(session, url, page, delay)
            if body is None:
                print(f"[bibliocommons] {label} p{page} HTTP {r.status_code}, "
                      f"and so was every {SPLIT_LIMIT}-row page of it")
                break
            print(f"[bibliocommons] {label} p{page} HTTP {r.status_code}; re-read as "
                  f"{body['split_ok']} of {PAGE_LIMIT // SPLIT_LIMIT} pages of {SPLIT_LIMIT}")
        elif r.status_code != 200:
            # A 403 here is the SYSTEM declining, not a transport error: several
            # BiblioCommons libraries answer the catalogue and refuse the events
            # gateway. Reported plainly and never retried or worked around.
            print(f"[bibliocommons] {label} p{page} HTTP {r.status_code}")
            break
        else:
            try:
                body = r.json()
            except Exception as exc:  # noqa: BLE001
                print(f"[bibliocommons] {label} p{page} bad JSON: {exc}")
                break
        ent = body.get("entities") or {}
        rows = list((ent.get("events") or {}).values())
        if not rows:
            break

        on_page = 0
        for ev in rows:
            day = str(((ev.get("definition") or {}).get("start") or ""))[:10]
            if day and day < today:
                past += 1
                continue
            # THE HORIZON IS APPLIED HERE BECAUSE THE SERVER'S IS NOT APPLIED AT
            # ALL - startDate/endDate are accepted and ignored (see the header).
            if day and day > limit_day:
                beyond += 1
                continue
            nev = to_event(ev, ent, site)
            if not nev:
                unplaceable += 1
                continue
            store.upsert(nev)
            kept += 1
            on_page += 1

        pag = (body.get("events") or {}).get("pagination") or {}
        pages = int(pag.get("pages") or 0)
        if pages and page >= pages:
            break
        # Rows come back in date order, so a page with nothing inside the
        # horizon means every later page is beyond it too. Without this the loop
        # reads the whole archive to find out it wanted none of it.
        if on_page == 0 and beyond:
            print(f"[bibliocommons] {label}: past the {horizon}-day horizon at page {page}")
            break
        if delay:
            time.sleep(delay)

    print(f"[bibliocommons] {label}: kept {kept} "
          f"(skipped {past} past, {beyond} beyond horizon, {unplaceable} unplaceable)")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import BiblioCommons library events into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site (substring match on name or slug)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in f"{site.get('name','')} {site.get('slug','')}".lower():
            continue
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:  # noqa: BLE001
            print(f"[bibliocommons] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[bibliocommons] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
