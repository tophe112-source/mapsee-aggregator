#!/usr/bin/env python3
"""
mapsee_ingest_annual.py — an annual event whose own site states its date only
in a sentence.

WHAT IS MISSING WITHOUT THIS. Short Run Comix Festival (shortrun.org), Seattle's
free one-day comics fair, publishes its date in exactly one place: the hero of
its homepage. Read 2026-11-07, the raw HTML is

    <h1 class="page-title">Short Run Comix Festival</h1>
    <p>Our 14th annual festival is coming up <strong>Saturday, November 7th,
    2026</strong> at Seattle Design Center. 11am &#8211; 6 pm. FREE!</p>

and that is all there is: no Event JSON-LD, no iCal, no calendar plugin, so every
verifier in catalog_curate rejects the site and nothing in the catalog could
ever carry the festival. mapsee_ingest_fairs reads a page the same way (a
marketing site whose date is its headline) but only date RANGES, and on Short
Run's three saved pages it answers "no date range on the page".

WHY A SEPARATE ADAPTER AND NOT A WIDER FAIRS ONE. Measured over the 45 state
fair homepages read 2026-11-07: letting single days in gave THREE false fairs
(Maryland's "Sun, Nov 08", Washington's "Saturday, Nov 7", Wyoming's "Board
Meeting Notice Nov. 10, 2026" — a fair's site is a fairground's site, and the
fairground has a calendar), and the rule that makes single days safe here — the
event's NAME near its date — loses five correct fairs (Colorado, New Jersey,
West Virginia, Idaho, Nebraska name themselves too far from their hero). So the
fairs adapter stays a ranges reader and this one IMPORTS its pieces: RANGE_RX,
parse_ranges' arithmetic, NOT_THE_FAIR_RX, the two-years-in-play refusal
(_YEAR_BEFORE_RX), _mk and the MAX_* limits, plus Polite, the one door its
requests go through. A fix to any of them is a fix to both.

TWO SHAPES, chosen per entry in annual_sources.json:

  * "day" — the site IS the event's own (Short Run). Single days count ("Saturday,
    November 7th, 2026", "Sat, Nov. 7", "November 7 & 8, 2026") as well as
    ranges, two dated days joined only by &, and, +, a dash, to or through are
    one range (JOIN_RX), and `name_rx` is REQUIRED: a date is believed only
    with the event's name within NAME_BEFORE characters before it or in its
    own paragraph. On Short Run's programming page the pre-party, the
    after-party and Sunday's panel all carry a date, a clock and "FREE"; with
    the name rule the festival is the only one left.
    `name_rx` NAMES THE EVENT, NOT THE ORGANISER: `short\\s+run\\s+comix`, not
    `short\\s+run`. Everything on an organiser's homepage says the organiser's
    name, and the ranking below puts the nearest named date first, so with
    `short\\s+run` one dated post beat the hero (measured 2026-11-08 on the
    saved homepage with one excerpt inserted: "Kick off Short Run at the
    Marathon Art Show, Friday, November 6th, 2026" gave 11-06, a Spring Market
    post 2027-05-15, a Sunday panel "presented with Short Run" 11-08, and a
    "Short Run's 11th annual Trailer Blaze comics residency runs April 4 – 18,
    2027" post 2027-04-04..04-18 on 11-01, 11-08 and 2027-02-01 alike - each
    written as the festival at the Design Center). With the festival's own name
    all four give the hero; the daily sweep of the saved homepage gives the
    hero on all 311 days from 2026-01-01 to the festival's 11-07, and nothing
    on 11-08, with the byline rule on AND off (with the organiser's name and
    the rule off it lost 181 of those days to a "June 30, 2026" byline after
    "Self-published by Short Run Seattle"). "Short Run Comix Festival" is on that homepage 8 times, the
    hero's <h1> among them; a hero that drops "Comix" reads as no edition,
    which is the failure to prefer.
  * "range" — behaves like the fairs adapter: ranges only, the earliest
    surviving one wins, `name_rx` optional.

WHICH DATE, when several survive. A day-shape candidate with a stated year
beats one whose year was inferred from its weekday, then the one nearest the
name, then the earliest, then the first on the page. Measured on Short Run's
homepage, which carries 48 dates: the hero is 48 characters after "Short Run
Comix" (its <h1>) and has a year; an older post's "Saturday, Nov. 7th" is 16
characters after a "Short Run Comix Festival" category link and has none, so
year-first keeps the hero (and with it the clock and the FREE that follow it;
the post's date has neither).

WHAT REFUSES A DATE, each one a date on a real or adversarial page that is not
the event: over (the end is before today), more than MAX_MONTHS_AHEAD out,
longer than MAX_FAIR_DAYS, two years in play, a stated weekday that is not that
date's ("Saturday, November 7th, 2027" is a Sunday), the fairs' sale and
other-event words plus this site type's own (deadlines, calls for exhibitors or
submissions, table and booth fees, applications, payment windows, recaps)
within CONTEXT_CHARS either side (after the date, only its own paragraph when
that paragraph goes on: see judge()), exhibitor load-in or set-up BEFORE the
date, a volunteer orientation, a start month outside the entry's curated
`months` (Short Run's [10, 11], from its five editions on the saved homepage:
see annual_sources.json's `_months`), and a WordPress byline, anchored to the
byline's own "Posted on <date>" shape so "Schedule published! Short Run is
Saturday..." is not one, or marked by the theme itself (<time class="updated">,
itemprop="dateModified"). The months are the guard that does not depend on
wording: once an edition is past, any date near the name that a deny list does
not foresee is a candidate for the next one. The byline matters most and is not
the only guard: on the real homepage every post ends "Posted on October 28,
2026 October 29, 2026", the second date the last edit — which is today on the
day somebody fixes a typo. With the byline rule switched off the name rule
still keeps the hero (test_ingest_annual.py runs both ways), because no byline
on that page has "Short Run Comix" before it within 90 characters except posts
already over.

WHAT IT KEEPS FROM THE PAGE, and only from right after the chosen date:
  * a CLOCK RANGE within CLOCK_AFTER (60) characters, single days only, never
    across another date, skipping a range whose 25 characters before it say
    load-in, set-up, exhibitors or VIP ("exhibitor load-in 8am-10am, public 11am
    - 6pm" is 11:00-18:00). It is stamped with the entry's timezone into
    start_utc/end_utc here: the sync converts a naive clock with the event's
    coordinates, and an entry without them would have read 11:00 as 11:00 UTC.
  * FREE within FREE_AFTER (90) characters, never across another date, after
    the perks are cut out ("Free parking. Tickets $10." is not free; neither is
    "children 12 and under free") — or in the page's own headline (<title>,
    og:/meta description, first <h1>). A hyphenated compound is never it ("a
    scent-free event", "step-free entrance"), and a price in the date's
    paragraph or that headline vetoes it ("Tickets $10").
  * the DESCRIPTION is the publisher's own words: the date's paragraph, through
    the one holding the clock or FREE when they sit in the next. This adapter
    never writes "free" itself. ../mapsee's event_offers() (migration 0227)
    reads the stored description the way it reads every listing, and on Short
    Run's hero its Python twin (../mapsee/tools/measure_deals.py classify)
    answers ['free'] for the description and [] for the title alone.

THE DATE IS NEVER STORED, as in fair_sources.json: identity, venue and
coordinates are curated, the date is read every run. An edition that is over
contributes nothing (a live run on 2026-11-08 keeps 0 rows and says "past"),
and next year's starts contributing the day the hero changes.

FETCHING. Through mapsee_ingest_fairs.Polite: robots.txt first (shortrun.org's
allows everything and asks `Crawl-delay: 10`, which is waited out after the
robots.txt read and between any two pages), the homepage, then ONLY the
entry's own `pages`, in order, while nothing upcoming has been found and no
page has said the edition's own hours ended today. No link is followed that
the config does not name. A 403, a 429 or a challenge - on a page or on
/robots.txt itself - ends the entry for the run without a retry.

    python mapsee_ingest_annual.py --config annual_sources.json --store feeds_events.json
"""
from __future__ import annotations

