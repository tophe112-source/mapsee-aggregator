#!/usr/bin/env python3
"""
mapsee_ingest_slu.py - import the Discover South Lake Union neighbourhood
calendar (https://www.discoverslu.com/calendar/) into the Mapsee store.

    python mapsee_ingest_slu.py --config slu_sources.json --store mapsee_events.json

WHY THIS SOURCE
---------------
It was added because the biggest free public event in the neighbourhood was not
on the map. The 2026 South Lake Union Block Party - free, all ages, two music
stages, food trucks, the association's own longest-running event - was absent
from a catalogue that held 395 other Seattle-area events for that weekend. So
was the Seattle Design Festival Block Party the next day. Neither is ticketed,
which is exactly why no ticketing API has them, and both are on this calendar.

WHY IT IS ITS OWN ADAPTER
-------------------------
Everything cheaper was checked first:

    /wp-json/wp/v2/event              200, 1016 posts - but `acf` is empty and
                                      the dates live in meta that is not exposed
    schema.org JSON-LD on an event    only Yoast's WebPage/BreadcrumbList graph
    /events/ archive                  464 bytes, empty
    event-sitemap.xml                 1001 URLs, no dates - and reading a date
                                      off each costs 1001 requests against a
                                      robots.txt that asks for Crawl-delay: 10
    The Events Calendar / Localist    not installed; the theme is bespoke ("dslu")

The calendar page is a SvelteKit island, and what it reads is a plain public
admin-ajax action. That is a real JSON API - date range, paging, `has_more` -
so this adapter reads what the page reads and parses no HTML at all:

    /wp-admin/admin-ajax.php?action=get_calendar_events
        &start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&limit=N&offset=N

Unauthenticated, no key, and it answers the production User-Agent with 200 -
which the browser-only sources in this repo do not, and which is the difference
between a source we may take and one we may not. robots.txt is
`User-agent: * / Disallow:` with `Crawl-delay: 10`; the whole horizon comes back
in ONE request, so the crawl delay is satisfied by never needing a second.

OCCURRENCES ARE ALREADY EXPANDED, AND THE SERIES START IS A TRAP
----------------------------------------------------------------
A six-month window returns 225 rows for 118 distinct `event_id`s: the endpoint
expands a recurrence into one row per date. On those rows `date_time` is the
SERIES start and `occurrence_date_time` is the row's own date, so reading
`date_time` would put every occurrence of the Saturday farmers market on the
first Saturday in June and lose the rest. `occurrence_date_time or date_time`,
which is what the site's own front-end does, is the only correct read.

PLACING THEM
------------
The feed carries a street address on all but a handful of rows, so unlike
mapsee_ingest_pioneersquare.py this is not a venue book with a feed attached -
it is a feed with a two-entry book for the gaps. See `venues` in the config for
where those two addresses came from, and why a bare venue name cannot be left
to the geocoder.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

_TAG = re.compile(r"<[^>]+>")

# "2026-08-14 11:00 am" (start) and "10:00 pm" (end, same day, no date of its own)
_STAMP = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\s*$", re.I)
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\s*$", re.I)

# SLU's taxonomy -> ours. Their vocabulary is a BUSINESS directory's (it also
# tags salons and dentists), so several branches have no event meaning and land
# on the site default rather than being forced into a lens they do not belong
# in. `entertainment` deliberately stays community: the sync's own title rules
# (_PARTY_RX and friends) are better at telling a block party from a book club
# than a directory tag chosen for a venue is.
_CATS = {
    "art": "arts", "other-arts-culture": "arts", "photography": "arts",
    "crafts": "arts", "museums": "arts", "dance": "arts",
    "film-theater": "theater",
    "music": "music",
    "classes-lectures": "learning", "education": "learning",
    "bakeries": "food", "bars-restaurants": "food", "coffee-tea": "food",
    "other-eat-drink": "food", "food-grocery": "food",
    "fitness": "fitness",
    "outdoors": "outdoors", "other-recreation": "outdoors",
    "pop-ups": "market", "other-shopping": "market",
    "non-profits": "community", "other-services": "community",
    "entertainment": "community", "other-live-work": "community",
}


def _clean(s: Optional[str]) -> str:
    """Markup and entities out; the feed ships both, sometimes double-encoded."""
    if not s:
        return ""
    t = html_mod.unescape(_TAG.sub(" ", str(s)))
    t = html_mod.unescape(t)          # "Arts &amp;amp; Culture" happens
    return re.sub(r"\s+", " ", t).strip()


def _h24(hour: int, half: str) -> int:
    hour %= 12
    return hour + (12 if half.lower() == "p" else 0)


def parse_start(row: Dict[str, Any]) -> Optional[str]:
    """The row's OWN date - see the occurrence note in the module docstring."""
    raw = (row.get("occurrence_date_time") or "").strip() or (row.get("date_time") or "").strip()
    m = _STAMP.match(raw)
    if not m:
        return None
    y, mo, d, hh, mm, half = m.groups()
    return f"{y}-{mo}-{d}T{_h24(int(hh), half):02d}:{mm}:00"


def parse_end(row: Dict[str, Any], start: str) -> Optional[str]:
    """`end_time` is a clock with no date: it belongs to the START day, not to
    `end_date` (which is the last day of a RUN, so pairing the two turns a
    three-week supply drive into one 21-day event)."""
    m = _CLOCK.match((row.get("end_time") or "").strip())
    if not m:
        return None
    hh, mm, half = m.groups()
    end = f"{start[:10]}T{_h24(int(hh), half):02d}:{mm}:00"
    return end if end > start else None      # "10 am - 1 am" would run backwards


