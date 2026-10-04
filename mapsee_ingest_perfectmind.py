#!/usr/bin/env python3
"""
mapsee_ingest_perfectmind.py - municipal DROP-IN schedules from PerfectMind
(Xplor Recreation, d.b.a. nextRec) BookMe4 widgets: what is on at the city's
pools, rinks, gyms and community centres this week, at the hour it is on.

    python mapsee_ingest_perfectmind.py --config perfectmind_sources.json \
        --store feeds_events.json [--only surrey] [--max-minutes 45] [--dry-run]

Env: none. Every widget is a public page a resident reaches from the city's own
recreation site, with no account and no key.

WHY THIS SOURCE. A community centre's schedule is the thing a neighbourhood
door is for and the thing no ticketing API carries: public skate at 14:00,
lane swim at 06:00, 55+ billiards, parent-and-tot play time, drop-in
pickleball. Canadian and US cities run these through a handful of recreation
platforms, and PerfectMind is the one whose public widget is both readable and
not refused (see THE BASIS below; ACTIVE Network's ActiveNet, the other big
one, forbids automated retrieval in its Terms of Use and is not read).

    GET  https://<tenant>.perfectmind.com/<org>/Clients/BookMe4?widgetId=<uuid>        the page
    POST .../BookMe4V2/GetCategoriesDataV2?embed=False  {widgetId}                    its calendars
    POST .../BookMe4BookingPagesV2/ClassesV2  {calendarId, widgetId, page, dateString, after}
                                                                                       one window of rows

MEASURED 2026-10-03, the live run over the 15 configured tenants (16 widgets):
538 calendars, 140 of them BookingType 2; 12 of those refused by name and 128
walked (22 empty this season), 78,339 rows read in the next 90 days with 2,230
requests in 17.5 minutes (three tenants at a time). Replayed through this code
(2026-10-04, the members-only rule of lesson 4 included): 65,163 rows kept, every
one on its facility's point, at 118 venues in 15 places: Brampton 13,562, Vaughan
11,631, Surrey 9,765, Markham 6,235, Caledon 5,559, Coquitlam 5,121, Abbotsford
3,609, North Vancouver 2,997, Calgary's Westside 2,392, Nanaimo 1,764, Moose Jaw
1,574, Menlo Park 269, Kamloops 262, Oakville 226, Rochester NY 197. Two
measured tenants are not configured (perfectmind_sources.json `_not_included`:
Hamilton's drop-in calendars are 98% pickleball court RENTALS, Chattanooga's
are empty). The research sweep before this adapter counted 57,034 rows for the
same fifteen: its pager stopped at 24 answers per calendar, which read Vaughan
as 5,317 rows of its 11,916.

------------------------------------------------------------------------------
THE BASIS FOR READING IT, stated plainly because it is the first question
------------------------------------------------------------------------------
  * robots.txt: /robots.txt answers 404 on all 19 tenant hosts probed - RFC
    9309 "unavailable", which disallows nothing. It is RE-READ AT RUN TIME for
    every tenant (robots_txt.Robots) and a tenant whose file starts refusing us
    is skipped that run and said so, never worked around.
  * Terms: the nextRec Terms of Service (nextrec.com/policies/terms-of-service)
    are a SUBSCRIBER agreement between nextRec and the city; 0 matches for
    robot, scrape, spider, crawl, automated, harvest or extract. The widget
    pages link no terms of their own. That is the opposite of ActiveNet, whose
    footer terms name "robot, bot, spider ... to retrieve, index, data mine"
    and are why that platform is not read.
  * Publication: these are the public schedules of fourteen cities and one
    non-profit centre (Calgary's Westside Recreation Centre). Three tenants are
    recorded linking the widget from the city's own recreation pages (Surrey's
    "Broad Search" on surrey.ca drop-in schedules, coquitlam.ca/979/Drop-In-
    Activities, kamloops.ca's PerfectMind page); the other twelve were found
    by searching for the widget URL, which is how a resident finds them too.
    `_found` in the config records which is which.
  * Manners: one request at a time per tenant host, at least PACE_SECONDS
    apart, the product User-Agent, a per-tenant request cap, and a whole-run
    deadline. A 401, 403, 429 or a bot challenge ends that tenant for the run.

------------------------------------------------------------------------------
THE THINGS THAT WILL BITE YOU
------------------------------------------------------------------------------
1. PAGING IS THE PAGE'S OWN, OR IT SILENTLY UNDERCOUNTS. The first two research
   passes read Brampton as 376 rows; this run read 16,069. `walk_calendar`
   copies ClassBookingV2Controller.loadItems line for line:
     * `after` carries the previous answer's `nextKey` (an ISO date) and
       `dateString` is nextKey + 1 day.
     * `page` is a 14-day WINDOW (the controller's numberOfDaysToLoad): page 0
       is today to day 13, page 1 the fortnight after. An EMPTY answer means
       the window is used up, so `page` moves on and `after` stays; the page is
       never reset by the next full answer. Two empties in a row end the list.
       Measured on Kamloops Skating: 9 requests, 159 rows, Oct 3 to Oct 31.
     * `nextKey`, never `classesMaxEndDateString`: that string's format varies
       by tenant (Kamloops `11/10/2026 08:15 PM`, Abbotsford `dd-MMM-yyyy`),
       and a "0001-01-01" nextKey is the end.
   It costs a request per window boundary: 2,230 requests for 128 calendars.
   It also means a calendar with a whole empty fortnight (a holiday closure)
   ends there - for the widget's own "load more" button too.

2. NO TOKEN IS NEEDED, SO NONE IS SENT, AND NO START PAGE IS READ. The page
   carries an ASP.NET __RequestVerificationToken (anti-forgery, not a login).
   Measured 2026-10-03 on Kamloops: both JSON endpoints answer 200 with
   identical bodies from a cold session with no cookies and no token, and the
   live run then needed the token on 0 of 15 tenants. So the adapter asks
   without it, and only if a tenant answers 400/500 or non-JSON does it read
   the start page once for the token and ask again (`token_fallbacks`).

3. A DROP-IN CALENDAR IS BookingType 2. 3 is registered courses, 4 is facility
   rental lists. Only 2 is read. Calendars are de-duplicated by Id across a
   tenant's widgets (Abbotsford's arena widget repeats two of its daily
   widget's calendars).

4. PRIVATE BOOKINGS LIVE INSIDE THE DROP-IN CALENDARS. Childminding, external
   personal trainers, birthday parties, court reservations, fitness
   orientations, equipment lending: all BookingType 2, all bookable, none of
   them something you can turn up to. 12 whole calendars are refused by name
   (`PRIVATE_CALENDAR_RX`; a tenant's `exclude_calendars` adds to it) - every
   one the research had to list per tenant - and then rows by title
   (`_ROW_RULES`), each counted under its reason. On 2026-10-03: members-only
   5,412 (159 by title, 5,253 by a membership the Details require - see
   MEMBERS_ONLY_DETAILS_RX; Surrey's Seniors Services, Markham's fitness classes
   and Brampton's Flower City Seniors Centre, each a paid membership, and
   every one of them "No fee" or "$0.00 - ..." because a member books for
   nothing), childminding 1,542 (Surrey files it under "Drop In 0-12"), closure
   notices 450, private bookings and rentals 149, cancelled 22, orientations
   12. Closures and cancellations are refused HERE because
   notice_reason in mapsee_ingest misses "CANCELLED Winmar Toddler Turf" and
   "Lane Swim - CLOSED for Programs".

5. BOOKING GRIDS. Moose Jaw publishes "Yara Centre Track Drop in" as 1,040
   rows in 90 days, up to 16 one-hour slots a day: a grid, not a schedule.
   `collapse_booking_grids` mirrors mapsee_ingest_openactive's (read its note)
   with one change, measured: a grid is a SHAPE as well as a count, because six
   a day is also Markham's lane swim timetable. 4,087 slot rows fold into 370
   day rows (Moose Jaw's Track, Gym, Turf and Toddler Turf; North Vancouver's
   back-to-back lane swim). Exact duplicates (same title, place and instant)
   fold first: 141. Weekly series are deliberately NOT folded into standing
   rows: a standing row is rolled forward for ever and nothing retires it
   (docs/agents/openactive-and-standing-rows.md, "A STANDING ROW NEVER DIES"),
   and a rec-centre timetable changes every season.

6. THE COORDINATE IS THE FACILITY'S - AND 5 OF 118 FACILITIES ARE MISPLACED.
   Each row carries `Address.Latitude/Longitude`, the point the city set on the
   facility record. Checked 2026-10-03 against OpenStreetMap for every one of
   the 118 venues (Photon, by name and by street address): 96 sit within 250 m
   of the OSM facility of the same name, carrying 60,559 of the rows. Five are
   460 m to 6.0 km away from a building that OSM and their own street address
   agree on - Surrey's North Surrey Sport and Ice Complex (5,981 m) and
   Clayton Community Centre (1,096 m), Caledon's Centre for Recreation and
   Wellness (3,326 m), Nanaimo Ice Centre (704 m), Kamloops' McArthur Island
   (460 m): 3,784 rows. The rest are parks (an area has no one point) or OSM
   hits for some other place, with the widget's point within 160 m of its
   street address. So the tenant's `venues` book is consulted FIRST and holds
   those five, each with its provenance; with that, `coords_exact` is set and
   the sync never geocodes over the point (outside the US it could not anyway:
   Census is the only geocoder). A facility with NO point is placed from the
   same book, else another row at the same Location, else at the same street
   line (Surrey's "...Complex - Aquatics" shares 16555 Fraser Highway with its
   Arenas half). Anything still unplaced is dropped HERE, where the count can be
   printed: 0 on 2026-10-03. Re-check `venues` when a tenant is added.

7. THE PRICE WORDING IS A CONTRACT with ../mapsee's offer tagger (migration
   0227 reads `free` from the title AND THE WHOLE DESCRIPTION, prose included;
   its Python twin is ../mapsee/tools/measure_deals.py). Three rules, each
   measured with that twin over the rows the sync writes (replay of 2026-10-03):
     * FREE only when the PriceRange is "No fee" (or all $0) on a row the
       widget sells: "Free drop-in: no fee." "No fee" is the WIDGET's charge -
       a members-only row says it (lesson 4) and so does a row the widget does
       not sell: Markham's 749 "Drop-In Aquafit" rows, "not available for
       registration online", cost $7.58 at the desk. Those write no price.
     * A STATED FEE SAYS "not free", which 0227's negation (`FREE_NEG`) reads
       as a veto on the whole row: "Drop-in fee: $9.00 (not free)." and, when
       the range starts at a pass-holder's $0, "(not free for everyone)".
       Without it the source's own prose tagged 181 paid rows free - Coquitlam's
       $9.00 Figure Skating Buy-On says "Coaches are free of charge" (95), Moose
       Jaw's $0.00 - $18.25 gym "free of charge with membership" (86).
     * A HIDDEN PRICE (DisplayPrices false: NVRC 3,018 rows, Westside 2,392)
       writes no price line: the old "Drop-in fee: see the listing" claimed a
       fee on 119 youth-centre rows whose text says "It is FREE to drop in!".
   Before: 8,692 rows tagged free, 3,190 of them behind a paid membership
   (Surrey 1,792, Markham 1,313; Caledon's 85 "Free Membership" track rows are
   free and stay), 181 with a stated fee. After: 4,498 tagged free, 0 with a
   stated fee, 0 behind a paid membership. The line comes FIRST in the
   description, because the sync's _cap_prose cuts the END of long prose and
   keeps only a short last paragraph.

8. TITLES CARRY THE PLACE AND THE CLOCK. Brampton writes "Billiards Drop-In
   (55+ Years) | Bob Callahan Flower City Seniors Centre 11:00-2:15pm" on 96%
   of its rows, North Vancouver "Lane Swim Delbrook Thursday 7:15-8:15am". The
   tail is cut when its range is the row's own start and end (see
   _TITLE_TAIL_RX), so the same session at three times reads as one title, to
   a person and to the grid collapse.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import html as html_mod
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

import robots_txt
from mapsee_ingest import (EventStore, NormalizedEvent, VALID_CATEGORIES, looks_online_only,
                           make_fingerprint, normalize_text, notice_reason)

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

# The only BookingType that is a drop-in schedule. 3 = registered courses,
# 4 = facility rental lists (see lesson 3).
DROPIN_BOOKING_TYPE = 2

# 90 days: what the lead asked for and what was measured. The schedules are
# published a season ahead (Brampton's max date was 2027-01-08 on 2026-10-03),
# so the horizon, not the source, decides how much reaches the map.
DEFAULT_HORIZON_DAYS = 90

# Between two requests to the SAME tenant host. Tenants are separate hosts, and
# `--workers` reads that many tenants at once, one request in flight each.
PACE_SECONDS = 1.1

# Per tenant. The live run of 2026-10-03 needed 326 requests for Brampton, the
# most of any tenant (Surrey 300, Coquitlam 235), so 600 is headroom and a
# ceiling - it is LOUD when it bites, because a silent cap reads as "we read the
# whole schedule". Per-tenant override: `max_requests`.
DEFAULT_MAX_REQUESTS = 600

# The whole run. The civic group finished in 70.8 of its 300 minutes on
# 2026-09-12; this keeps a slow platform day from taking the rest.
DEFAULT_MAX_MINUTES = 45.0

# The store is written between tenants at most this often, and always at the end.
SAVE_EVERY_SECONDS = 120

# How many tenants are read at once. Each is a different host and each keeps
# PACE_SECONDS between its own requests.
DEFAULT_WORKERS = 3

# See collapse_booking_grids. Six, as in mapsee_ingest_openactive. Measured on
# 2026-10-03 over 58,568 title/venue/days: 57,996 hold five sessions or fewer;
# of the 362 with six or more, Moose Jaw's slot grids are 284 and the rest are
# lane swims and a badminton timetable, which the stretch test keeps.
GRID_MIN_PER_DAY = 6
# ...and they have to look like one: on average this many slots per
# back-to-back stretch, a stretch being slots that start within
# GRID_JOIN_MINUTES of the last one's end. See collapse_booking_grids.
GRID_SLOTS_PER_STRETCH = 3
GRID_JOIN_MINUTES = 5

# A transient failure costs one pause and one more try; a refusal costs nothing.
# 500 is NOT retried blind: it is also how an ASP.NET anti-forgery check fails,
# so post_json answers it with the token fallback (lesson 2) instead.
_RETRYABLE_STATUS = {408, 502, 503, 504}
_REFUSAL_STATUS = {401, 403, 429}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# What is NOT a drop-in: calendars, then rows
# ---------------------------------------------------------------------------
# Matched against "<category> / <calendar>" so a tenant's "Childminding /
# ARC Childminding" and "Appointments / Skate Vault" are caught by either half.
# Measured over the 538 calendars: every hit below is a private booking or a
# service you reserve for yourself, and nothing a resident could turn up to.
# `part(y|ies)` is CALENDAR-level only: a "Halloween Skate Party" title is a
# public event, and the title rules below say `birthday` instead.
PRIVATE_CALENDAR_RX = re.compile(
    r"child\s*-?\s*minding|child\s*care|babysit|external\s+trainers?|personal\s+train|"
    r"birthday|\bpart(?:y|ies)\b|court\s+(?:reservations?|bookings?)|\brentals?\b|"
    r"facility\s+(?:visits?|bookings?|rentals?)|room\s+bookings?|orientations?|"
    r"equipment\s+(?:lending|loans?)|appointments?", re.I)

# (reason, pattern) in priority order; the first match names the refusal.
# Every pattern was read against the research's 61,381 rows before it went in:
#   * "closed" catches 300+ pool and rink annotations ("Main Pool Closed - Tots
#     pool, Hot Tub & Sauna ONLY", "Lane Swim - CLOSED for Programs") and no
#     session; "unavailable" and "bulkhead" are Nanaimo's 136 maintenance rows.
#   * "cancelled" anywhere, because "CANCELLED Winmar Toddler Turf" and
#     "'Cancelled' Group Cycle" pass mapsee_ingest.notice_reason.
#   * camps: "Boot Camp" is a fitness class (52 rows), never a camp.
#   * "(Attendance Only)" is Rochester's Fire Department booking the complex.
_ROW_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("cancelled", re.compile(r"\bcancel+ed\b|\bpostponed\b", re.I)),
    ("closure notice", re.compile(
        r"\bclosed\b|\bclosures?\b|\bunavailable\b|\bbulkhead\b|"
        r"\b(?:ice|pool|facility)\s+maintenance\b", re.I)),
    ("childminding", re.compile(r"child\s*-?\s*minding|child\s*care\b|babysit", re.I)),
    ("private booking or rental", re.compile(
        r"\brentals?\b|\brented\b|private\s+(?:bookings?|rentals?|events?|functions?|lessons?|part(?:y|ies))|"
        r"\buser\s+group\b|\breserved\s+for\b|court\s+(?:reservations?|bookings?)|birthday|"
        r"external\s+train|personal\s+train|equipment\s+(?:lending|loans?)|\bappointments?\b|"
        r"\(attendance\s+only\)|"
        # A numbered resource is a thing you book: Vaughan's "Family Bowling
        # Lane #1" to "#4", $33 each, 144 rows.
        r"\b(?:lane|court|rink|room|field|diamond|table|sheet)\s*#\s*\d+\b", re.I)),
    ("orientation", re.compile(r"\borientations?\b", re.I)),
    # Coquitlam's "Society Pickleball (Society Members Only)": 159 rows a
    # non-member cannot turn up to, which is the whole test.
    ("members only", re.compile(r"\bmembers?[\s-]+only\b", re.I)),
    ("staff or internal", re.compile(
        r"\bstaff\s+(?:only|training|meeting|use)\b|\bfor\s+staff\b|\binternal\b|"
        r"\bemployees?\s+only\b", re.I)),
    ("governance meeting", re.compile(
        r"\b(?:council|committee|board|commission)\s+meeting\b|\bAGM\b|"
        r"annual\s+general\s+meeting", re.I)),
    ("registered course or camp", re.compile(
        r"(?<!boot\s)(?<!boot)\bcamps?\b|\bcourse\b", re.I)),
)


# A MEMBERSHIP WRITTEN IN THE DETAILS IS A MEMBERS-ONLY SESSION TOO (lesson 4).
# The title rule above sees Coquitlam's "(Society Members Only)"; the same gate
# written in the prose did not, and those rows say "No fee" because a member
# books them for nothing. Read over the 78,339 rows of 2026-10-03: Surrey's
# "Seniors Services Membership required." (1,801 rows, the membership is $30 a
# year), Markham's "Fitness membership required" (1,313; "Fitness members Only"
# on markham.ca), Brampton's "...will require a Flower City Senior Membership in
# order to register for a spot in drop-ins" (2,161), NVRC's "Must have an active
# Parkgate Society Membership to attend at $10 a year" (21) and Markham's
# "required to hold a Recreation Youth Basketball Pass" (32).
# A membership the source itself calls FREE is a sign-up at the desk, not a
# gate, and stays: Markham's "requires a free Youth Basketball membership" (127)
# and "required to hold a free basketball pass" (44), Caledon's "Free Membership
# is required" (92). So do "Membership is not required." (Surrey, 33),
# "Membership or per visit drop-in fee is required" (Caledon, 93) and "for pool
# plan holders and non-members" (Vaughan, 1,138): anyone can turn up to those.
_NOT_FREE_WORDS = r"(?:(?!free\b)[\w'-]+\s+){0,4}"
MEMBERS_ONLY_DETAILS_RX = re.compile(
    r"\bmembers?[\s-]+only\b|"
    r"(?<!free\s)(?<!no\s)\bmembership\s+(?:is\s+)?required\b|"
    r"\b(?:must|should)\s+have\s+(?:a|an)\s+(?:active\s+|valid\s+|current\s+)?" + _NOT_FREE_WORDS + r"membership\b|"
    r"\brequires?\s+(?:a|an)\s+" + _NOT_FREE_WORDS + r"membership\b|"
    r"\brequired\s+to\s+hold\s+(?:a|an)\s+" + _NOT_FREE_WORDS + r"(?:membership|pass)\b", re.I)


def calendar_refusal(category: str, calendar: str, tenant: Dict[str, Any]) -> Optional[str]:
    """Why a calendar is not read, or None. Shared rule first, then the tenant's."""
    both = f"{category or ''} / {calendar or ''}"
    if PRIVATE_CALENDAR_RX.search(both):
        return "private-booking calendar"
    low = both.lower()
    for name in tenant.get("exclude_calendars") or ():
        if str(name).strip().lower() in low:
            return "excluded by the tenant's config"
    return None