import argparse
import html as _html
import json
import math
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import requests
except ImportError:                                               # pragma: no cover
    requests = None

import mapsee_ingest_fairs as F
from mapsee_admission import normalize_admission_facts
from mapsee_ingest import (EventStore, NormalizedEvent, VALID_CATEGORIES,
                           make_fingerprint, norm_categories)

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
SOURCE = "annual"
SHAPES = ("day", "range")
MAX_RUN_SECONDS = 900
NAME_BEFORE = 90            # the name, this close before a date, vouches for it
CLOCK_AFTER = 60            # a clock range this close after a single day is its hours
FREE_AFTER = 90             # FREE this close after the date is about the date
DESC_CAP = 400              # a long paragraph is cut to the sentences that matter
BYLINE_BEFORE = 40


# --------------------------------------------------------------------- text
_BLOCK_RX = re.compile(r"</?(?:p|div|h[1-6]|li|ul|ol|br|tr|td|th|dt|dd|section|article|"
                       r"header|footer|nav|main|aside|figure|figcaption|blockquote|"
                       r"table|form|hr)\b[^>]*>", re.I)


# A <time> the theme marks as the post's own date. WordPress writes every byline
# as <time class="entry-date published"> and <time class="updated"> (all 37
# <time> elements on Short Run's saved homepage, 2026-11-07), and schema.org
# themes say itemprop="datePublished"/"dateModified". The words in front of it
# vary or are missing - "<a rel=category>Short Run Comix Festival</a> &middot;
# <time class="updated">November 20, 2026</time>" has none, and on that day it
# was kept as the festival - so the element's own markup is the byline.
_BYLINE_TIME_RX = re.compile(
    r"<time\b[^>]*?\b(?:class|itemprop)\s*=\s*"
    r"(?:\"[^\"]*?(?:updated|modified|published)[^\"]*\"|"
    r"'[^']*?(?:updated|modified|published)[^']*'|"
    r"[^\s>\"']*(?:updated|modified|published))[^>]*>", re.I)
_MARK = "\ue000"                    # private use: never a publisher's character


def page_text(html: str, bylines_at: Optional[List[int]] = None) -> str:
    """F.text_of with the paragraphs kept: every block boundary becomes a
    newline, so a description can be cut at its own paragraph and the name rule
    can ask "is it in the date's paragraph". Entities decoded and whitespace
    collapsed as F.text_of does; the regexes' `\\s` matches the newline, so a
    date split across a <br> still parses.

    Given a list, `bylines_at` receives the text position where each byline
    <time> (_BYLINE_TIME_RX) has its first character. The text is the same
    either way, except that a U+E000 of the page's own (the mark used here)
    is dropped."""
    html = re.sub(r"<(script|style|template|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    if bylines_at is not None:
        html = _BYLINE_TIME_RX.sub(" " + _MARK, html.replace(_MARK, ""))
    html = _BLOCK_RX.sub("\n", html)
    t = re.sub(r"<[^>]+>", " ", html)
    if bylines_at is None:
        t = _html.unescape(t)
    else:
        t = _MARK.join(_html.unescape(seg).replace(_MARK, "") for seg in t.split(_MARK))
        # Each mark moves past the whitespace after it, onto its date's first
        # character, so the collapsing below sees the runs it would without it.
        t = re.sub(_MARK + r"(\s*)", lambda m: m.group(1) + _MARK, t)
    t = re.sub(r"[^\S\n]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    if bylines_at is None or _MARK not in t:
        return t
    parts = t.split(_MARK)
    out = parts[0]
    for part in parts[1:]:
        bylines_at.append(len(out))
        out += part
    return out.rstrip()


_HEADLINE_RX = (re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I),
                re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I),
                re.compile(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', re.I),
                re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', re.I),
                re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I))


def headline_parts(html: str) -> List[str]:
    """Where a site says something about ITSELF: its <title>, og:title,
    og:description, meta description and first <h1>, each decoded."""
    out = []
    for rx in _HEADLINE_RX:
        m = rx.search(html)
        if m:
            s = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
            if s:
                out.append(s)
    return out


# --------------------------------------------------------------------- single days
_MONTH_WORD = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
               r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_WD_WORD = (r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|"
            r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)")
WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# A WHOLE MONTH WORD, not F's three-letter prefix plus [a-z]*: single days are
# far commoner than ranges, and "Marathon 5" or "maybe 3" must not read as
# March or May. The \b after the ordinal stops "Aug 12" being re-read as "Aug 1".
SINGLE_RX = re.compile(
    # "Saturday & Sunday, November 7 & 8": a weekday PAIR before a day pair.
    # Without it the second weekday was read as the first day's and the
    # festival refused as "weekday disagrees".
    rf"(?:\b(?P<wd>{_WD_WORD})\.?(?:\s*(?:&|and|\+|[-\u2013\u2014]|to)\s*(?P<wd2>{_WD_WORD})\.?)?,?\s+)?"
    rf"\b(?P<mon>{_MONTH_WORD})\.?\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\b"
    rf"(?:\s*(?:&|and|\+)\s*(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\b)?"
    # A YEARLESS RANGE is not a single day: "Nov 7-8" must not read as Nov 7.
    # Only a dash followed by a DAY refuses it; "November 7th - 11am to 6pm"
    # is a single day with its clock. (A range with a year is RANGE_RX's, and
    # a single overlapping one is skipped in candidates().)
    rf"(?!\s*(?:[-\u2013\u2014]|to\b|through\b|thru\b)\s*(?:{_MONTH_WORD}\.?\s+)?"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\b(?!\s*(?::|a\.?\s?m\b|p\.?\s?m\b)))"
    rf"(?:,?\s*(?P<yr>20\d\d)\b)?", re.I)

