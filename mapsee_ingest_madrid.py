#!/usr/bin/env python3
"""
mapsee_ingest_madrid.py - the Ayuntamiento de Madrid's own agenda: what is on
at the city's centros culturales, centros socioculturales, espacios de
igualdad, libraries and sports grounds, and at its theatres and museums.

    python mapsee_ingest_madrid.py --config madrid_sources.json \
        --store feeds_events.json [--max-minutes 3]

PARKED, AND WHY. datos.madrid.es's robots.txt refuses every path to these
rows, so the config ships as madrid_sources.json.pending-permission and no
workflow step runs it. Checked 2026-10-05 (the file's Last-Modified is
2026-09-29), under `User-agent: *`:
  - the datasets' JSON files (/egob/catalogo/300107-0-agenda-actividades-
    eventos.json, the path the 2026-10-03 survey read) answer 302 to
    /dataset/*/resource/*/download/*, which is Disallowed;
  - the CKAN datastore holding the same rows is Disallowed BY NAME, under the
    comment "# bloquear endpoints concretos del datastore":
    `Disallow: /api/3/action/datastore_search` - beside `/api/`, `/*?`,
    `/resource/*` and `/datastore/`;
  - the portal's own published API spec (/swagger/openapi.yaml, "CKAN Public
    GET API", 25 endpoints) lists no datastore_* call, so the owner's
    exemption for a documented public API (catalog_curate.
    DOCUMENTED_API_TYPES has `ckan`) does not reach it. Montreal is no
    precedent: there only the stock `Disallow: /api/` applies, and the CSV
    download path is allowed.
  - www.madrid.es Disallows /sites, which holds every event page.
So `Reader.robots_gate` asks robots.txt about the exact first request before
anything else, and a Disallow stops the run (exit 0, 0 rows, one request)
unless the config's `permission` records the Ayuntamiento's written yes. An
unreachable or challenged robots.txt stops it whatever `permission` says. To
switch it on: ask through datos.madrid.es's "Contacta" form, write the reply
(who, date, scope) into `permission`, rename the config to
madrid_sources.json, then add the workflow step and the AGENTS.md row.

THE DATA. Two datasets (CC BY 4.0, rebuilt daily): "Agenda de actividades y
eventos" (300107) and "Actividades culturales y de ocio municipal en los
proximos 100 dias" (206974), read as each one's CSV resource in the datastore
(2 requests, 10 s apart: robots.txt asks `Crawl-delay: 10`). Each event has a
start and end date, a clock, weekly days (`DIAS-SEMANA`, L..D) and excluded
days, a free flag and a price, the venue, its street and postal code and the
City's own point. Titles and descriptions are Spanish, as published.
`item_from_row` turns a CSV row into the JSON files' shape, so one set of
rules reads both: on the 1,714 ids the 2026-10-03 JSON and 2026-10-05 CSV
share, dates, clocks, weekdays, points, free flags and description presence
agree; 29 prices differ by a trailing non-breaking space.

WHAT IT WOULD WRITE. Nothing else on the map covers Madrid's municipal
programme. Replaying the 2026-10-05 datastore pull (1,766 + 1,403 rows,
1,767 ids, 1,530 overlapping the 90 days from today) through these rules
offline: 975 rows at 147 venues - 616 at community places (madrid:centros),
359 at theatres, cinemas and parks (madrid:agenda); 735 free in the City's
own flag, 63 with a fee, 177 unpriced; 880 timed, 95 all-day.

------------------------------------------------------------------------------
WHAT IS KEPT
------------------------------------------------------------------------------

1. A SESSION ANYONE CAN TURN UP TO, OR A SHOW ANYONE CAN BUY A SEAT FOR. There
   is no registration field, so `verdict` reads the source's own words (title,
   description and price, accents folded), in this order:
     - always out: online-only rows pinned to a library ("Taller online
       'Remedial English'"), closure and cancellation notices, governance
       meetings, sessions for members / registered residents / school groups
       only, appointments ("cita previa"), and application calls ("Convocatoria
       abierta ... Las solicitudes serán evaluadas": Bohemios' Artrics), and a
       title that puts the act in a square the venue is not ("Acto
       comunitario en Plaza Prosperidad", pinned 1.9 km away at an Espacio de
       Igualdad);
     - kept: an explicit "sin inscripción", "no es necesaria la inscripción",
       "de carácter abierto" - checked before the registration words, because
       "Actividad gratuita y de carácter abierto, sin inscripción previa" holds
       the phrase "inscripción previa" too;
     - out: a programme that signs people up on another site: Villaverde's
       "Deporte en la Calle" (16 items, 185 rows a quarter, every text empty;
       sign-ups at inscripciones.pebetero.com), or any text naming such a form;
     - out: "inscripción previa", "previa inscripción", "inscripciones: ...",
       "inscribirse", "imprescindible reserva", "reserva de plaza",
       "matrícula" - each match checked for a "sin / no" right before it;
     - out: a course: "curso", "N sesiones", "trimestral", "nivel B1" in a
       workshop row (below), unless the text also says "entrada libre",
       "acceso libre" or "hasta completar aforo";
     - kept: "entrada libre", "acceso libre", "hasta completar aforo";
     - a WORKSHOP row that says NOTHING about how to join is out. Workshop
       rows are the types where registration is the rule - on the 2026-10-03
       pull, of the rows whose text says how to join, registration in 25 of
       38 Cursos y talleres, 35 of 38 guided visits, 25 of 28 nature walks, 5
       of 7 reading clubs - plus any title naming a workshop or course
       ("Taller de iniciación a castañuelas. Turno de mañana", a Música row
       every Thursday to June) - plus any SILENT row at an Espacio de Igualdad
       (73 of their 75 items in the window had no text; they take sign-ups by
       phone) except a commemoration;
     - out: a weekly season of over 14 days with no text at all, unless an
       audience watches it (a cine-club, a play, a concert, a talk):
       "Tertulias en latín", "Grupo de crianza".
   Every other agenda row - a concert, a play, a talk, a film, a storytelling -
   is an event and is kept.
   A ticket ("previa reserva de entradas", "retirada de invitaciones") is a
   seat, not a registration: kept.
   Replay of the 2026-10-05 pull, 1,530 items in the window: 592 refused - 288
   workshops/visits/clubs with no word on joining, 210 registration, 45
   silent seasons, 18 Deporte en la Calle, 14 application calls, 8 online, 6
   courses, 1 appointment, 1 closure, 1 square elsewhere; of the 938 left, 38
   had no point, 116 gave no timetable (2. below), 784 were written (682
   agenda events, 97 open admission, 5 "sin inscripción").

2. ONE ROW PER SESSION, ON ITS DATE. A single-day row is its date at `time`
   (dtend's clock is always 23:59, a placeholder, so the end is left to the
   sync unless the text states "de 17:15 a 18:15 h" for that very clock). A
   row with a weekly `recurrence` is expanded over dtstart..dtend, minus its
   `excluded-days`, inside today..today+90 - but the recurrence is not always
   the timetable, and the text says when it is not:
     - dates the description names ("Jueves, 22 y 29 de octubre ... 5, 12, 19
       y 26 de noviembre", "Sábados 3, 10, 17 y 24 de octubre") are the
       sessions: a weekly row writes only those of its days (a "del 1 al 29 de
       octubre" range is a range, not two sessions);
     - an exception clause is read apart, never as the list of sessions:
       "excepto el 12 de octubre" and "no habrá sesión el 9 de noviembre"
       close those dates; "salvo el 23 de octubre y el 20 de noviembre que
       serán de 16 a 18:15 horas" moves those two sessions to those hours (a
       reading club's, live: 2 rows), the rest keep "de 17:30 a 19 horas";
     - a named date with its own clock keeps it: "Sábado 17 de octubre a las
       10.00 horas" is 10:00 although HORA says 19:00 ('Dibuja Madrid');
     - "el tercer jueves de cada mes" is the third Thursday, not every one; a
       monthly or fortnightly club with no dates given is refused;
     - an exhibition (Exposiciones, or a title that starts "Exposición") over
       several days is opening hours, not a session: its `time` is the opening
       hour and its text two or three stretches a day. Written, it would be one
       ALL-DAY row per open day, which ../mapsee pulses "Happening now" through
       every closed hour - 1,432 rows from 64 exhibitions on the replay, 59% of
       the 2,407 it would write with them. So `write_exhibitions` is false: they are counted and left
       out until the owner decides (true writes them, tested);
     - a long recurrence (over 14 days) with no clock, or one clock on three or
       more days a week, is a PERIOD, not a timetable ("Talleres Junior de
       cómic" every day of eight weeks for three sessions; a reading club
       "every day" that meets on the third Thursday) and is refused;
     - the City's every-day placeholder (L..D) with no clock and no dates over
       four calendar days or more is a run whose days are not given, refused:
       'Andrea Jiménez. Contra Antígona' was ten all-day rows (26 Nov - 5 Dec,
       a theatre dark on Mondays), 'XXXII Grandes del góspel' ten more; 6
       items, 44 all-day rows on the pull, 38 of them with no text. A
       placeholder day another id times as a show at the same venue is
       dropped too ('Compañía Juan Berlanga' all-day Friday 4 Dec beside 'Cía.
       Juan Berlanga', Saturday 5 Dec 20:00 only);
     - weekly sessions over more than 14 days skip the city's public holidays
       (`skip_dates`; 12 October is the date the city itself excludes most
       often), but a single dated row is never skipped.
   Two items for one session (same venue, title, date and clock) are one row.
   Replay: 723 single-day items, 41 short runs, 13 weekly series, 7 written on
   the dates their text names; 18 + 10 periods, 17 placeholders, 5 multi-day
   rows with no weekdays and 1 with dates for other weekdays refused, 64
   exhibitions left out; 2 holiday dates not written; 95 rows all-day (no
   clock anywhere), 4 clocks read from the text, 1 from a named date, 20 ends
   from a stated span.

3. THE PRICE IS THE SOURCE'S, AND THE WORDING IS A CONTRACT. ../mapsee's
   migration 0227 tags `offer:free` from a row's own text and reads Spanish
   ("gratuito", "entrada libre", "acceso libre") - and vetoes the whole row on
   "no es gratis". The price sentence is the FIRST paragraph and the whole
   description fits the sync's 800 characters (DESCRIPTION_MAX), because
   _cap_prose trims the head from its end:
     - `free` 1, or a price field that says free in words with no amount:
       "Actividad gratuita." (the price field's wording follows in brackets);
     - a price field with an amount: "Precio: 18 euros (no es gratis).", even
       when it also says "gratuito para menores";
     - otherwise "Precio no indicado."; and when the source's own description
       would read as free to 0227 although neither the flag nor the price says
       so, that text is withheld rather than let it tag the row.
   We never write "entrada libre" ourselves: 0227 reads it as free.

4. THE POINT IS THE CITY'S, CHECKED AGAINST ITS POSTAL CODE. location is the
   City's coordinate for its own venue, so coords_exact is True and the sync
   never re-geocodes it. A point outside the municipality's box is refused,
   and so is one that most other venues sharing its postal code place more
   than 5 km away (the Montreal lesson: a city's point can sit on the same
   street name in another district). Rows with no location (district-wide
   programmes, routes) are not written.

5. TWO SOURCES. Rows at a community place (`community_place_rx`: centros
   culturales and socioculturales, espacios de igualdad, municipal libraries,
   sports grounds, environmental classrooms, youth and seniors' centres) are
   `madrid:centros`, a civic timetable; the theatres, museums, Matadero and
   the parks are `madrid:agenda`, a city's cultural agenda of shows.

6. THE DOOR. `category_by_type` maps the City's type; two types are a
   programme, not a genre (`category_by_text_types`): the highlights
   (ProgramacionDestacadaAgendaCultura - JAZZMADRID sets, Cineteca films,
   circus, dance; all 161 rows were arts before) and the street-arts
   afternoons (ActividadesCalleArteUrbano - tribute bands, a face-painting
   stall, an orienteering race). A row whose title (or text, away from a
   cinema) names music is music, a sporting title is sports, an exhibition
   arts, the rest the type's own door (theater; community). Replay, after the
   sync's derive_categories: theater 484, learning 171, music 146, community
   89, kids 73, arts 5, sports 3, running 3, fitness 1; kids second on 185.

Identity is the City's event id + date + clock: a re-read is identical, and an
id published in both datasets is one item (the one with a clock wins when the
two disagree on a single date's time; two different titles under one id are
two items - seen once on 2026-10-03). One session published under two ids
(same venue, title, date and clock, or a title one typo apart) is one row
keyed on the lower id, with the longer text (17 on the pull). An all-day
item joins a timed one of the same title only when its text is empty or the
same text: 'Fiestas del Pilar 2026 en Salamanca' on 10 October is a 21:00
concert AND an all-day programme from 10:30, two rows.

CC BY 4.0 asks for attribution: it is the description's LAST paragraph and
under 200 characters, so the sync's _cap_prose keeps it (TAIL_KEEP_MAX).

Every request carries the MapseeAggregator UA and is paced to the host's
Crawl-delay (robots.txt's, when it asks more than the config); no redirect is
followed; robots.txt is asked first (above). A 401/403/429 or a bot challenge is never
retried and ends the run's reading; a 5xx or a timeout is retried twice.
--max-minutes (default 3) is a deadline no request starts after; one resource
failing never stops the other, and the store is saved at the end of every run.
"""
from __future__ import annotations