def place(row: Dict[str, Any], site: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Address off the row, else the config's book, else nothing - and nothing
    means SKIP, because the sync drops a street-less row anyway and does it
    without a log line."""
    street = " ".join(x for x in ((row.get("address1") or "").strip(),
                                  (row.get("address2") or "").strip()) if x).strip()
    if street:
        return {"address": street,
                "city": (row.get("city") or "").strip() or site.get("default_city"),
                "region": (row.get("state") or "").strip() or site.get("default_region"),
                "postal_code": (row.get("zip") or "").strip() or None,
                "country": site.get("default_country")}
    book = {k.lower(): v for k, v in (site.get("venues") or {}).items()}
    hit = book.get(_clean(row.get("location_name")).lower())
    if not hit:
        return None
    return {"address": hit.get("address"),
            "city": hit.get("city") or site.get("default_city"),
            "region": hit.get("region") or site.get("default_region"),
            "postal_code": hit.get("postal_code"),
            "country": hit.get("country") or site.get("default_country")}


def categories(row: Dict[str, Any], site: Dict[str, Any]) -> Tuple[str, List[str]]:
    keys: List[str] = []
    for c in row.get("categories") or []:
        k = _CATS.get(str((c or {}).get("slug") or "").strip().lower())
        if k and k not in keys:
            keys.append(k)
    primary = keys[0] if keys else site.get("category", "community")
    return primary, norm_categories(primary, keys[1:])


def to_event(row: Dict[str, Any], site: Dict[str, Any]) -> Tuple[Optional[NormalizedEvent], str]:
    """(event, reason-if-skipped). The reason is returned rather than printed so
    the caller can keep the quiet skips quiet and shout about the rest."""
    if (row.get("post_status") or "publish") != "publish":
        return None, ""
    name = _clean(row.get("title"))
    start = parse_start(row)
    venue = _clean(row.get("location_name"))
    if not name or not start:
        return None, f"incomplete (name={name!r} start={start!r})"
    if venue.lower() in {v.lower() for v in (site.get("skip_venues") or [])}:
        return None, ""                       # not a place; see _skip_venues in the config
    loc = place(row, site)
    if not loc:
        return None, (f"unplaceable venue, skipped: {venue!r} ({name}) - "
                      f"add it to `venues` in the config")
    primary, extra = categories(row, site)
    permalink = (row.get("permalink") or "").strip() or None
    nev = NormalizedEvent(
        source="discoverslu",
        # The occurrence, not the event: a weekly market is one row per week and
        # they must not collapse onto each other in the store.
        source_id=f"{row.get('event_id')}:{start[:10]}",
        name=name,
        description=_clean(row.get("description")) or _clean(row.get("excerpt")) or None,
        start_local=start,
        end_local=parse_end(row, start),
        venue_name=venue or None,
        address=loc.get("address"), city=loc.get("city"),
        region=loc.get("region"), country=loc.get("country"),
        postal_code=loc.get("postal_code"),
        category=primary,
        categories=extra,
        poster_image_url=(row.get("image_src") or "").strip() or None,
        ticket_url=permalink,
    )
    nev.fingerprint = make_fingerprint(name, start[:10], venue)
    return nev, ""


def fetch(session, site: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One window, paged only if the endpoint says there is more. `has_more` is
    the endpoint's own flag; the offset walk exists so a busier season than the
    one this was written against cannot silently truncate."""
    today = datetime.now().date()
    end = today + timedelta(days=int(site.get("within_days", 180)))
    limit = int(site.get("limit", 500))
    out: List[Dict[str, Any]] = []
    offset = 0
    while True:
        params = {"action": "get_calendar_events",
                  "start_date": today.isoformat(), "end_date": end.isoformat(),
                  "limit": limit, "offset": offset}
        r = session.get(site["endpoint"], params=params, timeout=45)
        if r.status_code != 200:
            print(f"[slu] {site.get('name','?')}: HTTP {r.status_code}")
            break
        try:
            body = r.json()
        except ValueError:
            print(f"[slu] {site.get('name','?')}: response was not JSON ({len(r.text)} bytes)")
            break
        data = (body or {}).get("data") or {}
        rows = data.get("events") or []
        meta = data.get("meta") or {}
        if meta.get("sqlite_error"):
            # The endpoint reports its own backend failure INSIDE a 200. Left
            # unread that is an empty calendar, which reads as "nothing on".
            print(f"[slu] {site.get('name','?')}: endpoint reported "
                  f"sqlite_error={meta['sqlite_error']!r}")
        out.extend(rows)
        if not rows or not meta.get("has_more"):
            break
        offset += len(rows)
        if offset >= int(site.get("max_events", 2000)):
            print(f"[slu] {site.get('name','?')}: stopping at {offset} rows (max_events)")
            break
    if not out:
        # The likeliest breakage, and it must not read as a quiet neighbourhood:
        # source-health.yml turns a quiet source into an issue, the log says why.
        print(f"[slu] !! {site.get('name','?')} returned no rows - the admin-ajax action "
              f"may have been renamed. Check get_calendar_events on the calendar page.")
    return out


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    rows = fetch(session, site)
    kept = skipped = 0
    for row in rows:
        nev, why = to_event(row, site)
        if not nev:
            skipped += 1
            if why:
                print(f"[slu] {why}")
            continue
        store.upsert(nev)
        kept += 1
    print(f"[slu] {site.get('name','?')}: kept {kept}, skipped {skipped}, of {len(rows)} rows")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import the Discover South Lake Union calendar into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[slu] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[slu] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
