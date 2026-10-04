#!/usr/bin/env python3
"""
mapsee_ingest_linkedevents.py - what is on at the community centre, from the
Linked Events API that Finnish cities publish their event calendars through.

    python mapsee_ingest_linkedevents.py --config linkedevents_sources.json \
        --store feeds_events.json [--only espoo] [--max-minutes 15]

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
The review's rules (2026-10-04) take some of those out again; the paragraph
after item 15 has the count.

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

4. REGISTRATION: THE FIELDS FIRST, THEN THE WORDS. An enrolment window or a
   Linked Registrations sign-up is out (216 Helsinki rows: gym circuits,
   peer-support groups, holiday camps - and ticket-sale windows, which is the
   cost: in the morning's sample 46 of 232 such rows were paid, 27 of them one
   paint-and-party night). Espoo's fork has no enrolment fields; its rows say
   it as a hobby-catalogue entry (`hobby_categories`, 3,691 rows - the JUMPPI
   school hobby groups and their kind) or a sign-up form
   (`event_registration_link`, 277 rows; 38 of them one children's theatre's
   ticket link, which is that fork's cost). A catalogue entry that says
   "Töpinöihin ei tarvitse ilmoittautua" (no need to register) is a drop-in
   all the same: 89 of 3,852 catalogue rows, every one read a drop-in.
   THE WORDS: a field-only rule kept kulke's stock "Maksuton, vaatii
   ilmoittautumisen" (free, requires registration). 200 kept rows held a
   registration phrase; 158 of them beside a negation that CONTAINS the phrase
   ("ingen förhandsanmälan krävs", "no prior registration required"), so a
   phrase counts only when nothing right before it takes it back, and not when
   "osa" (some of) makes it partial - 10 rows of one baby morning. And a COURSE
   is a sign-up by the policy: 46 kept rows were titled as one ("Sirkuskurssi
   senioreille, Ryhmä 1"), less the 5 that were a course's performance
   ("... kurssi esittää").

5. A SERVICE-CENTRE CARD IS NOT "ANYONE". Helsinki's senior and service
   centres run most of their programme for holders of the palvelukeskuskortti -
   free, from the front desk, for Helsinki pensioners and unemployed people.
   1,586 rows say so (an audience keyword, or the stock sentence in the text),
   and 10 more say it in Swedish ("servicecentralskort" - with the s), and they
   are left out unless the config sets keep_card_holder_rows, which keeps them
   with that sentence in English and a restricted, never-free admission marker.

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
   series: 56 Helsinki, 6 Espoo) is collapsed to the SHORTER row, then the
   smallest id - not the copy that runs to the series' last date (item 12).

8. THE LANGUAGE IS THE ONE THE EVENT IS HELD IN. Finnish is the only
   language on 9,490 of the morning's 13,851 rows; a row held in Swedish or
   English that has a title in it is shown in it (150 and 62 rows), and its
   link opens in it too - Espoo's pages are named per language
   (/fi/tapahtumat/, /sv/evenemang/, /en/events/; 154 kept Espoo rows). The
   town stays Finnish ("Vantaa", not "Vanda") because other sources join on it.

9. CATEGORIES COME FROM KEYWORDS, because the titles are Finnish and the
   sync's promotion rules read English. The YSO ontology is shared by every
   instance; see _KEYWORD_RULES. Two more signals: the VENUE (a staffed
   playground or a family house makes a row kids - 199 of 2,259 rows there
   were on no kids door) and an audience id filed among the keywords (213
   rows). A row no keyword describes reads two title words (flea market,
   "jumppa"). Primary after derive_categories, replayed over the 2026-10-03
   pull: kids 4,161, learning 2,042, community 1,308, arts 956, music 901,
   fitness 587, theater 270, food 45, sports 24, outdoors 6, market 2.

10. FREE IS THE PUBLISHER'S WORD. `is_free` becomes "Free to attend." through
    mapsee_admission, which ../mapsee's 0227 tagger reads; a price is stated as
    an amount (128 rows) or quoted as written (869 rows: "39-48€", "12€/20€"),
    and "0 €" against `is_free: false` is never called free. The publisher's
    own text vetoes `is_free` when it says paid: "Maksullinen, 7 €/ kerta" -
    12 rows, which now say only that they are not free.

11. CC BY 4.0 IS A CONDITION. The attribution is the final, short paragraph of
    every description, so the sync's _cap_prose keeps it when it trims. An
    image is taken under `cc_by` (4,127 kept rows), with its photographer or
    the licence named in that paragraph. NOT under `event_only`, the API's own
    licence: "can only be used for information and communications connected to
    the event ... The source and photographer must absolutely be mentioned".
    ../mapsee reuses a poster as a venue page's og:image and shows thumbnails
    without a credit, so `event_only_images` is false on both instances (6,426
    rows without a picture). Switched on, it still skips an image whose
    photographer names nobody (1,333 empty, 112 "-", 60 "m", 44 ".").

12. TIMES THAT ARE NOT WHAT THEY SAY. A SERIES WRITTEN AS ONE ROW: "Vauva-aamu"
    (baby morning, Mondays 10-11) is one leaf from 09-21 10:00 to 12-14 11:00,
    and "Lady T" one from 08-06 18:30 to 10-31 20:35. A row running a week or
    more on a window of four hours or less (or ending on an earlier clock) is
    left out unless it is an exhibition: 19 kept rows. And MIDNIGHT IS A
    DATE: an exhibition from 00:00 to 00:00, or 00:01 to 23:59, is written as
    all-day dates (14 rows), as mapsee_ingest_opendata does.

13. A PLACE THAT IS A WHOLE TOWN (matko:732 "Helsinki", espooevents:espoo
    "Espoo") is the city centre's point, not a venue: 10 rows, left out
    rather than pinned there as exact.

14. WHAT ELSE IS NOT A DROP-IN, read from the title or the text: a private
    appointment (242 rows - one-to-one digital help "ajanvarauksella", a
    sewing machine "varattavissa", a reading dog's 15-minute slots; "Ei
    ajanvarausta", no appointment, does not match), online in Finnish
    ("kielikahvila Zoomissa", 20 rows at a real library), a cancellation
    written at the END of the title ("Killer (PERUTTU)", 4 rows marked
    EventScheduled), and a council's sitting (3).

15. A BUDGET, AND THE SAVE INSIDE IT. --max-minutes (15) is a deadline no
    request starts after; what was read is converted and saved, and the store
    is saved after each instance. The full window was 210 requests in 417 s.

The rules in items 4-5 and 12-14 were measured by replaying the 2026-10-03
pull offline on 2026-10-04: they leave out 368 more rows (Helsinki 317, Espoo
51) and let 23 Espoo drop-ins back, 10,587 -> 10,252 after the collapse in
item 7 (53 more drop-ins need their place fetched live); 89.6% stay free.
Live, a 7-day Helsinki window on 2026-10-04 (28 requests, 61 s) kept 1,033 of
1,768 rows: none with an event_only image (624 had one; 405 carry a cc_by
one), and none of the 1 Swedish-card, 11 appointment-title, 31 book-a-time, 8
registration-phrase or 9 course rows the window held.
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
# A SERIES WRITTEN AS ONE ROW (header, item 12): a leaf that runs a week or more
# while its clock window is short is the first session and the last session of
# a weekly group or a theatre run, not one continuous event.
SERIES_MIN_DAYS = 7
SERIES_MAX_WINDOW_MINUTES = 240
# THE RUN BUDGET (header, item 15). No request STARTS after --max-minutes, and
# what was read is converted and saved. The slowest single request is 3 tries
# x 30 s + 5 s + 10 s of backoff = 105 s, so 15 minutes + 105 s + the save sit
# inside a 20-minute step; the full 90-day read took 417 s on 2026-10-03 at
# about 2 s a page, so 30 s is fifteen times the page that was measured.
DEFAULT_MAX_MINUTES = 15.0
REQUEST_TIMEOUT_S = 30
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

# THE VENUE SAYS WHO IT IS FOR. A staffed playground (leikkipuisto) and a
# family house (perhetalo) run nothing but family sessions, yet 199 of the
# 2,259 rows at one on 2026-10-03 reached neither kids door - "Vauva-aamu"
# (baby morning) filed under music, "Pihapuuhat" under fitness - because the
# publisher tagged only the activity. A row there is a kids row.
_KIDS_VENUE_RX = re.compile(r"^\W*(?:leikkipuisto|lekpark|perhetalo|familjehus)", re.I)
# Two topics the keyword table has no id for, read from the title only when no
# keyword said anything (the row would otherwise fall to the config's
# 'community'): "Sunnuntaikirpputori" is a flea market and "Tuolijumppa"
# (chair exercise, 29 rows) is exercise.
_TITLE_TOPIC_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("market", re.compile(r"kirpputori|kirppis|loppis|\bflea\s+market\b", re.I)),
    ("fitness", re.compile(r"jump(?:pa|paa|passa)\b|\bgymnastik\b", re.I)),
)

# --------------------------------------------------------------- exclusions
# A service-centre card ("palvelukeskuskortti") is Helsinki's pass for its
# senior and service centres: free, issued at the front desk, and only to
# Helsinki pensioners and unemployed people. A row that requires it is a
# drop-in for card holders, not one anybody can turn up to. The audience
# keyword says so on 1,133 rows of the 2026-10-03 pull and the text says so on
# 512 more; every one of the 512 read was a requirement ("Toimintaan
# osallistuminen edellyttää maksutonta palvelukeskuskorttia"), none a waiver.
_CARD_AUDIENCES = frozenset({"helsinki:aflfbat76e"})   # palvelukeskuskortti
# The Swedish word is servicecentralSkort: spelt without the s, 10 rows of
# "Svenska klubben" ("För att delta behöver du ett servicecentralskort") were
# kept and called free.
_CARD_RX = re.compile(r"palvelukeskuskort|servicecentrals?kort|service[\s-]cent(?:re|er)\s+card", re.I)

# "Suljettu muistiryhmä" is a closed memory group: 24 rows, all one title
# shape. Narrow on purpose - "Closed" as a word is also the name of a play.
_CLOSED_GROUP_RX = re.compile(
    r"^\W*suljettu\s+\w*ryhmä|\bsuljettu\s+ryhmä\b|\bslutna?\s+grupp|\bclosed\s+group\b", re.I)

# A cancellation written into the title in the languages these cities publish
# in, at the START ("PERUTTU: Laura Voutilainen") or the END ("Killer
# (PERUTTU)", "Mosaiikin kielikahvila PERUTTU"). mapsee_ingest.notice_reason
# knows the English, German, French, Spanish, Dutch and Italian words and not
# these. 11 raw titles of the 18,724 read on 2026-10-03 carried one, 4 of them
# only at the end - and those 4 were EventScheduled and had been kept.
_CANCEL_WORD = r"(?:peru(?:u)?tettu|peruttu|peruuntunut|peruuntuu|inställ(?:d|t|da)|cancel+ed)"
_CANCELLED_TITLE_RX = re.compile(
    rf"^\W*{_CANCEL_WORD}\b|[\s(\[|:–—-]{_CANCEL_WORD}\s*[)\]!.]*\s*$", re.I)

# A PRIVATE APPOINTMENT IS NOT A DROP-IN (the lead's policy): one-to-one
# digital help "ajanvarauksella" (by appointment), a sewing machine
# "varattavissa" (bookable), a reading dog's 15-minute slots. Kept rows on
# 2026-10-03: 67 said so in the title and 175 more in the text ("Varaa aika
# kirjastosta", "Ajanvaraus p. 09 ...", "Boka en tid"). The text form is
# narrower on purpose: "Ei ajanvarausta" (no appointment) is the partitive and
# does not match, "ma suljettu / ajanvarauksella" is a gallery's opening hours
# and is not read from the text, a /ajanvaraus/ inside a URL is not a sentence,
# and "tai varaa aika" (or book a time) offers a booking beside a drop-in.
_BOOKING_TITLE_RX = re.compile(
    r"\bajanvarauksella\b|\bvarattavissa\b|\btidsbokning\b|\bpå\s+tidsbeställning\b|\bby\s+appointment\b",
    re.I)
_BOOKING_TEXT_RX = re.compile(
    r"(?<!tai\s)(?<!myös\s)\bvaraa\s+(?:oma\s+|tunnin\s+)?aika(?:si|a)?\b"
    r"|(?<![/\w])ajanvaraus\b"
    r"|\bboka\s+(?:en\s+|din\s+)?(?:tid|lästiden)\b"
    r"|\bbook\s+(?:a|your)\s+(?:time|slot|appointment)\b", re.I)

# REGISTRATION IN WORDS. The fields (item 4) miss a publisher who writes it:
# kulke's stock line is "Maksuton, vaatii ilmoittautumisen" (free, requires
# registration). A phrase is read only when nothing right before it negates it
# - "ingen förhandsanmälan krävs" and "no prior registration required" contain
# the positive phrase - and not when "osa" makes it partial ("Osa tuokioista
# ... vaatii ennakkoilmoittautumisen": SOME sessions need it). 200 kept rows
# held a phrase on 2026-10-03; 42 survived the negation check, and 10 of
# those were that partial Vauva-aamu, which stays.
_REGISTRATION_RX = re.compile(
    r"\bvaatii\s+(?:ennakko)?ilmoittautumisen\b"
    r"|\b(?:ennakko)?ilmoittautuminen\s+(?:on\s+)?(?:pakollinen|välttämätön)\b"
    r"|\b(?:förhands)?anmälan\s+(?:är\s+)?(?:krävs|obligatorisk)\b"
    r"|\b(?:advance\s+|prior\s+|pre-?)?registration\s+(?:is\s+)?(?:required|mandatory|compulsory)\b", re.I)
_REGISTRATION_NEGATED_BEFORE = re.compile(
    r"(?:\b(?:no|not|without|ingen|inge|ei|ilman)\s+(?:[\w-]+\s+)?|\bosa\b[^.!?]{0,60})$", re.I)
_REGISTRATION_NEGATED_AFTER = re.compile(r"\s*(?:inte|not)\b", re.I)
# "No need to register", which lets a row the hobby catalogue lists back in
# (item 4): 89 of Espoo's 3,852 catalogue rows said it on 2026-10-03, all of
# them drop-ins (Töpinät family play, a skate club, open bouldering).
_NO_REGISTRATION_RX = re.compile(
    r"\bei\s+(?:tarvitse\s+|vaadi\s+)?(?:ennakko)?ilmoittautu\w*"
    r"|\bilman\s+(?:ennakko)?ilmoittautumista\b"
    r"|\bingen\s+(?:förhands)?anmälan\b"
    r"|\bno\s+(?:prior\s+|advance\s+|pre-?)?registration\b"
    r"|\bregistration\s+(?:is\s+)?not\s+(?:required|needed)\b", re.I)

# A COURSE is a registration-only group by the policy, and 46 kept rows were
# titled as one on 2026-10-03 ("Sirkuskurssi senioreille, Ryhmä 1", a K-pop
# dance course over the autumn break). The 5 that were a course's PERFORMANCE
# ("... kurssi esittää: Elämän makuista") are a show anyone may watch.
_COURSE_TITLE_RX = re.compile(r"kurssi(?!keskus)|\bkurs(?:en|er)?\b|\bcourse\b", re.I)
_PERFORMANCE_TITLE_RX = re.compile(r"\besittää\b|\bpresenterar\b|\bpresents\b", re.I)
# A council's sitting is governance, not an event to turn up to (the policy):
# 3 rows of "Espoon kaupunginvaltuuston kokous" on 2026-10-03.
_GOVERNANCE_TITLE_RX = re.compile(
    r"valtuuston\s+kokous|lautakunnan\s+kokous|(?:fullmäktige|nämnd)\w*\s+(?:sammanträde|möte)"
    r"|\bcouncil\s+meeting\b", re.I)

_ONLINE_PLACE_NAMES = frozenset({"internet", "verkossa", "online", "virtuaalinen", "etänä"})
# Online in FINNISH: mapsee_ingest.looks_online_only reads English, and these
# rows sit at a real library ("Puhutaan suomea -kielikahvila Zoomissa", 9 rows
# at Tikkurila; "Kielikahvila e-Ekstra Teamsissa", 11). "Verkossa" is left out
# on purpose: "Turvallisesti verkossa" is a workshop ABOUT the web, held in a room.
_ONLINE_TITLE_RX = re.compile(r"\bzoom(?:issa|in)?\b|\bteamsissa\b|\bvia\s+(?:zoom|teams)\b", re.I)
_HYBRID_RX = re.compile(r"\bhybrid\w*|\bpaikan\s+päällä\b|\bja\s+etänä\b|\bon\s+site\b", re.I)

# A PLACE THAT IS A WHOLE TOWN. Some publishers file an event under the city
# itself, and its point is the city centre: 10 kept rows on 2026-10-03 sat on
# matko:732 "Helsinki" or espooevents:espoo "Espoo" - a trout-spawning walk on
# the Mätäjoki, public skating "in Espoo's ice halls". A centroid marked exact
# would pin them where they are not, so the row is left out.
_TOWN_NAMES = frozenset({"helsinki", "helsingfors", "espoo", "esbo", "vantaa", "vanda",
                         "kauniainen", "grankulla"})

# THE PUBLISHER'S OWN WORDS VETO `is_free`: "Maksullinen, 7 €/ kerta,
# tutustumiskerta ilmainen" (paid, 7 € a session, the first trial free) is
# published with is_free true. 12 kept rows on 2026-10-03 - 11 Tanssix, and a
# Winnie-the-Pooh day whose film is paid - would otherwise read "Free to attend."
_PAID_TEXT_RX = re.compile(
    r"\bmaksullinen\b|\bavgiftsbelagd\b|\d+(?:[.,]\d+)?\s*(?:€|e|eur|euroa)\s*/\s*(?:kerta|krt|gång)\b",
    re.I)

# An exhibition may run for weeks on short hours and still be one thing you
# can walk into on any day (item 12).
_EXHIBITION_KEYWORDS = frozenset({"yso:p5121"})          # näyttelyt - exhibitions
_EXHIBITION_TITLE_RX = re.compile(r"näyttely|utställning|exhibition", re.I)

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
    s = html_mod.unescape(_ANY_TAG.sub(" ", s)).replace("\u00a0", " ")
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


def categorise(ev: Dict[str, Any], default: str,
               place: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """(primary, extras) from the row's own keywords and audiences, and the
    venue when it is a playground or a family house."""
    kw = [_ref_id(k) for k in ev.get("keywords") or []]
    aud = [_ref_id(a) for a in ev.get("audience") or []]
    hit = {_KEYWORD_KEY[k] for k in kw if k in _KEYWORD_KEY}
    keys = [k for k in _KEY_ORDER if k in hit]
    if not keys:
        title = " ".join(filter(None, (_tr(ev.get("name"), c) for c in LANGS)))
        keys = [key for key, rx in _TITLE_TOPIC_RULES if rx.search(title)]
    # An audience id filed among the KEYWORDS is still an audience: a circus
    # show tagged yso:p4354 (children) there and nowhere else was on no kids
    # door (213 kept rows on 2026-10-03).
    aud = [a for a in aud if a]
    aud += [k for k in kw if k in _YOUNG_AUDIENCES and k not in aud]
    young = [a for a in aud if a in _YOUNG_AUDIENCES]
    max_age = ev.get("audience_max_age")
    young_by_age = isinstance(max_age, int) and 1 <= max_age <= 17
    names = (place or {}).get("name")
    at_kids_venue = isinstance(names, dict) and any(
        isinstance(v, str) and _KIDS_VENUE_RX.match(v) for v in names.values())
    if (aud and len(young) == len(aud)) or (not aud and young_by_age) or at_kids_venue:
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
              card_only: bool, text: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(source_details, price line) from the offers the row publishes.

    `is_free` is the publisher's own statement and the only thing that may make
    a row say Free (../mapsee's 0227 tagger reads the WORDS "Free to attend."
    that admission_description writes; it does not read "Vapaa pääsy"). A paid
    row says its price, or that it has one; it never says free, even when the
    price text reads "0 €" against `is_free: false` (24 rows on 2026-10-03).
    And `is_free` loses to the publisher's own text when that says paid
    (_PAID_TEXT_RX): the row then says only that it is not free.
    """
    offers = [o for o in ev.get("offers") or [] if isinstance(o, dict)]
    if card_only:
        # Free for the people allowed in is not free admission for the public.
        return {"free": False, "restricted": True}, None
    if not offers:
        return None, None
    if all(o.get("is_free") is True for o in offers):
        if _PAID_TEXT_RX.search(text):
            return {"free": False}, None
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


