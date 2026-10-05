#!/usr/bin/env python3
"""
mapsee_ingest_revize.py - what is on at a small town's community centre, read
from the town's own Revize calendar.

    python mapsee_ingest_revize.py --config revize_sources.json \
        --store feeds_events.json [--only manassas] [--max-minutes 10] [--dry-run]

WHY THIS SOURCE. Revize is a government website CMS (the vendor says 3,000+
clients) and its calendar plugin answers ONE JSON request with a town's whole
calendar: `/_assets_/plugins/revizeCalendar/calendar_data_handler.php
?webspace=<ws>&relative_revize_url=//cmsN.revize.com&protocol=https:`, on the
town's own host. There is no iCal export and no schema.org markup, and the
generic adapters never saw it. The City of Manassas, VA (DC metro) is why it
was written: measured 2026-10-04, 915 rows - Community Events 663, Public
Meetings 135, Community Center 66, Harris Pavilion 51 - and the Community
Center calendar is the city's drop-in timetable (Toddler Drop-In four mornings
a week, Open Gym, Zumba 18+, Line Dancing 18+, Walk With Ease 55+, Run Club,
Lego Club). Only 49 rows are DATED inside 90 days; 193 carry an RRULE, and the
Community Center's 155 sessions in the next 90 days are ALL recurring. Read
without the rule, the timetable is invisible.

The config is per SITE: which calendars to read (by `primary_calendar_name`),
the town's time zone, a geocoder suffix, a `centre` and `radius_km` that bound
what the geocoder is believed, a small venue book, the calendars the town
itself runs (`host_calendars`), and title rules that pick the door
(`category_by_title`, config-wide). A calendar that is not named is never
read; a Public Meetings calendar is never named.

------------------------------------------------------------------------------
LESSONS, each measured on the 2026-10-04 and 2026-10-05 reads
------------------------------------------------------------------------------

1. THE RULE IS A SMALL iCalendar BLOCK, IN FLOATING LOCAL TIME.
   "DTSTART:20260903T171500\\nRDATE:20260903T171500\\nRRULE:FREQ=WEEKLY;
   INTERVAL=1;BYDAY=MO,TH;UNTIL=20261130T000000\\nEXDATE:20261012T171500".
   Over 1,924 rows on five sites: FREQ DAILY/WEEKLY/MONTHLY/YEARLY/HOURLY
   (HOURLY is a matinee and an evening show: INTERVAL=4;COUNT=2), BYDAY with
   BYSETPOS for "second Tuesday", BYMONTH + BYMONTHDAY for holidays, COUNT,
   UNTIL. `expand_rule` implements exactly that subset (no dependency: the
   pipeline's requirements.txt has no dateutil) and refuses a rule part it
   does not know rather than guess, and refuses the RFC combinations it
   would expand wrongly (YEARLY BYMONTHDAY without BYMONTH, an ordinal BYDAY
   with BYMONTHDAY, WEEKLY BYSETPOS - none is used live). Checked against
   python-dateutil on all 323 rules of those five sites, 2020-2028: 5,209
   occurrences, 0 different. The clock is the town's wall clock, so a
   10:30 drop-in stays 10:30 across the 1 November DST change; the UTC
   instant is computed per occurrence with zoneinfo.

2. UNTIL IS AN INSTANT, AS THE TOWN'S OWN PAGE READS IT. Revize writes the
   editor's end date at midnight (UNTIL=20261031T000000), so a 14:00 session
   on 31 October is AFTER it. The town's calendar page expands the same string
   with rrule.js (rz.js: `new rrule.rrulestr(ev.rrule, {forceset: true})`),
   which reads it the same way, so the adapter shows what the page shows. The
   prose does not always agree: "STEM Looks Like Me!" lists "October 17, 24"
   (no 31st), but Walk With Ease (55+) says "until November 7" and its
   UNTIL=20261107T000000 drops that Saturday on the page and here alike.

3. THE DESCRIPTION'S "NO PROGRAM" DATES ARE EXCLUSIONS THE RULE FORGOT. Zumba
   18+ says "(No program 9/7, 10/12, 11/23 and 11/26)" and its EXDATEs are
   9/7, 10/12, 11/13 and 11/26 - 11/13 is a Friday, a typo for Monday 11/23.
   Without reading the text, Monday 23 November is a Zumba class nobody
   teaches. `no_program_days` reads "No program/class/session <m/d, ...>" and
   skips those days too (1 occurrence on 2026-10-05: that Monday).

4. A TYPED-AM CLOCK IS FIXED BY ROW ID, WITH THE EVIDENCE, AND NOTHING ELSE
   IS. Open Gym (row 1115) runs 08:30-22:00 every Friday; the city's own
   Saturday row says "Friday 8:30 - 10:00 pm". A 13.5-hour row would pulse
   "happening now" all day at a gym that is shut. `clock_fixes` in the config
   moves a start only while it still reads the wrong value (`from`), so a city
   correction or a recycled id is never moved. Every other timed row of 12 to
   24 hours is refused as a span, not a session (counted).

5. THE CALENDAR IS NOT ALL EVENTS. Of Manassas's 248 occurrences in the next
   90 days on the three calendars read (2026-10-05), 23 are refused: 11 civic
   services (voter-registration deadlines, early-voting days, Election Day, a
   hazardous-waste drop-off, a sticker contest), 5 office-closure holidays, 4
   committee and Friends-of meetings filed under Community Events, 3 monthly
   spice-kit pick-ups. Public Meetings (135 rows) is never read. Each refusal
   has a name and a count (`REFUSALS`). Registration-only rows are refused in
   the city's own words too ("Registration with the City of Manassas Community
   Center required!", "Sign up is required at cityofmanassas.recdesk.com" -
   the Jul-Aug LaBlast and Line Dancing block, a library STEAM club), but
   "*No Sign Up Is Required*" and "Registration is not required" are not; a
   ticketed show is an event with its price stated, not a course; the
   library's "Teen Advisory Group" is a teen club, not governance.

6. "FREE" IS SAID ONLY WHEN THE TOWN SAYS IT, AND A PRICE SAYS "not free".
   ../mapsee's 0227 tagger reads the row's text. A row whose own words call it
   free ("this free program", "FREE Lessons", "2 pm Free") and names no price
   gets "Admission: free."; a row naming a price gets "Price as listed: $25
   (not free)." - 0227's FREE_NEG vetoes the row, so the Chorale's "land of the
   free" and its "free GMU student" seats cannot make a $30 concert read free.
   NOT EVERY AMOUNT IS A PRICE: "$3 off admission" (Factory of Fear), "a $75,000
   Our Town award" (Olympia's Armory, x9, "Event is free, open to the public"),
   "earning less than $69,000" (a tax clinic), "valued at $15-30" (an art swap)
   are read by their neighbouring words and dropped, and when the town says
   the EVENT is free, a fee in a sentence about participants or vendors
   (Bladensburg's Auto Showcase: "A $20 registration fee ... for showcase
   participants") is theirs, not the visitor's. Silence says nothing.

7. PLACEMENT IS THE ROW'S OWN LOCATION TEXT, AND A FAR ANSWER IS NOT BELIEVED
   (221 of 226 kept occurrences placed on 2026-10-05; the 5 others are Tai
   Chi, which names no place). A venue book entry (regex over the location)
   pins the places Photon gets wrong: "9317 Center St" (the cemetery tours)
   was a ROOFER 2.5 km away and the Census geocoder has no match to correct
   it; the library was on its building but the sync's Census pass moved it
   370 m to an address interpolation, so it is pinned exact; "Downtown
   Manassas" was a post office and is now a deliberate downtown point. A book
   entry with `query` is geocoded by that text ("Gymnasium" in Pacific is the
   community centre's gym; Photon answered it with Pacific COUNTY's centroid,
   140 km off). Anything else is geocoded the way mapsee_ingest_ics geocodes
   a LOCATION - its helper, its shared cache, its budget - and an answer more
   than `radius_km` (25) from the site's `centre` is unplaced and counted:
   "Downtown Olympia" was a memorial in Tacoma (40 km), a bare "2600 East Bay
   Drive NE" Massapequa NY. A trailing NE/NW/SE/SW is a street quadrant, not a
   state, so that street gets the town's suffix. A location that is only the
   town ("bladensburg, maryland"), no single place ("Various Locations in
   Bladensburg") or a district ("Downtown Olympia") is unplaced without asking.
   Rows keep their street so the sync's Census pass can refine a non-exact
   point. A row with no location is never pinned to the community centre:
   Run Club meets at the museum and Open Gym at the Boys & Girls Club, on the
   Community Center's own calendar.

8. IDENTITY IS THE OCCURRENCE: `<site>:<row id>:<YYYYMMDDTHHMM>`, and the clock
   joins the fingerprint's basis (as perfectmind and bibliocommons do), so the
   6:30 and 7:30 cemetery tours are two rows, and a re-read writes the same
   rows. A moved start orphans the old occurrence until its date passes.

9. THE TOWN'S CLOSURE ROWS CLOSE THE SESSIONS IT RUNS. "Christmas Holidays ...
   all City offices closed" (Dec 24-25), "New Year's Day Holiday" and the
   Thanksgiving pair are refused as rows, AND every occurrence on those days
   from a `host_calendars` calendar is skipped: Toddler Drop-In's own EXDATEs
   already skip every other city holiday in its rule (9/7, 10/12, 11/23-27),
   and an upsert cannot delete a session written 90 days ahead. 6 skipped on
   2026-10-05 (Toddler Drop-In 12/24, 12/25, 1/1; Open Gym 11/27, 12/25, 1/1).
   A club the town only lists (`not_host_title`: Run Club) is not closed by it.

10. THE HOST AND THE DOOR. Only a `host_calendars` row names the town as
   promoter (the product renders it as "hosted by" and as JSON-LD organizer);
   Community Events lists the Hylton PAC's, the ARTfactory's, the county
   library's and Historic Manassas Inc's events, and those name no host.
   Every calendar maps to `community`, and the sync promoted none of the
   timetable, so `category_by_title` names the door: Run Club 26 -> running,
   Open Gym 18 and Higher Level Basketball 14 -> sports, Walk With Ease 14,
   Zumba 13, Line Dancing 7 -> fitness, the ARTfactory's musical -> theater.

THE 2026-10-05 RUN. 4 sites, 8 requests (4 robots.txt, 4 calendars), 11 s on a
warm geocode cache (the 2026-10-04 cold run made 17 Photon lookups in 55 s).
221 occurrences in 90 days: Manassas 213 (151 from the Community Center
calendar - Toddler Drop-In 44, Run Club 26, Open Gym 18, Walk With Ease 14,
Higher Level Basketball 14, Zumba 13, Line Dancing 7, Lego Club 6), Pacific WA
5, Bladensburg MD 2, Olympia WA 1. 0 duplicate fingerprints. After
derive_categories (primary): kids 67, community 52, fitness 34, sports 32,
running 26, theater 4, learning 3, volunteer 3. 12 say "Admission: free." and
15 carry a price; 0227's twin tags exactly those 12 free. `expand_rule` agrees
with python-dateutil on all 323 rules of the five probed sites, 2020-2028:
5,209 occurrences, 0 different.

ROBOTS AND REFUSALS. Every site's robots.txt is read before its one request
(robots_txt.Robots); a refusal, a 401/403/429 or a challenge page skips the
site for the run and is printed, never retried another way. City of Manassas
Park's Revize site answers `Disallow: /` and is in `_not_included`.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint, looks_online_only

USER_AGENT = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_HORIZON_DAYS = 90
DEFAULT_MAX_MINUTES = 10.0
MIN_GAP_SECONDS = 1.1                  # per host, and never below a Crawl-delay
SPAN_REFUSE_HOURS = 12                 # lesson 4
MAX_PERIODS = 20000                    # expansion safety: a rule that never matches stops here

# ---------------------------------------------------------------------------
# Lesson 1 - the RRULE subset
# ---------------------------------------------------------------------------
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_KNOWN_PARTS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYSETPOS", "BYMONTH",
                "BYMONTHDAY", "WKST"}


class RuleError(ValueError):
    """A rule this module will not guess at."""


def _dt(value: str, tz=None) -> datetime:
    """'20260903T171500' (floating local), '...Z' (UTC -> tz's wall clock) or
    '20260903' (a date: the whole day counts, so 23:59:59)."""
    v = value.strip()
    m = re.fullmatch(r"(\d{8})(?:T(\d{6})(Z?))?", v)
    if not m:
        raise RuleError(f"bad date-time {value!r}")
    d, t, z = m.groups()
    if t is None:
        return datetime.strptime(d, "%Y%m%d").replace(hour=23, minute=59, second=59)
    out = datetime.strptime(d + t, "%Y%m%d%H%M%S")
    if z:
        out = out.replace(tzinfo=timezone.utc)
        out = (out.astimezone(tz) if tz else out).replace(tzinfo=None)
    return out


def parse_rule_block(text: str, tz=None) -> Dict[str, Any]:
    """The Revize `rrule` string -> {dtstart, rule (dict), rdates, exdates}."""
    out: Dict[str, Any] = {"dtstart": None, "rule": None, "rdates": [], "exdates": []}
    for line in re.split(r"[\r\n]+", text or ""):
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.split(";", 1)[0].upper()
        if name == "DTSTART":
            out["dtstart"] = _dt(value, tz)
        elif name == "RRULE":
            parts = {}
            for p in value.split(";"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    parts[k.strip().upper()] = v.strip()
            unknown = set(parts) - _KNOWN_PARTS
            if unknown:
                raise RuleError(f"rule parts not supported: {sorted(unknown)}")
            out["rule"] = parts
        elif name in ("RDATE", "EXDATE"):
            for v in value.split(","):
                if v.strip():
                    out["rdates" if name == "RDATE" else "exdates"].append(_dt(v, tz))
    if out["dtstart"] is None:
        raise RuleError("no DTSTART")
    return out


def _ints(v: Optional[str]) -> List[int]:
    return [int(x) for x in (v or "").split(",") if x.strip()]


def _byday(v: Optional[str]) -> List[Tuple[Optional[int], int]]:
    out = []
    for tok in (v or "").split(","):
        tok = tok.strip().upper()
        if not tok:
            continue
        m = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", tok)
        if not m:
            raise RuleError(f"bad BYDAY {tok!r}")
        out.append((int(m.group(1)) if m.group(1) else None, _WEEKDAYS[m.group(2)]))
    return out


def _month_days(y: int, m: int) -> int:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def _days_matching_byday(days: List[date], byday) -> List[date]:
    """Within one period's ordered days: plain weekdays, or the n-th / -n-th of
    a weekday counted inside that period (RFC 5545 BYDAY with an ordinal)."""
    out = set()
    for n, wd in byday:
        hits = [d for d in days if d.weekday() == wd]
        if n is None:
            out.update(hits)
        elif 0 < n <= len(hits):
            out.add(hits[n - 1])
        elif n < 0 and -n <= len(hits):
            out.add(hits[n])
    return sorted(out)


def _by_month_day(days: List[date], mdays: List[int]) -> List[date]:
    out = []
    for d in days:
        last = _month_days(d.year, d.month)
        for md in mdays:
            if (md > 0 and d.day == md) or (md < 0 and d.day == last + md + 1):
                out.append(d)
                break
    return out


def _setpos(cands: List[date], setpos: List[int]) -> List[date]:
    if not setpos:
        return cands
    out = set()
    for p in setpos:
        if 0 < p <= len(cands):
            out.add(cands[p - 1])
        elif p < 0 and -p <= len(cands):
            out.add(cands[p])
    return sorted(out)


def _period_dates(freq: str, anchor: date, rule: Dict[str, str], dtstart: datetime) -> List[date]:
    """The candidate DAYS of the period that starts at `anchor`."""
    bymonth = _ints(rule.get("BYMONTH"))
    bymday = _ints(rule.get("BYMONTHDAY"))
    byday = _byday(rule.get("BYDAY"))
    setpos = _ints(rule.get("BYSETPOS"))
    if freq == "DAILY":
        days = [anchor]
        if bymonth:
            days = [d for d in days if d.month in bymonth]
        if bymday:
            days = _by_month_day(days, bymday)
        if byday:
            days = [d for d in days if d.weekday() in {wd for _, wd in byday}]
        return _setpos(days, setpos)
    if freq == "WEEKLY":
        days = [anchor + timedelta(days=i) for i in range(7)]
        if byday:
            days = [d for d in days if d.weekday() in {wd for _, wd in byday}]
        else:
            days = [d for d in days if d.weekday() == dtstart.weekday()]
        if bymonth:
            days = [d for d in days if d.month in bymonth]
        return _setpos(days, setpos)
    if freq == "MONTHLY":
        if bymonth and anchor.month not in bymonth:
            return []
        days = [anchor.replace(day=i) for i in range(1, _month_days(anchor.year, anchor.month) + 1)]
        if bymday:
            days = _by_month_day(days, bymday)
        if byday:
            days = _days_matching_byday(days, byday)
        if not bymday and not byday:
            days = [d for d in days if d.day == dtstart.day]
        return _setpos(days, setpos)
    if freq == "YEARLY":
        months = bymonth or [dtstart.month]
        out: List[date] = []
        if byday and not bymonth:
            year = [date(anchor.year, 1, 1) + timedelta(days=i)
                    for i in range((date(anchor.year + 1, 1, 1) - date(anchor.year, 1, 1)).days)]
            out = _days_matching_byday(year, byday)
            if bymday:
                out = _by_month_day(out, bymday)
        else:
            for m in sorted(months):
                days = [date(anchor.year, m, i) for i in range(1, _month_days(anchor.year, m) + 1)]
                if bymday:
                    days = _by_month_day(days, bymday)
                if byday:
                    days = _days_matching_byday(days, byday)
                if not bymday and not byday:
                    days = [d for d in days if d.day == dtstart.day]
                out.extend(days)
        return _setpos(sorted(out), setpos)
    raise RuleError(f"FREQ={freq} not supported")


def expand_rule(block: Dict[str, Any], until_bound: datetime) -> List[datetime]:
    """Every occurrence from DTSTART to `until_bound` (inclusive), RDATEs added,
    EXDATEs removed. Floating local datetimes in, floating local out."""
    dtstart: datetime = block["dtstart"]
    rule = block.get("rule")
    occ: Set[datetime] = set(r for r in block.get("rdates") or [] if r <= until_bound)
    if rule:
        freq = (rule.get("FREQ") or "").upper()
        interval = int(rule.get("INTERVAL") or 1)
        if interval < 1:
            raise RuleError("INTERVAL < 1")
        count = int(rule["COUNT"]) if rule.get("COUNT") else None
        until = _dt(rule["UNTIL"]) if rule.get("UNTIL") else None
        # Combinations this expander would answer WRONGLY rather than not at
        # all (a 12k-rule fuzz against python-dateutil): refused and counted.
        # None of the 308 live rules on the four configured sites uses them.
        ordinal = any(n is not None for n, _ in _byday(rule.get("BYDAY")))
        if freq == "YEARLY" and rule.get("BYMONTHDAY") and not rule.get("BYMONTH"):
            raise RuleError("YEARLY BYMONTHDAY without BYMONTH not supported")
        if ordinal and (rule.get("BYMONTHDAY") or freq not in ("MONTHLY", "YEARLY")):
            raise RuleError("an ordinal BYDAY with BYMONTHDAY, or outside MONTHLY/YEARLY, not supported")
        if freq == "WEEKLY" and rule.get("BYSETPOS"):
            raise RuleError("WEEKLY with BYSETPOS not supported")
        stop = min(until_bound, until) if until else until_bound
        made = 0
        if freq == "HOURLY":
            if any(k in rule for k in ("BYDAY", "BYMONTH", "BYMONTHDAY", "BYSETPOS")):
                raise RuleError("HOURLY with BY* parts not supported")
            t = dtstart
            while t <= stop and (count is None or made < count):
                occ.add(t)
                made += 1
                t += timedelta(hours=interval)
        else:
            if freq == "DAILY":
                anchor = dtstart.date()
            elif freq == "WEEKLY":
                anchor = dtstart.date() - timedelta(days=dtstart.weekday())   # WKST=MO
            elif freq == "MONTHLY":
                anchor = dtstart.date().replace(day=1)
            elif freq == "YEARLY":
                anchor = date(dtstart.year, 1, 1)
            else:
                raise RuleError(f"FREQ={freq} not supported")
            done = False
            for _ in range(MAX_PERIODS):
                for d in _period_dates(freq, anchor, rule, dtstart):
                    t = datetime.combine(d, dtstart.time())
                    if t < dtstart:
                        continue
                    if t > stop or (count is not None and made >= count):
                        done = True
                        break
                    occ.add(t)
                    made += 1
                if done or datetime.combine(anchor, dtstart.time()) > stop:
                    break
                if freq == "DAILY":
                    anchor += timedelta(days=interval)
                elif freq == "WEEKLY":
                    anchor += timedelta(weeks=interval)
                elif freq == "MONTHLY":
                    m = anchor.month - 1 + interval
                    anchor = date(anchor.year + m // 12, m % 12 + 1, 1)
                else:
                    anchor = date(anchor.year + interval, 1, 1)
    ex = set(block.get("exdates") or [])
    return sorted(t for t in occ if t not in ex)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def clean_text(s: Any, limit: int = 1500) -> str:
    """Revize percent-encodes `desc` and HTML-escapes titles ("&#8211;")."""
    if not isinstance(s, str) or not s.strip():
        return ""
    t = unquote(s) if re.search(r"%[0-9A-Fa-f]{2}", s) else s
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(_html.unescape(t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t[:limit].rstrip()


_NO_PROGRAM_RX = re.compile(
    r"\bno\s+(?:program|programs|class|classes|session|sessions|meeting|meetings|club|practice)\b"
    r"(?:\s+on)?\s*:?\s*((?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:\s*(?:,|and|&)\s*)*)+)", re.I)


def no_program_days(desc: str) -> Set[Tuple[int, int]]:
    """Lesson 3: (month, day) pairs the text says have no session."""
    out: Set[Tuple[int, int]] = set()
    for m in _NO_PROGRAM_RX.finditer(desc or ""):
        for mm, dd in re.findall(r"(\d{1,2})/(\d{1,2})", m.group(1)):
            if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
                out.add((int(mm), int(dd)))
    return out


# ---------------------------------------------------------------------------
# Lesson 5 - what is not an event
# ---------------------------------------------------------------------------
REFUSALS: List[Tuple[str, "re.Pattern[str]", str]] = [
    # (reason, pattern, read on: "title" | "text" (title + description))
    ("cancelled", re.compile(r"^\W*no meeting\b|\bcancel+ed\b|\bpostponed\b", re.I), "title"),
    ("closure", re.compile(
        r"\b(?:offices?|city hall|town hall|campus|facilit(?:y|ies)|buildings?|centers?|centres?)\b"
        r"(?:\s+(?:and|&)\s+\w+)?\s+(?:will\s+be\s+|are\s+|is\s+)?closed\b|\bclosed\s+for\b|"
        r"\bholidays?\s*(?:observance)?\s*$", re.I), "text"),
    ("governance", re.compile(
        r"\b(?:committee|commission|council|board(?!\s+games?)|authority|work\s+session|hearing|"
        r"advisory|interagency|legislative|recess|appointments)\b|^friends of .* meeting$|"
        r"\b(?:business|homeowners?|civic)\s+association\b", re.I), "title"),
    ("civic service, not an event", re.compile(
        r"\bdeadline\b|\b(?:early|absentee|sunday|saturday|in[- ]person)\s+voting\b|^voting\b|"
        r"\belection\s+day\b|\bdrop[- ]?off\b|\baccepting\s+submissions\b|\bbills?\s+due\b|"
        r"\bsticker\s+design\s+contest\b|\btax\s+(?:preparation|prep|help|assistance)\b|"
        r"\b(?:recycling|shred(?:ding)?)\s+(?:event|day|collection)\b", re.I), "title"),
    ("take-home kit, not a gathering", re.compile(
        r"\bpick\s*up\b[^.]{0,60}\bkits?\b|\btake[- ]home\s+kits?\b", re.I), "text"),
    ("members only", re.compile(r"\bmembers?[- ]only\b|\bfor\s+members\s+only\b", re.I), "text"),
    ("registration required", re.compile(
        r"\b(?:pre-?)?registration\s+(?:is\s+)?(?:required|mandatory|only)\b|"
        r"\bregistration\b[^.!?;\n]{0,60}\brequired\b|\bsign[- ]?ups?\s+(?:is\s+)?required\b|"
        r"\bmust\s+(?:pre-?)?register\b|\bby\s+registration\s+only\b|\bregistered\s+participants\s+only\b",
        re.I), "text"),
]
# Said in the town's words, these are the OPPOSITE of a registration-only
# course; they are blanked before the pattern above reads the text.
_NOT_REQUIRED_RX = re.compile(
    r"\b(?:no\s+(?:pre-?)?registration(?:\s+(?:is\s+)?(?:required|needed|necessary))?|"
    r"(?:pre-?)?registration\s+(?:is\s+)?not\s+(?:required|needed|necessary)|"
    r"(?:pre-?)?registration\s+(?:is\s+)?(?:encouraged|recommended|appreciated|optional|suggested)|"
    r"no\s+sign[- ]?ups?\s+(?:is\s+)?(?:required|needed|necessary))\b", re.I)
# A library's teen club is called an advisory group; it is a programme, not governance.
_NOT_GOVERNANCE_RX = re.compile(r"\bteen\s+advisory\s+(?:group|board|council)\b", re.I)
# "Pick up a kit ... Make it there or take it home!" is a gathering with a kit.
_KIT_ON_SITE_RX = re.compile(r"\bmake\s+it\s+(?:there|here|on[- ]site)\b", re.I)


def refusal(title: str, desc: str, site: Dict[str, Any]) -> Optional[str]:
    text = f"{title}\n{desc}"
    extra = site.get("skip_title")
    if extra and re.search(extra, title or "", re.I):
        return "skip_title (config)"
    for reason, rx, on in REFUSALS:
        hay = title if on == "title" else text
        if reason == "registration required":
            hay = _NOT_REQUIRED_RX.sub(" ", hay)
        elif reason == "governance":
            hay = _NOT_GOVERNANCE_RX.sub(" ", hay)
        elif reason == "take-home kit, not a gathering" and _KIT_ON_SITE_RX.search(text):
            continue
        if rx.search(hay or ""):
            return reason
    if looks_online_only(title, desc):
        return "online only"
    return None


# ---------------------------------------------------------------------------
# Lesson 6 - price wording
# ---------------------------------------------------------------------------
_MONEY_RX = re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
_SAYS_FREE_RX = re.compile(r"(?<![-\w])free(?![-\w])", re.I)
_FREE_NOT_PRICE_RX = re.compile(
    r"\bland\s+of\s+the\s+free\b|\bfeel\s+free\b|\bfree\s+(?:play|time|throw|throws|style|form|will|"
    r"speech|range|parking|wi-?fi|refreshments|snacks|food|coffee|t-?shirts?|gifts?|giveaways?|"
    r"samples?|swag|of\s+(?:clutter|debris|litter))\b|"
    r"\b(?:gluten|sugar|smoke|drug|alcohol|tobacco|nut|dairy|scent|hands|toll|tax|fragrance)\s+free\b|"
    r"\bfree\s+(?:for\s+)?(?:\w+\s+){0,2}(?:students?|members?|youth|kids|children|seniors|veterans|"
    r"military|residents|under\s*\d+)\b|\b(?:students?|members?|kids|children|seniors|under\s*\d+s?)\s+"
    r"(?:are\s+|get\s+in\s+)?free\b", re.I)


# An amount is a price to attend unless its own words say it is something else:
# "$3 off", "a $75,000 Our Town award", "earning less than $69,000", "art valued
# at $15-30", "$4.9 million". Read on the 40 characters before and 30 after.
_AMOUNT_NOT_PRICE_BEFORE = re.compile(
    r"\b(?:earn(?:ing|s)?|income|less\s+than|more\s+than|up\s+to|valued\s+at|value\s+of|worth|"
    r"rais(?:e|ed|ing)|receive[ds]?|receiving|award(?:ed)?|win|won|sav(?:e|es|ing)|prizes?\s+of|"
    r"grants?\s+of|donations?\s+of|budget\s+of|fines?\s+of|parking(?:\s+is)?)\s*"
    r"(?:approximately|about|nearly|almost|over)?\s*\(?$", re.I)
_AMOUNT_NOT_PRICE_AFTER = re.compile(
    r"^[\d.,]*\s*(?:-\s*\$?[\d.,]+\s*)?(?:off\b|awards?\b|grants?\b|prizes?\b|scholarships?\b|million\b|"
    r"billion\b|in\s+(?:prizes|grants|funding|scholarships|awards|savings|value)\b|worth\b|value\b|"
    r"raised\b|budget\b|funding\b|cash\s+prize|gift\s+cards?\b|discount\b|rebate\b|stipend\b|"
    r"(?:for\s+)?parking\b)", re.I)
# The town saying the EVENT is free - not a seat, not a child, not a vendor.
_EVENT_FREE_RX = re.compile(
    r"\b(?:th(?:is|e)\s+)?event\s+is\s+(?:\w+\s+)?free\b|"
    r"\bfree\s*(?:and|&)\s*open\s+to\s+(?:the\s+)?(?:public|all|everyone)\b|"
    r"\bfree\s+(?:to|for)\s+(?:the\s+)?(?:public|all|everyone|attend)\b|"
    r"\bfree\s+admission\b|\badmission\s+(?:is\s+)?free\b|\bopen\s+to\s+the\s+public\s*(?:and|&)\s*free\b", re.I)
# ...and an amount in the same sentence as these words is the exhibitors' or
# vendors' fee, not the visitor's (Bladensburg's Auto Showcase: "This event is
# FREE to the public. A $20 registration fee ... for showcase participants").
_PARTICIPANT_RX = re.compile(
    r"\b(?:participants?|participating|vendors?|exhibitors?|showcase|entrants?|competitors?|"
    r"contestants?|booths?|sponsors?|registrants?|crafters|artisans)\b", re.I)
_TICKET_WORD_RX = re.compile(r"\b(?:admission|tickets?|entry|entrance|cover\s+charge)\b", re.I)


def _clause(text: str, pos: int) -> str:
    """The sentence around `pos` ('.' between digits is a decimal, not an end)."""
    ends = [m.end() for m in re.finditer(r"(?<!\d)[.;!?](?!\d)|\n", text)]
    lo = max([e for e in ends if e <= pos], default=0)
    hi = min([e for e in ends if e > pos], default=len(text))
    return text[lo:hi]


def price_amounts(text: str) -> List[str]:
    """Every amount the text asks a visitor to pay, as '$25', in order."""
    event_free = bool(_EVENT_FREE_RX.search(text))
    out: List[str] = []
    for m in _MONEY_RX.finditer(text):
        if _AMOUNT_NOT_PRICE_BEFORE.search(text[max(0, m.start() - 40):m.start()]) \
                or _AMOUNT_NOT_PRICE_AFTER.search(text[m.end():m.end() + 30]):
            continue
        clause = _clause(text, m.start())
        if event_free and (_PARTICIPANT_RX.search(clause) or not _TICKET_WORD_RX.search(clause)):
            continue                                     # the extras of a free event
        v = m.group(1).replace(",", "")
        if float(v) > 0 and f"${v}" not in out:
            out.append(f"${v}")
    return out


def price_line(title: str, desc: str) -> Tuple[Optional[str], Optional[bool]]:
    """(line, is_free): free only in the town's own words, never with a price."""
    text = f"{title}\n{desc}"
    positive = price_amounts(text)
    if positive:
        return f"🎟 Price as listed: {', '.join(positive[:4])} (not free).", False
    if _SAYS_FREE_RX.search(_FREE_NOT_PRICE_RX.sub(" ", text)):
        return "🎟 Admission: free.", True
    return None, None


# ---------------------------------------------------------------------------
# Lesson 7 - placement
# ---------------------------------------------------------------------------
_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY"}
_STATE_CODES = set(_US_STATES.values())
_STATE_CODE_TAIL_RX = re.compile(r"(,\s*|\s+)([A-Za-z]{2})\.?(?:\s+(\d{5})(?:-\d{4})?)?\s*$")
_STATE_NAME_TAIL_RX = re.compile(
    r",\s*(" + "|".join(sorted((re.escape(n) for n in _US_STATES), key=len, reverse=True)) +
    r")\.?(?:\s+(\d{5})(?:-\d{4})?)?\s*$", re.I)


_STATE_NAME_END_RX = re.compile(
    r"\b(?:" + "|".join(sorted((re.escape(n) for n in _US_STATES), key=len, reverse=True)) + r")\.?\s*$", re.I)


def _state_tail(loc: str, site_region: Optional[str] = None):
    """(start, code, zip) when the location ENDS in a US state, else None.

    A trailing two-letter token is a state only when a ZIP follows it, when it
    is the site's own state, or when a comma sets it apart - never when it is a
    street quadrant: Olympia writes "2600 East Bay Drive NE" and "222 Columbia
    St NW", and reading NE as Nebraska sent the 2026-10-04 build's query to
    Massapequa NY without the town's suffix. A lower-case pair counts only
    after a comma ("Manassas, Va."), so "Mayfield Ct" is never Connecticut."""
    m = _STATE_NAME_TAIL_RX.search(loc)
    if m and m.start() > 0:
        return m.start(), _US_STATES[m.group(1).lower()], m.group(2)
    m = _STATE_CODE_TAIL_RX.search(loc)
    if not m or m.start() == 0:
        return None
    code, comma, postal = m.group(2), "," in m.group(1), m.group(3)
    uc = code.upper()
    if uc not in _STATE_CODES or (not comma and not code.isupper()):
        return None
    if postal or uc == (site_region or "").upper() or (comma and uc != "NE"):
        return m.start(), uc, postal
    return None


def split_location(loc: str, town: Optional[str] = None,
                   site_region: Optional[str] = None) -> Dict[str, Optional[str]]:
    """'Harris Pavilion, 9201 Center St, Manassas, VA 20110' ->
    venue 'Harris Pavilion', street '9201 Center St', city, region, postal.

    The town is the last comma part before the state, or - when the town
    writes no comma ("9431 West St. Manassas, VA 20110") - the site's own town
    name at the end of a street part. "Downtown Manassas" stays a venue."""
    loc = re.sub(r"\s+", " ", (loc or "").replace("\n", ", ")).strip(" ,")
    out: Dict[str, Optional[str]] = {"venue": None, "street": None, "city": None,
                                     "region": None, "postal": None}
    if not loc:
        return out
    rest = loc
    tail = _state_tail(rest, site_region)
    if tail:
        out["region"], out["postal"] = tail[1], tail[2]
        rest = rest[:tail[0]].strip(" ,")
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    if len(parts) >= 2 and not re.search(r"\d", parts[-1]) and \
            (out["region"] or (town and parts[-1].lower() == town.lower())):
        out["city"] = parts.pop()
    elif parts and town:
        last = parts[-1]
        if re.match(r"\d", last) and re.search(rf"\s{re.escape(town)}$", last, re.I):
            out["city"] = town
            parts[-1] = last[:-len(town)].strip(" ,")
        elif len(parts) == 1 and re.search(rf"\b{re.escape(town)}\b", last, re.I):
            out["city"] = town
    street_i = next((i for i, p in enumerate(parts) if re.match(r"\d", p)), None)
    if street_i is not None:
        out["street"] = parts[street_i]
        if street_i:
            out["venue"] = ", ".join(parts[:street_i])
    elif parts:
        out["venue"] = parts[0]
    return out


# Words that name no single place ("Various Locations in Bladensburg").
_VAGUE_WORDS = {"various", "multiple", "several", "many", "different", "locations", "location",
                "sites", "venues", "places", "citywide", "townwide", "throughout", "around",
                "across", "all", "over", "in", "the", "and", "tbd", "tba", "to", "be",
                "determined", "announced"}
# A district, not a building: "Downtown Olympia" was answered by Photon with a
# memorial in TACOMA, and "Downtown Manassas" with a post office. A district a
# town uses often gets a deliberate point in its venue book instead.
_DISTRICT_RX = re.compile(r"^\s*(?:historic\s+)?(?:downtown|uptown|midtown|old\s+town)\b[^\d]*$", re.I)


def unplaceable(loc: str, site: Dict[str, Any]) -> Optional[str]:
    """Why a location names no place a geocoder should be asked for, or None.

    'Manassas, VA 20110', 'bladensburg, maryland' and 'Pacific' name the town;
    'Various Locations in Bladensburg' names nowhere; 'Downtown Olympia' names a
    district. Each is counted as unplaced, never pinned by a guess."""
    s = re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", loc or "")
    s = _STATE_NAME_END_RX.sub(" ", s)
    s = re.sub(r"\b[A-Z]{2}\b|\b(?:usa|us)\b", " ", s, flags=re.I)
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", s)}
    town = {w.lower() for w in re.findall(r"[A-Za-z]+", site.get("city") or "")}
    rest = words - town - {"city", "of", "town"}
    if words and not rest:
        return "location names only the town"
    if rest and rest <= _VAGUE_WORDS:
        return "location names no single place"
    if _DISTRICT_RX.search(re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", loc or "")):
        return "location names a district, not a place"
    return None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    a = sin(radians(lat2 - lat1) / 2) ** 2 + \
        cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


DEFAULT_RADIUS_KM = 25.0


def too_far(site: Dict[str, Any], lat: float, lon: float) -> Optional[float]:
    """The distance in km when a GEOCODED point is outside the town's radius.

    Photon answers its best text match anywhere on earth: 'Gymnasium, Pacific,
    WA' is the centroid of Pacific COUNTY (140 km off), 'Downtown Olympia' a
    memorial in Tacoma (40 km). A town calendar's events are in or near the
    town; an answer further than `radius_km` (default 25) from `centre` is not
    believed, and the row is unplaced."""
    c = site.get("centre")
    if not c:
        return None
    d = _km(float(c[0]), float(c[1]), lat, lon)
    return d if d > float(site.get("radius_km") or DEFAULT_RADIUS_KM) else None


def book_venue(loc: str, site: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for v in site.get("venues") or ():
        if v.get("match") and re.search(v["match"], loc or "", re.I):
            return v
    return None


# ---------------------------------------------------------------------------
# Rows -> events
# ---------------------------------------------------------------------------
def _tz(name: Optional[str]):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:                                   # noqa: BLE001
        return None


def _now_local(tz) -> datetime:
    """MAPSEE_TODAY=YYYYMMDD fixes "now" (that day's midnight) for the tests."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d")
    return datetime.now(tz).replace(tzinfo=None) if tz else datetime.now()


def _to_utc(dt: Optional[datetime], tz) -> Optional[str]:
    if dt is None or tz is None:
        return None
    return dt.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return datetime.fromisoformat(s.strip()[:19])
    except ValueError:
        return None


def site_slug(site: Dict[str, Any]) -> str:
    return site.get("key") or re.sub(r"[^a-z0-9]+", "-", (site.get("name") or "").lower()).strip("-")


def handler_url(site: Dict[str, Any]) -> str:
    if site.get("url"):
        return site["url"]
    origin = site["origin"].rstrip("/")
    return (f"{origin}/_assets_/plugins/revizeCalendar/calendar_data_handler.php"
            f"?webspace={site['webspace']}&relative_revize_url={site['revize_base']}&protocol=https:")


def event_link(row: Dict[str, Any], site: Dict[str, Any]) -> Optional[str]:
    """The row's own link when it is a page, never a map; else the calendar page."""
    u = (row.get("url") or "").strip()
    if u and not re.search(r"(?:google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|"
                           r"bing\.com/maps|apple\.com/maps)", u, re.I):
        if u.lower().startswith("www."):
            u = "https://" + u
        if re.match(r"https?://[^\s/]+\.[^\s/]+", u):
            return u[:500]
    return site.get("calendar_page")


def occurrences(row: Dict[str, Any], tz, lo: datetime, hi: datetime,
                stats: Dict[str, int]) -> List[Tuple[datetime, Optional[datetime]]]:
    """(start, end) wall-clock pairs inside [lo, hi). A dated row is one pair."""
    start = _iso(row.get("start"))
    if start is None:
        stats["no start"] = stats.get("no start", 0) + 1
        return []
    end = _iso(row.get("end"))
    dur = (end - start) if end and end >= start else None
    if dur is None and re.fullmatch(r"\d{1,2}:\d{2}", str(row.get("duration") or "")):
        h, m = str(row["duration"]).split(":")
        dur = timedelta(hours=int(h), minutes=int(m))
    if row.get("rrule"):
        try:
            block = parse_rule_block(row["rrule"], tz)
            starts = expand_rule(block, hi)
        except (RuleError, ValueError) as exc:
            stats["rule not understood"] = stats.get("rule not understood", 0) + 1
            print(f"[revize]   rule not understood on row {row.get('id')}: {exc}", flush=True)
            return []
    else:
        starts = [start]
    out = []
    for s in starts:
        e = s + dur if dur is not None else None
        if (e or s) < lo or s >= hi:
            continue
        out.append((s, e))
    return out


def closure_days(site: Dict[str, Any], rows: List[Dict[str, Any]], tz, lo: datetime,
                 hi: datetime) -> Dict[date, str]:
    """{day: the closure row's title} for every day inside [lo, hi) that a row
    the town files as a closure ("Christmas Holidays ... all City offices
    closed") covers, from ANY calendar of the site - a closure is the town's
    fact whichever calendar it is filed on."""
    out: Dict[date, str] = {}
    for r in rows:
        t = clean_text(r.get("title"), 200).replace("\n", " ")
        if not t or refusal(t, clean_text(r.get("desc")), site) != "closure":
            continue
        for s, e in occurrences(r, tz, lo, hi, {}):
            last = e if e is not None and e > s else s
            if last.hour == 0 and last.minute == 0 and last.date() > s.date():
                last -= timedelta(minutes=1)               # ends at midnight: not that day
            d = s.date()
            while d <= last.date():
                out.setdefault(d, t)
                d += timedelta(days=1)
    return out


def build_events(site: Dict[str, Any], rows: List[Dict[str, Any]], now_local: datetime,
                 horizon_end: datetime, geocode: Optional[Callable[[str], Tuple[Any, Any]]] = None
                 ) -> Tuple[List[NormalizedEvent], Dict[str, int], Dict[str, int]]:
    """(events, refused-by-reason, notes). Pure apart from `geocode`."""
    tz = _tz(site.get("timezone"))
    slug = site_slug(site)
    cals: Dict[str, str] = site.get("calendars") or {}
    fixes = {str(f["id"]): f for f in site.get("clock_fixes") or () if f.get("id")}
    refused: Dict[str, int] = {}
    notes: Dict[str, int] = {}
    events: List[NormalizedEvent] = []
    seen_ids: Set[str] = set()

    def refuse(why: str, n: int = 1) -> None:
        refused[why] = refused.get(why, 0) + n

    def note(why: str, n: int = 1) -> None:
        notes[why] = notes.get(why, 0) + n

    # Lesson 9 - the town's own closure rows, applied to the sessions it runs.
    host_cals = set(site.get("host_calendars") or ())
    not_host = site.get("not_host_title")
    closed = closure_days(site, rows, tz, now_local, horizon_end) if host_cals else {}
    by_title = [(re.compile(rx, re.I), lens) for rx, lens in site.get("category_by_title") or ()]

    for row in rows:
        cal = row.get("primary_calendar_name") or ""
        if cal not in cals:
            continue                                     # counted by the caller per calendar
        rid = str(row.get("id") or row.get("rid") or "")
        if rid and rid in seen_ids:
            note("row repeated in the feed")
            continue
        seen_ids.add(rid)
        title = clean_text(row.get("title"), 200).replace("\n", " ")
        desc = clean_text(row.get("desc"))
        if not title:
            refuse("no title")
            continue
        # Clock fix (lesson 4) - before expansion, on the row and its rule alike.
        fix = fixes.get(rid)
        if fix:
            st = _iso(row.get("start"))
            if st is not None and st.strftime("%H:%M") == fix.get("from"):
                h, m = map(int, fix["to"].split(":"))
                new = st.replace(hour=h, minute=m)
                row = dict(row, start=new.isoformat())
                if row.get("rrule"):
                    row["rrule"] = re.sub(r"(?m)^(DTSTART|RDATE|EXDATE)(:\d{8}T)" + st.strftime("%H%M%S"),
                                          lambda mt: mt.group(1) + mt.group(2) + new.strftime("%H%M%S"),
                                          row["rrule"])
                note(f"clock fixed by config (row {rid}: {fix['from']} -> {fix['to']})")
        occ = occurrences(row, tz, now_local, horizon_end, notes)
        if not occ:
            continue
        why = refusal(title, desc, site)
        if why:
            refuse(why, len(occ))
            continue
        loc_raw = clean_text(row.get("location"), 300).replace("\n", ", ")
        if loc_raw and re.search(r"\b(?:zoom|online|virtual|webinar|teams)\b", loc_raw, re.I) \
                and not re.search(r"\d", loc_raw) and not re.search(r"\b(?:and|&|\+)\b", loc_raw):
            refuse("online only", len(occ))
            continue
        skip_days = no_program_days(desc)
        all_day = bool(row.get("allDay"))
        parts = split_location(loc_raw, site.get("city"), site.get("region"))
        venue = book_venue(loc_raw, site)
        lat = lon = None
        exact = False
        why = None
        if venue and venue.get("lat") is not None:
            lat, lon, exact = float(venue["lat"]), float(venue["lon"]), bool(venue.get("coords_exact"))
        elif not loc_raw:
            why = "no location"
        else:
            # A book entry without a pin may name the query that finds it
            # ("Gymnasium" in Pacific is the community centre's gym).
            query = (venue or {}).get("query")
            why = None if query else unplaceable(loc_raw, site)
            if why is None:
                lat, lon = geocode(query or loc_raw) if geocode is not None else (None, None)
                if lat is None or lon is None:
                    why = "location not found by the geocoder"
                else:
                    km = too_far(site, lat, lon)
                    if km is not None:
                        lat = lon = None
                        why = "the geocoder answered outside the town's radius"
                        note(f"  far answer for {loc_raw[:60]!r}: {km:.0f} km", len(occ))
        if lat is None or lon is None:
            note(f"unplaced: {why}", len(occ))
            continue
        venue_name = (venue or {}).get("name") or parts["venue"] or parts["street"] or loc_raw[:120]
        street = (venue or {}).get("address") or parts["street"]
        city = (venue or {}).get("city") or parts["city"] or site.get("city")
        region = (venue or {}).get("region") or parts["region"] or site.get("region")
        postal = (venue or {}).get("postal_code") or parts["postal"]
        fee, _free = price_line(title, desc)
        head = [x for x in (fee,) if x]
        paras = ["\n".join(head)] if head else []
        if desc:
            paras.append(desc)
        paras.append(f"From the {cal} calendar of {site.get('name')}.")
        description = "\n\n".join(paras)
        category = cals.get(cal) or site.get("category") or "community"
        for rx, lens in by_title:                        # lesson 10: the title names the door
            if rx.search(title):
                category = lens
                break
        is_host = cal in host_cals and not (not_host and re.search(not_host, title, re.I))
        for s, e in occ:
            if (s.month, s.day) in skip_days:
                note("skipped: the description says no session that day")
                continue
            if is_host and s.date() in closed:
                note(f"skipped: the town is closed that day ({closed[s.date()]})")
                continue
            day_iso = s.strftime("%Y-%m-%d")
            whole_day = all_day or (s.strftime("%H:%M") == "00:00" and e is not None
                                    and e.strftime("%H:%M") >= "23:55")
            if not whole_day and e is not None:
                span_h = (e - s).total_seconds() / 3600
                if SPAN_REFUSE_HOURS <= span_h < 24:
                    refuse(f"a {SPAN_REFUSE_HOURS}-24 h span, not a session")
                    continue
            if whole_day:
                start_local, start_utc, end_utc = day_iso, None, None
                end_local = e.strftime("%Y-%m-%d") if e is not None and e.date() > s.date() else None
                hm = "allday"
            else:
                start_local = s.strftime("%Y-%m-%dT%H:%M:%S")
                end_local = e.strftime("%Y-%m-%dT%H:%M:%S") if e is not None and e > s else None
                start_utc = _to_utc(s, tz)
                end_utc = _to_utc(e, tz) if end_local else None
                hm = s.strftime("%H:%M")
            ev = NormalizedEvent(
                source=f"revize:{slug}",
                source_id=f"{slug}:{rid}:{s.strftime('%Y%m%dT%H%M')}"[:200],
                name=title,
                description=description,
                start_local=start_local, start_utc=start_utc,
                end_local=end_local, end_utc=end_utc,
                timezone=site.get("timezone"),
                venue_name=venue_name,
                latitude=lat, longitude=lon,
                address=street, city=city, region=region,
                country=site.get("country") or "US", postal_code=postal,
                category=category,
                promoter=site.get("name") if is_host else None,
                ticket_url=event_link(row, site),
                coords_exact=exact,
            )
            ev.fingerprint = make_fingerprint(f"{title} {hm}" if hm != "allday" else title,
                                              day_iso, venue_name, city)
            events.append(ev)
    return events, refused, notes


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
class Refused(Exception):
    pass


def fetch_site(session, robots, site: Dict[str, Any], deadline: Optional[float]) -> List[Dict[str, Any]]:
    url = handler_url(site)
    if deadline is not None and time.monotonic() >= deadline:
        raise Refused("deadline reached before the request")
    verdict = robots.check(url)
    if verdict.get("allowed") is not True:
        raise Refused(f"robots.txt ({verdict.get('status')}): {verdict.get('rule') or 'not permitted'}")
    time.sleep(max(MIN_GAP_SECONDS, float(verdict.get("crawl_delay") or 0)))
    r = session.get(url, timeout=60, allow_redirects=False)
    if r.status_code in (401, 403, 429):
        raise Refused(f"HTTP {r.status_code} - a refusal, not retried")
    if r.status_code in (301, 302, 303, 307, 308):
        raise Refused(f"HTTP {r.status_code} to {r.headers.get('location', '?')} - not followed; "
                      "update the config after checking that host's robots.txt")
    r.raise_for_status()
    body = r.text.lstrip()
    if not body.startswith("["):
        raise Refused("not a JSON list (a challenge page or a moved plugin)")
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise Refused("not a JSON list")
    return rows


def make_geocoder(session, site: Dict[str, Any]):
    """mapsee_ingest_ics's geocoder (its cache, its budget, its attempts). The
    text is sent as written when it ends in a state; with ", <state>" when it
    names its town but no state ("2600 East Bay Dr NE, Olympia"); with the
    site's whole suffix otherwise. build_events disbelieves a far answer."""
    from mapsee_ingest_ics import make_location_geocoder
    bare = make_location_geocoder(session, "")
    region = site.get("region")
    region_only = make_location_geocoder(session, f", {region}") if region else bare
    suffixed = make_location_geocoder(session, site.get("geocode_suffix") or "")

    def geocode(loc: str):
        p = split_location(loc, site.get("city"), region)
        return (bare if p["region"] else region_only if p["city"] else suffixed)(loc)
    return geocode


def with_defaults(site: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """A site's own `category_by_title` first, then the config-wide one."""
    rules = list(site.get("category_by_title") or []) + list(cfg.get("category_by_title") or [])
    return dict(site, category_by_title=rules)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import town calendars served by the Revize CMS calendar plugin.")
    ap.add_argument("--config", default="revize_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one site by key or name (substring)")
    ap.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    ap.add_argument("--dump", help="dry run: write the would-be rows to this JSON file")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    sites = [with_defaults(s, cfg) for s in cfg.get("sites", []) if not s.get("skip") and
             (not a.only or a.only.lower() in f"{s.get('key', '')} {s.get('name', '')}".lower())]
    if not sites:
        print("[revize] no sites selected", flush=True)
        return 0
    from robots_txt import Robots
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = Robots(session)
    store = None if a.dry_run else EventStore(a.store)
    kept_total, failed, dumped = 0, 0, []
    for site in sites:
        label = site.get("name") or site_slug(site)
        try:
            rows = fetch_site(session, robots, site, deadline)
        except Refused as exc:
            print(f"[revize] {label}: SKIPPED - {exc}", flush=True)
            continue
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            print(f"[revize] {label}: FAILED - {type(exc).__name__}: {exc}", flush=True)
            continue
        try:
            tz = _tz(site.get("timezone"))
            now_local = _now_local(tz)
            horizon_end = now_local + timedelta(days=int(site.get("horizon_days") or a.horizon_days))
            events, refused, notes = build_events(site, rows, now_local, horizon_end,
                                                  make_geocoder(session, site))
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            print(f"[revize] {label}: FAILED building rows - {type(exc).__name__}: {exc}", flush=True)
            continue
        by_cal: Dict[str, int] = {}
        for r in rows:
            by_cal[r.get("primary_calendar_name") or "?"] = by_cal.get(r.get("primary_calendar_name") or "?", 0) + 1
        read = {c: n for c, n in by_cal.items() if c in (site.get("calendars") or {})}
        unread = {c: n for c, n in by_cal.items() if c not in read}
        print(f"[revize] {label}: {len(rows)} row(s); read {read}; not read {unread}", flush=True)
        if refused:
            print(f"[revize] {label}: refused " + ", ".join(
                f"{n} {w}" for w, n in sorted(refused.items(), key=lambda kv: -kv[1])), flush=True)
        for w, n in sorted(notes.items(), key=lambda kv: -kv[1]):
            print(f"[revize] {label}: {n} {w}", flush=True)
        print(f"[revize] {label}: kept {len(events)} occurrence(s)", flush=True)
        kept_total += len(events)
        if store is not None:
            for ev in events:
                store.upsert(ev)
            store.save()                                 # after each site: a cancelled step keeps the work
        elif a.dump:
            from dataclasses import asdict
            dumped.extend(asdict(ev) for ev in events)
    if store is not None:                               # a dry run leaves the shared cache alone
        try:
            from mapsee_ingest_ics import _GEO_CACHE, _save_geo_cache
            _save_geo_cache(_GEO_CACHE)
        except Exception as exc:                        # noqa: BLE001
            print(f"[revize] geocode cache not saved: {type(exc).__name__}: {exc}", flush=True)
    took = (time.monotonic() - started) / 60
    if store is not None:
        print(f"[revize] wrote {kept_total} occurrence(s) from {len(sites)} site(s) in {took:.1f} min; "
              f"store now holds {len(store.records)} unique events.", flush=True)
    else:
        if a.dump:
            with open(a.dump, "w", encoding="utf-8") as fh:
                json.dump(dumped, fh, ensure_ascii=False, indent=1)
        print(f"[revize] dry run - {kept_total} occurrence(s) would be written; {took:.1f} min", flush=True)
    # Exit 1 only when EVERY selected site failed outright, as the siblings do:
    # one town being down is a quiet day, all of them is a broken adapter.
    return 1 if failed and failed == len(sites) else 0


if __name__ == "__main__":
    raise SystemExit(main())