def row_refusal(title: str, details: str, tenant: Dict[str, Any]) -> Optional[str]:
    """Why one row is not a drop-in anybody can turn up to, or None."""
    for reason, rx in _ROW_RULES:
        if rx.search(title or ""):
            return reason
    extra = tenant.get("exclude_title_rx")
    if extra and re.search(extra, title or "", re.I):
        return "excluded by the tenant's config"
    if MEMBERS_ONLY_DETAILS_RX.search(details or ""):
        return "members only"
    # The shared refusals every adapter shares, asked here so the count lands in
    # THIS report rather than only in EventStore's.
    if notice_reason(title):
        return "closure notice"
    if looks_online_only(title, details):
        return "online only"
    return None


# ---------------------------------------------------------------------------
# Category: the CALENDAR names the activity; the classifier sorts the rest
# ---------------------------------------------------------------------------
# docs/agents/classification-and-categories.md: a config category is a DEFAULT,
# a pure calendar should state its key, and a mixed one must state `community`
# so the promotions can run. A PerfectMind tenant is several calendars and most
# of them are pure ("Swimming", "Skating & Shinny Hockey", "Group Fitness:
# Cardio"), so the key is chosen per calendar. Order is priority:
#   * kids ONLY for calendars that are children's by construction. "Youth" is
#     not here - a youth hub serves 12-24 - and a source's own kids is never
#     demoted (McKinney's note), so the teen rows reach kids by their titles.
#   * fitness before sports, so "Fitness Centre" is not read as a gym.
#   * 55+, seniors, general interest and inclusion are `community`, the default.
_CALENDAR_CATEGORY: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("kids", re.compile(
        r"\b0\s*-\s*12\b|early\s+years|pre-?school|toddlers?|indoor\s+play(?:ground|time)?|"
        r"parent\s+participation|family\s+gym|\bkids?\b|\bchildren\b", re.I)),
    ("fitness", re.compile(
        r"aquatics?|aqua\s*-?\s*fit|\bswim(?:ming)?\b|\bpools?\b|fitness|group\s+exercise|"
        r"\bexercise\b|workout|weight\s+room|\btrack\b|\bwalking\b|climbing", re.I)),
    ("sports", re.compile(
        r"\bsports?\b|\bgym(?:nasium)?\b|basketball|volleyball|badminton|pickleball|tennis|"
        r"hockey|shinny|\bskat(?:e|es|ing)\b|\barenas?\b|\bturf\b|\bgolf\b|\bcourts?\b", re.I)),
    ("arts", re.compile(r"\barts?\b|pottery|ceramics|museum|\bcrafts?\b", re.I)),
)


