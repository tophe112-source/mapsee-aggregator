#!/usr/bin/env python3
"""
mapsee_ingest_repaircafe_ch.py - import every Swiss repair café's dated
sessions from repair-cafe.ch into the Mapsee store.

    python mapsee_ingest_repaircafe_ch.py --config repaircafe_ch_sources.json \
        --store feeds_events.json [--cursor repaircafe_ch_cursor.json] \
        [--max-minutes 11] [--max-pages N] [--dry-run] [--dump rows.json]

WHAT THE SOURCE IS
------------------
repair-cafe.ch is the platform of the Swiss repair-café movement, run since
2014 by the three consumer organisations (Stiftung für Konsumentenschutz, FRC,
ACSI). Every repair café in the country, German, French and Italian speaking,
posts its sessions there: one WordPress post of type `repairevent` per date,
with Datum, Uhrzeit and Ort (street, postcode, town). Free (or pay what you
like) community repair sessions; nothing else in this repo carries them for
Switzerland beyond the odd OpenAgenda page.

CONDUCT, CHECKED 2026-10-24 WITH OUR OWN USER-AGENT
---------------------------------------------------
robots.txt is Yoast's "User-agent: * / Disallow:" - every path allowed, no
Crawl-delay. The site has no terms of use; its Impressum says each café is
responsible for its own sub-pages, that the operators do not check them, and
that unlawful content is removed on notice. Nothing forbids reading or
republishing. The adapter still asks robots.txt before the first request (and
again for every redirect host), sends our User-Agent, never more than one
request a second, and treats a 401/403/429 or a bot challenge as a refusal that
ends the run. A café that wants its sessions off contacts us (README, Conduct).

WHY THIS IS A SCRAPER, AND WHY IT HAS A CURSOR
----------------------------------------------
No feed: WP REST /wp/v2/repairevent exposes no date (acf: []), there is no
Event JSON-LD and no iCal. The date is only in the rendered page. The
"Nächste Repair Cafés" listing gives a date and a café but links the CAFÉ, not
the session, and its ?paged=2 returns page 1 again.
The index is Yoast's sitemaps: 1,866 repairevent URLs on 2026-10-24
(repairevent-sitemap.xml 1,000 + repairevent-sitemap2.xml 866), mostly
history. Measured over 24 pages sampled across the lastmod range, every page
last modified before 2025-10 was a past session, and about half of those
modified since were past too. LASTMOD IS NOT THE EVENT DATE: GZ Loogarten
created a two-year series on 2025-10-24 whose sessions run to 2027-01-27 (15
months after their lastmod), and a 2025-09-01 bulk re-save touched 360 old
posts at once. So each URL is fetched ONCE and remembered in the cursor
(lastmod, date, and for an upcoming session the parsed page): a URL is fetched
again only when its lastmod changes, a past one never. A URL last modified
more than `lastmod_floor_days` (548, 18 months) ago is never fetched at all.
Newest lastmod first, so a cold cursor reaches the upcoming sessions early;
the cold walk is spread over runs by `--max-minutes` and `--max-pages`, and
a warm run costs the sitemap index, the repairevent sitemaps, the café map and
the posts changed since the last run (about 4 a day in Sept-Oct 2026).
Rows are rebuilt from the cursor on every run, so a session read last week is
still written today without a request.
mapsee_prune_cancelled.py reads each written row's page again in its daily
full sweep (07:55, --days 120): 180 of the 216 rows on the full 2026-10-24
cursor, at its own 0.35 s spacing, since the site sets no Crawl-delay (13
sessions share 2026-10-31 09:00Z, so their pages are asked one after another).
That sweep, not this adapter, is most of what Mapsee asks of the host.

MEASURED 2026-10-24 (live, scratch store and cursor): 590 of the 1,866 URLs
are under the floor; the newest 500 of the other 1,276 (lastmod back to
2026-02-04) took 534 requests over two runs at one a second and held 244
upcoming sessions and 255 past ones (1 page with no Datum). The upcoming share
falls with lastmod: 64 of 69 modified in October, 93 of 132 in September, 30
of 173 in February-April. 141 sessions from 94 cafés fall inside 180 days
and 140 are placed (91 by the page's pin, 39 by the café's, 10 by a geocoded
street); the other is "Kuhstall, 7503 Samedan", which Photon answers with the
village itself (see PLACEMENT). 102 more are further out, one as far as 2036
(a typo, left for the horizon to reach); one double posting (Weesen, two
posts, one session) is written once. The café map lists 199 next dates inside
the horizon and 111 of them were written; the rest sit in the 776 older pages
the next runs read. A warm run then cost 7 requests and 0.3 minutes. Read
whole (all 1,276, in five runs), the cursor gives 218 sessions inside 180 days:
216 placed (137 by the page's pin, 63 by the café's, 16 by Photon), and
"Kuhstall" (Samedan) and "Schulhaus" (Almens) left unplaced; 176 of the café
map's 199 next dates are written.

A POST THAT LEAVES THE SITEMAP is dropped from the cursor and not written
again; it is not called off here. mapsee_prune_cancelled.py re-probes the
row's own URL and hides it on a 404, which is the evidence the notes accept.
A TITLE that calls a session off (notice_reason: "ABGESAGT", "annulé", ...) is
a tombstone for the session's own fingerprint (EventStore.cancel, notice=True).
Prose is never read as a cancellation.
A POST EDITED TO ANOTHER DATE OR TITLE has a new lastmod, so it is read again,
and a new fingerprint. The cursor keeps the key each page's row was written
under, with its day (`keys`). When the session's row is written, the old keys
go with it as legacy_fingerprints, so the sync's rekey_legacy MOVES the row to
its new date (id and /e/ link kept) instead of leaving the old one on the map
beside it (an upsert cannot delete, --only-new never touches the old row, and
prune_cancelled finds its page alive). When it is not written this run (the
new date is past or beyond the horizon, or not placed), every key for another
day is tombstoned (EventStore.cancel, not a notice) and kept as `dead`, sent
again each run until its day is past: the page's own Datum says that day is
not happening, as a JSON-LD EventRescheduled's previousStartDate does. A dead
key is never moved: rekey_legacy would carry a cancelled row onto the live key.
A new title on the same day is never a tombstone; its key waits to be moved.
A title notice's tombstone names every key.

PLACEMENT, WITH THE TOWN AS THE GUARD
-------------------------------------
The sync's geocoder is US Census, which cannot place a Swiss address, so every
row is placed here and written with coords_exact (no Census pass may move it).
The REFERENCE is the session's own postcode and town, geocoded by Photon
through mapsee_ingest_ics.make_location_geocoder (the shared cache, the
GEOCODE_MAX_NEW budget, one lookup a second) and required to be in
Switzerland/Liechtenstein. Then, first that lands within `max_km` of it:
  1. the session page's own map pin (about half the pages carry one), unless
     its Ort is its own café's address and the pin is ANOTHER café's (a
     template's pin: Le Vilebrequin in Delémont carries LARC's, 0.42 km off;
     see borrowed_pin);
  2. the café's pin from the café map (/reparieren/, 291 cafés in one page),
     when the session is at the café's own address, or its Ort gives only a
     postcode and town (an empty Ort is read as the café's own address);
  3. the session's street (or, with none, its venue's name), geocoded by
     Photon in its own postcode and town; an answer within 50 m of the
     town's point is the town, not the place (Photon, asked for a venue it
     does not know, returns what the postcode and town alone return:
     "Kuhstall, 7503 Samedan" is the village node, "Kuhstallhalle, 8048
     Zürich" Altstetten station). The town's point is the postcode-and-town
     answer and the town's own node ("Luzern" is place=city 0.48 km from
     "6003 Luzern"). A venue name is not refused outright: Photon found
     "Oberstufenzentrum Widem" (Gams), "Freizeitwerk Bleichi, Hofmattenweg"
     (Wohlen) and "Schulgebäude Platanenhof, Sonnenhofstrasse" (Oberuzwil),
     22, 21 and 377 m from their cafés' pins, and answered the other two,
     "Kuhstall" and "Schulhaus", with the village node.
Anything else is left unplaced, counted and named. Measured on the first 300
pages: page pins sat a median 0.31 km from Photon's point for their town (max
2.82), café pins 0.52 (max 3.35), geocoded streets 0.34 (max 1.00). Eschenbach
SG's correct pin is 6.1 km from it, because Photon answers with the merged
municipality, hence `pin_km` 8; a street is held to `street_km` 3, because
Photon's failure is a same-named street one village over. One Tüftelwerk
Luzern page carries the pin (85.0511, -180), the map tiles' corner, 4,596 km
from its café; the guard refuses it and the café pin (same address) places it.

WHAT A ROW SAYS
---------------
Title: the café's name, or the session's own title when that is the café's
name with something added ("Repair Café Aarau mit Kleider-Tauschbörse «Share
the Schrank»"); a bare "Repair Café", a slogan ("reparieren statt
wegwerfen"), a place ("am Standort Badenerstrasse 658") or "Reparaturtreff"
is quoted in the description instead. Fingerprint: (title, date, café and
town), so two cafés' "Repair Café" on one day in Zürich stay two rows, one
café's sessions in two towns on one day stay two (five cafés post in more than
one town: FRC Genève in Genève, Chambésy and Meyrin), and a corrected street
keeps the row. "Nur auf Voranmeldung" and its French and Italian
equivalents put "Registration required: see the event page." first. The
contact box (a volunteer's name, mobile number and e-mail) is not copied, and
phone numbers and e-mail addresses are cut out of the café's own text, with a
name left beside them ("Nur auf Voranmeldung: <number> <name>": 3 of the 26
contact lines on 2026-10-24).
Category community; the sync's market secondary already reads "repair café".

HTTP: our own User-Agent, robots.txt per host, one request per `pause`
seconds (Crawl-delay honoured if larger), a redirect only to the same site, a
401/403/429 or a bot challenge ends the run (a refusal), a network error or a
5xx costs one page and `max_failures_in_row` of them in a row end the run.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, notice_reason, strip_notice

SOURCE = "repaircafe_ch"
BASE = "https://www.repair-cafe.ch"
TZ_NAME = "Europe/Zurich"
USER_AGENT = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_MAX_MINUTES = 11.0
DEFAULT_CURSOR = "repaircafe_ch_cursor.json"
CURSOR_VERSION = 1
# Bump when parse_event_page changes what it reads: an UPCOMING session read by
# an older parser is fetched again (a few hundred pages, once); a past one is not.
PARSER_VERSION = 1
SAVE_EVERY = 50                 # pages between cursor saves, so a killed step keeps its walk
TEXT_KEEP = 1200                # characters of a café's own text kept in the cursor
# Switzerland and Liechtenstein with a margin of a few km: lat, lat, lon, lon.
CH_BOX = (45.78, 47.86, 5.90, 10.55)
# Swiss postal codes 9485-9498 are Liechtenstein's.
LI_POSTCODES = range(9485, 9499)
# Two pins this close are one pin (a page template's pin copied from another café).
SAME_PIN_KM = 0.03
# A session at its café's address whose page pin is further than this from the
# café's pin: the pins disagree, and borrowed_pin asks whose the page's is.
PINS_DISAGREE_KM = 0.2
# A geocoded street this close to the town's own point is the town's point.
TOWN_NODE_KM = 0.05
# The keys a page's row was written under before, kept per page.
LEGACY_KEEP = 8
MOVED_REASON = "moved to another date by its page"

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</li>")


def _text(s: Optional[str], br: str = " ") -> str:
    """Markup to one line of text: tags dropped, entities read, spaces folded."""
    s = _BR.sub(br, s or "")
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    if br == "\n":
        lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.split("\n")]
        return "\n".join(ln for ln in lines if ln)
    return re.sub(r"\s+", " ", s).strip()


def _fold(s: Optional[str]) -> str:
    """Lowercase, accents dropped, 'str.' read as 'strasse', punctuation as space."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"str\.(?=\s|\d|$)", "strasse", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def canon_url(url: str) -> str:
    """https://www.repair-cafe.ch/<path>/ - query and fragment dropped, so the
    café map's link and a page's link (?date=24.10.2026) are one key."""
    p = urlsplit((url or "").strip())
    path = p.path if p.path.endswith("/") else p.path + "/"
    return f"https://{p.netloc.lower()}{path}"


