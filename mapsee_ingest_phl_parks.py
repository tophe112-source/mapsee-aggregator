#!/usr/bin/env python3
"""
mapsee_ingest_phl_parks.py - what anyone can turn up to at Philadelphia's
recreation centres: the drop-in programmes of the City's Parks & Recreation
Finder, as dated rows inside each programme's own season.

    python mapsee_ingest_phl_parks.py --config phl_parks_sources.json \
        --store feeds_events.json [--max-minutes 5]

Read ONLY through the CARTO SQL API at phl.carto.com/api/v2/sql (the City's
phl-oddt account), which is the backend phila.gov/parks-rec-finder queries
from the browser. The PPR tables are the Finder's backend, not datasets
catalogued on OpenDataPhilly, and no licence is stated per table - so the
attribution names the Finder, not "open data". The holiday list comes from
api.phila.gov/phila/trashday/v1, which is where phila.gov's own "City holidays
and closures" page fills its list from: phila.gov's own backend, so a
publisher feed. robots.txt is read on BOTH hosts every run, before either is
asked anything (2026-10-05: api.phila.gov 404, allow-all; phl.carto.com
disallows /api/ only for Googlebot, Slurp, Yandex, msnbot and baiduspider); a
refusal stops that host for the run and a Crawl-delay is honoured.

WHY THIS SOURCE. It is the one US city open-data portal that publishes a
recreation centre's weekly timetable as data (docs/agents/community-centres.md:
65 search terms over 3,067 US Socrata datasets found none other). Measured
2026-10-04: 294 schedules overlap the next 90 days; they name 270 programmes,
of which 243 are what the Finder itself shows (active, public AND approved -
the three WHERE clauses in the Finder's build.js), at 54 facilities.

------------------------------------------------------------------------------
WHAT IT KEEPS, AND WHY THAT IS FEW
------------------------------------------------------------------------------

The live dry run of 2026-10-05 (10 requests + 2 robots.txt, 13 s): 6
programmes kept of 270, 144 rows in the next 90 days at 4 centres, every one
on the Finder's own pin and every one free by the Finder's fee. The 264
refused, with the weekly occurrences each would have written: sign-up
programmes with no drop-in words 179 (113 "Registering" and 66 "Planning",
3,574 occurrences), camps and before/after-school care 24 (1,418), not on the
Finder 27 (633), "Closed: Check back again!" 13 (424), recovery fellowships 6
(254), governance meetings 4 (52), a sign-up link 4 (24), registration
required in their own words 4 (63), a shredding day 1 (34), "open to all"
with a season fee 1 (6), not weekly by its own words 1 (11). City holidays
removed 11 occurrences of kept programmes. That is the honest size of
Philadelphia's drop-in supply in this table: the Finder is a catalogue of
sign-ups, and the brief's 7,077 weekly occurrences were almost all
after-school clubs, camps, leagues and classes. Over the WHOLE catalogue
(2,246 active, public, approved programmes, 2026-10-05) the rules keep 53: 49
on drop-in words, 4 on "all are welcome".

1. REGISTRATION_STATUS NEVER SAYS DROP-IN, SO IT CANNOT SAY WHO MAY TURN UP.
   Over all 3,277 active public programmes it has four values: "Registering"
   1,567, "Planning: Seeking registrants!" 1,132, "Closed: Check back again!"
   555, "Closed: Did not occur" 23. It is a workflow status every programme
   carries: "Basketball Open Gym - Adult" ("Walk-in free play") says
   "Registering", and so do two AA meetings. The lead's line is registration,
   not price (community-centres.md), and a Finder programme is an after-school
   club, a league or a class you sign up for unless it says otherwise - so a
   programme is kept only when its OWN name or description says anyone can
   turn up (`dropin_words`: drop-in, walk-in(s), open gym/court/swim, free
   play, pickup games, "no registration required"). The weaker "all are
   welcome" / "open to all" (`open_words`) counts only on a programme with no
   fee (a $5.00 total/season walking club "open to all" is a sign-up), never
   for "open to all ages / skill levels / children" (who may SIGN UP), and
   such a row says "Price: free", never "drop-in". Bare "walk in" is the verb
   ("a morning walk in search of birds"): only the noun counts. A "Closed:
   ..." status is out whatever the wording. Every refusal is counted by
   reason, in programmes and in the weekly occurrences it would have written.

2. THE SOURCE'S OWN SIGN-UP SIGNALS ARE VETOES, READ BEFORE THE WORDS. The
   first word list kept 80 of the 2,246 catalogue programmes; 27 of those were
   sign-ups that also said an open word. (a) `registration_form_link` is what
   the Finder prints as "To sign up visit: <link>": a web or e-mail address
   there (105 URLs, 52 e-mails, 15 bare hosts in the catalogue) is a sign-up
   channel - Pickup Volleyball Games, live, $3.00 per week, signs up on
   opensports.net. (b) `registration_required` in the programme's own text:
   "Registration required by emailing PEC@phila.gov for a time slot" (Maple
   Sugaring Open House, which also says "All are welcome"), "Please complete a
   one time 2-page registration form" (adult volleyball), "Must be registered
   by 9/26/2025" (In House Basketball). The `registration_not_required`
   phrases are cut out first, so "No Registration Required" stays a drop-in.
   (c) `care_and_camp`: camps and before/after-school care by activity type
   or NAME - their copy is full of "free play" (Summer Camp, $500.00 a
   season, passed the old list on it). `deny` runs before all of these:
   recovery fellowships (AA/NA meet in the centres; anonymous by their own
   traditions, and a pin outs everyone walking in - matched on the word
   "anonymous" and on what such groups call themselves, since "Gamblers'
   Anonymous", "N.A. Meeting" and "Alcholics Anonymous" all live), civic-
   association and council meetings (governance), a shredding day (a service).

3. THE SCHEDULE IS "THESE WEEKDAYS BETWEEN THESE DATES", AND NOTHING ELSE.
   `days` is a list of ids into ppr_days (config `weekday_ids`; an unknown id
   is counted, never guessed), `date_from`..`date_to` is the season, both ends
   inclusive (Open Gym - Youth runs Tuesday 2026-09-08 to Tuesday 12-08). A
   date is written only inside its own schedule's season and the horizon - no
   date is ever invented outside a season. A programme whose own words say it
   is NOT weekly ("Third Wednesdays", "every other Friday", "monthly") is
   refused rather than expanded weekly (`not_weekly`).

4. THE CLOCK IS WALL-CLOCK TIME WEARING A "Z". time_from/time_to sit on a
   dummy 2012-01-01 with a 'Z' suffix, and are Philadelphia wall-clock: the
   "Community Meeting 630-730" stores 18:30-19:30, the "6pm-8pm" reading club
   18:00-20:00, and the Finder casts `time_from::time` and prints it. So the
   clock is read as America/New_York on each DATE, which is DST-correct across
   2026-11-01 (a Tuesday 15:00 is 19:00Z in October, 20:00Z in November).
   3 schedules in the window have no usable clock (two 00:00-00:00, one
   18:30-07:30) and are refused, never repaired.

5. CITY HOLIDAYS ARE CLOSED DAYS. phila.gov/city-holidays-and-closures: "All
   City offices close on holidays. This includes ... Philadelphia Parks &
   Recreation facilities". A weekly expansion would put a Monday-to-Friday open
   gym on Thanksgiving. The City's list (13 dates in 2026; the API answers
   the current year only) is read each run from `holidays_api` and joined to
   `skip_dates`, which carries the next year computed by the same 13 labels;
   a failed read falls back to skip_dates and says so, and the run warns when
   the horizon reaches a year with fewer than 5 dates known.

6. ONE ROW PER CONTIGUOUS STRETCH. 18 programmes carry more than one schedule.
   Per programme and date, overlapping or back-to-back times (JOIN_MINUTES)
   join and a gap starts a new row, so no row spans hours when nothing runs.
   Identity is programme id | date | the stretch's first clock, in source_id
   and in the fingerprint's basis (as toronto_rec and perfectmind key a
   session): two programmes of one name at one centre at one time are one
   row, and a re-read writes identical rows.

7. THE PRICE IS THE FINDER'S, IN THE FINDER'S WORDS. Its build.js prints
   "Free" for a fee of "0.00" and "$<fee> <fee_frequency>" otherwise, so a
   0.00 row says "Free drop-in: no fee." (0227's strict `free` reads "free
   drop-in") - or "Price: free (no fee)." when only "all are welcome" let it
   in (0227 reads "price: free") - and a fee says "Fee: $5.00 per day (not
   free)." - the "(not free)" is the veto 0227's FREE_NEG reads, so "Free
   Play" in a paid title can never tag it free. A null fee says nothing.

8. THE PIN IS THE FINDER'S PIN. Every one of the 54 facilities has a point in
   ppr_website_locatorpoints (joined on website_locator_points_link_id, the
   join the Finder makes); only 38 also carry lat/lon in the address JSON
   (median 58 m from the locator point, worst 523 m), and `the_geom` is null on
   all of them. The locator point is used with coords_exact=True; the address
   JSON point is the fallback (not exact); a facility with neither is written
   with its street for the sync to geocode.

Every request carries the MapseeAggregator UA and is paced >= 1.1 s per host.
SQL IN lists are batched at 60 ids (a GET carrying 271 ids returned a non-JSON
error, 2026-10-03), and every id is checked to be the source's 24-hex shape
before it is put in SQL. A 401/403/429 is never retried and ends reading from
that host for the run; a 5xx or a timeout is retried twice. --max-minutes is a
deadline no request starts after; the store is saved after the source and on
the way out, so a step cancelled by its timeout keeps what was read.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("This script needs Python 3.9+ (zoneinfo).")

import robots_txt
from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_API = "https://phl.carto.com/api/v2/sql"
MIN_INTERVAL_S = 1.1
# The largest answer (270 programmes over 5 batched requests) came back in
# under 2 s each on 2026-10-04: 30 s is generous, and with 3 tries and 2 + 4 s
# of backoff the worst call is 96 s - inside the 10-minute step with the
# 5-minute deadline.
REQUEST_TIMEOUT_S = 30
DEFAULT_MAX_MINUTES = 5.0
# More than this many ids in one GET came back as a non-JSON error (271 ids,
# 2026-10-03); 60 worked every time.
IN_BATCH = 60
# Two sessions of one programme this close are one stretch (the same tolerance
# as toronto_rec's JOIN_MINUTES and perfectmind's GRID_JOIN_MINUTES).
JOIN_MINUTES = 5
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}
ATTRIBUTION_MAX = 200
_ID_RX = re.compile(r"^[0-9a-f]{24}$")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class Refused(Exception):
    """The publisher said no (401/403/429). Reported, never retried or worked
    around - and the reader asks that host nothing more this run."""


class OutOfTime(Exception):
    """The run deadline (--max-minutes) has passed: no request starts after it."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """A paced JSON reader, paced and refused PER HOST (CARTO and api.phila.gov
    are two publishers). Counts its own requests for the dry-run report."""

    def __init__(self, session, min_interval: float = MIN_INTERVAL_S, tries: int = 3,
                 sleep=time.sleep, clock=time.monotonic, deadline: Optional[float] = None,
                 timeout: float = REQUEST_TIMEOUT_S) -> None:
        self.session = session
        self.min_interval = min_interval
        self.tries = tries
        self.sleep = sleep
        self.clock = clock
        self.deadline = deadline          # on clock's scale; None = no deadline
        self.timeout = timeout
        self.requests = 0
        self.refused: Dict[str, str] = {}  # host -> why
        self.crawl_delay: Dict[str, float] = {}  # host -> robots.txt Crawl-delay, when longer
        self._last: Dict[str, float] = {}

    def _pace(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = max(self.min_interval, self.crawl_delay.get(host, 0.0)) - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        host = urlsplit(url).netloc
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(self.tries):
            if host in self.refused:
                raise Refused(f"{self.refused[host]} earlier this run; nothing more is asked of {host}")
            self._pace(host)
            if self.deadline is not None and self.clock() >= self.deadline:
                raise OutOfTime("run deadline reached; no request starts after it")
            try:
                self.requests += 1
                r = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused[host] = f"HTTP {r.status_code} from {host}"
                    raise Refused(self.refused[host])
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as exc:
                        last = exc
                elif r.status_code not in _RETRYABLE:
                    # CARTO answers a bad query with 400 and {"error": [...]}:
                    # a fact about the query, which a retry cannot change.
                    raise RuntimeError(f"HTTP {r.status_code} from {host}: {r.text[:200]}")
                else:
                    last = RuntimeError(f"HTTP {r.status_code}")
            finally:
                self._last[host] = self.clock()
            if attempt + 1 < self.tries:
                self.sleep(2 * (attempt + 1))
        raise last

    def sql(self, api: str, query: str) -> List[Dict[str, Any]]:
        body = self.get_json(api, {"q": query})
        rows = body.get("rows") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"CARTO answered without rows: {str(body)[:200]}")
        return rows

    def sql_in(self, api: str, template: str, ids: Iterable[str], stats: Dict[str, int]) -> List[Dict[str, Any]]:
        """template has one %s for a quoted id list. Ids that are not the
        source's 24-hex shape never reach SQL."""
        good = []
        for i in sorted(set(ids)):
            if _ID_RX.match(i or ""):
                good.append(i)
            else:
                _bump(stats, "id not 24-hex (never put in SQL)")
        out: List[Dict[str, Any]] = []
        for k in range(0, len(good), IN_BATCH):
            out += self.sql(api, template % ",".join(f"'{x}'" for x in good[k:k + IN_BATCH]))
        return out


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------
def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def _jlist(v: Any) -> List[str]:
    """CARTO stores arrays as JSON TEXT ('["Registering"]'). Tolerant: a list
    passes through, None or bad JSON is []."""
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    if not v:
        return []
    try:
        got = json.loads(v)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in got if x is not None] if isinstance(got, list) else []


