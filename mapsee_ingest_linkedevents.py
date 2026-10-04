#!/usr/bin/env python3
"""
mapsee_ingest_linkedevents.py - what is on at the community centre, from the
Linked Events API that Finnish cities publish their event calendars through.

    python mapsee_ingest_linkedevents.py --config linkedevents_sources.json \
        --store feeds_events.json [--only espoo]

Linked Events is the City of Helsinki's open event API (6Aika), and the same
codebase runs Espoo's calendar as a fork. It is a DOCUMENTED PUBLIC API: the
OpenAPI 3.0.3 description is at https://api.hel.fi/linkedevents/api-docs/schema/
("Linked Events information API", v3.31.1 on 2026-10-03), and its data licence
is CC BY 4.0 ("Unless otherwise stated"), with images marked `event_only`
usable only for communication about that event. api.hel.fi/robots.txt is a 301
to the developer portal at dev.hel.fi, which resets the connection from CI's
network - RFC 9309's "unreachable", so robots_txt.py would assume Disallow. The
exemption this rests on is the documented-API one (catalog_curate's
DOCUMENTED_API_TYPES), and it holds only while the walk keeps to the API's own
usage guidance: page_size at most 100, and places cached separately (PLACES).

WHY IT IS WORTH A WHOLE ADAPTER. It is the community-centre schedule this map
did not have, for two cities, with the city's own coordinate on every row.
Kept from the 90-day window on 2026-10-03 (10,529 rows):

    staffed playgrounds (leikkipuisto)   2,044 rows at 47     <- family drop-ins
    community houses / residents' rooms  1,228 rows at 10        most mornings
    senior and service centres             829 rows at 22
    family houses (perhetalo)              175 rows at  2
    youth houses (nuorisotalo)              43 rows at 10
    public libraries                     4,098 rows at 89
    cultural centres (Malmitalo, Stoa..)   781 rows at 11

90.0% are free by the publisher's own `is_free` (Helsinki 92.1%, Espoo 80.9%).

    Helsinki  api.hel.fi/linkedevents/v1   12,575 leaf rows, 142 requests, 311 s
    Espoo     espooevents.prod.espoon-voltti.fi/api/events/v1
                                            6,149 leaf rows,  68 requests, 105 s

ESPOO IS ITS OWN INSTANCE, and the copy inside Helsinki's is a mirror: all
1,548 of Helsinki's `espoo_le` rows had a twin in Espoo's by title and day, and
the mirror had dropped the two fields that say a row is a sign-up. So Helsinki
drops `espoo_le` and Espoo is read at home. api.espoo.fi/events/v1 answers too,
but it links every page through the backend host and its own robots.txt is
unreachable, so the config names the backend host, whose robots.txt is absent
(an HTML page: nothing disallowed). Turku and Tampere run instances that could
not be reached from this network on 2026-10-03; see `_unverified` in the config.

------------------------------------------------------------------------------
THE THINGS THAT WILL BITE YOU
------------------------------------------------------------------------------

1. LEAVES ONLY, ASKED FOR BY THE SERVER. A recurring series is a parent row
   (`super_event_type: recurring`, 1,258 on 2026-10-03) whose dates are its
   children, and a festival is an `umbrella` (20); the children are listed as
   rows of their own. `super_event_type=none` returns exactly the leaves, so
   nothing is expanded and nothing is counted twice.

2. THE DEFAULT ORDER LOSES ROWS. The list is ordered by -last_modified_time,
   and a row edited while the walk is on page 40 jumps to page 1, which has
   already been read - it is never seen. `sort=start_time` does not move when
   a description changes: 12,575 distinct ids read of the 12,575 counted.

3. PLACES. Inlining the place on every event (`include=location`) is 7.5 KB a
   row - 104 MB for one window - and the API asks callers listing everything
   to cache places separately. So the place list is walked once
   (`has_upcoming_events=true`: 942 places in 10 pages for Helsinki), and a
   place it missed is fetched by id; a place that answers 410 Gone leaves its
   rows unplaceable and counted. A place list too big to be filtered falls back
   to inlining.

4. ENROLMENT IS A FIELD, AND IT IS THE ONLY REGISTRATION SIGNAL READ. An
   enrolment window or a Linked Registrations sign-up is out (216 Helsinki
   rows: gym circuits, peer-support groups, holiday camps - and ticket-sale
   windows, which is the cost: in the morning's sample 46 of 232 such rows were
   paid, 27 of them one paint-and-party night). Espoo's fork has no enrolment
   fields; its rows say it as a hobby-catalogue entry (`hobby_categories`,
   3,691 rows - the JUMPPI school hobby groups and their kind) or a sign-up
   form (`event_registration_link`, 277 rows; 38 of them one children's
   theatre's ticket link, which is that fork's cost). The TEXT is not
   read: 2,498 of 12,032 Helsinki leaves mention registration in fi/sv/en, and
   about half of those beside a negation ("Ingen förhandsanmälan krävs", "no
   prior registration required"), so a phrase rule needs its own measurement.

5. A SERVICE-CENTRE CARD IS NOT "ANYONE". Helsinki's senior and service
   centres run most of their programme for holders of the palvelukeskuskortti -
   free, from the front desk, for Helsinki pensioners and unemployed people.
   1,586 rows say so (an audience keyword, or the stock sentence in the text),
   and they are left out unless the config sets keep_card_holder_rows, which
   keeps them with that sentence in English and a restricted, never-free
   admission marker.

6. A LEAF CAN RUN FOR FIFTY YEARS. Helsinki City Museum's "Lasten kaupunki"
   is one row from 2001-01-01 to 2050-12-31; 69 Helsinki and 17 Espoo rows span
   more than max_span_days (92), and they are permanent displays, not occasions.

7. IDENTITY IS THE SOURCE ID, so a re-run writes nothing new: re-reading
   Espoo into the same store updated 1,954 rows and added and rekeyed 0. A
   title|date fingerprint would write a rescheduled row as a second row on its
   new date, and an upsert cannot delete the first; 810 rows carry
   EventRescheduled (Helsinki 694, Espoo 116), though kulke marks all 581 of
   its rows that way, so the true count of moved dates is smaller.
   The same session published under two ids (a standalone row and its copy in a
   series: 56 Helsinki, 6 Espoo) is collapsed to the smallest id.

8. THE LANGUAGE IS THE ONE THE EVENT IS HELD IN. Finnish is the only
   language on 9,490 of the morning's 13,851 rows; a row held in Swedish or
   English that has a title in it is shown in it (150 and 62 rows), and its
   link opens in it too. The town stays Finnish ("Vantaa", not "Vanda")
   because other sources join on it.

9. CATEGORIES COME FROM KEYWORDS, because the titles are Finnish and the
   sync's promotion rules read English. The YSO ontology is shared by every
   instance; see _KEYWORD_RULES. Primary after derive_categories: kids 3,716,
   learning 2,302, community 1,443, music 1,084, arts 1,045, fitness 583,
   theater 278, food 46, sports 25, outdoors 6.

10. FREE IS THE PUBLISHER'S WORD. `is_free` becomes "Free to attend." through
    mapsee_admission, which ../mapsee's 0227 tagger reads; a price is stated as
    an amount (128 rows) or quoted as written (869 rows: "39-48€", "12€/20€"),
    and "0 €" against `is_free: false` is never called free.

11. CC BY 4.0 IS A CONDITION. The attribution is the final, short paragraph of
    every description, so the sync's _cap_prose keeps it when it trims, and an
    image is taken only under `event_only` or `cc_by`, with its photographer
    named in that paragraph as the image terms require.
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_admission import admission_description, normalize_admission_facts
from mapsee_ingest import EventStore, NormalizedEvent, looks_online_only, norm_categories, normalize_text

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
# The API's own ceiling: "100 is the maximum value for page_size" (OpenAPI).
PAGE_SIZE = 100
# A cap so one instance cannot hold a run open for ever. It is LOUD when it
# bites, because a silent cap reads as "we read the whole calendar". Helsinki
# was 126 pages of 100 for a 90-day window on 2026-10-03.
DEFAULT_MAX_PAGES = 300
# The place list is walked once per run (header, item 3). More than
# this many pages means `has_upcoming_events` was not honoured and the walk
# would read every address the city has ever geocoded; the adapter then asks
# for the places inline instead.
PLACE_MAX_PAGES = 40
# Places an event points at that the walk did not return, fetched one by one.
DEFAULT_PLACE_LOOKUPS = 150
DEFAULT_HORIZON_DAYS = 90
# Longer than a season is a standing exhibition or a permanent collection, not
# an occasion: Helsinki City Museum's "Lasten kaupunki" runs 2001-01-01 to
# 2050-12-31 and is a leaf row like any other (header, item 6).
DEFAULT_MAX_SPAN_DAYS = 92
LANGS = ("fi", "sv", "en")

# ---------------------------------------------------------------- categories
# Keyword id -> mapsee key. ORDER IS PRIORITY: the earliest rule a row matches
# becomes the primary, the rest are extras (norm_categories caps them at two).
# The ids are YSO (the national ontology every Linked Events instance shares)
# plus the instance-local topic keywords measured in the 2026-10-03 pull; the
# Finnish labels are the API's own `name.fi`, so the table can be read against
# /keyword/<id>/ without guessing.
#
# Keywords and not text, because the titles are Finnish and the sync's
# promotion rules read English (and a handful of Finnish kids words). The
# source has already said what each event is about, in a controlled
# vocabulary; reading that is cheaper and more honest than translating.
_KEYWORD_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("kids", ("yso:p316",            # leikkiminen - playing (children's games)
              "yso:p8105",           # leikkipuistot - (staffed) playgrounds
              "yso:p14710",          # satutunnit - story hours
              "yso:p15937",          # vauvat - babies
              "kulke:105")),         # Lastentapahtumat - children's events
    ("theater", ("yso:p2625",        # teatteritaide
                 "yso:p2315")),      # teatteritapahtumat
    ("music", ("yso:p1808",          # musiikki
               "yso:p11185",         # konsertit
               "yso:p2371",          # laulut - songs (sing-alongs)
               "kulke:31", "kulke:348")),   # Musiikki
    ("arts", ("yso:p1235",           # elokuvat - films
              "yso:p2739",           # kuvataide - fine arts
              "yso:p5121",           # näyttelyt - exhibitions
              "yso:p2851",           # taide - art
              "yso:p1278", "kulke:32",      # tanssi - dance (performing arts)
              "yso:p8630",           # kädentaidot - craft skills
              "yso:p4923",           # käsityöt - handicrafts
              "yso:p8631",           # askartelu - hobby crafting
              "yso:p3910", "yso:p20136",    # ompelu - sewing
              "yso:p10066")),        # neulonta - knitting
    ("fitness", ("yso:p916",         # liikunta - physical training
                 "yso:p3093",        # soveltava liikunta - adapted physical activity
                 "yso:p3046")),      # tanssilajit - dances (as an activity)
    ("sports", ("yso:p965",)),       # urheilu
    ("outdoors", ("yso:p2771",)),    # ulkoilu - outdoor recreation
    ("food", ("yso:p3670",)),        # ruoka
    ("learning", ("yso:p15875",      # luennot - lectures
                  "yso:p2149",       # opastus - guidance
                  "yso:p556",        # kieli ja kielet - languages
                  "yso:p8113",       # kirjallisuus - literature
                  "yso:p11406",      # lukeminen - reading
                  "helsinki:agjffu7tgq",   # Kielikahvilat - language cafés
                  "helmet:11733",    # Kielikahvilat ja keskusteluryhmät
                  "helsinki:agjffvmzeu",   # Lukupiirit - book clubs
                  "helsinki:aflfbatker",   # digitaidot - digital skills
                  "yso:p37943",      # digineuvonta - digital support
                  "yso:p8856", "yso:p12469", "yso:p24061", "yso:p10163",  # Finnish, Swedish, language learning
                  "yso:p2630",       # opetus - teaching
                  "kulke:732")),     # Työpajat - workshops
    ("community", ("yso:p14004",     # keskustelu - conversation
                   "yso:p12878", "yso:p12877",   # vertaistuki / vertaisryhmät - peer support
                   "yso:p7642",      # kerhot - clubs
                   "yso:p6062",      # pelit - games (board games and bingo, not nightlife)
                   "yso:p10647",     # monikulttuurisuus - multiculturalism
                   "helsinki:agifbfhi2e",   # Muistiryhmät - memory groups
                   "yso:p10727")),   # osallistuminen - participation (on a third of rows; last)
)
_KEYWORD_KEY = {kid: key for key, ids in _KEYWORD_RULES for kid in ids}
_KEY_ORDER = [key for key, _ in _KEYWORD_RULES]

# Audience keywords. A row whose audiences are ALL young is a kids row whatever
# its topic; a row that names an adult band too is a community event children
# are welcome at, and stays on its topic's door (the bibliocommons rule).
_YOUNG_AUDIENCES = frozenset({
    "yso:p4354",               # lapset (ikäryhmät) - children
    "yso:p13050",              # lapsiperheet - families with children
    "yso:p20513",              # vauvaperheet - families with babies
    "yso:p16485",              # koululaiset - pupils
    "yso:p11617",              # nuoret - young people
    "yso:p4363",               # perheet - families
    "yso:p15937",              # vauvat - babies
    "helfi:1",                 # Lapset ja lapsiperheet
    "espoo_le:aggh5gwmlm",     # lapsiperheet (Espoo)
    "espoo_le:aggh5gwmm4",     # nuoret (Espoo)
    "espoo:a1",                # lapsiperheet - families (Espoo's own instance)
    "espoo:a3",                # nuoret - youth (Espoo's own instance)
})

# --------------------------------------------------------------- exclusions
# A service-centre card ("palvelukeskuskortti") is Helsinki's pass for its
# senior and service centres: free, issued at the front desk, and only to
# Helsinki pensioners and unemployed people. A row that requires it is a
# drop-in for card holders, not one anybody can turn up to. The audience
# keyword says so on 1,133 rows of the 2026-10-03 pull and the text says so on
# 512 more; every one of the 512 read was a requirement ("Toimintaan
# osallistuminen edellyttää maksutonta palvelukeskuskorttia"), none a waiver.
_CARD_AUDIENCES = frozenset({"helsinki:aflfbat76e"})   # palvelukeskuskortti
_CARD_RX = re.compile(r"palvelukeskuskort|servicecentralkort|service[\s-]cent(?:re|er)\s+card", re.I)

# "Suljettu muistiryhmä" is a closed memory group: 24 rows, all one title
# shape. Narrow on purpose - "Closed" as a word is also the name of a play.
_CLOSED_GROUP_RX = re.compile(
    r"^\W*suljettu\s+\w*ryhmä|\bsuljettu\s+ryhmä\b|\bslutna?\s+grupp|\bclosed\s+group\b", re.I)

# A cancellation written into the title in the languages these cities publish
# in. mapsee_ingest.notice_reason knows the English, German, French, Spanish,
# Dutch and Italian words and not these; 5 of 12,264 leaf rows carried one on
# 2026-10-03, four of them still marked EventScheduled or EventRescheduled.
_CANCELLED_TITLE_RX = re.compile(
    r"^\W*(?:peru(?:u)?tettu|peruttu|peruuntunut|peruuntuu|inställ(?:d|t|da)|cancel+ed)\b", re.I)

_ONLINE_PLACE_NAMES = frozenset({"internet", "verkossa", "online", "virtuaalinen", "etänä"})

# ------------------------------------------------------------------- prices
# One amount and a currency, and nothing else: "5 €", "5€", "€5", "5,50 €",
# "49,99 €/hlö", "8 euroa". A range, two prices or a word is not one price and
# is quoted as the source wrote it instead.
_ONE_PRICE_RX = re.compile(
    r"^\s*(?:€\s*)?(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:€|e|eur|euro|euroa)?\s*"
    r"(?:/\s*(?:hlö|henkilö|hlo|person|pers\.?|kerta|gång))?\s*\.?\s*$", re.I)
_URL_ONLY_RX = re.compile(r"^\s*https?://\S+\s*$", re.I)


# ------------------------------------------------------------------- helpers
def _today() -> date:
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests, as everywhere else."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return date.today()


def _now(tz) -> datetime:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.combine(_today(), datetime.min.time(), tzinfo=tz)
    return datetime.now(tz)


def _tz(name: Optional[str]):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name or "Europe/Helsinki")


def _ref_id(ref: Any) -> Optional[str]:
    """'https://api.hel.fi/linkedevents/v1/keyword/yso:p916/' -> 'yso:p916'."""
    if isinstance(ref, dict):
        if ref.get("id"):
            return str(ref["id"])
        ref = ref.get("@id")
    if not isinstance(ref, str) or not ref:
        return None
    return urllib.parse.unquote(ref.rstrip("/").rsplit("/", 1)[-1]) or None


def _tr(field: Any, lang: str) -> Optional[str]:
    """A multilingual field in `lang`, else in fi/sv/en order, else any."""
    if isinstance(field, str):
        return field.strip() or None
    if not isinstance(field, dict):
        return None
    for code in (lang,) + LANGS:
        v = field.get(code)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for v in field.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def pick_language(ev: Dict[str, Any]) -> str:
    """The language the row is shown in.

    THE ONE THE EVENT IS HELD IN, when it says so and has a name in it: a
    Swedish-language storytime ("in_language": [sv]) is shown in Swedish and an
    English conversation club in English, because a reader who cannot read the
    title cannot take part either. Otherwise Finnish, the language 9,490 of the
    13,851 rows were written in ONLY, then Swedish, then English.
    """
    names = ev.get("name") if isinstance(ev.get("name"), dict) else {}
    held = [c for c in (_ref_id(x) for x in ev.get("in_language") or []) if c in LANGS]
    if len(held) == 1 and (names.get(held[0]) or "").strip():
        return held[0]
    for code in LANGS:
        if (names.get(code) or "").strip():
            return code
    for code, v in names.items():
        if isinstance(v, str) and v.strip():
            return code
    return "fi"


_PARA_TAG = re.compile(r"</(?:p|div|h[1-6]|ul|ol|table|blockquote)\s*>", re.I)
_LINE_TAG = re.compile(r"</(?:li|tr)\s*>|<br\s*/?>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def clean_text(s: Optional[str], limit: int = 1500) -> Optional[str]:
    """Rich text to plain paragraphs. A closing paragraph is a blank line, a
    <br> a line break, and every other tag a SPACE (not nothing, or
    "<b>one</b>two" reads "onetwo")."""
    if not s:
        return None
    s = _LINE_TAG.sub("\n", _PARA_TAG.sub("\n\n", str(s)))
    s = html_mod.unescape(_ANY_TAG.sub(" ", s)).replace(" ", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.split("\n")]
    out: List[str] = []
    for ln in lines:
        if ln:
            out.append(ln)
        elif out and out[-1] != "":
            out.append("")
    text = "\n".join(out).strip()
    text = re.sub(r"\n{2,}", "\n\n", text)
    for ch in ",.;:!?)":
        text = text.replace(" " + ch, ch)
    if len(text) > limit:
        cut = text[:limit]
        stop = max(cut.rfind(". "), cut.rfind("\n"))
        text = (cut[:stop + 1] if stop > limit * 0.6 else cut.rsplit(" ", 1)[0]).rstrip() + "…"
    return text or None


def _parse_time(s: Optional[str], tz) -> Tuple[Optional[str], Optional[str], Optional[datetime]]:
    """(local, utc, aware) for an API timestamp.

    "2026-10-06T15:00:00Z" -> ("2026-10-06T18:00:00", "2026-10-06T15:00:00Z", ...).
    A bare "2026-10-12" is a DAY - 29 leaf rows publish one (camps, festivals) -
    and stays a bare date, so the sync anchors it to a local day rather than
    to midnight in London.
    """
    s = (s or "").strip()
    if not s:
        return None, None, None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=tz)
        return s, None, d
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    local = dt.astimezone(tz)
    return (local.strftime("%Y-%m-%dT%H:%M:%S"),
            dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), local)


def categorise(ev: Dict[str, Any], default: str) -> Tuple[str, List[str]]:
    """(primary, extras) from the row's own keywords and audiences."""
    kw = [_ref_id(k) for k in ev.get("keywords") or []]
    aud = [_ref_id(a) for a in ev.get("audience") or []]
    hit = {_KEYWORD_KEY[k] for k in kw if k in _KEYWORD_KEY}
    keys = [k for k in _KEY_ORDER if k in hit]
    aud = [a for a in aud if a]
    young = [a for a in aud if a in _YOUNG_AUDIENCES]
    max_age = ev.get("audience_max_age")
    young_by_age = isinstance(max_age, int) and 1 <= max_age <= 17
    if (aud and len(young) == len(aud)) or (not aud and young_by_age):
        return "kids", norm_categories("kids", keys)
    if aud and len(young) < len(aud) and "kids" in keys:
        # An adult band is named too: a family event, not a children's one.
        keys.remove("kids")
        keys.append("kids")
    if young and "kids" not in keys:
        keys.append("kids")
    if not keys:
        return default, []
    return keys[0], norm_categories(keys[0], keys[1:])


