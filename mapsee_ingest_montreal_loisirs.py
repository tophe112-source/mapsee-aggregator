#!/usr/bin/env python3
"""
mapsee_ingest_montreal_loisirs.py - the free-play sessions in Montreal's
sports and leisure programme: public skating, free swims, lane swims, pick-up
hockey, open badminton and basketball gyms, library drop-ins.

    python mapsee_ingest_montreal_loisirs.py --config montreal_loisirs_sources.json \
        --store feeds_events.json [--max-minutes 10]

The source is the Ville de Montreal open dataset "Programmation de sports et
loisirs disponible via Loisirs Montreal" (donnees.montreal.ca, CC BY 4.0),
resource "annee en cours", rebuilt daily (metadata 2026-10-04T00:04). It is read
through the portal's CKAN datastore API (`/api/3/action/datastore_search`), a
documented public API: catalog_curate.DOCUMENTED_API_TYPES lists `ckan`, which
is the owner's exemption from robots.txt's `Disallow: /api/`. The same file as a
CSV download is the path robots.txt allows; the host also asks
`Crawl-delay: 10`, and every request here is paced to it anyway (2 requests).
Titles and descriptions are French, as published.

WHY THIS SOURCE. It is the only Montreal-wide table of what runs when at the
City's arenas, pools, gyms and community centres. Measured live (read
2026-10-05, today fixed at 2026-10-04): 10,759 session rows for the year; 1,791
of them overlap 2026-10-04..2027-01-02, every row with the City's own lat/lon
for its site. The result is 920 rows in 90 days at 18 sites (Le Sud-Ouest 416,
Lachine 194, L'Ile-Bizard 167, Verdun 63, Bibliotheque Benny 51), 2 requests,
12 s.

------------------------------------------------------------------------------
WHAT IS KEPT: A SESSION ANYONE CAN TURN UP TO
------------------------------------------------------------------------------

1. THE FLAG IS NECESSARY, NOT SUFFICIENT. `est_inscription_obligatoire` is Vrai
   on 1,315 of the 1,791 window rows (73.4%): courses, plus 146 free-play titles
   ("Patinage libre", "Baton-Rondelle Libre") that need a reserved place -
   Toronto's "Reserve a Spot" by another name - and are refused with the rest.
   `est_annulee` Vrai is refused too (8).
   ...but Faux does NOT mean drop-in. Of the 468 Faux rows only 133 are kept.
   Most of the rest are a CLUB's season listed on the platform with nobody to
   register through it: Lachine's karate, figure-skating and minor-hockey
   clubs, scouts, a choir, Camp Energie's leagues ("1 session de 8 seances"),
   Saint-Laurent's fitness club ("L'inscription a ce cours donne acces...", 29)
   and Lachine's Centre de loisirs directory rows ("Pilates (plusieurs cours
   offerts)"). A few are court rentals ("reservez un terrain"; a pickleball
   court needing a biblio-loisirs card: 7). So a row is kept only on the
   source's own words (`verdict`):
     - a hard refusal always wins: a court or space booking, a card-holder or
       residents-only session, a lifeguard course, an equipment-loan counter,
       "l'inscription a ce cours";
     - an explicit "Aucune reservation necessaire" / "sans inscription" /
       "l'inscription n'est PAS requise" / "entree libre" keeps it (39);
     - course words veto: cours, ligue, clinique, entrainement, "N seances",
       "session de N", "inscription requise" (166);
     - "libre" in the title (patinage libre, baignade libre, pratique libre)
       keeps it (73), and so do the description's own "ouvert(e) a tous",
       "venez"/"viens", "rencontres informelles" (21: the library's chess,
       video-game and conversation groups), each counted under its reason;
     - nothing at all is "no drop-in evidence" - a club's empty listing (128).
   Every refusal is counted by reason in the run's output.

2. ONE ROW PER CONTIGUOUS SESSION, ON ITS DATE. A session row is a weekly slot
   (`jour_semaine_seance`, Lundi..Dimanche, and a clock) over a season window
   (`date_debut`..`date_fin`); it is expanded to its dates inside today..
   today + `horizon_days`. Sessions of one title at one site on one day are then
   folded (exact repeats: the same activity published twice under two ids,
   e.g. Verdun's "Zumba Gold" for 16+ and for 5+ at the same hour), joined when
   back to back or overlapping, and split at every gap - the
   mapsee_ingest_toronto_rec rule, because ../mapsee pulses a pin "Happening
   now" from start to end. Live: 9 repeats folded, 95 title-days
   split at a gap (Saint-Charles's adult swim 12-14 and 20:30-22 is two rows,
   never one 12-22), 0 rows spanning a gap. Identity is site | title (folded:
   "Patinage Libre" = "Patinage libre") | date | first clock.

3. THE WINDOW IS THE SEASON'S, AND THE TEXT KNOWS BETTER. `date_fin` is often a
   season default (2026-12-20 on most autumn rows) while the description says
   when the activity actually stops: Verdun's "Lundis Salsa ... Du 18 mai au 12
   octobre" carries date_fin 2026-12-20. So, read from the description:
     - "du 18 mai au 12 octobre" ranges: when one overlaps the row's own window,
       a date outside every stated range is not written. A range whose own
       line names weekdays ("Jeudis : 28 mai au 3 septembre") binds those
       weekdays only: pooled, 897448's "Dimanches : du 17 mai au 11 octobre"
       wrote its Thursday 10-08 and Friday 10-09, which its text had ended in
       September;
     - "Pas d'activite le 19 novembre", "Exception 15 decembre 2026",
       "Conge : 7 septembre et 12 octobre", "Non disponible ... 5 nov, 3 dec":
       those dates are not written;
     - a title that names its own date ("Badminton libre (7ans+) 05 juillet")
       is written on that date only, never every week of the season;
     - a one-day row whose weekday contradicts its date is refused, not guessed
       (901148 "Patinage libre", 2026-10-06, a Tuesday, says Lundi).
   Statutory holidays (`skip_dates`) are not written: the data carries no
   holiday closures, and a locked arena is worse than a missing skate.
   Live: 30 holiday dates, 12 Sundays of a July badminton ("05 juillet" in
   its title, season 08-31..12-20) and those 2 dance dates not written. The closure clause is read on the RAW text, line breaks intact:
   collapsed, "(sauf le 23 juin) Jeudis ... au 8 octobre" swallowed two real
   dates.

4. THE PRICE IS NOT IN THE DATA, AND THE WORDING IS A CONTRACT. There is no fee
   column. ../mapsee's migration 0227 reads `offer:free` from a row's own text,
   and it reads French ("gratuit", "entree libre") - so the source's own
   description can tag a row free. A row says free only where its own text says
   so about the activity, for everyone (`price_line`); a stated amount ("5 $",
   "tarif", "cout", "frais d'inscription" - not "l'air frais") says "(pas
   gratuit)", which 0227's FREE_NEG reads as a veto on the whole row; anything
   else says "Tarif non indique", which tags nothing. "Gratuit pour les
   enfants / residents / aines", "avec la carte" is free for SOME people: the
   session's price is unknown. 0227's FREE_PERK and FREE_COND are English
   only, so an unpriced row whose source text says "gratuit" about something
   else (a loan of skates), for some people only, or "acces libre" (open
   access) has that text withheld rather than tagged.
   The price sentence is in the FIRST paragraph and the whole description
   fits the sync's 800 characters (DESCRIPTION_MAX): past it _cap_prose trims
   the head from its end, and with the price after the source text 12 live
   rows of 837-873 characters lost it - a fee row would have lost its "(pas
   gratuit)" and read free on its "gratuit pour les enfants". The source text
   gets the room our lines leave (10 shortened live).
   Live: 63 rows free in their own words (Verdun Actif, Verdun's skates), 857
   unpriced, 0 with a fee, 0 free for some only, 0 withheld;
   ../mapsee/tools/measure_deals.py's classify, run on the row the sync's
   to_row stores, agreed with the tier on all 920.

5. THE POINT IS THE CITY'S - UNLESS ITS OWN ADDRESS SAYS OTHERWISE. latitude/
   longitude are the City's own coordinates for its own site list, so
   coords_exact is True and the sync never re-geocodes them. But the City
   geocodes a street without its borough: "504 5e Avenue, Verdun, H4G" is
   pinned in Pointe-aux-Trembles, 20 km off, and "35 rue Sainte-Anne,
   Sainte-Genevieve, H9H" 34 km off beside it - both inside the island's box,
   68 rows that read as correct and were not. A point that most other sites
   sharing its postal area (FSA) place more than 5 km away is refused
   (`contradicted_points`): 8 of the dataset's 176 sites (those two, five in
   Lachine, one in Montreal-Nord), 7 window session rows. A point outside the
   island's box is a parse gone wrong and refused. A site alone in its postal
   area cannot be checked this way and is counted (1 written).

------------------------------------------------------------------------------
THE LICENCE LINE
------------------------------------------------------------------------------

CC BY 4.0 asks for attribution. It is the description's LAST paragraph and
under 200 characters, so the sync's _cap_prose keeps it (TAIL_KEEP_MAX).

Every request carries the MapseeAggregator UA and is paced to the host's
Crawl-delay (10 s). A 401/403/429 is never retried and ends the run's reading;
a 5xx or a timeout (30 s) is retried twice. --max-minutes (default 10) is a
deadline no request starts after, and the store is saved after every source.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("This script needs Python 3.9+ (zoneinfo).")

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_API = "https://donnees.montreal.ca/api/3/action/datastore_search"
# 10,845 rows are two requests at 10,000 (measured 2026-10-03, 11 s and 9 s).
PAGE_LIMIT = 10000
# robots.txt: Crawl-delay: 10. The API is exempt from its Disallow, not from
# being polite: two requests cost 10 s of waiting.
MIN_INTERVAL_S = 10.0
REQUEST_TIMEOUT_S = 30
DEFAULT_MAX_MINUTES = 10.0
# Back to back or overlapping sessions are one stretch (toronto_rec's rule).
JOIN_MINUTES = 5
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}
ATTRIBUTION_MAX = 200
# mapsee_supabase_sync.DESCRIPTION_MAX: past it the sync's _cap_prose trims the
# HEAD from its end, which deleted the price paragraph (and its "(pas gratuit)"
# veto) from 12 live rows of 837-873 characters. The source's own text gets
# only the room our lines leave, so the sync never has to cut.
DESCRIPTION_MAX = 800
# Below this much room the source text is not worth a truncated stub.
SOURCE_TEXT_MIN = 60
# A site whose point is further than this from MOST other sites sharing its
# postal area (FSA) is refused - see contradicted_points().
SITE_CHECK_KM = 5.0


class Refused(Exception):
    """The publisher said no (401/403/429). Reported, never retried."""


class OutOfTime(Exception):
    """The run deadline (--max-minutes) has passed: no request starts after it."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """A paced reader for one CKAN datastore; counts its own requests."""

    def __init__(self, session, api: str = DEFAULT_API, min_interval: float = MIN_INTERVAL_S,
                 tries: int = 3, sleep=time.sleep, clock=time.monotonic,
                 deadline: Optional[float] = None, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self.session = session
        self.api = api
        self.min_interval = min_interval
        self.tries = tries
        self.sleep = sleep
        self.clock = clock
        self.deadline = deadline
        self.timeout = timeout
        self.requests = 0
        self.refused: Optional[str] = None
        self._last: Optional[float] = None

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)

    def call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(self.tries):
            if self.refused:
                raise Refused(f"{self.refused} earlier this run; nothing more is asked of this host")
            self._pace()
            if self.deadline is not None and self.clock() >= self.deadline:
                raise OutOfTime("run deadline reached; no request starts after it")
            try:
                self.requests += 1
                r = self.session.get(self.api, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused = f"HTTP {r.status_code} from {self.api}"
                    raise Refused(self.refused)
                if r.status_code == 200:
                    try:
                        body = r.json()
                    except ValueError as exc:
                        last = exc
                    else:
                        if body.get("success") is True and isinstance(body.get("result"), dict):
                            return body["result"]
                        raise RuntimeError(f"CKAN said no: {str(body.get('error'))[:200]}")
                elif r.status_code not in _RETRYABLE:
                    raise RuntimeError(f"HTTP {r.status_code} from {self.api}")
                else:
                    last = RuntimeError(f"HTTP {r.status_code}")
            finally:
                self._last = self.clock()
            if attempt + 1 < self.tries:
                self.sleep(2 * (attempt + 1))
        raise last

    def resource(self, resource_id: str, fields: Optional[Iterable[str]] = None,
                 limit: int = PAGE_LIMIT) -> List[Dict[str, Any]]:
        """Every row, paged by offset in `_id` order; restarts once if `total`
        moves mid-read (the table is rebuilt daily)."""
        self.last_total = None
        for _ in range(2):
            rows: List[Dict[str, Any]] = []
            total: Optional[int] = None
            moved = False
            while True:
                params: Dict[str, Any] = {"resource_id": resource_id, "limit": limit,
                                          "offset": len(rows), "sort": "_id asc"}
                if fields:
                    params["fields"] = ",".join(fields)
                res = self.call(params)
                page = res.get("records") or []
                t = res.get("total")
                if total is None:
                    total = t
                elif t != total:
                    moved = True
                    break
                rows.extend(page)
                if not page or len(page) < limit or (total is not None and len(rows) >= total):
                    break
            if not moved:
                self.last_total = total          # ingest's completeness test reads it
                return rows
        raise RuntimeError(f"resource {resource_id} changed size twice while being read")


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------
def _s(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _fold(s: str) -> str:
    """Lower case without accents, apostrophes made straight: the regexes below
    are written once, for 'reservation' and 'réservation' alike."""
    s = unicodedata.normalize("NFKD", (s or "").replace("’", "'").replace("‘", "'"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _int(v: Any) -> Optional[int]:
    try:
        return int(float(_s(v)))
    except ValueError:
        return None


def _today(tz) -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; else today in Montreal."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


def _minutes(hms: Any) -> Optional[int]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", _s(hms))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h <= 24 and mi < 60 else None


def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


WEEKDAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}

# French clock: 9 h 30, 12 h, 21 h 45 - the City's own style.
def hfr(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m:02d}" if m else f"{h} h"


def span(a: int, b: int) -> str:
    return f"{hfr(a)} à {hfr(b)}"


def _join(parts: List[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " et " + parts[-1]


def _stamp(d: date, minutes: int, tz) -> Tuple[str, str]:
    h, m = divmod(int(minutes), 60)
    local = datetime(d.year, d.month, d.day) + timedelta(hours=h, minutes=m)
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M:00"), utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def age_label(lo: Any, hi: Any) -> str:
    a, b = _int(lo), _int(hi)
    if a is not None and a <= 0:
        a = None
    if b is not None and b >= 99:
        b = None
    if a is None and b is None:
        return "Tous âges"
    if b is None:
        return f"{a} ans et plus"
    if a is None:
        return f"{b} ans et moins"
    return f"{a} à {b} ans" if a != b else f"{a} ans"


_POSTAL = re.compile(r"^([A-Z]\d[A-Z])\s*(\d[A-Z]\d)$", re.I)


def split_address(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """'330 80e Avenue  , LaSalle, H8R2T3' -> ('330 80e Avenue, LaSalle', 'H8R 2T3')."""
    parts = [p.strip() for p in _s(raw).split(",") if p.strip()]
    postal = None
    if parts and _POSTAL.match(parts[-1]):
        m = _POSTAL.match(parts.pop())
        postal = f"{m.group(1).upper()} {m.group(2).upper()}"
    return (", ".join(parts) or None), postal


def _phone(raw: Any) -> Optional[str]:
    d = re.sub(r"\D", "", _s(raw))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10:
        return None
    return f"{d[:3]} {d[3:6]}-{d[6:]}"


# ---------------------------------------------------------------------------
# French dates in the description
# ---------------------------------------------------------------------------
_MONTHS = {"janv": 1, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7,
           "aout": 8, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_RX = (r"(janv(?:ier)?|fevr(?:ier)?|fev|mars|avr(?:il)?|mai|juin|juil(?:let)?|aout|"
             r"sept(?:embre)?|oct(?:obre)?|nov(?:embre)?|dec(?:embre)?)\.?")
_DAY_RX = r"(\d{1,2})(?:er)?"
# "19 et 26 octobre", "5, 12 et 19 nov", "15 decembre 2026"
_DATE_LIST = re.compile(rf"((?:\b\d{{1,2}}(?:er)?\s*(?:,|et|&)\s*)*)\b{_DAY_RX}\s*{_MONTH_RX}(?:\s+(20\d\d))?\b")
_RANGE = re.compile(rf"\b{_DAY_RX}\s*{_MONTH_RX}(?:\s+(20\d\d))?\s*(?:au|a|-|–)\s*{_DAY_RX}\s*{_MONTH_RX}(?:\s+(20\d\d))?\b")
# Words that make the dates after them days WITHOUT the activity.
_CLOSED_CUE = re.compile(
    r"pas\s+d'activites?|pas\s+de\s+(?:cours|seances?|patin\w*|baignade|bain|activites?)|pas\s+cours|"
    r"aucune\s+(?:activite|seance)|relache|conges?\b|exceptions?\b|\bsauf\b|non\s+disponible|"
    r"\bfermee?s?\b|annulee?s?\b")


def _month(tok: str) -> int:
    t = tok.lower()
    if t.startswith("fev"):
        return 2
    return _MONTHS[t[:4] if t[:4] in _MONTHS else t[:3]]


def _place_year(m: int, d: int, year: Optional[str], lo: date, hi: date) -> Optional[date]:
    """A day and month with no year -> the year that puts it nearest [lo, hi]."""
    years = [int(year)] if year else [lo.year - 1, lo.year, lo.year + 1, hi.year + 1]
    best: Optional[Tuple[int, date]] = None
    for y in sorted(set(years)):
        try:
            dt = date(y, m, d)
        except ValueError:
            continue
        dist = 0 if lo <= dt <= hi else min(abs((dt - lo).days), abs((dt - hi).days))
        if best is None or dist < best[0]:
            best = (dist, dt)
    return best[1] if best else None


def _dates_in(text: str, lo: date, hi: date) -> Set[date]:
    out: Set[date] = set()
    for m in _DATE_LIST.finditer(text):
        month = _month(m.group(3))
        days = [int(x) for x in re.findall(r"\d{1,2}", m.group(1) or "")] + [int(m.group(2))]
        for d in days:
            dt = _place_year(month, d, m.group(4), lo, hi)
            if dt:
                out.add(dt)
    return out


def closed_dates(desc: str, lo: date, hi: date) -> Set[date]:
    """Dates the description says the activity does NOT run on: every date in
    the clause after a closure word, up to the end of that sentence."""
    t = _fold(desc)
    out: Set[date] = set()
    for cue in _CLOSED_CUE.finditer(t):
        clause = re.split(r"[.!?*)\n]|\s{3,}", t[cue.end():cue.end() + 160])[0]
        out |= _dates_in(clause, lo, hi)
    return out


# A weekday that labels a range on its line: "Jeudis : 28 mai au 3 septembre",
# "Mardis Zumba du 19 mai ...", "du 5 juin au 25 septembre les vendredis". Plural
# or followed by a colon, so "sauf le lundi 12 octobre" (one date) is not one.
_WEEKDAY_TAG = re.compile(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)(?:s\b|\s*:)")


def stated_ranges(desc: str, lo: date, hi: date, weekday: Optional[int] = None) -> List[Tuple[date, date]]:
    """'du 18 mai au 12 octobre' ranges that apply to a session on `weekday`,
    kept only if one overlaps [lo, hi]: a range about something else (a loan
    counter's summer) is not this row's.

    A range whose own line names weekdays applies to those weekdays only. 897448
    "Danse en ligne" (date_fin 2026-10-11) says "Dimanches : du 17 mai au 11
    octobre ... Jeudis : 28 mai au 3 septembre ... Vendredis : 29 mai au 4
    septembre": pooled, the Sunday range let a Thursday 10-08 and a Friday 10-09
    through. A row whose weekday no tag names keeps only the untagged ranges."""
    t = _fold(desc)
    found: List[Tuple[date, date, int, int]] = []
    for m in _RANGE.finditer(t):
        a = _place_year(_month(m.group(2)), int(m.group(1)), m.group(3), lo, hi)
        if not a:
            continue
        b_year = m.group(6) or None
        try:
            b = date(int(b_year) if b_year else a.year, _month(m.group(5)), int(m.group(4)))
        except ValueError:
            continue
        if b < a and not b_year:
            try:
                b = date(a.year + 1, b.month, b.day)
            except ValueError:
                continue
        if b >= a:
            found.append((a, b, m.start(), m.end()))
    tagged: List[Tuple[date, date, Set[int]]] = []
    for i, (a, b, s, e) in enumerate(found):
        # The range's own stretch of its line, up to its neighbours.
        left = max(t.rfind("\n", 0, s) + 1, found[i - 1][3] if i else 0)
        nl = t.find("\n", e)
        right = min(len(t) if nl < 0 else nl, found[i + 1][2] if i + 1 < len(found) else len(t))
        days = {WEEKDAYS[w.group(1)] for w in _WEEKDAY_TAG.finditer(t, left, right)}
        tagged.append((a, b, days))
    if weekday is not None:
        tagged = [r for r in tagged if not r[2] or weekday in r[2]]
    out = [(a, b) for a, b, _ in tagged]
    if any(a <= hi and b >= lo for a, b in out):
        return out
    return []


def title_dates(title: str, lo: date, hi: date) -> Set[date]:
    """A title that names its own date ('Badminton libre (7ans+) 05 juillet')."""
    return _dates_in(_fold(title), lo, hi)


# ---------------------------------------------------------------------------
# what counts as a drop-in
# ---------------------------------------------------------------------------
# Read on _fold()ed text: no accents, straight apostrophes, lower case.
_HARD_OUT = [
    ("court or space booking, not a session",
     re.compile(r"reserve[rz]?\s+(?:un|votre|vos)\s+(?:terrain|plateau|espace|court|table)|"
                r"reservations?\s+d'espaces|pour\s+reserver\s+un\s+terrain")),
    ("card-holder or residents only",
     re.compile(r"carte\s+(?:biblio-?loisirs|acces\s+montreal|de\s+membre|loisirs)|"
                r"reservee?s?\s+aux\s+(?:citoyen|resident|membre|abonne)|members?\s+only|"
                r"residents?\s+only|reserved\s+for\s+(?:city\s+of\s+montreal\s+)?residents")),
    ("a course: 'l'inscription a ce cours'", re.compile(r"inscription\s+a\s+ce\s+cours")),
    ("lifeguard qualification course",
     re.compile(r"medaille\s+de\s+bronze|croix\s+de\s+bronze|sauveteur|formation\s+en\s+sauvetage")),
    ("equipment loan counter, not a session",
     re.compile(r"^(?:centre\s+de\s+)?pret\s+de\s+materiel|centre\s+de\s+pret")),
]
_DROPIN_SAID = re.compile(
    r"aucune\s+(?:reservation|inscription)(?:\s+n'est)?\s+(?:n'est\s+)?(?:necessaire|requise|obligatoire)|"
    r"sans\s+(?:reservation|inscription)|"
    r"(?:inscription|reservation)\s+n'est\s+pas\s+(?:requise|necessaire|obligatoire)|"
    r"entree\s+libre|no\s+(?:registration|reservation)\s+(?:required|needed)|drop[- ]in")
_COURSE = re.compile(
    r"\bcours\b|\bligues?\b|\bclinique\b|\bentrainements?\b|\b\d+\s+seances\b|\bsession\s+de\s+\d+|"
    r"inscription\s+(?:est\s+)?(?:requise|obligatoire)|inscrivez-vous|\bplusieurs\s+(?:types?\s+de\s+)?cours")
_DROPIN_HINT_TITLE = re.compile(r"\blibres?\b|\bouverte?s?\s+a\s+tous\b")
_DROPIN_HINT_TEXT = re.compile(
    r"\bouverte?s?\s+a\s+tou(?:s|tes)\b|\bvenez\b|\bviens\b|rencontres?\s+informelles?|en\s+libre\s+acces|"
    r"pratique\s+libre|\bpatinage\s+libre\b|\bbaignade\s+libre\b|accueille\s+tout\s+le\s+monde|"
    r"tous\s+sont\s+les\s+bienvenus|bienvenue\s+a\s+tous")


def verdict(title: str, desc: str) -> Tuple[bool, str]:
    """(kept, reason). Read on the activity's own title and description."""
    t, d = _fold(title), _fold(desc)
    both = f"{t}\n{d}"
    for reason, rx in _HARD_OUT:
        if rx.search(t) or rx.search(d):
            return False, reason
    if _DROPIN_SAID.search(both):
        return True, "drop-in said in words"
    if _COURSE.search(both):
        return False, "a course, league or club session (its words)"
    if _DROPIN_HINT_TITLE.search(t):
        return True, "free play by its title"
    if _DROPIN_HINT_TEXT.search(d):
        return True, "drop-in by its description's words (venez, ouvert a tous)"
    return False, "no drop-in evidence (a club's or course's listing)"


# ---------------------------------------------------------------------------
# price
# ---------------------------------------------------------------------------
_FREE_SAID = re.compile(r"\bgratuite?s?\b|\bentree\s+libre\b")
# 'gratuit' that is not this session's price: a free loan of skates, free parking.
_FREE_OTHER = re.compile(r"(?:pret|stationnement|materiel|equipement|location)\s+(?:de\s+\w+\s+)?gratuite?s?")
# 'gratuit' for SOME people only: children, residents, seniors, card holders.
# The session's own price is then unknown, not free.
_FREE_COND = re.compile(
    r"\bgratuite?s?\s+(?:pour\s+(?:les\s+|la\s+|le\s+|l')?(?:enfants?|jeunes|tout-petits|bebes|petits|"
    r"residente?s?|citoyen\w*|aine\w*|membres?|abonnee?s?|detenteur\w*|accompagnat\w*|parents?|"
    r"moins\s+de|\d+\s*ans)|avec\s+(?:la\s+|votre\s+|une\s+)?carte)|"
    r"\b(?:enfants?|aine\w*|residente?s?|membres?|abonnee?s?|\d+\s*ans(?:\s+et\s+moins)?)\s*[:\-–]\s*gratuite?s?\b")
# A fee in words. Not a bare "frais": "l'air frais" is not a price, and
# "sans frais" says the opposite.
_FEE_SAID = re.compile(
    r"\d+(?:[,.]\d{1,2})?\s*\$|\$\s*\d|\btarifs?\b|\bcouts?\b|\bpayante?s?\b|non[- ]gratuit|pas\s+gratuit|"
    r"\bfrais\s+(?:d'(?:inscription|entree|admission|acces|adhesion|administration|participation)|"
    r"de\s+(?:\d|participation|location|base))|\bdes\s+frais\b|\bfrais\s+(?:applicables|s'appliquent|requis)")


# The phrases ../mapsee's 0227 tagger reads as `offer:free` in French and
# English (a subset of measure_deals.FREE, the twin of event_offers()). 0227's
# FREE_PERK list is English only, so "pret de patins gratuit" passed through as
# the source wrote it would tag an unpriced session free.
_TAGGED_FREE = re.compile(
    r"\bgratuit(?:e|s|es)?\b|\bentree\s+(?:libre|gratuite)\b|\bacces\s+(?:libre|gratuit)\b|\bgratis\b|"
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|join|enter|participate|"
    r"the\s+public|all|everyone)|for\s+(?:all|everyone|kids|children|the\s+public|families)|session|class|"
    r"classes|workshop|drop[- ]in|skate|swim)\b|\bis\s+free\b|\bat\s+no\s+cost\b|\bno\s+cost\b")


def price_line(title: str, desc: str) -> Tuple[str, str]:
    """(tier, sentence). 'free' only where the source's own words say so about
    the activity; a stated fee says '(pas gratuit)' (0227's FREE_NEG vetoes);
    otherwise nothing that 0227 could read as an offer. 'conditional' is free
    for some people only ("Gratuit pour les enfants de 5 ans et moins"): the
    session's price is unknown, and its text is withheld like an unpriced one."""
    text = _fold(f"{title}\n{desc}")
    if _FEE_SAID.search(text):
        return "fee", "Des frais s'appliquent selon la source (pas gratuit)."
    rest = _FREE_OTHER.sub(" ", text)
    if _FREE_SAID.search(_FREE_COND.sub(" ", rest)):
        return "free", "Activité gratuite selon la source. Admission: free."
    unknown = "Tarif non indiqué dans les données ; renseignez-vous auprès du site."
    return ("conditional" if _FREE_COND.search(rest) else "unknown"), unknown


# ---------------------------------------------------------------------------
# category
# ---------------------------------------------------------------------------
_KIDS_TITLE = re.compile(r"\b(?:enfants?|bebes?|tout-petits|parents?-enfants|familles?|familial|"
                         r"\d+\s*ans\s+et\s+moins|jeunes)\b")


def category(row: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    """(primary, extras) from the City's sous_categorie, then categorie, then a
    title word; kids as a secondary when the band or the title is a child's."""
    sub, cat, title = _s(row.get("sous_categorie")), _s(row.get("categorie")), _fold(row.get("nom"))
    primary = None
    for word, key in (cfg.get("category_by_title_word") or {}).items():
        if re.search(rf"\b{re.escape(_fold(word))}", title):
            primary = key
            break
    primary = (primary or (cfg.get("category_by_subcategory") or {}).get(sub)
               or (cfg.get("category_by_category") or {}).get(cat)
               or cfg.get("category_default", "community"))
    extras: List[str] = []
    hi = _int(row.get("age_maximum"))
    if (hi is not None and hi <= int(cfg.get("kids_age_max", 13))) or _KIDS_TITLE.search(title):
        extras.append("kids")
    return primary, norm_categories(primary, extras)


# ---------------------------------------------------------------------------
# rows -> events
# ---------------------------------------------------------------------------
def _in_box(lat: float, lon: float, box: Optional[List[float]]) -> bool:
    if not box:
        return True
    s, w, n, e = box
    return s <= lat <= n and w <= lon <= e


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 6371.0 * math.hypot((lo2 - lo1) * math.cos((la1 + la2) / 2), la2 - la1)


def _point(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    try:
        return round(float(row.get("latitude")), 5), round(float(row.get("longitude")), 5)
    except (TypeError, ValueError):
        return None


def _fsa(row: Dict[str, Any]) -> Optional[str]:
    postal = split_address(row.get("adresse_site_seance"))[1]
    return postal[:3] if postal else None


def contradicted_points(rows: List[Dict[str, Any]], radius_km: float = SITE_CHECK_KM
                        ) -> Tuple[Dict[Tuple[str, Tuple[float, float]], float], Set[Tuple[str, Tuple[float, float]]]]:
    """({(FSA, point): median km to its peers} for points MOST of their postal
    area's other sites disagree with, {(FSA, point) with no peer to ask}).

    The City geocodes a site's street without its borough. Measured 2026-10-05
    over the 176 sites (153 distinct points) of all 10,759 rows: "504 5e
    Avenue, Verdun, H4G" sits at 45.636,-73.492 (20 km off, in
    Pointe-aux-Trembles), "35 rue Sainte-Anne, Sainte-Genevieve, H9H" 34 km off
    beside it, five Lachine (H8S) sites 8-27 km off (on downtown's Notre-Dame,
    Sherbrooke and Saint-Antoine, Verdun's 5e Avenue, the east end's 19e
    Avenue), and one Montreal-Nord site (H1G) 11 km off on the wrong end of
    boulevard Gouin. Each is inside the island's box. Peers are the distinct
    points of the other sites sharing the postal code's first three characters;
    a point is refused when strictly more of them lie beyond `radius_km` than
    within it (a tie keeps it). The other sites sit at a median 0.8 km from
    their postal area's (p90 1.6 km, max 3.1 km), the eight wrong ones at 8-34
    km; 17 points are alone in their postal area and go unchecked."""
    by_fsa: Dict[str, Set[Tuple[float, float]]] = {}
    for row in rows:
        p, fsa = _point(row), _fsa(row)
        if p and fsa:
            by_fsa.setdefault(fsa, set()).add(p)
    bad: Dict[Tuple[str, Tuple[float, float]], float] = {}
    alone: Set[Tuple[str, Tuple[float, float]]] = set()
    for fsa, points in by_fsa.items():
        for p in points:
            dist = sorted(_km(p, q) for q in points if q != p)
            if not dist:
                alone.add((fsa, p))
            elif sum(1 for d in dist if d > radius_km) * 2 > len(dist):
                bad[(fsa, p)] = dist[len(dist) // 2]
    return bad, alone


def seniors_slot(desc: str, day: date, start: int) -> bool:
    """The description gives this weekday and clock to seniors: 901343 "Patinage
    libre tous" (6+) says "Patin libre pour aines mardi, jeudi et vendredi 10 h
    - 11h" while its rows carry the 6+ band."""
    name = next(k for k, v in WEEKDAYS.items() if v == day.weekday())
    h, m = divmod(start, 60)
    clock = rf"\b{h}\s*h\s*{m:02d}\b" if m else rf"\b{h}\s*h(?!\s*[0-5]\d)"
    # One sentence or line at a time: 901343 writes its three slots as three
    # sentences on one line, and "pour tous ... DIMANCHE DE 13H15" is not seniors'.
    return any(re.search(r"\baine", ln) and re.search(rf"\b{name}s?\b", ln) and re.search(clock, ln)
               for ln in re.split(r"\n|[.;!?](?:\s|$)", _fold(desc)))


def occurrences(row: Dict[str, Any], today: date, horizon: date, skip: Set[date],
                stats: Dict[str, int]) -> List[date]:
    """The dates one weekly session row runs on inside [today, horizon]."""
    try:
        lo, hi = date.fromisoformat(_s(row.get("date_debut"))[:10]), date.fromisoformat(_s(row.get("date_fin"))[:10])
    except ValueError:
        _bump(stats, "bad season dates")
        return []
    wd = WEEKDAYS.get(_fold(row.get("jour_semaine_seance")))
    if wd is None:
        _bump(stats, "no weekday")
        return []
    if lo == hi and lo.weekday() != wd:
        # 901148 "Patinage libre" 2026-10-06 (a Tuesday) says Lundi. Which half
        # is wrong is not ours to guess.
        _bump(stats, "one-day row whose weekday contradicts its date")
        return []
    # The RAW text: its line breaks end a "Pas d'activite le ..." clause.
    desc, title = str(row.get("description") or ""), _s(row.get("nom"))
    closed = closed_dates(desc, lo, hi)
    ranges = stated_ranges(desc, lo, hi, wd)
    named = title_dates(title, lo, hi)
    out: List[date] = []
    d = max(lo, today)
    d += timedelta(days=(wd - d.weekday()) % 7)
    end = min(hi, horizon)
    while d <= end:
        if d in skip:
            _bump(stats, "dates not written: statutory holiday")
        elif d in closed:
            _bump(stats, "dates not written: the description says no session")
        elif named and d not in named:
            _bump(stats, "dates not written: the title names another date")
        elif ranges and not any(a <= d <= b for a, b in ranges):
            _bump(stats, "dates not written: outside the description's own dates")
        else:
            out.append(d)
        d += timedelta(days=7)
    return out


def stretches(times: Iterable[Tuple[int, int]], join: int = JOIN_MINUTES
              ) -> List[Tuple[int, int, List[Tuple[int, int]]]]:
    out: List[List[Any]] = []
    for a, b in sorted(set(times)):
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append((a, b))
        else:
            out.append([a, b, [(a, b)]])
    return [(a, b, parts) for a, b, parts in out]


def events(rows: List[Dict[str, Any]], src: Dict[str, Any], cfg: Dict[str, Any], tz, today: date,
           stats: Dict[str, int], as_if_live: bool = False, as_if_placed: bool = False) -> List[NormalizedEvent]:
    """The rows to write. `as_if_live` reads every `est_annulee` session as
    running: the rows the City's table would give had nothing been called off
    (cancelled_events). `as_if_placed` skips the three placement refusals (no
    point, a point off the island, a point its postal area contradicts): the
    sessions the City LISTS whatever we make of their point (seen_events)."""
    horizon = today + timedelta(days=int(src.get("horizon_days", 90)))
    skip = {date.fromisoformat(x) for x in cfg.get("skip_dates") or []}
    if skip and max(skip) < horizon and not (as_if_live or as_if_placed):
        print(f"[montreal-loisirs] WARNING: skip_dates end {max(skip)}, before the horizon {horizon}; "
              "add the next statutory holidays to the config")
    box = cfg.get("bbox")
    bad_points, alone = contradicted_points(rows, float(cfg.get("site_check_km") or SITE_CHECK_KM))
    unchecked: Set[str] = set()
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    verdicts: Dict[str, Tuple[bool, str]] = {}
    for row in rows:
        lo, hi = _s(row.get("date_debut"))[:10], _s(row.get("date_fin"))[:10]
        if hi < today.isoformat() or lo > horizon.isoformat():
            _bump(stats, "outside the window (season over or not begun)")
            continue
        _bump(stats, "session rows in the window")
        if _s(row.get("est_annulee")).lower() == "vrai" and not as_if_live:
            _bump(stats, "refused: cancelled (est_annulee)")
            continue
        if _s(row.get("est_inscription_obligatoire")).lower() != "faux":
            _bump(stats, "refused: registration required (est_inscription_obligatoire)")
            if _DROPIN_HINT_TITLE.search(_fold(row.get("nom"))):
                _bump(stats, "  of which a free-play title that needs a reserved place")
            continue
        title, desc = _s(row.get("nom")), _s(row.get("description"))
        key = f"{title}\n{desc}"
        if key not in verdicts:
            verdicts[key] = verdict(title, desc)
        kept, why = verdicts[key]
        if not kept:
            _bump(stats, f"refused: {why}")
            continue
        _bump(stats, f"kept: {why}")
        a, b = _minutes(row.get("heure_debut_seance")), _minutes(row.get("heure_fin_seance"))
        if a is None or b is None or b <= a:
            _bump(stats, "no usable time")
            continue
        if _point(row) is None and not as_if_placed:
            _bump(stats, "unplaceable (no point)")
            continue
        lat, lon = (float(row.get("latitude")), float(row.get("longitude"))) if _point(row) else (None, None)
        if lat is not None and not _in_box(lat, lon, box) and not as_if_placed:
            _bump(stats, "unplaceable (point outside the island)")
            continue
        site = _s(row.get("site_seance"))
        if not title or not site:
            _bump(stats, "no title or site")
            continue
        here = (_fsa(row) or "", _point(row))
        if here in bad_points and not as_if_placed:
            _bump(stats, "unplaceable (the City's point contradicts its own address)")
            _bump(stats, f"  {site}, {split_address(row.get('adresse_site_seance'))[1]}: "
                         f"{bad_points[here]:.0f} km from its postal area's other sites")
            continue
        days = occurrences(row, today, horizon, skip, stats)
        if days and (here in alone or not here[0]):
            unchecked.add(site)
        r = dict(row, _start=a, _end=b, _lat=lat, _lon=lon)
        for d in days:
            # Keyed on the folded title: "Patinage Libre" and "Patinage libre"
            # at one rink at one hour are one session.
            groups.setdefault((site, _norm(title), d.isoformat()), []).append(r)

    out: List[NormalizedEvent] = []
    attribution = cfg["attribution"]
    for (site, _key, day_s), sessions in sorted(groups.items()):
        sessions.sort(key=lambda s: (s["_start"], s["_end"], _s(s.get("id_activite")), _s(s.get("id_seance"))))
        title = _s(sessions[0].get("nom"))
        times = sorted({(s["_start"], s["_end"]) for s in sessions})
        _bump(stats, "repeat sessions folded", len(sessions) - len(times))
        runs = stretches(times)
        if len(runs) > 1:
            _bump(stats, "title-days split at a gap")
        first = sessions[0]
        primary, extras = category(first, src)
        for s in sessions[1:]:
            p2, e2 = category(s, src)
            extras = norm_categories(primary, extras, [p2], e2)
        day = date.fromisoformat(day_s)
        address, postal = split_address(first.get("adresse_site_seance"))
        for a, b, parts in runs:
            inside = [s for s in sessions if a <= s["_start"] and s["_end"] <= b]
            lead = inside[0]
            ages = sorted({age_label(s.get("age_minimum"), s.get("age_maximum")) for s in inside} - {"Tous âges"})
            if any(seniors_slot(str(s.get("description") or ""), day, s["_start"]) for s in inside):
                _bump(stats, "age band from the description: a seniors' slot")
                ages = ["aînés, selon la description"]
            tier, price = price_line(title, " ".join(_s(s.get("description")) for s in inside))
            _bump(stats, f"price: {tier}")
            others = [span(x, y) for x, y, _ in runs if (x, y) != (a, b)]
            phone = _phone(lead.get("telephone_site_seance"))
            promoter = _s(lead.get("promoteur"))
            # The price is in the FIRST paragraph: the sync trims a long head
            # from its end, and the "(pas gratuit)" veto must survive it.
            head = (f"Activité libre, sans inscription{' (' + ' ; '.join(ages) + ')' if ages else ''}. "
                    + (f"Offerte par {promoter}, à {site}. " if promoter and _norm(promoter) != _norm(site)
                       else f"À {site}. ") + price)
            tail = [
                (f"Séances : {_join([span(x, y) for x, y in parts])}." if len(parts) > 1 else None),
                (f"Aussi ce jour-là : {_join(others)}." if others else None),
                (f"Renseignements : {phone}." if phone else None),
                attribution,
            ]
            # The source's text gets the room our own lines leave under the
            # sync's cap, so the sync never cuts this description.
            room = DESCRIPTION_MAX - len("\n\n".join([head] + [p for p in tail if p])) - 2
            desc_src = _s(lead.get("description"))
            if len(desc_src) > room:
                desc_src = desc_src[:max(room - 2, 0)].rsplit(" ", 1)[0] + " …" if room >= SOURCE_TEXT_MIN else ""
                _bump(stats, "source text shortened to fit the sync's cap" if desc_src
                      else "source text dropped: no room under the sync's cap")
            if tier in ("unknown", "conditional") and _TAGGED_FREE.search(_fold(desc_src)):
                # The source says free about something else (a loan of skates),
                # for some people only ("gratuit pour les enfants"), or "acces
                # libre" meaning open access: as written, 0227 would call this
                # session free. The text is withheld, not rewritten.
                _bump(stats, "source text withheld: it reads as free, not about the price")
                desc_src = ""
            paras = [head, desc_src or None] + tail
            sl, su = _stamp(day, a, tz)
            el, eu = _stamp(day, b, tz)
            hm = f"{a // 60:02d}:{a % 60:02d}"
            ev = NormalizedEvent(
                source=src.get("source", "montreal-loisirs"),
                # The City's id_seance is one weekly slot of one activity and
                # the same session is published under two ids (Zumba Gold for
                # 16+ and 5+); site, title, day and first clock are the stretch.
                source_id=f"{_norm(site)}|{_norm(title)}|{day_s}|{hm}",
                name=title,
                description="\n\n".join(p for p in paras if p),
                start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                timezone=cfg.get("timezone"),
                venue_name=site,
                latitude=lead["_lat"], longitude=lead["_lon"],
                coords_exact=True,
                address=address,
                city=cfg.get("city"), region=cfg.get("region"), country=cfg.get("country"),
                postal_code=postal,
                category=primary, categories=extras,
                promoter=_s(lead.get("promoteur")) or None,
                ticket_url=src.get("info_url") or src.get("dataset"),
            )
            ev.fingerprint = make_fingerprint(f"{title} {hm}", day_s, f"{site} {address or ''}".strip())
            out.append(ev)
    if unchecked:
        _bump(stats, "sites written whose point no other site in its postal area could check", len(unchecked))
    return out


def cancelled_events(rows: List[Dict[str, Any]], live: List[NormalizedEvent], src: Dict[str, Any],
                     cfg: Dict[str, Any], tz, today: date) -> List[NormalizedEvent]:
    """The rows `est_annulee` took off: what events() gives with every
    cancelled session read as running, less what it gives today. Each is built
    by the code that built the live row, so it carries the fingerprint that
    row has in the database (events.external_id) - a stretch's identity is its
    FIRST clock, and a cancelled 10:00 hour in a 10:00-12:00 stretch moves the
    surviving row to 11:00, so the 10:00 row is the one that must go. A
    session the policy would refuse anyway (registration, no place) gives
    nothing. 8 of the 1,791 window rows were est_annulee on 2026-10-05."""
    if not any(_s(r.get("est_annulee")).lower() == "vrai" for r in rows):
        return []
    have = {ev.fingerprint for ev in live}
    return [ev for ev in events(rows, src, cfg, tz, today, {}, as_if_live=True) if ev.fingerprint not in have]


def seen_events(rows: List[Dict[str, Any]], live: List[NormalizedEvent], src: Dict[str, Any],
                cfg: Dict[str, Any], tz, today: date, stats: Dict[str, int]) -> List[str]:
    """Fingerprints of sessions the City LISTS that a placement check refused
    (no point, off the island, a point its postal area contradicts): still on
    its timetable, so EventStore.mark_seen, and absence never cancels one. A
    point is a property of the row AND of its neighbours (contradicted_points),
    so a neighbour's new point can refuse a site whose sessions all still run.
    Only computed when a placement check refused something."""
    if not any(stats.get(k) for k in ("unplaceable (no point)", "unplaceable (point outside the island)",
                                      "unplaceable (the City's point contradicts its own address)")):
        return []
    have = {ev.fingerprint for ev in live}
    return sorted({ev.fingerprint for ev in events(rows, src, cfg, tz, today, {}, as_if_placed=True)} - have)


def ingest(store: EventStore, reader: Reader, src: Dict[str, Any], cfg: Dict[str, Any], tz) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    rows = reader.resource(src["resource_id"], fields=src.get("fields"))
    stats["session rows read"] = len(rows)
    if not rows:
        raise RuntimeError("the datastore returned no rows")
    today = _today(tz)
    evs = events(rows, src, cfg, tz, today, stats)
    for ev in evs:
        store.upsert(ev)
    stats["rows written"] = len(evs)
    for ev in cancelled_events(rows, evs, src, cfg, tz, today):
        if store.cancel(ev, "est_annulee") == "cancelled":
            _bump(stats, "rows cancelled (est_annulee: a tombstone for the row we wrote)")
    for fp in seen_events(rows, evs, src, cfg, tz, today, stats):
        store.mark_seen(src.get("source", "montreal-loisirs"), fp)
        _bump(stats, "listed but unplaced (seen, never absent)")
    # THE WHOLE TABLE WAS READ (every page, and as many rows as CKAN's own
    # total), so a session the last complete read wrote and this one did not
    # is gone from the City's timetable: mapsee_supabase_sync --retire-absent
    # may cancel it. The window is the one events() expanded into.
    total = getattr(reader, "last_total", None)
    if total is None or len(rows) >= int(total):
        store.mark_complete(src.get("source", "montreal-loisirs"), today,
                            today + timedelta(days=int(src.get("horizon_days", 90))))
        stats["read complete (absence may cancel)"] = 1
    else:
        _bump(stats, f"read NOT complete: {len(rows)} of {total} rows")
    return stats


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(open(path, encoding="utf-8").read())
    attribution = (cfg.get("attribution") or "").strip()
    if not attribution or len(attribution) > ATTRIBUTION_MAX:
        raise ValueError(f"attribution must be 1-{ATTRIBUTION_MAX} characters "
                         "(the sync keeps a final paragraph only that short)")
    if not cfg.get("timezone"):
        raise ValueError("config needs a timezone")
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Montreal's free-play sport and leisure sessions into the Mapsee store.")
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
    reader = Reader(session, cfg.get("api") or DEFAULT_API,
                    min_interval=float(cfg.get("crawl_delay") or MIN_INTERVAL_S),
                    deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    store = EventStore(a.store)
    total = 0
    unsaved, saved = False, False
    for src in cfg.get("sources", []):
        label = src.get("name", "montreal-loisirs")
        if reader.refused:
            print(f"[montreal-loisirs] {label}: NOT READ - {reader.refused} earlier this run (one host)")
            continue
        before = reader.requests
        unsaved = True
        try:
            stats = ingest(store, reader, src, cfg, tz)
        except Refused as exc:
            print(f"[montreal-loisirs] {label} REFUSED: {exc} - not retried, and the host is asked nothing more")
            continue
        except OutOfTime as exc:
            print(f"[montreal-loisirs] {label} STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
            break
        except Exception as exc:  # noqa: BLE001 - one source never stops another
            print(f"[montreal-loisirs] {label} FAILED: {exc}")
            continue
        total += stats.get("rows written", 0)
        print(f"[montreal-loisirs] {label}: {stats.get('rows written', 0)} rows, "
              f"{stats.get('rows cancelled (est_annulee: a tombstone for the row we wrote)', 0)} cancelled, "
              f"in {reader.requests - before} requests")
        for k, v in sorted(stats.items()):
            if k != "rows written":
                print(f"[montreal-loisirs]     {k}: {v}")
        store.save()
        unsaved, saved = False, True
    if unsaved or not saved:
        store.save()
    st = store.stats
    print(f"[montreal-loisirs] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rekeyed {st.get('rekeyed', 0)}, rejected {st.get('rejected', 0)}) in {reader.requests} "
          f"requests; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
