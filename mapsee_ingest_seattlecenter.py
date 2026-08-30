#!/usr/bin/env python3
"""
mapsee_ingest_seattlecenter.py - import the Seattle Center campus calendar
(https://seattlecenter.com/events/event-calendar) into the Mapsee store.

    python mapsee_ingest_seattlecenter.py --config seattlecenter_sources.json \
        --store feeds_events.json

WHY THIS IS A SCRAPER
---------------------
Same reason as mapsee_ingest_pioneersquare.py: the site publishes no feed.
Checked, all of it:

    /robots.txt                     404 (ASP.NET "resource cannot be found")
    schema.org JSON-LD              absent from listing AND detail pages
    ?format=ical / .ics anywhere    no link, no endpoint
    any /api/ or JSON endpoint      none; the markup is server-rendered

So there is nothing to consume but HTML. Worth paying for: Seattle Center is
one campus holding McCaw Hall, Cornish Playhouse, the Bagley Wright, the
Armory, Fisher Pavilion, the Mural Amphitheatre and the open grounds, and
roughly half of what it lists is FREE - 36 of the first 70 rows. Free
courtyard concerts, community commemorations and public art walks are exactly
the supply the ticketing APIs do not carry.

robots.txt returning 404 means nothing is disallowed; there is no rule to
honour and none to work around. This still reads only the public pages, at one
request per event with a pause, like every other scraper here.

THE DATE IS ON THE DETAIL PAGE. DO NOT READ IT FROM THE LISTING.
---------------------------------------------------------------
The listing groups cards under a `date-bar__date` heading - "August 25" - and
the card itself carries only a TIME. So the naive read of a card gives a time
with no date, and the obvious fix (inherit the nearest date-bar) gives a date
WITH NO YEAR. This calendar runs about seven months ahead, so it crosses New
Year every autumn, and a year inferred from "today" puts every January show
eleven months in the past, where the horizon filter drops it without a word.

The detail page states it in full and unambiguously:

    <div class="event__label">Date</div> ... <div class="event__detail">
        Tuesday, August 25, 2026

That block - Date / Time / Place / Cost - was present on 70 of 70 pages
sampled, and it is the ONLY thing this adapter trusts for when and where. The
listing is used for two things and nothing else: which events exist, and when
to stop paginating (see `_year_walk`).

DO NOT TAKE COORDINATES FROM THE PAGES EITHER. TWO TRAPS, BOTH SILENT.
----------------------------------------------------------------------
Every location renders as a Google Maps link, and reading it is wrong twice
over:

1. ON A DETAIL PAGE THE LINK BELONGS TO A DIFFERENT EVENT. Each detail page
   carries a related-events rail with its own location links, so the first
   maps URL in the markup is usually some other show's. Measured: the pages
   for Climate Pledge Arena, the Bagley Wright and Artists At Play Plaza all
   offer the same three coordinate pairs in varying order. Scraping it would
   put J. Cole in a theatre 300m away, in a well-formed row with nothing to
   flag it.

2. THE FIRST COORDINATE IN THE URL IS THE WRONG ONE. A maps URL carries two
   pairs - `@lat,lon` (the viewport centre) and `!3d<lat>!4d<lon>` (the
   place). On this site they disagree by a CONSTANT 0.0021887 of longitude,
   about 165m west, on every link, and `@` is the pair a naive regex finds
   first. Against OpenStreetMap `!3d!4d` is right and `@` is not: Climate
   Pledge Arena is -122.35398 in OSM and -122.353988 in `!3d!4d`, but `@`
   says -122.3561767.

So placement comes from a VENUE BOOK in the config, keyed on the detail page's
own `Place` string, exactly as pioneersquare and rolodex do it. The campus has
a bounded vocabulary - 14 distinct Places across the first 70 events, all
inside four city blocks - so a book is both feasible and stable. Its
coordinates were taken from the site's OWN per-card `!3d!4d` values and then
cross-checked against OpenStreetMap; every building agreed to within ~35m. A
Place that is not in the book is skipped LOUDLY, because an unplaceable event
is worse than no event and the log line is how the book grows.

WHAT IS DELIBERATELY NOT INGESTED
---------------------------------
`skip_places` in the config. Climate Pledge Arena is in it by default and the
config says why at length: it is the most thoroughly Ticketmaster-covered room
in Seattle, this repo already ingests Ticketmaster, and mapsee_dedupe_events.py
matches on the NORMALIZED TITLE - so "J. Cole" here and Ticketmaster's "J.
Cole: The Fall-Off Tour" would not collapse, and the arena would draw two pins
for every show. The skip is printed on every run rather than dropped in
silence.
"""
from __future__ import annotations