# The fairs' vocabulary kept whole and EXTENDED, never edited. A comics fest's
# site is mostly addressed to its exhibitors, and their deadlines, table fees
# and payment windows are all real dates in the same sentences as the
# festival's name. Each word below refused a date in adversarial cases built
# from Short Run's own phrasing (2026-11-08); the fairs' list alone kept
# "Applications are due Friday, March 5th, 2027" as the 2027 festival and a
# "table fee payments open June 1 - 15, 2027" window as a two-week one.
NOT_THIS_EVENT_RX = re.compile(
    F.NOT_THE_FAIR_RX.pattern + r"|"
    r"deadline|due\s+(?:by|on)|apply\s+by|applications?\s+(?:open|close|are\s+due|due)|"
    r"call\s+for\s+(?:exhibitors|artists|entries|submissions|proposals|vendors|volunteers)|"
    r"submissions?\s+(?:open|close|due)|registration\s+(?:opens?|closes?)|"
    r"table\s+(?:fees?|payments?)|booth\s+fees?|payments?\s+(?:due|window|opens?|closes?)|pay\s+by|"
    r"tickets?\s+(?:go|goes|went)\s+on\s+sale|"
    r"recap|was\s+held|took\s+place|thank\s+you\s+for\s+(?:coming|joining|attending)|"
    # The one adversarial date a curated `months` cannot refuse (2026-11-08):
    # "Volunteer for Short Run Comix Festival! Orientation is Saturday, October
    # 30th, 2027" is a Saturday inside the festival's months, the week before it.
    r"orientation",
    re.I)
# The exhibitors' own logistics disqualify a date only when they come FIRST:
# "load-in and set-up is at 10 am on Saturday, November 7th" is the exhibitors'
# morning, while "Saturday, November 7th, 2026: exhibitor load-in 8am-10am,
# public 11am - 6pm" is the festival's hero with its logistics after it. A
# window both ways threw that hero away.
LOGISTICS_BEFORE_RX = re.compile(r"load[- ]?in|set[- ]?up\b|tear[- ]?down", re.I)

# WordPress's byline, "Posted on October 28, 2026 October 29, 2026": the second
# date is the post's last edit, and on the day an organizer fixes a typo it is
# TODAY - which every other rule here would keep. Short Run's homepage carries
# 20 of these. "Last updated: ..." is the same thing in other themes.
#
# ANCHORED TO THE BYLINE'S OWN SHAPE (2026-11-08): the word, an optional
# "by <author>", an optional "on" or ":", then the date itself or the first
# date of a "Posted on <date> <edit date>" pair, and nothing else. The first
# version allowed any 26 characters between the word and the date, and so
# refused ordinary announcements: "Schedule published! Short Run is Saturday,
# November 6th, 2027", "UPDATED: Short Run moves to Saturday, November 13th,
# 2027" and "Map updated for Short Run, Saturday, November 6th, 2027" each lost
# the festival's only date as "byline". Anchored, all three read; "Last
# updated: November 6, 2026" and both dates of every Short Run byline are still
# bylines, and the saved-homepage sweep (2026-01-01..11-08) is unchanged.
BYLINE_RX = re.compile(
    r"\b(?:posted|published|updated|modified|edited)(?:\s+by\s+\S+(?:\s+\S+)?)?(?:\s+on)?\s*:?\s*"
    r"(?:[a-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\s*)?$", re.I)


@dataclass
class Cand:
    start: date
    end: date
    matched: str
    pos: int
    stop: int
    has_year: bool
    kind: str                       # "range" | "day"
    reason: str = ""                # why it cannot be a date at all
    weekday: Optional[int] = None
    weekday2: Optional[int] = None
    name_dist: Optional[int] = None


# TWO DATED DAYS JOINED ONLY BY A CONNECTIVE are one event: "Saturday, November
# 7 & Sunday, November 8, 2026", "November 7, 2026 - November 8, 2026",
# "December 31, 2026 - January 1, 2027". Read as two singles, the first lost its
# end, and in the first phrasing the shared trailing year sat on Sunday alone,
# so year-first ranking started the festival on its second day with Saturday's
# clock. Between the two there may be nothing else; a sentence break is not a
# connective, so two dates in two sentences stay two.
JOIN_RX = re.compile(r"\s*(?:&|and|\+|[-\u2013\u2014]|to|through|thru)\s*", re.I)


def _resolve_year(mon: int, d1: int, wd: Optional[int], today: date) -> Tuple[Optional[int], str]:
    """A yearless day is believed only on its weekday's word, and only when
    exactly ONE of last year, this year and next year agrees with it. Within
    three consecutive years a (month, day, weekday) triple cannot repeat, so
    'none' is the only failure: a typo, or a date from an older post."""
    if wd is None:
        return None, "no year and no weekday"
    fits = []
    for y in (today.year - 1, today.year, today.year + 1):
        d = F._mk(mon, d1, y)
        if d and d.weekday() == wd:
            fits.append(y)
    if len(fits) != 1:
        return None, "weekday fits no nearby year" if not fits else "weekday fits two years"
    return fits[0], ""


def _ranges(text: str) -> List[Cand]:
    out = []
    for m in F.RANGE_RX.finditer(text):
        # Same arithmetic as the fairs: parse_ranges on the match alone, then
        # the same leading-year refusal against the page.
        got = F.parse_ranges(m.group(0))
        if not got:
            continue
        s, e, matched, _ = got[0]
        lead = F._YEAR_BEFORE_RX.search(text[max(0, m.start() - 12):m.start()])
        why = "two years in play" if lead and int(lead.group(1)) != e.year else ""
        out.append(Cand(s, e, matched, m.start(), m.end(), True, "range", why))
    return out


def _wd(m, group: str) -> Optional[int]:
    return WEEKDAY[m.group(group)[:3].lower()] if m.group(group) else None