import argparse
import calendar
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("This script needs Python 3.9+ (zoneinfo).")

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_API = "https://datos.madrid.es/api/3/action/datastore_search"
# 1,766 and 1,403 rows on 2026-10-05: one request each (2.9 MB and 2.3 MB).
PAGE_LIMIT = 5000
# robots.txt: Crawl-delay: 10.
MIN_INTERVAL_S = 10.0
REQUEST_TIMEOUT_S = 60
DEFAULT_MAX_MINUTES = 3.0
_RETRYABLE = {408, 500, 502, 503, 504}
_REFUSALS = {401, 403, 429}
ATTRIBUTION_MAX = 200
# mapsee_supabase_sync.DESCRIPTION_MAX: past it _cap_prose trims the HEAD from
# its end. The source's own text gets only the room our lines leave.
DESCRIPTION_MAX = 800
SOURCE_TEXT_MIN = 60
# A recurrence longer than this is a season; shorter is a run of shows.
LONG_SPAN_DAYS = 14
# The City's every-day placeholder (L..D) with no clock over this many days or
# more (four calendar days) is a run, not a timetable (see _sessions).
PLACEHOLDER_SPAN_DAYS = 3
SITE_CHECK_KM = 5.0


class Refused(Exception):
    """The publisher said no (401/403/429, a challenge, robots.txt). Never retried."""


