#!/usr/bin/env python3
"""
mapsee_dedupe_events.py — hide the second row of one imported event, and record
which row it copied.

WHAT IT DOES (since 2026-10-13). One event is sometimes on the map twice: a
source renamed it ("Halloween Pet Portraits" became "... Fundraiser" on Discover
SLU, and the fingerprint is hashed from the title, so the sync inserted a second
row beside the first), two adapters read the same library programme, or two
ticket sites list the same show. The upsert cannot delete, so nothing at import
can take the first row away. This job finds those pairs in the table and HIDES
the copy.

  • It lists ONE UTC DAY of LIVE imported rows at a time (visible, not standing,
    not pin_only, not private, not yet ended): yesterday's rows still running,
    every day of --days, then the rows that started up to --lookback days ago
    and are still running, last (window_order says why). Every listing is a
    keyset with a starts_at lower bound the index can use; never offset=, never
    count=; the part of a day that has already started is listed in slices
    that halve on a statement timeout (list_window). --max-seconds covers the
    listing, the reads and the writes.
  • Two rows are one event when they are at most RADIUS_M apart, neither title
    is a ticket add-on, and one of three rules holds (match()):
      same-page     the same event page ("Tickets / info:" link that names an
                    event, not a listing page), the same start instant, similar
                    titles, and rows created at least TOGETHER_H apart (one read
                    that lists two records on one page is two records);
      same-slot     the same start instant, on different pages or none: equal
                    titles with no link, an anchored or typo-level title match
                    across two publishers, word-for-word containment within one
                    - but never two pages of a booking platform (two sessions,
                    whichever row is first), nor one publisher's two place names
                    made in one read (two venues it pins to one coordinate), nor
                    two clockless day rows made in one read under two titles
                    (two weekdays' classes), nor across publishers a title of
                    one or two words beside one that adds who it is for ('Yoga'
                    / 'Yoga for Seniors'), nor two titles whose level words
                    differ ('Intermediate' / 'High Intermediate ... (invite
                    only)'), nor '<a service> at <the other title>' on its own
                    page ('Vote Riders Identification Clinic at Our Space');
      all-day-copy  the same event page, one row a one-day span (23-25 h) that
                    holds the other's start: a feed that gives a date where the
                    other gives a clock (made in one read: one title only; a
                    timed row titled '<the day's title>: <a part>' is a part).
    Two rows that start together but end END_GAP_H+ apart are two (an
    exhibition and its vernissage). A link that names no event, a month
    ('october2026') or only a date in its query is a listing page. Category is
    NOT compared: 305 of 1,012 matched pairs are filed under two.
  • One row of each cluster survives: claimed, then one people are on (an RSVP,
    chat, a live promotion), then a row with a clock over a clockless copy, then
    a row whose link is its own page (not another date's), then the whole event
    over a part of it on another page or a package sold beside it ('...
    Courtside Experience') and over a row whose link is another event's undated
    page, then the newest import (the source's current title); an exact
    creation tie goes to not a 'Copy of', then to the fewer title words, then
    id. Only a member that matched the survivor DIRECTLY, is unclaimed, has
    nobody on it and does not say the event is off when the survivor does not
    ('Canceled:', 'Entfällt:': left for a person) is hidden.
  • THE KEPT ROW CARRIES ITS COPIES' CATEGORIES (merged_categories, one function
    in mapsee_supabase_sync, which applies it whenever it rewrites a kept row,
    as mapsee_reclassify.py does): a door, and every door's category chips,
    match `category = any(keys) OR categories && keys`, so without it a hide
    took the event out of the copy's doors (2026-10-15: 127 of 558 planned
    hides, 93 of them a whole door). A copy whose categories do not fit stays
    visible: 0108 allows two secondaries, and a door that leaves out a primary
    (bar.ventures: never a market) must neither gain nor lose the event. A
    copy whose link is another event's page carries nothing (copy_counts): its
    categories are that event's. Every run first checks the rows hidden on
    earlier nights (kept_rows_pass): a kept row that lost the union gets it
    back, and a copy that no longer fits is un-hidden.
  • A hidden copy gets hidden_at AND duplicate_of = the survivor's id (../mapsee
    migration 0236). Nothing is deleted: a delete cascades RSVPs and chat, and
    the next sync's existence read would have re-inserted the key. A hidden row
    still counts as existing to fetch_import_state, so --only-new never brings
    it back; to_row writes neither column, so Wednesday's refresh leaves it; the
    sync lifts only its own cancellation (cancelled_at == hidden_at).
  • REPORT ONLY unless --apply, and --apply is refused (a report, exit 1) while
    events.duplicate_of does not exist. In CI the switch is the repository
    variable DEDUPE_APPLY: unset = report; 'bounded' = the nightly job reports
    and runs `--apply --days 0 --lookback 0` (no window: only kept_rows_pass,
    nothing new hidden), and dedupe-events.yml may apply at most 7 days each
    way; 'true' = the nightly job hides too. --unhide --stamp S reverses exactly
    one run.

WHY IT CHANGED. Measured 2026-10-13 (census of 25,369 live imported rows in 11
metros): 1,153 redundant rows (4.5%; Chicago 11.1%). The old rule (same
normalized title + category + start, 1.5 km single-link) reached 282 of 1,373
true pairs and none of the renames or all-day copies, and its listing had no
starts_at floor (~1.49M planned rows, sized for 193k): the step ran 12m12s, its
12-minute timeout, on most runs 2026-09-22..10-07 and 4-5 minutes from 10-08,
exiting 1 every time while it showed green (its log was never read; a statement
timeout from 10-08 is inferred, not seen). The first revision's rules, replayed
through main() over the census rows: 866 hidden, 1,040 of 1,373 true pairs resolved,
redundancy 1,153 -> 308 rows (1.2%), 4 hides on links it labelled listing pages;
over its 15,632 labelled pairs, with each link's titles counted census-wide, 994
true matched and 0 that it labelled not-a-duplicate. Deliberately left out: re-timed
and re-dated copies (the shape of a matinee or a weekly occurrence), cross-posts
whose titles name different things, standing rows, and the 6 cross-source pairs
whose starts differ (which includes the market whose sources disagree about the
clock: the market adapter's name + geohash identity collapses those at import
now). REVISED the same day after two reviews of the first build's 1,069
production report hides: the booking, two-venue, end-gap, listing-page and
survivor terms above; re-decided, 914 of those are unchanged, 107 no longer
hidden, 47 keep the other row, 1 is left for a person.
REVISED AGAIN after the second safety review read a fresh day by hand (2026-10-15,
558 planned hides, 13 wrong: 3 distinct events, 10 wrong survivors): add-on rows,
two clockless classes, undated wrong-site links, the booking test both ways, the
import's off-words, and the kept row's categories. Re-planned cluster by cluster:
541 unchanged, 8 keep the other row, 2 behind another row, 3 no longer matched,
4 left visible because their categories do not fit; 4 of the 553 hides remain
wrong (a France Travail workshop beside another agency's webinar, three wrong-site
survivors). The census replay through main(): 805 hidden, 973 of 1,373 true pairs
resolved, redundancy 1,153 -> 369 (1.45%). Labelled pairs: 991 true, 0 not.
REVISED A THIRD TIME after a third safety read of a fresh day (2026-10-16, 547
planned hides, 8 wrong: 5 of the 103 same-slot hides within one publisher were
distinct sessions) and the coverage re-read: two level words, a service held at
the other, the door that leaves out a market, and a copy whose link is another
event's page. 10-16 re-planned offline from the saved listing (the old module
reproduces its 547 exactly): 542, of which 3 wrong (two branches on one pin, two
wrong-site survivors); 10-15: the same 553, 4 wrong. The census replay: 804
hidden, 972 of 1,373 true pairs resolved, redundancy 370 (1.46%; Chicago 81,
2.5%); 58 copies stay visible for want of room, 56 of them on Chicago Public
Library links, every one beside or behind a row of the open-data feed this
branch retires (50 BiblioCommons rows beside an open-data kept row); without
that rule 862, 1,036 and 312. Replayed without the 238 open-data-shaped CPL
rows (the state once the feed is gone): 629 hidden, 308 redundant (1.23%;
Chicago 19, 0.6%), 2 left for want of room, 0 doors lost but a wrong-site
copy's own. Labelled pairs: 990
true, 0 not.

HISTORY — what this file was for before, kept because the reasons still hold.

A market reaches the map from up to three places: a curated city list,
OpenStreetMap's `amenity=marketplace`, and the USDA Local Food Directories. They
agree on the name and on the coordinates to within a few hundred metres, and
disagree on the address string. The import fingerprint used to be keyed on that
address, so one market was filed three times and drew three pins.
mapsee_ingest_markets.py keys identity on the name plus a ~5km geohash cell,
which collapses the vast majority at import. The database is still the one place
where every source's rows meet (sources run on different weekdays and the store
is rebuilt each run, so two sources' duplicates are never in memory together).

NOT ONLY MARKETS, since 2026-08-12: 407 clusters, 428 redundant rows in thirteen
categories over the 20,000 nearest upcoming rows. A MATINEE IS NOT THE EVENING
SHOW: everything keys on the exact start instant, never on a date, because two
performances of one show share a title, a venue and a day. That is still why
same-slot and same-page compare instants, and why the one rule that does not
(all-day-copy) needs one row to be a whole-day span and both to share a page.

STILL RUNNING COUNTS AS CURRENT (2026-08-12: 1,354 redundant rows were rows that
had started and not ended): the --lookback windows are that lesson.

SAFETY. Scoped to `external_source = 'mapsee'`, asserted rather than trusted, so
a user's own events can never be matched. Every write carries that scope plus
claimed_at=is.null and hidden_at=is.null (a kept row's categories also the values
they were read with), so the database decides at write time. A hide filter that
matches fewer than half of 20+ rows it is sent exits 1.
A window refuses if one cluster exceeds --max-cluster (the census's largest true
cluster was 7) or its hides exceed max(SHARE_FLOOR, --max-share of its rows).

EXIT CODE. Non-zero when anything is left owed: a window the budget never
reached (BACKLOG), a window whose listing or reads failed, a refusal, a write
that failed, or --apply asked for without the column. It used to print that and
exit 0 under `|| true`, so a job that had stopped keeping up looked exactly like
a clean one. Same reasoning as runCronTask in ../mapsee.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; MAPSEE_TODAY=YYYYMMDD fixes now
      (that day at 12:00Z), for tests.
Run:  python mapsee_dedupe_events.py                            # report
      python mapsee_dedupe_events.py --apply --report r.json    # hide
      python mapsee_dedupe_events.py --unhide --stamp 2026-10-14T06:30:00+00:00
      python mapsee_dedupe_events.py --unhide --stamp ... --apply
"""
import argparse
import difflib
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import normalize_text
# The sync's own title helpers, so the audience guard ("ages 6+" is not
# "ages 60+", "[Goalies]" is not the skaters' session) stays ONE copy.
from mapsee_supabase_sync import _AUDIENCE_WORD, _ident_words, _missing_column, _similar_titles
# ...and the sync's one rule for what a kept row carries of its copies'
# categories: the sync applies the same function whenever it rewrites a kept
# row, so the two writers never disagree about the value.
from mapsee_supabase_sync import adds_categories, categories_cover, copy_counts, merged_categories
# ...and how a row's own link is read: the union leaves out a copy whose link is
# another event's page (copy_counts), and the survivor terms use the same tests.
from mapsee_supabase_sync import _LEAF_DROP, _STRONG_ID, canonical_link, link_names_row, link_ok

