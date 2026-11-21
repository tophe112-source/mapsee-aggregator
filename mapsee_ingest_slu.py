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

A CLOCK THE SITE WILL NOT SHOW IS NOT A CLOCK
---------------------------------------------
Every row carries a `date_time`, and 230 of 327 (2026-11-21) also carry
`dont_display_time: 1`. The site's own front-end returns an empty time label
for those and prints the date alone, because the editor never set a time: 93
of them are the form's `12:00 am`, and the other 137 are whatever the form
held (an exhibition's opening-reception hour on every weekday it hangs).
Read as a clock, A New Brain - a musical at 2pm and 8pm - reached the map as
"12:00 AM to 3:00 AM", and was off it eleven hours before its matinee.

So a row the site shows no time for names a DAY (a bare date; the sync
brackets it where the venue is), unless the publisher states the hour in
words this adapter can check against the row's own date:

    November 21st - 2pm and 8pm          in `long_description`: a line that
    Thursday, Dec 3: 10:00 am to 4:00 pm  BEGINS with this row's date
    Wed - Sat, ..., 12 - 5 pm             the tail of `custom_date_display_text`

Two clocks on one day are two performances, and each is its own row (see
`occasions`). A line that begins with a label ("Opening Reception: Sat, Nov 7
from 4-6pm") is somebody else's hour and is not read.

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
import hashlib
import html as html_mod
import json
import re
import sys
from datetime import date, datetime, timedelta
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


# --------------------------------------------------------------------------- #
# a row the site shows no time for - see the module docstring
# --------------------------------------------------------------------------- #
_BREAK = re.compile(r"<br\s*/?>|</(?:p|li|div|h[1-6]|tr)\s*>", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# "November 21st - 2pm and 8pm", "Friday, November 6th at 8 p.m.",
# "Thursday, Dec 3: 10:00 am to 4:00 pm". The DATE LEADS: a line that opens
# with a label names another occasion's hour, not this row's.
_DATED_LINE = re.compile(
    r"^(?:(?P<wd>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?"
    r"\s*(?:[-\u2013\u2014:@|]|\bat\b|\bfrom\b)\s*(?P<rest>.+?)\s*$", re.I)
_HOUR = r"(\d{1,2})(?::(\d{2}))?"
_HALF = r"([ap])\.?\s?m\.?"
_ONE_CLOCK = re.compile(rf"^{_HOUR}\s*{_HALF}$", re.I)
_RANGE = re.compile(rf"^(?:from\s+)?{_HOUR}\s*(?:{_HALF})?\s*(?:[-\u2013\u2014]|to|until)\s*{_HOUR}\s*{_HALF}$", re.I)
_AND = re.compile(r"\s*(?:,|&|\+|\band\b)\s*", re.I)


def _minute(hh: str, mm: Optional[str], half: str) -> Optional[int]:
    h, m = int(hh), int(mm or 0)
    if not (1 <= h <= 12 and 0 <= m <= 59):
        return None
    return _h24(h, half) * 60 + m


def _times(text: str) -> List[Tuple[int, Optional[int]]]:
    """[(start, end-or-None)] in minutes of the day, or [] unless ALL of `text`
    is clocks: one range ("10:00 am to 4:00 pm", "12 - 5 pm") or a list of
    starts ("2pm and 8pm"). Anything else on the line - a price, a room, an
    "or" - and nothing is read, because half a line is a guess."""
    text = re.sub(r"\s+", " ", text or "").strip().rstrip(".;,").strip()
    if not text:
        return []
    m = _RANGE.match(text)
    if m:
        h1, m1, half1, h2, m2, half2 = m.groups()
        end = _minute(h2, m2, half2)
        start = _minute(h1, m1, half1 or half2)
        if start is not None and end is not None and not half1 and start >= end:
            start = _minute(h1, m1, "a")              # "11 - 1 pm" opens in the morning
        return [(start, end)] if start is not None and end is not None and start < end else []
    out: List[Tuple[int, Optional[int]]] = []
    for part in _AND.split(text):
        c = _ONE_CLOCK.match(part.strip().rstrip("."))
        minute = _minute(*c.groups()) if c else None
        if minute is None or (out and minute <= out[-1][0]):
            return []
        out.append((minute, None))
    return out


def showtimes(long_description: Optional[str], day: str) -> List[Tuple[int, Optional[int]]]:
    """What the listing's own prose says happens on `day` (YYYY-MM-DD).

    Only a line that BEGINS with that date, whose stated weekday and year (when
    it has them) agree with it, and whose whole remainder is clocks. Two such
    lines for one day that disagree are no answer."""
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return []
    text = html_mod.unescape(html_mod.unescape(_TAG.sub(" ", _BREAK.sub("\n", str(long_description or "")))))
    found: List[List[Tuple[int, Optional[int]]]] = []
    for line in text.replace("\xa0", " ").split("\n"):
        m = _DATED_LINE.match(line.strip())
        if not m:
            continue
        if _MONTHS[m.group("mon")[:3].lower()] != d.month or int(m.group("day")) != d.day:
            continue
        if m.group("year") and int(m.group("year")) != d.year:
            continue
        if m.group("wd") and _WEEKDAYS.index(m.group("wd").lower()) != d.weekday():
            continue                                  # the line is about another year's date
        times = _times(m.group("rest"))
        if times and times not in found:
            found.append(times)
    return found[0] if len(found) == 1 else []


def shown_hours(row: Dict[str, Any]) -> List[Tuple[int, Optional[int]]]:
    """Hours the site PRINTS in place of its time label: the tail of
    `custom_date_display_text` ("Wed - Sat, Nov 13 - Dec 18, 2026, 12 - 5 pm").
    One range only; a date range has no am/pm and is not one."""
    tail = str(row.get("custom_date_display_text") or "").rsplit(",", 1)[-1]
    times = _times(tail)
    return times if len(times) == 1 and times[0][1] is not None else []


def has_clock(row: Dict[str, Any]) -> bool:
    """False when the site itself shows no time for this row."""
    return str(row.get("dont_display_time") or "0").strip().lower() in ("", "0", "false")


def _stamp(day: str, minute: int) -> str:
    return f"{day}T{minute // 60:02d}:{minute % 60:02d}:00"


def occasions(row: Dict[str, Any]) -> List[Tuple[str, Optional[str]]]:
    """[(start_local, end_local)] for one feed row: usually one, none when the
    row has no readable date, several when the listing states several
    performances for the day. A start that is a bare YYYY-MM-DD claims the day
    and no hour."""
    start = parse_start(row)
    if not start:
        return []
    if has_clock(row):
        return [(start, parse_end(row, start))]
    day = start[:10]
    stated = showtimes(row.get("long_description"), day) or shown_hours(row)
    if not stated:
        return [(day, None)]
    return [(_stamp(day, s), _stamp(day, e) if e is not None else None) for s, e in stated]


def later_fingerprint(base: str, start_local: str) -> str:
    """The second performance of a day. make_fingerprint is date-keyed, so an
    8pm show would otherwise merge into its own 2pm matinee and vanish
    (sync-eventstore-and-paging.md). Only the LATER ones are widened: the
    day's first keeps the key its row has always had in the database."""
    return hashlib.sha1(f"{base}|{start_local[11:16]}".encode("utf-8")).hexdigest()


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


def to_events(row: Dict[str, Any], site: Dict[str, Any]) -> Tuple[List[NormalizedEvent], str]:
    """(events, reason-if-skipped). One event per performance (`occasions`).
    The reason is returned rather than printed so the caller can keep the quiet
    skips quiet and shout about the rest."""
    if (row.get("post_status") or "publish") != "publish":
        return [], ""
    name = _clean(row.get("title"))
    when = occasions(row)
    venue = _clean(row.get("location_name"))
    if not name or not when:
        return [], f"incomplete (name={name!r} start={parse_start(row)!r})"
    if venue.lower() in {v.lower() for v in (site.get("skip_venues") or [])}:
        return [], ""                         # not a place; see _skip_venues in the config
    loc = place(row, site)
    if not loc:
        return [], (f"unplaceable venue, skipped: {venue!r} ({name}) - "
                    f"add it to `venues` in the config")
    primary, extra = categories(row, site)
    permalink = (row.get("permalink") or "").strip() or None
    out: List[NormalizedEvent] = []
    for n, (start, end) in enumerate(when):
        day = start[:10]
        nev = NormalizedEvent(
            source="discoverslu",
            # The occurrence, not the event: a weekly market is one row per week
            # and they must not collapse onto each other in the store. A day's
            # second performance adds its clock; the first keeps the old id.
            source_id=f"{row.get('event_id')}:{day}" + (f":{start[11:13]}{start[14:16]}" if n else ""),
            name=name,
            description=_clean(row.get("description")) or _clean(row.get("excerpt")) or None,
            start_local=start,
            end_local=end,
            venue_name=venue or None,
            address=loc.get("address"), city=loc.get("city"),
            region=loc.get("region"), country=loc.get("country"),
            postal_code=loc.get("postal_code"),
            category=primary,
            categories=list(extra) if extra else extra,
            poster_image_url=(row.get("image_src") or "").strip() or None,
            ticket_url=permalink,
        )
        nev.fingerprint = make_fingerprint(name, day, venue)
        if n:
            nev.fingerprint = later_fingerprint(nev.fingerprint, start)
        out.append(nev)
    return out, ""


def to_event(row: Dict[str, Any], site: Dict[str, Any]) -> Tuple[Optional[NormalizedEvent], str]:
    """The row's first (usually only) event; see to_events."""
    events, why = to_events(row, site)
    return (events[0] if events else None), why


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
    days = 0
    for row in rows:
        events, why = to_events(row, site)
        if not events:
            skipped += 1
            if why:
                print(f"[slu] {why}")
            continue
        for nev in events:
            store.upsert(nev)
            kept += 1
            days += len(nev.start_local) == 10
    # Counted so a feed that stops setting times, or a reader that stops finding
    # them, shows as a number in the log rather than as a quieter evening.
    print(f"[slu] {site.get('name','?')}: kept {kept} ({days} with no time shown, as a day), "
          f"skipped {skipped}, of {len(rows)} rows")
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
