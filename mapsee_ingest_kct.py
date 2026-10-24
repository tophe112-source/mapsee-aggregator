#!/usr/bin/env python3
"""
mapsee_ingest_kct.py - the Czech Tourist Club's calendar of public hikes
(Klub českých turistů, kct.cz/kalendar-akci): New Year's climbs, club marches,
star hikes to a summit, ski and cycle tours, run by local KČT branches
and open to anybody.

    python mapsee_ingest_kct.py --config kct_sources.json --store feeds_events.json \
        [--cursor kct_cursor.json] [--max-minutes 10] [--force] [--dry-run]

WHAT IT READS. kct.cz publishes no feed and no schema.org Event (checked
2026-10-24; its REST API serves the `akce` posts without their fields). Two
pages carry everything:

  * /kalendar-akci/ - ONE GET (1.8 MB) whose FullCalendar script holds every
    event as an inline JSON array `allEvents`: title, start and end dates, the
    page url, `okres` (the district, free text) and `mistos` (the start place,
    free text). 3,999 on 2026-10-24, 1,056 still to come, 241 of them within
    90 days. No coordinates and no clock.
  * /akce/<slug>/ - one page per event (~380 KB, ~3 s each): the start (date,
    clock, place, district), the finish (cíl), the routes with their lengths,
    the organiser, a note, and a Leaflet map whose marker the page EXPLAINS in
    one sentence.

So a run is one list read plus one page per in-window event the cursor does
not already hold (below). HTTP: the production User-Agent, robots.txt read
before the first request (kct.cz's is empty: RFC 9309 says no rules), one
request a second, a 401/403/429 or a bot challenge ends the run, and no request
starts after --max-minutes. The site links no terms of use; /gdpr/ is the
membership consent form. The first read of the 2026-10-24 window took 237
pages and 12.3 minutes over two runs: --max-minutes 10 stopped the first after
194 pages, and the cursor let the second read only the other 43.

WHY THESE RULES (measured 2026-10-24 on all 237 event pages within 90 days):

1. THE PAGE SAYS WHAT ITS PIN IS, AND ONLY TWO OF ITS SENTENCES ARE A PLACE.
   "Značka je v místě souřadnic startu akce." - the start point the organiser
   entered (155 pages). "Značka je v místě souřadnic cíle. Souřadnice startu
   akce nebyly zadány." - the FINISH, because no start was entered (58, star
   hikes above all: "individuální", "odkudkoliv", walkers choose their own way
   to a summit), so the row is named after the finish. "Značka je v místě
   středu ČR. ..." - the centre of Czechia, 49.743756 15.3360752, when nothing
   was entered (24, some with a street address in the text): not written. Any
   other sentence is not believed.

2. A PIN MUST BE PRECISE AND IN ITS OWN DISTRICT. Of the 213 organiser pins,
   2 had two decimals (49.7, 16.25 for Polička; 48.53 for Třísov, south of
   the border), 2 were one-degree slips (České Budějovice's Dům kultury at
   49.97193 14.47813, 111 km north beside Prague; Lhenice, Prachatice district,
   at 49.996 14.149), a start in Olomouc was pinned 51 km from Olomouc, Kleť's
   lay south of the border and one was a caravan meet in Hungary. So both coordinates need
   three decimals (~100 m), the pin must be in Czechia's box, and it must lie
   within `district_radius_km` (50) of the Wikidata point of a district the
   event names. The 193 placed pins sit a median 11.0 km from theirs (p90
   26.8, max 47.0). The district is the one the organiser typed ("Ústí
   n.Orlicí", "Praha 8, Praha-východ", aliases such as "HK" and "Děčínsko":
   184 rows), else a district town named in the start or finish place
   ("Pardubice, žst.": 9). 13 name neither (Prague walks from "Olšanské
   hřbitovy", "zimní stadion", "dle propozic") and are not written. What this
   cannot see is an error inside the district: "Novoroční výstup na Račici"
   starts "U lihovaru, Zábřeh" and is pinned ~35 km south of Zábřeh, 47 km
   from Šumperk's point.

3. A CLOCK IS ONE THE ORGANISER WROTE. The start row shows a clock only when
   one was entered (63 of 237 pages); the page's add-to-calendar widget then
   says 05:00 to 22:00, which is its placeholder. No start clock is an all-day
   row, never a guess, and the finish clock goes into the description: a
   march whose finish says 10:00 is a start window that closes at 10:00. A
   timed start keeps a finish clock that comes after it, so a weekend camp
   runs Friday 16:00 to Sunday 12:00.

4. A CLUB MEETING IS NOT A PUBLIC OUTING. Conferences, programme councils and
   leaders' training and exams are on the same calendar (4 of 241 within 90
   days); they are skipped from the list's title, before any request.

5. A CANCELLATION IS IN THE TITLE, IN CZECH. "ZRUŠENO! – Podzimní stezka
   Polabím", "Zrušeno!!! ...", "... – akce zrušena !!!" (19 of the 3,999).
   The word is cut off, the row is built as the live one was, and it becomes
   a tombstone (store.cancel) with the live row's fingerprint. The shared
   notice rule in mapsee_ingest does not know these words.

6. NOTHING PERSONAL. Every page names a contact person with a private phone
   and email; none of it is read. A note's sentence that carries an email or
   a phone number is dropped.

THE CURSOR (`--cursor`, default kct_cursor.json) keeps each page's parsed
facts with the list row they were read for. A page is read again only when its
list row changed (title, dates, district, place, organiser), when it was read
`refresh_days` ago or more, or when the event starts within
`reread_within_days`; otherwise the row is rebuilt from the cursor at no cost.
Entries for events no longer listed, or past, are dropped. The cursor is saved
every 25 pages and on the way out of a deadline, so the next run carries on.

Category: outdoors with fitness (a route on foot, ski, bike, water or
horseback); running for a run or a race, kids for a family route (the
organiser's "Rodinná turistika" or "Kočárková trasa"); community when the page
names no route at all (a collectors' meet, a knot-tying regatta).
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from mapsee_ingest import (EventStore, NormalizedEvent, USER_AGENT, looks_online_only,
                           make_fingerprint, norm_categories)

SOURCE = "kct"
LIST_URL = "https://kct.cz/kalendar-akci/"
PAGE_PREFIX = "https://kct.cz/akce/"
TZ_NAME = "Europe/Prague"
DEFAULT_WITHIN_DAYS = 90
DEFAULT_MAX_MINUTES = 10.0
DEFAULT_RADIUS_KM = 50.0
DEFAULT_REFRESH_DAYS = 14
DEFAULT_REREAD_DAYS = 7
SAVE_EVERY = 25
DESCRIPTION_ROOM = 780        # under the sync's DESCRIPTION_MAX of 800
CURSOR_VERSION = 1            # bump when parse_detail changes: an old cursor is then read afresh

# Rule 1: the page's sentence about its own marker.
PIN_START = "start"
PIN_FINISH = "finish"
_PIN_START_RX = re.compile(r"^\s*Značka je v místě souřadnic startu akce\.?\s*$", re.I)
_PIN_FINISH_RX = re.compile(r"^\s*Značka je v místě souřadnic cíle\b", re.I)
_PIN_NONE_RX = re.compile(r"\bstředu ČR\b|nebyly organizátorem zadány", re.I)
CZ_CENTRE = (49.743756, 15.3360752)
CZ_BOX = (48.55, 51.06, 12.09, 18.86)          # lat min/max, lon min/max

# Rule 4: the calendar's own meetings and training, by title.
_MEETING_RX = re.compile(
    r"\b(?:konferenc\w*|porad[ayě]\b|valn\w+\s+hromad\w*|sněm\w*|zasedání|shromáždění\s+delegát\w*|"
    r"seminář\w*|školení|zkoušk\w*\s+vedoucích|programová\s+rada)", re.I)

# Rule 5: a title that says the event is off. Postponed (odloženo) is off too:
# the date on the row is not the one it will happen on.
_OFF = r"zrušen[oaáý]?|odložen[oaáý]?|(?:se\s+)?nekoná(?:\s+se)?"
_CANCELLED_RX = re.compile(rf"\b(?:{_OFF})\b", re.I)
_LEAD_OFF_RX = re.compile(rf"^\W*(?:akce\s+)?(?:{_OFF})\b[\s!.:]*[–—\-:|]?\s*", re.I)
_REASON_RX = re.compile(r"^\W*(?:z\s+)?(?:technick\w*\s+|zdravotn\w*\s+|organizačn\w*\s+)?důvod\w*\W*$", re.I)
_TAIL_OFF_RX = re.compile(
    rf"\s*(?:[–—\-:|(]|!+)\s*(?:akce|seminář|pochod)?\s*(?:je\s+)?(?:[^–—\-!|()]*?\s)?(?:{_OFF})\b[^–—|]*$",
    re.I)

# Route types, as the page labels them, and what each means on the map.
MOVEMENT = {
    "pěší turistika": "Hike", "cykloturistika": "Bike tour", "lyžařská turistika": "Ski tour",
    "vodní turistika": "Paddling", "běžecká turistika": "Run", "rodinná turistika": "Family route",
    "kočárková trasa": "Pushchair route", "turistika mládeže": "Youth hike",
    "vysokohorská turistika": "Mountain hike", "turistika na koni": "Horse ride",
    "bosá turistika": "Barefoot hike", "elektronická turistika": "GPS route",
    "turistika zdravotně postižených": "Accessible route",
    "turistika zdravotně znevýhodněných": "Accessible route",
    "turistický závod": "Orienteering race", "akce ivv": "IVV walk",
    "novoroční čtyřlístek": "New Year's hike (Novoroční čtyřlístek)",
}
OTHER_TYPES = {"turistický zájezd": "Coach trip", "akce tom": "TOM youth club event",
               "mototuristika a karavanink": "Motor touring"}
# Not copied: "Sleva KČT" is a members' discount (../mapsee 0227 would read
# "discount" as an offer to everybody), "Turistická 200" a badge scheme.
SILENT_TYPES = {"sleva kčt", "turistická 200", "turistická dvoustovka", "akce kčt"}
KIDS_TYPES = {"rodinná turistika", "kočárková trasa"}
RUN_TYPES = {"běžecká turistika"}
_RUN_TITLE_RX = re.compile(r"\b(?:běh\w*|půlmaraton\w*|maraton\w*|kros\w*|cross\w*|duatlon\w*|"
                           r"sprintduatlon\w*|triatlon\w*)\b", re.I)
_RACE_TITLE_RX = re.compile(r"\bzávod\w*\b", re.I)

# Rule 6: a sentence that reaches a person.
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RX = re.compile(r"(?:\+?420[\s-]?)?\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b")


# --------------------------------------------------------------------------- #
# small helpers

def _text(fragment: Optional[str]) -> str:
    """Visible text of an HTML fragment, one line."""
    s = re.sub(r"<br\s*/?>", " ", fragment or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def slug(s: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _cap(s: Optional[str], n: int) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _date_cz(s: Optional[str]) -> Optional[date]:
    """'1. 1. 2027' -> date(2027, 1, 1)."""
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _clock(s: Optional[str]) -> Optional[Tuple[int, int]]:
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def _decimals(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


# --------------------------------------------------------------------------- #
# the list

def _iso_day(s: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def parse_list(page: str) -> Tuple[List[Dict[str, Any]], int]:
    """(rows, malformed). Each row: url, slug, title, first, last (dates),
    okres, mistos, poradatel - the list's own text, whitespace-trimmed. A record
    without a kct.cz/akce/ url or a readable start date is counted, not raised."""
    m = re.search(r"\ballEvents\s*=\s*(\[.*?\])\s*;\s*\n", page or "", re.S)
    if not m:
        raise ValueError("no allEvents array on the calendar page")
    raw = json.loads(m.group(1))
    rows: List[Dict[str, Any]] = []
    bad = 0
    for rec in raw:
        if not isinstance(rec, dict):
            bad += 1
            continue
        url = str(rec.get("url") or "").strip()
        first = _iso_day(rec.get("start"))
        if not url.startswith(PAGE_PREFIX) or first is None:
            bad += 1
            continue
        last = _iso_day(rec.get("end")) or first
        if last < first:
            last = first
        rows.append({
            "url": url,
            "slug": url[len(PAGE_PREFIX):].strip("/"),
            "title": re.sub(r"\s+", " ", htmllib.unescape(str(rec.get("title") or ""))).strip(),
            "first": first,
            "last": last,
            "okres": re.sub(r"\s+", " ", str(rec.get("okres") or "")).strip(),
            "mistos": re.sub(r"\s+", " ", str(rec.get("mistos") or "")).strip(),
            "poradatel": re.sub(r"\s+", " ", str(rec.get("poradatel") or "")).strip(),
        })
    return rows, bad


def row_sig(row: Dict[str, Any]) -> str:
    """What the list says about one event; a change means its page is read again."""
    basis = json.dumps([row["title"], row["first"].isoformat(), row["last"].isoformat(),
                        row["okres"], row["mistos"], row["poradatel"]], ensure_ascii=False)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# the event page

def _icons(cell: str) -> Dict[str, str]:
    """The start and finish cells are icon-led lines: fa-calendar-check is the
    date, fa-clock the clock, fa-map-marker-alt the place, fa-map-marked-alt
    the district."""
    out: Dict[str, str] = {}
    for cls, val in re.findall(r"<i\s+class=['\"]([^'\"]+)['\"][^>]*>\s*</i>\s*([^<]*)", cell or "", re.I):
        icon = next((c for c in cls.split() if c.startswith("fa-")), None)
        if icon and icon not in out:
            out[icon] = _text(val)
    return out


def _rows(page: str) -> List[Tuple[str, str]]:
    """(label, value-html) of the two detail tables. Rows are not always closed
    and cells mix <td> and <TD>, so rows are split on '<tr' and cells on '<td'."""
    start = page.find("fa-map-pin")
    end = page.find("Přidejte si akci do kalendáře")
    if start < 0:
        start = 0
    seg = page[start:end if end > start else len(page)]
    out = []
    for tr in re.split(r"<tr\b[^>]*>", seg, flags=re.I)[1:]:
        cells = re.split(r"<td\b[^>]*>", tr, flags=re.I)[1:]
        if len(cells) >= 2:
            out.append((_text(cells[0]), cells[1]))
        elif len(cells) == 1:
            out.append(("", cells[0]))
    return out


def parse_detail(page: str) -> Dict[str, Any]:
    """Everything the adapter reads from one event page, JSON-serialisable (the
    cursor stores it). Contacts are never read."""
    det: Dict[str, Any] = {}
    t = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    if t:
        det["title"] = re.sub(r"\s+-\s+KČT\s+-\s+Klub českých turistů\s*$", "", _text(t.group(1)))
    mk = re.search(r"\bvar\s+markers\s*=\s*(\[.*?\])\s*;", page, re.S)
    if mk:
        try:
            first = (json.loads(mk.group(1)) or [{}])[0]
            det["lat_s"] = str(first.get("latitude") or "").strip() or None
            det["lon_s"] = str(first.get("longitude") or "").strip() or None
        except (ValueError, IndexError, AttributeError):
            pass
    note = re.search(r"fa-map-pin['\"][^>]*>\s*</i>\s*([^<]*)", page)
    if note:
        det["pin_note"] = _text(note.group(1))
    types: List[str] = []
    for label, cell in _rows(page):
        low = label.lower()
        if not label and "ročník" in cell:
            m = re.search(r"(\d+)\.\s*ročník", _text(cell))
            if m:
                det["edition"] = int(m.group(1))
        elif low.startswith("start"):
            f = _icons(cell)
            det["start_date"] = (_date_cz(f.get("fa-calendar-check")) or None)
            det["start_clock"] = f.get("fa-clock") or None
            det["start_place"] = f.get("fa-map-marker-alt") or None
            det["start_okres"] = re.sub(r"^okres\s+", "", f.get("fa-map-marked-alt") or "", flags=re.I) or None
        elif low.startswith("cíl"):
            f = _icons(cell)
            det["end_date"] = _date_cz(f.get("fa-calendar-check"))
            det["end_clock"] = f.get("fa-clock") or None
            det["finish_place"] = f.get("fa-map-marker-alt") or None
        elif low.startswith("typ"):
            for alt, txt in re.findall(r"<img[^>]*\balt=\"([^\"]*)\"[^>]*>\s*</span>\s*<span[^>]*>(.*?)</span>",
                                       cell, re.S | re.I):
                lab = _text(txt) or _text(alt)
                if lab:
                    types.append(lab)
        elif low.startswith("pořadatel"):
            det["organiser"] = _text(cell) or None
        elif low.startswith("poznámka"):
            det["note"] = _text(cell) or None
        # "Kontakt" (a person's phone and email), "Web", "Propozice" and
        # "Ubytování" are not read.
    det["types"] = types
    for k in ("start_date", "end_date"):
        if isinstance(det.get(k), date):
            det[k] = det[k].isoformat()
    return det


# --------------------------------------------------------------------------- #
# placement (rules 1 and 2)

def _district_keys_from_text(text: Optional[str], districts: Dict[str, Any],
                             aliases: Dict[str, str]) -> List[str]:
    out: List[str] = []
    s = re.sub(r"\bokres\b", " ", text or "", flags=re.I)
    for part in re.split(r",|/|;|\+|\ba\b", s):
        k = slug(part)
        k = re.sub(r"-?cr$", "", k)                      # "Chomutov, ČR"
        k = re.sub(r"(^|-)n-", r"\1nad-", k)             # "Ústí n.Orlicí"
        if re.fullmatch(r"praha(-\d+)?|praha-mesto|hlavni-mesto-praha", k):
            k = "hlavni-mesto-praha"
        k = aliases.get(k, k)
        if k in districts and k not in out:
            out.append(k)
    return out


_NOT_A_SEAT = {"most", "tabor"}        # also everyday words: a bridge, a camp


def _seat_names(districts: Dict[str, Any], aliases: Dict[str, str]) -> Dict[str, str]:
    """Town word -> district key, for a district town named in a start place."""
    seats: Dict[str, str] = {}
    for k in districts:
        if k == "hlavni-mesto-praha":
            seats["praha"] = k
        elif k.endswith("-mesto"):
            seats[k[:-len("-mesto")]] = k
        elif k in ("praha-vychod", "praha-zapad", "plzen-jih", "plzen-sever", "brno-venkov"):
            continue
        elif k not in _NOT_A_SEAT:
            seats[k] = k
    for a, k in aliases.items():
        if k in districts:
            seats.setdefault(a, k)
    return seats


# THE SITE'S OWN DISTRICT TAXONOMY IS NOT READ. Each page carries an
# okres-<slug> post class, but an empty or unreadable district is filed under
# Prague or Ostrava: of the 194 pages read on 2026-10-24, 24 carried
# okres-hlavni-mesto-praha and 10 okres-ostrava-mesto, among them "Pardubice,
# žst." and two "Ústí n.Orlicí" (130 km from Ostrava). Where the slug was right,
# the typed district said the same, so the text is read instead.


def districts_for(det: Dict[str, Any], row: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[List[str], str]:
    """(district keys the pin may be checked against, where they came from):
    the district the organiser typed, else a district town named in the start
    or finish place."""
    districts = cfg.get("districts") or {}
    aliases = cfg.get("district_aliases") or {}
    texts = [x for x in (det.get("start_okres"), row.get("okres")) if x and x.strip()]
    keys: List[str] = []
    for text in texts:
        for k in _district_keys_from_text(text, districts, aliases):
            if k not in keys:
                keys.append(k)
    if keys:
        return keys, "text"
    seats = _seat_names(districts, aliases)
    for place in (det.get("start_place"), row.get("mistos"), det.get("finish_place")):
        words = slug(place)
        for word, k in seats.items():
            if words and re.search(r"(?:^|-)" + re.escape(word) + r"(?:$|-)", words) and k not in keys:
                keys.append(k)
    return keys, ("start place" if keys else "none")


def place_pin(det: Dict[str, Any], row: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """({lat, lon, kind, district}, "") or (None, why the pin is not believed)."""
    note = det.get("pin_note") or ""
    lat_s, lon_s = det.get("lat_s"), det.get("lon_s")
    if not lat_s or not lon_s:
        return None, "no pin on the page"
    try:
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        return None, "no pin on the page"
    if _PIN_NONE_RX.search(note) or (abs(lat - CZ_CENTRE[0]) < 1e-6 and abs(lon - CZ_CENTRE[1]) < 1e-6):
        return None, "no place entered (the pin is the centre of Czechia)"
    if _PIN_START_RX.search(note):
        kind = PIN_START
    elif _PIN_FINISH_RX.search(note):
        kind = PIN_FINISH
    else:
        return None, "pin of unknown meaning"
    if _decimals(lat_s) < 3 or _decimals(lon_s) < 3:
        return None, "pin too coarse (under 3 decimals)"
    if not (CZ_BOX[0] <= lat <= CZ_BOX[1] and CZ_BOX[2] <= lon <= CZ_BOX[3]):
        return None, "pin outside Czechia"
    keys, how = districts_for(det, row, cfg)
    if not keys:
        return None, "no district to check the pin against"
    radius = float(cfg.get("district_radius_km", DEFAULT_RADIUS_KM))
    districts = cfg["districts"]
    near = min(keys, key=lambda k: km((lat, lon), tuple(districts[k])))
    if km((lat, lon), tuple(districts[near])) > radius:
        return None, f"pin over {radius:g} km from its district"
    return {"lat": lat, "lon": lon, "kind": kind, "district": near, "district_from": how}, ""


# --------------------------------------------------------------------------- #
# the row

def cancelled_title(title: str) -> Tuple[bool, str]:
    """(is it called off, the title without the notice). The title is left as
    it was when the notice cannot be cut off cleanly."""
    if not _CANCELLED_RX.search(title or ""):
        return False, title
    t = _LEAD_OFF_RX.sub("", title)
    # "Šternberský vandr – AKCE ZRUŠENA – Putování ...", "ZRUŠENO !! Technické
    # důvody !! Turistický pochod ...": a block between spaced dashes or "!!"
    # that is the notice or its reason goes; the others are the title.
    parts = re.split(r"(\s+[–—\-]\s+|\s*!{2,}\s*)", t)
    blocks, seps = parts[0::2], parts[1::2]
    keep = [not (_CANCELLED_RX.search(b) and len(b.split()) <= 3) and not _REASON_RX.match(b)
            for b in blocks]
    if any(keep) and not all(keep):
        out = ""
        for i, b in enumerate(blocks):
            if keep[i]:
                out += ((" – " if "!" not in seps[i - 1] else " ") if out else "") + b
        t = out
    t = _TAIL_OFF_RX.sub("", t)
    t = re.sub(r"\s*!{2,}\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" –—-:|!")
    return True, (t if t and not _CANCELLED_RX.search(t) else title)


def is_meeting(title: str) -> bool:
    return bool(_MEETING_RX.search(title or ""))


def _type_key(label: str) -> str:
    return re.sub(r"\s*\(.*$", "", label).strip().lower()


def categories_for(types: List[str], title: str) -> Tuple[str, List[str]]:
    keys = [_type_key(t) for t in types]
    moving = any(k in MOVEMENT for k in keys)
    extras: List[str] = []
    if any(k in RUN_TYPES for k in keys) or _RUN_TITLE_RX.search(title or ""):
        extras.append("running")
    elif _RACE_TITLE_RX.search(title or "") or "turistický závod" in keys:
        extras.append("sports")
    if any(k in KIDS_TYPES for k in keys):
        extras.append("kids")
    if not moving:
        return "community", norm_categories("community", extras)
    extras.append("fitness")
    return "outdoors", norm_categories("outdoors", extras)


def _routes(types: List[str]) -> Optional[str]:
    """'Hike (8 km), Ski tour (8 km)': the page's route labels in English, the
    distances as written."""
    out: List[str] = []
    for t in types:
        k = _type_key(t)
        if k in SILENT_TYPES:
            continue
        name = MOVEMENT.get(k) or OTHER_TYPES.get(k) or re.sub(r"\s*\(.*$", "", t).strip()
        m = re.search(r"\(([^)]*)\)", t)
        item = f"{name} ({m.group(1).strip()})" if m and m.group(1).strip() else name
        if item not in out:
            out.append(item)
    return ", ".join(out) or None


def without_contacts(text: Optional[str]) -> Optional[str]:
    """A note's sentences without the ones that reach a person (rule 6)."""
    if not text:
        return None
    # A sentence ends before a capital: "tel. 555 ..." is one sentence.
    kept = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])", text)
            if not _EMAIL_RX.search(s) and not _PHONE_RX.search(s)]
    return " ".join(kept).strip() or None