def _single(text: str, m, today: date) -> Optional[Cand]:
    mon = F.MONTHS[m.group("mon")[:3].lower()]
    d1 = int(m.group("d1"))
    d2 = int(m.group("d2")) if m.group("d2") else None
    wd, wd2 = _wd(m, "wd"), _wd(m, "wd2")
    why = ""
    if m.group("yr"):
        year = int(m.group("yr"))
        lead = F._YEAR_BEFORE_RX.search(text[max(0, m.start() - 12):m.start()])
        if lead and int(lead.group(1)) != year:
            why = "two years in play"
    else:
        year, why = _resolve_year(mon, d1, wd, today)
    if year is None:
        return Cand(date.min, date.min, m.group(0).strip(), m.start(), m.end(), False, "day", why)
    start = F._mk(mon, d1, year)
    end = F._mk(mon, d2, year) if d2 else start
    if not start or not end or end < start:
        return None
    return Cand(start, end, m.group(0).strip(), m.start(), m.end(), bool(m.group("yr")),
                "day", why, wd, wd2 if d2 else None)


def _joined(text: str, a, b, today: date) -> Optional[Cand]:
    """`a` JOIN_RX `b` as one range, the year `b`'s (stated, or its weekday's)
    and one less for `a` when the month wraps; None when they are not that."""
    if a.group("d2") or b.group("d2") or a.group("wd2") or b.group("wd2"):
        return None                      # a day pair is already two days
    if not JOIN_RX.fullmatch(text[a.end():b.start()]):
        return None
    end = _single(text, b, today)
    if end is None or end.start == date.min:
        return None                      # the second day has no year of its own
    mon, d1 = F.MONTHS[a.group("mon")[:3].lower()], int(a.group("d1"))
    year = end.start.year - (1 if (mon, d1) > (end.start.month, end.start.day) else 0)
    if a.group("yr") and int(a.group("yr")) != year:
        return None                      # "November 7, 2025 - November 8, 2026" is not a weekend
    start = F._mk(mon, d1, year)
    if not start or start >= end.start:
        return None
    # The second day's "two years in play" was the first day's own year; only
    # a year in front of the FIRST day is another one.
    lead = F._YEAR_BEFORE_RX.search(text[max(0, a.start() - 12):a.start()])
    why = "two years in play" if lead and int(lead.group(1)) != year else ""
    return Cand(start, end.start, text[a.start():b.end()].strip(), a.start(), b.end(),
                bool(a.group("yr") or b.group("yr")), "day", why, _wd(a, "wd"), _wd(b, "wd"))


def _days(text: str, today: date, ranges: List[Cand]) -> List[Cand]:
    ms = [m for m in SINGLE_RX.finditer(text)
          # "Sept. 18-Oct. 4, 2026" ends in a single-looking day
          if not any(r.pos < m.end() and m.start() < r.stop for r in ranges)]
    out, i = [], 0
    while i < len(ms):
        both = _joined(text, ms[i], ms[i + 1], today) if i + 1 < len(ms) else None
        if both:
            out.append(both)
            i += 2
            continue
        one = _single(text, ms[i], today)
        if one:
            out.append(one)
        i += 1
    return out


def candidates(text: str, today: date, shape: str) -> List[Cand]:
    ranges = _ranges(text)
    if shape != "day":
        return ranges
    return sorted(ranges + _days(text, today, ranges), key=lambda c: c.pos)


def _name_distance(text: str, c: Cand, name_rx, starts: List[int]) -> Optional[int]:
    """Characters between the event's name and the date: a name ending within
    NAME_BEFORE before it, or anywhere in the date's own paragraph. None when
    neither — never page-wide, because a programming page's <title> names the
    festival above every pre-party on it.

    THE 90 CHARACTERS BEFORE NEVER REACH ACROSS ANOTHER DATE, because they
    cross paragraphs: a name there vouches for the nearest date only. Short
    Run's homepage has a post excerpt ending "...to Short Run on Nov. 7th!
    Read more…" and, in the next block, "Posted on July 10, 2026"; on a day
    before July 10, with the byline rule switched off, that byline was the
    nearest named date with a year and beat the hero. "Nov. 7th" sits between
    them, so the name is that date's. Inside the date's OWN paragraph a date
    between does not matter: "Short Run 2027 call for exhibitors! Applications
    are due Friday, March 5th, 2027. The festival returns Saturday, November
    6th, 2027" names the festival, and the deadline is refused on its own."""
    best = None

    def clear(a: int, b: int) -> bool:
        return not any(a <= p < b for p in starts)

    for m in name_rx.finditer(text, max(0, c.pos - NAME_BEFORE), c.pos):
        if clear(m.end(), c.pos):
            d = c.pos - m.end()
            best = d if best is None else min(best, d)
    a = text.rfind("\n", 0, c.pos) + 1
    b = text.find("\n", c.stop)
    b = len(text) if b < 0 else b
    for m in name_rx.finditer(text, a, b):
        d = (c.pos - m.end() if m.end() <= c.pos
             else m.start() - c.stop if m.start() >= c.stop else 0)
        best = d if best is None else min(best, d)
    return best


def date_starts(text: str) -> List[int]:
    """Where every date-looking thing starts, whatever the shape reads: on a
    range-shape page a single day is still another date."""
    return sorted({m.start() for m in SINGLE_RX.finditer(text)}
                  | {m.start() for m in F.RANGE_RX.finditer(text)})