# Credits that name nobody: "Tuntematon" (unknown), "Ei tiedossa" (not known),
# a dash, a dot, one letter, "Linked events". On 2026-10-03, 1,333 kept rows had
# an event_only image with no photographer at all, 112 "-", 60 "m", 44 ".".
_NO_CREDIT_RX = re.compile(
    r"^(?:(?:kuvaaja\s+)?tuntematon|ei\s+tiedossa|unknown|okänd|n/?a|none|cc|creative\s+commons|"
    r"linked\s*events|kuva|photo|image|kuvaaja|photographer|maalaus)$", re.I)


def _credit(raw: Any) -> Optional[str]:
    """The photographer as the publisher typed it, or None when it names nobody."""
    who = re.sub(r"\s+", " ", str(raw or "").replace(" ", " ")).strip()[:60].strip(" .,;:-–*_/")
    if sum(ch.isalpha() for ch in who) < 2 or _NO_CREDIT_RX.match(who):
        return None
    return who


def _image(ev: Dict[str, Any], event_only: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """(url, credit line) for the event's own picture, or (None, None).

    `cc_by` is reusable with attribution: the photographer when the publisher
    names one, else the source. `event_only` is the API's own licence, and its
    terms say it "can only be used for information and communications connected
    to the event" and "the source and photographer must absolutely be
    mentioned". So it is taken only when the config allows it (item 11 says why
    neither instance does today) and only with a real photographer's name; an
    image that has neither is skipped and the next one tried. Any other licence
    is left.
    """
    for img in ev.get("images") or []:
        if not isinstance(img, dict):
            continue
        url = img.get("url")
        lic = (img.get("license") or "").strip().lower()
        if not (isinstance(url, str) and url.startswith("https://")):
            continue
        who = _credit(img.get("photographer_name"))
        if lic == "cc_by":
            return url, f"Image: {who}, CC BY 4.0." if who else "Image: CC BY 4.0, from the same source."
        if lic == "event_only" and event_only and who:
            return url, f"Image: {who}."
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


def registration_required(text: str) -> bool:
    """True when the text says registration is required and nothing right
    before or after the phrase takes it back (_REGISTRATION_RX)."""
    for m in _REGISTRATION_RX.finditer(text or ""):
        if _REGISTRATION_NEGATED_BEFORE.search(text[max(0, m.start() - 80):m.start()]):
            continue
        if _REGISTRATION_NEGATED_AFTER.match(text[m.end():m.end() + 12]):
            continue
        return True
    return False


def _is_placeholder_day(s_local: str, e_local: str) -> bool:
    return (len(s_local) > 10 and len(e_local) > 10
            and s_local[11:16] in ("00:00", "00:01") and e_local[11:16] in ("00:00", "23:59"))


def _placeholder_dates(s_local: str, e_local: str) -> Tuple[str, Optional[str]]:
    """'2026-10-01T00:00' .. '2026-11-01T00:00' -> ('2026-10-01', '2026-10-31'):
    an end at 00:00 is the end of the day before, an end at 23:59 its own day,
    and one day is just the start date."""
    s_day = date.fromisoformat(s_local[:10])
    e_day = date.fromisoformat(e_local[:10])
    if e_local[11:16] == "00:00":
        e_day -= timedelta(days=1)
    if e_day <= s_day:
        return s_day.isoformat(), None
    return s_day.isoformat(), e_day.isoformat()


def _is_series_row(s_dt: datetime, e_dt: Optional[datetime], ev: Dict[str, Any], name: str) -> bool:
    """A leaf that runs SERIES_MIN_DAYS or more on a short clock window: the
    first and last session of a weekly group ("Vauva-aamu", Mondays 10-11, one
    row from 09-21 to 12-14) or a theatre run (18:30 to 20:35 for twelve
    weeks). An end clock at or before the start clock over weeks is a run too
    (evening shows ending on a matinee). An exhibition is the exception: it is
    one thing you can walk into on any of those days."""
    if not e_dt or (e_dt.date() - s_dt.date()).days < SERIES_MIN_DAYS:
        return False
    if any(_ref_id(k) in _EXHIBITION_KEYWORDS for k in ev.get("keywords") or []) \
            or _EXHIBITION_TITLE_RX.search(name):
        return False
    window = (e_dt.hour * 60 + e_dt.minute) - (s_dt.hour * 60 + s_dt.minute)
    return window <= SERIES_MAX_WINDOW_MINUTES


def _is_city_level(place_id: Optional[str], place: Dict[str, Any], inst: Dict[str, Any]) -> bool:
    """A place that is the town itself (item 13): configured by id, or named
    exactly like a town in any language."""
    if place_id and place_id in (inst.get("city_level_places") or {}):
        return True
    names = place.get("name") if isinstance(place.get("name"), dict) else {}
    return any(isinstance(v, str) and v.strip().lower() in _TOWN_NAMES for v in names.values())


def event_page(inst: Dict[str, Any], event_id: Any, lang: str) -> Optional[str]:
    """The city's public page for the event, in the row's language when the
    config has a template for it. `event_page` is one template with {lang}
    (tapahtumat.hel.fi/{lang}/events/{id}) or one per language, because
    www.espoo.fi names the path in each language (/fi/tapahtumat/,
    /sv/evenemang/, /en/events/)."""
    tmpl = inst.get("event_page")
    if isinstance(tmpl, dict):
        tmpl = tmpl.get(lang) or tmpl.get("fi")
    if not tmpl or event_id is None:
        return None
    return tmpl.format(id=urllib.parse.quote(str(event_id), safe=":"), lang=lang if lang in LANGS else "fi")


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
    if _GOVERNANCE_TITLE_RX.search(name):
        return None, "governance meeting (title)"
    if _COURSE_TITLE_RX.search(name) and not _PERFORMANCE_TITLE_RX.search(name):
        return None, "course (title)"
    if _BOOKING_TITLE_RX.search(name):
        return None, "by appointment (title)"
    if _ONLINE_TITLE_RX.search(name) and not _HYBRID_RX.search(name):
        return None, "online (title)"

    place_id = _ref_id(ev.get("location"))
    if _is_online_place(place_id, place):
        return None, "online (location Internet)"
    if place_id in (inst.get("drop_locations") or {}):
        return None, "venue's own calendar already ingested"

    desc = clean_text(_tr(ev.get("description"), lang) or _tr(ev.get("short_description"), lang))
    # Every language's title and text, as plain prose: the rules below read
    # sentences, and "vaatii&nbsp;ilmoittautumisen" in the HTML is not one.
    every_text = clean_text(" ".join(filter(None, (
        _tr(ev.get(f), c) for f in ("name", "description", "short_description") for c in LANGS))),
        limit=100000) or ""

    # Registration is a FIELD here, so it is read from the field: an enrolment
    # window or a Linked Registrations sign-up means the session is not one
    # you turn up to. Espoo's fork says it differently - a hobby catalogue
    # entry (`hobby_categories`) or a sign-up form (`event_registration_link`)
    # - and its rows carry no enrolment fields at all. A catalogue entry that
    # says in words that nobody need register is a drop-in all the same. The
    # header's item 4 says what each costs.
    if ev.get("hobby_categories") and not (_NO_REGISTRATION_RX.search(every_text)
                                           and not registration_required(every_text)):
        return None, "hobby group (Espoo hobby catalogue)"
    if ev.get("enrolment_start_time") or ev.get("enrolment_end_time") or ev.get("registration"):
        return None, "registration required (enrolment)"
    if ev.get("event_registration_link"):
        return None, "registration required (sign-up link)"
    if registration_required(every_text):
        return None, "registration required (text)"
    if _BOOKING_TEXT_RX.search(every_text):
        return None, "by appointment (text)"

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
    if s_utc and e_utc and e_local and _is_placeholder_day(s_local, e_local):
        # 00:00 (or 00:01) to 00:00 (or 23:59) is a DATE typed into a timestamp,
        # not an event at midnight: the repo's idiom (mapsee_ingest_opendata)
        # writes it as an all-day row. 14 kept rows on 2026-10-03.
        s_local, e_local = _placeholder_dates(s_local, e_local)
        s_utc = e_utc = None
        s_dt = datetime.strptime(s_local, "%Y-%m-%d").replace(tzinfo=tz)
        e_dt = datetime.strptime(e_local or s_local, "%Y-%m-%d").replace(tzinfo=tz)
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
    if s_utc and e_utc and _is_series_row(s_dt, e_dt, ev, name):
        return None, "series written as one row (weeks long, short daily hours)"

    if not place:
        return None, "no location" if not place_id else "location not fetched"
    if _is_city_level(place_id, place, inst):
        return None, "location is a whole town"
    lat, lon = _point(place)
    if lat is None:
        return None, "location without coordinates"

    venue = clean_text(_tr(place.get("name"), lang), 160)
    venue = venue.replace("\n", " ") if venue else None
    info = _tr(ev.get("info_url"), lang)
    offer_url = next((_tr(o.get("info_url"), lang) for o in ev.get("offers") or []
                      if isinstance(o, dict) and _tr(o.get("info_url"), lang)), None)
    page = event_page(inst, ev.get("id"), lang)
    # The city's own page for THIS event first. `info_url` is what the
    # publisher typed, and it is often the venue's or a catalogue's front page
    # (Espoo libraries point every row at https://helmet.finna.fi/), while
    # tapahtumat.hel.fi renders every data source's events by id (checked
    # 2026-10-03 for helsinki, kulke, kursor and hkm rows) and espoo.fi the same.
    url = next((u for u in (page, info, offer_url) if isinstance(u, str) and u.startswith("http")), None)

    context = f"{_tr(ev.get('name'), 'en') or ''} {_tr(ev.get('description'), 'en') or ''}"
    details, price_line = admission(ev, lang, context, offer_url or url, card_only, every_text)

    parts: List[str] = []
    if card_only:
        parts.append(inst.get("card_holder_note") or
                     "Only for pensioners and unemployed people who hold a service-centre card "
                     "(ask at the centre's front desk).")
    if price_line:
        parts.append(price_line)
    room = clean_text(_tr(ev.get("location_extra_info"), lang), 80)
    if room:
        room = room.replace(chr(10), " ")
        # "2. krs" is a room; "Pihlajamäen yhteisötalo on kohtaamis- ja
        # tapahtumapaikka kaikenikäisille." is a sentence about the house.
        parts.append(room if len(room) > 50 or room.endswith((".", "!", "?")) else f"Room: {room}")
    if desc:
        parts.append(desc)
    img_url, img_credit = _image(ev, bool(inst.get("event_only_images")))
    # THE LICENCE CONDITION, not a footer: CC BY 4.0 is held on the term that
    # the source is named. Kept short so the sync's _cap_prose protects it as
    # the final paragraph when it trims a long description.
    attribution = inst.get("attribution") or f"Event data: {inst.get('name')} Linked Events, CC BY 4.0."
    if img_url and img_credit:
        attribution += " " + img_credit
    parts.append(attribution)
    description = admission_description("\n\n".join(parts), details) if details else "\n\n".join(parts)

    primary, extras = categorise(ev, inst.get("category", "community"), place)
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


class OutOfTime(Exception):
    """--max-minutes has passed: no request starts after it. What was read
    before it is converted and saved."""


class Walker:
    """GETs against one instance: paced, retried on a server's bad moment,
    stopped on a refusal or the run's deadline, and counted."""

    def __init__(self, session, base: str, delay: float, backoff: float = 5.0,
                 deadline: Optional[float] = None, clock=time.monotonic):
        self.session, self.base, self.delay, self.backoff = session, base.rstrip("/"), delay, backoff
        self.origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(self.base))
        # On `clock`'s scale; None means no deadline (a local run).
        self.deadline, self.clock = deadline, clock
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
            if self.deadline is not None and self.clock() >= self.deadline:
                raise OutOfTime(f"--max-minutes reached before {url}")
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
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
    except (Refused, OutOfTime):
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[linkedevents] {label}: place list failed ({exc}); asking inline instead")
        return None
    return out