def _local(d: date, hm: Tuple[int, int]) -> str:
    return f"{d.isoformat()}T{hm[0]:02d}:{hm[1]:02d}:00"


def _utc(local: str) -> str:
    from zoneinfo import ZoneInfo
    dt = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(TZ_NAME))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_event(row: Dict[str, Any], det: Dict[str, Any], cfg: Dict[str, Any], today: date,
             within_days: int) -> Tuple[Optional[NormalizedEvent], str, bool]:
    """(event, "", cancelled) or (None, why-not, cancelled). The page's facts win
    over the list's."""
    first = _iso_day(det.get("start_date")) or row["first"]
    last = _iso_day(det.get("end_date")) or row["last"]
    if last < first:
        last = first
    if last < today:
        return None, "past", False
    if first > today + timedelta(days=within_days):
        return None, "beyond the window", False
    off, title = cancelled_title(row["title"] or det.get("title") or "")
    if not off:
        off, _ = cancelled_title(det.get("title") or "")
    if not title:
        return None, "no title", off
    pin, why = place_pin(det, row, cfg)
    if pin is None:
        return None, why, off
    body = " ".join(x for x in (det.get("note"), title) if x)
    if looks_online_only(title, body):
        return None, "online", off

    start_hm = _clock(det.get("start_clock"))
    end_hm = _clock(det.get("end_clock"))
    if start_hm:
        start_local = _local(first, start_hm)
        end_local = None
        if end_hm and _local(last, end_hm) > start_local:
            end_local = _local(last, end_hm)
        elif last > first:
            end_local = last.isoformat()
    else:
        start_local = first.isoformat()
        end_local = last.isoformat() if last > first else None
    start_utc = _utc(start_local) if "T" in start_local else None
    end_utc = _utc(end_local) if end_local and "T" in end_local else None

    start_place = det.get("start_place") or row.get("mistos") or None
    finish_place = det.get("finish_place") or None
    venue = finish_place if pin["kind"] == PIN_FINISH else start_place
    okres = det.get("start_okres") or row.get("okres") or None
    organiser = det.get("organiser") or row.get("poradatel") or None

    lines: List[str] = []
    routes = _routes(det.get("types") or [])
    if routes:
        lines.append(routes + ".")
    start_bits = [x for x in (det.get("start_clock"), start_place) if x]
    if start_bits:
        lines.append("Start: " + ", ".join(start_bits) + (f" (okres {okres})." if okres else "."))
    finish_bits = [x for x in (det.get("end_clock"), finish_place) if x]
    if finish_bits:
        lines.append("Finish: " + ", ".join(finish_bits) + ".")
    if pin["kind"] == PIN_FINISH:
        lines.append("The pin is the finish: no start point was entered, so walkers choose their own way there.")
    facts = []
    if organiser:
        facts.append(f"Organised by {organiser}")
    if det.get("edition"):
        facts.append(f"edition {det['edition']}")
    if facts:
        lines.append(", ".join(facts) + ".")
    closing = "Listed in the Klub českých turistů events calendar (kct.cz)."
    room = DESCRIPTION_ROOM - sum(len(x) + 2 for x in lines) - len(closing) - 2
    note = without_contacts(det.get("note"))
    if note and room >= 60:
        lines.append(_cap(note, room))
    lines.append(closing)

    primary, extras = categories_for(det.get("types") or [], title)
    nev = NormalizedEvent(
        source=SOURCE,
        source_id=row["slug"],
        name=title,
        description="\n\n".join(lines),
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=TZ_NAME,
        venue_name=venue,
        latitude=pin["lat"], longitude=pin["lon"],
        country="CZ",
        category=primary,
        categories=extras,
        promoter=organiser,
        ticket_url=row["url"],
        coords_exact=True,
    )
    nev.fingerprint = make_fingerprint(nev.name, first.isoformat(), venue, None)
    return nev, "", off