def category_for_calendar(category: str, calendar: str, tenant: Dict[str, Any]) -> str:
    """The key a calendar's own NAME states; the category name if the calendar
    says nothing; else the tenant's default. A tenant may pin one by substring
    in `calendar_categories`."""
    default = tenant.get("category") or "community"
    for needle, key in (tenant.get("calendar_categories") or {}).items():
        if needle.lower() in f"{category} / {calendar}".lower() and key in VALID_CATEGORIES:
            return key
    for name in (calendar, category):
        for key, rx in _CALENDAR_CATEGORY:
            if rx.search(name or ""):
                return key
    return default


# ---------------------------------------------------------------------------
# Reading one row
# ---------------------------------------------------------------------------
def _clean(s: Any, limit: int = 4000) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = _WS.sub(" ", s).strip()
    for ch in ",.;:!?)":
        s = s.replace(" " + ch, ch)
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return s or None


# "Billiards Drop-In (55+ Years) | Bob Callahan Flower City Seniors Centre
# 11:00-2:15pm" -> "Billiards Drop-In (55+ Years)". Lesson 8. Measured over the
# 2026-10-03 run: 3,914 rows in 511 titles end in a clock range, spelled every
# way a person types one - "| Gore Meadows 8:15-9:45am", "I Bob Callahan ...
# 10:00AM-12:00PM" (a capital I for a pipe), "| |Earnscliffe", "7:00-800am",
# "3:45:-4:45pm", NVRC's "Lynn Creek Sunday 12:00-6:00pm".
#
# The tail is cut ONLY when its clock range IS the row's own start and end
# (hours compared modulo 12). Abbotsford's "Length/Public Swim - Limited Pool
# Space 8am-10am" runs 06:00-12:00: there the range qualifies the session and
# must stay. A pipe-or-I segment before it goes with it (the place is the
# venue); a weekday name goes with it (the date is the row's).
_TITLE_TAIL_RX = re.compile(
    r"(?:\s*\|[\s|]*[^|]*?|\s+I\s+(?=[A-Z])[^|]*?)?\s*"
    r"(?:\b(?:mon|tues|wednes|thurs|fri|satur|sun)days?\s+)?"
    r"(\d{1,2})(?::?(\d{2}))?\s*(?:[ap]\.?m\.?)?\s*:?\s*[-–]+\s*"
    r"(\d{1,2})(?::?(\d{2}))?\s*[ap]\.?m\.?\s*$", re.I)