import argparse
import hashlib
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

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

TZ_NAME = "America/Los_Angeles"

_TAG = re.compile(r"<[^>]+>")

# The listing card's link. Relative, and with no leading slash - "events/event-
# calendar/j-cole" - which is why every use of it goes through _abs().
_LINK = re.compile(r'(?is)class="event-list__title">\s*.*?href="(events/event-calendar/[^"#?]+)"')

# The date heading the listing groups cards under. No year; see the docstring.
_DATE_BAR = re.compile(r'(?is)class="date-bar__date">\s*(.*?)\s*</p>')

# The detail page's Date / Time / Place / Cost block. The label and its value
# sit in sibling columns with a wrapper between them, so this spans the gap.
_LABEL_DETAIL = re.compile(
    r'(?is)class="event__label">\s*(.*?)\s*</div>.*?class="event__detail"[^>]*>\s*(.*?)\s*</div>')

_H1 = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
_TITLE = re.compile(r"(?is)<title>(.*?)</title>")
_OG_IMAGE = re.compile(r'(?is)<meta[^>]+property="og:image"[^>]+content="([^"]*)"')
_META_DESC = re.compile(r'(?is)<meta[^>]+name="description"[^>]+content="([^"]*)"')

# The booking link, selected by ANCHOR TEXT rather than by URL or class: the
# page's outbound links are mostly site chrome (Support Us, Privacy Policy, the
# City of Seattle, four socials, two newsletter sign-ups), and the one that
# books the event is labelled "Tickets". "More Information" is the organiser's
# own site, and is the fallback for the free events that have nothing to book.
_ANCHOR = re.compile(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>')

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return re.sub(r"\s+", " ", html_mod.unescape(_TAG.sub(" ", s))).strip() or None


def _abs(href: str, base: str = "https://seattlecenter.com/") -> str:
    if href.startswith("http"):
        return href
    return base.rstrip("/") + "/" + href.lstrip("/")


def _ok(status: int) -> bool:
    return 200 <= status < 300


def parse_detail_date(s: str) -> Optional[str]:
    """'Tuesday, August 25, 2026' -> '2026-08-25'.

    The YEAR is required. This is the one field the listing cannot supply and
    the whole reason every event costs a second request, so a page that has
    drifted into some other format must fail here rather than be patched up
    from today's date - a guessed year is exactly the well-formed, plausible,
    wrong value that survives every check downstream.
    """
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(2))).isoformat()
    except ValueError:
        return None


def parse_detail_time(s: str) -> Optional[str]:
    """'8:00 p.m.' -> '20:00:00'. 'All Day' -> None, which is NOT midnight.

    An all-day event given a start of 00:00 sorts and renders as a thing that
    begins at midnight. Returning None leaves the row carrying a DATE and no
    clock, which is what the source actually said - the same shape
    mapsee_ingest_markets gives a market with no published hours.
    """
    low = (s or "").strip().lower()
    if not low or "all day" in low:
        return None
    if low.startswith("noon"):
        return "12:00:00"
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\s*\.?\s*m", low)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), m.group(2) or "00", m.group(3)
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi}:00" if h <= 23 else None


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:      # pragma: no cover - zoneinfo ships with 3.9+
        return None