class OutOfTime(Exception):
    """The run deadline (--max-minutes) has passed: no request starts after it."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Reader:
    """A paced reader for the portal's CKAN datastore; counts its own requests.

    It reads nothing until `robots_gate` has asked robots.txt about the very
    request it would make. datos.madrid.es names this endpoint
    (`Disallow: /api/3/action/datastore_search`), so CKAN's documented-API
    exemption does not reach it: the read is the publisher's to allow, and
    waits for the config's `permission` (the module header)."""

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
                # No redirect is followed: the API answers in place, and a
                # redirect would be to a path whose robots.txt nobody checked.
                r = self.session.get(self.api, params=params, timeout=self.timeout, allow_redirects=False)
            except Exception as exc:  # noqa: BLE001 - timeouts, resets
                last = exc
            else:
                if r.status_code in _REFUSALS:
                    self.refused = f"HTTP {r.status_code} from {self.api}"
                    raise Refused(self.refused)
                if r.status_code == 200:
                    head = (getattr(r, "text", "") or "")[:4000]
                    if head.lstrip().startswith("<"):
                        from robots_txt import CHALLENGE_RX
                        if CHALLENGE_RX.search(head):
                            self.refused = f"a bot challenge from {self.api}"
                            raise Refused(self.refused)
                    try:
                        body = r.json()
                    except ValueError as exc:
                        last = exc
                    else:
                        if isinstance(body, dict) and body.get("success") is True and isinstance(body.get("result"), dict):
                            return body["result"]
                        raise RuntimeError(f"CKAN said no: {str((body or {}).get('error'))[:200]}")
                elif r.status_code not in _RETRYABLE:
                    raise RuntimeError(f"HTTP {r.status_code} from {self.api}")
                else:
                    last = RuntimeError(f"HTTP {r.status_code}")
            finally:
                self._last = self.clock()
            if attempt + 1 < self.tries:
                self.sleep(2 * (attempt + 1))
        raise last

    def robots_gate(self, probe: str, permission: str = "") -> str:
        """Ask robots.txt (one request, paced like the rest) about `probe`, the
        exact URL the first read would request. Allowed: go on. Disallowed by a
        file that answered: go on ONLY under `permission`, the owner's record of
        the publisher's written yes. A challenge, an unreachable file, or a
        Disallow with no permission is a refusal: nothing more is asked."""
        from robots_txt import Robots
        if self.deadline is not None and self.clock() >= self.deadline:
            raise OutOfTime("run deadline reached; no request starts after it")
        self.requests += 1
        try:
            got = Robots(self.session, timeout=self.timeout).check(probe)
        finally:
            self._last = self.clock()
        delay = got.get("crawl_delay")
        if delay:
            self.min_interval = max(self.min_interval, float(delay))
        if got.get("allowed") is True:
            return f"robots.txt allows {probe.split('?', 1)[0]}"
        if got.get("allowed") is False and got.get("status") == "ok" and permission:
            return f"robots.txt says '{got.get('rule')}'; read under the publisher's written permission: {permission}"
        self.refused = (f"robots.txt ({got.get('robots')}, {got.get('status')}) "
                        f"{('says ' + repr(got.get('rule'))) if got.get('rule') else 'gives no answer'} for {probe}")
        raise Refused(self.refused)

    def resource(self, resource_id: str, limit: int = PAGE_LIMIT) -> List[Dict[str, Any]]:
        """Every row, paged by offset in `_id` order; restarts once if `total`
        moves mid-read (the table is rebuilt daily)."""
        for _ in range(2):
            rows: List[Dict[str, Any]] = []
            total: Optional[int] = None
            moved = False
            while True:
                res = self.call({"resource_id": resource_id, "limit": limit, "offset": len(rows), "sort": "_id asc"})
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
                return rows
        raise RuntimeError(f"resource {resource_id} changed size twice while being read")


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------
def _s(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _fold(s: str) -> str:
    """Lower case without accents: 'inscripción' and 'inscripcion' alike."""
    s = unicodedata.normalize("NFKD", (s or "").replace("’", "'").replace("‘", "'"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _today(tz) -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests; else today in Madrid."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now(tz).date()


def _bump(stats: Dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def _day(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(_s(v)[:10])
    except ValueError:
        return None


def _clock(v: Any) -> Optional[int]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", _s(v))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def _type(item: Dict[str, Any]) -> str:
    return _s(item.get("@type")).rstrip("/").rsplit("/", 1)[-1]


def _pc(item: Dict[str, Any]) -> str:
    return _s(((item.get("address") or {}).get("area") or {}).get("postal-code"))


def _street(item: Dict[str, Any]) -> Optional[str]:
    """'CALLE PRINCIPE 25' -> 'Calle Principe 25' (the City writes capitals)."""
    raw = _s(((item.get("address") or {}).get("area") or {}).get("street-address"))
    if not raw:
        return None
    small = {"de", "del", "la", "las", "los", "el", "y", "a"}
    words = raw.lower().split(" ")
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


# The 21 districts as the City writes them; the CSV gives 'CHAMARTIN' and the
# JSON 'Chamartin', both without the accent.
DISTRICTS = ["Centro", "Arganzuela", "Retiro", "Salamanca", "Chamartín", "Tetuán", "Chamberí", "Fuencarral-El Pardo",
             "Moncloa-Aravaca", "Latina", "Carabanchel", "Usera", "Puente de Vallecas", "Moratalaz", "Ciudad Lineal",
             "Hortaleza", "Villaverde", "Villa de Vallecas", "Vicálvaro", "San Blas-Canillejas", "Barajas"]
_DISTRICT_BY_KEY = {re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", d).encode("ascii", "ignore").decode().lower()): d
                    for d in DISTRICTS}


def _district(item: Dict[str, Any]) -> str:
    """'.../Distrito/PuenteDeVallecas' (JSON) or '/Distrito/CHAMARTIN' (CSV) ->
    'Puente de Vallecas', 'Chamartín'; anything else (the CSV's bare
    'DISTRITO') -> ''."""
    d = _s(((item.get("address") or {}).get("district") or {}).get("@id"))
    if "/Distrito/" not in d:
        return ""
    return _DISTRICT_BY_KEY.get(_norm(d.rsplit("/", 1)[-1]), "")


# The audience tokens the City uses (2026-10-05: Niños 564, Familias 446,
# Mayores 93, Jovenes 84, Mujeres 83, PoblaciónGeneral 48, 2 each of the rest).
AUDIENCES = {"ninos": "niños", "familias": "familias", "mayores": "personas mayores", "jovenes": "jóvenes",
             "mujeres": "mujeres", "poblaciongeneral": "público general", "asociacionismo": "asociaciones",
             "propietariosdeanimales": "personas con animales"}


def audience_line(item: Dict[str, Any]) -> Optional[str]:
    names = []
    for tok in _s(item.get("audience")).split(","):
        key = _norm(tok)
        if key:
            name = AUDIENCES.get(key) or re.sub(r"(?<=[a-zñ])(?=[A-ZÑ])", " ", tok.strip()).lower()
            if name not in names:
                names.append(name)
    return f"Público: {', '.join(names)}." if names else None


def _point(item: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    loc = item.get("location") or {}
    try:
        lat, lon = float(loc.get("latitude")), float(loc.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or (lat == 0 and lon == 0):
        return None
    return lat, lon


WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
WEEKDAY_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos"]
_WD = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "sabados": 5,
       "domingo": 6, "domingos": 6}
_MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
           "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
MONTH_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
            "octubre", "noviembre", "diciembre"]
_MONTH_RX = r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|se(?:p)?tiembre|octubre|noviembre|diciembre)"
_WD_RX = r"(lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)"


def excluded_days(item: Dict[str, Any], stats: Dict[str, int]) -> Set[date]:
    out: Set[date] = set()
    for tok in _s(item.get("excluded-days")).split(";"):
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", tok)
        try:
            out.add(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except (AttributeError, ValueError):
            _bump(stats, "excluded-days tokens that are not a date (ignored)")
    return out


# ---------------------------------------------------------------------------
# the text's own dates
# ---------------------------------------------------------------------------
_RANGE = re.compile(rf"\b(?:del?|desde\s+el)\s+(\d{{1,2}})(?:\s+de\s+{_MONTH_RX})?\s+(?:al|hasta\s+el)\s+"
                    rf"(\d{{1,2}})\s+de\s+{_MONTH_RX}")
_DATE_LIST = re.compile(rf"((?:\b\d{{1,2}}\s*(?:,|y|e)\s*)*)\b(\d{{1,2}})\s+(?:de\s+)?{_MONTH_RX}\b"
                        rf"(?:\s+(?:de|del)\s+(20\d\d))?")
# "Jueves 1, 15 y 29 a las 11:30h" - a weekday-led list with no month: only
# read when the row's own span is one calendar month.
_WD_LIST = re.compile(rf"\b{_WD_RX}\s*,?\s*((?:\d{{1,2}}\s*(?:,|y)\s*)*\d{{1,2}})\b(?!\s*(?::|h\b|horas|de\s+{_MONTH_RX}))")
_ORD = {"primer": 1, "primero": 1, "segundo": 2, "tercer": 3, "tercero": 3, "cuarto": 4, "quinto": 5,
        "ultimo": -1}
_NTH = re.compile(r"\b((?:primer|primero|segundo|tercer|tercero|cuarto|quinto|ultimo)"
                  r"(?:\s*(?:,|y)\s*(?:el\s+)?(?:primer|primero|segundo|tercer|tercero|cuarto|quinto|ultimo))*)\s+"
                  rf"{_WD_RX}\s+de\s+(?:cada|todos\s+los)\s+mes(?:es)?\b")
_MONTHLY = re.compile(r"\bcada\s+mes\b|\bmensual(?:es|mente)?\b|\buna\s+vez\s+al\s+mes\b|"
                      rf"\b(?:un|una)\s+{_WD_RX}\s+al\s+mes\b|\bcada\s+(?:dos|tres|quince)\s+(?:semanas|dias)\b|"
                      r"\bquincenal(?:es|mente)?\b|\bsemanas\s+alternas\b")


# An exception clause names dates that are NOT the timetable: "salvo el 23 de
# octubre que será de 16 a 18:15" (other hours), "excepto el 12 de octubre",
# "no habrá sesión el 9 de noviembre" (no session). Read apart, never as a
# list of sessions.
_EXC_CLAUSE = re.compile(r"\b(?:salvo|excepto|excepcion(?:es)?|no\s+(?:habra|hay|se\s+realizara)\s+"
                         r"(?:sesion|sesiones|actividad|clase|clases|club|taller|proyeccion)|suspendid[oa])\b[^.;]{0,140}")


def _place(m: int, d: int, year: Optional[str], lo: date, hi: date) -> Optional[date]:
    years = [int(year)] if year else sorted({lo.year, hi.year})
    for y in years:
        try:
            got = date(y, m, d)
        except ValueError:
            continue
        if lo <= got <= hi:
            return got
    return None


def named_dates(text: str, lo: date, hi: date) -> Set[date]:
    """Dates the folded text names inside lo..hi; a 'del X al Y' range is a
    range, never two sessions."""
    t = _EXC_CLAUSE.sub(" ", _RANGE.sub(" ", text))
    out: Set[date] = set()
    for m in _DATE_LIST.finditer(t):
        month = _MONTHS[m.group(3).replace("setiembre", "septiembre")]
        for d in re.findall(r"\d{1,2}", m.group(1)) + [m.group(2)]:
            got = _place(month, int(d), m.group(4), lo, hi)
            if got:
                out.add(got)
    if (lo.year, lo.month) == (hi.year, hi.month):
        for m in _WD_LIST.finditer(t):
            wd = _WD[m.group(1)]
            for d in re.findall(r"\d{1,2}", m.group(2)):
                got = _place(lo.month, int(d), str(lo.year), lo, hi)
                if got and got.weekday() == wd:
                    out.add(got)
    return out


def nth_weekdays(text: str, lo: date, hi: date) -> Optional[Set[date]]:
    """'el tercer jueves de cada mes' -> those dates in lo..hi; None if unsaid."""
    m = _NTH.search(text)
    if not m:
        return None
    wd = _WD[m.group(2)]
    ords = {_ORD[o] for o in re.findall(r"primero|primer|segundo|tercero|tercer|cuarto|quinto|ultimo", m.group(1))}
    out: Set[date] = set()
    y, mo = lo.year, lo.month
    while (y, mo) <= (hi.year, hi.month):
        days = [d for d in range(1, calendar.monthrange(y, mo)[1] + 1) if date(y, mo, d).weekday() == wd]
        for o in ords:
            if -len(days) <= (o - 1 if o > 0 else o) < len(days):
                got = date(y, mo, days[o - 1 if o > 0 else o])
                if lo <= got <= hi:
                    out.add(got)
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out


_HOUR = r"(\d{1,2})(?:[:.](\d{2}))?"
_STATED_SPAN = re.compile(rf"\bde\s+(?:las\s+)?{_HOUR}\s*(?:h\.?|horas)?\s+a\s+(?:las\s+)?{_HOUR}\s*(?:h\b|horas|hrs?\b)|"
                          rf"\b(\d{{1,2}})[:.](\d{{2}})\s*(?:h\s*)?(?:-|–|a)\s*(\d{{1,2}})[:.](\d{{2}})")


def stated_end(text: str, start: int) -> Optional[int]:
    """The end of the ONE stated span that starts at this clock ('de 17:15 a
    18:15 h'), or None when the text gives none, or several."""
    ends: Set[int] = set()
    for m in _STATED_SPAN.finditer(text):
        g = m.groups()
        a = (g[0], g[1], g[2], g[3]) if g[0] is not None else (g[4], g[5], g[6], g[7])
        h1, m1, h2, m2 = int(a[0]), int(a[1] or 0), int(a[2]), int(a[3] or 0)
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
            continue
        if h1 * 60 + m1 == start and start < h2 * 60 + m2 <= 24 * 60:
            ends.add(h2 * 60 + m2)
    return ends.pop() if len(ends) == 1 else None


# "Viernes 16 de octubre, a las 19:00 horas, charla ... Sábado 17 de octubre a
# las 10.00 horas: comenzamos ..." ('Dibuja Madrid', 50434813, HORA 19:00): a
# named date followed by its own clock.
_DATED_CLOCK = re.compile(rf"\b(\d{{1,2}})\s+de\s+{_MONTH_RX}(?:\s+de\s+(\d{{4}}))?\s*,?\s*(?:a|desde)\s+las\s+"
                          rf"(\d{{1,2}})(?:[:.](\d{{2}}))?(?=\s*(?:h\b|horas\b|hrs?\b))")


def dated_clocks(text: str, lo: date, hi: date) -> Dict[date, int]:
    """{date: clock} for each date the folded text gives exactly one clock."""
    seen: Dict[date, Set[int]] = {}
    for m in _DATED_CLOCK.finditer(text):
        h, mi = int(m.group(4)), int(m.group(5) or 0)
        got = _place(_MONTHS[m.group(2).replace("setiembre", "septiembre")], int(m.group(1)), m.group(3), lo, hi)
        if got and h < 24 and mi < 60:
            seen.setdefault(got, set()).add(h * 60 + mi)
    return {d: c.pop() for d, c in seen.items() if len(c) == 1}


# "Todas las sesiones serán de 17:30 a 19 horas, salvo el 23 de octubre y el 20
# de noviembre que serán de 16 a 18:15 horas" (a reading club, 2026-10-05).
_EXCEPT = re.compile(r"\b(?:salvo|excepto|menos)\s+(?:el|los)?\s*(?:dias?\s+)?(.{1,80}?)\s*,?\s*(?:que|cuando)\s+"
                     rf"(?:sera|seran|sera\s+el\s+horario)\s+de\s+(?:las\s+)?{_HOUR}\s*(?:h\.?|horas)?\s+a\s+(?:las\s+)?{_HOUR}")


def closed_dates(text: str, lo: date, hi: date) -> Set[date]:
    """Dates an exception clause names without giving them other hours."""
    out: Set[date] = set()
    for m in _EXC_CLAUSE.finditer(text):
        out |= named_dates(_strip_cue(m.group(0)), lo, hi)
    return out - set(exception_spans(text, lo, hi))


def _strip_cue(clause: str) -> str:
    # named_dates strips exception clauses itself, so read the clause's dates
    # without its leading cue word.
    return re.sub(r"^\S+", " ", clause)


def exception_spans(text: str, lo: date, hi: date) -> Dict[date, Tuple[int, int]]:
    """{date: (start, end)} for the dates a 'salvo el X que será de A a B'
    clause gives other hours."""
    out: Dict[date, Tuple[int, int]] = {}
    for m in _EXCEPT.finditer(text):
        h1, m1, h2, m2 = int(m.group(2)), int(m.group(3) or 0), int(m.group(4)), int(m.group(5) or 0)
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59 or h2 * 60 + m2 <= h1 * 60 + m1:
            continue
        for d in named_dates(m.group(1), lo, hi):
            out[d] = (h1 * 60 + m1, h2 * 60 + m2)
    return out


# ---------------------------------------------------------------------------
# who may come: the verdict
# ---------------------------------------------------------------------------
_ONLINE = re.compile(r"\b(?:online|on-line|en\s+linea|webinar(?:io)?s?|streaming|videoconferencia|seminario\s+digital)\b")
_ONLINE_TEXT = re.compile(r"\b(?:actividad|taller|sesion|sesiones|club|curso|charla|conferencia|encuentro)"
                          r"(?:\s+\w+){0,3}\s+(?:online|en\s+linea|virtual(?:es)?)\b|"
                          r"\b(?:a\s+traves\s+de|por|via)\s+(?:zoom|teams|google\s+meet|jitsi|videoconferencia)\b")
_CLOSED = re.compile(r"\b(?:cerrad[oa]s?|cierre|suspendid[oa]s?|cancelad[oa]s?|aplazad[oa]s?|anulad[oa]s?)\b")
_GOVERNANCE = re.compile(r"\b(?:pleno|plenos|junta\s+municipal\s+de\s+distrito|consejo\s+(?:de\s+proximidad|"
                         r"territorial|sectorial)|foro\s+local|audiencia\s+publica|mesa\s+de\s+trabajo)\b")
_RESTRICTED = re.compile(r"\b(?:solo|solamente|unicamente|exclusiv\w*)\s+(?:para\s+|a\s+)?(?:los\s+|las\s+)?"
                         r"(?:socios|socias|abonad|empadronad|usuari|alumn|grupos|centros\s+educativos|colegios)|"
                         r"\b(?:dirigid[oa]s?|destinad[oa]s?)\s+(?:a|para)\s+(?:los\s+|las\s+)?(?:centros\s+educativos|"
                         r"colegios|grupos\s+escolares|alumnado|docentes|profesorado|profesionales)\b|"
                         r"\bvisitas?\s+(?:escolares|para\s+grupos)\b")
# A title that puts the act in a square or street the venue is not: "Acto
# comunitario en Plaza Prosperidad" (50425452) is pinned at the Espacio de
# Igualdad Nieves Torres, 1.9 km away. 1 item on the 2026-10-05 pull.
_TITLE_PLACE = re.compile(r"\ben\s+(?:el\s+|la\s+)?(?:plaza|parque|calle|glorieta|paseo|jardin(?:es)?|avenida|bulevar|"
                          r"explanada)\s+(?:de\s+(?:la\s+|los\s+|las\s+)?|del\s+)?([a-z0-9]+)")
_APPOINTMENT = re.compile(r"\bcita\s+previa\b|\bprevia\s+cita\b")
_APPLICATION = re.compile(r"\bconvocatoria\b|\bsolicitudes\b|\bseleccion\s+de\s+(?:participantes|proyectos)\b|"
                          r"\bplazo\s+de\s+(?:presentacion|solicitud)\b")
_NO_REG = re.compile(r"\bsin\s+(?:necesidad\s+de\s+)?(?:inscripcion|reserva|inscribirse)\b|"
                     r"\bno\s+(?:es|sera|hace)\s+(?:necesari[oa]|precis[oa]|falta)\s+(?:la\s+|realizar\s+)?"
                     r"(?:inscripcion|reserva|inscribirse)|\bno\s+requiere\s+(?:inscripcion|reserva)\b|"
                     r"\bde\s+caracter\s+abierto\b|\bno\s+se\s+necesita\s+(?:inscripcion|reserva)\b")
# Any inscription word that no "sin / no" governs: "las inscripciones a las
# actividades se ...", "te puedes inscribir", "inscripción previa". A seat for
# a show ("previa reserva de entradas") is a ticket, not a registration.
_REG = re.compile(r"\binscrip\w*|\binscrib\w*|\bapunt(?:arse|ate|aros)\b|\bmatricula\b|"
                  r"\b(?:imprescindible|necesari[oa]|obligatori[oa])\s+(?:la\s+)?reserva\b|\brequiere\s+reserva\b|"
                  r"\breserva\s+(?:previa|de\s+plaza|obligatoria|necesaria)\b|\bprevia\s+reserva\b")
_TICKET_AFTER = re.compile(r"^\W*(?:de\s+)?(?:entradas?|localidad|invitaci)")
# The "sin / no" must govern the match from inside its own sentence: the
# window stops at a sentence end and at the line between title, description
# and price. "¿Y si el futuro no da miedo?" (a title) is not a "no" to the
# price field's "Gratuita con inscripción previa" (50382480, 2026-10-05).
_NEGATED = re.compile(r"\b(?:sin|no|ni)\b[^.;:?!¿¡\n]{0,25}$")
_COURSE = re.compile(r"\bcursos?\b|\b(?:\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|doce)\s+sesiones\b|"
                     r"\btrimestral(?:es)?\b|\bnivel\s+(?:a1|a2|b1|b2|c1|basico|inicial|iniciacion|medio|"
                     r"intermedio|avanzado)\b")
_OPEN = re.compile(r"\bentrada\s+libre\b|\bacceso\s+libre\b|\bhasta\s+completar\s+(?:el\s+)?aforo\b|"
                   r"\babiert[oa]s?\s+a\s+tod[oa]s\b|\bparticipacion\s+libre\b")
# Types whose rows mostly need a place booked: on the 2026-10-03 pull, of the
# rows whose text says how to join, registration in 25 of 38 Cursos y talleres,
# 35 of 38 guided visits (Excursiones), 25 of 28 nature walks (Itinerarios) and
# 5 of 7 reading clubs. A row of these types that says nothing is not written.
WORKSHOP_TYPES = {"CursosTalleres", "Idiomas", "CapacitacionDigital", "ExcursionesItinerariosVisitas",
                  "ItinerariosOtrasActividadesAmbientales", "ClubesLectura"}
# ...and a title that names a workshop or course, whatever its type: "Taller de
# iniciación a castañuelas. Turno de mañana" (Música, every Thursday to June),
# "Curso anual de cómic" (Recitales, 140 euros a term). 24 such rows were kept
# as plain agenda events on the 2026-10-03 pull. A fiesta's children's
# workshops and an exhibition of a workshop's work are not courses.
# A club ("Club de Teatro Vargas Llosa", weekly for 36 weeks with no text) and a
# guided visit ("Visitas dialogadas a Matadero", "Visita guiada a la Ermita")
# are joined, not dropped into, like the reading clubs and visits above.
# "Carabanchel Jazz Club" (a concert) and "Cine-club" are not "club de".
_WORKSHOP_TITLE = re.compile(r"\btaller(?:es)?\b|\bcursos?\b|\bclases?\s+de\b|\bmasterclass\b|\bseminario\b|"
                             r"\blaboratorio\b|\bclub\s+de\b|\bvisitas?\s+(?:guiadas?|dialogadas?|comentadas?|"
                             r"teatralizadas?)\b|\bvisita\s+al?\b")
# ...or a text that calls itself one: "Workshop intensivo y práctico dirigido a
# ... profesionales" (La Gramática de la Luz, 50421551; its public showing is
# its own item, 50421555, and is written).
_WORKSHOP_TEXT = re.compile(r"\bworkshops?\s+(?:intensiv|practic)\w*|\btaller(?:es)?\s+intensiv\w*")
# A programme whose sign-up lives on another site, whatever its rows say: the
# Villaverde district's "Deporte en la Calle" (16 items, 185 rows a quarter,
# every one with an EMPTY description, typed as plain sport) takes sign-ups
# for each activity, day and site through a web form ("Las inscripciones son
# gratuitas y se realiza por internet", inscripciones.pebetero.com; the
# district's own announcement for the 2026-27 season). Also any text that
# names such a form.
_SIGNUP = re.compile(r"\bdeporte\s+en\s+la\s+calle\b|\binscripciones\.[a-z0-9-]+\.[a-z]{2,}\b|\bpebetero\b")
# The Espacios de Igualdad publish workshop series and support groups with no
# text at all (73 of their 75 items in the window on 2026-10-05), typed as
# anything from LGTBI to sport ("Teatro Feminista", ActividadesDeportivas), and
# take sign-ups by phone or e-mail. A silent row there is a workshop; their
# public commemorations ("Concentración mensual", ComemoracionesHomenajes) are
# acts anyone attends.
_SILENT_IS_WORKSHOP_VENUE = re.compile(r"^espacio de igualdad\b")
# A long weekly season with an empty description says nothing about how to
# join. Kept only where an audience watches or listens - a screening, a show,
# a concert, a talk - which is how the City's own cine-clubs and tertulias
# run; refused otherwise ("Tertulias en latín", "Grupo de crianza").
AUDIENCE_TYPES = {"CineActividadesAudiovisuales", "CineFiccion", "TeatroPerformance", "DanzaBaile", "CircoMagia",
                  "ComediaMonologo", "Zarzuela", "CuentacuentosTiteresMarionetas", "Musica", "JazzSoulFunkySwingReagge",
                  "CoroGospel", "CantautorFolkCountry", "FolcloreEtnica", "LatinaEspanola", "Clasica", "Flamenco",
                  "SalonTango", "RockPop", "ConferenciasColoquios", "RecitalesPresentacionesActosLiterarios",
                  "ProgramacionDestacadaAgendaCultura"}


def _registration(text: str) -> bool:
    for m in _REG.finditer(text):
        if _NEGATED.search(text[max(0, m.start() - 40):m.start()]):
            continue
        if "reserva" in m.group(0) and _TICKET_AFTER.search(text[m.end():m.end() + 30]):
            continue
        return True
    return False


def verdict(item: Dict[str, Any]) -> Tuple[bool, str]:
    title = _fold(_s(item.get("title")))
    desc = _fold(_s(item.get("description")))
    price = _fold(_s(item.get("price")))
    text = f"{title}\n{desc}\n{price}"
    typ = _type(item)
    if typ == "EnLinea" or _ONLINE.search(title) or _ONLINE_TEXT.search(text):
        return False, "online, not at the venue"
    if _CLOSED.search(title):
        return False, "a closure or cancellation notice"
    if _GOVERNANCE.search(title):
        return False, "a governance meeting"
    if _RESTRICTED.search(text):
        return False, "for members, registered residents, pupils or professionals only"
    if _APPOINTMENT.search(text):
        return False, "by appointment (cita previa)"
    place = _TITLE_PLACE.search(title)
    if place and place.group(1) not in _fold(f"{_s(item.get('event-location'))} {_street(item) or ''}"):
        return False, "the title places it in a square or street that is not its venue"
    if _APPLICATION.search(text):
        return False, "an application call, not a session"
    if _NO_REG.search(text):
        return True, "the source says no registration is needed"
    if _SIGNUP.search(text):
        return False, "a programme that signs people up on another site (Deporte en la Calle)"
    if _registration(text):
        return False, "registration required (inscripción previa)"
    silent = not desc.strip()
    venue = _fold(_s(item.get("event-location")))
    is_workshop = (typ in WORKSHOP_TYPES
                   or ((bool(_WORKSHOP_TITLE.search(title)) or bool(_WORKSHOP_TEXT.search(desc)))
                       and typ != "Fiestas" and not is_exhibition(item))
                   or (silent and bool(_SILENT_IS_WORKSHOP_VENUE.search(venue)) and typ != "ComemoracionesHomenajes"))
    if is_workshop and _COURSE.search(text) and not _OPEN.search(text):
        return False, "a course (sessions you join, not drop in)"
    if _OPEN.search(text):
        return True, "open admission (entrada libre / hasta completar aforo)"
    if is_workshop:
        return False, "a workshop, visit or club with no word on how to join"
    lo, hi = _day(item.get("dtstart")), _day(item.get("dtend"))
    if (silent and item.get("recurrence") and lo is not None and hi is not None
            and (hi - lo).days > LONG_SPAN_DAYS and typ not in AUDIENCE_TYPES):
        return False, "a season of weekly sessions with no text at all (how to join is not said)"
    return True, "an event in the city's agenda"


# ---------------------------------------------------------------------------
# the price
# ---------------------------------------------------------------------------
_MONEY = re.compile(r"\d+(?:[.,]\d+)?\s*(?:euros?|eur\b|€)|€\s*\d", re.I)
_FREE_WORDS = re.compile(r"\bgratuit[oa]s?\b|\bgratis\b|\bentrada\s+libre\b|\bacceso\s+libre\b")
# A SUBSET of ../mapsee/tools/measure_deals.py's FREE (0227) - the Spanish and
# English alternatives a Madrid row can hit - and its FREE_NEG veto.
TAGGED_FREE = re.compile(
    r"\bgratuit(?:o|a|os|as|e|s|es)?\b|\bgratis\b|\b(?:entrada|acceso|ingreso)\s+(?:libre|gratuit[ao]|gratis)\b|"
    r"\bentr[ée]e\s+(?:libre|gratuite)\b|(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+"
    r"(?:attend|join|enter|participate|the\s+public|all|everyone)|for\s+(?:all|everyone|kids|children|the\s+"
    r"public|families))\b|\b(?:admission|entry|entrance|cost|price)\s*(?:is|:|-|–)?\s*free\b|\bis\s+free\b", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b|\bnon[- ]gratuit|\bpas\s+gratuit|"
                      r"\bno\s+es\s+gratis", re.I)
PRICE_TEXT_MAX = 160


def price_line(item: Dict[str, Any]) -> Tuple[str, str]:
    """(tier, sentence): free / fee / stated / unknown."""
    price = _s(item.get("price"))
    if len(price) > PRICE_TEXT_MAX:
        price = price[:PRICE_TEXT_MAX].rsplit(" ", 1)[0] + " …"
    fp = _fold(price)
    if _MONEY.search(price):
        # An amount wins over any free word in the same field ("gratuito para
        # menores; resto 5 euros"): the veto keeps 0227 from calling it free.
        return "fee", f"Precio: {price.rstrip('. ')} (no es gratis)."
    try:
        flag = int(item.get("free") or 0)
    except (TypeError, ValueError):
        flag = 0
    if flag == 1 or _FREE_WORDS.search(fp):
        extra = price.rstrip(". ")
        return "free", "Actividad gratuita" + (f" ({extra})." if extra else ".")
    if price:
        return "stated", f"Precio: {price.rstrip('. ')}."
    return "unknown", "Precio no indicado."


# ---------------------------------------------------------------------------
# where: the City's point, checked against its own postal code
# ---------------------------------------------------------------------------
def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def contradicted_points(items: Iterable[Dict[str, Any]], radius_km: float = SITE_CHECK_KM
                        ) -> Tuple[Dict[Tuple[str, Tuple[float, float]], float], Set[Tuple[str, Tuple[float, float]]]]:
    """({(postal, point): median km to its peers} for points that strictly
    more same-postal-code venues place beyond radius_km than within it,
    {(postal, point)} with no peer to check against)."""
    by_pc: Dict[str, Set[Tuple[float, float]]] = {}
    for it in items:
        p = _point(it)
        if p is not None:
            by_pc.setdefault(_pc(it), set()).add((round(p[0], 6), round(p[1], 6)))
    bad: Dict[Tuple[str, Tuple[float, float]], float] = {}
    alone: Set[Tuple[str, Tuple[float, float]]] = set()
    for pc, pts in by_pc.items():
        for p in pts:
            peers = [q for q in pts if q != p]
            if not pc or not peers:
                alone.add((pc, p))
                continue
            ds = sorted(_km(p, q) for q in peers)
            far = sum(1 for d in ds if d > radius_km)
            if far > len(ds) - far:
                bad[(pc, p)] = ds[len(ds) // 2]
    return bad, alone


def _key_point(item: Dict[str, Any]) -> Tuple[str, Tuple[float, float]]:
    p = _point(item)
    return _pc(item), (round(p[0], 6), round(p[1], 6))


# ---------------------------------------------------------------------------
# when: the sessions of one item
# ---------------------------------------------------------------------------
# "Javier Vercher Cuarteto ... 19.00 h": a one-day row with an empty `time`
# whose text states exactly one clock (4 of 32 such rows on 2026-10-03).
_TEXT_CLOCK = re.compile(r"(?<![\d.:])(\d{1,2})[:.](\d{2})\s*(?:h\b|horas\b)")


def is_exhibition(item: Dict[str, Any]) -> bool:
    return _type(item) == "Exposiciones" or _fold(_s(item.get("title"))).startswith(("exposicion", "muestra"))


def sessions(item: Dict[str, Any], today: date, horizon: date, skip: Set[date],
             stats: Dict[str, int], write_exhibitions: bool = True) -> Tuple[List[date], Optional[int], str]:
    """(dates, clock or None for all-day, shape) - or ([], None, refusal).
    A date an exception clause closes ("excepto el 12 de octubre", "no habrá
    sesión el 9 de noviembre") is never written."""
    days, clock, shape = _sessions(item, today, horizon, skip, stats, write_exhibitions)
    lo, hi = _day(item.get("dtstart")), _day(item.get("dtend"))
    if days and lo is not None and hi is not None and lo != hi:
        shut = closed_dates(_fold(_s(item.get("description"))), lo, hi)
        if shut & set(days):
            _bump(stats, "dates not written: the text says no session", len(shut & set(days)))
            days = [d for d in days if d not in shut]
    return days, clock, shape


def _sessions(item: Dict[str, Any], today: date, horizon: date, skip: Set[date],
              stats: Dict[str, int], write_exhibitions: bool = True) -> Tuple[List[date], Optional[int], str]:
    lo, hi = _day(item.get("dtstart")), _day(item.get("dtend"))
    if lo is None or hi is None or hi < lo:
        return [], None, "refused: no usable dates"
    text = _fold(f"{_s(item.get('title'))}\n{_s(item.get('description'))}")
    clock = _clock(item.get("time"))
    if clock is not None and re.search(r"\bhora(?:rio)?\s+(?:por|a)\s+(?:confirmar|determinar)\b", text):
        _bump(stats, "clock dropped: the text says the time is to be confirmed")
        clock = None
    first, last = max(lo, today), min(hi, horizon)
    if lo == hi:
        if clock is None:
            said = set()
            for m in _STATED_SPAN.finditer(text):      # "de 17:00 a 19:00 h" starts at 17:00
                g = m.groups()
                said.add((int(g[0] if g[0] is not None else g[4]), int((g[1] if g[0] is not None else g[5]) or 0)))
            said |= {(int(h), int(mi)) for h, mi in _TEXT_CLOCK.findall(_STATED_SPAN.sub(" ", text))}
            said = {x for x in said if x[0] < 24 and x[1] < 60}
            if len(said) == 1:
                h, m = said.pop()
                clock = h * 60 + m
                _bump(stats, "clock from the text (one stated time on a one-day row with none)")
        return ([lo] if today <= lo <= horizon else []), clock, "single day"
    rec = item.get("recurrence") or {}
    if not rec:
        return [], None, "refused: several days with no recurrence (which days is not said)"
    if _s(rec.get("frequency")).upper() != "WEEKLY":
        return [], None, f"refused: a {_s(rec.get('frequency')).lower() or 'blank'} recurrence"
    try:
        interval = max(1, int(rec.get("interval") or 1))
    except (TypeError, ValueError):
        interval = 1
    wds = {WEEKDAY_CODES[c] for c in _s(rec.get("days")).upper().replace(" ", "").split(",") if c in WEEKDAY_CODES}
    if not wds:
        return [], None, "refused: a recurrence with no weekdays"
    gone = excluded_days(item, stats)
    week0 = lo - timedelta(days=lo.weekday())
    days: List[date] = []
    d = lo
    while d <= hi:
        if d.weekday() in wds and ((d - week0).days // 7) % interval == 0 and d not in gone:
            days.append(d)
        d += timedelta(days=1)
    long = (hi - lo).days > LONG_SPAN_DAYS
    if is_exhibition(item):
        if not write_exhibitions:
            return [], None, "refused: an exhibition (write_exhibitions is false in the config)"
        return [x for x in days if first <= x <= last], None, "exhibition: one all-day row per open day"
    named = named_dates(text, lo, hi)
    if named:
        on = sorted(x for x in named if x.weekday() in wds and x not in gone)
        if not on:
            return [], None, "refused: the text's dates fall on none of the recurrence's days"
        # A real weekly timetable (not the City's every-day placeholder) whose
        # dates are for SOME of its weekdays, while the text gives the others a
        # timetable of their own: "Sábados de cine de otoño" (WE,SA,SU) names
        # 21 and 28 October for a Wednesday workshop, and "los sábados ... a las
        # 19 horas", "los domingos a las 12 horas" for the screenings. Writing
        # the two Wednesdays under the series' title drops every screening.
        dated = {x.weekday() for x in on}
        plain = {_WD[w] for w in re.findall(rf"\blos\s+{_WD_RX}", text)}
        if len(wds) < 7 and (wds - dated) & plain:
            return [], None, "refused: the text's dates are for some weekdays, and the others have their own timetable"
        return [x for x in on if first <= x <= last], clock, "the dates the text names"
    nth = nth_weekdays(text, lo, hi)
    if nth is not None:
        return sorted(x for x in nth if first <= x <= last and x not in gone and x not in skip), clock, \
            "an nth weekday of each month, as the text says"
    if clock is None and len(wds) == 7 and (hi - lo).days >= PLACEHOLDER_SPAN_DAYS:
        # Every day, no clock, no dates named: the City's placeholder for a
        # run whose days are not given. 'Andrea Jiménez. Contra Antígona' (26
        # Nov - 5 Dec, a theatre dark on Mondays) and 'XXXII Grandes del góspel'
        # were ten all-day rows each, pulsing 'happening now' all day; on the
        # 2026-10-05 pull 6 such items held 44 of the 60 all-day rows from
        # 7-day recurrences, 38 of the 44 with no text at all.
        return [], None, "refused: every day of a run, no clock and no dates (the City's placeholder)"
    if long and _MONTHLY.search(text):
        return [], None, "refused: monthly or fortnightly, and its dates are not given"
    if long and clock is None:
        return [], None, "refused: a period with no clock, not a timetable"
    if long and len(wds) >= 3:
        return [], None, "refused: one clock on 3+ days a week for weeks - a period, not a timetable"
    out = []
    for x in days:
        if not (first <= x <= last):
            continue
        if long and x in skip:
            _bump(stats, "dates not written: public holiday")
            continue
        out.append(x)
    return out, clock, ("weekly sessions" if long else "a short run of days")


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
_GLUED = re.compile(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ])")


def clean_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """The City escapes some text twice ('Akiba Art &amp;amp; African'), and
    glues the price field's two lines ('5 eurosDescuentos disponibles')."""
    out = dict(item)
    for k in ("title", "description", "price", "event-location"):
        v = _s(out.get(k))
        for _ in range(3):
            u = html.unescape(v)
            if u == v:
                break
            v = u
        out[k] = v
    out["price"] = _GLUED.sub(". ", out["price"])
    return out


def merge_files(files: List[List[Dict[str, Any]]], stats: Dict[str, int]) -> List[Dict[str, Any]]:
    """One item per (id, title): the two files share most ids. When they
    disagree about one date's clock, the one with a clock wins; two different
    titles under one id are two items."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for items in files:
        for it in items:
            if not isinstance(it, dict):
                continue
            it = clean_item(it)
            iid = _s(it.get("id"))
            if not iid:
                _bump(stats, "items with no id (skipped)")
                continue
            key = (iid, _norm(_s(it.get("title")).replace("'", "")))
            have = out.get(key)
            if have is None:
                if any(k[0] == iid for k in out):
                    _bump(stats, "an id carrying two different titles (both kept)")
                out[key] = it
                continue
            _bump(stats, "items in both files (merged)")
            if (_clock(have.get("time")) is None and _clock(it.get("time")) is not None
                    and _s(have.get("dtstart"))[:10] == _s(it.get("dtstart"))[:10]):
                out[key] = it
                _bump(stats, "  of which the second file's clock was used")
    return list(out.values())


# "ProgramacionDestacadaAgendaCultura" is the City's highlights, not a genre:
# on 2026-10-05 its 163 rows were JAZZMADRID sets, a gospel festival, circus
# galas, plays, dance and the Cineteca's films. A row is music when its title
# names music, or its text does at a venue that is not a cinema (a Cineteca
# documentary about a cantautor is a film); otherwise it is a show or a film,
# which this repo files under theater.
_MUSIC = re.compile(r"\bconciertos?\b|\bjazz\w*|\bgospel\b|\bflamenc\w*|\bdj\b|\btrio\b|\bcuarteto\b|\bquartet\b|"
                    r"\bquinteto\b|\bsexteto\b|\borquesta\b|\bcoros?\b|\bsinfonic\w*|\bcantautor\w*|\brecital\b|"
                    r"\bopera\b|\bzarzuela\b|\bescolania\b|\bmusica\s+en\s+directo\b|\btributo\s+a\b|\bboleros?\b|"
                    r"\btangos?\b|\bfolclore\b|\bguateque\b|\bkanautor\b")
_CINEMA = re.compile(r"\bcineteca\b|^cines?\b")
_SPORT = re.compile(r"\bcarreras?\b|\bdeportiv[oa]s?\b|\btorneo\b|\bmarcha\s+(?:popular|a\s+pie)\b")


def category(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    """`category_by_text_types` maps a type that is not a genre to the door
    its rows take when their words name no music: the highlights are shows
    and films (theater), the street-arts programme is a neighbourhood's
    afternoon (community) - "Tributo a Hombres G", "Boleros y Tangos" and
    "Grupo Flamenco" there are music, "Carrera de Orientación Deportiva" is
    sport, not art."""
    by_type = cfg.get("category_by_type") or {}
    typ = _type(item)
    primary = by_type.get(typ) or cfg.get("category_default") or "community"
    by_text = cfg.get("category_by_text_types") or {}
    if typ in by_text:
        title, desc = _fold(_s(item.get("title"))), _fold(_s(item.get("description")))
        at_cinema = bool(_CINEMA.search(_fold(_s(item.get("event-location")))))
        if is_exhibition(item):
            primary = "arts"
        elif _MUSIC.search(title) or (not at_cinema and _MUSIC.search(desc)):
            primary = "music"
        elif _SPORT.search(title):
            primary = "sports"
        else:
            primary = (by_text.get(typ) if isinstance(by_text, dict) else None) or "theater"
    extras: List[str] = []
    aud = _fold(_s(item.get("audience")))
    if re.search(r"\bninos\b|\bfamilias\b", aud) and primary != "kids":
        extras.append("kids")
    return primary, extras


_TITLE_STOP = {"de", "del", "la", "las", "el", "los", "en", "y", "a", "al"}


def _title_key(title: str) -> str:
    """'Cía. Juan Berlanga' and 'Compañía Juan Berlanga', 'Empaque - Chamartín'
    and 'Empaque en Chamartín' alike."""
    t = re.sub(r"\bcia\b\.?", "compania", _fold(title))
    return "".join(w for w in re.findall(r"[a-z0-9]+", t) if w not in _TITLE_STOP)


def _near(a: str, b: str) -> bool:
    """Equal, or one typo apart in a key of 8+ characters ('Led Silhouette',
    'Led Silhoutte')."""
    if a == b:
        return True
    if min(len(a), len(b)) < 8 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def _text_key(members: list) -> str:
    return "".join(re.findall(r"[a-z0-9]+", _fold(max((_s(m[0].get("description")) for m in members), key=len))))


def _same_text(a: list, b: list) -> bool:
    """Empty on one side, or one text holds the other ('Programa: Casi
    septiembre ...' and 'Festival de Cortometrajes ... Programa: Casi
    septiembre ...')."""
    ta, tb = _text_key(a), _text_key(b)
    return not ta or not tb or ta in tb or tb in ta


def fold_twins(groups: Dict[Tuple[str, str, str, str], list], stats: Dict[str, int]) -> None:
    """One show published under two ids whose titles differ by a typo or a
    'Cía.', or once with its clock and once as an all-day row, is one row
    (live 2026-10-05: 'Led Silhouette' and 'Led Silhoutte' at 20:00, 'Paula
    Comitre' at 20:00 and all-day, the Cineteca's 'Germinal - Sesión I' at
    19:00 and all-day). An all-day twin joins the timed session only when its
    text is empty or the same text: 'Fiestas del Pilar 2026 en Salamanca' on
    10 October is a 21:00 concert (50438944) AND an all-day programme from
    10:30 (50438921), two rows - folded, the 21:00 row carried the morning's
    programme. Two different clocks stay two sessions. The lowest id still
    names the row."""
    by_vd: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = {}
    for k in sorted(groups):
        by_vd.setdefault((k[0], k[2]), []).append(k)
    for keys in by_vd.values():
        if len(keys) < 2:
            continue
        clusters: List[List[Tuple[str, str, str, str]]] = []
        for k in keys:
            tk = _title_key(_s(groups[k][0][0].get("title")))
            for cl in clusters:
                if any(_near(tk, _title_key(_s(groups[o][0][0].get("title")))) for o in cl):
                    cl.append(k)
                    break
            else:
                clusters.append([k])
        for cl in clusters:
            if len(cl) < 2:
                continue
            timed = sorted((k for k in cl if k[3] != "dia"), key=lambda k: k[3])
            by_clock: Dict[str, Tuple[str, str, str, str]] = {}
            for k in sorted(cl, key=lambda k: (k[3] == "dia", k)):
                into = by_clock.get(k[3]) or (by_clock.get(timed[0][3]) if k[3] == "dia" and timed else None)
                if into is not None and k[3] == "dia" and into[3] != "dia" and not _same_text(groups[k], groups[into]):
                    _bump(stats, "twins NOT folded: an all-day item and a timed one with different texts")
                    into = None
                if into is None:
                    by_clock[k[3]] = k
                    continue
                groups[into].extend(groups.pop(k))
                _bump(stats, "twins folded: an all-day copy of a timed session" if k[3] == "dia" and into[3] != "dia"
                      else "twins folded: one session under two spellings of its title")


def _placeholder(item: Dict[str, Any]) -> bool:
    lo, hi = _day(item.get("dtstart")), _day(item.get("dtend"))
    days = {c for c in _s((item.get("recurrence") or {}).get("days")).upper().replace(" ", "").split(",")
            if c in WEEKDAY_CODES}
    return lo != hi and len(days) == 7 and _clock(item.get("time")) is None


def drop_untimed_copies(groups: Dict[Tuple[str, str, str, str], list], stats: Dict[str, int]) -> None:
    """An all-day row from the every-day placeholder, for a show another id
    times at the same venue, is a day the timed item does not confirm:
    'Compañía Juan Berlanga' (50377292, every day 4-5 Dec, no clock) left an
    all-day Friday 4 Dec beside 'Cía. Juan Berlanga' (50382740), which plays
    Saturday 5 Dec at 20:00 only. Dropped."""
    timed: Dict[str, List[str]] = {}
    for k, members in groups.items():
        if k[3] != "dia":
            timed.setdefault(k[0], []).append(_title_key(_s(members[0][0].get("title"))))
    for k in [k for k in groups if k[3] == "dia"]:
        members = groups[k]
        if not all(_placeholder(m[0]) for m in members):
            continue
        tk = _title_key(_s(members[0][0].get("title")))
        if any(_near(tk, other) for other in timed.get(k[0], [])):
            del groups[k]
            _bump(stats, "all-day placeholder days dropped: another id times the same show")


def _date_es(d: date) -> str:
    return f"{d.day} de {MONTH_ES[d.month]} de {d.year}"


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def events(items: List[Dict[str, Any]], cfg: Dict[str, Any], tz, today: date,
           stats: Dict[str, int]) -> List[NormalizedEvent]:
    horizon = today + timedelta(days=int(cfg.get("horizon_days", 90)))
    skip = {date.fromisoformat(x) for x in cfg.get("skip_dates") or []}
    if skip and max(skip) < horizon:
        print(f"[madrid] WARNING: skip_dates end {max(skip)}, before the horizon {horizon}; "
              "add the next public holidays to the config")
    box = cfg.get("bbox")
    civic_rx = re.compile(cfg.get("community_place_rx") or r"(?!)")
    bad_points, alone = contradicted_points(items, float(cfg.get("site_check_km") or SITE_CHECK_KM))
    groups: Dict[Tuple[str, str, str, str], List[Tuple[Dict[str, Any], Optional[int], str, Optional[int]]]] = {}
    for it in items:
        lo, hi = _day(it.get("dtstart")), _day(it.get("dtend"))
        if lo is None or hi is None:
            _bump(stats, "refused: no usable dates")
            continue
        if hi < today or lo > horizon:
            _bump(stats, "outside the window")
            continue
        _bump(stats, "items in the window")
        title, venue = _s(it.get("title")), _s(it.get("event-location"))
        if not title:
            _bump(stats, "refused: no title")
            continue
        kept, why = verdict(it)
        if not kept:
            _bump(stats, f"refused: {why}")
            continue
        point = _point(it)
        if point is None or not venue:
            _bump(stats, "unplaceable (no point or no venue: a district-wide or roving programme)")
            continue
        if box and not (box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]):
            _bump(stats, "unplaceable (point outside the municipality)")
            continue
        kp = _key_point(it)
        if kp in bad_points:
            _bump(stats, "unplaceable (the City's point contradicts its own postal code)")
            _bump(stats, f"  {venue}, {kp[0]}: {bad_points[kp]:.0f} km from its postal code's other venues")
            continue
        days, clock, shape = sessions(it, today, horizon, skip, stats,
                                      write_exhibitions=cfg.get("write_exhibitions", True) is not False)
        if shape.startswith("refused"):
            _bump(stats, shape)
            continue
        if not days:
            _bump(stats, "no session left in the window")
            continue
        _bump(stats, f"kept: {why}")
        _bump(stats, f"shape: {shape}")
        if kp in alone:
            _bump(stats, "items whose point no other venue in its postal code could check")
        other = exception_spans(_fold(_s(it.get("description"))), lo, hi) if clock is not None else {}
        own = dated_clocks(_fold(_s(it.get("description"))), lo, hi) if shape == "the dates the text names" else {}
        for d in days:
            c, e = other.get(d, (own.get(d, clock), None))
            if d in other:
                _bump(stats, "sessions on the text's own other hours ('salvo el 23 de octubre que será de 16 a 18:15')")
            elif d in own and own[d] != clock:
                _bump(stats, "sessions at the clock the text gives that date ('Sábado 17 de octubre a las 10.00 horas')")
            hm = _hm(c) if c is not None else "dia"
            groups.setdefault((_norm(venue), _norm(title), d.isoformat(), hm), []).append((it, c, shape, e))

    fold_twins(groups, stats)
    drop_untimed_copies(groups, stats)
    out: List[NormalizedEvent] = []
    attribution = cfg["attribution"]
    for (_v, _t, day_s, hm), members in sorted(groups.items()):
        # Identity from the lowest id (stable across runs); the clock from the
        # lowest-id member that has one (an all-day twin never erases it); the
        # words from the member that has the most of them (an exhibition
        # published twice, once with its 135-character blurb and once with none).
        members.sort(key=lambda m: (len(_s(m[0].get("id"))), _s(m[0].get("id"))))
        _bump(stats, "items folded into a session another id already holds", len(members) - 1)
        ident = _s(members[0][0].get("id"))
        _i, clock, _sh, end = next((m for m in members if m[1] is not None), members[0])
        it, _c, shape, _e = max(members, key=lambda m: len(_s(m[0].get("description"))))
        title, venue = _s(it.get("title")), _s(it.get("event-location"))
        lat, lon = _point(it)
        civic = bool(civic_rx.search(_fold(venue)))
        source = "madrid:centros" if civic else "madrid:agenda"
        primary, extras = category(it, cfg)
        tier, price = price_line(it)
        _bump(stats, f"price: {tier}")
        day = date.fromisoformat(day_s)
        desc_src = _s(it.get("description"))
        if end is None and clock is not None:
            end = stated_end(_fold(desc_src), clock)
        if end is not None:
            _bump(stats, "end time from the text's own span")
        lines: List[Optional[str]] = []
        if shape == "exhibition: one all-day row per open day":
            lines.append(f"Exposición abierta este día; hasta el {_date_es(_day(it.get('dtend')))}.")
        elif shape in ("weekly sessions", "the dates the text names", "an nth weekday of each month, as the text says"):
            wds = sorted({WEEKDAY_CODES[c] for c in _s((it.get("recurrence") or {}).get("days")).split(",")
                          if c in WEEKDAY_CODES})
            if shape == "weekly sessions" and wds:
                lines.append(f"Cada semana ({', '.join(WEEKDAY_ES[w] for w in wds)}), hasta el "
                             f"{_date_es(_day(it.get('dtend')))}.")
        district = _district(it)
        tail = [
            audience_line(it),
            (f"Distrito: {district}." if district else None),
            attribution,
        ]
        head = price + (" Sin inscripción, según la fuente." if verdict(it)[1].startswith("the source says no") else "")
        room = DESCRIPTION_MAX - len("\n\n".join([head] + [x for x in lines + tail if x])) - 2
        if len(desc_src) > room:
            desc_src = desc_src[:max(room - 2, 0)].rsplit(" ", 1)[0] + " …" if room >= SOURCE_TEXT_MIN else ""
            _bump(stats, "source text shortened to fit the sync's cap" if desc_src
                  else "source text dropped: no room under the sync's cap")
        if tier in ("unknown", "stated") and TAGGED_FREE.search(desc_src) and not FREE_NEG.search(desc_src):
            # Neither the flag nor the price says free, but 0227 would read
            # this text as free. Withheld, not rewritten.
            _bump(stats, "source text withheld: it reads as free where the flag and price do not")
            desc_src = ""
        paras = [head, desc_src or None] + lines + tail
        if clock is not None:
            start = datetime(day.year, day.month, day.day, clock // 60, clock % 60, tzinfo=tz)
            sl, su = start.strftime("%Y-%m-%dT%H:%M:%S"), start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
            if end is not None:
                fin = start + timedelta(minutes=end - clock)
                el, eu = fin.strftime("%Y-%m-%dT%H:%M:%S"), fin.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                el = eu = None
        else:
            # All-day: a bare date, which the sync anchors to Madrid's local day.
            sl, su, el, eu = day_s, None, None, None
            _bump(stats, "all-day rows (no clock)")
        ev = NormalizedEvent(
            source=source,
            source_id=f"{ident}|{day_s}|{hm}",
            name=title,
            description="\n\n".join(p for p in paras if p),
            start_local=sl, start_utc=su, end_local=el, end_utc=eu,
            timezone=cfg.get("timezone"),
            venue_name=venue,
            latitude=lat, longitude=lon,
            coords_exact=True,
            address=_street(it),
            city=cfg.get("city"), region=cfg.get("region"), country=cfg.get("country"),
            postal_code=_pc(it) or None,
            category=primary, categories=extras,
            ticket_url=_s(it.get("link")) or cfg.get("info_url"),
        )
        ev.fingerprint = make_fingerprint(f"{title} {hm}", day_s, f"{venue} {_street(it) or ''}".strip())
        _bump(stats, f"rows: {source}")
        out.append(ev)
    return out


# The CSV's weekday letters (lunes..domingo) and the JSON's RRULE codes.
_LETTER_DAYS = {"L": "MO", "M": "TU", "X": "WE", "J": "TH", "V": "FR", "S": "SA", "D": "SU"}


def item_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One datastore row (the CSV twin of the JSON files) in the JSON's shape,
    so one set of rules reads both. `DIAS-SEMANA` 'L,M,X,J,V,S,D' is the
    JSON's weekly recurrence 'MO,...,SU'; the CSV carries no interval."""
    def g(k: str) -> str:
        return _s(row.get(k))
    letters = [x.strip().upper() for x in g("DIAS-SEMANA").split(",") if x.strip()]
    codes = [_LETTER_DAYS[x] for x in letters if x in _LETTER_DAYS]
    street = " ".join(x for x in (g("CLASE-VIAL-INSTALACION"), g("NOMBRE-VIA-INSTALACION"),
                                  g("NUM-INSTALACION")) if x)
    item: Dict[str, Any] = {
        "@type": g("TIPO").rstrip("/").rsplit("/", 1)[-1],
        "id": g("ID-EVENTO"),
        "title": g("TITULO"),
        "description": g("DESCRIPCION"),
        "free": 1 if g("GRATUITO") == "1" else 0,
        "price": g("PRECIO"),
        "dtstart": g("FECHA"),
        "dtend": g("FECHA-FIN"),
        "time": g("HORA"),
        "excluded-days": g("DIAS-EXCLUIDOS"),
        "audience": ",".join(x.rsplit("/", 1)[-1] for x in g("AUDIENCIA").split(",") if x.strip()),
        "link": g("CONTENT-URL"),
        "event-location": g("NOMBRE-INSTALACION"),
        "address": {"district": {"@id": f"/Distrito/{g('DISTRITO-INSTALACION')}" if g("DISTRITO-INSTALACION") else ""},
                    "area": {"postal-code": g("CODIGO-POSTAL-INSTALACION"), "street-address": street}},
    }
    if codes:
        item["recurrence"] = {"days": ",".join(codes), "frequency": "WEEKLY", "interval": 1}
    if g("LATITUD") and g("LONGITUD"):
        item["location"] = {"latitude": g("LATITUD"), "longitude": g("LONGITUD")}
    return item


def ingest(store: EventStore, reader: Reader, cfg: Dict[str, Any], tz, stats: Dict[str, int]) -> int:
    first = cfg["sources"][0]["resource_id"]
    print(f"[madrid] {reader.robots_gate(f'{reader.api}?resource_id={first}&limit={PAGE_LIMIT}', _s(cfg.get('permission')))}")
    files: List[List[Dict[str, Any]]] = []
    for src in cfg.get("sources", []):
        label = src.get("name") or src["resource_id"]
        try:
            rows = reader.resource(src["resource_id"])
        except (Refused, OutOfTime) as exc:
            if not files:
                raise
            # What was read before the stop is written: a partial run is still
            # the City's rows, and an upsert never deletes the rest.
            print(f"[madrid] {label} not read ({type(exc).__name__}: {exc}); writing the {len(files)} resource(s) read")
            _bump(stats, "PARTIAL RUN: a resource not read after a refusal or the deadline")
            break
        except Exception as exc:  # noqa: BLE001 - one resource never stops the other
            print(f"[madrid] {label} FAILED: {exc}")
            _bump(stats, "resources that failed")
            continue
        stats[f"rows read: {label}"] = len(rows)
        files.append([item_from_row(r) for r in rows if isinstance(r, dict)])
    if not files:
        raise RuntimeError("no resource could be read")
    items = merge_files(files, stats)
    stats["distinct items"] = len(items)
    evs = events(items, cfg, tz, _today(tz), stats)
    for ev in evs:
        store.upsert(ev)
    return len(evs)


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
    if not cfg.get("sources") or not all(src.get("resource_id") for src in cfg["sources"]):
        raise ValueError("every source needs a datastore resource_id")
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import the Ayuntamiento de Madrid's agenda into the Mapsee store.")
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
    reader = Reader(session, cfg.get("api") or DEFAULT_API, sleep=lambda sec: time.sleep(sec),
                    min_interval=float(cfg.get("crawl_delay") or MIN_INTERVAL_S),
                    deadline=started + a.max_minutes * 60 if a.max_minutes else None)
    store = EventStore(a.store)
    stats: Dict[str, int] = {}
    total = 0
    try:
        total = ingest(store, reader, cfg, tz, stats)
    except Refused as exc:
        print(f"[madrid] REFUSED: {exc} - not retried, and the host is asked nothing more")
    except OutOfTime as exc:
        print(f"[madrid] STOPPED: {exc} (--max-minutes {a.max_minutes:g})")
    except Exception as exc:  # noqa: BLE001
        print(f"[madrid] FAILED: {exc}")
    for k, v in sorted(stats.items()):
        print(f"[madrid]     {k}: {v}")
    store.save()
    st = store.stats
    print(f"[madrid] done in {(time.monotonic() - started):.0f} s: {total} rows written "
          f"(added {st.get('added', 0)}, updated {st.get('updated', 0)}, merged {st.get('merged', 0)}, "
          f"rekeyed {st.get('rekeyed', 0)}, rejected {st.get('rejected', 0)}) in {reader.requests} "
          f"requests; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