def judge(text: str, c: Cand, today: date, name_rx=None, bylines: bool = True,
          starts: Optional[List[int]] = None, months=None, marked=()) -> str:
    """'' when the candidate may be the event, else the first reason it is not.
    `months` is the entry's curated months (None: any); `marked` the byline
    <time> positions page_text found."""
    if c.reason:
        return c.reason
    if c.end < today:
        return "past"
    if c.start > today + timedelta(days=F.MAX_MONTHS_AHEAD * 31):
        return "too far out"
    if (c.end - c.start).days + 1 > F.MAX_FAIR_DAYS:
        return "too long"
    if c.weekday is not None and c.start.weekday() != c.weekday:
        return "weekday disagrees"       # "Saturday, November 7th, 2027" is a Sunday
    if c.weekday2 is not None and c.end.weekday() != c.weekday2:
        return "weekday disagrees"
    # THE EVENT'S OWN MONTHS, curated: not a word on the page. Once an edition
    # is over, every other date near the name is a candidate for the next one,
    # and a deny list is only as good as its phrasings: "Exhibitor applications
    # ... are open until Friday, March 5th, 2027", "Submit your work to the ...
    # anthology by", "Apply before", "sign-ups close" and a "Spring Market:
    # Saturday, May 15th, 2027" each became the 2027 festival (review a7.py,
    # today 2026-11-20). No wording can be the next one that gets through this.
    if months and c.start.month not in months:
        return "outside the event's months"
    a, b = max(0, c.pos - F.CONTEXT_CHARS), min(len(text), c.stop + F.CONTEXT_CHARS)
    # AFTER THE DATE, ONLY ITS OWN PARAGRAPH - when that paragraph goes on. On
    # the real homepage the hero's paragraph ends ". FREE!" and the next one is
    # "Add yourself to our mailing list for deadline reminders", whose
    # "deadline" is 83 characters after the date: a margin of 38 under the
    # fairs' 45. "...Saturday, November 6th, 2027. FREE!</p><p>Join our mailing
    # list for deadline reminders.</p>" refused the festival outright. A date
    # that ENDS its block ("<h3>Saturday, March 5th, 2027</h3><p>Application
    # deadline</p>") is labelled by the block after it, so there the window
    # still crosses; and a heading BEFORE a date always counts ("<h3>Short Run
    # application deadline</h3><p>Friday, March 5th, 2027</p>"). The fairs
    # adapter reads flattened text with no paragraphs and is unaffected.
    nl = text.find("\n", c.stop)
    if nl >= 0 and text[c.stop:nl].strip(" ,.;:!?-\u2013\u2014"):
        b = min(b, nl)
    hit = NOT_THIS_EVENT_RX.search(text[a:b]) or LOGISTICS_BEFORE_RX.search(text[a:c.pos])
    if hit:
        return "not-this-event:" + hit.group(0).lower()
    if bylines and (BYLINE_RX.search(text[max(0, c.pos - BYLINE_BEFORE):c.pos])
                    or any(p <= c.pos and not text[p:c.pos].strip() for p in marked)):
        return "byline"
    if name_rx is not None:
        c.name_dist = _name_distance(text, c, name_rx,
                                     date_starts(text) if starts is None else starts)
        if c.name_dist is None:
            return "event not named near the date"
    return ""


def pick(text: str, today: date, shape: str = "day", name_rx=None, bylines: bool = True,
         months=None, marked=()) -> Tuple[Optional[Cand], str, List[Tuple[Cand, str]]]:
    """(chosen, why-not, every candidate with its verdict)."""
    starts = date_starts(text)
    log = [(c, judge(text, c, today, name_rx, bylines, starts, months, marked))
           for c in candidates(text, today, shape)]
    kept = [c for c, v in log if not v]
    if kept:
        if shape == "range":
            kept.sort(key=lambda c: (c.start, c.pos))           # the fairs' rule
        else:
            kept.sort(key=lambda c: (not c.has_year,
                                     c.name_dist if c.name_dist is not None else 10 ** 6,
                                     c.start, c.pos))
        return kept[0], "", log
    if not log:
        return None, "no date on the page", log
    counts = Counter(v for _, v in log)                 # "not-this-event:table fee" whole
    parts = []
    for reason, n in counts.most_common():
        if reason == "past":
            newest = max(c.end for c, v in log if v == "past")
            parts.append(f"past {n} (newest {newest.isoformat()})")
        else:
            parts.append(f"{reason} {n}")
    return None, f"{len(log)} date(s), none upcoming and the event's: " + ", ".join(parts), log


# --------------------------------------------------------------------- clock
_MER = r"(?:a\.?\s?m\.?|p\.?\s?m\.?)"
TIME_RANGE_RX = re.compile(
    rf"(?<![\d:/#.$])(?P<h1>\d{{1,2}})(?::(?P<m1>[0-5]\d))?\s*(?P<a1>{_MER})?\s*"
    rf"(?:[-\u2013\u2014]|to\b|until\b|till\b|til\b)\s*"
    rf"(?P<h2>\d{{1,2}})(?::(?P<m2>[0-5]\d))?\s*(?P<a2>{_MER})?(?![\d:])", re.I)
NOT_PUBLIC_HOURS_RX = re.compile(
    r"load[- ]?in|set[- ]?up|exhibitors?|vendors?|check[- ]?in|tear[- ]?down|break[- ]?down|"
    r"members?\s+(?:only|preview)|early\s+(?:access|entry)|vip|press\s+preview", re.I)
CLOCK_LEAD = 25


def _h(h: int, mer: Optional[str]) -> int:
    if mer is None:
        return h
    return (h % 12) + (12 if mer.lower().startswith("p") else 0)


def parse_clock(m) -> Optional[Tuple[int, int, int, int]]:
    h1, h2 = int(m.group("h1")), int(m.group("h2"))
    m1, m2 = int(m.group("m1") or 0), int(m.group("m2") or 0)
    a1, a2 = m.group("a1"), m.group("a2")
    if not (a1 or a2):
        # No meridiem: only a 24-hour clock with minutes on BOTH sides is a
        # clock. "7-8" is a date, a score or an age range.
        if not (m.group("m1") and m.group("m2")) or h1 > 24 or h2 > 24:
            return None
        return h1, m1, h2, m2
    if h1 > 12 or h2 > 12 or h1 == 0 or h2 == 0:
        return None
    if a1 and a2:
        s, e = _h(h1, a1), _h(h2, a2)
    elif a2:                                    # "11 - 6 pm", "6:00 - 9:00 pm", "12-2 pm"
        e = _h(h2, a2)
        s = _h(h1, a2)
        if s > e:                               # "11 - 6 pm": the 11 is the morning
            s = _h(h1, "am" if a2.lower().startswith("p") else "pm")
    else:                                       # "11am-6"
        s = _h(h1, a1)
        e = _h(h2, a1)
        if e <= s:
            e = _h(h2, "pm" if a1.lower().startswith("a") else "am")
    return s, m1, e, m2


def _next_date_after(c: Cand, starts: List[int], limit: int) -> int:
    later = [p for p in starts if c.stop <= p < limit]
    return min(later) if later else limit


def clock_after(text: str, c: Cand, starts: List[int]
                ) -> Tuple[Optional[Tuple[int, int]], str, int]:
    """((start minute, end minute) of the day, why-not, end position)."""
    if c.start != c.end:
        return None, "more than one day: all-day on purpose", c.stop
    limit = _next_date_after(c, starts, c.stop + CLOCK_AFTER)
    win = text[c.stop:limit]
    why = "no clock range after the date"
    prev = 0
    for m in TIME_RANGE_RX.finditer(win):
        got = parse_clock(m)
        lead = win[max(prev, m.start() - CLOCK_LEAD):m.start()]   # never the previous clock's words
        prev = m.end()
        if not got:
            why = f"not a clock: {m.group(0)!r}"
            continue
        hit = NOT_PUBLIC_HOURS_RX.search(lead)
        if hit:
            why = f"not the public's hours: {hit.group(0)!r}"
            continue
        sh, sm, eh, em = got
        start_min, end_min = sh * 60 + sm, eh * 60 + em
        if end_min <= start_min:
            end_min += 24 * 60                   # "8pm - 2am"
        if end_min - start_min > 16 * 60:
            return None, "longer than 16 hours", c.stop
        return (start_min, end_min), "", c.stop + m.end()
    return None, why, c.stop