PAGE = 1000                    # rows per listing page
ID_BATCH = 100                 # ids per id=in.(...) read or write
WORKERS = 8                    # concurrent hide PATCHes
SHARE_FLOOR = 25               # a window may always hide this many
TIMEOUT_CODE = "57014"         # postgres: canceling statement due to statement timeout
SELECT = ("id,title,lat,lon,starts_at,ends_at,created_at,claimed_at,last_message_at,promoted_until,"
          "place_name,category,categories")
LIVE = "&hidden_at=is.null&pin_only=not.is.true&recurring_hours=is.null&is_private=is.false"

RADIUS_M = 150.0          # census: 287 of the old rule's 728 drops were >150 m (branches, centres)
TOGETHER_H = 12.0         # census: 0 false positives; NYC Tennis, Status Mic were made in one read
# Same start, ends further apart than this: an exhibition and its vernissage.
# Census: 1,098 of 1,100 true same-start pairs end under 6 h apart, 0 at 6-48 h;
# production: a 25 h pop-up beside its 1 h slot ends exactly 24 h apart.
END_GAP_H = 12.0
GENERIC_PATHS = {"", "events", "event", "calendar", "whats-on", "agenda"}
GENERIC_TITLES = 2        # a link carrying more than this many distinct titles is a listing page
ALL_DAY_MIN_H, ALL_DAY_MAX_H = 23.0 - 1.0 / 60.0, 25.0
GRID_DEG = 0.002          # candidate grid: 222 m of latitude, wider than RADIUS_M
RUNNING_SLICE = timedelta(hours=6)       # first slice of the part of a window that has started
RUNNING_FLOOR = timedelta(minutes=15)    # a statement timeout halves the slice down to this
# 'NOT A SHOW TICKET', 'not a concert ticket', 'not an event': a row that sells
# something beside the event. Up to two words between 'not a' and 'ticket'
# (production 2026-10-15: 'The Pre-Show Speakeasy with Hazlett - NOT A SHOW
# TICKET' hid the concert; the census's labelled pairs: 0 new matches).
NOT_AN_EVENT = re.compile(r"not an? (?:[a-z]+ ){0,2}ticket|not an event|\badd[ -]?on\b|parking|"
                          r"express entry|amplified access", re.I)
STOP = {"the", "a", "an", "and", "at", "vs", "v", "versus", "with", "w", "featuring", "feat",
        "ft", "presents", "live", "in", "of", "de", "la", "le"}
ARMS = ("same-page", "same-slot", "all-day-copy")

# One host, many publishers: two groups on it are two publishers, so "made in
# one read" says nothing about one venue (see two_venues).
MULTI_TENANT = ("meetup.com", "openagenda.com", "eventbrite", "facebook.com", "lu.ma", "luma.com")
# Booking platforms: one timetable, a page per session, and sessions that share a
# title and a start are different pools, studios or centres (Everyone Active
# 'Public Swimming Tue 19:00' ...AM190096 / ...AT190096). Two of their pages at
# one slot are two sessions; only the SAME page can be one session listed twice.
# The census's own c1? kind excluded the first four for this reason.
BOOKING_HOSTS = ("book.everyoneactive.com", "bookings.better.org.uk", "apm.activecommunities.com",
                 "sfpl.org", "placesleisure.org", "operations.daxko.com", "perfectmind.com")
# A word naming WHO a session is for, in the languages the census met that the
# sync's _AUDIENCE_WORD lacks. Local on purpose: the sync's absent_successors
# uses that one, and widening it there changes what a re-keyed row matches.
AUDIENCE_EXTRA = re.compile(
    r"frauen|manner|maenner|kinder|jugendliche|senioren|eltern|familien?|mujeres|hombres|ninos|ninas|"
    r"jovenes|mayores|donne|uomini|bambini|ragazzi|anziani|vrouwen|mannen|kinderen|ouderen|kvinnor|"
    r"barn|ungdom|damer|herrer|kvinder|børn")     # NFKD does not fold ø: Danish 'børn' stays
# A title that says the event is off. Never hidden behind one that does not
# say so: which is current is for a person (cancelled-events.md).
# Matched against _ident_words (lower case, accents folded: 'inställt' is
# 'installt'); 'Not cancelled - ...' is not a mark.
# The import's own off-words (mapsee_ingest._OFF_WORDS: 'Entfällt', 'Fällt
# aus', 'verschoben') are here too, or the part rule keeps a stale 'Offener
# Treff' over 'Entfällt: Offener Treff'.
CANCEL_MARK = re.compile(r"(?<!\bnot )\b(cancel+ed|postponed|abgesagt|annulee?|aplazad[oa]|cancelad[oa]|"
                         r"suspendid[oa]|annullat[oa]|rinviat[oa]|afgelast|geannuleerd|aflyst|avlyst|"
                         r"installt|peruttu|entfallt|verschoben|fallt aus)\b")
COPY_OF = re.compile(r"^\W*copy of\b", re.I)
PLACE_STOP = {"the", "de", "la", "le", "at", "of", "and", "a", "du", "des", "ny", "nyc", "co", "mo", "in", "on"}
# Words that name the KIND of room, not which one: 'The Yard at Chicago
# Shakespeare' is a stage of 'Chicago Shakespeare Theater', not another venue.
VENUE_WORDS = {"theater", "theatre", "centre", "center", "hall", "stage", "auditorium", "venue", "building"}
# A longer title that is "<a part> <separator> <the shorter title>": a booth at
# a carnival, an activity at a market, club seating for a concert (part_of).
PART_SEPS = {"|", ":", "@", "-", "–", "—", "at", "bei", "au", "im"}
# A longer title that is the shorter one PLUS a word that names a package sold
# beside it: 'San Antonio Spurs vs. Phoenix Suns Courtside Experience',
# 'Khalid - VIP Packages', 'Epik High HQ Upgrade' (addon_of). Matched against
# _ident_words, so 'Pre-Show' is the two words 'pre show' (a pair of words is
# tried joined) - and production 2026-10-15 put the game in a cluster with
# three such rows: '... Courtside Experience', '... Frost Club Level Access'
# and '... Post-Game Free Throw'.
ADDON_WORDS = {"courtside", "seating", "vip", "package", "packages", "upgrade", "upgrades", "hospitality",
               "premium", "preshow", "speakeasy", "suite", "suites", "warmup", "warmups", "parking",
               "access", "experience", "postgame", "pregame"}
PART_ARTICLES = {"the", "der", "die", "das", "le", "la", "les"}
# A part whose separator says WHERE it is held ('<a clinic> at <the programme>')
# is a listing of its own, not a copy (held_at). Production 2026-10-16: four
# services 'X at Our Space' (VoteRiders, a resource navigator, a veterans'
# housing charity, the county's mental-health team), each on its own library
# page with its own text, hidden behind the library's 'Our Space', whose text
# names none of them. Refusing the shape costs 1 census pair (labelled c1?: a
# talk 'au Salon SME' beside the fair) and 1 of the build's 1,069 hides
# ('Nourish Moves at the Market Square Farmers Market', judged distinct).
HELD_AT_SEPS = {"at", "@", "bei", "au", "im"}
# Words that name a session's LEVEL. Two titles at one slot whose level words
# differ are two sessions ('Intermediate Indoor volleyball' was hidden behind
# 'High Intermediate Indoor Volleyball (invite only)', one Meetup group, one
# court, 2026-10-16). 'high', 'upper', 'lower' count only before a level word
# ('high intermediate'), and 'only' is not one: with it, 'Clint Black ... 8
# Nights Only' would have been refused on 10-15. Cost: 0 census pairs, 0 of the
# 10-15 plan, 0 of the build's 1,069.
LEVEL_WORDS = {"beginner", "beginners", "intermediate", "advanced", "improver", "improvers", "novice", "elite",
               "invite", "competitive", "recreational"}
LEVEL_QUALIFIERS = {"high", "upper", "lower"}
_SEQ_RX = re.compile(r"\w+|[|:@–—]|(?<!\S)-(?!\S)")

_sleep = time.sleep            # patched by the tests
_clock = time.monotonic


def _timed_out(resp) -> bool:
    return resp.status_code >= 500 and TIMEOUT_CODE in (resp.text or "")


def _p(*args) -> None:
    print(*args, flush=True)


# --------------------------------------------------------------------------- #
# THE RULES (pure; test_dedupe_events.py)
# --------------------------------------------------------------------------- #
def utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def metres(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, h)))


def link_host(link: str) -> str:
    return link.split("/", 1)[0]


def same_publisher(la: str, lb: str) -> bool:
    ha, hb = link_host(la), link_host(lb)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def _on(host: str, names: Iterable[str]) -> bool:
    return any(host == n or host.endswith("." + n) or ("." not in n and n in host.split("."))
               for n in names)


def multi_tenant(link: str) -> bool:
    return _on(link_host(link), MULTI_TENANT)


def booking_host(link: str) -> bool:
    return _on(link_host(link), BOOKING_HOSTS)