# --------------------------------------------------------------------------- #
# HTTP

class Refused(Exception):
    """The host said no: 401/403/429, a bot challenge or robots.txt."""


class Stop(Exception):
    """The run's deadline passed; no request starts after it."""


class Client:
    """One host, paced, robots-checked and deadline-aware. get() returns the body
    of a 200, None for any other answer worth skipping (a network error costs
    one page, not the run), and raises Refused or Stop."""

    def __init__(self, session, pause: float, deadline: Optional[float], robots=None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.session, self.pause, self.deadline = session, pause, deadline
        self.robots, self.clock, self.sleep = robots, clock, sleep
        self.requests = 0
        self._last: Optional[float] = None

    def get(self, url: str) -> Optional[str]:
        from robots_txt import CHALLENGE_RX
        if self.deadline is not None and self.clock() >= self.deadline:
            raise Stop("run deadline reached")
        if self.robots is not None:
            v = self.robots.check(url)
            if v.get("allowed") is not True:
                raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url}")
            if v.get("crawl_delay") and v["crawl_delay"] > self.pause:
                self.pause = float(v["crawl_delay"])
        if self._last is not None:
            wait = self.pause - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.requests += 1
        try:
            r = self.session.get(url, timeout=40, allow_redirects=True)
        except OSError as exc:            # requests.RequestException is an OSError
            print(f"[{SOURCE}] network error, page skipped: {type(exc).__name__} - {url}", flush=True)
            return None
        if r.status_code in (401, 403, 429):
            raise Refused(f"HTTP {r.status_code} for {url}")
        if urlsplit(str(getattr(r, "url", url))).netloc not in ("", urlsplit(url).netloc):
            print(f"[{SOURCE}] redirected off the site, not read: {url} -> {r.url}", flush=True)
            return None
        text = r.text or ""
        if CHALLENGE_RX.search(text[:6000]):
            raise Refused(f"bot challenge for {url}")
        if r.status_code != 200:
            print(f"[{SOURCE}] HTTP {r.status_code}: {url}", flush=True)
            return None
        return text