def admission(ev: Dict[str, Any], lang: str, context: str, url: Optional[str],
              card_only: bool) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(source_details, price line) from the offers the row publishes.

    `is_free` is the publisher's own statement and the only thing that may make
    a row say Free (../mapsee's 0227 tagger reads the WORDS "Free to attend."
    that admission_description writes; it does not read "Vapaa pääsy"). A paid
    row says its price, or that it has one; it never says free, even when the
    price text reads "0 €" against `is_free: false` (24 rows on 2026-10-03).
    """
    offers = [o for o in ev.get("offers") or [] if isinstance(o, dict)]
    if card_only:
        # Free for the people allowed in is not free admission for the public.
        return {"free": False, "restricted": True}, None
    if not offers:
        return None, None
    if all(o.get("is_free") is True for o in offers):
        return normalize_admission_facts("Free", url=url, context=context), None
    texts = []
    for o in offers:
        if o.get("is_free") is True:
            continue
        t = _tr(o.get("price"), lang)
        if t and not _URL_ONLY_RX.match(t):
            t = re.sub(r"\s+", " ", t).strip().rstrip(".")[:120]
            if t and t not in texts:
                texts.append(t)
    if len(texts) == 1:
        m = _ONE_PRICE_RX.match(texts[0])
        if m:
            amount = m.group(1).replace(",", ".")
            try:
                positive = float(amount) > 0
            except ValueError:
                positive = False
            if positive:
                facts = normalize_admission_facts({"price": amount, "priceCurrency": "EUR"},
                                                  url=url, context=context)
                if facts and facts.get("free") is False:
                    return facts, None
    line = f"Price: {'; '.join(texts)}." if texts else None
    return {"free": False}, line


def _image(ev: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(url, credit) for the event's own picture, or (None, None).

    The API's terms: an `event_only` image may be used only for communication
    about that event, and "the source and photographer must absolutely be
    mentioned". A pin's own sheet is that event's communication; the credit
    goes in the attribution paragraph. Anything with another licence is left.
    """
    for img in ev.get("images") or []:
        if not isinstance(img, dict):
            continue
        url = img.get("url")
        lic = (img.get("license") or "").strip().lower()
        if not (isinstance(url, str) and url.startswith("https://")) or lic not in ("event_only", "cc_by"):
            continue
        who = re.sub(r"\s+", " ", str(img.get("photographer_name") or "")).strip()[:60].rstrip(" .")
        return url, who or None
    return None, None