_ID_TOKEN = re.compile(r"(?=[a-z0-9_]*\d)(?=[a-z0-9_]*[a-z])[a-z0-9_]{6,}")
# A month and a year is a listing's page ('/whats-on/october2026'), not an id;
# a date in the query is a calendar's day view ('?tribe-bar-date=2026-10-15').
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_MONTH_TOKEN = re.compile(rf"(?:(?:19|20)\d\d)?{_MONTH}(?:(?:19|20)?\d\d)?|(?:19|20)\d\d{_MONTH}")
_DATE_IN_QUERY = re.compile(r"(?<!\d)(?:19|20)\d\d-\d\d-\d\d(?!\d)"
                            r"|(?<!\d)(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)")
_WORD3 = re.compile(r"[^\W\d_]{3,}")
_WORD_STOP = STOP | {"for", "von", "und", "der", "die", "das", "les", "des", "del", "los", "las", "con",
                     "pour", "avec", "une", "event", "events"}
# The segment before a leaf that names a PLACE: the leaf is that place's page.
VENUE_PATH = {"centre", "centres", "center", "centers", "branch", "branches", "location", "locations",
              "venue", "venues", "facility", "facilities", "complex", "site", "sites"}


def _words(text: str) -> Set[str]:
    return set(_WORD3.findall(_ident_words(text))) - _WORD_STOP


def names_an_event(link: str, titles: Iterable[str] = ()) -> bool:
    """The link is one event's page: its last segment (a trailing date or day
    number dropped: /event/<slug>/2026-10-14) carries an event id (5+ digits,
    12+ hex, a 6+-character token of letters and digits like Luma's 'zzaa4x39',
    3+ digits on their own like /m/4480), or one of the two segments before it
    carries a 5+-digit or hex id (/evento/1285675556092/comunicacion), or the
    query has 3+ digits, or the last two segments share a 3+-letter word with
    one of these titles. A centre's or branch's page
    (placesleisure.org/centres/<centre>), a dataset link or a group's calendar
    (luma.com/cmevents) does none of these, however few titles this run has
    seen on it."""
    path, _, query = link.partition("?")
    segs = [s.lower() for s in path.split("/")[1:] if s]
    while segs and _LEAF_DROP.fullmatch(segs[-1]):
        segs.pop()
    if re.search(r"\d{3,}", _DATE_IN_QUERY.sub("", query)):
        return True
    if not segs:
        return False
    leaf = segs[-1]
    if _STRONG_ID.search(leaf) or any(_ID_TOKEN.fullmatch(w) and not _MONTH_TOKEN.fullmatch(w)
                                      for w in re.split(r"[^a-z0-9_]+", leaf)):
        return True
    if len(segs) > 1 and segs[-2] in VENUE_PATH:
        return False                              # /centres/<centre>, /branch/<branch>
    if re.fullmatch(r"\d{3,}", leaf) or any(_STRONG_ID.search(x) for x in segs[-3:-1]):
        return True
    words = _words(" ".join(segs[-2:]))
    return any(words & _words(t) for t in titles)


def is_generic(link: str, titles_by_link: Dict[str, Set[str]], titles: Iterable[str] = ()) -> bool:
    """A listing page rather than this event's page: its path is a calendar's
    front (/events, /calendar ...), it names no event (names_an_event), or it
    carries more than GENERIC_TITLES distinct titles among the rows read this
    run. The last test alone is order-dependent (it counts only rows read so
    far): 9 replayed census hides sat on links where it had seen 2 titles or
    fewer and the census 3 to 65."""
    path = link.split("/", 1)[1] if "/" in link else ""
    return (path.strip("/") in GENERIC_PATHS or not names_an_event(link, titles)
            or len(titles_by_link.get(link, ())) > GENERIC_TITLES)


def _place_words(s: Optional[str]) -> Set[str]:
    return set(_ident_words(s or "").split()) - PLACE_STOP - VENUE_WORDS