# --------------------------------------------------------------------------- #
# the cursor

def load_cursor(path: Optional[str]) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {"version": CURSOR_VERSION, "pages": {}}
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        print(f"[{SOURCE}] cursor {path} unreadable; starting fresh", flush=True)
        return {"version": CURSOR_VERSION, "pages": {}}
    if not isinstance(data, dict) or data.get("version") != CURSOR_VERSION or not isinstance(data.get("pages"), dict):
        return {"version": CURSOR_VERSION, "pages": {}}
    return data


def save_cursor(path: Optional[str], cursor: Dict[str, Any]) -> None:
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cursor, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)


def needs_read(entry: Optional[Dict[str, Any]], sig: str, first: date, today: date,
               refresh_days: int, reread_days: int) -> Optional[str]:
    """Why this event's page must be read now, or None when the cursor will do."""
    if not entry or not isinstance(entry.get("det"), dict):
        return "new"
    if entry.get("sig") != sig:
        return "changed"
    read = _iso_day(entry.get("read"))
    if read is None or (today - read).days >= refresh_days:
        return "stale"
    if (first - today).days < reread_days and read < today:
        return "soon"
    return None


# --------------------------------------------------------------------------- #

def ingest(cfg: Dict[str, Any], client: Client, today: date,
           emit: Callable[[NormalizedEvent], None],
           cancel: Callable[[NormalizedEvent, str], None],
           cursor: Optional[Dict[str, Any]] = None,
           save: Callable[[], None] = lambda: None) -> Dict[str, Any]:
    """Read the list, then each in-window event's page unless the cursor holds
    it unchanged. Returns counts. After a deadline no request starts, but every
    event the cursor still holds is written."""
    within = int(cfg.get("within_days", DEFAULT_WITHIN_DAYS))
    refresh_days = int(cfg.get("refresh_days", DEFAULT_REFRESH_DAYS))
    reread_days = int(cfg.get("reread_within_days", DEFAULT_REREAD_DAYS))
    cursor = cursor if cursor is not None else {"version": CURSOR_VERSION, "pages": {}}
    pages: Dict[str, Any] = cursor.setdefault("pages", {})
    rep: Dict[str, Any] = {"listed": 0, "malformed": 0, "future": 0, "in_window": 0, "read": 0,
                           "from_cursor": 0, "kept": 0, "cancelled": 0, "skipped": {}, "kinds": {},
                           "pins": {}, "reads": {}, "stopped": None}

    def skip(why: str) -> None:
        rep["skipped"][why] = rep["skipped"].get(why, 0) + 1

    page = client.get(cfg.get("list_url") or LIST_URL)
    if page is None:
        rep["stopped"] = "the calendar page was not read"
        return rep
    try:
        rows, bad = parse_list(page)
    except ValueError as exc:
        rep["stopped"] = f"the calendar page did not parse: {exc}"
        return rep
    rep["listed"], rep["malformed"] = len(rows), bad
    horizon = today + timedelta(days=within)
    future = [r for r in rows if r["last"] >= today]
    rep["future"] = len(future)
    # Entries for events no longer listed (or past) go; nothing else reads them.
    live = {r["url"] for r in future}
    for url in [u for u in pages if u not in live]:
        del pages[url]
    window = sorted((r for r in future if r["first"] <= horizon), key=lambda r: (r["first"], r["url"]))
    rep["in_window"] = len(window)
    stopped = False
    since_save = 0
    for row in window:
        if is_meeting(row["title"]):
            skip("club meeting or training")
            continue
        sig = row_sig(row)
        entry = pages.get(row["url"])
        reason = needs_read(entry, sig, row["first"], today, refresh_days, reread_days)
        # A page due only for a refresh ("stale", "soon") that cannot be read
        # now - the deadline passed, or the read failed - is written from the
        # cursor; a page whose list row CHANGED is not, since the change may be
        # the cancellation.
        fallback = entry["det"] if reason in ("stale", "soon") else None
        body = None
        if reason is not None and not stopped:
            try:
                body = client.get(row["url"])
            except Stop as exc:
                stopped = True
                rep["stopped"] = f"{exc} after {rep['read']} event page(s)"
        if reason is None:
            det = entry["det"]
            rep["from_cursor"] += 1
        elif body is None:
            if fallback is None:
                skip("deadline: page not read" if stopped else "page not read")
                continue
            det = fallback
            rep["from_cursor"] += 1
        else:
            det = parse_detail(body)
            rep["read"] += 1
            rep["reads"][reason] = rep["reads"].get(reason, 0) + 1
            if not (det.get("pin_note") or det.get("start_date")):
                # Not the page this parser knows (a maintenance page, a new
                # template): not cached, so the next run reads it again.
                skip("page did not parse")
                continue
            pages[row["url"]] = {"sig": sig, "read": today.isoformat(), "det": det}
            since_save += 1
            if since_save >= SAVE_EVERY:
                save()
                since_save = 0
        ev, why, off = to_event(row, det, cfg, today, within)
        note_kind = ("start" if _PIN_START_RX.search(det.get("pin_note") or "") else
                     "finish" if _PIN_FINISH_RX.search(det.get("pin_note") or "") else
                     "centre of Czechia" if _PIN_NONE_RX.search(det.get("pin_note") or "") else "other")
        rep["pins"][note_kind] = rep["pins"].get(note_kind, 0) + 1
        if ev is None:
            skip(("cancelled, never placed: " if off else "") + why)
            continue
        if off:
            cancel(ev, "the KČT calendar marks it cancelled")
            rep["cancelled"] += 1
            continue
        emit(ev)
        rep["kept"] += 1
        rep["kinds"][ev.category] = rep["kinds"].get(ev.category, 0) + 1
    save()
    return rep