def _same_clock(h: str, m: Optional[str], hm: Optional[str]) -> bool:
    return bool(hm) and int(h) % 12 == int(hm[:2]) % 12 and int(m or 0) == int(hm[3:])


def clean_title(raw: Any, start_hm: Optional[str] = None, end_hm: Optional[str] = None) -> Optional[str]:
    """The source's title, with a trailing "| place time-range" cut when that
    range is this row's own (see _TITLE_TAIL_RX). Without the row's times
    (the refusal rules), the title is only cleaned."""
    title = _clean(raw, 200)
    if not title:
        return None
    m = _TITLE_TAIL_RX.search(title)
    if m and start_hm and _same_clock(m.group(1), m.group(2), start_hm) \
            and (end_hm is None or _same_clock(m.group(3), m.group(4), end_hm)):
        return title[:m.start()].rstrip(" -|–") or title
    return title


def _clock(s: Any) -> Optional[str]:
    """'10:45 AM' -> '10:45'; '12:00 PM' -> '12:00'; garbage -> None."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?\s*", str(s or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        return None
    h = h % 12 + (12 if m.group(3).lower() == "p" else 0)
    return f"{h:02d}:{mi:02d}"


_AMOUNT_RX = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)")

# "No fee" is what the WIDGET charges, and a row the widget does not sell says
# it whatever the desk charges. Markham's 749 "Drop-In Aquafit" rows read "No
# fee" and "Drop-in Aquafit programs are not available for registration online;
# please visit the facility on the day-of" - and markham.ca prices drop-in
# aquafit at $7.58 ($5.26 at 65+). Measured 2026-10-03: the phrase is on 876
# rows, all Markham's. Such a row states no price, so it says none (lesson 7).
_NOT_SOLD_HERE_RX = re.compile(
    r"\bnot\s+available\s+for\s+(?:online\s+registration|registration\s+online)\b", re.I)


def price_line(price_range: Any, details: Any = None) -> Tuple[Optional[str], bool]:
    """(the description's fee line or None, is it free). Lesson 7: the wording
    is a contract with ../mapsee's offer tagger (0227), so FREE is said only
    when the source says "No fee" (or every amount is zero) for a row it sells,
    and a stated fee says "not free" in the words 0227's negation vetoes on."""
    text = _clean(price_range, 80)
    if not text:
        # DisplayPrices false (NVRC, Westside: 6,210 rows): the city shows no
        # price, so neither do we - and nothing here may claim a fee exists.
        return None, False
    amounts = [float(a.replace(",", "")) for a in _AMOUNT_RX.findall(text)]
    if re.fullmatch(r"(?:no\s+fee|free)\.?", text, re.I) or (amounts and all(a == 0 for a in amounts)):
        if _NOT_SOLD_HERE_RX.search(_clean(details) or ""):
            return None, False
        return "🎟 Free drop-in: no fee.", True
    # The $0 end of "$0.00 - $18.25" is a pass-holder's or an extra family
    # member's price, not everyone's.
    tail = "not free for everyone" if amounts and min(amounts) == 0 else "not free"
    return f"🎟 Drop-in fee: {text.rstrip('.')} ({tail}).", False


def age_text(row: Dict[str, Any]) -> Optional[str]:
    """'Ages 1 to 5', 'Ages 19+', 'Ages up to 12', from MinAge/MaxAge.

    An upper bound of 99 or 100 is how the platform says "no upper bound"
    (Kamloops: AgeRestrictions "54 to 99" on 59 of 262 rows, "16 to 100" on 11),
    and printing it would read as a joke. The source's own text is the fallback
    for the shapes the numbers cannot say (an age in months).
    """
    def num(v) -> Optional[int]:
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    lo, hi = num(row.get("MinAge")), num(row.get("MaxAge"))
    if hi is not None and hi >= 99:
        hi = None
    months = row.get("MinAgeMonths") or row.get("MaxAgeMonths")
    if not months and (lo or hi):
        if lo and hi:
            return f"Ages {lo} to {hi}"
        return f"Ages {lo}+" if lo else f"Ages up to {hi}"
    own = _clean(row.get("AgeRestrictions"), 80)
    if own and not re.fullmatch(r"0\s*to\s*(?:99|100)", own):
        return own if own.lower().startswith("age") else f"Ages {own}"
    return None


# AlternativeLocation is free text. On 2026-10-03 it was set on 171 rows, all
# Coquitlam's: 121 repeat the Location ("Centennial Pavilion"), 50 say
# "Spectators are not allowed". Only a different PLACE reads as "Meets at".
_SENTENCE_RX = re.compile(r"\b(?:are|is|not|no|please|must|will|may|allowed)\b", re.I)


def alternative_location(row: Dict[str, Any]) -> Optional[str]:
    """'Meets at: <place>', the source's note as it stands, or None."""
    if not row.get("HasAlternativeLocation"):
        return None
    alt = _clean(row.get("AlternativeLocation"), 120)
    if not alt or normalize_text(alt) == normalize_text(row.get("Location") or ""):
        return None
    if _SENTENCE_RX.search(alt):
        return alt if alt.endswith((".", "!", "?")) else alt + "."
    return f"Meets at: {alt}"


def _point(addr: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    try:
        lat, lon = float(addr.get("Latitude")), float(addr.get("Longitude"))
    except (TypeError, ValueError):
        return None
    # 0,0 is "no idea" in every system that has ever stored a coordinate, and
    # out of range is a parse that went wrong rather than a place.
    if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    return lat, lon


def _street_key(addr: Dict[str, Any]) -> Optional[str]:
    street = normalize_text(addr.get("Street") or "")
    return street or None


def coordinate_book(rows: Iterable[Dict[str, Any]], tenant: Dict[str, Any]
                    ) -> Callable[[Dict[str, Any]], Tuple[Optional[Tuple[float, float]], str]]:
    """A lookup for a row's point and where it came from. Lesson 6.

    The tenant's `venues` book comes FIRST: it holds the facilities whose own
    point was checked against OpenStreetMap and found elsewhere, and the
    facilities that have none. Then the row's own point, then another row's at
    the same Location, then at the same street line."""
    by_loc: Dict[str, Tuple[float, float]] = {}
    by_street: Dict[str, Tuple[float, float]] = {}
    for r in rows:
        addr = r.get("Address") or {}
        p = _point(addr)
        if not p:
            continue
        loc = normalize_text(r.get("Location") or addr.get("AddressTag") or "")
        if loc:
            by_loc.setdefault(loc, p)
        st = _street_key(addr)
        if st:
            by_street.setdefault(st, p)
    book: Dict[str, Tuple[float, float]] = {}
    for name, v in (tenant.get("venues") or {}).items():
        try:
            book[normalize_text(name)] = (float(v["lat"]), float(v["lon"]))
        except (KeyError, TypeError, ValueError):
            continue

    def lookup(r: Dict[str, Any]) -> Tuple[Optional[Tuple[float, float]], str]:
        addr = r.get("Address") or {}
        loc = normalize_text(r.get("Location") or addr.get("AddressTag") or "")
        if loc in book:
            return book[loc], "venues book"
        own = _point(addr)
        if own:
            return own, "own"
        if loc in by_loc:
            return by_loc[loc], "same Location"
        st = _street_key(addr)
        if st and st in by_street:
            return by_street[st], "same street"
        return None, "none"
    return lookup


def tenant_slug(tenant: Dict[str, Any]) -> str:
    return (tenant.get("host") or "").split(".")[0].lower()


def _base(tenant: Dict[str, Any]) -> str:
    return f"https://{tenant['host']}{tenant.get('org_path') or ''}/{tenant.get('widget_path') or 'Clients'}"


def landing_url(tenant: Dict[str, Any], widget: str, event_id: str, day: str) -> str:
    """The page the widget's own Book / More Info button opens for ONE
    occurrence (ClassBookingV2's onBookButtonClick). Checked 200 on both the
    /Clients/ and /Reports/ spellings."""
    return (f"{_base(tenant)}/BookMe4LandingPages/Class?widgetId={widget}"
            f"&redirectedFromEmbededMode=False&classId={event_id}&occurrenceDate={day}")


def to_event(row: Dict[str, Any], cal: Dict[str, Any], tenant: Dict[str, Any],
             point: Optional[Tuple[float, float]], tz) -> Tuple[Optional[NormalizedEvent], Optional[str]]:
    """One dated row, or (None, why-not). `cal` is the calendar context the row
    was read under: {id, name, category, widget, key}."""
    all_day = bool(row.get("AllDayEvent"))
    start_hm = None if all_day else _clock(row.get("FormattedStartTime"))
    end_hm = None if all_day else _clock(row.get("FormattedEndTime"))
    title = clean_title(row.get("EventName"), start_hm, end_hm)
    if not title:
        return None, "no title"
    day = str(row.get("OccurrenceDate") or "")
    if not re.fullmatch(r"\d{8}", day):
        return None, "no date"
    iso_day = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    if not point:
        # The honest count: outside the US nothing downstream can place it.
        return None, "no coordinates"
    lat, lon = point
    addr = row.get("Address") or {}

    if start_hm:
        start_local = f"{iso_day}T{start_hm}:00"
        start_dt = datetime.fromisoformat(start_local)
        end_dt = None
        if end_hm:
            end_dt = datetime.fromisoformat(f"{iso_day}T{end_hm}:00")
            if end_dt <= start_dt:
                # Past midnight, when the duration agrees; otherwise a typo.
                dur = row.get("DurationInMinutes")
                nxt = end_dt + timedelta(days=1)
                end_dt = nxt if isinstance(dur, (int, float)) and \
                    abs((nxt - start_dt).total_seconds() / 60 - dur) < 1 else None
        end_local = end_dt.isoformat() if end_dt else None
        start_utc = _to_utc(start_dt, tz)
        end_utc = _to_utc(end_dt, tz) if end_dt else None
    else:
        start_local, end_local, start_utc, end_utc = iso_day, None, None, None

    details = _clean(row.get("Details"), 520)
    fee, _free = price_line(row.get("PriceRange"), row.get("Details"))
    # Not the Facility: it is the booking system's room code ("AQ-Westsyde Pool
    # Swim Lesson Deck Space 6", "SSLCAq - Deck - Main Shallow"), which reads as
    # noise to anybody but the front desk. The building is the venue.
    facts = [x for x in (age_text(row), alternative_location(row)) if x]
    org = _clean(row.get("OrgName"), 80) or tenant.get("name")
    head = "\n".join(x for x in (fee, " · ".join(facts)) if x)
    paras = [head] if head else []
    if details:
        paras.append(details)
    # THE LAST PARAGRAPH IS SHORT ON PURPOSE: the sync's _cap_prose keeps a final
    # paragraph of <= 200 characters and trims the head instead, so this line -
    # the provenance a later audit or retirement can key on - always survives.
    paras.append(f"Drop-in schedule published by {org} (Xplor Recreation / PerfectMind).")
    description = "\n\n".join(paras)

    venue = _clean(row.get("Location"), 120) or _clean(addr.get("AddressTag"), 120)
    event_id = str(row.get("EventId") or "")
    slug = tenant_slug(tenant)
    hm = (start_hm or "allday").replace(":", "")
    ev = NormalizedEvent(
        source="perfectmind",
        # IDENTITY IS THE OCCURRENCE. EventId alone is not one: it names a run
        # of sessions (41 ids for 55 rows in one Kamloops window), so the date
        # and the clock join it, and the same session re-read tomorrow keys to
        # the same row.
        source_id=f"{slug}:{event_id}:{day}:{hm}"[:200],
        name=title,
        description=description,
        start_local=start_local, end_local=end_local,
        start_utc=start_utc, end_utc=end_utc,
        timezone=tenant.get("timezone"),
        venue_name=venue,
        latitude=lat, longitude=lon,
        address=_clean(addr.get("Street"), 160),
        city=_clean(addr.get("City"), 80) or tenant.get("city"),
        region=tenant.get("region"),
        country=tenant.get("country"),
        postal_code=_clean(addr.get("PostalCode"), 20),
        category=cal.get("key") or tenant.get("category") or "community",
        promoter=org,
        ticket_url=landing_url(tenant, cal.get("widget") or "", event_id, day) if event_id else None,
        coords_exact=True,
    )
    # TWO SESSIONS OF ONE TITLE ON ONE DAY ARE TWO EVENTS, so the clock joins
    # the cross-source key's BASIS (never the stored name), exactly as
    # mapsee_ingest_bibliocommons does for its 10:15 and 11:15 storytimes.
    ev.fingerprint = make_fingerprint(f"{title} {start_hm or ''}".strip(), iso_day, venue, ev.city)
    return ev, None


def _to_utc(dt: Optional[datetime], tz) -> Optional[str]:
    if dt is None or tz is None:
        return None
    return dt.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Booking grids (lesson 5) - mirrors mapsee_ingest_openactive.collapse_booking_grids
# ---------------------------------------------------------------------------
def _grid_key(ev: NormalizedEvent) -> Tuple[str, float, float, str]:
    """Title, point and LOCAL day. start_local is the tenant's wall clock, which
    is the day a person would be looking at."""
    return ((ev.name or "").strip().lower(), round(ev.latitude or 0.0, 5),
            round(ev.longitude or 0.0, 5), (ev.start_local or "")[:10])


def _runs(rows: List[NormalizedEvent]) -> List[Tuple[str, str, int]]:
    """Back-to-back stretches of a day's slots: [(first start, last end, slots)].
    A slot that starts within GRID_JOIN_MINUTES of the previous one's end
    continues the stretch."""
    out: List[List[Any]] = []
    for e in sorted(rows, key=lambda e: e.start_local or ""):
        s, t = (e.start_local or "")[11:16], (e.end_local or e.start_local or "")[11:16]
        if out:
            ph, pm = int(out[-1][1][:2]), int(out[-1][1][3:])
            if int(s[:2]) * 60 + int(s[3:]) - (ph * 60 + pm) <= GRID_JOIN_MINUTES:
                out[-1][1] = max(out[-1][1], t)
                out[-1][2] += 1
                continue
        out.append([s, t, 1])
    return [(a, b, n) for a, b, n in out]


def collapse_booking_grids(events: List[NormalizedEvent], min_per_day: int = GRID_MIN_PER_DAY
                           ) -> Tuple[List[NormalizedEvent], int, int, List[str]]:
    """(events, exact duplicates folded, grid slots folded, notes).

    EXACT DUPLICATES FIRST: same title, same point, same local instant is one
    listing whichever calendar or widget it came through (Caledon publishes
    "Pickleball" twice at 35 instants, Brampton its sponsored basketball in two
    calendars). Then a title one venue publishes as a GRID becomes one row for
    that day, saying in its own text which stretches its slots cover.

    A GRID IS A SHAPE, NOT A COUNT. openactive's rule is "six in a day", and
    here six is also a lane swim: Markham and Brampton run "Lane Swim" 6-7 times
    a day in 3-4 separate stretches (76 title-days on 2026-10-03), a real
    timetable whose gaps are the information. Moose Jaw's Track, Gym and Turf
    are 6-16 one-hour slots in 1-3 back-to-back stretches (284 title-days).
    So a day folds when it has `min_per_day` slots AND they average three or
    more per stretch - which separates every one of those 360 day-groups.

    IDENTITY IS THE DAY, not the first slot: keyed on a slot, a track opening at
    06:30 instead of 06:00 tomorrow would orphan today's row.
    """
    by_instant: Dict[Tuple[str, float, float, str], NormalizedEvent] = {}
    for ev in events:
        name, lat, lon, _ = _grid_key(ev)
        by_instant.setdefault((name, lat, lon, ev.start_local or ""), ev)
    dupes = len(events) - len(by_instant)
    events = list(by_instant.values())
    if min_per_day <= 1:
        return events, dupes, 0, []
    groups: Dict[Tuple[str, float, float, str], List[NormalizedEvent]] = {}
    for ev in events:
        groups.setdefault(_grid_key(ev), []).append(ev)
    out: List[NormalizedEvent] = []
    folded = 0
    notes: List[str] = []
    for key, rows in groups.items():
        if len(rows) < min_per_day or not all(len(e.start_local or "") > 10 for e in rows):
            out.extend(rows)
            continue
        runs = _runs(rows)
        if len(rows) < GRID_SLOTS_PER_STRETCH * len(runs):
            out.extend(rows)
            continue
        rows.sort(key=lambda e: e.start_local or "")
        first = rows[0]
        last_end_local = max((e.end_local or e.start_local or "") for e in rows)
        last_end_utc = max((e.end_utc or e.start_utc or "") for e in rows)
        if last_end_local > (first.start_local or ""):
            first.end_local, first.end_utc = last_end_local, last_end_utc or first.end_utc
        # The stretches, not just the first start and the last end: a turf
        # booked out from 08:00 to 12:00 is not open "06:00 to 22:00".
        first.description = (
            f"🕒 {len(rows)} drop-in time slots on this day: "
            + ", ".join(f"{a}–{b}" for a, b, _n in runs) + ".\n\n"
            + (first.description or "")).strip()
        name, lat, lon, day = key
        first.source_id = f"{first.source_id.split(':', 1)[0]}:grid:{name}|{lat},{lon}|{day}"[:200]
        first.fingerprint = make_fingerprint(first.name, day, first.venue_name, first.city)
        out.append(first)
        folded += len(rows) - 1
        notes.append(f"{first.name} @ {first.venue_name} {day}: {len(rows)} slots -> 1")
    return out, dupes, folded, notes


# ---------------------------------------------------------------------------
# HTTP: one client per tenant, paced, capped, deadline-aware
# ---------------------------------------------------------------------------
class Stop(Exception):
    """The TENANT cannot be read further this run: a refusal, the request cap or
    the run deadline. Whatever was read before it is kept."""


class FetchError(Exception):
    """One request failed for a reason that is not a refusal (a 5xx after its
    retry, a body that is not JSON). It costs the CALENDAR being walked, not
    the tenant."""


class Client:
    """Requests to ONE tenant host, PACE_SECONDS apart, at most `max_requests`,
    never past `deadline` (time.monotonic()). Thread-confined: one per tenant."""

    def __init__(self, tenant: Dict[str, Any], session=None, max_requests: int = DEFAULT_MAX_REQUESTS,
                 deadline: Optional[float] = None, pace: Optional[float] = None,
                 sleep: Optional[Callable[[float], None]] = None, clock: Callable[[], float] = time.monotonic):
        self.tenant = tenant
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": UA})
        self.session = session
        self.max_requests = max_requests
        self.deadline = deadline
        # Read at construction, not at definition, so a caller (or a test) that
        # changes the module's PACE_SECONDS is obeyed.
        self.pace = PACE_SECONDS if pace is None else pace
        self.sleep, self.clock = sleep or time.sleep, clock
        self.requests = 0
        self.token: Optional[str] = None
        self.token_fallbacks = 0
        self.retries = 0
        self._last = -1e9

    def _wait(self) -> None:
        if self.deadline is not None and self.clock() >= self.deadline:
            raise Stop("run deadline reached")
        if self.requests >= self.max_requests:
            raise Stop(f"REQUEST CAP HIT at {self.max_requests} - this tenant was NOT read "
                       f"to the end and its later sessions are missing")
        gap = self._last + self.pace - self.clock()
        if gap > 0:
            self.sleep(gap)
        self._last = self.clock()
        self.requests += 1

    def _send(self, method: str, url: str, data=None, headers=None):
        for attempt in range(2):
            self._wait()
            try:
                r = self.session.request(method, url, data=data, headers=headers, timeout=45)
            except Exception as exc:                          # noqa: BLE001 - timeouts, resets
                if attempt == 0:
                    self.retries += 1
                    self.sleep(3)
                    continue
                raise FetchError(f"{type(exc).__name__}: {exc}")
            if r.status_code in _REFUSAL_STATUS:
                # A refusal is the operator's answer. Never retried, never
                # re-asked with another User-Agent (AGENTS.md).
                raise Stop(f"HTTP {r.status_code} - refused; not retried")
            body = (r.text or "")[:6000] if r.content else ""
            if robots_txt.CHALLENGE_RX.search(body):
                raise Stop("bot challenge - refused; not retried")
            if r.status_code in _RETRYABLE_STATUS and attempt == 0:
                self.retries += 1
                self.sleep(3)
                continue
            return r
        return r

    def _load_token(self, widget: str) -> None:
        url = f"{_base(self.tenant)}/BookMe4?widgetId={widget}"
        self.token_fallbacks += 1
        self.token = ""
        r = self._send("GET", url)
        m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', r.text or "")
        self.token = m.group(1) if m else ""

    def post_json(self, url: str, data: Dict[str, Any], widget: str) -> Any:
        """POST the way the widget's own XHR does. Lesson 2: without the
        anti-forgery token first; with it only if the tenant insists."""
        hdr = {"X-Requested-With": "XMLHttpRequest"}
        payload = dict(data)
        if self.token:
            payload["__RequestVerificationToken"] = self.token
            hdr["__RequestVerificationToken"] = self.token
        r = self._send("POST", url, data=payload, headers=hdr)
        parsed = _json(r)
        if parsed is None and self.token is None and r.status_code in (200, 400, 500):
            self._load_token(widget)
            if self.token:
                return self.post_json(url, data, widget)
        if parsed is None:
            raise FetchError(f"HTTP {r.status_code}, not JSON")
        return parsed


def _json(r) -> Any:
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def walk_calendar(client: Client, url: str, calendar_id: str, widget: str,
                  today: date, horizon_end: date) -> Tuple[List[Dict[str, Any]], str, Optional[Stop]]:
    """Every row one calendar has from `today` until `horizon_end`, why the walk
    stopped, and the Stop that ended the TENANT if one did (its rows are kept).
    ClassBookingV2Controller.loadItems, copied (lesson 1)."""
    rows: List[Dict[str, Any]] = []
    page, after, date_string, empties = 0, None, today.isoformat(), 0
    limit = horizon_end.isoformat()
    while True:
        data = {"calendarId": calendar_id, "widgetId": widget, "page": page, "dateString": date_string}
        if after:
            data["after"] = after
        try:
            body = client.post_json(url, data, widget)
        except FetchError as exc:
            return rows, f"stopped early: {exc}", None
        except Stop as stop:
            return rows, f"stopped: {stop}", stop
        classes = body.get("classes") if isinstance(body, dict) else None
        if not classes:
            # loadZeroEventsInARow: the page moves on, `after` does not.
            empties += 1
            if empties > 1:
                return rows, "two empty answers in a row", None
            page += 1
            continue
        empties = 0
        rows.extend(c for c in classes if isinstance(c, dict))
        nk = str(body.get("nextKey") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", nk) or nk.startswith("0001"):
            return rows, "end of schedule", None
        if after and nk <= after:
            # A cursor that does not advance would loop until the cap.
            return rows, f"nextKey did not advance ({nk})", None
        if nk >= limit:
            return rows, "horizon reached", None
        after = nk
        date_string = (date.fromisoformat(nk) + timedelta(days=1)).isoformat()


def read_tenant(tenant: Dict[str, Any], today: date, horizon_end: date,
                deadline: Optional[float] = None, client: Optional[Client] = None,
                robots: Optional[robots_txt.Robots] = None) -> Dict[str, Any]:
    """Fetch only - no store, so it can run in a worker thread. Returns the raw
    rows with the calendar each was read under, and a report."""
    client = client or Client(tenant, max_requests=int(tenant.get("max_requests") or DEFAULT_MAX_REQUESTS),
                              deadline=deadline)
    report: Dict[str, Any] = {"notes": [], "calendars": {}, "refused_calendars": {}, "rows": [],
                              "stopped": None}
    base = _base(tenant)
    cats_url = f"{base}/BookMe4V2/GetCategoriesDataV2?embed=False"
    classes_url = f"{base}/BookMe4BookingPagesV2/ClassesV2"

    # A tenant still queued when the run deadline passes asks nothing, not even
    # for robots.txt (that read does not go through Client._wait).
    if deadline is not None and client.clock() >= deadline:
        report["stopped"] = "run deadline reached before this tenant started"
        report["requests"] = 0
        return report

    # ROBOTS, EVERY RUN. 404 today on every tenant; the day one starts refusing,
    # this is where the adapter stops reading it. The start page is asked too:
    # it is read only as the token fallback (lesson 2), but it is still a read.
    robots = robots or robots_txt.Robots(client.session)
    start_urls = [f"{base}/BookMe4?widgetId={w}" for w in tenant.get("widgets") or ()]
    for u in [cats_url, classes_url] + start_urls:
        verdict = robots.check(u)
        if verdict.get("allowed") is not True:
            report["stopped"] = (f"robots.txt {verdict.get('status')}: "
                                 f"{verdict.get('rule') or 'permission not established'} - skipped")
            report["requests"] = client.requests + 1
            return report
    report["robots"] = robots.check(cats_url).get("status")

    seen_calendars: Dict[str, Dict[str, Any]] = {}
    try:
        for widget in tenant.get("widgets") or ():
            try:
                cats = client.post_json(cats_url, {"widgetId": widget}, widget)
            except FetchError as exc:
                report["notes"].append(f"widget {widget}: calendars unreadable - {exc}")
                continue
            for cat in cats if isinstance(cats, list) else ():
                for cal in cat.get("Calendars") or ():
                    cid = str(cal.get("Id") or "")
                    if not cid or cid in seen_calendars:
                        # Lesson 3: one calendar, two widgets, read once.
                        if cid:
                            report["duplicate_calendars"] = report.get("duplicate_calendars", 0) + 1
                        continue
                    bt = (cal.get("BookingTypeInfo") or {}).get("BookingType")
                    entry = {"id": cid, "name": cal.get("Name") or "", "category": cat.get("Name") or "",
                             "widget": widget, "booking_type": bt}
                    seen_calendars[cid] = entry
                    if bt != DROPIN_BOOKING_TYPE:
                        why = f"BookingType {bt} (not drop-in)"
                    else:
                        why = calendar_refusal(entry["category"], entry["name"], tenant)
                    if why:
                        report["refused_calendars"].setdefault(why, []).append(
                            f"{entry['category']} / {entry['name']}")
                        continue
                    entry["key"] = category_for_calendar(entry["category"], entry["name"], tenant)
                    report["calendars"][cid] = entry
        for cid, entry in report["calendars"].items():
            rows, why, stop = walk_calendar(client, classes_url, cid, entry["widget"], today, horizon_end)
            entry["rows"], entry["why"] = len(rows), why
            report["rows"].extend((entry, r) for r in rows)
            if stop is not None:
                raise stop
    except Stop as stop:
        report["stopped"] = str(stop)
    report["requests"] = client.requests + 1          # + the robots.txt read
    report["token_fallbacks"] = client.token_fallbacks
    report["retries"] = client.retries
    return report


# ---------------------------------------------------------------------------
# Rows -> events, with every refusal counted
# ---------------------------------------------------------------------------
def _tz(name: Optional[str]):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:                                   # noqa: BLE001
        return None


def _now_local(tz) -> datetime:
    """MAPSEE_TODAY=YYYYMMDD fixes "now" (to that day's midnight) for the tests."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d")
    return datetime.now(tz).replace(tzinfo=None) if tz else datetime.now()


def build_events(tenant: Dict[str, Any], pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
                 now_local: datetime, horizon_end: date
                 ) -> Tuple[List[NormalizedEvent], Dict[str, int], List[str], Dict[str, int]]:
    """(events, refused-by-reason, notes, stats) for one tenant's raw rows."""
    tz = _tz(tenant.get("timezone"))
    refused: Dict[str, int] = {}
    notes: List[str] = []
    seen_occ = set()
    lookup = coordinate_book((r for _c, r in pairs), tenant)
    placed: Dict[str, int] = {}
    events: List[NormalizedEvent] = []

    def refuse(why: str) -> None:
        refused[why] = refused.get(why, 0) + 1

    for cal, row in pairs:
        occ = (row.get("EventId"), row.get("OccurrenceDate"), row.get("FormattedStartTime"))
        if occ in seen_occ:
            refuse("same occurrence read twice")
            continue
        seen_occ.add(occ)
        if row.get("BookingType") not in (None, DROPIN_BOOKING_TYPE):
            refuse(f"BookingType {row.get('BookingType')} row")
            continue
        day = str(row.get("OccurrenceDate") or "")
        if re.fullmatch(r"\d{8}", day) and day >= horizon_end.strftime("%Y%m%d"):
            refuse("beyond horizon")
            continue
        why = row_refusal(clean_title(row.get("EventName")) or "", _clean(row.get("Details")) or "", tenant)
        if why:
            refuse(why)
            continue
        point, how = lookup(row)
        if how != "own":
            placed[how] = placed.get(how, 0) + 1
        ev, why = to_event(row, cal, tenant, point, tz)
        if not ev:
            refuse(why or "?")
            continue
        events.append(ev)

    # The grid decision is made BEFORE the past filter, so slots this adapter
    # would drop as past still count toward the day's shape. That does NOT make
    # TODAY's grid row stable: the source itself stops returning slots once they
    # have started, so a run partway through a grid day can see too few to fold
    # and writes slot rows beside the day row an earlier run stored. Measured
    # 2026-10-03, Moose Jaw's Gym at 19:06 local: 2 of the day's 9 slots came
    # back; from Oct 5 to Dec 31, 0 of 274 grid days changed shape. A run
    # near the tenants' local midnight sees whole days.
    events, dupes, folded, grid_notes = collapse_booking_grids(
        events, int(tenant.get("grid_min_per_day", GRID_MIN_PER_DAY)))
    if dupes:
        refused["exact duplicate (same title, place and time)"] = dupes
    if folded:
        notes.append(f"booking grids: {folded} slot row(s) folded into {len(grid_notes)} day row(s)")
        notes.extend("  " + n for n in grid_notes[:5])
        if len(grid_notes) > 5:
            notes.append(f"  ...and {len(grid_notes) - 5} more")
    kept: List[NormalizedEvent] = []
    now_s = now_local.strftime("%Y-%m-%dT%H:%M:%S")
    for ev in events:
        ends = ev.end_local or ev.start_local or ""
        if len(ends) == 10:
            ends += "T23:59:59"
        if ends < now_s:
            refuse("past")
            continue
        kept.append(ev)
    placed.pop("none", None)
    if placed:
        notes.append("placed by other than the row's own point: "
                     + ", ".join(f"{n} by {how}" for how, n in sorted(placed.items())))
    return kept, refused, notes, {"grid_folded": folded, "grid_days": len(grid_notes),
                                  "borrowed": sum(placed.values()), "placed": placed}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _rotate(tenants: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    """Start at a different tenant each day, so the deadline - if it ever bites -
    does not starve the same tail of the list every run."""
    if not tenants:
        return tenants
    k = today.toordinal() % len(tenants)
    return tenants[k:] + tenants[:k]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import municipal drop-in schedules from PerfectMind BookMe4 widgets.")
    ap.add_argument("--config", default="perfectmind_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one tenant by name or host (substring)")
    ap.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help="tenants read at once; each is its own host, paced on its own")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    tenants = [t for t in cfg.get("tenants", [])
               if not t.get("skip") and (not a.only or a.only.lower() in
                                         f"{t.get('name', '')} {t.get('host', '')}".lower())]
    if not tenants:
        print("[perfectmind] no tenants selected", flush=True)
        return 0
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    today = _now_local(None).date()
    store = None if a.dry_run else EventStore(a.store)
    lock = threading.Lock()
    totals = {"kept": 0, "requests": 0}
    last_save = [time.monotonic()]

    def fetch(t: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        horizon = int(t.get("horizon_days") or a.horizon_days)
        tz = _tz(t.get("timezone"))
        t_today = _now_local(tz).date()
        return t, read_tenant(t, t_today, t_today + timedelta(days=horizon), deadline)

    with cf.ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        futures = [pool.submit(fetch, t) for t in _rotate(tenants, today)]
        for fut in cf.as_completed(futures):
            try:
                tenant, report = fut.result()
            except Exception as exc:                    # noqa: BLE001
                # One tenant must never cost the others (the Tribe lesson).
                print(f"[perfectmind] a tenant FAILED: {type(exc).__name__}: {exc}", flush=True)
                continue
            label = tenant.get("name") or tenant.get("host")
            try:
                tz = _tz(tenant.get("timezone"))
                horizon = int(tenant.get("horizon_days") or a.horizon_days)
                now_local = _now_local(tz)
                events, refused, notes, _stats = build_events(
                    tenant, report["rows"], now_local, now_local.date() + timedelta(days=horizon))
            except Exception as exc:                    # noqa: BLE001
                print(f"[perfectmind] {label}: FAILED building rows - {type(exc).__name__}: {exc}", flush=True)
                continue
            for why, names in sorted(report["refused_calendars"].items()):
                print(f"[perfectmind] {label}: {len(names)} calendar(s) not read - {why}"
                      + (f": {', '.join(names[:6])}" if not why.startswith("BookingType") else ""), flush=True)
            for cal in report["calendars"].values():
                print(f"[perfectmind] {label}:   {cal['category']} / {cal['name']} [{cal.get('key')}]: "
                      f"{cal.get('rows', 0)} row(s), {cal.get('why', 'not read')}", flush=True)
            if report.get("stopped"):
                print(f"[perfectmind] {label}: STOPPED - {report['stopped']}", flush=True)
            for n in report.get("notes") or ():
                print(f"[perfectmind] {label}: {n}", flush=True)
            for n in notes:
                print(f"[perfectmind] {label}: {n}", flush=True)
            if refused:
                detail = ", ".join(f"{n} {w}" for w, n in sorted(refused.items(), key=lambda kv: -kv[1]))
                print(f"[perfectmind] {label}: refused {sum(refused.values())} of {len(report['rows'])} "
                      f"({detail})", flush=True)
            print(f"[perfectmind] {label}: kept {len(events)} drop-in session(s) "
                  f"in {report.get('requests', 0)} request(s)"
                  + (f", {report['token_fallbacks']} token fallback(s)" if report.get("token_fallbacks") else ""),
                  flush=True)
            with lock:
                totals["kept"] += len(events)
                totals["requests"] += report.get("requests", 0)
                if store is not None:
                    for ev in events:
                        store.upsert(ev)
                    # Between tenants, so a step cancelled by its timeout keeps
                    # the work - but at most every SAVE_EVERY_SECONDS: in CI this
                    # store is the civic group's shared feeds_events.json, and
                    # this run alone added 91 MB of it (70,408 rows, 2026-10-03).
                    if time.monotonic() - last_save[0] >= SAVE_EVERY_SECONDS:
                        store.save()
                        last_save[0] = time.monotonic()
    took = (time.monotonic() - started) / 60
    if store is not None:
        store.save()
        print(f"[perfectmind] wrote {totals['kept']} session(s) in {totals['requests']} request(s), "
              f"{took:.1f} min; store now holds {len(store.records)} unique events.", flush=True)
    else:
        print(f"[perfectmind] dry run - {totals['kept']} session(s) would be written; "
              f"{totals['requests']} request(s), {took:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