# --------------------------------------------------------------------- free
# (?<![-\w]): A HYPHENATED COMPOUND IS NEVER FREE ADMISSION. "A scent-free
# event", "Step-free entrance", "barrier-free entry" and "Alcohol-free." each
# set {'free': True, 'offer': {'price': '0'}} beside "Tickets $10" (review
# a1.py/a2.py, 2026-11-08) through \bfree after the hyphen.
FREE_RX = re.compile(
    r"(?<![-\w])free\s*!|"
    r"(?<![-\w])free\s+(?:admission|entry|entrance|event|to\s+(?:attend|the\s+public|all|everyone)|"
    r"and\s+open\s+to\s+(?:the\s+public|all|everyone)|for\s+(?:all|everyone|the\s+public))\b|"
    r"\b(?:admission|entry|entrance)\s*(?:is\s+)?(?:always\s+)?(?::\s*)?free\b|"
    r"\bno\s+(?:admission\s+(?:fee|charge)|entry\s+fee|cover(?:\s+charge)?)\b|"
    r"(?<![-\w])free(?=\s*[.\n]|\s*$)", re.I)
# CUT OUT, not vetoed, before FREE_RX is tried — the 0227 tagger's rule: "Free
# parking. Free admission." stays free; "Free parking." alone does not make it.
# On Short Run's exhibitor page all six "free" words are perks (parking three
# times, WIFI, masks) and this removes all six.
FREE_PERK_RX = re.compile(
    r"\bfree\s+(?:street\s+)?(?:parking|wi-?fi|shuttle|swag|drinks?|food|snacks?|coffee|tote|"
    r"t-?shirts?|samples?|refreshments|masks?|tables?|zines?|comics?|books?|downloads?)\b|"
    r"\b(?:street\s+)?(?:parking|wi-?fi|refreshments|snacks|coffee|masks?|hand\s+sanitizer|"
    r"food|drinks?)\b[^.!\n]{0,40}?\b(?:for\s+)?free\b|"
    r"\bfree\s+(?:for|to)\s+(?:\w+\s+){0,2}(?:members|exhibitors|vendors|students|seniors|"
    r"kids|children|residents|volunteers|ticket\s*holders)\b|"
    r"\bfree\s+(?:with|before|until)\b[^.!\n]{0,30}|"
    r"\b(?:members|exhibitors)\s+(?:get\s+in\s+|enter\s+)?free\b|"
    # FREE FOR CHILDREN ONLY, the age anywhere in the clause: "children 12 and
    # under free." set free beside "Tickets $15" (review a4.py), because the
    # kids word had to be right before "free".
    r"\b(?:kids?|child(?:ren)?|toddlers?|bab(?:y|ies)|infants?|youth|under[- ]?\d+s?)\b"
    r"[^.!?;,\n\u2013\u2014]{0,30}?\bfree\b|"
    r"\b\d+\s*(?:years?\s+)?(?:(?:and|&)\s+(?:under|younger)|or\s+(?:under|younger))\b"
    r"[^.!?;,\n\u2013\u2014]{0,15}?\bfree\b",
    re.I)
FREE_NEG_RX = re.compile(r"\b(?:not|isn'?t)\s+(?:a\s+)?free\b", re.I)
# A PRICE IN THE SAME PARAGRAPH VETOES FREE: the page contradicts itself, and a
# missed FREE costs nothing while a false one is a wrong Event JSON-LD offer.
PRICE_RX = re.compile(
    r"[$\u20ac\u00a3]\s?\d+(?:[.,]\d+)?|\b\d+(?:[.,]\d+)?\s?[\u20ac\u00a3]|"
    r"\b(?:usd|eur|gbp|cad|aud)\s?\d+|"
    r"\b\d+(?:[.,]\d+)?\s?(?:usd|eur|gbp|cad|aud|dollars?|euros?|pounds?)\b|"
    r"\btickets?\s*(?:are\s+|cost\s+|:\s*)?\d+(?:[.,]\d\d)?\b(?!\s*(?::\d|[ap]\.?\s?m\b))", re.I)


def _free_in(s: str) -> Optional[re.Match]:
    if FREE_NEG_RX.search(s):
        return None
    # Blank the perks out IN PLACE (same length), so a match's position still
    # points into the publisher's text.
    return FREE_RX.search(FREE_PERK_RX.sub(lambda m: " " * len(m.group(0)), s))


def _paragraph(text: str, a: int, b: int) -> str:
    """From the start of the paragraph holding `a` to the end of the one holding `b`."""
    lo = text.rfind("\n", 0, a) + 1
    hi = text.find("\n", max(a, b))
    return text[lo:len(text) if hi < 0 else hi]


def free_after(text: str, c: Cand, starts: List[int], headline: List[str]
               ) -> Tuple[Optional[str], str, int, str]:
    """(the phrase, where, end position in text, headline part) — right after the
    event's own date and never across another date, else the page's headline.
    A price in the date's paragraph (through FREE's own) or in the headline part
    vetoes it: (None, why, ...)."""
    limit = _next_date_after(c, starts, c.stop + FREE_AFTER)
    m = _free_in(text[c.stop:limit])
    if m:
        price = PRICE_RX.search(_paragraph(text, c.pos, c.stop + m.end()))
        if price:
            return None, f"a price beside FREE: {price.group(0)!r}", c.stop, ""
        return m.group(0).strip(), "after the date", c.stop + m.end(), ""
    for part in headline:
        m = _free_in(part)
        if m:
            price = PRICE_RX.search(part) or PRICE_RX.search(_paragraph(text, c.pos, c.stop))
            if price:
                return None, f"a price beside FREE: {price.group(0)!r}", c.stop, ""
            return m.group(0).strip(), "headline", c.stop, part
    return None, "", c.stop, ""


# --------------------------------------------------------------------- the page
def quote(text: str, pos: int, upto: int, cap: int = DESC_CAP) -> str:
    """The publisher's words from the start of the paragraph holding `pos` to
    the end of the one holding `upto`, verbatim; a paragraph too long for a
    description is cut to the sentences between the two."""
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", max(pos, upto - 1))
    b = len(text) if b < 0 else b
    s = text[a:b].strip()
    if len(s) <= cap:
        return s
    rel_a, rel_b = pos - a, max(pos, upto) - a
    lo = max(s.rfind(p, 0, rel_a) for p in (". ", "! ", "? "))
    lo = lo + 2 if lo >= 0 else 0
    his = [h for h in (s.find(p, rel_b) for p in (". ", "! ", "? ")) if h >= 0]
    hi = (min(his) + 1) if his else len(s)
    out = s[lo:hi].strip()
    if len(out) > cap:
        out = out[:cap].rsplit(" ", 1)[0] + "\u2026"
    return out


