#!/usr/bin/env python3
"""
mapsee_ingest_fairs.py — the state fair itself, as one multi-day event.

WHAT IS MISSING WITHOUT THIS. Measured against the live catalog on 2026-09-04:
969 future events carry the word "fair" in their title and almost none of them
is a state fair. They are career fairs, graduate school fairs, Fairleigh
Dickinson, the Fairfield Stags and a Manitoba Moose game at a place called Texas
Stars. Of the 48 fairs this config names, the ones genuinely present arrive as
per-DAY admission rows through Ticketmaster ("Minnesota State Fair - Saint Paul"
on the 6th and again on the 7th) — never as the thing people actually plan
around, which is "the Iowa State Fair runs 13–23 August in Des Moines".

WHY THE EXISTING DISCOVERY CANNOT DO IT. All 47 reachable fair sites were probed
with catalog_discover_osm.find_calendar. Six have a machine-readable calendar
(five The Events Calendar, one Modern Events) and those calendars are the
fairgrounds' year-round bookings — a circus in October, a gun show in March —
not the fair. Thirty-four fingerprint as nothing at all. A state fair's website
is a marketing site, and the fair's dates are its headline, not its data.

SO THE DATES ARE SCRAPED, AND NEVER WRITTEN DOWN HERE. A curated date list would
be accurate for one season and then wrong in a way nothing could see: fairs move
by up to a fortnight year to year, and a config that has to be hand-edited
annually across 48 rows is a config that is quietly stale by March. Scraping on
every run means a fair that has not announced next year yet contributes nothing
and starts contributing the day it does — which is the correct behaviour and the
one nobody has to remember. Measured on the first pass: 26 of 48 publish a
usable future date today, and the 22 that do not are mostly fairs that closed
last week and have not put next year's up.

FOUR RULES DECIDE WHETHER A DATE RANGE IS THE FAIR, and each one is a false
positive this made before the rule existed:

  1. NOT A SALE WINDOW. statefairva.org's homepage says "Advance Tickets on Sale
     Now from September 1 – 22, 2026" — a 22-day span that parses perfectly and
     is when you can buy, not when it opens. The 45 characters either side of a
     range are checked and sale language disqualifies it. Kept SHORT and kept
     NARROW, both learned the same way: at 90 characters Colorado's correct
     dates were rejected for a "Deals" menu item and Kansas's for a "Commercial
     Vendor application", neither of which was about the range at all.
  2. NOT A DIFFERENT EVENT. kystatefair.org offers "World's Championship Horse
     Show August 22-29, 2026", which is a real event with real dates and is not
     the fair. Same check, different vocabulary.
  3. NOT LONGER THAN THREE WEEKS. The longest fair here is The Big E at 17 days
     (Sept. 18–Oct. 4). Anything past 21 is a season, a sale or a parse that
     joined two ranges.
  4. NOT OVER. The end date has to be today or later, which is also what makes
     the scrape self-correcting: delawarestatefair.com currently shows both
     "JULY 23 - August 1, 2026" (its last one) and "JULY 22-31, 2027" (its next),
     and only the second survives.

The earliest surviving range wins, so a fair running RIGHT NOW beats the one it
is already advertising for next year.

    python mapsee_ingest_fairs.py --config fair_sources.json --store events.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:                                               # pragma: no cover
    requests = None

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = "|".join(MONTHS)

# "Aug 12-22, 2027" | "July 23 - August 1, 2026" | "Sept. 18–Oct. 4, 2026"
# | "Aug. 27 – Labor Day, Sept. 7, 2026" — that last interjection is Minnesota's
# and New York's, and it is why a holiday name is allowed between the dash and
# the second month.
RANGE_RX = re.compile(
    rf"\b({_MON})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:[-–—]|to|through)\s*"
    # THE INTERJECTION MUST END IN A COMMA, and that comma is load-bearing.
    # Without it the group is greedy enough to swallow the END MONTH: "July 13 -
    # August 12, 2026" matched with "August " eaten as an interjection, leaving
    # July 13 to July 12, which reverses and is silently dropped — so Ohio and
    # Wisconsin both read as "no date range on the page" while the range was
    # right there. The real interjections all carry it: "Aug. 27 - Labor Day,
    # Sept. 7, 2026" and "August 28 - Monday, September 7, 2026".
    rf"(?:[A-Za-z][A-Za-z ]{{0,13}},\s*)?(?:({_MON})[a-z]*\.?\s+)?"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(20\d\d)", re.I)

# Rules 1 and 2, as one vocabulary over the words AROUND the range. Deliberately
# a deny list and deliberately short: the words a fair uses to announce itself
# are unbounded and local, the words a ticket promotion uses are not.
NOT_THE_FAIR_RX = re.compile(
    r"on sale|presale|pre-sale|advance ticket|early bird|discount|save \$|"
    r"entry deadline|entries? (?:close|due)|"
    r"horse show|rodeo finals|demolition derby", re.I)

MAX_FAIR_DAYS = 21          # rule 3 — The Big E is the longest here at 17
# HOW CLOSE A DISQUALIFYING WORD HAS TO BE. Ninety characters was too far: a
# fair's hero sits next to its navigation, so "August 28 - September 7, 2026"
# (Colorado's, correct) landed within reach of a "Deals" menu item and "Sept.
# 11 - 20, 2026" (Kansas's, correct) within reach of "Commercial Vendor
# application". Both were thrown away for words that were never about them.
# Forty-five still covers the phrasings that matter, because a sale window
# announces itself immediately before its dates — "Advance Tickets on Sale Now
# from September 1 - 22, 2026" — never a paragraph away.
CONTEXT_CHARS = 45


def text_of(html: str) -> str:
    """Visible text. Script and style bodies go first — a JSON blob in a script
    tag carries dates that are not on the page at all."""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _mk(month: int, day: int, year: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None                          # "February 31" is somebody's typo


# A YEAR SITTING JUST BEFORE A RANGE. iowastatefair.org publishes a TABLE of
# future dates — "YEAR FAIR DATES 2027 Aug 12-22 2028 Aug 10-20 2029 Aug 9-19" —
# where the year LEADS its row. Read left to right that is "Aug 12-22 2028",
# which parses perfectly and is the 2027 fair wearing 2028's label. The guard is
# not to prefer the leading year (a page can say "Thank you for 2026! August
# 5-15, 2027" and mean the trailing one); it is to notice that two years are in
# play and refuse to choose. One fair contributing nothing beats one fair
# contributing a date a year out.
_YEAR_BEFORE_RX = re.compile(r"(20\d\d)\D{0,6}$")
MAX_MONTHS_AHEAD = 15       # a pin two years out is not a plan, it is noise


def parse_ranges(text: str) -> List[Tuple[date, date, str, str]]:
    """(start, end, matched, context) for every date range in the text.

    The END month is optional in the source — "Aug 12-22, 2027" leaves it out —
    and the year belongs to the END. When the end month is absent it is the same
    month; when it is present and EARLIER than the start month, the range
    crosses new year, so the start belongs to the year before. That last case is
    not hypothetical here: a January fair advertised in December reads
    "December 30 - January 4, 2027".
    """
    out = []
    for m in RANGE_RX.finditer(text):
        m1, d1, m2, d2, yr = m.groups()
        year = int(yr)
        lead = _YEAR_BEFORE_RX.search(text[max(0, m.start() - 12):m.start()])
        if lead and int(lead.group(1)) != year:
            continue                         # two years, no way to tell — see above
        mon1, mon2 = MONTHS[m1[:3].lower()], MONTHS[(m2 or m1)[:3].lower()]
        start = _mk(mon1, int(d1), year if mon2 >= mon1 else year - 1)
        end = _mk(mon2, int(d2), year)
        if not start or not end or end < start:
            continue
        a, b = max(0, m.start() - CONTEXT_CHARS), min(len(text), m.end() + CONTEXT_CHARS)
        out.append((start, end, m.group(0).strip(), text[a:b]))
    return out


def pick_fair_dates(text: str, today: Optional[date] = None
                    ) -> Tuple[Optional[Tuple[date, date, str]], str]:
    """(the fair's dates, why-not). The four rules, in the order they are cheap.

    Returns the EARLIEST surviving range, so a fair open right now beats the one
    it is already advertising for next year.
    """
    today = today or date.today()
    ranges = parse_ranges(text)
    if not ranges:
        return None, "no date range on the page"
    reasons = []
    kept = []
    for start, end, matched, ctx in ranges:
        if end < today:
            reasons.append("past"); continue
        if start > today + timedelta(days=MAX_MONTHS_AHEAD * 31):
            reasons.append("too far out"); continue
        if (end - start).days + 1 > MAX_FAIR_DAYS:
            reasons.append("too long"); continue
        hit = NOT_THE_FAIR_RX.search(ctx)
        if hit:
            reasons.append("not-the-fair:" + hit.group(0).lower()); continue
        kept.append((start, end, matched))
    if not kept:
        seen = sorted(set(reasons))
        return None, f"{len(ranges)} range(s), all rejected ({', '.join(seen[:3])})"
    kept.sort(key=lambda k: k[0])
    return kept[0], ""


def to_event(site: Dict[str, Any], start: date, end: date) -> NormalizedEvent:
    """One all-day, multi-day event.

    ALL-DAY ON PURPOSE. A fair's gates open at a different hour every day of its
    run and on none of these sites is that a machine-readable fact. A bare
    YYYY-MM-DD is this repo's way of saying "a day, not a minute", and the sync
    spans it over the day where the EVENT is rather than the server's.
    """
    name = site["name"]
    nev = NormalizedEvent(
        source="fair",
        # The YEAR is in the identity. A fair is annual and its name never
        # changes, so without it next year's row would be read as this year's
        # having moved, and EventStore would pop one for the other.
        source_id=f"{site['state'].lower()}-{start.year}",
        name=f"{name} {start.year}",
        description=None,
        start_local=start.isoformat(),
        end_local=end.isoformat(),
        venue_name=site.get("venue_name") or f"{name} fairgrounds",
        latitude=site.get("lat"), longitude=site.get("lon"),
        city=site.get("city"), region=site.get("region"),
        country=site.get("country") or "United States",
        category="community",
        categories=norm_categories("community", ["market", "kids", "food"]),
        ticket_url=site.get("url"),
        coords_exact=bool(site.get("lat")),
    )
    nev.fingerprint = make_fingerprint(nev.name, start.isoformat(),
                                       nev.venue_name, nev.city)
    return nev


# Which page to read when the homepage says nothing, best first. A fair site's
# navigation is thirty links wide and the dates are on exactly one of them, so
# an unranked "first three that match" reads Buy Tickets, Concerts and Vendors
# and never reaches General Information — which is where wistatefair.com states
# its dates in a sentence.
_HINT_RANK = (
    (re.compile(r"\bdates?\b", re.I), 0),
    (re.compile(r"general.?info|fair.?info", re.I), 1),
    (re.compile(r"plan.?your|hours|admission", re.I), 2),
    (re.compile(r"\bvisit\b|\babout\b", re.I), 3),
    (re.compile(r"tickets", re.I), 4),
)


def _hinted_links(html: str, base: str) -> List[str]:
    from urllib.parse import urljoin, urlparse
    host = urlparse(base).netloc
    scored = {}
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, txt = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        hay = href + " " + txt
        rank = next((r for rx, r in _HINT_RANK if rx.search(hay)), None)
        if rank is None:
            continue
        u = urljoin(base, href)
        if urlparse(u).netloc != host:
            continue
        # A link can match twice; the strongest word wins, and the first
        # occurrence of a URL keeps its position for ties.
        if u not in scored or rank < scored[u][0]:
            scored[u] = (rank, len(scored))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: kv[1])]


def ingest_site(store: EventStore, session, site: Dict[str, Any],
                today: Optional[date] = None, follow: int = 6) -> int:
    label = site.get("name", "?")
    try:
        r = session.get(site["url"], timeout=25, allow_redirects=True)
    except Exception as exc:                                      # noqa: BLE001
        print(f"[fair] {label}: unreachable ({type(exc).__name__})")
        return 0
    if r.status_code != 200:
        print(f"[fair] {label}: HTTP {r.status_code}")
        return 0
    got, why = pick_fair_dates(text_of(r.text)[:80000], today)
    # THE HOMEPAGE FIRST, ALWAYS. A deep page is where the promotions live —
    # wistatefair.com's deals page is the false positive that proves it — so the
    # hinted pages are only read when the homepage said nothing at all, never to
    # overrule it.
    if not got:
        for u in _hinted_links(r.text, str(r.url))[:follow]:
            try:
                b = session.get(u, timeout=20).text
            except Exception:                                     # noqa: BLE001
                continue
            got, why2 = pick_fair_dates(text_of(b)[:80000], today)
            if got:
                why = ""
                break
            why = why2 or why
            time.sleep(0.4)
    if not got:
        print(f"[fair] {label}: no dates — {why}")
        return 0
    start, end, matched = got
    store.upsert(to_event(site, start, end))
    print(f"[fair] {label}: {start} -> {end} ({(end-start).days+1}d)  \"{matched}\"")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import US state fairs as multi-day events.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="one fair by name or state code (substring match)")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args(argv)

    doc = json.load(open(args.config, encoding="utf-8"))
    sites = doc["sites"] if isinstance(doc, dict) else doc
    if args.only:
        q = args.only.lower()
        sites = [s for s in sites
                 if q in s.get("name", "").lower() or q == s.get("state", "").lower()]
    if requests is None:
        print("[fair] requests is not installed"); return 1
    session = requests.Session()
    session.headers["User-Agent"] = UA
    store = EventStore(args.store)
    kept = 0
    for s in sites:
        # PER FAIR, not per run. One marketing site that hangs or serves a shape
        # nothing here expects must not cost the other forty-seven.
        try:
            kept += ingest_site(store, session, s)
        except Exception as exc:                                  # noqa: BLE001
            print(f"[fair] {s.get('name','?')} FAILED: {type(exc).__name__}: {exc}")
        time.sleep(args.delay)
    store.save()
    print(f"[fair] done: {kept} of {len(sites)} fairs have published dates; "
          f"store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