def _today() -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return date.today()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import the Czech Tourist Club's (KČT) public hikes.")
    ap.add_argument("--config", default="kct_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--cursor", default="kct_cursor.json",
                    help="the pages already read, so an unchanged event is not fetched again ('' = none)")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--force", action="store_true", help="run regardless of the config's run_weekdays")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write neither store nor cursor")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    today = _today()
    wd = cfg.get("run_weekdays")
    if wd and not a.force and today.weekday() not in wd:
        print(f"[{SOURCE}] skipped (runs on weekdays {wd}; today is {today.weekday()})")
        return 0
    try:
        import requests
    except ImportError:  # pragma: no cover
        sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
    from robots_txt import Robots

    session = requests.Session()
    # Only the User-Agent: robots.txt is read with this session (see
    # mapsee_ingest_rallybound, where a session-wide Accept turned it into a 406).
    session.headers.update({"User-Agent": USER_AGENT})
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    client = Client(session, float(cfg.get("pause", 1.0)), deadline, robots=Robots(session))
    store = None if a.dry_run else EventStore(a.store)
    cursor_path = None if a.dry_run else (a.cursor or None)
    cursor = load_cursor(a.cursor or None)
    pending = [0]

    def emit(ev: NormalizedEvent) -> None:
        if store is None:
            return
        store.upsert(ev)
        pending[0] += 1
        if pending[0] >= SAVE_EVERY:
            store.save()
            pending[0] = 0

    def cancel(ev: NormalizedEvent, reason: str) -> None:
        if store is not None:
            store.cancel(ev, reason)

    def save() -> None:
        save_cursor(cursor_path, cursor)

    rep = None
    try:
        rep = ingest(cfg, client, today, emit, cancel, cursor=cursor, save=save)
    except Refused as exc:
        print(f"[{SOURCE}] REFUSED - {exc}; stopping (a refusal is not retried)", flush=True)
    finally:
        if store is not None:
            store.save()
        save()
    took = (time.monotonic() - started) / 60
    if rep is not None:
        skipped = ", ".join(f"{k} {v}" for k, v in sorted(rep["skipped"].items())) or "none"
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(rep["kinds"].items())) or "none"
        pins = ", ".join(f"{k} {v}" for k, v in sorted(rep["pins"].items())) or "none"
        reads = ", ".join(f"{k} {v}" for k, v in sorted(rep["reads"].items())) or "none"
        print(f"[{SOURCE}] listed {rep['listed']} ({rep['malformed']} malformed), {rep['future']} to come, "
              f"{rep['in_window']} in the {cfg.get('within_days', DEFAULT_WITHIN_DAYS)}-day window; "
              f"pages read {rep['read']} ({reads}), from the cursor {rep['from_cursor']}; "
              f"pins: {pins}; kept {rep['kept']} ({kinds}), cancelled {rep['cancelled']}; "
              f"skipped: {skipped}; {client.requests} request(s) in {took:.1f} min", flush=True)
        if rep.get("stopped"):
            print(f"[{SOURCE}] STOPPED - {rep['stopped']}", flush=True)
    if store is not None:
        print(f"[{SOURCE}] store now holds {len(store.records)} unique events.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