def _point(place: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    pos = place.get("position") or {}
    c = pos.get("coordinates") if isinstance(pos, dict) else None
    try:
        lon, lat = float(c[0]), float(c[1])           # GeoJSON order: lon, lat
    except (TypeError, ValueError, IndexError, KeyError):
        return None, None
    if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None
    return lat, lon


def _is_online_place(place_id: Optional[str], place: Optional[Dict[str, Any]]) -> bool:
    if place_id and place_id.split(":")[-1].lower() == "internet":
        return True
    if place:
        names = place.get("name") if isinstance(place.get("name"), dict) else {}
        return any(isinstance(v, str) and v.strip().lower() in _ONLINE_PLACE_NAMES
                   for v in names.values())
    return False


def _span_days(start: Optional[datetime], end: Optional[datetime]) -> float:
    if not start or not end:
        return 0.0
    return (end - start).total_seconds() / 86400.0


# ----------------------------------------------------------------- one row
def to_event(ev: Dict[str, Any], place: Optional[Dict[str, Any]], inst: Dict[str, Any],
             now: datetime, horizon_end: date) -> Tuple[Optional[NormalizedEvent], Optional[str]]:
    """(event, None) or (None, the reason it was left out)."""
    tz = _tz(inst.get("timezone"))
    if ev.get("deleted"):
        return None, "deleted"
    if ev.get("super_event_type"):
        return None, "super event (series or festival parent)"
    if (ev.get("type_id") or "General") != "General":
        return None, f"type {ev.get('type_id')}"
    status = ev.get("event_status") or "EventScheduled"
    if status == "EventCancelled":
        return None, "cancelled (event_status)"
    if status == "EventPostponed":
        return None, "postponed, no date (event_status)"
    if ev.get("data_source") in set(inst.get("drop_data_sources") or ()):
        return None, f"data source {ev.get('data_source')}"
    if ev.get("publisher") in set(inst.get("drop_publishers") or ()):
        return None, f"publisher {ev.get('publisher')}"

    lang = pick_language(ev)
    name = clean_text(_tr(ev.get("name"), lang), 300)
    if not name:
        return None, "no name"
    name = name.replace("\n", " ")
    if _CANCELLED_TITLE_RX.search(name):
        return None, "cancelled (title)"
    if _CLOSED_GROUP_RX.search(name):
        return None, "closed group (title)"

    place_id = _ref_id(ev.get("location"))
    if _is_online_place(place_id, place):
        return None, "online (location Internet)"
    if place_id in (inst.get("drop_locations") or {}):
        return None, "venue's own calendar already ingested"

    # Registration is a FIELD here, so it is read from the field: an enrolment
    # window or a Linked Registrations sign-up means the session is not one
    # you turn up to. Espoo's fork says it differently - a hobby catalogue
    # entry (`hobby_categories`) or a sign-up form (`event_registration_link`)
    # - and its rows carry no enrolment fields at all. The header's item 4
    # says what each costs.
    if ev.get("hobby_categories"):
        return None, "hobby group (Espoo hobby catalogue)"
    if ev.get("enrolment_start_time") or ev.get("enrolment_end_time") or ev.get("registration"):
        return None, "registration required (enrolment)"
    if ev.get("event_registration_link"):
        return None, "registration required (sign-up link)"

    desc = clean_text(_tr(ev.get("description"), lang) or _tr(ev.get("short_description"), lang))
    every_text = " ".join(filter(None, (
        _tr(ev.get("name"), c) for c in LANGS))) + " " + " ".join(filter(None, (
            _tr(ev.get(f), c) for f in ("description", "short_description") for c in LANGS)))
    card_only = (any(_ref_id(a) in _CARD_AUDIENCES for a in ev.get("audience") or [])
                 or bool(_CARD_RX.search(every_text)))
    if card_only and not inst.get("keep_card_holder_rows"):
        return None, "card holders only (service-centre card)"
    if looks_online_only(name, desc):
        return None, "online (text)"

    s_local, s_utc, s_dt = _parse_time(ev.get("start_time"), tz)
    e_local, e_utc, e_dt = _parse_time(ev.get("end_time"), tz)
    if not s_local or not s_dt:
        return None, "no start"
    if e_dt and e_local and len(e_local) == 10:
        e_dt = e_dt + timedelta(days=1)             # a bare end date runs to its end
    if e_dt is not None and e_dt <= s_dt:
        e_local = e_utc = None                       # an end before the start is a typo
        e_dt = None
    if (e_dt or s_dt + timedelta(hours=1 if s_utc else 24)) <= now:
        return None, "past"
    if s_dt.date() > horizon_end:
        return None, "beyond horizon"
    if _span_days(s_dt, e_dt) > float(inst.get("max_span_days", DEFAULT_MAX_SPAN_DAYS)):
        return None, "long run (span over the limit)"

    if not place:
        return None, "no location" if not place_id else "location not fetched"
    lat, lon = _point(place)
    if lat is None:
        return None, "location without coordinates"

    venue = clean_text(_tr(place.get("name"), lang), 160)
    venue = venue.replace("\n", " ") if venue else None
    info = _tr(ev.get("info_url"), lang)
    offer_url = next((_tr(o.get("info_url"), lang) for o in ev.get("offers") or []
                      if isinstance(o, dict) and _tr(o.get("info_url"), lang)), None)
    page_tmpl = inst.get("event_page")
    page = page_tmpl.format(id=urllib.parse.quote(str(ev.get("id")), safe=":"), lang=lang) if page_tmpl else None
    # The city's own page for THIS event first. `info_url` is what the
    # publisher typed, and it is often the venue's or a catalogue's front page
    # (Espoo libraries point every row at https://helmet.finna.fi/), while
    # tapahtumat.hel.fi renders every data source's events by id (checked
    # 2026-10-03 for helsinki, kulke, kursor and hkm rows) and espoo.fi the same.
    url = next((u for u in (page, info, offer_url) if isinstance(u, str) and u.startswith("http")), None)

    context = f"{_tr(ev.get('name'), 'en') or ''} {_tr(ev.get('description'), 'en') or ''}"
    details, price_line = admission(ev, lang, context, offer_url or url, card_only)

    parts: List[str] = []
    if card_only:
        parts.append(inst.get("card_holder_note") or
                     "Only for pensioners and unemployed people who hold a service-centre card "
                     "(ask at the centre's front desk).")
    if price_line:
        parts.append(price_line)
    room = clean_text(_tr(ev.get("location_extra_info"), lang), 80)
    if room:
        parts.append(f"Room: {room.replace(chr(10), ' ')}")
    if desc:
        parts.append(desc)
    img_url, img_credit = _image(ev)
    # THE LICENCE CONDITION, not a footer: CC BY 4.0 is held on the term that
    # the source is named. Kept short so the sync's _cap_prose protects it as
    # the final paragraph when it trims a long description.
    attribution = inst.get("attribution") or f"Event data: {inst.get('name')} Linked Events, CC BY 4.0."
    if img_url:
        attribution += f" Image: {img_credit or 'via the event organiser'}."
    parts.append(attribution)
    description = admission_description("\n\n".join(parts), details) if details else "\n\n".join(parts)

    primary, extras = categorise(ev, inst.get("category", "community"))
    provider = clean_text(_tr(ev.get("provider"), lang), 120)
    key = inst["key"]
    nev = NormalizedEvent(
        source=f"linkedevents:{key}",
        source_id=str(ev.get("id")),
        name=name,
        description=description,
        start_local=s_local, start_utc=s_utc,
        end_local=e_local, end_utc=e_utc,
        timezone=inst.get("timezone") or "Europe/Helsinki",
        venue_name=venue,
        latitude=lat, longitude=lon,
        # The point is the city's own: a service-registry unit (tprek:) or an
        # address the city geocoded (osoite:). Nothing downstream can improve on
        # it, and outside the US the sync has no geocoder that would try.
        coords_exact=True,
        address=clean_text(_tr(place.get("street_address"), lang), 160),
        # The town in Finnish whatever language the row is shown in: it is the
        # machine-readable locality other sources join on ("Vantaa", not the
        # "Vanda" a Swedish-language row would otherwise carry).
        city=clean_text(_tr(place.get("address_locality"), "fi"), 80) or inst.get("city"),
        country=inst.get("country"),
        postal_code=str(place.get("postal_code") or "").strip() or None,
        category=primary, categories=extras,
        promoter=provider.replace("\n", " ") if provider else None,
        poster_image_url=img_url,
        ticket_url=url,
        source_details=details,
        admission_checked=details is not None,
    )
    # IDENTITY IS THE SOURCE'S ID, not the title. A leaf row in Linked Events is
    # one occurrence with a permanent id, and its title and date are the two
    # things publishers edit (810 rows were EventRescheduled on 2026-10-03),
    # and a name|date fingerprint would write each moved one as a second row
    # beside the first (the DB upserts on the fingerprint, and an upsert cannot
    # delete). The cost is the cross-source merge, which the drop_locations
    # list in the config does deliberately instead.
    nev.fingerprint = hashlib.sha1(f"linkedevents|{key}|{ev.get('id')}".encode("utf-8")).hexdigest()
    return nev, None


# ---------------------------------------------------------------- the walk
class Refused(Exception):
    """A 401/403/429: the operator's answer, never retried or worked around."""


class Walker:
    """GETs against one instance: paced, retried on a server's bad moment,
    stopped on a refusal, and counted."""

    def __init__(self, session, base: str, delay: float, backoff: float = 5.0):
        self.session, self.base, self.delay, self.backoff = session, base.rstrip("/"), delay, backoff
        self.origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(self.base))
        self.requests = 0
        self._last = 0.0

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            if attempt and self.backoff:
                time.sleep(self.backoff * attempt)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.session.get(url, params=params, timeout=60)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
            if r.status_code in (401, 403, 429):
                raise Refused(f"HTTP {r.status_code} on {url}")
            if r.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {r.status_code}")
                continue
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {url}")
            try:
                return r.json()
            except ValueError as exc:                # a truncated body: a bad moment too
                last_exc = exc
                continue
        raise RuntimeError(f"{last_exc} on {url} after 3 tries")

    def pages(self, path: str, params: Dict[str, Any], max_pages: int, label: str
              ) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """(meta, row) for every row, following meta.next.

        `next` is followed only on the instance's own origin: Espoo's API
        answers on api.espoo.fi and links its pages through its backend host,
        and a config that names one host should not quietly read another.
        """
        url: Optional[str] = f"{self.base}/{path.strip('/')}/"
        p: Optional[Dict[str, Any]] = dict(params, page_size=PAGE_SIZE)
        for n in range(1, max_pages + 1):
            body = self.get(url, p)
            meta = body.get("meta") or {}
            for row in body.get("data") or []:
                yield meta, row
            nxt = meta.get("next")
            if not nxt:
                return
            if not str(nxt).startswith(self.origin + "/"):
                print(f"[linkedevents] {label}: next page is on another host ({nxt[:80]}); stopped")
                return
            url, p = nxt, None
        print(f"[linkedevents] {label}: STOPPED AT THE {max_pages}-PAGE CAP - the rest of "
              f"{path} was not read")