@dataclass
class Result:
    start: Optional[date] = None
    end: Optional[date] = None
    matched: str = ""
    clock: Optional[Tuple[int, int]] = None        # minutes from the day's midnight
    clock_why: str = ""
    free: Optional[str] = None
    free_where: str = ""
    why: str = ""
    description: str = ""
    ended: Optional[date] = None                   # an edition whose stated hours are over today
    log: List[Tuple[Cand, str]] = field(default_factory=list)


def read_page(html: str, today: date, shape: str = "day", name_rx=None,
              bylines: bool = True, now_minute: Optional[int] = None,
              months=None) -> Result:
    """`now_minute` is the minute of `today` where the event is; given, a single
    day whose stated hours have ended counts as over, as a date alone cannot
    say. Short Run 2026 ended at 18:00 on 2026-11-07 in Seattle, which is 02:00
    UTC on the 8th. The daily cron is 06:17 UTC (22:17 on the 7th there),
    though its last 30 scheduled runs (2026-10-15..11-07, read with `gh run
    list --event schedule` on 2026-11-08) started 10:48-16:31 UTC (the 8th
    there): a run on time, or a dispatch that evening, would by its date alone
    have written the finished festival again. `months` is the entry's curated
    `months` (None: any month)."""
    marked: List[int] = []
    text = page_text(html, marked)[:120000]
    c, why, log = pick(text, today, shape, name_rx, bylines, months, marked)
    r = Result(why=why, log=log)
    if not c:
        return r
    starts = date_starts(text)
    clock, clock_why, clock_end = clock_after(text, c, starts)
    if clock and now_minute is not None and c.end == today and now_minute >= clock[1]:
        r.ended = c.end
        r.why = (f"past: the {c.start.isoformat()} edition ended at "
                 f"{(clock[1] // 60) % 24:02d}:{clock[1] % 60:02d} where it is held")
        return r
    r.start, r.end, r.matched = c.start, c.end, c.matched
    r.clock, r.clock_why = clock, clock_why
    r.free, r.free_where, free_end, head_part = free_after(text, c, starts, headline_parts(html))
    r.description = quote(text, c.pos, max(c.stop, clock_end, free_end))
    if head_part:
        r.description = (r.description + "\n" + head_part[:DESC_CAP]).strip()
    return r


_ZIP_RX = re.compile(r"\b(\d{5}(?:-\d{4})?)\s*$")