def _jobj(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict):
        return v
    try:
        got = json.loads(v or "{}")
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _today(tz) -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; otherwise it is today
    IN PHILADELPHIA (the runner is on UTC: at 20:00 local it is tomorrow there)."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


def _no_format_chars(s: str) -> str:
    """Unicode format characters (category Cf: zero-width space U+200B, BOM
    U+FEFF, soft hyphen) are invisible and break matching: 15 of the 2,246
    catalogue names began with U+200B on 2026-10-05 ('\u200bCoffee With The
    Birds')."""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def strip_html(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", s)
    s = _no_format_chars(html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " "))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return " ".join(ln for ln in lines if ln).strip()


def title_of(name: Optional[str]) -> str:
    """Format characters gone, whitespace collapsed; an all-lowercase name
    ('stickball') gets a capital."""
    t = re.sub(r"\s+", " ", _no_format_chars(html.unescape(name or ""))).strip()
    return t[:1].upper() + t[1:] if t and t == t.lower() else t


def clock_minutes(v: Optional[str]) -> Optional[int]:
    """'2012-01-01T18:30:00Z' -> 1110. The date and the 'Z' are decoration
    (lesson 4): the clock is Philadelphia wall-clock time."""
    m = re.search(r"T(\d{2}):(\d{2})", v or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def clock(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h == 12 and m == 0:
        return "noon"
    return f"{(h % 12) or 12}:{m:02d} {'a.m.' if h < 12 else 'p.m.'}"


def span(a: int, b: int) -> str:
    ca, cb = clock(a), clock(b)
    if ca[-4:] == cb[-4:] and "noon" not in (ca, cb):
        return f"{ca[:-5]}-{cb}"
    return f"{ca}-{cb}"


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _stamp(d: date, minutes: int, tz) -> Tuple[str, str]:
    """(naive local ISO, UTC ISO) for a date and a minute-of-day in tz: the
    offset is the one in force ON THAT DATE."""
    h, m = divmod(int(minutes), 60)
    local = datetime(d.year, d.month, d.day, h, m)
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M:00"), utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_box(lat: Any, lon: Any, box: Optional[List[float]]) -> bool:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if not box:
        return True
    s, w, n, e = box
    return s <= lat <= n and w <= lon <= e


def _phone(v: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else None


def _long_date(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _join(parts: List[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def days_text(wds: Tuple[int, ...]) -> str:
    """(1,) -> 'Tuesdays'; (0..4) -> 'Monday to Friday'; (0, 2) -> 'Mondays and Wednesdays'."""
    wds = tuple(sorted(set(wds)))
    if len(wds) >= 3 and wds == tuple(range(wds[0], wds[-1] + 1)):
        return f"{_WEEKDAYS[wds[0]]} to {_WEEKDAYS[wds[-1]]}"
    return _join([_WEEKDAYS[w] + "s" for w in wds])


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------
class Rules:
    """The config's word lists, compiled once."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        c = lambda xs: [re.compile(x, re.I) for x in xs or []]  # noqa: E731
        self.dropin = c(cfg.get("dropin_words"))
        self.open = c(cfg.get("open_words"))
        self.not_weekly = c(cfg.get("not_weekly"))
        self.signup_link = c(cfg.get("signup_link_rx"))
        self.reg_required = c(cfg.get("registration_required"))
        self.reg_not_required = c(cfg.get("registration_not_required"))
        cc = cfg.get("care_and_camp") or {}
        self.care_types = set(cc.get("types") or [])
        self.care_name = re.compile(cc["name_rx"], re.I) if cc.get("name_rx") else None
        self.deny = [(re.compile(d["rx"], re.I), d["why"]) for d in cfg.get("deny") or []]
        self.weekday = {k: _WEEKDAYS.index(v) for k, v in (cfg.get("weekday_ids") or {}).items()}
        self.cat_by_type = cfg.get("category_by_activity_type") or {}
        self.cat_default = cfg.get("category_default", "community")
        self.kids_age_max = int(cfg.get("kids_age_max", 12))


def _first(rxs, text: str) -> Optional[str]:
    for rx in rxs:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def fee_amount(p: Dict[str, Any]) -> Optional[float]:
    """The Finder's fee as a number; None when the source states none (null,
    empty or unreadable). '0.00' is 0.0, which the Finder prints as "Free"."""
    v = p.get("fee")
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def price_line(p: Dict[str, Any], kind: str = "drop-in") -> Tuple[Optional[str], str]:
    """(the description's fee line or None, tier). Lesson 7. `kind` is how the
    programme got in: only one whose own words say drop-in is written as a
    "Free drop-in"; one let in by "all are welcome" says "Price: free"."""
    amt = fee_amount(p)
    if amt is None:
        return None, "not stated"
    if amt == 0:
        if kind != "drop-in":
            return "🎟 Price: free (no fee).", "free"
        return "🎟 Free drop-in: no fee.", "free"
    freq = (_jlist(p.get("fee_frequency")) or [""])[0].strip().lower()
    text = f"${amt:,.2f}" + (f" {freq}" if freq else "")
    return f"🎟 Fee: {text} (not free).", "fee"


def signup_link(p: Dict[str, Any], rules: Rules) -> Tuple[Optional[str], str]:
    """(the sign-up address, None) when registration_form_link holds a web or
    e-mail address, else (None, its words for the text, '' for none). The
    Finder prints the field as "To sign up visit: <link>"."""
    v = re.sub(r"\s+", " ", _no_format_chars(str(p.get("registration_form_link") or ""))).strip()
    if _first(rules.signup_link, v):
        return v, ""
    return None, v


def decide(p: Dict[str, Any], rules: Rules, type_names: Optional[Dict[str, str]] = None
           ) -> Tuple[Optional[str], Optional[str], str]:
    """(refusal reason, None, "") or (None, the words that let it in, kind)
    where kind is "drop-in" (its own words say so) or "open" ("all are
    welcome", free only). Order matters only for which reason a refusal is
    COUNTED under, except that every veto runs before the words are read."""
    if not (p.get("program_is_active") and p.get("program_is_public") and p.get("program_is_approved")):
        return "not on the Finder (inactive, private or unapproved)", None, ""
    name = title_of(p.get("program_name"))
    link, link_words = signup_link(p, rules)
    text = f"{name} \n {strip_html(p.get('program_description'))} \n {link_words}"
    for rx, why in rules.deny:
        if rx.search(text):
            return why, None, ""
    status = (_jlist(p.get("registration_status")) or ["(none)"])[0]
    if status.lower().startswith("closed"):
        return f"status '{status}'", None, ""
    types = {(type_names or {}).get(t) for t in _jlist(p.get("activity_type"))}
    if types & rules.care_types or (rules.care_name and rules.care_name.search(name)):
        return "camp or before/after-school care (a sign-up by nature)", None, ""
    if link:
        return "signs up through registration_form_link (a web or e-mail address)", None, ""
    unsaid = text
    for rx in rules.reg_not_required:
        unsaid = rx.sub(" ", unsaid)
    if _first(rules.reg_required, unsaid):
        return "its own words say registration is required", None, ""
    strong = _first(rules.dropin, text)
    weak = None if strong else _first(rules.open, text)
    if not (strong or weak):
        return f"sign-up programme: status '{status}', no drop-in words", None, ""
    if weak and (fee_amount(p) or 0) > 0:
        return "'open to all' but charges a fee: a sign-up", None, ""
    nw = _first(rules.not_weekly, text)
    if nw:
        return "not weekly by its own words", None, ""
    return None, (strong or weak), ("drop-in" if strong else "open")


def category(p: Dict[str, Any], type_names: Dict[str, str], rules: Rules) -> Tuple[str, List[str]]:
    """(primary, extras): the first activity type the config maps decides;
    a programme for children (age_high <= kids_age_max) adds `kids`."""
    names = [type_names.get(t) for t in _jlist(p.get("activity_type"))]
    keys = [rules.cat_by_type[n] for n in names if n in rules.cat_by_type]
    primary = keys[0] if keys else rules.cat_default
    extras: List[str] = list(keys[1:])
    hi = p.get("age_high")
    try:
        if hi is not None and int(hi) <= rules.kids_age_max:
            extras.append("kids")
    except (TypeError, ValueError):
        pass
    return primary, norm_categories(primary, extras)


def occurrences(schedules: List[Dict[str, Any]], rules: Rules, today: date, horizon: date,
                skip: set, stats: Optional[Dict[str, int]]
                ) -> Dict[date, List[Tuple[int, int, date, Tuple[int, ...]]]]:
    """{date: [(start_min, end_min, season_end, weekdays)]} inside each schedule's OWN
    season, the window [today, horizon], and never on a skip date. `stats`
    None counts nothing (used to size what a refusal cost)."""
    st = stats if stats is not None else {}
    out: Dict[date, List[Tuple[int, int, date, Tuple[int, ...]]]] = {}
    for s in schedules:
        a, b = clock_minutes(s.get("time_from")), clock_minutes(s.get("time_to"))
        if a is None or b is None or b <= a:
            _bump(st, "schedules with no usable clock (end not after start)")
            continue
        try:
            d0 = date.fromisoformat(str(s.get("date_from") or "")[:10])
            d1 = date.fromisoformat(str(s.get("date_to") or "")[:10])
        except ValueError:
            _bump(st, "schedules with no season dates")
            continue
        wds = set()
        for did in _jlist(s.get("days")):
            if did in rules.weekday:
                wds.add(rules.weekday[did])
            else:
                _bump(st, "unknown weekday id (day never guessed)")
        d, end = max(d0, today), min(d1, horizon)
        while d <= end:
            if d.weekday() in wds:
                if d.isoformat() in skip:
                    _bump(st, "occurrences on a City holiday (centres closed)")
                else:
                    out.setdefault(d, []).append((a, b, d1, tuple(sorted(wds))))
            d += timedelta(days=1)
    return out


def stretches(times: Iterable[Tuple[int, int]], join: int = JOIN_MINUTES) -> List[Tuple[int, int]]:
    """Overlapping or back-to-back (within `join`) sessions as one stretch; a
    gap starts the next."""
    runs: List[List[int]] = []
    for a, b in sorted(set(times)):
        if runs and a <= runs[-1][1] + join:
            runs[-1][1] = max(runs[-1][1], b)
        else:
            runs.append([a, b])
    return [(a, b) for a, b in runs]


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
def place(f: Dict[str, Any], locators: Dict[str, Tuple[float, float]],
          box: Optional[List[float]]) -> Tuple[Optional[float], Optional[float], bool, str]:
    """(lat, lon, coords_exact, how). Lesson 8."""
    pt = locators.get(f.get("website_locator_points_link_id") or "")
    if pt and _in_box(pt[0], pt[1], box):
        return float(pt[0]), float(pt[1]), True, "the Finder's locator point"
    ad = _jobj(f.get("address"))
    if _in_box(ad.get("latitude"), ad.get("longitude"), box):
        return float(ad["latitude"]), float(ad["longitude"]), False, "the address record's point"
    return None, None, False, "street only (the sync geocodes)"


def build_events(schedules: List[Dict[str, Any]], programs: Dict[str, Dict[str, Any]],
                 facilities: Dict[str, Dict[str, Any]], locators: Dict[str, Tuple[float, float]],
                 type_names: Dict[str, str], src: Dict[str, Any], cfg: Dict[str, Any], tz,
                 today: date, skip: set, stats: Dict[str, int]) -> List[NormalizedEvent]:
    rules = Rules(cfg)
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    by_prog: Dict[str, List[Dict[str, Any]]] = {}
    for s in schedules:
        for pid in _jlist(s.get("programs")):
            by_prog.setdefault(pid, []).append(s)

    out: List[NormalizedEvent] = []
    attribution = cfg["attribution"]
    for pid in sorted(by_prog):
        sch = by_prog[pid]
        p = programs.get(pid)
        if not p:
            _bump(stats, "programmes: schedule names a programme that is not in ppr_programs")
            continue
        why, evidence, kind = decide(p, rules, type_names)
        if why is None:
            fids = _jlist(p.get("facility"))
            if len(fids) != 1 or fids[0] not in facilities:
                why = "no single facility to put it at"
            elif not facilities[fids[0]].get("facility_is_published", True):
                why = "facility not published by the Finder"
        if why:
            _bump(stats, f"programmes refused: {why}")
            n = sum(len(v) for v in occurrences(sch, rules, today, horizon, skip, None).values())
            _bump(stats, f"occurrences refused: {why}", n)
            continue
        f = facilities[_jlist(p.get("facility"))[0]]
        days = occurrences(sch, rules, today, horizon, skip, stats)
        if not days:
            _bump(stats, "programmes kept with no date in the window")
            continue
        _bump(stats, "programmes kept")
        _bump(stats, f"let in by ({kind}): {evidence.lower()}")
        title = title_of(p.get("program_name"))
        venue = re.sub(r"\s+", " ", (f.get("public_name") or f.get("official_name") or "")).strip()
        ad = _jobj(f.get("address"))
        street = ", ".join(x for x in (ad.get("street"), ad.get("street2")) if x) or None
        lat, lon, exact, how = place(f, locators, cfg.get("bbox"))
        primary, extras = category(p, type_names, rules)
        fee_text, tier = price_line(p, kind)
        source_desc = strip_html(p.get("program_description") or p.get("program_description_short"))
        age = re.sub(r"\s+", " ", p.get("age_range") or "").strip()
        phone = _phone(f.get("contact_phone"))
        url = src.get("program_url", "").format(id=pid) or src.get("finder_url")
        for day in sorted(days):
            runs = stretches((o[0], o[1]) for o in days[day])
            if len(runs) > 1:
                _bump(stats, "programme-days split at a gap")
            _bump(stats, "occurrences folded into a stretch", len(days[day]) - len(runs))
            for a, b in runs:
                # The season the row's own clock came from (the latest, if two
                # schedules give the same stretch).
                until, wds = max((o[2], o[3]) for o in days[day] if a <= o[0] and o[1] <= b)
                others = [span(x, y) for x, y in runs if (x, y) != (a, b)]
                paras = [
                    source_desc or None,
                    f"Philadelphia Parks & Recreation at {venue}" + (f", {age}." if age else "."),
                    f"Also on this day: {_join(others)}." if others else None,
                    fee_text,
                    f"{days_text(wds)} until {_long_date(until)}." + (f" Centre phone: {phone}." if phone else ""),
                    attribution,
                ]
                sl, su = _stamp(day, a, tz)
                el, eu = _stamp(day, b, tz)
                hm = _hm(a)
                ev = NormalizedEvent(
                    source=src.get("source", "phl-parks"),
                    source_id=f"{pid}|{day.isoformat()}|{hm}",
                    name=title,
                    description="\n\n".join(x.strip() for x in paras if x and x.strip()),
                    start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                    timezone=cfg.get("timezone"),
                    venue_name=venue,
                    latitude=lat, longitude=lon, coords_exact=exact,
                    address=street,
                    city=ad.get("city") or cfg.get("city"),
                    region=ad.get("state") or cfg.get("region"),
                    country=cfg.get("country"),
                    postal_code=(str(ad.get("zip")) if ad.get("zip") else None),
                    category=primary, categories=extras,
                    promoter=src.get("promoter"),
                    ticket_url=url,
                )
                ev.fingerprint = make_fingerprint(f"{title} {hm}", day.isoformat(),
                                                  f"{venue} {street or ''}".strip())
                _bump(stats, f"rows placed by {how}")
                _bump(stats, f"price: {tier}" + (" ('Price: free', not drop-in)" if tier == "free" and kind != "drop-in" else ""))
                out.append(ev)
    return out


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
_PROGRAM_COLS = ("id, program_name, program_description, program_description_short, facility, "
                 "activity_type, age_high, age_range, fee, fee_frequency, registration_status, "
                 "registration_form_link, program_is_active, program_is_public, program_is_approved")


def robots_allows(robots: "robots_txt.Robots", reader: Reader, url: str) -> Optional[str]:
    """None when robots.txt lets us read `url`, else why not. A Crawl-delay
    longer than ours is honoured for that host from here on."""
    if reader.deadline is not None and reader.clock() >= reader.deadline:
        raise OutOfTime("run deadline reached; not even robots.txt is asked after it")
    v = robots.check(url)
    if v.get("allowed") is not True:
        return f"robots.txt {v.get('status')}: {v.get('rule') or 'permission not established'}"
    if v.get("crawl_delay"):
        reader.crawl_delay[urlsplit(url).netloc] = float(v["crawl_delay"])
    return None


def read_holidays(reader: Reader, cfg: Dict[str, Any],
                  robots: Optional["robots_txt.Robots"] = None) -> Tuple[set, List[str]]:
    """(skip dates as ISO strings, notes). The City's list joined to the
    config's; a failed read, or a robots.txt that refuses it, is a note,
    never a stop."""
    skip = set(cfg.get("skip_dates") or [])
    notes: List[str] = []
    api = cfg.get("holidays_api")
    if not api:
        return skip, notes
    if robots is not None:
        why = robots_allows(robots, reader, api)  # OutOfTime propagates
        if why:
            notes.append(f"the City's holiday list was not read ({why}); config skip_dates only")
            return skip, notes
    try:
        body = reader.get_json(api)
        got = [h.get("start_date") for h in (body or {}).get("holidays") or [] if isinstance(h, dict)]
        got = [d for d in got if re.match(r"^\d{4}-\d{2}-\d{2}$", str(d or ""))]
        if not got:
            notes.append("the City's holiday list came back empty; config skip_dates only")
        skip.update(got)
    except (Refused, OutOfTime):
        raise
    except Exception as exc:  # noqa: BLE001 - the config list still applies
        notes.append(f"the City's holiday list was not read ({exc}); config skip_dates only")
    return skip, notes


def holiday_gap(skip: set, today: date, horizon: date) -> Optional[str]:
    """A warning when the window reaches a year with fewer than 5 holidays
    known: the City publishes one year at a time (13 dates for 2026)."""
    per_year: Dict[int, int] = {}
    for d in skip:
        per_year[int(d[:4])] = per_year.get(int(d[:4]), 0) + 1
    thin = [y for y in range(today.year, horizon.year + 1) if per_year.get(y, 0) < 5]
    if thin:
        return (f"holiday list thin for {', '.join(map(str, thin))} "
                f"({', '.join(str(per_year.get(y, 0)) for y in thin)} known): dates there are "
                "written without a full City-holiday check")
    return None


def ingest(store: EventStore, reader: Reader, src: Dict[str, Any], cfg: Dict[str, Any],
           tz, skip: set) -> Dict[str, int]:
    api = cfg.get("api") or DEFAULT_API
    stats: Dict[str, int] = {}
    today = _today(tz)
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    schedules = reader.sql(api, (
        "SELECT id, programs, date_from, date_to, days, time_from, time_to "
        "FROM ppr_program_schedules "
        f"WHERE date_to >= '{today.isoformat()}' AND date_from <= '{horizon.isoformat()}'"))
    stats["schedules read"] = len(schedules)
    pids = {pid for s in schedules for pid in _jlist(s.get("programs"))}
    progs = reader.sql_in(api, f"SELECT {_PROGRAM_COLS} FROM ppr_programs WHERE id IN (%s)", pids, stats)
    stats["programmes read"] = len(progs)
    programs = {p["id"]: p for p in progs if p.get("id")}
    type_names = {r["id"]: r["activity_type_name"] for r in reader.sql(
        api, "SELECT id, activity_type_name FROM ppr_activity_types")}
    rules = Rules(cfg)
    fids = {f for p in programs.values() if decide(p, rules, type_names)[0] is None
            for f in _jlist(p.get("facility"))}
    facs = reader.sql_in(api, (
        "SELECT id, public_name, official_name, address, facility_is_published, contact_phone, "
        "website_locator_points_link_id FROM ppr_facilities WHERE id IN (%s)"), fids, stats) if fids else []
    facilities = {f["id"]: f for f in facs if f.get("id")}
    links = sorted({f["website_locator_points_link_id"] for f in facs
                    if re.match(r"^[A-Za-z0-9_-]{1,32}$", f.get("website_locator_points_link_id") or "")})
    locators: Dict[str, Tuple[float, float]] = {}
    if links:
        for r in reader.sql(api, (
                "SELECT linkid, ST_Y(the_geom) lat, ST_X(the_geom) lon FROM ppr_website_locatorpoints "
                "WHERE linkid IN (%s)" % ",".join(f"'{x}'" for x in links))):
            if r.get("lat") is not None and r.get("lon") is not None:
                locators.setdefault(r["linkid"], (r["lat"], r["lon"]))
    evs = build_events(schedules, programs, facilities, locators, type_names, src, cfg, tz,
                       today, skip, stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    # THE WHOLE FINDER WAS READ: every query above either answered whole (CARTO
    # SQL has no paging; a failure raises and never reaches here) or the source
    # failed. So a session the last complete read wrote and this one did not
    # is gone from the City's timetable - a programme made inactive or private,
    # a schedule cut short, a City holiday added - and mapsee_supabase_sync
    # --retire-absent may cancel it. The Finder carries no per-session
    # cancellation of its own (registration_status "Closed" is a SIGN-UP that
    # closed, and is refused as such), so absence is the only signal there is.
    if not schedules:
        # No schedule at all in 90 days is a broken query or a rebuilt table,
        # not a City that called every session off.
        _bump(stats, "read NOT complete: no schedules in the window")
        return stats
    store.mark_complete(src.get("source", "phl-parks"), today, horizon)
    stats["read complete (absence may cancel)"] = 1
    return stats


def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(open(path, encoding="utf-8").read())
    attribution = (cfg.get("attribution") or "").strip()
    if not attribution or len(attribution) > ATTRIBUTION_MAX:
        raise ValueError(f"attribution must be 1-{ATTRIBUTION_MAX} characters "
                         "(the sync keeps a final paragraph only that short)")
    if not cfg.get("timezone"):
        raise ValueError("config needs a timezone")
    if sorted((cfg.get("weekday_ids") or {}).values()) != sorted(_WEEKDAYS):
        raise ValueError("weekday_ids must name each weekday exactly once")
    Rules(cfg)  # every regex compiles, or the run stops here
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Philadelphia Parks & Recreation drop-in programmes into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    a = ap.parse_args(argv)

    started = time.monotonic()
    cfg = load_config(a.config)
    tz = ZoneInfo(cfg["timezone"])
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    reader = Reader(session, deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    # ROBOTS, EVERY RUN, ON BOTH HOSTS: one read each (2026-10-05: api.phila.gov
    # answers 404, allow-all; phl.carto.com disallows /api/ only for five named
    # search crawlers). The day either starts refusing us, this is where the
    # adapter stops reading it. Its reads are counted with the run's requests.
    robots = robots_txt.Robots(session)
    robots_hosts: set = set()
    store = EventStore(a.store)
    total = 0
    try:
        try:
            skip, notes = read_holidays(reader, cfg, robots)
            if cfg.get("holidays_api"):
                robots_hosts.add(urlsplit(cfg["holidays_api"]).netloc)
        except (Refused, OutOfTime) as exc:
            skip, notes = set(cfg.get("skip_dates") or []), [f"holiday list not read: {exc}"]
        today = _today(tz)
        for src in cfg.get("sources", []):
            label = src.get("name", "phl-parks")
            gap = holiday_gap(skip, today, today + timedelta(days=int(src.get("horizon_days", 90))))
            for n in notes + ([gap] if gap else []):
                print(f"[phl-parks] note: {n}")
            api = cfg.get("api") or DEFAULT_API
            before = reader.requests
            try:
                why = robots_allows(robots, reader, api + "?q=SELECT%201")
                robots_hosts.add(urlsplit(api).netloc)
                if why:
                    print(f"[phl-parks] {label} REFUSED: {why} - not read")
                    continue
                stats = ingest(store, reader, src, cfg, tz, skip)
            except Refused as exc:
                print(f"[phl-parks] {label} REFUSED: {exc} - not retried")
                continue
            except OutOfTime as exc:
                print(f"[phl-parks] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
                break
            except Exception as exc:  # noqa: BLE001 - one source never stops another
                print(f"[phl-parks] {label} FAILED: {exc}")
                continue
            total += stats.get("rows written", 0)
            print(f"[phl-parks] {label}: {stats.get('rows written', 0)} rows "
                  f"in {reader.requests - before} requests")
            for k, v in sorted(stats.items()):
                if k != "rows written":
                    print(f"[phl-parks]     {k}: {v}")
            store.save()
    finally:
        # On the way out too - a run that wrote nothing still writes the file
        # the sync step expects, and a cancelled one keeps what it upserted.
        store.save()
    st = store.stats
    print(f"[phl-parks] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rekeyed {st.get('rekeyed', 0)}, rejected {st.get('rejected', 0)}) in {reader.requests} "
          f"requests + {len(robots_hosts)} robots.txt; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