def fetch_places(walker: Walker, label: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Every place with an upcoming event, by id, or None to ask inline instead.

    The API asks for exactly this: "if you are listing the maximum number of
    events (100) or updating your cache with all events, please consider
    caching the keyword and location data separately". 942 places in 10 pages
    for Helsinki, against the same place inlined on every one of its events.
    """
    out: Dict[str, Dict[str, Any]] = {}
    first = True
    try:
        for meta, row in walker.pages("place", {"has_upcoming_events": "true"},
                                      PLACE_MAX_PAGES + 1, label):
            if first:
                first = False
                count = int(meta.get("count") or 0)
                if count > PLACE_MAX_PAGES * PAGE_SIZE:
                    print(f"[linkedevents] {label}: {count} places with upcoming events is "
                          f"more than a filtered list; asking for them inline instead")
                    return None
            if row.get("id"):
                out[str(row["id"])] = row
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[linkedevents] {label}: place list failed ({exc}); asking inline instead")
        return None
    return out


def ingest_instance(store: EventStore, session, inst: Dict[str, Any]) -> Dict[str, Any]:
    label = inst.get("name") or inst.get("key")
    tz = _tz(inst.get("timezone"))
    horizon = int(inst.get("horizon_days", DEFAULT_HORIZON_DAYS))
    today = _today()
    now = _now(tz)
    horizon_end = today + timedelta(days=horizon)
    walker = Walker(session, inst["api"], float(inst.get("crawl_delay", 1.0)),
                    float(inst.get("retry_backoff", 5.0)))
    t0 = time.monotonic()
    reasons: Dict[str, int] = {}
    kept = 0

    places = fetch_places(walker, label)
    params: Dict[str, Any] = {
        "start": today.isoformat(), "end": (horizon_end + timedelta(days=1)).isoformat(),
        # Leaves only: a recurring parent and an umbrella festival are each a
        # summary of rows the list also returns one by one.
        "super_event_type": "none",
        # NOT the default order. The default is -last_modified_time, and a row
        # edited during the walk jumps to page one - which the walk has already
        # read - so it is never seen. Start time does not move when a
        # description is edited.
        "sort": "start_time",
    }
    if places is None:
        params["include"] = "location"
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    count = None
    for meta, row in walker.pages("event", params, int(inst.get("max_pages", DEFAULT_MAX_PAGES)), label):
        if count is None:
            count = meta.get("count")
        rid = row.get("id")
        if rid in seen:
            reasons["read twice (paging)"] = reasons.get("read twice (paging)", 0) + 1
            continue
        seen.add(rid)
        rows.append(row)
    if count is not None and len(seen) < int(count):
        print(f"[linkedevents] {label}: read {len(seen)} distinct rows of the {count} the API counted")

    # Places the walk did not return (an event at a place whose flag is stale),
    # one by one and capped.
    places = places if places is not None else {}
    for row in rows:
        loc = row.get("location")
        if isinstance(loc, dict) and loc.get("id") and loc.get("position") is not None:
            places.setdefault(str(loc["id"]), loc)
    missing = sorted({pid for pid in (_ref_id(r.get("location")) for r in rows)
                      if pid and pid not in places and pid.split(":")[-1] != "internet"})
    lookups = int(inst.get("max_place_lookups", DEFAULT_PLACE_LOOKUPS))
    for pid in missing[:lookups]:
        try:
            places[pid] = walker.get(f"{walker.base}/place/{urllib.parse.quote(pid, safe=':')}/")
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[linkedevents] {label}: place {pid} failed: {exc}")
    if len(missing) > lookups:
        print(f"[linkedevents] {label}: {len(missing) - lookups} places left unfetched (cap {lookups})")

    # THE SAME SESSION PUBLISHED TWICE is two ids with one title, one start
    # and one place - a standalone row and its copy inside a series, usually
    # by the same publisher. 62 such pairs on 2026-10-03 (a playground's
    # "viikko-ohjelma" entered once by hand and once as a recurrence). The
    # smallest id is kept, so the choice is the same on every run.
    chosen: Dict[Tuple[str, str, str], Tuple[str, NormalizedEvent]] = {}
    for row in rows:
        pid = _ref_id(row.get("location"))
        nev, why = to_event(row, places.get(pid) if pid else None, inst, now, horizon_end)
        if nev is None:
            reasons[why] = reasons.get(why, 0) + 1
            continue
        k = (normalize_text(nev.name), nev.start_local or "", pid or "")
        rid = str(row.get("id"))
        if k in chosen:
            reasons["same session published twice"] = reasons.get("same session published twice", 0) + 1
            if rid >= chosen[k][0]:
                continue
        chosen[k] = (rid, nev)
    for _rid, nev in sorted(chosen.values(), key=lambda kv: kv[0]):
        result = store.upsert(nev)
        if result in ("rejected", "notice"):
            reasons[f"refused by the store ({result})"] = reasons.get(f"refused by the store ({result})", 0) + 1
            continue
        kept += 1
    left = ", ".join(f"{v} {k}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
    print(f"[linkedevents] {label}: kept {kept} of {len(rows)} rows read "
          f"({walker.requests} requests, {time.monotonic() - t0:.0f}s); left out: {left or 'none'}")
    return {"kept": kept, "read": len(rows), "reasons": reasons, "requests": walker.requests}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Linked Events (Finnish city event APIs) into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this instance (substring match on name or key)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for inst in cfg.get("instances", []):
        if a.only and a.only.lower() not in f"{inst.get('name', '')} {inst.get('key', '')}".lower():
            continue
        try:
            total += ingest_instance(store, session, inst)["kept"]
        except Refused as exc:
            print(f"[linkedevents] {inst.get('name', '?')} REFUSED: {exc} - not retried")
        except Exception as exc:  # noqa: BLE001
            print(f"[linkedevents] {inst.get('name', '?')} FAILED: {exc}")
    store.save()
    print(f"[linkedevents] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