def two_venues(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Each place name carries a word the other lacks ('Familienzentrum Thekla'
    / 'Familienzentrum Mockau'); one contained in the other is one venue."""
    pa, pb = _place_words(a.get("place_name")), _place_words(b.get("place_name"))
    return bool(pa and pb and (pa - pb) and (pb - pa))


def addon_of(whole: str, title: str) -> bool:
    """`title` is `whole` plus words that name a package sold beside it ('...
    Courtside Experience', '- VIP Packages'): it ranks with the parts."""
    w, t = set(_ident_words(whole).split()), _ident_words(title).split()
    extra = [x for x in t if x not in w]
    return bool(w) and w <= set(t) and bool(extra) and (
        any(x in ADDON_WORDS for x in extra) or any(x + y in ADDON_WORDS for x, y in zip(extra, extra[1:])))


def says_off(r: Dict[str, Any]) -> bool:
    return bool(CANCEL_MARK.search(_ident_words(r.get("title"))))


def _seq(title: str) -> List[str]:
    folded = unicodedata.normalize("NFKD", str(title or "")).lower()
    return _SEQ_RX.findall("".join(c for c in folded if not unicodedata.combining(c)))


def _part_sep(whole: str, title: str) -> Optional[str]:
    """The separator when `title` is '<a part> <separator> <whole>' (part_of),
    else None."""
    s = [t for t in _seq(whole) if t[0].isalnum()]
    seq = _seq(title)
    at = [i for i, t in enumerate(seq) if t[0].isalnum()]
    n = len(s)
    if not n or len(at) <= n or [seq[i] for i in at[-n:]] != s:
        return None
    before = seq[:at[-n]]
    if before and before[-1] in PART_ARTICLES and len(before) > 1:
        before = before[:-1]
    return before[-1] if before and before[-1] in PART_SEPS else None


def part_of(whole: str, title: str) -> bool:
    """`title` is '<a part> <separator> <whole>': 'Strong Complex Booth |
    Children's Carnival', 'Paramount Theatre Club Seating: Gary Clark Jr.',
    'Copy of – <whole>'. The whole title must be the entire tail; a title that
    only ADDS words after another ('Rhyme Time – Safety Bay') is not this shape."""
    return _part_sep(whole, title) is not None


def held_at(whole: str, title: str) -> bool:
    """`title` is '<something> at <whole>' (or @, bei, au, im) and names no
    package sold beside it: a service or activity HELD AT the other listing,
    which is a listing of its own ('Vote Riders Identification Clinic at Our
    Space' beside the library's 'Our Space', each organisation with its own
    page and text; 'Nourish Moves at the Market Square Farmers Market')."""
    if _part_sep(whole, title) not in HELD_AT_SEPS or COPY_OF.match(title or ""):
        return False
    extra = set(_ident_words(title).split()) - set(_ident_words(whole).split())
    return not (extra & ADDON_WORDS) and not addon_of(whole, title)


def level_marks(title: str) -> Set[str]:
    """The level words of a session's title ('Intermediate', 'High
    Intermediate', 'invite only'): two titles with different marks at one slot
    are two sessions of one organiser."""
    w = _ident_words(title).split()
    out: Set[str] = set()
    for i, x in enumerate(w):
        if x in LEVEL_WORDS:
            out.add(x)
            if i and w[i - 1] in LEVEL_QUALIFIERS:
                out.add(w[i - 1] + " " + x)
    return out


def extends(whole: str, title: str) -> bool:
    """`title` is '<whole> <separator> <more>': 'Day of the Dead Festival:
    Parade' extends 'Day of the Dead Festival'. part_of's mirror image."""
    s = [t for t in _seq(whole) if t[0].isalnum()]
    seq = _seq(title)
    at = [i for i, t in enumerate(seq) if t[0].isalnum()]
    n = len(s)
    if not n or len(at) <= n or [seq[i] for i in at[:n]] != s:
        return False
    rest = seq[at[n - 1] + 1:]
    return bool(rest) and rest[0] in PART_SEPS and any(t[0].isalnum() for t in rest[1:])


def adds_audience(a: str, b: str) -> bool:
    """The shorter title is one or two words and the longer adds a word naming
    who it is for ('Yoga' / 'Yoga for Seniors'). Digits do not count here: '(16+)'
    and '3.0' are how ticket sites spell one show."""
    ta, tb = _ident_words(a).split(), _ident_words(b).split()
    s, l = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return 0 < len(s) <= 2 and any(_audience(w) and not w.isdigit() for w in set(l) - set(s))


def _audience(w: str) -> bool:
    return bool(_AUDIENCE_WORD.fullmatch(w) or AUDIENCE_EXTRA.fullmatch(w))


def _toks(title: str) -> List[str]:
    return [w for w in _ident_words(title).split() if w not in STOP]


def title_relation(a: str, b: str) -> Dict[str, bool]:
    """eq: the same title. sim: one contains the other word for word, with the
    sync's audience guard and AUDIENCE_EXTRA ('Schwimmen fuer Frauen' is not
    'Schwimmen'). typo: same length, 1-2 words differ slightly and none
    is an audience word. anchored: the shorter's words are a subset of the
    longer's and sit at its start, its end, or share its first word. loose: any
    of those, or token Jaccard >= 0.5 (only ever used with the same page)."""
    ia, ib = _ident_words(a), _ident_words(b)
    eq = normalize_text(a) == normalize_text(b) or (bool(ia) and ia.replace(" ", "") == ib.replace(" ", ""))
    sim = _similar_titles(ia, ib) and not any(_audience(w) for w in set(ia.split()) ^ set(ib.split()))
    ta, tb = _toks(a), _toks(b)
    typo = False
    if ta and len(ta) == len(tb):
        diff = [(x, y) for x, y in zip(ta, tb) if x != y]
        typo = 0 < len(diff) <= 2 and all(
            difflib.SequenceMatcher(None, x, y).ratio() >= 0.8
            and not _audience(x) and not _audience(y) for x, y in diff)
    anchored = False
    if ta and tb:
        s, l = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if set(s) <= set(l):
            n = len(s)
            anchored = set(s) == set(l) or l[:n] == s or l[-n:] == s or s[0] == l[0]
    sa, sb = set(ia.split()), set(ib.split())
    jacc = len(sa & sb) / len(sa | sb) if sa | sb else 0.0
    return {"eq": eq, "sim": sim, "typo": typo, "anchored": anchored,
            "loose": eq or sim or typo or anchored or jacc >= 0.5}


def hours(a: Dict[str, Any]) -> float:
    s = utc(a["starts_at"])
    e = utc(a["ends_at"]) if a.get("ends_at") else s
    return (e - s).total_seconds() / 3600.0


def is_all_day(r: Dict[str, Any]) -> bool:
    return ALL_DAY_MIN_H <= hours(r) <= ALL_DAY_MAX_H


def within_day(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """One row is a one-day span (23-25 h) and the other, shorter, starts
    inside its first 24 hours. Needs no time zone: the span is the day."""
    for d, t in ((a, b), (b, a)):
        if is_all_day(d) and not is_all_day(t):
            s = utc(d["starts_at"])
            if s <= utc(t["starts_at"]) < s + timedelta(hours=24):
                return True
    return False


def gap_hours(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    if not (a.get("created_at") and b.get("created_at")):
        return 0.0                                # unknown: treat as made together
    return abs((utc(a["created_at"]) - utc(b["created_at"])).total_seconds()) / 3600.0


def match(a: Dict[str, Any], b: Dict[str, Any], titles_by_link: Dict[str, Set[str]]) -> Optional[str]:
    """'same-page' | 'same-slot' | 'all-day-copy' | None. Rows are listed
    (visible, imported, not standing, not pin_only) and carry 'link'."""
    return _decide(a, b, titles_by_link)[0]


def why_not(a: Dict[str, Any], b: Dict[str, Any], titles_by_link: Dict[str, Set[str]]) -> Optional[str]:
    """The reason a candidate pair was NOT matched (the report's near misses)."""
    return _decide(a, b, titles_by_link)[1]


def _decide(a, b, titles_by_link) -> Tuple[Optional[str], Optional[str]]:
    if NOT_AN_EVENT.search(a.get("title") or "") or NOT_AN_EVENT.search(b.get("title") or ""):
        return None, "ticket add-on"
    if a.get("lat") is None or b.get("lat") is None or metres(a, b) > RADIUS_M:
        return None, "distance"
    ta, tb = a.get("title") or "", b.get("title") or ""
    t = title_relation(ta, tb)
    la, lb = a.get("link"), b.get("link")
    if utc(a["starts_at"]) == utc(b["starts_at"]):
        if abs((_ends(a) - _ends(b)).total_seconds()) / 3600.0 > END_GAP_H:
            # An exhibition and its vernissage, a run and its first show: hiding
            # the long one takes it off the map for its whole run (production
            # 2026-10-13: a 59-day exhibition behind a 2 h row of it).
            return None, "same start, ends 12 h+ apart"
        if la and lb and la == lb:
            if gap_hours(a, b) < TOGETHER_H:
                return None, "same page, made together"     # one read listed both: two records
            if is_generic(la, titles_by_link, (ta, tb)):
                return None, "same page, a listing page"    # not this event's page
            return ("same-page", None) if t["loose"] else (None, "same page, titles")
        if is_all_day(a) and is_all_day(b) and gap_hours(a, b) < TOGETHER_H and not t["eq"]:
            # Two classes with no clock time, read together on two pages: a
            # start instant says nothing when both rows are the whole day
            # (production 2026-10-15: 'Recreation: Zumba Toning' hidden behind
            # 'Recreation: Zumba', made 0 s apart on two weekdays' timetables).
            return None, "two clockless day rows, one read, titles differ"
        if not (la and lb):
            return ("same-slot", None) if t["eq"] else (None, "same slot, no link, titles")
        if not t["eq"] and level_marks(ta) != level_marks(tb):
            return None, "same slot, two levels"
        if held_at(ta, tb) or held_at(tb, ta):
            return None, "same slot, a service held at the other"
        if not same_publisher(la, lb):
            if t["eq"] or t["typo"] or (t["anchored"] and not adds_audience(ta, tb)):
                return "same-slot", None
            return None, "same slot, two publishers, titles"
        if booking_host(la) or booking_host(lb):            # either way round: match(a, b) == match(b, a)
            return None, "same slot, a booking platform's two sessions"
        if not multi_tenant(la) and gap_hours(a, b) < TOGETHER_H and two_venues(a, b):
            # One publisher, one read, two place names: centres it pins to one
            # coordinate (fiz-leipzig, bona-peiser, a YMCA's branches).
            return None, "one read, two venues"
        return (("same-slot", None) if (t["eq"] or t["sim"] or t["typo"])
                else (None, "same slot, one publisher, titles"))
    if within_day(a, b):
        if not (la and lb and la == lb):
            return None, "all-day, different pages"
        if is_generic(la, titles_by_link, (ta, tb)):
            return None, "all-day, a listing page"
        if gap_hours(a, b) < TOGETHER_H and not (t["eq"] or t["typo"]):
            # One read wrote a day and a timed row under different titles: a
            # festival and its parade. The census's 45 all-day copies made in
            # one read (CPL's two adapters in one sync) carry one title.
            return None, "all-day, made together, titles differ"
        d, tt = (ta, tb) if is_all_day(a) else (tb, ta)
        if part_of(d, tt) or extends(d, tt):
            # 'Day of the Dead Festival: Parade' on the festival's page is a part
            # of the day, not a clock for it (census all-day copies: 0 of this shape).
            return None, "all-day, a part of the day's event"
        return ("all-day-copy", None) if t["loose"] else (None, "all-day, titles")
    return None, "other start"


def _ends(r: Dict[str, Any]) -> datetime:
    return utc(r["ends_at"]) if r.get("ends_at") else utc(r["starts_at"])


def engaged(r: Dict[str, Any], rsvp_ids: Set[str], now: datetime) -> bool:
    return (r["id"] in rsvp_ids or bool(r.get("last_message_at"))
            or bool(r.get("promoted_until") and utc(r["promoted_until"]) > now))


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def survivor_key(r: Dict[str, Any], rsvp_ids: Set[str], now: datetime, part: bool = False) -> Tuple:
    """Claimed, then people on it, then a row with a clock over a clockless
    copy, then a row whose link is its own page, then the whole event over a
    part of it (`part`: on another page at the same slot, this row's title is
    '<a part> <separator> <that row's title>'), then the newest import (the
    source's current title); on an exact creation tie (one read), not a 'Copy
    of', then the fewer title words, then id.

    The clock term sits before age on purpose: in 193 of 219 all-day copies the
    clockless row is the NEWER one, because both keys are still emitted (by two
    adapters), so 'newest' would keep the row without a time 88% of the time.
    The link term too: 24 of 1,069 production hides (2026-10-13) would have kept
    a newer row whose link was another site's event on another date (a yacht
    club's 'Musical Bingo' linking a Minnesota council meeting) and hidden the
    venue's own. The part term: 'Strong Complex Booth | Children's Carnival'
    (made two months later) would have hidden the carnival, and an exact tie
    broken by id alone kept 'Nourish Moves at the Market Square Farmers Market'
    over the market and a club-seating upsell over the concert. It counts only
    across same-slot edges: on one page, the newer title is the one the source
    still emits."""
    made = utc(r["created_at"]) if r.get("created_at") else _EPOCH
    title = r.get("title") or ""
    return (bool(r.get("claimed_at")), engaged(r, rsvp_ids, now), not is_all_day(r), link_ok(r), not part,
            made, not COPY_OF.match(title), -len(_ident_words(title).split()), r["id"])


def plan(rows: Dict[str, Dict[str, Any]], edges: Dict[Tuple[str, str], str],
         rsvp_ids: Set[str], now: datetime, max_cluster: int = 12) -> List[Dict[str, Any]]:
    """Union-find over matched pairs; one survivor per component; hide only
    members with a DIRECT match to the survivor, unclaimed and with nobody
    on them (a star, not a chain), and never a row whose title says the event
    is off ('Canceled:', 'Postponed:') behind one whose title does not: which
    of the two is current is for a person. Raises ValueError on a component
    over max_cluster."""
    par: Dict[str, str] = {}

    def find(x: str) -> str:
        while par.setdefault(x, x) != x:
            par[x] = par.get(par[x], par[x])
            x = par[x]
        return x
    adj: Dict[str, Dict[str, str]] = {}
    for (x, y), arm in sorted(edges.items()):
        par[find(x)] = find(y)
        adj.setdefault(x, {})[y] = arm
        adj.setdefault(y, {})[x] = arm
    comps: Dict[str, Set[str]] = {}
    for x in adj:
        comps.setdefault(find(x), set()).add(x)
    out = []
    for comp in sorted(comps.values(), key=lambda c: min(c)):
        if len(comp) > max_cluster:
            raise ValueError(f"cluster of {len(comp)} rows (limit {max_cluster}): "
                             f"{sorted(str(rows[i].get('title')) for i in comp)[:3]}")
        parts = {i for i in comp for j, arm in adj[i].items()
                 if arm == "same-slot" and (part_of(rows[j].get("title") or "", rows[i].get("title") or "")
                                            or addon_of(rows[j].get("title") or "", rows[i].get("title") or ""))}
        # A row whose link names another event (and no date), beside one whose
        # DIFFERENT link names its own row: the venue's own row survives.
        misled = {i for i in comp if not link_names_row(rows[i]) and any(
            rows[j].get("link") and rows[j].get("link") != rows[i].get("link") and link_names_row(rows[j])
            for j in adj[i])}
        s = max(comp, key=lambda i: survivor_key(rows[i], rsvp_ids, now, i in parts or i in misled))
        hide, left = [], []
        off = says_off(rows[s])
        for i in sorted(comp - {s}):
            r = rows[i]
            if (i in adj[s] and not r.get("claimed_at") and not engaged(r, rsvp_ids, now)
                    and (off or not says_off(r))):
                hide.append((i, adj[s][i]))
            else:
                left.append(i)
        # THE KEPT ROW CARRIES ITS COPIES' CATEGORIES (merged_categories), so
        # the event stays in every door and behind every category chip it was
        # in. A copy whose link is another event's page counts for nothing
        # (copy_counts: its categories are that event's) and is hidden without
        # them. A counted copy whose categories do not all fit (0108: 2
        # secondaries; or a door that leaves out the kept row's primary) is
        # left visible; a claimed row is never written, so it carries only its
        # own. Then the union is taken again over the copies actually hidden,
        # which is what the sync will compute when it next writes this row.
        k = rows[s]
        uncounted = [i for i, _ in hide if not copy_counts(rows[i])]
        counted = [i for i, _ in hide if i not in uncounted]
        pool = [(rows[i].get("category"), rows[i].get("categories")) for i in counted]
        cats = k.get("categories") if k.get("claimed_at") else merged_categories(k.get("category"), k.get("categories"), pool)
        no_room = [i for i in counted if not categories_cover(k.get("category"), cats, rows[i].get("category"),
                                                              rows[i].get("categories"))]
        if no_room:
            hide = [(i, arm) for i, arm in hide if i not in no_room]
            left = sorted(left + no_room)
        if not k.get("claimed_at"):
            cats = merged_categories(k.get("category"), k.get("categories"),
                                     [(rows[i].get("category"), rows[i].get("categories")) for i, _ in hide
                                      if i not in uncounted])
        out.append({"survivor": s, "hide": hide, "left": left, "no_room": no_room,
                    "not_counted": [i for i in uncounted if adds_categories(
                        k.get("category"), cats, rows[i].get("category"), rows[i].get("categories"))],
                    "categories": cats if hide and cats != k.get("categories") else None,
                    "was": (k.get("category"), k.get("categories"))})
    return out


def candidate_pairs(rows: List[Dict[str, Any]], carry: Iterable[Dict[str, Any]] = ()
                    ) -> List[Tuple[str, str]]:
    """Pairs worth reading links for: the same start instant, or a one-day row
    and a shorter row starting inside it; at most RADIUS_M apart; titles at
    least loosely alike. `carry` is the previous day's one-day rows (a Sydney
    all-day row starts at 13:00Z the day before its timed twin); a pair of two
    carried rows was already seen in their own window."""
    own = {r["id"] for r in rows}
    carried = [c for c in carry if c["id"] not in own]
    carry_ids = {c["id"] for c in carried}
    meta: Dict[str, Tuple[datetime, bool]] = {}
    by_start: Dict[Tuple[int, int], Dict[datetime, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    timed: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    cell_of: Dict[str, Tuple[int, int]] = {}
    for r in list(rows) + carried:
        if r.get("lat") is None or r.get("lon") is None or not r.get("starts_at"):
            continue
        start, allday = utc(r["starts_at"]), is_all_day(r)
        meta[r["id"]] = (start, allday)
        cell = (math.floor(r["lat"] / GRID_DEG), math.floor(r["lon"] / GRID_DEG))
        cell_of[r["id"]] = cell
        by_start[cell][start].append(r)
        if not allday:
            timed[cell].append(r)
    out: Set[Tuple[str, str]] = set()
    titles: Dict[Tuple[str, str], bool] = {}

    def consider(r: Dict[str, Any], s: Dict[str, Any]) -> None:
        if r["id"] == s["id"] or (r["id"] in carry_ids and s["id"] in carry_ids):
            return
        key = (r["id"], s["id"]) if r["id"] < s["id"] else (s["id"], r["id"])
        if key in out or key in titles:
            return
        if metres(r, s) > RADIUS_M:
            return
        titles[key] = title_relation(r.get("title") or "", s.get("title") or "")["loose"]
        if titles[key]:
            out.add(key)

    for r in list(rows) + carried:
        if r["id"] not in meta:
            continue
        start, allday = meta[r["id"]]
        ci, cj = cell_of[r["id"]]
        span = RADIUS_M / (111320.0 * max(0.01, math.cos(math.radians(abs(r["lat"]) + 0.01))))
        k = int(math.ceil(span / GRID_DEG))
        for di in (-1, 0, 1):
            for dj in range(-k, k + 1):
                cell = (ci + di, cj + dj)
                if cell not in by_start:
                    continue
                for s in by_start[cell].get(start, ()):
                    if s["id"] > r["id"]:
                        consider(r, s)
                if allday:
                    for s in timed.get(cell, ()):
                        if start <= meta[s["id"]][0] < start + timedelta(hours=24):
                            consider(r, s)
    return sorted(out)


def window_order(days: int, lookback: int) -> List[int]:
    """Day offsets from today's UTC midnight: yesterday's rows still running
    (its one-day rows are carried into today), then every day forward, then
    the older rows still running, LAST. Those are the dearest to list (a past
    day's whole range is scanned for the few rows still on) and the least
    productive: 3 of the census's 854 hides, all Chicago Park District season
    programmes that started 21-29 days before; so a budget that runs out costs
    them, not tomorrow."""
    return ([-1] if lookback >= 1 else []) + list(range(days)) + [-d for d in range(2, lookback + 1)]


# --------------------------------------------------------------------------- #
# THE DATABASE (PostgREST; every URL carries external_source=eq.mapsee)
# --------------------------------------------------------------------------- #
def Z(dt: datetime) -> str:
    """A timestamp for a filter: UTC, microseconds, a 'Z', quoted."""
    return quote(dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), safe="")


class Db:
    def __init__(self, url: str, key: str, http):
        self.url = url.rstrip("/")
        self.base = self.url + "/rest/v1/events?external_source=eq.mapsee"
        # SAFETY: imported events only. Everything below appends to this string.
        assert "external_source=eq.mapsee" in self.base, "refusing an unscoped read or write"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.http = http
        self.calls = Counter()

    def get(self, url: str, tries: int = 3, waits=(2.0, 4.0), retry_timeouts: bool = True):
        """A GET retried on a statement timeout, a 5xx, a 429 or a dropped
        connection. -> the last response, or None if every try raised. With
        retry_timeouts=False a 57014 comes straight back: the caller shrinks
        the range instead (list_window)."""
        resp = None
        for attempt in range(tries):
            self.calls["GET"] += 1
            try:
                resp = self.http.get(url, headers=self.headers, timeout=60)
            except Exception as exc:                  # noqa: BLE001
                _p(f"  read failed ({type(exc).__name__})")
                resp = None
            else:
                if not retry_timeouts and _timed_out(resp):
                    self.calls["57014"] += 1
                    return resp
                if not (_timed_out(resp) or resp.status_code >= 500 or resp.status_code == 429):
                    return resp
            if attempt + 1 < tries:
                _sleep(waits[min(attempt, len(waits) - 1)])
        return resp

    def patch(self, url: str, body: Dict[str, Any], tries: int = 3, waits=(1.5, 3.0)):
        assert "external_source=eq.mapsee" in url, "refusing an unscoped write"
        headers = dict(self.headers, Prefer="return=representation")
        headers["Content-Type"] = "application/json"
        resp = None
        for attempt in range(tries):
            self.calls["PATCH"] += 1
            try:
                resp = self.http.patch(url, headers=headers, data=json.dumps(body), timeout=60)
            except Exception as exc:                  # noqa: BLE001
                _p(f"  write failed ({type(exc).__name__})")
                resp = None
            else:
                if resp.status_code < 300:
                    return resp
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp                       # a refusal: retrying will not help
            if attempt + 1 < tries:
                _sleep(waits[min(attempt, len(waits) - 1)])
        return resp


def _ok(resp) -> bool:
    return resp is not None and resp.status_code < 300


def _why(resp) -> str:
    if resp is None:
        return "no response"
    return f"[{resp.status_code}] {(resp.text or '')[:200]}"


def column_exists(db: Db, column: str) -> Optional[bool]:
    """True/False, or None when the probe itself failed."""
    resp = db.get(f"{db.base}&select={column}&limit=0")
    if _ok(resp):
        return True
    if resp is not None and resp.status_code == 400 and _missing_column(resp):
        return False
    return None


def _list_range(db: Db, lo: datetime, hi: datetime, now: datetime, budget, retry_timeouts: bool
                ) -> Tuple[List[Dict[str, Any]], str, int]:
    """Every live row starting in [lo, hi), paged.
    -> (rows, 'ok'|'timeout'|'failed'|'budget', pages).

    A KEYSET WITH A LOWER BOUND THE INDEX CAN USE: the cursor is
    starts_at >= s AND (starts_at > s OR id > last), not the or() alone. The
    bare or() climbed 0.81 -> 2.03 s over five pages of one day on the anon
    key; with the gte beside it, 0.45-1.01 s over six. No count: count=exact
    hit 57014 at 3.2-3.6 s on three of four days."""
    rows: List[Dict[str, Any]] = []
    lower = f"starts_at.gte.{Z(lo)}"
    pages = 0
    while True:
        if budget():
            return rows, "budget", pages
        url = (f"{db.base}{LIVE}&ends_at=gte.{Z(now)}&select={SELECT}"
               f"&order=starts_at.asc,id.asc&limit={PAGE}&and=({lower},starts_at.lt.{Z(hi)})")
        resp = db.get(url, retry_timeouts=retry_timeouts)
        pages += 1
        if not _ok(resp):
            if resp is not None and _timed_out(resp):
                return rows, "timeout", pages
            _p(f"  listing failed on page {pages}: {_why(resp)}")
            return rows, "failed", pages
        page = resp.json() or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows, "ok", pages
        last = page[-1]
        s = Z(utc(last["starts_at"]))
        lower = f"starts_at.gte.{s},or(starts_at.gt.{s},id.gt.{last['id']})"


def list_window(db: Db, lo: datetime, hi: datetime, now: datetime, budget
                ) -> Tuple[List[Dict[str, Any]], List[Tuple[datetime, datetime, str]], int]:
    """Every live row starting in [lo, hi). -> (rows, [(from, to, why) not
    listed], pages).

    A WINDOW THAT HAS STARTED IS TWO LISTINGS. From `now` on every row is live,
    so the plain keyset returns what it scans. Before `now` the index walks
    every row that started in the range to return the few still running: on
    2026-10-13 (anon key, 3 s ceiling) the whole of today and of yesterday hit
    57014 on their first page, as did five of six 3-hour slices of today, while
    the day after listed 28,759 rows in 29 pages. So the started part goes in
    slices of RUNNING_SLICE, and a statement timeout HALVES the slice (the cost
    is the rows the range scans, not the rows it returns) down to
    RUNNING_FLOOR, rather than re-asking the same range. A part that still
    fails is reported and the rest of the window is used: a row missing from a
    window can only lose a match, never cause a wrong hide."""
    rows: List[Dict[str, Any]] = []
    missed: List[Tuple[datetime, datetime, str]] = []
    pages = 0
    t, step, end = lo, RUNNING_SLICE, min(now, hi)
    while t < end:
        b = min(t + step, end)
        got, status, n = _list_range(db, t, b, now, budget, retry_timeouts=False)
        pages += n
        if status == "timeout" and step > RUNNING_FLOOR:
            step = max(RUNNING_FLOOR, step / 2)          # re-ask a smaller slice
            continue
        rows.extend(got)
        if status != "ok":
            stop = end if status == "budget" else b
            if missed and missed[-1][1] == t and missed[-1][2] == status:
                missed[-1] = (missed[-1][0], stop, status)      # one part, not 96 slices
            else:
                missed.append((t, stop, status))
            if status == "budget":
                return rows, missed, pages
        t = b
    if hi > now:
        a = max(lo, now)
        got, status, n = _list_range(db, a, hi, now, budget, retry_timeouts=True)
        pages += n
        rows.extend(got)
        if status != "ok":
            missed.append((a, hi, status))
    return rows, missed, pages


def _chunks(ids: Iterable[str], n: int = ID_BATCH) -> Iterable[List[str]]:
    ids = sorted(set(ids))
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def read_links(db: Db, ids: Iterable[str]) -> Optional[Dict[str, Optional[str]]]:
    """{id: canonical link or None} read from each row's description."""
    out: Dict[str, Optional[str]] = {}
    for chunk in _chunks(ids):
        resp = db.get(f"{db.base}&select=id,description&id=in.({','.join(chunk)})")
        if not _ok(resp):
            _p(f"  link read failed: {_why(resp)}")
            return None
        for r in resp.json() or []:
            out[r["id"]] = canonical_link(r.get("description"))
    return out


def read_rsvp_ids(db: Db, ids: Iterable[str]) -> Optional[Set[str]]:
    """The ids among these that anybody has RSVPed to, whatever the status.

    A keyset on event_id, not offset=: only WHETHER an event has an RSVP
    matters, so the next page starts after the last event seen, and a chunk of
    100 ids costs at most 101 reads however many RSVPs one event has. A page
    that does not move the cursor (a server ignoring the filter) is a failed
    read, not a loop: offset= kept such a server going for 28 reads."""
    out: Set[str] = set()
    for chunk in _chunks(ids):
        after: Optional[str] = None
        while True:
            more = f"&and=(event_id.gt.{after})" if after else ""
            resp = db.get(f"{db.url}/rest/v1/event_rsvps?select=event_id&event_id=in.({','.join(chunk)})"
                          f"{more}&order=event_id.asc&limit=1000")
            if not _ok(resp):
                _p(f"  RSVP read failed: {_why(resp)}")
                return None
            got = [str(r["event_id"]) for r in resp.json() or []]
            out.update(got)
            if len(got) < 1000:
                break
            if after is not None and max(got) <= after:
                _p("  RSVP read failed: the keyset did not advance")
                return None
            after = max(got)
    return out


def visible_ids(db: Db, ids: Iterable[str]) -> Optional[Set[str]]:
    out: Set[str] = set()
    for chunk in _chunks(ids):
        resp = db.get(f"{db.base}&select=id&hidden_at=is.null&id=in.({','.join(chunk)})")
        if not _ok(resp):
            _p(f"  survivor read failed: {_why(resp)}")
            return None
        out.update(r["id"] for r in resp.json() or [])
    return out


def _is(col: str, value: Any) -> str:
    """A PostgREST filter that the column still holds `value` (an array as {a,b})."""
    if value is None:
        return f"&{col}=is.null"
    if isinstance(value, (list, tuple)):
        return f"&{col}=eq." + quote("{" + ",".join(value) + "}", safe=",")
    return f"&{col}=eq.{quote(str(value), safe='')}"


def give_categories(db: Db, survivor: str, was: Tuple[Any, Any], cats: Optional[List[str]]):
    """Put the union on a kept row, only if it is unclaimed, visible and still
    holds what was read (the database decides, as for the hide).
    -> 'ok' | 'changed' (0 rows: it moved since) | 'failed'."""
    url = (f"{db.base}&id=eq.{survivor}&claimed_at=is.null&hidden_at=is.null"
           f"{_is('category', was[0])}{_is('categories', was[1])}&select=id")
    resp = db.patch(url, {"categories": cats})
    if not _ok(resp):
        _p(f"  categories for {survivor} failed: {_why(resp)}")
        return "failed"
    return "ok" if (resp.json() or []) else "changed"


def write(db: Db, plans: List[Dict[str, Any]], stamp: str, budget, now: datetime,
          workers: int = WORKERS) -> Dict[str, int]:
    """Hide each plan's copies, naming the survivor, after giving the survivor
    their categories (plan()'s 'categories'). Re-checked at write time: the
    survivor must still be visible, and a copy somebody RSVPed to since the
    listing is skipped. The RSVP re-read is made per batch of at most ID_BATCH
    copies, just before that batch's writes, so the window in which an RSVP can
    land unseen is one batch's PATCHes, not the whole day's (no column on events
    says 'has an RSVP', so the PATCH filter itself cannot check it). The filter
    carries claimed_at=is.null, hidden_at=is.null, last_message_at=is.null and
    no unexpired promotion, so a claim, a first chat message or a paid promotion
    that lands after the listing still keeps the copy: the database has the
    last word. A kept row whose categories moved since the listing (the sync
    rewrote it) is not written, and neither are its copies: next run."""
    res = {"hidden": 0, "skipped": 0, "owed": 0, "survivor_gone": 0, "late_rsvp": 0,
           "survivor_changed": 0, "categories_given": 0}
    todo = [p for p in plans if p["hide"]]
    if not todo:
        return res
    alive = visible_ids(db, [p["survivor"] for p in todo])
    if alive is None:
        res["owed"] = sum(len(p["hide"]) for p in todo)
        return res
    ready = []
    for p in todo:
        if p["survivor"] not in alive:
            res["survivor_gone"] += len(p["hide"])
        else:
            ready.append(p)
    batches: List[List[Dict[str, Any]]] = []
    for p in ready:
        if batches and sum(len(q["hide"]) for q in batches[-1]) + len(p["hide"]) <= ID_BATCH:
            batches[-1].append(p)
        else:
            batches.append([p])

    def one(job: Tuple[Dict[str, Any], List[str]]) -> Tuple[str, int, int, int]:
        p, ids = job
        if budget():
            return "owed", 0, len(ids), 0
        given = 0
        if p.get("categories"):
            got = give_categories(db, p["survivor"], p["was"], p["categories"])
            if got != "ok":
                return ("owed" if got == "failed" else "changed"), 0, len(ids), 0
            given = 1
        url = (f"{db.base}&claimed_at=is.null&hidden_at=is.null&last_message_at=is.null"
               f"&or=(promoted_until.is.null,promoted_until.lt.{Z(now)})&select=id"
               f"&id=in.({','.join(ids)})")
        resp = db.patch(url, {"hidden_at": stamp, "duplicate_of": p["survivor"]})
        if not _ok(resp):
            _p(f"  hide failed for {len(ids)} row(s) of {p['survivor']}: {_why(resp)}")
            return "owed", 0, len(ids), given
        n = len(resp.json() or [])
        return "ok", n, len(ids) - n, given

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for batch in batches:
            late = read_rsvp_ids(db, [h for p in batch for h, _ in p["hide"]])
            if late is None:
                res["owed"] += sum(len(p["hide"]) for p in batch)
                continue
            jobs = []
            for p in batch:
                ids = [h for h, _ in p["hide"] if h not in late]
                res["late_rsvp"] += len(p["hide"]) - len(ids)
                if ids:
                    jobs.append((p, ids))
            for status, n, rest, given in pool.map(one, jobs):
                res["categories_given"] += given
                if status == "ok":
                    res["hidden"] += n
                    res["skipped"] += rest
                elif status == "changed":
                    res["survivor_changed"] += rest
                else:
                    res["owed"] += rest
    return res


def kept_rows_pass(db: Db, now: datetime, apply: bool, budget) -> Optional[Dict[str, Any]]:
    """Every row this job hid and the row it kept, before tonight's windows.

    THE SYNC REWRITES A KEPT ROW'S CATEGORIES WHEN IT WRITES THE ROW (to_row),
    and it merges the copies' categories back as it does (merge_copy_categories)
    - unless its read of the copies failed, or something else wrote the row
    (mapsee_reclassify.py). So once a night: a kept row that lacks a category
    of a copy hidden behind it is given the union again (merged_categories, the
    same function), and a copy whose categories still do not fit (0108: two
    secondaries; a door that leaves out the kept row's primary; or the kept row
    is claimed, and a claimed row is never written) is UN-hidden, because it is
    then the only row that keeps the event in that door. A copy whose link is
    another event's page (copy_counts) is neither: its categories are that
    event's. Also counts the copies whose kept row another writer has hidden
    since (a cancellation, a take-down): they stay hidden, which is the owner's
    rule that nobody is sent to an event that is off.

    Reads: one keyset over 0236's partial index (duplicate_of is not null),
    the kept rows by id, then the text of the copies a kept row does not cover
    (100 a request). Writes only with --apply. -> counts, or None when a read
    failed."""
    lo = Z(now - timedelta(days=1))
    copies: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    after: Optional[Tuple[str, str]] = None
    while True:
        if budget():
            return None
        more = (f"&and=(duplicate_of.gte.{after[0]},or(duplicate_of.gt.{after[0]},id.gt.{after[1]}))"
                if after else "")
        resp = db.get(f"{db.base}&duplicate_of=not.is.null&hidden_at=not.is.null&ends_at=gte.{lo}"
                      f"&select=id,duplicate_of,category,categories&order=duplicate_of.asc,id.asc"
                      f"&limit={PAGE}{more}")
        if not _ok(resp):
            _p(f"  hidden copies read failed: {_why(resp)}")
            return None
        got = resp.json() or []
        for r in got:
            copies[str(r["duplicate_of"])].append(r)
        if len(got) < PAGE:
            break
        nxt = (str(got[-1]["duplicate_of"]), str(got[-1]["id"]))
        if after is not None and nxt <= after:
            _p("  hidden copies read failed: the keyset did not advance")
            return None
        after = nxt
    res = {"copies": sum(len(v) for v in copies.values()), "kept_rows": len(copies), "orphaned": 0,
           "short": 0, "given": 0, "unhidden": 0, "changed": 0, "owed": 0, "not_counted": 0, "examples": []}
    kept: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(copies):
        if budget():
            return None
        resp = db.get(f"{db.base}&hidden_at=is.null&select=id,category,categories,claimed_at"
                      f"&id=in.({','.join(chunk)})")
        if not _ok(resp):
            _p(f"  kept rows read failed: {_why(resp)}")
            return None
        for r in resp.json() or []:
            kept[str(r["id"])] = r
    res["orphaned"] = sum(len(v) for sid, v in copies.items() if sid not in kept)
    # A copy the kept row does not cover matters only if its categories count
    # (copy_counts): read the text of those few, not of every copy.
    need = [str(c["id"]) for sid, k in kept.items() for c in copies[sid]
            if not categories_cover(k.get("category"), k.get("categories"), c.get("category"), c.get("categories"))]
    text: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(need):
        if budget():
            return None
        resp = db.get(f"{db.base}&select=id,title,place_name,starts_at,description&id=in.({','.join(chunk)})")
        if not _ok(resp):
            _p(f"  hidden copies' text read failed: {_why(resp)}")
            return None
        for r in resp.json() or []:
            text[str(r["id"])] = r
    fixes = []
    for sid, k in sorted(kept.items()):
        # Covered copies stay in the union as they are; an uncovered one only if
        # its link is its own (one whose text is gone is left out).
        cs = [c for c in copies[sid]
              if categories_cover(k.get("category"), k.get("categories"), c.get("category"), c.get("categories"))
              or (str(c["id"]) in text and copy_counts(text[str(c["id"])]))]
        res["not_counted"] += len(copies[sid]) - len(cs)
        if all(categories_cover(k.get("category"), k.get("categories"), c.get("category"), c.get("categories"))
               for c in cs):
            continue
        res["short"] += 1
        cats = (k.get("categories") if k.get("claimed_at") else
                merged_categories(k.get("category"), k.get("categories"),
                                  [(c.get("category"), c.get("categories")) for c in cs]))
        out = [str(c["id"]) for c in cs
               if not categories_cover(k.get("category"), cats, c.get("category"), c.get("categories"))]
        fixes.append((sid, k, cats if cats != k.get("categories") else None, out))
        if len(res["examples"]) < 20:
            res["examples"].append({"kept": sid, "category": k.get("category"), "categories": k.get("categories"),
                                    "to": cats, "unhide": out})
    if not apply:
        res["given"] = sum(1 for f in fixes if f[2] is not None)
        res["unhidden"] = sum(len(f[3]) for f in fixes)
        return res
    for sid, k, cats, out in fixes:
        if budget():
            res["owed"] += 1
            continue
        if cats is not None:
            got = give_categories(db, sid, (k.get("category"), k.get("categories")), cats)
            if got == "failed":
                res["owed"] += 1
                continue
            if got == "changed":
                res["changed"] += 1                    # moved since this read: tomorrow
                continue
            res["given"] += 1
        if out:
            resp = db.patch(f"{db.base}&duplicate_of=eq.{sid}&hidden_at=not.is.null&cancelled_at=is.null"
                            f"&select=id&id=in.({','.join(out)})", {"hidden_at": None, "duplicate_of": None})
            if _ok(resp):
                res["unhidden"] += len(resp.json() or [])
            else:
                _p(f"  un-hide of {len(out)} copy(ies) of {sid} failed: {_why(resp)}")
                res["owed"] += 1
    return res


def unhide(db: Db, stamp: Optional[str], ids: List[str], apply: bool, show: int) -> int:
    """Put back what one run hid (--stamp), or named rows (--id). Only rows this
    job hid (duplicate_of set) and nobody cancelled since."""
    # One filter per column: hidden_at=eq.<stamp> already implies not null.
    scope = (f"{db.base}&duplicate_of=not.is.null&cancelled_at=is.null"
             + (f"&hidden_at=eq.{quote(stamp, safe='')}" if stamp else "&hidden_at=not.is.null"))
    flt = scope + (f"&id=in.({','.join(sorted(set(ids)))})" if ids else "")
    rows: List[Dict[str, Any]] = []
    last = None
    while True:
        after = f"&and=(id.gt.{last})" if last else ""
        resp = db.get(f"{flt}&select=id,title,starts_at,duplicate_of&order=id.asc&limit={PAGE}{after}")
        if not _ok(resp):
            _p(f"Couldn't list the hidden copies: {_why(resp)}")
            return 1
        got = resp.json() or []
        rows.extend(got)
        if len(got) < PAGE:
            break
        last = got[-1]["id"]
    _p(f"Copies this job hid that match: {len(rows)}")
    for r in rows[:show]:
        _p(f"  {r['id']}  {str(r.get('starts_at') or '')[:16]}  {r.get('title')!r}  copy of {r.get('duplicate_of')}")
    if len(rows) > show:
        _p(f"  ... and {len(rows) - show} more")
    if not apply:
        _p("Report only: nothing un-hidden (add --apply).")
        return 0
    done = owed = 0
    for chunk in _chunks(r["id"] for r in rows):
        resp = db.patch(f"{scope}&select=id&id=in.({','.join(chunk)})", {"hidden_at": None, "duplicate_of": None})
        if _ok(resp):
            done += len(resp.json() or [])
        else:
            owed += len(chunk)
            _p(f"  un-hide failed for {len(chunk)} row(s): {_why(resp)}")
    _p(f"Un-hid {done} row(s) (hidden_at and duplicate_of cleared).")
    if owed:
        _p(f"OWED: {owed} row(s) not un-hidden; re-run the same command.")
    return 1 if owed else 0


# --------------------------------------------------------------------------- #
# THE RUN
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").replace(hour=12, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def main(argv: Optional[List[str]] = None, http=None) -> int:
    ap = argparse.ArgumentParser(description="Hide duplicate imported events in Supabase (report unless --apply).")
    ap.add_argument("--apply", action="store_true",
                    help="Hide the copies. Default: report what would be hidden; write nothing.")
    ap.add_argument("--days", type=int, default=21, help="Forward horizon in UTC days (default 21).")
    ap.add_argument("--lookback", type=int, default=60,
                    help="Also rows that started up to this many days ago and are still running (default 60).")
    ap.add_argument("--max-seconds", type=int, default=1200,
                    help="Budget for the whole run: listing, reads and writes (default 1200).")
    # The census's largest true cluster was 7 and this rule's largest is 6; a
    # grouping bug looks like hundreds in one cluster.
    ap.add_argument("--max-cluster", type=int, default=12,
                    help="Refuse a window in which one event clusters more rows than this (default 12).")
    ap.add_argument("--max-share", type=float, default=0.15,
                    help=f"Refuse a window whose hides exceed max({SHARE_FLOOR}, this share of its rows) "
                         "(default 0.15; the census measured 3.4%% overall, Chicago 11.1%%).")
    ap.add_argument("--report", default=None, help="Write the full report (every pair) as JSON here.")
    ap.add_argument("--show", type=int, default=15, help="Examples to print per rule (default 15).")
    ap.add_argument("--unhide", action="store_true",
                    help="Reverse: un-hide copies this job hid. Needs --stamp or --id; reports unless --apply.")
    ap.add_argument("--stamp", default=None, help="With --unhide: the hidden_at stamp of one run (from its report).")
    ap.add_argument("--id", action="append", default=[], help="With --unhide: one row id (repeatable).")
    a = ap.parse_args(argv)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    db = Db(url, key, http if http is not None else requests.Session())

    if a.unhide:
        if not (a.stamp or a.id):
            sys.exit("--unhide needs --stamp (one run) or --id (named rows); it never un-hides everything.")
        if column_exists(db, "duplicate_of") is not True:
            sys.exit("events.duplicate_of is missing (../mapsee 0236 not applied) or unreadable: "
                     "nothing this job hid can be told apart.")
        return unhide(db, a.stamp, a.id, a.apply, a.show)

    now = _now()
    started = _clock()
    budget = lambda: _clock() - started > a.max_seconds   # noqa: E731
    has_col = column_exists(db, "duplicate_of")
    apply = bool(a.apply and has_col)
    if a.apply and not has_col:
        _p("::warning::events.duplicate_of is missing (../mapsee 0236 not applied) or unreadable: "
           "reporting only")
    stamp = now.replace(microsecond=0).isoformat()
    _p(f"Mode: {'APPLY (hidden_at=' + stamp + ')' if apply else 'REPORT ONLY'}; "
       f"duplicate_of {'present' if has_col else 'missing' if has_col is False else 'unknown'}; "
       f"{a.days} days ahead, {a.lookback} back, budget {a.max_seconds}s")

    # FIRST, before tonight's windows: the rows hidden on earlier nights and the
    # rows they were hidden behind (kept_rows_pass). Cheap (one keyset over the
    # partial index, then the kept rows by id), and it is what keeps a kept row
    # in every door its copies were in after a sync rewrote it.
    kept = kept_rows_pass(db, now, apply, budget) if has_col else None
    if kept is not None:
        _p(f"Hidden copies: {kept['copies']} behind {kept['kept_rows']} kept row(s)"
           + (f"; {kept['orphaned']} whose kept row another writer has hidden since (they stay hidden)"
              if kept["orphaned"] else "")
           + (f"; {kept['short']} kept row(s) lacked a copy's category: "
              + ("given the union" if apply else "--apply gives the union")
              + f" {kept['given']}, copies {'un-hidden' if apply else 'to un-hide'} (no room) {kept['unhidden']}"
              + (f", moved since the read {kept['changed']}" if kept["changed"] else "")
              + (f", not counted (link is another event's page) {kept['not_counted']}" if kept["not_counted"] else "")
              + (f", OWED {kept['owed']}" if kept["owed"] else "") if kept["short"] else ""))

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    titles_by_link: Dict[str, Set[str]] = defaultdict(set)
    allday_by_day: Dict[int, List[Dict[str, Any]]] = {}
    backlog: List[str] = []
    owed_windows: List[str] = []
    refused: List[str] = []
    owed = 0
    totals: Counter = Counter()
    by_arm: Counter = Counter()
    near: Counter = Counter()
    hidden_out: List[Dict[str, Any]] = []
    left_out: List[Dict[str, Any]] = []
    windows: List[Dict[str, Any]] = []
    examples: Dict[str, List[str]] = defaultdict(list)
    misdated: List[Dict[str, Any]] = []

    for d in window_order(a.days, a.lookback):
        lo = midnight + timedelta(days=d)
        hi = lo + timedelta(days=1)
        day = lo.date().isoformat()
        w: Dict[str, Any] = {"day": day, "offset": d}
        windows.append(w)
        if budget():
            backlog.append(day)
            w["status"] = "backlog"
            continue
        t0 = _clock()
        rows, missed, pages = list_window(db, lo, hi, now, budget)
        w.update(rows=len(rows), pages=pages)
        for a_, b_, why in missed:
            part = f"{day} {a_:%H:%M}-{'24:00' if b_ >= hi else format(b_, '%H:%M')}Z ({why})"
            (backlog if why == "budget" else owed_windows).append(part)
            w.setdefault("missed", []).append(part)
            _p(f"[{day} {d:+d}] not listed: {part}")
        if not rows:
            w["status"] = "nothing listed" if missed else "ok"
            continue
        totals["rows"] += len(rows)
        allday_by_day[d] = [r for r in rows if r.get("starts_at") and is_all_day(r)]
        carry = allday_by_day.get(d - 1, [])
        pairs = candidate_pairs(rows, carry)
        by_id = {c["id"]: c for c in carry}
        by_id.update({r["id"]: r for r in rows})
        need = {i for p in pairs for i in p if "link" not in by_id[i]}
        links = read_links(db, need) if need else {}
        if links is None:
            owed_windows.append(day)
            w["status"] = "link read failed"
            continue
        for i in need:
            r = by_id[i]
            r["link"] = links.get(i)
            if r["link"]:
                titles_by_link[r["link"]].add(normalize_text(r.get("title")))
                if not link_ok(r):
                    # Another site's event on another date: an adapter's bug
                    # (it ranks below the venue's own row; see survivor_key).
                    totals["link_names_another_date"] += 1
                    if len(misdated) < 200:
                        misdated.append({"id": i, "title": r.get("title"), "starts_at": r.get("starts_at"),
                                         "link": r["link"], "day": day})
        edges: Dict[Tuple[str, str], str] = {}
        w_near: Counter = Counter()
        for x, y in pairs:
            arm, why = _decide(by_id[x], by_id[y], titles_by_link)
            if arm:
                edges[(x, y)] = arm
            else:
                w_near[why] += 1
        members = {i for e in edges for i in e}
        rsvp = read_rsvp_ids(db, members) if members else set()
        if rsvp is None:
            owed_windows.append(day)
            w["status"] = "RSVP read failed"
            continue
        try:
            plans = plan(by_id, edges, rsvp, now, a.max_cluster)
        except ValueError as exc:
            refused.append(f"{day}: {exc}")
            w["status"] = f"refused: {exc}"
            _p(f"[{day} {d:+d}] REFUSED: {exc}")
            continue
        n = sum(len(p["hide"]) for p in plans)
        ceiling = max(SHARE_FLOOR, a.max_share * len(rows))
        if n > ceiling:
            msg = f"{n} hides in {len(rows)} rows (limit {ceiling:.0f})"
            refused.append(f"{day}: {msg}")
            w["status"] = f"refused: {msg}"
            _p(f"[{day} {d:+d}] REFUSED: {msg}")
            continue
        arms = Counter(arm for p in plans for _, arm in p["hide"])
        left = sum(len(p["left"]) for p in plans)
        w.update(status="partial" if missed else "ok", candidates=len(pairs), links_read=len(need), matched=dict(Counter(edges.values())),
                 clusters=len(plans), hide=n, hide_by_arm=dict(arms), left=left, near_misses=dict(w_near))
        totals.update(candidates=len(pairs), links_read=len(need), matched=len(edges), clusters=len(plans),
                      hide=n, left=left, kept_rows_given_categories=sum(1 for p in plans if p.get("categories")),
                      left_no_room=sum(len(p["no_room"]) for p in plans),
                      not_counted=sum(len(p["not_counted"]) for p in plans if p["hide"]))
        by_arm.update(arms)
        near.update(w_near)
        for p in plans:
            s = by_id[p["survivor"]]
            for h, arm in p["hide"]:
                r = by_id[h]
                hidden_out.append({"id": h, "title": r.get("title"), "starts_at": r.get("starts_at"),
                                   "link": r.get("link"), "created_at": r.get("created_at"), "arm": arm,
                                   "category": r.get("category"), "categories": r.get("categories"),
                                   "survivor": s["id"], "survivor_title": s.get("title"),
                                   "survivor_starts_at": s.get("starts_at"), "survivor_link": s.get("link"),
                                   "survivor_category": s.get("category"),
                                   "survivor_categories": s.get("categories"),
                                   "survivor_categories_to": p.get("categories"),
                                   "metres": round(metres(r, s), 1), "day": day})
                if len(examples[arm]) < a.show:
                    examples[arm].append(f"hide {r.get('title')!r} ({h[:8]}) -> keep {s.get('title')!r} "
                                         f"({s['id'][:8]}) {str(s.get('starts_at') or '')[:16]}")
            for i in p["left"]:
                left_out.append({"id": i, "title": by_id[i].get("title"), "survivor": s["id"],
                                 "why": ("claimed" if by_id[i].get("claimed_at") else
                                         "people on it" if engaged(by_id[i], rsvp, now) else
                                         "no direct match to the survivor" if (
                                             (i, s["id"]) not in edges and (s["id"], i) not in edges) else
                                         "its categories do not fit on the kept row" if i in p["no_room"] else
                                         "it says the event is off; the survivor does not"), "day": day})
        hidden_now = {h for p in plans for h, _ in p["hide"]}
        allday_by_day[d] = [r for r in allday_by_day[d] if r["id"] not in hidden_now]
        line = (f"[{day} {d:+d}] {len(rows)} rows" + (" (PARTIAL)" if missed else "")
                + f", {pages} page(s), {_clock() - t0:.1f}s | "
                f"{len(pairs)} candidates, {len(need)} links read | "
                + (", ".join(f"{k} {v}" for k, v in sorted(arms.items())) or "nothing")
                + f" to hide in {len(plans)} cluster(s)" + (f", {left} left" if left else ""))
        if apply and n:
            res = write(db, plans, stamp, budget, now)
            owed += res["owed"]
            w["written"] = res
            totals.update(hidden=res["hidden"], skipped_at_write=res["skipped"] + res["survivor_gone"]
                          + res["late_rsvp"] + res["survivor_changed"], filter_skipped=res["skipped"],
                          categories_given=res["categories_given"])
            line += (f" | hid {res['hidden']}" + (f", skipped {res['skipped']}" if res["skipped"] else "")
                     + (f", survivor gone {res['survivor_gone']}" if res["survivor_gone"] else "")
                     + (f", late RSVP {res['late_rsvp']}" if res["late_rsvp"] else "")
                     + (f", kept row moved {res['survivor_changed']}" if res["survivor_changed"] else "")
                     + (f", categories to {res['categories_given']} kept row(s)" if res["categories_given"] else "")
                     + (f", OWED {res['owed']}" if res["owed"] else ""))
        _p(line)

    _p("")
    _p(f"Rows listed: {totals['rows']} | candidates {totals['candidates']} | links read {totals['links_read']} "
       f"| matched pairs {totals['matched']} | clusters {totals['clusters']}")
    _p(f"Copies {'hidden' if apply else 'to hide'}: "
       + (f"{totals['hidden']} of {totals['hide']} planned" if apply else f"{totals['hide']}")
       + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(by_arm.items())) + ")" if by_arm else "")
       + (f"; {totals['left']} left visible ({totals['left'] - totals['left_no_room']} for a person, "
          f"{totals['left_no_room']} because their categories do not fit)" if totals["left"] else ""))
    if totals["rows"]:
        _p(f"  that is {totals['hide'] / totals['rows']:.1%} of the rows listed")
    for arm in ARMS:
        for e in examples.get(arm, []):
            _p(f"  [{arm}] {e}")
    if near:
        _p("Near misses (candidates no rule took): "
           + ", ".join(f"{k} {v}" for k, v in near.most_common()))
    if totals["link_names_another_date"]:
        _p(f"Rows whose link names another date (an adapter's bug; listed in the report as "
           f"'link_names_another_date'): {totals['link_names_another_date']}")
    if totals["kept_rows_given_categories"] or totals["left_no_room"] or totals["not_counted"]:
        _p(f"Kept rows that carry their copies' categories: {totals['kept_rows_given_categories']}"
           + (f"; copies left visible because their categories do not fit: {totals['left_no_room']}"
              if totals["left_no_room"] else "")
           + (f"; copies hidden WITHOUT their categories, because their link is another event's page: "
              f"{totals['not_counted']}" if totals["not_counted"] else ""))
    # A PATCH WHOSE FILTER MATCHES NOTHING MUST NOT PASS. Every planned hide that
    # reaches the database comes back skipped only if its filter no longer
    # matches (a claim, a chat message since the listing): rare. Half of them
    # skipped is a filter the server reads differently, the shape of the weeks
    # this job hid nothing while green (a replay with a fake that did not know
    # the top-level or= printed 'hidden 0 of 866' and exited 0).
    tried = totals["hidden"] + totals["filter_skipped"]
    filter_broke = apply and tried >= 20 and totals["filter_skipped"] * 2 > tried
    if filter_broke:
        _p(f"::error::The hide filter matched {totals['hidden']} of {tried} rows it was sent: "
           f"a filter the database reads differently; exit 1.")
    kept_failed = bool(apply and has_col and kept is None)
    if kept_failed:
        _p("OWED: the kept rows' categories could not be checked (read failed); tomorrow's run does it")
    for day in backlog:
        _p(f"BACKLOG: {day} not reached inside {a.max_seconds}s")
    for day in owed_windows:
        _p(f"OWED: {day} could not be read; it runs again tomorrow")
    for msg in refused:
        _p(f"REFUSED: {msg}")
    if owed:
        _p(f"OWED: {owed} copies not hidden this run (write failures or the budget)")
    code = 1 if (backlog or owed_windows or refused or owed or (a.apply and not has_col) or filter_broke
                 or kept_failed or (kept and kept["owed"])) else 0
    _p(f"Requests: {dict(db.calls)} | {_clock() - started:.0f}s | exit {code}")
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"now": now.isoformat(), "stamp": stamp if apply else None,
                       "mode": "apply" if apply else "report", "duplicate_of_column": has_col,
                       "args": vars(a), "totals": dict(totals), "hidden_by_arm": dict(by_arm),
                       "near_misses": dict(near), "kept_rows": kept,
                       "orphaned_copies": kept["orphaned"] if kept else None, "backlog": backlog,
                       "owed_windows": owed_windows, "refused": refused, "owed": owed, "exit": code,
                       "requests": dict(db.calls), "windows": windows, "hidden": hidden_out,
                       "left": left_out, "link_names_another_date": misdated}, fh, indent=1, default=str)
        _p(f"Report: {a.report}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