def km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    dy = (a[0] - b[0]) * 111.195
    dx = (a[1] - b[1]) * 111.195 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def in_box(p: Optional[Tuple[float, float]]) -> bool:
    return p is not None and CH_BOX[0] <= p[0] <= CH_BOX[1] and CH_BOX[2] <= p[1] <= CH_BOX[3]


# --------------------------------------------------------------------------- #
# Sitemaps

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")


def parse_sitemap_index(xml: str) -> List[str]:
    """The repairevent sitemaps named by Yoast's index (two on 2026-10-24)."""
    return [html_mod.unescape(u) for u in _LOC.findall(xml or "")
            if re.search(r"/repairevent-sitemap\d*\.xml$", u)]


def parse_sitemap(xml: str) -> List[Tuple[str, str]]:
    """[(event url, lastmod)] for every /repairevent/ <url>. The hreflang
    alternates inside an entry are not <loc>s and are not read."""
    out = []
    for block in re.findall(r"(?s)<url>(.*?)</url>", xml or ""):
        loc = _LOC.search(block)
        if not loc or "/repairevent/" not in loc.group(1):
            continue
        lm = _LASTMOD.search(block)
        out.append((canon_url(html_mod.unescape(loc.group(1))), lm.group(1) if lm else ""))
    return out


# --------------------------------------------------------------------------- #
# The café map (/reparieren/): every café's own pin, address and next dates

_CAFE_ITEM = re.compile(
    r'\{\s*name:\s*"((?:[^"\\]|\\.)*)",\s*coords:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\],\s*'
    r'content:\s*"((?:[^"\\]|\\.)*)",\s*logo:\s*"(?:[^"\\]|\\.)*",\s*'
    r'postcode:\s*"((?:[^"\\]|\\.)*)",\s*city:\s*"((?:[^"\\]|\\.)*)"')
# A café's page is /repaircafe/<slug>/; 16 of the 291 map entries on 2026-10-24
# are a multi-site café's /standorte/<slug>/ (Zug, Cham, Frauenfeld's OFFCUT).
_CAFE_LINK = re.compile(r"""href=['"]?(https?://www\.repair-cafe\.ch/(?:repaircafe|standorte)/[^'"\s>]+)""")
_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def _dmy(m) -> Optional[date]:
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def parse_cafe_book(page: str) -> Dict[str, Dict[str, Any]]:
    """{café url: {name, lat, lon, venue, postcode, city, next}} from the map's
    `var events = [...]`. A café with no link has no key to be found by."""
    book: Dict[str, Dict[str, Any]] = {}
    for m in _CAFE_ITEM.finditer(page or ""):
        name, lat, lon, content, pc, city = m.groups()
        link = _CAFE_LINK.search(content)
        if not link:
            continue
        lines = re.split(r"(?i)<br\s*/?>", content)
        venue = _text(lines[0])
        pcline = _text(lines[1]) if len(lines) > 1 else ""
        got = re.match(r"(\d{4})\s+(.+)", pcline)
        if not pc.strip() and got:
            pc, city = got.group(1), got.group(2)
        nxt = []
        if "Veranstaltungen" in content:
            for d in _DMY.finditer(content.split("Veranstaltungen", 1)[1]):
                day = _dmy(d)
                if day:
                    nxt.append(day.isoformat())
        try:
            point = [float(lat), float(lon)]
        except ValueError:
            point = None
        book[canon_url(link.group(1))] = {
            "name": _text(name), "pin": point, "venue": venue,
            "postcode": pc.strip(), "city": _text(city), "next": nxt}
    return book