def localize(ds: str, t: Optional[str]) -> Tuple[str, Optional[str]]:
    """(start_local, start_utc). No clock -> a bare date and no instant.

    Seattle Center writes wall-clock times with no offset, and August and
    January are not the same offset, so the instant is derived through the
    campus timezone rather than a fixed -08:00.
    """
    if not t:
        return ds, None
    tz = _tz()
    if tz is None:
        return f"{ds}T{t}", None
    dt = datetime.fromisoformat(f"{ds}T{t}").replace(tzinfo=tz)
    return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_place(s: str) -> str:
    """Book key: lowercased, punctuation dropped, a leading 'the' and any
    ' at Seattle Center' suffix removed. The site writes one room several ways
    across a season - "Cornish Playhouse", "The Cornish Playhouse", "Cornish
    Playhouse at Seattle Center" - and they are the same room."""
    s = (s or "").lower()
    s = re.sub(r"\bat seattle center\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def place(name: str, site: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A location for a `Place` string, or None when the book has no entry."""
    book = {_norm_place(k): v for k, v in (site.get("places") or {}).items()}
    hit = book.get(_norm_place(name))
    if not hit:
        return None
    lat, lon = hit.get("lat"), hit.get("lon")
    if not hit.get("address") and (lat is None or lon is None):
        return None
    return {
        "address": hit.get("address") or site.get("default_address"),
        "city": hit.get("city") or site.get("default_city"),
        "region": hit.get("region") or site.get("default_region"),
        "country": hit.get("country") or site.get("default_country"),
        "postal_code": hit.get("postal_code") or site.get("default_postal_code"),
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "category": hit.get("category"),
        "coords_exact": bool(hit.get("coords_exact")),
    }


def _year_walk(bars: List[str], start: date) -> List[date]:
    """Date headings ("August 24", "January 03") -> real dates.

    The listing is chronological and carries no year, so the year is recovered
    by watching the month go BACKWARDS: once it does, the calendar has crossed
    New Year. Used only to decide when to stop paginating - never as an event's
    date, which always comes from its own detail page.
    """
    out: List[date] = []
    year, prev_month = start.year, start.month
    for b in bars:
        m = re.search(r"([A-Za-z]+)\s+(\d{1,2})", b or "")
        if not m:
            continue
        mon = _MONTHS.get(m.group(1).lower())
        if not mon:
            continue
        if mon < prev_month:
            year += 1
        prev_month = mon
        try:
            out.append(date(year, mon, int(m.group(2))))
        except ValueError:
            continue
    return out


def event_urls(session, site: Dict[str, Any]) -> List[str]:
    """Every event link within the horizon, walking the listing's pages.

    Three stopping conditions, because the calendar is long - about seven
    months, roughly 90 pages of 7 - and almost all of it is beyond any useful
    horizon. A page with no event links, which is how this site ends (page 99
    returns 0 rather than wrapping back to page 1, so this cannot loop); the
    horizon; and max_pages as a backstop if either of those ever stops holding.
    """
    base = site.get("listing") or "https://seattlecenter.com/events/event-calendar"
    horizon = date.today() + timedelta(days=int(site.get("within_days", 60)))
    max_pages = int(site.get("max_pages", 40))
    pause = float(site.get("pause", 0.4))
    seen: List[str] = []
    for page in range(1, max_pages + 1):
        url = f"{base}?&page={page}"
        try:
            r = session.get(url, timeout=40)
        except Exception as exc:
            print(f"[seattlecenter] listing page {page} failed: {exc}")
            break
        if not _ok(r.status_code):
            print(f"[seattlecenter] listing page {page} HTTP {r.status_code}")
            break
        links = _LINK.findall(r.text)
        if not links:
            if page == 1:
                # The single most likely breakage, and it must not read as "no
                # events on this month". source-health.yml turns a source that
                # has gone quiet into an issue; only this line says why.
                print("[seattlecenter] !! listing page 1 matched no event links - the "
                      "markup has probably changed. Check _LINK.")
            break
        for u in links:
            if u not in seen:
                seen.append(u)
        bars = _year_walk(_DATE_BAR.findall(r.text), date.today())
        if bars and bars[-1] > horizon:
            break
        time.sleep(pause)
    return seen[: int(site.get("max_events", 400))]


def booking_link(page: str) -> Optional[str]:
    """The "Tickets" anchor, else "More Information"; see _ANCHOR."""
    best: Dict[str, str] = {}
    for href, text in _ANCHOR.findall(page):
        label = (_clean(text) or "").lower()
        if label in ("tickets", "more information") and href.startswith("http"):
            best.setdefault(label, href)
    return best.get("tickets") or best.get("more information")


def occurrence_fingerprint(name: str, start_local: str, spot: str) -> str:
    """The shared cross-source key, WIDENED BY THE CLOCK. A matinee is not the
    evening show.

    make_fingerprint truncates its date argument to YYYY-MM-DD on purpose - it
    is the cross-source key, and two feeds describing one gig disagree about the
    minute. Every adapter in this repo uses it as-is, and for every one of them
    that is right, because their sources do not run the same event twice in a
    day.

    THIS ONE DOES. Freak the Mighty plays a 2:00 p.m. matinee and a 7:30 p.m.
    evening show on both Saturdays in the sample, and the site files them as
    separate listings (-x47869, -x47870) because they are separate performances
    that different people hold tickets to. name|date|place is byte-identical for
    the pair, and EventStore dedupes on the fingerprint PRIMARY - so the second
    one merges into the first and a real, sold, attended performance disappears
    before anything downstream can notice. Measured on the 70-page sample: 59
    events, 57 fingerprints, both losses a Saturday matinee.

    mapsee_dedupe_events.py already draws this line the same way, and says why:
    outside the DATE_KEYED categories it clusters on the exact start instant,
    because "a matinee and an evening performance of one show share a title, a
    venue and a date, and collapsing them would delete a real event that real
    people are attending separately." This is that rule, applied one stage
    earlier, for the one source that needs it.

    An All Day event has no clock in start_local, so it hashes to exactly what
    make_fingerprint alone would return, and cross-source matching for the 29
    all-day rows is unchanged.
    """
    base = make_fingerprint(name, start_local, spot)
    clock = start_local[11:16] if len(start_local) >= 16 else ""
    if not clock:
        return base
    return hashlib.sha1(f"{base}|{clock}".encode("utf-8")).hexdigest()


def to_event(url: str, page: str, site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    fields = {(_clean(a) or ""): _clean(b) for a, b in _LABEL_DETAIL.findall(page)}
    when = parse_detail_date(fields.get("Date") or "")
    spot = fields.get("Place")
    hm = _H1.search(page)
    name = _clean(hm.group(1)) if hm else None
    if not name:
        tm = _TITLE.search(page)
        name = _clean((tm.group(1).split("|")[0] if tm else "")) or None
    if not name or not when or not spot:
        missing = ", ".join(w for w, ok in
                            (("name", name), ("date", when), ("place", spot)) if not ok)
        print(f"[seattlecenter] incomplete ({missing}): {url}")
        return None
    if _norm_place(spot) in {_norm_place(s) for s in (site.get("skip_places") or [])}:
        return None
    loc = place(spot, site)
    if not loc:
        print(f"[seattlecenter] unplaceable place, skipped: {spot!r} ({name}) - "
              f"add it to `places` in the config")
        return None
    start_local, start_utc = localize(when, parse_detail_time(fields.get("Time") or ""))
    dm = _META_DESC.search(page)
    ig = _OG_IMAGE.search(page)
    # "Free Event" is the site's own words, and half this calendar carries it -
    # which is most of why the source is worth having. NormalizedEvent has no
    # price field, so it leads the description, where a reader sees it.
    desc = _clean(dm.group(1)) if dm else None
    cost = fields.get("Cost")
    if cost and desc:
        desc = f"{cost} · {desc}"
    elif cost:
        desc = cost
    nev = NormalizedEvent(
        source="seattlecenter",
        source_id=url.rstrip("/").rsplit("/", 1)[-1],
        name=name,
        description=desc,
        start_local=start_local,
        start_utc=start_utc,
        timezone=TZ_NAME,
        venue_name=spot,
        address=loc.get("address"), city=loc.get("city"),
        region=loc.get("region"), country=loc.get("country"),
        postal_code=loc.get("postal_code"),
        latitude=loc.get("lat"), longitude=loc.get("lon"),
        coords_exact=loc.get("coords_exact", False),
        # Per ROOM, not per site: a McCaw Hall night and a Mural Amphitheatre
        # afternoon are not the same lens. Still only a default -
        # derive_categories in mapsee_supabase_sync promotes from the text,
        # which is what turns "Summer Fitness: Workout Wednesdays: Yoga" into
        # fitness on its own.
        category=loc.get("category") or site.get("category", "community"),
        poster_image_url=ig.group(1) if ig else None,
        ticket_url=booking_link(page) or _abs(url),
    )
    nev.fingerprint = occurrence_fingerprint(name, start_local, spot)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    urls = event_urls(session, site)
    pause = float(site.get("pause", 0.4))
    horizon = (date.today() + timedelta(days=int(site.get("within_days", 60)))).isoformat()
    today = date.today().isoformat()
    kept = skipped = 0
    for u in urls:
        full = _abs(u)
        try:
            r = session.get(full, timeout=40)
        except Exception as exc:
            print(f"[seattlecenter] {full} failed: {exc}")
            continue
        if not _ok(r.status_code):
            print(f"[seattlecenter] {full} HTTP {r.status_code}")
            continue
        nev = to_event(u, r.text, site)
        if not nev:
            skipped += 1
        elif nev.start_local[:10] < today or nev.start_local[:10] > horizon:
            # The listing keeps the day's finished events up, and pagination
            # overshoots the horizon by up to one page.
            skipped += 1
        else:
            store.upsert(nev)
            kept += 1
        time.sleep(pause)
    print(f"[seattlecenter] {site.get('name', '?')}: kept {kept}, skipped {skipped}, "
          f"of {len(urls)} listed")
    if site.get("skip_places"):
        print(f"[seattlecenter] not ingested, by config: {', '.join(site['skip_places'])} "
              f"- see skip_places in the config for why")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Import the Seattle Center campus calendar into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({
        "User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[seattlecenter] {site.get('name', '?')} FAILED: {exc}")
    store.save()
    print(f"[seattlecenter] done: +{total} events; "
          f"store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