def street_and_postcode(address: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The config holds the address as the publisher writes it, "5701 6th Ave S,
    Seattle, WA 98108"; the row wants its street line and postcode apart,
    because the sync composes "street, city, region postcode" for the 📍 line
    and a whole address there printed Seattle twice."""
    if not address:
        return None, None
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    zip_m = _ZIP_RX.search(parts[-1]) if len(parts) > 1 else None
    return (parts[0] if parts else None), (zip_m.group(1) if zip_m else None)


def _utc(local: datetime, zone: ZoneInfo) -> str:
    return local.replace(tzinfo=zone).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_event(site: Dict[str, Any], r: Result) -> NormalizedEvent:
    """One row per edition. ALL-DAY unless a single day stated its public hours,
    for the fairs' reason: a multi-day event's doors move day to day and no page
    here says so in a way that can be read."""
    zone_name = site["timezone"]
    zone = ZoneInfo(zone_name)
    year = r.start.year
    start_utc = end_utc = None
    if r.clock:
        midnight = datetime.combine(r.start, datetime.min.time())
        s_dt, e_dt = midnight + timedelta(minutes=r.clock[0]), midnight + timedelta(minutes=r.clock[1])
        start_local, end_local = s_dt.isoformat(timespec="seconds"), e_dt.isoformat(timespec="seconds")
        start_utc, end_utc = _utc(s_dt, zone), _utc(e_dt, zone)
    else:
        start_local, end_local = r.start.isoformat(), r.end.isoformat()
    category = site.get("category") or "community"
    facts = normalize_admission_facts("Free", context=r.description) if r.free else None
    street, postcode = street_and_postcode(site.get("address"))
    nev = NormalizedEvent(
        source=SOURCE,
        # THE YEAR IS IN THE IDENTITY, as in the fairs: the name never changes,
        # so without it next year's edition would read as this year's having
        # moved, and EventStore would pop one for the other.
        source_id=f"{site['key']}-{year}",
        name=f"{site['name']} {year}",
        description=r.description or None,
        start_local=start_local, end_local=end_local,
        start_utc=start_utc, end_utc=end_utc,
        timezone=zone_name,
        venue_name=site.get("venue"),
        address=street,
        latitude=site.get("lat"), longitude=site.get("lon"),
        city=site.get("city"), region=site.get("region"),
        country=site.get("country"), postal_code=site.get("postal_code") or postcode,
        category=category,
        categories=norm_categories(category, site.get("categories") or []),
        ticket_url=site["url"],
        # The publisher's FREE, as a fact the product can put in Event JSON-LD
        # (mapsee_admission, the route the waterfront adapter takes for "| Free").
        # {} with admission_checked says "read, and no admission stated", which
        # clears a free this reader wrote on an earlier run.
        source_details=facts or {},
        admission_checked=True,
        coords_exact=site.get("lat") is not None and site.get("lon") is not None,
    )
    nev.fingerprint = make_fingerprint(nev.name, r.start.isoformat(), nev.venue_name, nev.city)
    return nev


# --------------------------------------------------------------------- config
_KEY_RX = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def validate_site(site: Dict[str, Any]) -> None:
    """Raise ValueError naming the entry. An entry is curated: a wrong one is a
    config bug, not a source that went quiet, so it fails loudly."""
    label = site.get("key") or site.get("name") or "?"
    if not isinstance(site.get("key"), str) or not _KEY_RX.match(site["key"]):
        raise ValueError(f"{label}: key must be a lower-case slug")
    if not str(site.get("name") or "").strip():
        raise ValueError(f"{label}: name is required")
    url = site.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in ("https", "http") or not urlparse(url).netloc:
        raise ValueError(f"{label}: url must be an absolute http(s) URL")
    host = urlparse(url).netloc.lower()
    for p in site.get("pages") or []:
        if not isinstance(p, str) or urlparse(p).netloc.lower() != host:
            raise ValueError(f"{label}: every page must be on {host} (no link hunting, no other host)")
    if site.get("shape") not in SHAPES:
        raise ValueError(f"{label}: shape must be one of {SHAPES}")
    if site["shape"] == "day" and not site.get("name_rx"):
        raise ValueError(f"{label}: shape 'day' needs name_rx (single days are believed only near the name)")
    if site.get("name_rx"):
        try:
            re.compile(site["name_rx"], re.I)
        except re.error as exc:
            raise ValueError(f"{label}: name_rx does not compile: {exc}") from exc
    months = site.get("months")
    if months is not None and (
            not isinstance(months, list) or not months or len(set(months)) != len(months)
            or any(isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= 12 for m in months)):
        raise ValueError(f"{label}: months must be a non-empty list of distinct month numbers 1-12")
    if site.get("category") not in VALID_CATEGORIES:
        raise ValueError(f"{label}: category must be one of the lens keys")
    try:
        ZoneInfo(site.get("timezone"))
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"{label}: timezone must be an IANA zone") from exc
    lat, lon = site.get("lat"), site.get("lon")
    if (lat is None) != (lon is None):
        raise ValueError(f"{label}: lat and lon come together or not at all")
    if lat is not None:
        if (not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
                or not math.isfinite(lat) or not math.isfinite(lon)
                or not -90 <= lat <= 90 or not -180 <= lon <= 180):
            raise ValueError(f"{label}: lat/lon out of range")


def load_sites(path: str) -> List[Dict[str, Any]]:
    doc = json.load(open(path, encoding="utf-8"))
    sites = doc.get("sites") if isinstance(doc, dict) else None
    if not isinstance(sites, list) or not sites:
        raise ValueError("config must hold a non-empty 'sites' list")
    for s in sites:
        validate_site(s)
    keys = [s["key"] for s in sites]
    if len(set(keys)) != len(keys):
        raise ValueError("two entries share a key")
    return sites


# --------------------------------------------------------------------- the run
def now_for(site: Dict[str, Any]) -> Tuple[date, Optional[int]]:
    """(today, minute of the day) where the event is held, so an edition is
    over when its own evening is. MAPSEE_TODAY=YYYYMMDD fixes the day for the
    tests and leaves the minute unknown (None): a day, not a clock."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date(), None
    now = datetime.now(ZoneInfo(site["timezone"]))
    return now.date(), now.hour * 60 + now.minute


def ingest_site(store: EventStore, polite: "F.Polite", site: Dict[str, Any],
                today: Optional[date] = None, bylines: bool = True,
                now_minute: Optional[int] = None) -> Dict[str, Any]:
    """{'kept': 0|1, 'verdict': str, 'pages': n read}. The homepage, then only
    the entry's own `pages`, while nothing upcoming has been found."""
    label = site["name"]
    if today is None:
        today, now_minute = now_for(site)
    name_rx = re.compile(site["name_rx"], re.I) if site.get("name_rx") else None
    verdicts, read = [], 0
    for url in [site["url"]] + list(site.get("pages") or []):
        page, why = polite.get(url)
        if page is None:
            verdicts.append(f"{url}: not read — {why}")
            # Out of time is Polite's own "run deadline..." words, never a
            # substring: a refused URL is quoted in its reason, and a page at
            # /entry-deadlines/ would otherwise end the walk.
            if urlparse(url).netloc.lower() in polite.refused or why.startswith("run deadline"):
                break                    # the host said no, or the run is out of time
            continue
        read += 1
        r = read_page(page.text, today, site["shape"], name_rx, bylines, now_minute,
                      site.get("months"))
        if not r.start:
            verdicts.append(f"{url}: {r.why}")
            # ITS OWN HOURS ARE OVER, so no later page may bring it back. A
            # programming page states the same day with no clock, and read
            # after the homepage's 18:00 it wrote the finished festival as an
            # all-day row (measured with Short Run's saved homepage and
            # programming page as `pages`, today 2026-11-07: 18:01 and 22:17
            # each gave 1 row, 2026-11-07 all day, admission {} - which also
            # clears the FREE the morning's row carried). Tomorrow the date
            # alone says past, and the pages are read again.
            if r.ended:
                break
            continue
        status = store.upsert(to_event(site, r))
        hours = (f"{r.clock[0] // 60:02d}:{r.clock[0] % 60:02d}-"
                 f"{(r.clock[1] // 60) % 24:02d}:{r.clock[1] % 60:02d} {site['timezone']}"
                 if r.clock else f"all day ({r.clock_why})")
        free = (f"free {r.free!r} ({r.free_where})" if r.free
                else f"admission not stated ({r.free_where})" if r.free_where else "admission not stated")
        line = (f"{r.start}" + (f" -> {r.end}" if r.end != r.start else "")
                + f", {hours}, {free}  \"{r.matched}\" from {url} [{status}]")
        print(f"[annual] {label}: {line}")
        return {"kept": 1 if status in ("added", "updated", "merged") else 0,
                "verdict": line, "pages": read}
    verdict = "; ".join(verdicts) or "nothing to read"
    print(f"[annual] {label}: no upcoming edition — {verdict}")
    return {"kept": 0, "verdict": verdict, "pages": read}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import annual events whose own site states the date in prose.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="one entry by key")
    ap.add_argument("--max-seconds", type=int, default=180,
                    help="no request starts after this; a Crawl-delay wait that would cross it is not made")
    args = ap.parse_args(argv)
    if not 1 <= args.max_seconds <= MAX_RUN_SECONDS:
        ap.error(f"--max-seconds must be between 1 and {MAX_RUN_SECONDS}")
    try:
        sites = load_sites(args.config)
    except (OSError, ValueError, TypeError) as exc:
        print(f"[annual] configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.only:
        sites = [s for s in sites if s["key"] == args.only]
    if requests is None:
        print("[annual] requests is not installed", file=sys.stderr)
        return 1
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    t0 = time.monotonic()
    polite = F.Polite(session, min_interval=1.0, deadline=t0 + args.max_seconds)
    store = EventStore(args.store)
    kept = 0
    for site in sites:
        # PER ENTRY: one site that hangs must not cost the others.
        try:
            kept += ingest_site(store, polite, site)["kept"]
        except Exception as exc:                                  # noqa: BLE001
            print(f"[annual] {site.get('name', '?')} FAILED: {type(exc).__name__}: {exc}")
    if kept:
        store.save()
    print(f"[annual] done in {time.monotonic() - t0:.0f} s: {kept} of {len(sites)} edition(s) "
          f"upcoming; {polite.requests} page request(s), {polite.waited:.0f} s waited on the "
          f"hosts' pace; store holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