# --------------------------------------------------------------------------- #
# A session page

_SECTION = re.compile(r'(?is)<section id="rcdetails".*?</section>')
_ORGANIZER = re.compile(r'(?is)class="eventOrganizerCafe"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
_FINDOUT = re.compile(r'(?is)id="findoutmore".*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
_TITLE = re.compile(r"(?is)</div>\s*<h2[^>]*>(.*?)</h2>")
_LABEL = re.compile(r"(?is)<strong>\s*{label}\s*:\s*(?:<br\s*/?>)?\s*</strong>(.*?)</div>")
_ORT = re.compile(r'(?is)class="cafeStreetNo"[^>]*>\s*<strong>\s*Ort\s*:\s*</strong>(.*?)</div>\s*'
                  r'<div class="cafeAddressLine"[^>]*>(.*?)</div>')
_CONTENT = re.compile(r'(?is)class="inner_rcdetails_content"[^>]*>(.*?)</div>')
_PIN = re.compile(r"L\.marker\(\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]")
_ROUTE_PIN = re.compile(r"directions\?a=null,null,(-?[\d.]+),(-?[\d.]+)")
_HM = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def _label(box: str, label: str) -> Optional[str]:
    m = re.search(_LABEL.pattern.format(label=label), box)
    return _text(m.group(1)) if m else None


def parse_time(s: Optional[str]) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """'10:00 - 12:15 Uhr' -> ((10, 0), (12, 15)). A start alone, or nothing."""
    got = []
    for m in _HM.finditer(s or ""):
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi <= 59:
            got.append((h, mi))
    if not got:
        return None, None
    start = got[0]
    end = got[1] if len(got) > 1 and got[1] > start else None
    return start, end


def parse_event_page(page: str) -> Dict[str, Any]:
    """The session's facts, or {"why": ...} when the page is not one. Pure."""
    sec = _SECTION.search(page or "")
    if not sec:
        return {"why": "no event section (the markup has probably changed)"}
    box = sec.group(0)
    when = _label(box, "Datum")
    if when is None:
        return {"why": "no Datum"}
    days = sorted({d for d in (_dmy(m) for m in _DMY.finditer(when)) if d})
    if not days:
        return {"why": "Datum unreadable"}
    if len(days) > 1:
        return {"why": "Datum spans several days"}
    start, end = parse_time(_label(box, "Uhrzeit"))
    out: Dict[str, Any] = {"date": days[0].isoformat(),
                           "start": "%02d:%02d" % start if start else None,
                           "end": "%02d:%02d" % end if end else None}
    t = _TITLE.search(box)
    out["title"] = _text(t.group(1)) if t else ""
    org = _ORGANIZER.search(box) or _FINDOUT.search(box)
    out["cafe_url"] = canon_url(org.group(1)) if org and re.search(r"/(?:repaircafe|standorte)/", org.group(1)) \
        else None
    out["cafe_name"] = _text(org.group(2)) if org else ""
    ort = _ORT.search(box)
    line1 = _text(ort.group(1), br=", ") if ort else ""
    line2 = _text(ort.group(2)) if ort else ""
    out["line1"] = re.sub(r"\s*,\s*,+", ", ", line1).strip(" ,")
    got = re.match(r"(\d{4})\s*[-–]?\s*(.+)", line2)
    pc, town = (got.group(1), got.group(2).strip()) if got else ("", line2)
    if pc:   # "9642 9642 Ebnat-Kappel", "1880 - 1880 - Bex": the postcode typed twice
        town = re.sub(rf"^(?:{pc}\s*[-–]?\s*)+", "", town).lstrip("-– ").strip()
    out["postcode"], out["town"] = pc, town
    pin = _PIN.search(page) or _ROUTE_PIN.search(box)
    try:        # "-?[\d.]+" also matches "47.19.": a pin that is no number is no pin
        out["pin"] = [float(pin.group(1)), float(pin.group(2))] if pin else None
    except ValueError:
        out["pin"] = None
    c = _CONTENT.search(box)
    out["text"] = _text(c.group(1), br="\n") if c else ""
    return out


# --------------------------------------------------------------------------- #
# Address parts

_STREET_TAIL = re.compile(
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.\- ]*?\s\d{1,4}\s?[A-Za-z]?(?:\s?[/-]\s?\d{1,4}[A-Za-z]?)?)(?=$|[\s,;/()])")
_STREET_HEAD = re.compile(
    r"^\d{1,4}[A-Za-z]?,?\s+(?:rue|chemin|ch\.|avenue|av\.|route|rte|place|pl\.|boulevard|bd|quai|"
    r"ruelle|impasse|all[ée]e|passage|sentier|via|viale|piazza|vicolo|strada)\b.*", re.I)
_RANGE = re.compile(r"(\d{1,4}[A-Za-z]?)\s?[/-]\s?\d{1,4}[A-Za-z]?")


def first_number(street: str) -> str:
    """'Therwilerstrasse 16/18' -> 'Therwilerstrasse 16'."""
    return _RANGE.sub(r"\1", street or "")


def split_line(line1: str) -> Tuple[Optional[str], Optional[str]]:
    """'Parktheater Grenchen, Lindenstrasse 41' -> ('Parktheater Grenchen',
    'Lindenstrasse 41'); 'Bernstrasse 9  Nouvo' -> ('Nouvo', 'Bernstrasse 9');
    'Werkraum Primarschulhaus' -> ('Werkraum Primarschulhaus', None)."""
    segs = [s.strip() for s in re.split(r",|;|\s/\s", line1 or "") if s.strip()]
    for i in range(len(segs) - 1, -1, -1):
        seg = segs[i]
        head = _STREET_HEAD.match(seg)
        m = None if head else _STREET_TAIL.search(seg)
        if not head and not m:
            continue
        street = seg if head else m.group(1).strip()
        rest = "" if head else (seg[:m.start()] + " " + seg[m.end():]).strip()
        venue = ", ".join([s for s in segs[:i] + segs[i + 1:]] + ([rest] if rest else []))
        return (re.sub(r"\s+", " ", venue).strip(" ,") or None), re.sub(r"\s+", " ", street)
    return (re.sub(r"\s+", " ", ", ".join(segs)) or None), None


def same_place(line1: str, cafe_venue: str) -> bool:
    """Is the session at its café's own address? Streets, when both name one,
    must agree to the house number ('Therwilerstrasse 18' is not '16');
    otherwise one place name must contain the other."""
    a, b = _fold(line1), _fold(cafe_venue)
    if not a or not b:
        return False
    sa, sb = split_line(line1)[1], split_line(cafe_venue)[1]
    if sa and sb:
        return _fold(first_number(sa)) == _fold(first_number(sb))
    return a in b or b in a


# --------------------------------------------------------------------------- #
# Placement

def country_of(postcode: str) -> str:
    return "LI" if postcode.isdigit() and int(postcode) in LI_POSTCODES else "CH"


_COUNTRY_WORD = {"CH": "Schweiz", "LI": "Liechtenstein"}

# Geocode = (query, suffix) -> (lat, lon) | None
Geocode = Callable[[str, str], Optional[Tuple[float, float]]]


def borrowed_pin(pin: List[float], line1: str, cafe: Optional[Dict[str, Any]],
                 others: Optional[Iterable[Dict[str, Any]]]) -> bool:
    """Is the session page's pin ANOTHER café's, copied with a page template,
    while its Ort says the session is at its own café's address? Measured
    2026-10-24 on the 160 upcoming pages that carry a pin and whose café has
    one: 7 sessions at their own café's address carry a pin more than
    `PINS_DISAGREE_KM` from the café's, and only one of those pins is another
    café's - Le Vilebrequin in Delémont (Rue Emile-Boéchat 34) carrying the
    pin of LARC (Passage des Ponts 2), 0.42 km off, as its past pages do too.
    Photon puts no. 34 30 m from the Vilebrequin café's pin and 402 m from the
    page's. The other six (Hinwil 0.37 km five times, Stans 0.22 km) are not
    another café's pin, and Hinwil's page pin is the right one (25 m from
    Photon's no. 17, the café pin 353 m), so a disagreement alone settles
    nothing; a pin that is another café's settles it."""
    if not (pin and cafe and cafe.get("pin") and line1 and others):
        return False
    if not same_place(line1, cafe.get("venue") or ""):
        return False
    p = (float(pin[0]), float(pin[1]))
    if km(p, tuple(cafe["pin"])) <= PINS_DISAGREE_KM:
        return False
    for other in others:
        op = other.get("pin")
        if op and km(p, tuple(op)) <= SAME_PIN_KM and not same_place(line1, other.get("venue") or ""):
            return True
    return False


def place(page: Dict[str, Any], cafe: Optional[Dict[str, Any]], geocode: Geocode,
          pin_km: float, street_km: float, notes: Optional[Dict[str, int]] = None,
          others: Optional[Iterable[Dict[str, Any]]] = None
          ) -> Tuple[Optional[Tuple[float, float]], str]:
    """((lat, lon), how) or (None, why not). A publisher's pin (the page's or
    the café's) must lie within `pin_km` of the session's own postcode and
    town as Photon places them, a geocoded street within `street_km`: the
    publisher's failure is a pin in the wrong country or at the map's corner,
    Photon's a same-named street one village over (every village has a
    Dorfstrasse), so the street is held closer. `others` is the café map's
    every café, so a page pin that is another café's is known for one
    (borrowed_pin). A geocoded answer within `TOWN_NODE_KM` of the town's
    point is the town, not the street: Photon, asked for a venue it does not
    know, answers with the place it was told it is in. The town's point is
    both the postcode-and-town answer and the town's own node ("Samedan,
    Schweiz"), which in a city lies apart from the postcode's; a town Photon
    cannot find alone (or finds abroad) leaves the first. Measured on the
    whole 2026-10-24 cursor: the 16 street and venue placements lie 0.14 to
    1.2 km from their town's own node, so the rule costs none of them."""
    def note(k: str) -> None:
        if notes is not None:
            notes[k] = notes.get(k, 0) + 1

    pc, town = page.get("postcode") or "", page.get("town") or ""
    if not town:
        return None, "no town in Ort"
    word = _COUNTRY_WORD[country_of(pc)]
    ref = geocode(f"{pc} {town}".strip(), f", {word}")
    if ref is None:
        return None, "town not found by the geocoder"
    if not in_box(ref):
        return None, "the geocoder put the town outside Switzerland"

    def near(p, limit: float) -> bool:
        return p is not None and in_box(tuple(p)) and km(tuple(p), ref) <= limit

    towns: List[Tuple[float, float]] = [ref]

    def is_town(p: Tuple[float, float]) -> bool:
        # The town's own node is asked for only when an answer would be
        # kept, and once per town for ever (the shared cache keeps hits).
        if len(towns) == 1 and pc:
            alone = geocode(town, f", {word}")
            towns.append(alone if alone is not None and in_box(alone) else ref)
        return any(km(p, t) <= TOWN_NODE_KM for t in towns)

    line1 = page.get("line1") or ""
    street = split_line(line1)[1]
    pin = page.get("pin")
    if pin:
        if not near(pin, pin_km):
            note("event page's own pin is not in its town (not used)")
        elif borrowed_pin(pin, line1, cafe, others):
            note("event page's pin is another café's, its Ort its own café's (not used)")
        else:
            return (pin[0], pin[1]), "event pin"
    if cafe and cafe.get("pin"):
        same_town = (cafe.get("postcode") == pc) if (pc and cafe.get("postcode")) else \
            (_fold(cafe.get("city")) == _fold(town))
        if same_town and (not line1 or same_place(line1, cafe.get("venue") or "")):
            if near(cafe["pin"], pin_km):
                return (cafe["pin"][0], cafe["pin"][1]), "café pin"
            note("café pin is not in its town (not used)")
    if line1:
        suffix = f", {pc} {town}, {word}".replace(" ,", ",").replace("  ", " ")
        tried = [line1]
        for q in (street, first_number(street or "")):
            if q and all(_fold(q) != _fold(t) for t in tried):
                tried.append(q)
        for q in tried:
            p = geocode(q, suffix)
            if p is None:
                continue
            if not near(p, street_km):
                note("the geocoder answered outside the town (not used)")
                continue
            if is_town(tuple(p)):
                # Measured 2026-10-24: "Kuhstall, 7503 Samedan", "Kuhstallhalle,
                # 8048 Zürich" and "Kuhstall, 1205 Genève" each came back as
                # exactly what the postcode and town alone return (a village
                # node, Altstetten station, a library): the town, not the venue.
                # In a city the town's own node is not the postcode's answer
                # ("Luzern" 0.48 km from "6003 Luzern", "Genève" 0.34 km from
                # "1205 Genève"), and is no venue either.
                note("the geocoder answered with the town itself (not used)")
                continue
            return (p[0], p[1]), "geocoded street"
        return None, "street not found in its town"
    return None, "no street, and not at its café's address"


# --------------------------------------------------------------------------- #
# Rows

def event_name(title: str, cafe_name: str) -> str:
    """The café's name, unless the session's own title is that name with
    something added ("Repair Café Aarau mit Kleider-Tauschbörse «Share the
    Schrank»", "11. Repair Café Fraubrunnen"). A bare "Repair Café", a slogan
    ("reparieren statt wegwerfen": 3 cafés' titles in the first 300 pages), a
    place ("am Standort Badenerstrasse 658") or "Reparaturtreff" names nothing
    a map can show; it is quoted in the description instead."""
    t, c = (title or "").strip(), (cafe_name or "").strip()
    if not c:
        return t
    if t and len(t) <= 90 and _fold(c) and f" {_fold(c)} " in f" {_fold(t)} ":
        return t
    return c


_PHONE = re.compile(r"(?:\+|00)?(?:41|423)?[\s/.-]?\(?0?\d{2}\)?[\s/.-]?\d{3}[\s/.-]?\d{2}[\s/.-]?\d{2}\b")
# "a.muster @ epost.ch" and "name(at)host.ch" are addresses too.
_MAIL = re.compile(r"[\w.+-]+\s?(?:@|\(at\)|\[at\])\s?[\w-]+(?:\.[\w-]+)+", re.I)
_REG_NOT = re.compile(
    r"\b(?:keine|ohne)\s+(?:vor)?anmeldung|\b(?:vor)?anmeldung\s+(?:ist\s+)?(?:nicht|kein\w*)\s+"
    r"(?:n[öo]tig|notwendig|erforderlich)|\bsans\s+inscription|\bpas\s+(?:besoin\s+)?d['’]inscription|"
    r"\binscription\s+(?:pas|non)\s+(?:n[ée]cessaire|obligatoire)|\bsenza\s+(?:iscrizione|prenotazione)",
    re.I)
_REG = re.compile(
    r"\bnur\s+(?:auf|mit|nach)\s+(?:vor)?anmeldung|\b(?:vor)?anmeldung\s+(?:ist\s+)?(?:erforderlich|"
    r"obligatorisch|notwendig|n[öo]tig|zwingend)|\bbitte\s+(?:vorher\s+|vorg[äa]ngig\s+)?(?:vor)?anmelden|"
    r"\binscription\s+(?:obligatoire|requise|n[ée]cessaire)|\bsur\s+inscription|"
    r"\bil\s+faut\s+s['’]inscrire|\biscrizione\s+obbligatoria|\bsu\s+prenotazione|"
    r"\bprenotazione\s+obbligatoria", re.I)


def registration_required(text: str) -> bool:
    return bool(_REG.search(_REG_NOT.sub(" ", text or "")))


# A person's name: two or three capitalised words ("Anna Muster", "Anne-Marie
# de Wolf" is not caught, and need not be: it is only looked for on a line
# whose number or address was just cut).
_NAME_WORD = r"[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’]+(?:-[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’]+)?"
_PERSON = re.compile(rf"(?:(?P<label>[^:]{{1,60}}):)?\s*{_NAME_WORD}(?:\s+{_NAME_WORD}){{1,2}}")
_DANGLING = re.compile(r"(?:[\s,;:.\-–]|\b(?:oder|und|or|ou|et|o)\b)+$", re.I)


def _uncontact(line: str) -> str:
    """One line without its phone numbers and e-mail addresses, and without
    the name they belonged to. Measured 2026-10-24 on the 321 upcoming pages:
    26 lines held a contact, and in 3 a volunteer's name was all that was
    left of it ("Nur auf Voranmeldung: <number> <name>", "<name>: <number>",
    "<name>, <number> oder <number>"); a label left alone ("Email:") says
    nothing either."""
    cut = _PHONE.sub("", _MAIL.sub("", line))
    if cut == line:
        return line
    probe = _DANGLING.sub("", cut).strip()
    person = _PERSON.fullmatch(probe)
    if person:
        return (person.group("label") or "").strip()
    if re.fullmatch(r"[\w’'.-]+", probe):
        return ""
    return cut


def clean_text(text: str, limit: int) -> str:
    """The café's own words without phone numbers, e-mail addresses or the
    names that went with them."""
    t = "\n".join(_uncontact(ln) for ln in (text or "").split("\n"))
    t = _MAIL.sub("", t)
    t = _PHONE.sub("", t)
    t = re.sub(r"[ \t]+([:,.;])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"(?m)^[\s:,;.\-–]+$", "", t)
    t = re.sub(r"\n{2,}", "\n", t).strip()
    if len(t) > limit:
        cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
        t = cut + " …"
    return t


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:      # pragma: no cover - zoneinfo ships with 3.9+
        return None


def localize(d: date, hm: Optional[str]) -> Tuple[str, Optional[str]]:
    """(local ISO, UTC ISO). No clock -> a bare date and no instant."""
    if not hm:
        return d.isoformat(), None
    naive = datetime(d.year, d.month, d.day, int(hm[:2]), int(hm[3:5]))
    tz = _tz()
    if tz is None:
        return naive.isoformat(), None
    dt = naive.replace(tzinfo=tz)
    return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_id_of(url: str) -> str:
    return urlsplit(url).path.strip("/")           # "repairevent/repair-cafe-murten-morat-4"


def build_event(url: str, page: Dict[str, Any], cafe: Optional[Dict[str, Any]],
                point: Optional[Tuple[float, float]], site: Dict[str, Any],
                title: Optional[str] = None) -> NormalizedEvent:
    """The row for one session. `title` overrides the page's (a notice's
    stripped title). `point` None builds the row for a tombstone only."""
    cafe_name = page.get("cafe_name") or (cafe or {}).get("name") or ""
    raw_title = page.get("title") if title is None else title
    name = event_name(raw_title or "", cafe_name)
    d = date.fromisoformat(page["date"])
    start_local, start_utc = localize(d, page.get("start"))
    end_local = end_utc = None
    if page.get("start") and page.get("end"):
        end_local, end_utc = localize(d, page["end"])
    venue, street = split_line(page.get("line1") or "")
    parts: List[str] = []
    text = page.get("text") or ""
    if registration_required(f"{raw_title}\n{text}"):
        parts.append("Registration required: see the event page.")
    if raw_title and _fold(raw_title) != _fold(name) and _fold(raw_title) not in _fold(page.get("line1")):
        parts.append(f"«{raw_title.strip()}»")
    body = clean_text(text, int(site.get("description_chars", 700)))
    if body:
        parts.append(body)
    pc, town = page.get("postcode") or "", page.get("town") or ""
    # The café AND the town: make_fingerprint reads its city argument only when
    # the place is empty, so a town passed only there would never count. FRC
    # Genève posts sessions in Genève, Chambésy and Meyrin under one organiser
    # link; two of them on one Saturday are two rows.
    fp_place = " ".join(x for x in (cafe_name or venue or page.get("line1") or "", town) if x)
    ev = NormalizedEvent(
        source=SOURCE,
        source_id=source_id_of(url),
        name=name,
        description="\n\n".join(parts) or None,
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=TZ_NAME,
        venue_name=venue or cafe_name or None,
        address=street,                            # "Matthäuszentrum" is the venue, not a street
        city=town or None,
        country=country_of(pc),
        postal_code=pc or None,
        latitude=point[0] if point else None,
        longitude=point[1] if point else None,
        # US Census is the sync's only geocoder; for a Swiss street it can
        # only be wrong. Every point here is the publisher's or a guarded Photon
        # answer in the session's own town.
        coords_exact=True,
        category=site.get("category", "community"),
        promoter=cafe_name or None,
        ticket_url=url,
    )
    ev.fingerprint = make_fingerprint(name, start_local[:10], fp_place, town)
    return ev


# --------------------------------------------------------------------------- #
# The cursor

def load_cursor(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("version") == CURSOR_VERSION and \
                isinstance(data.get("pages"), dict):
            data.setdefault("cafes", {})
            return data
    except Exception:            # noqa: BLE001 - a missing or broken cursor is a cold start
        pass
    return {"version": CURSOR_VERSION, "pages": {}, "cafes": {}}


def save_cursor(path: str, cursor: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cursor, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def select_pages(rows: List[Tuple[str, str]], pages: Dict[str, Dict[str, Any]], today: date,
                 floor_days: int, recheck_days: int) -> Tuple[List[str], Dict[str, int]]:
    """Which session pages to fetch this run, newest lastmod first, and why
    the rest are not fetched. Never fetches a page twice for one lastmod,
    except one that would not parse, again after `recheck_days`."""
    floor = (today - timedelta(days=floor_days)).isoformat()
    recheck = (today - timedelta(days=recheck_days)).isoformat()
    todo: List[Tuple[str, str]] = []
    counts = {"known, unchanged": 0, "lastmod before the floor, never fetched": 0,
              "new or changed": 0, "upcoming, read by an older parser": 0}
    for url, lm in rows:
        seen = pages.get(url)
        if seen is not None and seen.get("page") and seen.get("v", 1) != PARSER_VERSION and \
                (seen.get("date") or "") >= today.isoformat():
            counts["upcoming, read by an older parser"] += 1
            todo.append((lm, url))
            continue
        if seen is not None and seen.get("lastmod") == lm and \
                not (seen.get("why") and (seen.get("checked") or "") < recheck):
            counts["known, unchanged"] += 1
            continue
        if seen is None and lm and lm[:10] < floor:
            counts["lastmod before the floor, never fetched"] += 1
            continue
        counts["new or changed"] += 1
        todo.append((lm, url))
    todo.sort(reverse=True)
    return [u for _lm, u in todo], counts


def remember(pages: Dict[str, Dict[str, Any]], url: str, lastmod: str, parsed: Dict[str, Any],
             today: date) -> None:
    """A past session keeps its date only; an upcoming one its whole page. Both
    keep the keys their rows were written under (`keys`, `dead`; see identity
    and retire_moved), so a re-dated or re-titled session's old row is moved
    or called off, never left beside the new one."""
    old = pages.get(url) or {}
    entry: Dict[str, Any] = {"lastmod": lastmod, "checked": today.isoformat()}
    if parsed.get("why"):
        entry["why"] = parsed["why"]
    else:
        entry["date"] = parsed["date"]
        if parsed["date"] >= today.isoformat():
            # The café's text is cut to what a row can use, so the cursor
            # (an Actions cache file) stays small: ~1.4 KB a session.
            entry["page"] = dict(parsed, text=(parsed.get("text") or "")[:TEXT_KEEP])
            entry["v"] = PARSER_VERSION
    for k in ("keys", "dead"):
        if old.get(k):       # kept through a failed parse too: its row is still there
            entry[k] = old[k]
    pages[url] = entry


def identity(entry: Dict[str, Any], fp: str, day: str, name: str, record: bool = True) -> List[str]:
    """The keys this page's row was written under before, other than `fp`,
    for NormalizedEvent.legacy_fingerprints: the sync's rekey_legacy then
    MOVES the row to `fp` (its id, /e/ link and any claim kept) instead of
    inserting a second one beside it. The fingerprint is (title, day, café,
    town), so a café that edits its post's date or title gives the session a
    new one; without this the row on the old date stays on the map (an upsert
    cannot delete, --only-new never touches it, and prune_cancelled finds the
    page alive). `keys` is [[fingerprint, day, name], ...], the newest last.
    `record` notes `fp` as the row's key now; a tombstone does not."""
    keys = [k for k in entry.get("keys") or [] if k[0] != fp]
    olds = [k[0] for k in keys][-LEGACY_KEEP:]
    if record:
        entry["keys"] = keys[-LEGACY_KEEP:] + [[fp, day, name]]
        dead = [k for k in entry.get("dead") or [] if k[0] != fp]
        if dead:
            entry["dead"] = dead
        else:
            entry.pop("dead", None)
    return olds


def retire_moved(entry: Dict[str, Any], day: str) -> int:
    """The page now names `day` and its row was not written this run (the new
    date is past, beyond the horizon, or not placed): every key written for
    ANOTHER day becomes `dead`, a tombstone for its own row. The page's edited
    Datum is the publisher saying that day is not happening, as a JSON-LD
    EventRescheduled's previousStartDate is (cancelled-events.md). A dead key
    is never sent as a legacy one again: rekey_legacy would carry a cancelled
    row onto the live key, and only a source read whole may lift it. A key for
    the same day (a new title, not a new date) waits for its row to move."""
    keys = entry.get("keys") or []
    off = [k for k in keys if k[1] != day]
    if not off:
        return 0
    stay = [k for k in keys if k[1] == day]
    if stay:
        entry["keys"] = stay
    else:
        entry.pop("keys", None)
    dead = {k[0]: k for k in entry.get("dead") or []}
    dead.update({k[0]: k for k in off})
    entry["dead"] = list(dead.values())
    return len(off)


def moved_tombstone(url: str, key: List[str], site: Dict[str, Any]) -> NormalizedEvent:
    """The record a dead key's row was: same source, source_id, link and
    fingerprint (the only thing the sync finds the row by)."""
    fp, day, name = key[0], key[1], (key[2] if len(key) > 2 else "")
    ev = NormalizedEvent(source=SOURCE, source_id=source_id_of(url), name=name or "",
                         start_local=day, timezone=TZ_NAME, coords_exact=True,
                         category=site.get("category", "community"), ticket_url=url)
    ev.fingerprint = fp
    return ev


# --------------------------------------------------------------------------- #
# HTTP

class Refused(Exception):
    """The host said no (401/403/429, a challenge, robots.txt). Ends the run."""


class Stop(Exception):
    """The run deadline passed, or the host stopped answering."""


class Client:
    """One host, paced, robots-checked, deadline-aware (mapsee_ingest_glenecho's
    rules). `get` returns a 2xx body, None for a page worth skipping, raises
    Refused for a refusal and Stop at the deadline or after `max_failures`
    failed requests in a row."""

    def __init__(self, session, pause: float, deadline: Optional[float], robots=None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Optional[Callable[[float], None]] = None, max_failures: int = 5):
        self.session, self.pause, self.deadline = session, max(1.0, pause), deadline
        # time.sleep looked up now, not when the module loaded, so a harness
        # that stubs it (test_ingest_repaircafe_ch's main() runs) is obeyed.
        self.clock, self.sleep, self.robots = clock, sleep or time.sleep, robots
        self.requests = self.errors = 0
        self.max_failures = max_failures
        self._in_row = 0
        self._last: Optional[float] = None

    def _failed(self, why: str) -> None:
        self._in_row += 1
        if self._in_row >= self.max_failures:
            raise Stop(f"{self._in_row} requests in a row failed (last: {why}) - the host is not answering")

    def _allowed(self, url: str) -> None:
        if self.robots is None:
            return
        v = self.robots.check(url)
        if v.get("allowed") is not True:
            raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url}")
        cd = v.get("crawl_delay")
        if cd and cd > self.pause:
            self.pause = float(cd)

    def get(self, url: str, deadline: Optional[float] = None) -> Optional[str]:
        from robots_txt import CHALLENGE_RX
        stop_at = deadline if deadline is not None else self.deadline
        host = urlsplit(url).netloc
        for _hop in range(4):
            if stop_at is not None and self.clock() >= stop_at:
                raise Stop("run deadline reached")
            self._allowed(url)
            if self._last is not None:
                wait = self.pause - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
                    if stop_at is not None and self.clock() >= stop_at:
                        raise Stop("run deadline reached")     # no request STARTS after it
            self._last = self.clock()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=40, allow_redirects=False)
            except OSError as exc:    # requests.RequestException is an OSError
                self.errors += 1
                print(f"[{SOURCE}] network error, page skipped: {type(exc).__name__}: {exc} - {url}")
                self._failed(type(exc).__name__)
                return None
            code = r.status_code
            if code in (301, 302, 303, 307, 308):
                nxt = urljoin(url, r.headers.get("Location") or "")
                if urlsplit(nxt).netloc.removeprefix("www.") != host.removeprefix("www."):
                    print(f"[{SOURCE}] redirect off the site, not followed: {url} -> {nxt}")
                    return None
                url = nxt
                continue
            if code in (401, 403, 429):
                raise Refused(f"HTTP {code} for {url}")
            text = r.text or ""
            if CHALLENGE_RX.search(text[:6000]):
                raise Refused(f"bot challenge for {url}")
            if not 200 <= code < 300:
                print(f"[{SOURCE}] HTTP {code}: {url}")
                if code >= 500:
                    self._failed(f"HTTP {code}")
                else:
                    self._in_row = 0
                return None
            self._in_row = 0
            return text
        print(f"[{SOURCE}] too many redirects: {url}")
        return None


# --------------------------------------------------------------------------- #
# One run

def _count(d: Dict[str, int], k: str, n: int = 1) -> None:
    d[k] = d.get(k, 0) + n


def read_site(site: Dict[str, Any], client: Client, cursor: Dict[str, Any], today: date,
              geocode: Geocode, emit: Callable[[NormalizedEvent], None],
              cancel: Optional[Callable[[NormalizedEvent, str, bool], Any]] = None,
              max_pages: Optional[int] = None, fetch_until: Optional[float] = None,
              save: Optional[Callable[[], None]] = None,
              geocode_unasked: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Sitemaps -> new or changed session pages -> the cursor -> rows. Never
    raises on content; returns the report. `cancel(ev, reason, notice)`
    tombstones a row: notice=True for a title that calls the session off,
    False for a date the page has left."""
    base = site.get("base", BASE).rstrip("/")
    pages: Dict[str, Dict[str, Any]] = cursor.setdefault("pages", {})
    horizon = today + timedelta(days=int(site.get("within_days", 180)))
    rep: Dict[str, Any] = {"sitemap_urls": 0, "fetched": 0, "kept": 0, "cancelled": 0, "moved_off": 0,
                           "placed_by": {}, "unplaced": {}, "refused": {}, "notes": {},
                           "selection": {}, "stopped": None, "map_dates": None}
    notes = rep["notes"]
    rows: Optional[List[Tuple[str, str]]] = None
    try:
        # 1. The index. A sitemap not read leaves the cursor as it was.
        idx = client.get(site.get("sitemap_index", f"{base}/sitemap_index.xml"))
        maps = parse_sitemap_index(idx or "")
        if idx is not None and not maps:
            _count(notes, "sitemap index names no repairevent sitemap (the markup has probably changed)")
        got: List[Tuple[str, str]] = []
        complete = bool(maps)
        for sm in maps:
            xml = client.get(sm)
            if xml is None:
                complete = False
                _count(notes, "repairevent sitemap not read")
                continue
            got.extend(parse_sitemap(xml))
        if complete:
            rows = got
        elif got:
            _count(notes, "sitemap read in part: no post is dropped this run")
        rep["sitemap_urls"] = len(got)

        # 2. The café map: every café's pin. The last good one is kept.
        page = client.get(site.get("cafe_map", f"{base}/reparieren/"))
        book = parse_cafe_book(page or "")
        if book:
            cursor["cafes"] = {"read": today.isoformat(), "book": book}
        else:
            _count(notes, "café map not read: the last one is used")
            book = (cursor.get("cafes") or {}).get("book") or {}
        rep["cafes"] = len(book)

        # 3. New and changed pages, newest first, until the fetch budget.
        if got:
            todo, sel = select_pages(got, pages, today, int(site.get("lastmod_floor_days", 548)),
                                     int(site.get("recheck_unparsed_days", 30)))
            rep["selection"] = sel
            cap = max_pages if max_pages is not None else int(site.get("max_pages_per_run", 600))
            if len(todo) > cap:
                _count(notes, "new or changed pages left for the next run (max pages)", len(todo) - cap)
                todo = todo[:cap]
            lastmods = dict(got)
            for i, url in enumerate(todo):
                try:
                    body = client.get(url, deadline=fetch_until)
                except Stop as exc:
                    why = "deadline" if "deadline" in str(exc) else "the host stopped answering"
                    _count(notes, f"new or changed pages left for the next run ({why})", len(todo) - i)
                    rep["stopped"] = str(exc)
                    break
                if body is None:
                    _count(notes, "session page not read (retried next run)")
                    continue
                parsed = parse_event_page(body)
                rep["fetched"] += 1
                if parsed.get("why"):
                    _count(rep["refused"], parsed["why"])
                    print(f"[{SOURCE}] {parsed['why']}: {url}")
                remember(pages, url, lastmods.get(url, ""), parsed, today)
                if save is not None and rep["fetched"] % SAVE_EVERY == 0:
                    save()
        if rows is not None:
            live = {u for u, _lm in rows}
            gone = [u for u in pages if u not in live]
            for u in gone:
                if (pages[u].get("date") or "") >= today.isoformat():
                    _count(notes, "upcoming post gone from the sitemap (prune_cancelled re-probes its row)")
                del pages[u]
    except Refused as exc:
        rep["stopped"] = f"REFUSED - {exc}"
        return rep                     # a refusal: nothing is written this run
    except Stop as exc:
        rep["stopped"] = str(exc)

    # 4. Rows, from the cursor: every upcoming session inside the horizon.
    book = (cursor.get("cafes") or {}).get("book") or {}
    lo, hi = today.isoformat(), horizon.isoformat()
    seen_fp: Dict[str, str] = {}
    kept_dates = set()

    def write(url: str, entry: Dict[str, Any]) -> bool:
        """Write (or call off by its title) the page's session. True when the
        page's own row was emitted or tombstoned this run."""
        pg = entry.get("page")
        if not pg or not (lo <= pg["date"] <= hi):
            if pg and pg["date"] > hi:
                _count(notes, "upcoming, beyond the horizon (written when it comes inside)")
            return False
        cafe = book.get(pg.get("cafe_url") or "")
        if pg.get("cafe_url") and cafe is None:
            _count(notes, "café not on the café map")
        if not (pg.get("line1") or pg.get("postcode") or pg.get("town")) and cafe and cafe.get("city"):
            # An empty Ort is the café's own place (the café map's address),
            # as a source's venue block is for a LOCATION-less event.
            pg = dict(pg, line1=cafe.get("venue") or "", postcode=cafe.get("postcode") or "",
                      town=cafe["city"])
            _count(notes, "no Ort on the page: the café's own address")
        if notice_reason(pg.get("title")) == "cancelled in the title":
            # The café's own title says the session is off: a tombstone for the
            # row the live title wrote (same café, day and town), never a row.
            live_title = strip_notice(pg.get("title"))
            ev = build_event(url, pg, cafe, None, site, title=live_title or "")
            # The row may be filed under what the page said before (another
            # date or title): the tombstone names those keys too.
            ev.legacy_fingerprints = identity(entry, ev.fingerprint, pg["date"], ev.name, record=False)
            if cancel is not None:
                cancel(ev, (pg.get("title") or "")[:80], True)
            rep["cancelled"] += 1
            return True
        point, how = place(pg, cafe, geocode, float(site.get("pin_km", 8.0)),
                           float(site.get("street_km", 3.0)), notes, others=book.values())
        if point is None:
            if how in ("town not found by the geocoder", "street not found in its town") and \
                    geocode_unasked is not None and geocode_unasked():
                how = "not geocoded this run (budget or deadline): retried next run"
            _count(rep["unplaced"], how)
            print(f"[{SOURCE}] unplaced ({how}): {pg.get('line1')!r}, {pg.get('postcode')} "
                  f"{pg.get('town')} - {url}")
            return False
        ev = build_event(url, pg, cafe, point, site)
        if ev.fingerprint in seen_fp:
            _count(notes, "two posts, one café, one day (the first is kept)")
            print(f"[{SOURCE}] same title, café, town and day as {seen_fp[ev.fingerprint]}: {url} - not written twice")
            return False
        seen_fp[ev.fingerprint] = url
        ev.legacy_fingerprints = identity(entry, ev.fingerprint, pg["date"], ev.name)
        if ev.legacy_fingerprints:
            _count(notes, "re-dated or re-titled since its row was written: "
                          "legacy fingerprint sent (the sync moves the row)")
        _count(rep["placed_by"], how)
        rep["kept"] += 1
        if pg.get("cafe_url"):
            kept_dates.add((pg["cafe_url"], pg["date"]))
        emit(ev)
        return True

    dead_keys: List[Tuple[str, List[str]]] = []
    for url in sorted(pages):
        entry = pages[url]
        day = entry.get("date")
        if not write(url, entry) and day and retire_moved(entry, day):
            _count(notes, "re-dated, its new date not written this run: the old date's row called off")
        if day and day < lo:
            entry.pop("keys", None)              # a past session's row moves nowhere now
        dead = [k for k in entry.get("dead") or [] if k[1] >= lo]
        if dead:
            entry["dead"] = dead
            dead_keys.extend((url, k) for k in dead)
        else:
            entry.pop("dead", None)
    # Every run while the old day is to come, so a sync that failed once is not
    # the end of it; never a key some post is written under this run.
    for url, k in dead_keys:
        if k[0] in seen_fp:
            continue
        if cancel is not None:
            cancel(moved_tombstone(url, k, site), MOVED_REASON, False)
        rep["moved_off"] += 1

    # 5. How much of what the café map says is coming did we read?
    want = {(u, d) for u, c in book.items() for d in c.get("next") or [] if lo <= d <= hi}
    if want:
        rep["map_dates"] = (len(want & kept_dates), len(want))
    return rep


def _report(rep: Dict[str, Any]) -> None:
    placed = ", ".join(f"{n} by {w}" for w, n in sorted(rep["placed_by"].items(), key=lambda kv: -kv[1]))
    unplaced = sum(rep["unplaced"].values())
    print(f"[{SOURCE}] sitemaps list {rep['sitemap_urls']} session pages; fetched {rep['fetched']} "
          f"this run; kept {rep['kept']} upcoming session(s) ({placed or 'none placed'}); "
          f"unplaced {unplaced}; cancelled {rep['cancelled']} by their title, "
          f"{rep.get('moved_off', 0)} old date(s) of a re-dated session", flush=True)
    for w, n in sorted(rep["selection"].items()):
        print(f"[{SOURCE}] selection: {w}: {n}", flush=True)
    for bucket in ("unplaced", "refused", "notes"):
        for w, n in sorted(rep[bucket].items(), key=lambda kv: -kv[1]):
            print(f"[{SOURCE}] {bucket}: {w}: {n}", flush=True)
    if rep.get("map_dates"):
        hit, want = rep["map_dates"]
        print(f"[{SOURCE}] the café map's next dates inside the horizon: {hit} of {want} written", flush=True)
    if rep.get("stopped"):
        print(f"[{SOURCE}] STOPPED - {rep['stopped']}", flush=True)


def _today_local() -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    tz = _tz()
    return datetime.now(tz).date() if tz else date.today()


def _budget_spent() -> bool:
    """True when this run's cap on NEW Photon lookups (mapsee_geo_budget) is
    used up (mapsee_ingest_revize's helper)."""
    try:
        import mapsee_geo_budget as gb
        if gb._MAX <= 0:
            return False
        with open(gb._FILE, encoding="utf-8") as fh:
            return int(json.load(fh).get("n", 0)) >= gb._MAX
    except Exception:  # noqa: BLE001 - no file yet: nothing spent
        return False


def make_geocode(session, deadline: Optional[float], clock: Callable[[], float] = time.monotonic):
    """mapsee_ingest_ics's geocoder (its cache, its GEOCODE_MAX_NEW budget, its
    one lookup a second), one instance per suffix. Past the deadline only a
    cached answer is given: no lookup starts after it. Returns (geocode,
    unasked) where unasked() says a miss may have been a question not asked."""
    import mapsee_ingest_ics as ics
    made: Dict[str, Callable[[str], Tuple[Any, Any]]] = {}
    skipped = [0]

    def geocode(query: str, suffix: str) -> Optional[Tuple[float, float]]:
        if deadline is not None and clock() >= deadline and \
                (query + "|" + suffix).strip().lower() not in ics._GEO_CACHE:   # ics's own cache key
            skipped[0] += 1
            return None
        g = made.get(suffix)
        if g is None:
            g = made[suffix] = ics.make_location_geocoder(session, suffix)
        lat, lon = g(query)
        return (float(lat), float(lon)) if lat is not None and lon is not None else None

    return geocode, (lambda: skipped[0] > 0 or _budget_spent())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Swiss repair-café sessions from repair-cafe.ch.")
    ap.add_argument("--config", default="repaircafe_ch_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--cursor", default=DEFAULT_CURSOR)
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="at most this many session pages fetched this run (config max_pages_per_run)")
    ap.add_argument("--dry-run", action="store_true", help="read and report; write no store, cursor or cache")
    ap.add_argument("--dump", help="also write the rows to this JSON file")
    a = ap.parse_args(argv)

    try:
        import requests
    except ImportError:  # pragma: no cover
        sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
    from robots_txt import Robots

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    store = None if a.dry_run else EventStore(a.store)
    today = _today_local()
    total, failed, sites, dumped = 0, 0, 0, []
    for site in cfg.get("sites", []):
        if site.get("skip"):
            continue
        sites += 1
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT,
                                "Accept": "text/html,application/xhtml+xml,application/xml",
                                "Accept-Language": "de-CH,de;q=0.9"})
        client = Client(session, float(site.get("pause", 1.0)), deadline, robots=Robots(session),
                        max_failures=int(site.get("max_failures_in_row", 5)))
        # The page walk ends early enough to leave the geocoder its minutes.
        reserve = float(site.get("geocode_reserve_minutes", 4)) * 60
        fetch_until = (deadline - reserve) if deadline is not None else None
        cursor = load_cursor(a.cursor)

        def save_progress(_c=cursor) -> None:
            if not a.dry_run:
                save_cursor(a.cursor, _c)

        geocode, unasked = make_geocode(session, deadline)
        pending = [0]

        def emit(ev: NormalizedEvent) -> None:
            if a.dump:
                from dataclasses import asdict
                dumped.append(asdict(ev))
            if store is None:
                return
            store.upsert(ev)
            pending[0] += 1
            if pending[0] >= SAVE_EVERY:
                store.save()
                pending[0] = 0

        def cancel(ev: NormalizedEvent, reason: str, notice: bool) -> None:
            if store is not None:      # notice: the café's own title says it is off
                store.cancel(ev, reason, notice=notice)

        try:
            rep = read_site(site, client, cursor, today, geocode, emit, cancel,
                            max_pages=a.max_pages, fetch_until=fetch_until, save=save_progress,
                            geocode_unasked=unasked)
        except Exception as exc:                          # noqa: BLE001
            failed += 1
            print(f"[{SOURCE}] {site.get('name', '?')} FAILED: {type(exc).__name__}: {exc}", flush=True)
            continue
        finally:
            if store is not None:
                store.save()
            save_progress()            # a crash keeps the pages it fetched, as a deadline does
        rep["requests"] = client.requests
        _report(rep)
        print(f"[{SOURCE}] {client.requests} request(s) to the site", flush=True)
        total += rep["kept"]
    if not a.dry_run:                                     # a dry run leaves the shared cache alone
        try:
            from mapsee_ingest_ics import _GEO_CACHE, _save_geo_cache
            _save_geo_cache(_GEO_CACHE)
        except Exception as exc:                          # noqa: BLE001
            print(f"[{SOURCE}] geocode cache not saved: {type(exc).__name__}: {exc}", flush=True)
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as fh:
            json.dump(dumped, fh, ensure_ascii=False, indent=1)
    took = (time.monotonic() - started) / 60
    if store is not None:
        print(f"[{SOURCE}] done: +{total} sessions in {took:.1f} min; "
              f"store now holds {len(store.records)} unique events.", flush=True)
    else:
        print(f"[{SOURCE}] dry run - {total} session(s) would be written; {took:.1f} min", flush=True)
    return 1 if failed and failed == sites else 0


if __name__ == "__main__":
    sys.exit(main())