def _span_seconds(nev: NormalizedEvent) -> float:
    try:
        s = datetime.fromisoformat(nev.start_local or "")
        e = datetime.fromisoformat(nev.end_local) if nev.end_local else s
    except ValueError:
        return 0.0
    return (e - s).total_seconds()


def ingest_instance(store: EventStore, session, inst: Dict[str, Any],
                    deadline: Optional[float] = None, clock=time.monotonic) -> Dict[str, Any]:
    label = inst.get("name") or inst.get("key")
    tz = _tz(inst.get("timezone"))
    horizon = int(inst.get("horizon_days", DEFAULT_HORIZON_DAYS))
    today = _today()
    now = _now(tz)
    horizon_end = today + timedelta(days=horizon)
    walker = Walker(session, inst["api"], float(inst.get("crawl_delay", 1.0)),
                    float(inst.get("retry_backoff", 5.0)), deadline=deadline, clock=clock)
    t0 = time.monotonic()
    reasons: Dict[str, int] = {}
    kept = 0
    cut = False

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    count = None
    places: Optional[Dict[str, Dict[str, Any]]] = None
    try:
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
        for meta, row in walker.pages("event", params, int(inst.get("max_pages", DEFAULT_MAX_PAGES)), label):
            if count is None:
                count = meta.get("count")
            rid = row.get("id")
            if rid in seen:
                reasons["read twice (paging)"] = reasons.get("read twice (paging)", 0) + 1
                continue
            seen.add(rid)
            rows.append(row)
    except OutOfTime:
        cut = True
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
    for pid in ([] if cut else missing[:lookups]):
        try:
            places[pid] = walker.get(f"{walker.base}/place/{urllib.parse.quote(pid, safe=':')}/")
        except Refused:
            raise
        except OutOfTime:
            cut = True
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[linkedevents] {label}: place {pid} failed: {exc}")
    if len(missing) > lookups:
        print(f"[linkedevents] {label}: {len(missing) - lookups} places left unfetched (cap {lookups})")

    # THE SAME SESSION PUBLISHED TWICE is two ids with one title, one start
    # and one place - a standalone row and its copy inside a series, usually
    # by the same publisher. 62 such pairs on 2026-10-03 (a playground's
    # "viikko-ohjelma" entered once by hand and once as a recurrence). The
    # SHORTER row is kept - the single session over a copy that runs to the
    # series' last date - and then the smallest id, so the choice is the same
    # on every run.
    chosen: Dict[Tuple[str, str, str], Tuple[Tuple[float, str], NormalizedEvent]] = {}
    for row in rows:
        pid = _ref_id(row.get("location"))
        nev, why = to_event(row, places.get(pid) if pid else None, inst, now, horizon_end)
        if nev is None:
            reasons[why] = reasons.get(why, 0) + 1
            continue
        k = (normalize_text(nev.name), nev.start_local or "", pid or "")
        rank = (_span_seconds(nev), str(row.get("id")))
        if k in chosen:
            reasons["same session published twice"] = reasons.get("same session published twice", 0) + 1
            if rank >= chosen[k][0]:
                continue
        chosen[k] = (rank, nev)
    for _rank, nev in sorted(chosen.values(), key=lambda kv: kv[0][1]):
        result = store.upsert(nev)
        if result in ("rejected", "notice"):
            reasons[f"refused by the store ({result})"] = reasons.get(f"refused by the store ({result})", 0) + 1
            continue
        kept += 1
    left = ", ".join(f"{v} {k}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
    if cut:
        print(f"[linkedevents] {label}: CUT SHORT BY --max-minutes - {len(rows)} rows read of "
              f"{count if count is not None else '?'} counted; what was read is kept and saved")
    print(f"[linkedevents] {label}: kept {kept} of {len(rows)} rows read "
          f"({walker.requests} requests, {time.monotonic() - t0:.0f}s); left out: {left or 'none'}")
    return {"kept": kept, "read": len(rows), "reasons": reasons, "requests": walker.requests, "cut": cut}


def main(argv=None, session=None, clock=time.monotonic) -> int:
    ap = argparse.ArgumentParser(description="Import Linked Events (Finnish city event APIs) into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this instance (substring match on name or key)")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline: no request starts after it, and what was read is "
                         f"saved (default {DEFAULT_MAX_MINUTES:g}; 0 = none)")
    a = ap.parse_args(argv)
    if a.max_minutes < 0:
        ap.error("--max-minutes must be non-negative")

    deadline = clock() + a.max_minutes * 60 if a.max_minutes else None
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    try:
        for inst in cfg.get("instances", []):
            if a.only and a.only.lower() not in f"{inst.get('name', '')} {inst.get('key', '')}".lower():
                continue
            if deadline is not None and clock() >= deadline:
                print(f"[linkedevents] {inst.get('name', '?')}: NOT READ - --max-minutes reached")
                continue
            try:
                total += ingest_instance(store, session, inst, deadline=deadline, clock=clock)["kept"]
            except Refused as exc:
                print(f"[linkedevents] {inst.get('name', '?')} REFUSED: {exc} - not retried")
            except Exception as exc:  # noqa: BLE001
                print(f"[linkedevents] {inst.get('name', '?')} FAILED: {exc}")
            store.save()                 # after every instance: a cancelled step keeps what was done
    finally:
        store.save()
    print(f"[linkedevents] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
