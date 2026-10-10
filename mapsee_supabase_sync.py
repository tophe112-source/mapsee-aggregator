#!/usr/bin/env python3
"""
mapsee_supabase_sync.py — the production write_to_mapsee() for the live app.

Pushes aggregated public events (produced by mapsee_ingest.py into
mapsee_events.json) into Mapsee's Supabase `public.events` table as the
"mapsee" host account, so they appear in the Nearby map (events_near RPC).

It UPSERTs via PostgREST using the SERVICE ROLE key, idempotent on
(external_source, external_id) — so re-runs update in place, never duplicate.
Location is filled automatically by the events lat/lon trigger; ends_at is left
NULL (the app's effective_end() treats it as +6h).

PREREQUISITES
  1. Apply migration 0039_aggregated_events.sql.
  2. Create ONE host identity for the aggregator (a Supabase auth user —
     anonymous sign-in once, or a dedicated account). Use its profiles.id below.
  3. Environment (server-side only — never ship the service role key to a client):
        export SUPABASE_URL="https://<project>.supabase.co"
        export SUPABASE_SERVICE_ROLE_KEY="<service_role secret, NOT the anon key>"
        export MAPSEE_HOST_PROFILE_ID="<uuid of the aggregator's profiles.id>"

RUN  (on a host that can reach Supabase — e.g. your machine or CI, NOT the
      Cowork sandbox, whose network is proxy-blocked from Supabase):
        python mapsee_supabase_sync.py --store mapsee_events.json
        python mapsee_supabase_sync.py --store mapsee_events.json --dry-run   # print only

Typical pipeline (cron):
        python mapsee_ingest.py --city "Seattle" --sqlite-db /tmp/mapsee.db --store mapsee_events.json
        python mapsee_supabase_sync.py --store mapsee_events.json
"""
from __future__ import annotations
import argparse
import functools
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from mapsee_music_links import spotify_search_url, youtube_search_url, bandcamp_search_url, SpotifyResolver
from mapsee_ingest import normalize_agenda

try:                                  # resolved against the REAL requests, once
    from requests.exceptions import RequestException as _TransportError
except Exception:                     # requests absent (import-only use): match nothing
    class _TransportError(Exception):
        """Placeholder so the except clauses below stay valid without requests."""

# Map an event's category (from Ticketmaster's segment, when carried) to a
# Mapsee FRONTEND category KEY — must match the keys in site/js/app.js CATEGORIES.
# The UI looks events up by key (catLabel/catEmoji), so a label like "Music"
# renders as "Other". Emit the key; the app supplies the label + emoji.
CATEGORY_KEYS = {
    "music": "music",
    "sports": "sports",
    # Performing arts / stage & screen live in their own 'theater' key now
    # (comedy, standup, plays, broadway, dance, film) — 'arts' stays visual arts.
    "arts & theatre": "theater",
    "arts & theater": "theater",
    "theatre": "theater",
    "theater": "theater",
    "film": "theater",
    "comedy": "theater",
    "festival": "music",
    "food": "food",
    "family": "kids",
    "miscellaneous": "other",
}
DEFAULT_CATEGORY_KEY = "other"   # unknown/unlabeled segment -> Other


MAPSEE_CATEGORY_KEYS = {"running", "fitness", "sports", "music", "food", "community", "party",
                        "market", "outdoors", "arts", "theater", "kids", "learning", "volunteer", "other"}


# Volunteer events hide inside generic civic buckets — a "Green Lake Litter Patrol"
# arrives from the city calendar tagged "community". Promote clearly-volunteer events
# onto the volunteer layer (rose pins) by keyword, but ONLY out of generic categories,
# so a stray word in a concert/theatre title can't hijack a strong one.
_VOLUNTEER_RX = re.compile(
    r"\b(volunteers?|volunteering|clean[\s-]?ups?|stewardship|litter|"
    r"food\s?bank|habitat\s+restoration|mutual\s+aid|work\s+part(?:y|ies)|"
    r"trail\s+work|tree\s+planting|beach\s+clean|park\s+clean|blood\s+drive|"
    r"days?\s+of\s+service|working\s+bees?|bushcare)\b", re.I)   # AU/NZ: a work party
_PROMOTABLE_TO_VOLUNTEER = {"community", "learning", "outdoors", "other"}

# A DESCRIPTION THAT MENTIONS A VOLUNTEER IS NOT A CALL FOR ONE. The rule above
# reads a TITLE, where "Volunteer Shift", "Park Cleanup" and "Blood Drive" mean
# what they say. A description mostly names the people who RUN the event: "a
# trained therapy dog and volunteer handler" (Read to a Dog), "Math Club
# volunteers are available to help tutor", "Food is provided by Three Square
# Food Bank" (Kids Cafe, which lost the kids door to this), "the library will
# handle all the preparation and clean up", and Trumba's footer "Event Type:
# Service & Volunteering". So a description has to ASK: volunteers needed, sign
# up to volunteer, a volunteer session, a morning of service, or name the work
# itself. See _volunteer_hit.
_VOLUNTEER_CALL_RX = re.compile(
    r"\b(volunteers?\s+(?:are\s+|is\s+)?(?:needed|wanted|welcome|required)|"
    r"(?:looking\s+for|seeking|recruiting|needs?)\s+(?:\w+\s+){0,2}volunteers|"
    r"(?:sign(?:ed)?\s+up|register(?:ed)?|come|like|want)\s+to\s+volunteer|"
    r"become\s+an?\s+(?:\w+\s+){0,2}volunteer|community\s+service\s+hours|"
    r"(?:information|info|orientation|training)\s+(?:\w+\s+){0,2}for\s+(?:new\s+|prospective\s+)?"
    r"volunteers|"
    r"volunteer\s+(?:sessions?|shifts?|(?:service\s+)?hours|opportunit(?:y|ies)|orientation|"
    r"work\s?days?)|"
    r"(?:days?|morning|afternoon|weekend)\s+of\s+service|"
    r"habitat\s+restoration|work\s+part(?:y|ies)|working\s+bees?|trail\s+work|"
    r"tree\s+planting|litter\s+pick(?:ing|s)?|beach\s+clean[\s-]?ups?|"
    r"park\s+clean[\s-]?ups?|invasive\s+(?:plant\s+|species\s+|weed\s+)?(?:removal|pull)|"
    r"blood\s+drive)\b", re.I)


def _volunteer_hit(rec: Dict[str, Any]) -> bool:
    """Is this listing a volunteering shift? Any volunteer word in the title, or
    a call to volunteer in the description's opening (_DESC_SCAN_CHARS, where
    every other description rule reads). ONE predicate for the primary and the
    secondary, because a secondary puts the event on the volunteer door too."""
    # A building's opening hours are never a shift. NYC Aging names one older
    # adult centre for its sponsor, "Food Bank For New York City Older Adult
    # Center", and the title's food bank sent it to the volunteer door (1 of 455
    # facility rows, 2026-10-05). A store record carries `sources`, not `source`.
    src = rec.get("source") or ((rec.get("sources") or [{}])[0].get("source")) or ""
    if str(src).startswith("facility-hours:"):
        return False
    if _VOLUNTEER_RX.search(_strip_urls(rec.get("name") or "")):
        return True
    desc = (rec.get("description") or "")[:_DESC_SCAN_CHARS]
    return bool(_VOLUNTEER_CALL_RX.search(_strip_urls(desc)))


# Comedy / standup / film / theater booked at a MUSIC venue inherit that venue
# feed's hardcoded "music" category. High-precision title signals move them onto
# the 'theater' layer. Kept tight (e.g. "comedy night/show", not a bare "comedy"
# that could be a band name) so a real concert is never miscast, and only from
# music/other — a strong existing category (sports, food…) is left alone.
# A PLAY IS NOT THE VERB. Bare `play` sat in this rule, and the theater
# SECONDARY reads descriptions, where the word is what children and mahjong
# players do: of the 2,448 listings in the 2026-09-28 corpus (40,920 distinct
# listings from 831 community and library feeds) that carried theater as a
# secondary, 2,039 carried it on a bare "play" alone - "Stay & Play", "play
# board games", "Toddler Play Time", "Mah-Jongg Mondays Free Play" - and a
# sample of 45 held no stage play at all. Only the phrases that make it a
# work count now: "a play", "the play", "a new play", "play reading". Of the
# 2,039, 24 still match: "Gloria" by Branden Jacobs-Jenkins, a campus
# premiere, a play reading, films "based on a play" - and one toddler
# storytime's "stay for the play!".
_THEATER_RX = re.compile(
    r"\b(stand[\s-]?up|comedy\s+(?:night|show|hour|special|showcase)|open\s+mic\s+comedy|"
    r"improv|sketch\s+comedy|burlesque|drag\s+(?:show|brunch|bingo|race)|"
    r"film\s+screening|movie\s+night|screening\s+of|play\s+readings?|"
    r"(?:a|the|new|this|one[\s-]act|stage|staged|original|award[\s-]winning)\s+play(?![/-])"
    r"(?!\s*(?:dates?|groups?|time|ground|room|area|space|days?|dough|ball|card|house|pen|mat|"
    r"kitchen|centre|center|equipment|based))|"
    r"broadway|matinee|one[\s-]?(?:wo)?man\s+show)\b", re.I)
_PROMOTABLE_TO_THEATER = {"music", "other"}


# NIGHTLIFE -> 'party'. Measured 2026-07-27: 'party' was a valid category key
# that NO adapter ever emitted and no rule ever created - so bar.ventures, whose
# whole identity is the crawl, opened onto an empty layer. Crawls, happy hours,
# trivia and karaoke are genuinely NOT concerts, so they add net-new supply
# rather than relabelling music. Promoted only out of generic buckets, and NEVER
# out of 'music': a DJ set is already a concert, and bar.ventures shows music
# anyway, so restealing it would just mislabel the general map.
_PARTY_RX = re.compile(
    r"\b(bar\s+crawl|pub\s+crawl|happy\s+hour|club\s+night|nightlife|"
    r"dance\s+part(?:y|ies)|silent\s+disco|block\s+part(?:y|ies)|"
    r"karaoke|trivia\s+night|quiz\s+night|bingo\s+night|"
    r"singles?\s+(?:night|mixer)|speed\s+dating|"
    r"launch\s+part(?:y|ies)|after[\s-]?part(?:y|ies)|"
    r"new\s+year'?s?\s+eve|nye\s+part(?:y|ies)|"
    r"rooftop\s+part(?:y|ies)|warehouse\s+part(?:y|ies)|"
    r"cocktail\s+(?:hour|night|class)|wine\s+tasting|beer\s+(?:tasting|festival)|"
    r"tap\s?takeover|pub\s+quiz)\b", re.I)
_PROMOTABLE_TO_PARTY = {"community", "food", "other"}

# Source prefixes of the community-centre timetable adapters. A lane swim or a
# seniors' bingo is not a show: without this the 70,953 PerfectMind sessions of
# 2026-10-03 would each have carried a "More on this show" web search and a
# violet big-venue pin (see to_row).
#
# NOR IS IT NIGHTLIFE. "Music: Karaoke, ages 60+" at 09:30 in a Toronto seniors'
# centre, "Karaoke" at a Helsinki senior centre at 11:00 and a PerfectMind
# "Zumba Party" all matched _PARTY_RX and reached bar.ventures: 16 Toronto, 44
# Linked Events and 150 PerfectMind rows on 2026-10-03/04. A community centre's
# timetable never earns the party door - not as a primary, not as a layer.
#
# Fairfax County's calendar (drupal-fullcalendar:), Montreal's Loisirs sessions
# and Philadelphia's Finder drop-ins joined 2026-10-05: a county's "Boo-gie"
# Halloween dance for families was the party row. Glen Echo Park is NOT here -
# its contra, swing and tango nights are the DC area's social dances, which is
# what the party door is for - and neither are the Revize town calendars, whose
# concerts and festivals are shows.
#
# Joined later on 2026-10-05, each measured: Barcelona's agenda at its centres
# cívics, casals and libraries (1,230 of 1,237 rows took the violet pin, all a
# web search, a centre's ping-pong group included); Hong Kong's SmartPlay
# walk-in sessions (all 2,009; LCSD's cultural programme, lcsd-culture, is an
# agenda of shows and stays out); civic facility opening hours (455 rows - a
# building's weekly hours, and without this every standing row's description
# changed daily, which --skip-unchanged cannot survive); and Madrid's sessions
# at its community places (madrid:centros; madrid:agenda is its shows), which
# write nothing while that config is parked.
CIVIC_TIMETABLE_SOURCES = ("toronto-rec", "toronto-earlyon", "linkedevents:", "perfectmind",
                           "drupal-fullcalendar:", "montreal-loisirs", "phl-parks",
                           "barcelona-agenda", "lcsd-smartplay", "facility-hours:",
                           "madrid:centros")


def _from_civic_timetable(rec: Dict[str, Any]) -> bool:
    src = rec.get("source") or ((rec.get("sources") or [{}])[0].get("source")) or ""
    return str(src).startswith(CIVIC_TIMETABLE_SOURCES)


# KIDS. Measured 2026-07-27: exactly ONE 'kids' event existed across six major
# metros, because only Ticketmaster's rare "family" segment mapped to it - while
# storytimes, family days and children's workshops sat in community/learning.
# Promoted out of generic buckets so the Kids filter reflects what is actually
# happening for families.
# The single largest source of children's programming in the catalogue is the
# public library, and every one of the 58 library feeds in ics_sources.json is
# filed `learning` — correctly, because that is what the calendar as a whole is.
# So this rule is what decides whether `kids` gets any supply at all, and it was
# missing a third of it.
#
# Measured 2026-08-16 over 1,347 distinct live titles from eight public library
# feeds (Fairfax, Arlington VA, Arlington TX, Beverly Hills, Chester County,
# Montgomery County, Brookline, Denver): 124 promoted, and 132 more were plainly
# children's or teen events that did not. The gaps were systematic, not random:
#
#   • TEEN/TWEEN was absent altogether — the single biggest miss. "Teen Book
#     Club", "Tween Crafternoon", "Dungeons & Dragons for Tweens/Teens".
#   • `lego\s+(?:club|build)` missed "LEGO in the Library", "LEGO Fun Time",
#     "Lego Free Play" — the programme is usually named after the brick alone.
#   • `baby\s+(?:time|rhyme|song)` missed "Baby Lap Sit", "Baby Steps",
#     "Bouncing Babies".
#   • "Read to the Dog" is a staple US library literacy programme and matched
#     nothing at all.
#
# Also here: an explicit AGE RANGE ("ages 4-18", "grades K-2"), which is how a
# library says "this is for children" without using any of the words above.
#
# THE RANGE MUST START BELOW 18, and that bound is the whole rule. Written for a
# library title it read as "an age bracket is a children's signal"; used as a
# SECONDARY it also reads DESCRIPTIONS, and an adult listing states its bracket
# there far more often than a library states one anywhere. Measured 2026-09-13
# over 2,000 live event pages sampled from mapsee.me's own sitemaps: _KIDS_RX
# fired on 42, the age range was what fired on 18, and **18 of those 18 had a
# low end of 18 or more** — every one a speed-dating listing enumerating its
# brackets ("Ages 18-32", "Ages 28-40"), none of them a children's programme.
# Zero real children's ranges were lost to the bound, because a library writes
# "ages 4-18" and "grades K-2" and never "ages 30-46". The reported one was
# "Seattle Gay Online Speed Dating", on plansie's kids layer.
#
# PLURALS AND SUFFIXES, SPELLED OUT: the group's trailing \b is the bug the
# outdoors and fitness rules already record, and it had bitten here too.
# `activit` could never match anything, because every real title goes on to
# "Activities" and the boundary refused the "i"; "Family Movies", "Kids Crafts"
# and "Summer Reading Kickoff" missed the same way. Counted 2026-09-28 over
# 40,920 distinct listings from 831 feeds: "Family Movies" alone was 16 of them.
_KIDS_RX = re.compile(
    r"\b(story\s?times?|"
    r"family\s+(?:days?|fun|friendly|workshops?|concerts?|steam|stem|storytimes?|movies?|games?|"
    r"crafts?|nights?|hours?)|"
    r"kids?\s+(?:clubs?|crafts?|workshops?|class(?:es)?|hours?|days?|camps?|activit(?:y|ies))|"
    r"children'?s?\s+(?:workshops?|hours?|programs?|programmes?|crafts?|stor(?:y|ies)|activit(?:y|ies))|"
    r"toddlers?|preschool(?:ers?)?|pre[\s-]?k\b|baby\s+(?:time|rhymes?|songs?)|rhyme\s?time|"
    r"puppet\s+shows?|petting\s+zoo|face\s+painting|egg\s+hunts?|"
    r"lego\s+(?:club|build)|youth\s+(?:programs?|workshops?|clubs?)|"
    r"t(?:w)?eens?|teenagers?|"
    r"(?:baby|babies)\s+(?:bounce|lap\s?sit|steps|play|sign)|"
    r"(?:baby|babies|toddlers?|grown[\s-]?ups?|caregivers?|parents?|dads?|moms?|mums?)\s*(?:and|&|\+|n['’]?)\s*me|"
    r"bouncing\s+babies|"
    r"lego|duplo|brick\s+(?:club|build|night)|"
    r"read\s+(?:to|with)\s+(?:a\s+|the\s+)?(?:dog|therapy\s+dog|teen)|"
    r"minecraft|pok[eé]mon|anime\s+club|"
    r"homeschool|"
    # SCOPED, like every other ambiguous word in this rule, and for the same
    # reason: "infant" is a word people use ABOUT a person, not a statement
    # about who an event is for. Bare, it produced 3 matches across the same
    # 2,000 live pages and all 3 were wrong — a Red Cross certification class
    # ("respond to adult, infant and child victims") and a book group on
    # Beloved ("an enslaved woman who killed her infant daughter"). Zero were
    # real. A library names the session, the way it does for baby and toddler
    # ones a line above: Infant Massage, Newborn Group, Infants & Toddlers.
    r"(?:infants?|newborns?)\s*(?:&|and|/|\+)\s*(?:toddlers?|parents?|caregivers?|carers?)|"
    r"(?:infant|newborn)s?\s+(?:time|stor(?:y|ies)|story\s?time|massage|care|class|group|"
    r"playgroup|play|music|swim|sensory|support|feeding|rhyme|sign)|"
    r"youth\s+(?:crew|group|night|craft|art|writing|hangout)|"
    r"sensory\s+(?:play|story)|messy\s+play|tummy\s+time|"
    r"summer\s+reading\s+(?:kick[\s-]?off|programs?|club)|"
    # HOW A LIBRARY STATES THE AGE IN A TITLE, beyond "ages 4-8": McKinney's
    # LibCal writes "Baby & Me (0-10 Months)", "Mad Science (K-5th Grade)",
    # "Art Challenge (6th-12th Grade)"; Midlothian "for ages 2 to 3" and
    # "2- to 5-year-olds". This rule reads descriptions too, where "a 1-2 year
    # commitment", "3-6 months" and "payment over up to 12 months" are not
    # ages, so a bare range counts only in parentheses or when it says OLD: a
    # range from birth or 0, or one in months that ends in years, is always an
    # age. Years must start by 12 and end by 18, as _KIDS_INTL_RX draws it,
    # and "ages N-M" must end by 24: a working bee "for ages 5 to 95" is
    # everyone's.
    r"ages?\s+(?:1[0-7]|\d)\s*(?:[-–]|to)\s*(?:1\d|2[0-4]|\d)(?!\d)|grades?\s+[kK0-9]|"
    r"(?:k|pre-?k|\d{1,2}(?:st|nd|rd|th)?)\s*(?:[-–]|to|through)\s*\d{1,2}(?:st|nd|rd|th)?\s+grades?|"
    r"(?:birth|newborns?|0)\s*(?:[-–]|to|through)\s*\d{1,2}\s*(?:months?|mos?|years?|yrs?)|"
    r"\d{1,2}\s*(?:months?|mos?)\s*(?:[-–]|to|through)\s*\d{1,2}\s*(?:years?|yrs?)|"
    r"(?<=\()\d{1,2}\s*(?:months?|mos?)?\s*(?:[-–]|to)\s*\d{1,2}\s*(?:months?|mos?)(?=\s*\))|"
    r"(?<=\()(?:1[0-2]|\d)\s*(?:[-–]|to)\s*(?:1[0-8]|\d)\s*(?:years?|yrs?)(?=\s*\))|"
    r"(?:infants?|babies|children|kids)\s+(?:\w+\s+)?up\s+to\s+\d{1,2}\s+months|"
    r"(?:1[0-2]|\d)\s*(?:[-–]\s*(?:to\s*)?|to\s+)(?:1[0-8]|\d)[\s-]*(?:years?|yrs?)[\s-]*olds?)\b", re.I)

# The same widening that catches "LEGO in the Library" catches "Adult LEGO®
# Club", which is a real listing on a real library calendar — libraries run the
# identical programme for grown-ups and say so in the title. Checked only
# against the kids promotion, and only ever to WITHHOLD it, so it can move
# nothing off the layer it is already on.
#
# THE TWO AGE GATES IN IT COULD NEVER FIRE. `18\+` and `21\+` sat inside a
# trailing `\b`, and `+` is not a word character — so the boundary demanded a
# letter or digit immediately after the plus sign, which is exactly what an age
# gate never has. Measured 2026-09-13: "Ages 18+", "21+ only" and "18+ event"
# all returned no match, i.e. both alternatives were dead text from the day
# they were written. They are out of the `\b(...)\b` group now. A senior
# bracket like "Ages 55+" is still not matched HERE, deliberately — that is
# a description shape, and _ADULTS_ONLY_RX below is what reads it.
#
# Dating is here for the same reason "Adult LEGO Club" is: a speed-dating night
# is the adults' version of a listing whose vocabulary otherwise reads young.
# Scoped, not bare — "dating" alone matches "Teen Dating Violence Awareness",
# which is a real library programme and genuinely for teenagers.
_NOT_FOR_KIDS_RX = re.compile(
    r"\b(?:adults?|adult[\s-]only|grown[\s-]?ups?|seniors?|"
    r"speed\s+dating|singles?\s+(?:night|mixer|event|party|social|meetup))\b"
    r"|(?:18|19|20|21)\+"
    # The same statement in the languages _KIDS_INTL_RX reads, as PHRASES:
    # "Spectacle pour enfants ...adultes bien sûr bienvenus !" is a children's
    # show that says grown-ups may come too, and a bare "adultes" would have
    # withheld it. Speed dating in Spanish is here because it was ON the kids
    # layer: four "CITAS RÁPIDAS ... Solteros/as de 48 a 59 años" in Barcelona,
    # 2026-09-24.
    r"|\bf[üu]r\s+erwachsene|\bpour\s+(?:les\s+)?adultes|\badultes\s+uniquement"
    r"|\bpara\s+adultos|\bsolo\s+adultos|\bper\s+adulti|\bvoor\s+volwassenen"
    r"|\bf[öo]r\s+vuxna|\bfor\s+voksne|\baikuisille|\bdla\s+doros[łl]ych"
    r"|\bpro\s+dosp[ěe]l[ée]|大人限定|大人向け|大人のみ|成人限定"
    r"|\bcitas\s+r[áa]pidas|\bsolter[oa]s\b", re.I)

# KIDS, IN THE LANGUAGES THE MAP ACTUALLY SPEAKS. _KIDS_RX is English, and the
# kids door is the emptiest on the map because of it. Measured 2026-09-24 with
# categories_near over the 261 swept metros, next 14 days: fewer than 5 kids
# events in 178 of them, none at all in 108 - Germany 13 of 14 metros thin
# while its median metro held 243 community events, France 12 of 13 with 265.
# The supply was not missing, it was unlabelled, which is fleabop's lesson one
# door along (`Flohmarkt` and `brocante` are why _MARKET_RX has loanwords).
#
# Every term below was counted over 18,137 distinct live titles from 60 metros
# in 13 languages (events_near, promotable categories, three weekly windows)
# and every hit was read. The corpus decided the SHAPE as much as the words:
#   - bare `enfants` is 5 job adverts in 18 (childcare-worker recruitment, a
#     children's hospital hiring), so French says "pour enfants";
#   - bare `famille` is honey brands and home-care training, and bare
#     `Familien...` is family-constellation therapy, genealogy and counsellor
#     training, so family words are the listed compounds that meant an outing;
#   - `Kita` was Tokyo's Kita ward every single time, `baby foot` is French
#     for table football, `Born`/`Quickborn` are German place names, and
#     "Big Bruce & the Retro Kids" is a band, so none of those are here;
#   - all 35 Spanish age ranges were ADULT brackets ("20-45 años"), so an age
#     range must start at 12 or under and end by 18, and only the French forms
#     scored at all - and never "3 à 5 ans d'expérience", a job advert's range
#     on a map that carries French recruitment sessions by the dozen.
# A word that scored zero is absent however obvious it looks (bambini,
# crianças, cuentacuentos, Vorlesestunde) - the same rule as `bandshell` in
# the music secondary. Add one when a corpus shows it.
#
# TITLE ONLY, for the primary AND the secondary. In a description these words
# are prices, not audiences: "gratuit pour les enfants de moins de 12 ans" is on
# every other concert page, and "Kinder bis 6 Jahre frei" on every German one.
_KIDS_INTL_RX = re.compile(
    # French
    r"\b(?:pour|avec)\s+(?:les\s+)?(?:jeunes\s+|petits\s+)?enfants?\b|\bparents?[\s-]+enfants?\b"
    r"|\benfants\s+uniquement\b|\bjeune[\s-]public\b|\bheure\s+du\s+conte\b|\btout[\s-]petits\b"
    r"|\b(?:ateliers?|visites?|spectacles?|mercredis|samedis|dimanches|danse|lectures?)\s+en\s+famille\b"
    r"|\bvisites?\s+familiales?\b"
    r"|\b(?:d[èe]s|[àa]\s+partir\s+de)\s+[1-9]\s*ans\b(?!\s*d['’]\s*(?:exp|anc))"
    r"|\b(?:1[0-2]|[0-9])\s*(?:[-–/]|[àa])\s*(?:1[0-8]|[0-9])\s*ans\b(?!\s*d['’]\s*(?:exp|anc))"
    # Catalan / Spanish / Portuguese - the accent is required: `bebe` is "drink"
    r"|\bhora\s+del\s+(?:conte|cuento)\b|\ben\s+fam[íi]lia\b|\b(?:b[ée]bé|bebê)s?\b"
    # German / Dutch
    r"|\bf[üu]r\s+(?:kinder|kids|familien)(?![\w-])|\beltern[\s-]kind\b|\bkrabbel|\bkasperl(?:e|theater)?\b"
    r"|\b(?:klein)?kinder[\s-]*(?:theater|bibliothee?k|bücherei|club|clubhaus|haus|hof|treff|treffen"
    r"|zentrum|freizeit\w*|kirche|gottesdienst|park|disco|schnupper\w*|hüeti)"
    r"|\bkinder[\s-]+(?:und|&)\s+jugend(?:zentrum|club|haus|treff|einrichtung|liche\b)"
    r"|\bfamilien[\s-]*(?:zentrum|haus|treff|führung|gottesdienst|chor|bereich|begegnungs\w*)"
    r"|\bjugend(?:zentrum|club|treff|treffen|haus|caf[eé]|raum|freizeit\w*|arbeit|bibliothek|kulturhaus)"
    r"|\bhaus\s+der\s+jugend\b|\bkinderen\b|\bjeugd(?:huis|club|punt)"
    # Nordic, Finnish, Polish, Czech, Italian
    r"|\bf[öo]r\s+barn\b|\bfamiljetr[äa]ff|\bbaby[\s/-]*(?:club|klub|caf[eé]|f[öo]r[äa]ldracaf[eé]|dans"
    r"|salmesang|meet[\s-]?ups?)|\bbørne(?:gudstjeneste|nes\b|ne\s+synger)"
    r"|\blapsille\b|\blasten(?:tapahtuma|\s+oma\b)|\bperhe(?:sport\w*|liikun\w*|kahvila|talo)"
    r"|\bvauva(?:kerho\w*|treff\w*)"
    r"|\bdla\s+dzieci\b|\bwarsztaty\s+rodzinne\b|\brodzinnie\b|\bbajkow\w+"
    r"|\bpro\s+d[ěe]ti\b|\bpro\s+rodiny\b|\bdei\s+ragazzi\b|\bper\s+le\s+famiglie\b"
    # Japanese
    r"|子ども|子供|こども|キッズ", re.I)

# What BACKS a keyword sweep's `kids` guess, which is a different question from
# what may PROMOTE an event onto the layer - see _refused_sweep_guess. Wider
# than _KIDS_RX because it can only ever KEEP a guess the source already made,
# never create one: "Family Picnic ~ International Mom & Kids Circle" and
# "English-Speaking Parents & Kids (children aged 3-8)" are real Meetup
# children's events that _KIDS_RX does not match. Not bare `family` - that is
# the very keyword that fetched "Family Constellations" - and not bare `child`,
# which is how "Healing the Inner Child" reads.
_KIDS_BACKING_RX = re.compile(
    r"\b(?:kids?|children|toddlers?|babies|preschoolers?|little\s+ones|strollers?"
    r"|(?:parents?|moms?|mums?|dads?|caregivers?)\s*(?:&|and|\+|with)\s*(?:kids?|children|tots?|little\s+ones|toddlers?|babies))\b",
    re.I)

# The guard the SECONDARY may use, which is a different question. The rule above
# reads a TITLE, where a bare "adult" is a statement about who the event is for.
# The kids secondary reads the description, where it is usually a statement about
# who else is in the room — and over the same 2,000 live pages, applying the
# title guard to descriptions would have withheld the layer from three rows of
# which one, "Homeschool Days", is unambiguously a children's event: its blurb
# prices "Students (4 – 18): $15" beside "Adults (19 & older): $22.50". A price
# table is not an age policy.
#
# So this is only the phrases that can mean nothing except "this event is for
# adults", each one carrying its own age or its own format. Same shape as
# _NOT_A_MARKET_RX and _FITNESS_METAPHOR_RX: narrow, listed, and only ever
# withholding.
_ADULTS_ONLY_RX = re.compile(
    r"\badults?[\s-]only\b|\b(?:18|19|20|21)\+|"
    r"\bages?\s+(?:1[89]|[2-9]\d)\+|"
    r"\b(?:must\s+be|aged?)\s+(?:at\s+least\s+)?(?:18|19|20|21)\s*(?:\+|or\s+(?:over|older|above)|and\s+(?:over|older|above)|years?)|"
    r"\bover[\s-](?:18|21)s?\b|\bno\s+(?:one\s+)?under\s+(?:18|21)\b|\bno\s+minors\b|"
    r"\bspeed\s+dating\b|\bsingles?\s+(?:night|mixer|event|party|social|meetup)\b", re.I)

# ENGLISH WORDS THAT NAME A CHILDREN'S AUDIENCE ONLY WHEN THEY ARE IN A TITLE,
# the same reasoning as _KIDS_INTL_RX: in a description "kids" is a price ("kids
# under 12 free") or who else may come, and in a title it is who the event is
# for. Read over 40,920 distinct listings from 831 community and library feeds
# (2026-09-28), every term below scored and every hit was read:
#   - bare `kids` was the biggest single miss: 153 distinct titles in 87 feeds
#     reached no kids door ("STEAM for Kids", "Kids Café", "Ukulele for Kids",
#     Deschutes Land Trust's "Nature Kids"). 9 were wrong, and 8 of those are
#     talks for the PARENTS ("AI and Your Kids", "Parenting Kids in Turbulent
#     Times"), a toy drive or "Kids Eat Free", which _ABOUT_KIDS_RX withholds;
#     the ninth is a lecture, "Leave Them Kids (and Retirees) Alone!". Not the
#     singular: `kid` is Kid Rock, The Karate Kid and Ugly Kid Joe;
#   - a `little`, `mini`, `young` or `junior` word only with the noun that
#     makes it a children's programme: "Little Explorers" 20, "Little Learners"
#     15, "Mini Makers" 7, "Junior Scientists". Not bare: Little Women, Little
#     Shop of Horrors, Little Falls, Young Frankenstein, MLK Jr. Day, Young at
#     Heart (a seniors' club), the Junior Women's Club;
#   - babies with a library's word for them ("Book Babies", "Library Babies",
#     "Books and Babies"), tots and tykes, playgroups and playdates, "Tales for
#     Twos and Threes";
#   - `family` with an activity, never alone: "Family Art Lab", "Family Dance
#     Party", "Family Sunday", while "Family History", "Family Law" and "The
#     Family Stone" are not for children - nor a university's "Family Weekend",
#     which is the students' parents, nor "Writing Family Stories".
# Never from a performance primary - see _NO_KIDS_TITLE_SECONDARY_FROM.
_KIDS_TITLE_RX = re.compile(
    r"\b(?:kids|kid['’]s|kids['’])(?![\w'’])|"
    r"\bfor\s+(?:young\s+)?families\b|"
    r"\blittle\s+(?:(?:nature|history|art|science|music|lego)\s+)?(?:ones|hands|sprouts?|explorers?|"
    r"learners?|readers?|creators?|makers?|artists?|scientists?|listeners?|builders?|crafters?|"
    r"cooks?|chefs?|movers?|lifeguards?|lifesavers?|brushes|stompers)\b|"
    r"\bmini\s+(?:makers?|painters?|chefs?|scientists?|explorers?)\b|"
    r"\byoung\s+(?:explorers?|scientists?|children|audiences?|artists?|readers?|learners?|makers?|"
    r"naturalists?)\b|"
    r"\b(?:junior|jr\.?)\s+(?:book\s+club|police|explorers?|artists?|scientists?|steam|stem|kitchen|"
    r"chefs?|rangers?|naturalists?|robotics|makers?)\b|"
    r"\b(?:books?|library|lap|rhyme|music|sign(?:ing)?|story|parents?|mums?|moms?|mothers?)\s*"
    r"(?:(?:and|&|\+|with|for)\s*)?babies\b|\bfor\s+babies\b|"
    r"(?<!tater\s)\b(?:tiny\s+)?(?:tots|tykes)\b|"
    r"\bplay\s?groups?\b|\bplay\s?dates?\b|"
    r"\btwos\s*(?:&|and|\+|n)\s*threes\b|\b(?:for|with)\s+twos\b|\bterrific\s+twos\b|"
    r"\bstroller\s+(?:walks?|strides?|derby|tours?)\b|"
    r"\bfamily\s+(?:art|arts|dance|festival|fest|music|literacy|sundays?|saturdays?|fridays?|"
    r"colou?ring|yoga|films?|puzzles?|chess|nature|science|makerspace|board\s+games?|picnic|"
    r"story\s+hours?|books?|zines?|bingo|trivia|skate|swim|hikes?|walks?|campout|"
    r"paint\w*|pottery|cooking|lego|tours?|drop[\s-]?in|programs?|programmes?|play|playtime)\b",
    re.I)

# A band called "Retro Kids" and a play called "The Kids Are Alright" are names,
# the rule _NO_FITNESS_SECONDARY_FROM applies to "The Rowing Club".
_NO_KIDS_TITLE_SECONDARY_FROM = {"music", "theater", "party"}

# A TALK FOR THE PARENTS NAMES THE CHILDREN, and every kids word then fires on
# it. "Parenting Your Toddler" was PROMOTED to kids by `toddler`; with bare
# `kids` added, "AI and Your Kids: An Open Conversation", "Parenting Kids in
# Turbulent Times" and "How to Launch Financially Independent Kids" would have
# followed, and so would "Firefighters for Kids Toy Drive", where the children
# are who the toys are FOR. Only phrases that make the children the topic or
# the beneficiary, so "Storytime with Your Toddler" is untouched. Checked with
# the title guard, and like it only ever withholds.
_ABOUT_KIDS_RX = re.compile(
    r"\b(?:parenting|raising|teaching|helping|launch\w*|protecting|supporting|talking\s+(?:to|with))\s+"
    r"(?:\w+\s+){0,2}(?:kids|children|child|teens?|tweens?|toddlers?|famil(?:y|ies))\b|"
    r"\b(?:and|&)\s+your\s+(?:kids|children|child|teens?|tweens?)\b|"
    r"\bparents?\s+of\s+(?:\w+\s+){0,3}(?:kids|children|teens?|tweens?)\b|"
    r"\bparent\s+caf[eé]\b|\bnot\s+just\s+for\s+kids\b|\bkids\s+eat\s+free\b|"
    r"\b(?:toy|coat|book|food|supply|school\s+supplies|diaper|clothing)\s+drive\b|"
    # and a title that says the children are NOT coming: "Brunch Club (no
    # kids!)", "Kids-Free Night Out". Here and not in the title guard, because
    # the co-audience exception below would read the word "kids" as a child.
    r"\bno\s+kids\b|\bkids?[\s-]free\b|\bwithout\s+(?:the\s+|your\s+)?kids\b|\bchild[\s-]?free\b", re.I)

# THE TITLE GUARD WAS ONLY EVER ASKED ABOUT THE PRIMARY, so "LEGO for Adults",
# "Adult Story Time -- Ghost Stories" and "Adults Read YA Book Club" kept `kids`
# as a SECONDARY off the very words the guard had refused to promote them on.
# Of the 85 listings in the 2026-09-28 corpus whose title says adult, senior
# or grown-up and still reached kids, 25 were for adults alone; the rest also
# named a young audience in the same title - "Teen & Adult Crochet Club",
# "Grown Ups & Me Halloween Storytime", "Fun soccer for seniors and kids" -
# and those keep it. "Adult 101" is LA County's life-skills series for teens,
# "Young Adult" is a library's word for them, and "Donuts with Grown Ups Story
# Time" brings the child.
_KIDS_CO_AUDIENCE_RX = re.compile(
    r"\b(?:kids?|children|child|teens?|tweens?|youth|famil(?:y|ies)|toddlers?|bab(?:y|ies)|"
    r"preschool\w*|grades?|ages?\s+\d|adult(?:ing)?\s+101|young\s+adults?|"
    r"with\s+(?:your\s+|a\s+|their\s+)?grown[\s-]?ups?)\b|(?:&|\band)\s*me\b", re.I)

_PROMOTABLE_TO_KIDS = {"community", "learning", "arts", "outdoors", "other"}


# FITNESS. The gap wegosie.com exposed: a community yoga class, a beginners'
# climbing night and a Saturday bootcamp all arrived tagged 'community',
# 'learning' or 'other', so a lens about moving your body opened onto almost
# nothing. This promotes them onto their own layer.
#
# Precision matters more here than anywhere else because the vocabulary overlaps
# other categories badly:
#   • "boxing" is also Boxing Day       • "spin"/"row"/"ride" are common words
#   • "walk"  is also an ART walk       • "class" is also a LEARNING class
# So single ambiguous words are always qualified, and the whole rule only fires
# out of generic buckets — a real sports fixture or a concert is never restolen.
_FITNESS_RX = re.compile(
    r"\b(yoga|vinyasa|hatha|ashtanga|pilates|barre\s+class|zumba|aerobics|"
    r"tai\s?chi|qi\s?gong|"
    r"hiit\b|crossfit|calisthenics|kettlebell|bootcamp|boot\s+camp|"
    r"strength\s+training|weight\s+training|circuit\s+training|"
    r"spin\s+class|spinning\s+class|indoor\s+cycling|"
    r"fitness\s+(?:class|session|challenge)|workout|exercise\s+class|"
    r"martial\s+arts|karate|judo|jiu[\s-]?jitsu|taekwondo|kickboxing|muay\s+thai|"
    r"boxing(?!\s+day)|"
    r"rock\s+climbing|bouldering|climbing\s+(?:gym|night|session)|"
    r"group\s+(?:ride|walk)|bike\s+ride|cycling\s+club|gran\s+fondo|charity\s+ride|"
    r"walking\s+(?:club|group)|"
    r"lap\s+swim|open\s+water\s+swim|masters\s+swim|swim\s+lesson|"
    r"triathlon|duathlon|obstacle\s+race|tough\s+mudder|spartan\s+race|"
    r"rowing\s+club|learn\s+to\s+row|erg\s+session|"
    r"snowshoe|cross[\s-]?country\s+ski|ski\s+(?:trip|club|day|tour)|snowboard)\b", re.I)
# NOT promotable out of 'outdoors': a hike or a ski trip should keep its green
# pin and stay on the outdoors layer — it reaches wegosie as a SECONDARY instead
# (see _SECONDARY_RX). Nor out of 'sports': a league fixture is already sport.
#
# 'food' IS here, added 2026-08-12 on evidence. A user screenshot showed "Gentle
# Morning Hatha Yoga" rendered as Food & Drink, and the category turned out to be
# badly polluted: of 1,000 upcoming food-classified events, only 16% had a food
# word in the TITLE at all, while yoga, pilates, tai chi, qigong, zumba, karate
# and climbing nights sat inside it. Meetup tags an event with whichever sweep
# found it, exactly as the _WEAK_KEY_FOR_FITNESS note describes for 'party' —
# so a food key from that source is not the deliberate classification it looks
# like. Measured over those titles this rule moves 24 events, of which one is
# genuinely both ("BIKE RIDE & AUG BDAYS! LUNCH @ …"); it keeps food as a
# SECONDARY, which is what that mechanism is for.
_PROMOTABLE_TO_FITNESS = {"community", "learning", "other", "food"}

# Unambiguous exercise words — ones that cannot plausibly appear in a nightlife,
# market or concert listing. Because they cannot, this is the ONE fitness rule
# allowed to read the description as well as the title, and the one allowed to
# override a source's own key.
_FITNESS_STRONG_RX = re.compile(
    r"\b(work\s?outs?|working\s+out|yoga|pilates|hiit\b|crossfit|bootcamp|boot\s+camp|"
    r"calisthenics|kettlebell|strength\s+training|circuit\s+training|spin\s+class|"
    r"exercise\s+(?:class|station|routine)s?)\b", re.I)
# Keys that came from a SEARCH KEYWORD rather than from the listing's content.
# Meetup tags an event with whichever term found it, so a station-to-station
# workout in a park arrived as 'party' purely because the "dance" sweep matched
# it. Those guesses lose to strong evidence. A deliberate 'sports', 'music' or
# 'food' key is real classification and is left alone.
_WEAK_KEY_FOR_FITNESS = {"party", "community", "learning", "other"}


# ---------------------------------------------------------------------------
# SECONDARY categories (migration 0108: events.categories, max 2 extras).
#
# Every rule above REPLACES the primary, which throws information away: a food
# festival matching _PARTY_RX became 'party' and stopped being food, so it left
# oneday.cafe entirely. Secondaries fix that in both directions —
#   1. a demoted primary is kept as a secondary, and
#   2. cross-cutting signals are added, so a vintage market with a band and food
#      trucks reaches fleabop AND bar.ventures AND oneday.cafe from one row.
#
# These are strictly ADDITIVE — they widen where an event surfaces and never
# change its pin, colour or primary key. That lower stake is why they may scan
# the description, which the primary rules deliberately don't: the signal for
# "with food trucks and live music" is almost never in the title. The regexes
# stay multi-word for the same reason a bare "food" would tag every concert
# whose blurb says "no outside food".
_SECONDARY_RX = [
    # SECOND-HAND IS THE HALF THIS RULE WAS MISSING. fleabop.com is "Flea
    # Markets, Clothing Swaps and Vintage Near You" and it was the thinnest lens
    # on the map: measured 2026-08-16, 3,469 events with `market` as primary, of
    # which 2 had "flea" in the title and 0 had thrift, vintage, swap or
    # antique — 46.6% were farmers markets, and market_sources.json is a
    # farmers-market file end to end (85 uses of "farmers", 0 of "flea").
    #
    # The supply was not missing, it was UNLABELLED. ~230 upcoming events name
    # second-hand retail in their title and sat on community (100), music (44),
    # other (24) and arts (13) instead. This regex is why: it asked only for
    # English, and only for the shopping words, so "Flohmarkt am Arkonaplatz"
    # and "Fashion Thrift Society Sydney" both missed.
    #
    # Additive by construction — a secondary never changes a pin, colour or
    # primary key — so widening it can put a market on fleabop but can never
    # take a concert off vivosie.
    #
    # SCOPED, not bare. "thrift" alone matches "Tuesdays Thrifty Theater Night"
    # and "Monday Night Trivia: Thrift Shop Bulls**t"; "vintage" alone matches
    # vintage guitars, vintage wine and vintage baseball. Each English word
    # carries the noun that makes it retail. The loanwords do NOT need scoping
    # and deliberately have none: Flohmarkt, brocante and vide-grenier mean
    # exactly one thing, and Flohmarkt is a COMPOUND — Garagenflohmarkt,
    # Frauenflohmarkt, Musik-Flohmarkt are all real live titles — so it must
    # match as a suffix, which is why it carries no leading \b.
    #
    # THE COMMONEST SPELLING OF THE COMMONEST MARKET COULD NOT MATCH, and nor
    # could any plural. `farmers?\s+market` refuses "Farmer's Market" (19
    # listings in 7 feeds of the 2026-09-28 corpus, 40,920 distinct listings
    # from 831 feeds) and "Farmers’ Market", and the trailing \b refuses
    # "Community Markets" and "Pre-Loved Markets" — the plural bug the kids,
    # outdoors and fitness rules record. A market is also named for its DAY,
    # its HOUR or its SEASON far more often than for what it sells: "Sunday
    # Market" 14, "Produce Market" 15, "Community Market", "Christmas Market",
    # "Twilight Market". Business words are why none of this is bare `market`:
    # stock, job, housing, "Recommendation Algorithms, Markets, and Society",
    # and "Arbeitsmarktberatung" is job-market counselling, so German takes the
    # compounds that scored (Weihnachtsmarkt, Kunsthandwerkerinnenmarkt,
    # Nostalgiemarkt, Abendmarkt) and French takes marché only with the word
    # that makes it one ("marché aux puces", "marché des producteurs"). The
    # seasons ride only on a noun that sells: "Spring Market Update" is a
    # realtor's, and no qualifier survives "research", "trends" or "update"
    # after it ("Art Market Trends" is a gallery talk). "Food Swap" is
    # Community Plate's produce swap, the swap fleabop is for.
    ("market", _MARKET_RX := re.compile(
        r"(\b(flea\s+markets?|farmers?['’]?s?\s+markets?|night\s+markets?|craft\s+fairs?|"
        r"makers?['’]?\s+markets?|artisans?['’]?\s+markets?|vintage\s+(?:markets?|fairs?|sales?|shows?|pop[\s-]?ups?)|"
        r"swap\s+meets?|clothing\s+swaps?|rummage\s+sales?|estate\s+sales?|holiday\s+markets?|"
        r"bazaars?|street\s+fairs?|pop[\s-]?up\s+shops?|craft\s+markets?|record\s+fairs?|"
        r"(?:sunday|saturday|friday|thursday|wednesday|tuesday|monday|weekend|twilight|moonlight|"
        r"evening|midnight|morning|christmas|xmas|festive|halloween|harvest|produce|farm|growers['’]?|"
        r"producers['’]?|flower|plant|art|arts|artists['’]?|gift|community|village|street|"
        r"pre[\s-]?loved|preloved|mobile|merry|school)\s+markets?"
        r"(?!\s+(?:research|analysis|updates?|reports?|trends?|values?|insights?|outlook|data|share))|"
        # second-hand, thrift and reuse
        r"thrift\s+(?:stores?|shops?|markets?|sales?|fairs?|society|pop[\s-]?ups?)|thrifting|"
        r"thrift\s*(?:&|and)\s*vintage|"
        r"second[\s-]?hand\s+(?:markets?|shops?|sales?|fairs?)|"
        r"antiques?\s+(?:markets?|fairs?|shows?|malls?|sales?)|antiquing|"
        r"(?:car\s+)?boot\s+sales?|jumble\s+sales?|garage\s+sales?|yard\s+sales?|"
        r"charity\s+shops?|op\s+shops?|consignment\s+sales?|"
        r"(?:book|toy|plant|seed|clothes|clothing|kit|food)\s+swaps?|swap\s+shops?|"
        r"repair\s+caf[eé]s?|upcycling\s+(?:markets?|fairs?)|"
        r"record\s+swaps?|vinyl\s+fairs?|car\s+boot)\b|"
        # Loanwords: unambiguous in their own language, and Flohmarkt/Trodelmarkt
        # compound freely, so no leading word boundary on those two.
        r"flohmarkt|tr[oö]delmarkt|tauschb[oö]rse|"
        r"\b(?:weihnachts|kunsthandwerk(?:er(?:innen)?)?|nostalgie|abend|cadeau)[\s-]?markt|"
        r"\bjule?marked\b|\bmarch[ée]s?\s+(?:aux\s+puces|des?\s+producteurs|d['’]artistes|du\s+village)\b|"
        r"\b(brocante|vide[\s-]?greniers?|mercadillo|rastro|rommelmarkt|"
        r"loppis|kirpputori|mercatino|feira\s+da\s+ladra|pchli\s+targ)\b)", re.I)),
    # BRUNCH WAS MISSING, and it is the single commonest food word on the map.
    # Measured 2026-08-16: 615 upcoming events with "brunch" in the title, of
    # which only 158 reached oneday.cafe — 457 sat on community (235), theater
    # (78) and music (37) instead. oneday is the second-thinnest lens and food
    # is its ONLY category, so that is a third of its potential supply.
    #
    # A brunch is a meal whatever else is happening at it. "Burlesque Brunch",
    # "Golden Girls Drag Brunch" and "Gospel Brunch: The Moriah Sisters" are all
    # people eating, and because this is a SECONDARY they keep theater or music
    # as their primary and reach oneday as well — which is exactly the case the
    # secondaries column was added for. `taproom` and `distillery` are the same
    # omission one size down: `brewery` was here and its two siblings were not.
    ("food", re.compile(
        r"\b(food\s+trucks?|food\s+hall|food\s+vendors?|supper\s+club|tasting\s+menu|"
        r"pop[\s-]?up\s+(?:dinner|kitchen)|beer\s+garden|brewery|winery|cidery|"
        r"wine\s+tasting|beer\s+(?:tasting|festival)|bbq|barbecue|"
        r"chili\s+cook[\s-]?off|bake\s+sale|farm\s+dinner|potluck|"
        r"brunch|taproom|tap\s+takeover|distillery|bottomless)\b", re.I)),
    # LOCAL LIVE MUSIC, which this asked for in almost none of the ways a town
    # names it. vivosie is "Live Music Near You Tonight" and the free outdoor
    # summer concert is the most characteristic thing it could open onto, and
    # the rule wanted the exact phrase "concert series" — so "Concert in the
    # Park - Radio Replay" reached no music layer at all while "Summer Concert
    # Series" did. Measured 2026-09-13 over 3,160 DISTINCT live titles from 335
    # civic community calendars: 63 titles are about music and this caught 12.
    # The other 51 were symphonies, community concert bands, mariachi in the
    # park, Oktoberfest concerts and tribute acts.
    #
    # Each addition was counted over that corpus before it went in, because the
    # ambiguous ones are ambiguous in a way only a real sample shows:
    #   • `concerts?`  69 hits, 69 of them genuine live music. Read every one.
    #   • symphony/philharmonic/orchestra  11 hits, all genuine (a ballet with a
    #     live orchestra is live music too).
    #   • `bands?`  25 hits, 24 genuine — the one miss is the FILM "Trolls Band
    #     Together", hence the lookahead, which also covers the idiom.
    #   • `jazz` had to be QUALIFIED: 6 of its 10 bare hits are Jr. Jazz youth
    #     BASKETBALL. Same trap as `market` being a business word first.
    #   • `music` had to be qualified too: bare, it takes "Bill & Ted Face the
    #     Music", "Music Together" (a toddler class) and "Family Music Bingo".
    #   • `choir` is deliberately absent: half its hits are "Choir Practice",
    #     and a rehearsal is not a gig.
    #   • `bandshell` is absent because it scored ZERO, not because it is wrong.
    ("music", re.compile(
        r"\b(live\s+music|live\s+bands?|dj\s+set|open\s+mic|acoustic\s+set|"
        r"concerts?|symphon(?:y|ic)|philharmonic|orchestras?|"
        r"music\s+(?:series|festival|night|of)\b|music\s+(?:in|on)\s+the\b|"
        r"live\s+jazz|jazz\s+(?:night|series|band|brunch|concert|ensemble|trio|"
        r"quartet|orchestra|jam)|jazz\s+at\b|"
        r"drum\s+circle)\b|"
        # Not the bands a fitness class hands out: "resistance bands" and "wrist
        # bands provided on deck" put 568 PerfectMind workouts and swims on the
        # music layer (2026-10-04).
        r"(?<!resistance )(?<!wrist )(?<!elastic )(?<!exercise )(?<!rubber )(?<!loop )"
        r"\bbands?\b(?!\s+together)", re.I)),
    # `kayak` and `canoe` COULD NOT MATCH THE WORD PEOPLE ACTUALLY WRITE. Both sit
    # inside the group's trailing `\b`, so the boundary demanded a non-word
    # character straight after "canoe" — and every real listing says "Canoeing"
    # or "Kayaking". `hik(?:e|ing)` one line above shows the suffix was meant to
    # be there; these two and `snowshoe` were simply missed. Exactly the family
    # of the `18+` age gate whose `+` could never end a `\b`.
    #
    # Also added, each counted over 6,000 DISTINCT live titles from 490 civic and
    # park feeds (2026-09-13) before it went in:
    #   • `birding` / bird walk  17 hits, 16 of which reached NEITHER layer.
    #     `bird watching` was already here; nobody writes that on a calendar —
    #     they write "Bird Walk at Brust Park" and "Beginning Birding".
    #   • kayaking/canoeing      2 hits, both missed, both unambiguous.
    #
    # AND THE ONE THAT LOOKS OBVIOUS AND IS WRONG: a bare `walks?` scores 77 hits
    # and 74 of them reach neither layer CORRECTLY — "Art & Wine Walk", "Historic
    # Cemetery Walk", "Luminary Walk", "A Walk in Their Shoes - Dementia
    # Simulation Workshop". On a town calendar a "walk" is an art crawl or a
    # fundraiser far more often than it is exercise, so it stays qualified.
    # Bare `paddle` is the same trap one size down: 5 hits, and 4 are "Paddle
    # Battle", "Battle of the Paddle" and "Doggie Paddle Day".
    ("outdoors", re.compile(
        r"\b(hik(?:e|ing)|trail\s+(?:run|walk|day)|nature\s+walk|guided\s+walk|"
        r"bird\s?watching|birding|bird\s+(?:walk|hike)|"
        r"kayak(?:ing)?|canoe(?:ing)?|snowshoe(?:ing)?|paddling|paddle\s?board|"
        r"camp(?:ing|out)|"
        r"beach\s+clean|garden\s+tour|stargazing|tide\s?pool)\b", re.I)),
    # Placed directly after 'outdoors' and BEFORE 'learning' on purpose. Only two
    # slots exist, and this is what fills wegosie.com: a hike or a ski trip keeps
    # 'outdoors' as its primary and reaches the movement lens through here. Ahead
    # of 'learning' because that regex claims "bootcamp", which is a fitness word
    # far more often than an educational one.
    # Wider than _FITNESS_RX above: that one has to be safe enough to REPLACE a
    # primary, whereas this only ever adds a layer, so the human-powered outdoor
    # verbs (hike, paddle, climb) are welcome here even though they are too
    # generic to re-key an event on their own.
    ("fitness", re.compile(
        r"\b(yoga|pilates|barre|zumba|aerobics|tai\s?chi|hiit\b|crossfit|bootcamp|"
        r"boot\s+camp|kettlebell|calisthenics|strength\s+training|circuit\s+training|"
        r"spin\s+class|indoor\s+cycling|fitness|workout|exercise\s+class|"
        r"martial\s+arts|karate|judo|jiu[\s-]?jitsu|taekwondo|kickboxing|muay\s+thai|"
        r"boxing(?!\s+day)|rock\s+climbing|bouldering|climbing\s+(?:gym|night|session)|"
        r"hik(?:e|ing)|trail\s+run|snowshoe|cross[\s-]?country\s+ski|ski\s+(?:trip|tour|day)|"
        r"snowboard|group\s+(?:ride|run|walk)|bike\s+ride|cycling\s+club|gran\s+fondo|"
        # The -ing forms, for the reason the outdoors rule above records: the
        # trailing \b meant "Kayaking" and "Canoeing" — the only spelling a real
        # listing uses — could never match.
        r"kayak(?:ing)?|canoe(?:ing)?|paddle\s?board|paddling|"
        # RACQUET, ICE AND TARGET SPORTS, which a parks department runs constantly
        # and this had never heard of. Counted over 6,000 distinct live titles
        # from 490 civic and park feeds, 2026-09-13, every count being titles that
        # reached NEITHER the outdoors nor the fitness layer before:
        #   pickleball 25, skating 17, swim lessons 3, archery 3, disc golf 2.
        # pickleball alone is the single biggest miss in the corpus and the word
        # is unambiguous — there is no other pickleball.
        # `bird\s+(?:walk|hike)` and not bare `birding`, on the same line the
        # outdoors rule draws: a bird WALK is a walk, a "Birding Day" lecture is
        # not. `nature walk` and `guided walk` are already here for that reason.
        r"pickleball|skat(?:e|ing)|disc\s+golf|archery|bird\s+(?:walk|hike)|"
        # `meet` is deliberately NOT in that list: 3 of the 6 `swim …` hits in the
        # corpus are CLOSURE notices ("Swim Meet - Swim Center Closed", "Pool
        # Closed … for Halloween & Swim Meets"), and putting a closed pool on a
        # movement lens is worse than missing the one real meet.
        # PLURALS SPELLED OUT, because the group's trailing \b is the whole bug
        # being fixed here and it bites a new word just as fast: `swim\s+lesson`
        # cannot match "Swim Lessons", which is how every listing writes it.
        r"swim\s+(?:lessons?|class(?:es)?|practices?|teams?)|"
        r"lap\s+swim|open\s+water\s+swim|masters\s+swim|"
        r"triathlon|duathlon|obstacle\s+race|tough\s+mudder|spartan\s+race|"
        r"rowing\s+club|learn\s+to\s+row|walking\s+(?:club|group))\b", re.I)),
    # Explicitly inclusive language — the listing going out of its way to say
    # anyone can turn up. Kept to multi-word phrases because the obvious single
    # words ("group", "meetup", "community") appear in nearly every listing and
    # would tag the whole feed.
    ("community", re.compile(
        r"\b(skill[\s-]?shar\w*|all\s+levels\s+welcome|beginners?\s+welcome|"
        r"no\s+experience\s+(?:necessary|needed|required)|newcomers?\s+welcome|"
        r"open\s+to\s+(?:all|everyone)|meet\s+new\s+people|"
        r"learn\s+from\s+(?:one\s+another|each\s+other)|welcoming\s+(?:group|space))\b", re.I)),
    ("arts", re.compile(
        r"\b(art\s+walk|gallery\s+(?:opening|night|walk)|craft\s+workshop|pottery|"
        r"paint\s+(?:and|&|n)\s+sip|life\s+drawing|art\s+fair|open\s+studios?|"
        r"mural\s+(?:tour|project)|printmaking)\b", re.I)),
    ("learning", re.compile(
        r"\b(workshop|masterclass|master\s+class|seminar|lecture|panel\s+discussion|"
        r"book\s+club|author\s+talk|guest\s+speaker|intro\s+to\s+|"
        # A bare "bootcamp" is a workout: about 605 of the 685 PerfectMind rows
        # this rule put on learning were fitness boot camps (2026-10-04). The
        # 'fitness' rule above still takes the bare word; learning keeps the
        # kinds that teach something.
        r"(?:coding|code|data|tech|ux|design|developer|writing|startup|business|"
        r"job|career|grant[\s-]?writing)\s+boot\s?camp)\b", re.I)),
    ("running", re.compile(
        r"\b(5k|10k|half\s+marathon|marathon|fun\s+run|park\s?run|"
        r"turkey\s+trot|road\s+race|group\s+run)\b", re.I)),
    ("kids", _KIDS_RX),
    ("party", _PARTY_RX),
    ("theater", _THEATER_RX),
    ("volunteer", _VOLUNTEER_RX),
]
# A band called "The Rowing Club" is not a rowing club, and a play called
# "Boxing" is not a boxing class. Performance categories describe what you WATCH,
# so a movement word in a bill, a band name or a show title is a NAME rather than
# an activity — never widen those onto the movement lens. (Measured: this is the
# only false positive the fitness rule produced across the test corpus.)
_NO_FITNESS_SECONDARY_FROM = {"music", "theater"}

# A SHIFT AT AN EVENT IS NOT THE EVENT. Tualatin Hills Park & Recreation's
# volunteer calendar titles each shift by the event it staffs - "Silent Disco
# Dance at Tualatin Hills Nature Center", "Holiday Bazaar at Elsie Stuhr
# Center" - and only the description says "Volunteer at this fun,
# family-oriented event! Provide assistance setting up". The keyword rules then
# sent 7 of its 26 rows to the event's door: the disco to bar.ventures, a 07:00
# bazaar set-up shift to fleabop. So a volunteer PRIMARY whose description is
# shift work keeps no door a guest would choose it from. Not when the text is
# only the event: Seattle Parks Foundation files "Chocolate Sundays at Be'er
# Sheva Park" under volunteer too, and the concert is what reaches vivosie.
# Outdoors, kids, learning, community and fitness stay - a trail work party is
# outdoors, a teen volunteer club is for teens.
_SHIFT_RX = re.compile(
    r"\b(?:set(?:ting)?[\s-]?up\s+(?:for|the|and)|tear(?:ing)?[\s-]?down|clean(?:ing)?[\s-]?up\s+after|"
    r"provide\s+assistance|(?:help|assist)\s+(?:us\s+)?(?:run|staff)\b|"
    r"volunteer\s+(?:at|for|with|during)\s+(?:this|the|our)\s+(?:\w+[\s,-]+){0,3}(?:event|festival|fair|"
    r"race|tournament|parade|bazaar|dance|concert|party|celebration|market|show)|"
    r"volunteer\s+shifts?|ushers?|course\s+marshals?|greeters?|parking\s+attendants?)\b", re.I)
_GUEST_KEYS = {"party", "music", "market", "food", "theater", "running"}

MAX_EXTRA_CATEGORIES = 2          # DB CHECK allows 2 (migration 0108) => 3 total
_DESC_SCAN_CHARS = 600            # enough for the real blurb, short of the boilerplate
                                  # footer ("parking info", "our sponsors") that
                                  # would otherwise tag half a feed 'learning'


# "workout" is a live metaphor and the classifier kept believing it. A glass-
# fusing craft class opens "Your weekly creative workout starts here!" and was
# promoted to fitness on that phrase alone — the description-reading rule's one
# remaining false positive after URLs were excluded (measured: 6 description-only
# matches in a 300-row sample, 2 wrong, both of which this and _strip_urls now
# catch).
#
# Deliberately a short list of MODIFIERS rather than an attempt to understand the
# sentence. "creative/mental/brain workout" is a figure of speech in every
# listing that uses it; "morning workout" is not. Extending this list is cheap;
# guessing at intent is not.
_FITNESS_METAPHOR_RX = re.compile(
    r"\b(creative|mental|brain|mind|memory|vocabulary|financial|emotional|"
    r"spiritual|social|linguistic)\s+work\s?outs?\b|"
    r"\bwork\s?outs?\s+(?:for|of)\s+(?:the\s+)?(?:mind|brain|soul|imagination)\b",
    re.I)


def _classify_text(rec: Dict[str, Any], limit: int = 0) -> str:
    """The text a keyword rule is allowed to classify on.

    ONE place, because doing this in some paths and not others is how both of
    this file's classification bugs happened. URLs went first — a Meetup slug is
    the name of a group, not a claim about an event — and the metaphor guard had
    to follow the same route: it was added to the primary fitness rule only, and
    CI immediately caught "creative workout" still handing a glass-fusing class a
    fitness SECONDARY, which is what puts it on wegosie. The primary read clean
    and the event was still in the wrong lens.
    """
    desc = rec.get("description") or ""
    if limit:
        desc = desc[:limit]
    text = _strip_urls(f"{rec.get('name') or ''} {desc}")
    return _FITNESS_METAPHOR_RX.sub(" ", text)


def _fitness_strong_hit(rec: Dict[str, Any]) -> bool:
    """Does the STRONG fitness rule fire on this record's title + description?

    One place, so the primary rule and the secondary-override decision can never
    disagree about what fired. URLs are removed (a slug is not a claim about the
    event) and figurative uses of "workout" are neutralised before matching.
    """
    return bool(_FITNESS_STRONG_RX.search(_classify_text(rec)))


def _strong_fitness_override(rec: Dict[str, Any], base: str) -> bool:
    """Did the strong fitness rule beat a keyword-derived key? Used to decide
    whether that key is worth keeping as a secondary — see derive_categories."""
    return base in _WEAK_KEY_FOR_FITNESS and _fitness_strong_hit(rec)


def _base_category(rec: Dict[str, Any]) -> str:
    """The source's own classification, mapped to a Mapsee key — no promotions."""
    raw = (rec.get("category") or "").strip().lower()
    return raw if raw in MAPSEE_CATEGORY_KEYS else CATEGORY_KEYS.get(raw, DEFAULT_CATEGORY_KEY)


def _classifiable(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The record as the rules must read it: HTML entities decoded.

    _clean_text decodes them on the way INTO the table, but the rules ran on the
    raw record, so the map showed "Kitsilano Farmer's Market" while the
    classifier read "Farmer&#39;s Market", and every rule with an apostrophe or
    an ampersand in it ("Kids' Club", "Craft & Country Fair", "thrift &
    vintage") missed. 293 of 40,920 distinct listings from 831 feeds carried one
    in the title on 2026-09-28, 25 feeds, Trumba nearly all of them. A copy, so
    the row that is written keeps going through _clean_text as before."""
    name, desc = rec.get("name") or "", rec.get("description") or ""
    if "&" not in name and "&" not in desc:
        return rec
    return dict(rec, name=html.unescape(name), description=html.unescape(desc))


def derive_categories(rec: Dict[str, Any]) -> Tuple[str, Optional[List[str]]]:
    """(primary, secondaries) for one record. Secondaries is None, never [], so
    the column stays NULL — 0108's CHECK rejects an empty array."""
    rec = _classifiable(rec)
    base = _base_category(rec)
    primary = map_category(rec)

    extras: List[str] = []

    def add(key: str) -> None:
        if key and key != primary and key in MAPSEE_CATEGORY_KEYS and key not in extras:
            extras.append(key)

    # 1. A promotion fired => the source's own key survives as a secondary.
    #    'other' carries no information, so it is not worth a slot.
    #
    #    EXCEPT when the strong fitness rule overrode a keyword guess. That key
    #    was never a classification — Meetup's "dance" sweep calling a park
    #    workout a party — so carrying it forward would leave that workout on
    #    bar.ventures, which is the mislabelling the override exists to fix.
    #
    #    And never a keyword sweep's guess that map_category just REFUSED: that
    #    key was demoted because the text does not back it, and re-adding it
    #    here puts the event straight back on the door it was taken off. See
    #    _refused_sweep_guess for the Berlin row this left on fleabop.
    if base != primary and base != DEFAULT_CATEGORY_KEY \
       and not (primary == "fitness" and _strong_fitness_override(rec, base)) \
       and not _refused_sweep_guess(rec, base):
        add(base)

    # 2. Anything a source already told us explicitly (no adapter emits this
    #    today, but Eventbrite subcategories and Dice genres are the obvious
    #    next win, and this is the seam they plug into).
    for c in (rec.get("categories") or []):
        add(str(c).strip().lower())

    # 3. Keyword signals, in the fixed priority of _SECONDARY_RX.
    #    URLs stripped for the same reason map_category strips them: a link in a
    #    description — including the two this pipeline writes itself — puts
    #    arbitrary words in front of the classifier. Without this, a karate
    #    listing whose Meetup slug reads `…-yoga-karate-writing-…` picks up a
    #    fitness SECONDARY off the slug, and an event whose only mention of yoga
    #    is inside our own generated Google search URL reaches wegosie on it.
    text = _classify_text(rec, _DESC_SCAN_CHARS)
    title = _strip_urls(rec.get("name") or "")
    for key, rx in _SECONDARY_RX:
        if len(extras) >= MAX_EXTRA_CATEGORIES:
            break
        if key == "fitness" and primary in _NO_FITNESS_SECONDARY_FROM:
            continue
        # A pub quiz named after a song about a thrift shop is not a market.
        # "Monday Night Trivia: Thrift Shop Bulls**t" is the live one, and it was
        # the single false positive in 138 second-hand matches — cheap to refuse,
        # and the shape recurs every time a venue names a night after a lyric.
        if key == "market" and _NOT_A_MARKET_RX.search(text):
            continue
        if key == "party" and _from_civic_timetable(rec):
            continue
        # A volunteer shift keeps no door a guest would choose it from - see
        # _SHIFT_RX for the Silent Disco set-up crew that reached bar.ventures.
        if key in _GUEST_KEYS and primary == "volunteer" and _SHIFT_RX.search(text):
            continue
        # THE KIDS GUARD WAS ONLY EVER ASKED ABOUT THE PRIMARY, and the primary
        # is the path that reads titles. This one reads descriptions, so it is
        # the path an adults' listing actually arrives down: "Seattle Gay Online
        # Speed Dating" reached plansie's kids layer as a SECONDARY, off the age
        # brackets in its blurb, with _NOT_FOR_KIDS_RX never consulted. Bounding
        # the age range fixes that listing; this is the backstop for the next
        # one, because "Pokémon", "LEGO", "teens" and "anime club" all have a
        # grown-ups' night somewhere. Withholds only — it can never move an
        # event off a layer it already has.
        if key == "kids" and _ADULTS_ONLY_RX.search(text):
            continue
        # And the title guard itself, which only the primary ever asked: see
        # _KIDS_CO_AUDIENCE_RX for "LEGO for Adults" and "Teen & Adult Crochet".
        # A talk for the parents is withheld whatever else it names, because
        # its topic IS the children.
        if key == "kids" and (_ABOUT_KIDS_RX.search(title)
                              or (_NOT_FOR_KIDS_RX.search(title)
                                  and not _KIDS_CO_AUDIENCE_RX.search(title))):
            continue
        # The non-English kids words read the TITLE only (see _KIDS_INTL_RX:
        # in a description they are ticket prices), under the title guard.
        if key == "kids" and _KIDS_INTL_RX.search(title) and not _NOT_FOR_KIDS_RX.search(title):
            add(key)
            continue
        # So do the English ones in _KIDS_TITLE_RX, and never from a bill.
        if key == "kids" and _KIDS_TITLE_RX.search(title) \
                and primary not in _NO_KIDS_TITLE_SECONDARY_FROM:
            add(key)
            continue
        # The same predicate as the primary: a description that names a
        # volunteer handler or a food bank sponsor is not a shift.
        if key == "volunteer":
            if _volunteer_hit(rec):
                add(key)
            continue
        if rx.search(text):
            add(key)

    return primary, (extras[:MAX_EXTRA_CATEGORIES] or None)


# Formats that borrow retail words for a night out. Checked only against the
# market secondary, and only ever to WITHHOLD a layer — it can never move an
# event off the lens it is already on.
#
# "Bazaar Crafts, ages 60+" is Toronto's name for a seniors' craft session (40
# rows at five community centres, 09:30-12:30, 2026-10-03), not a bazaar: the
# crafts are being MADE. A bazaar crafts SALE, fair, market or show still is one.
_NOT_A_MARKET_RX = re.compile(
    r"\b(trivia|quiz\s+night|pub\s+quiz|bingo|karaoke|open\s+mic)\b"
    r"|\bbazaar\s+crafts?\b(?!\s+(?:sales?|fairs?|markets?|shows?))", re.I)


_URL_RX = re.compile(r"https?://\S+", re.I)


def _strip_urls(text: str) -> str:
    """Remove URLs before keyword classification.

    The strong fitness rule is the only one allowed to read the DESCRIPTION, and
    descriptions carry links — including two this pipeline writes itself, the
    "Tickets / info:" line and the "🔎 More on this show" Google search. Both put
    arbitrary words in front of the classifier as URL text.

    Found 2026-08-12 while auditing why yoga was landing in food. Live examples:
    "Breathing Ecstasy: Tantric Way of Breathing" matched on `yoga` inside OUR
    OWN generated search URL (…q=Yoga%20Society%20Of%20San%20Francisco…), and a
    karate listing matched on `yoga` inside the meetup group slug
    `san-francisco-yoga-karate-writing-meetup-group`. A slug is not a statement
    about what an event is, and a URL we generated is not evidence at all —
    letting either decide the category is the pipeline classifying its own
    output.
    """
    return _URL_RX.sub(" ", text or "")


# Adapters whose category is the SEARCH TERM that found the event rather than
# anything the source said about it. Matched against the `source` on each stored
# source ref, which the markets adapter namespaces ("market:osm-berlin-de"), so
# the comparison is on the part before the colon.
_KEYWORD_SWEEP_SOURCES = {"meetup"}


def _from_keyword_sweep(rec: Dict[str, Any]) -> bool:
    return any(str(s.get("source") or "").split(":", 1)[0].strip().lower()
               in _KEYWORD_SWEEP_SOURCES
               for s in (rec.get("sources") or []))


def _refused_sweep_guess(rec: Dict[str, Any], key: str) -> bool:
    """Is `key` a keyword sweep's GUESS that the event's own text does not back?

    ONE place, for the two callers that must agree: map_category demotes the
    guess, and derive_categories must then not carry it forward as a secondary.
    They disagreed. The market demotion shipped in map_category alone, and
    derive_categories kept "the source's own key" as a secondary like any other
    - so the Berlin Magic: the Gathering night that note records as fixed
    became ('community', ['market', ...]) and stayed on fleabop. A lens matches
    `category = any(keys) OR categories && keys`, so the demotion had only ever
    changed the pin's colour.

    `kids` is the same trap, measured 2026-09-24 on the live kids layer of 24
    metros. Meetup's sweep files `family` and `storytime` hits as kids, and its
    eventSearch is fuzzy: New York's layer was 41 primary-kids rows of which 4
    were for children - Shut Up & Write! sessions, an investor rooftop, Jewish
    speed dating, a Toastmasters club. Barcelona's was 29 with about 4 real:
    singles dinners, seven speed-dating nights, a pirate-boat party,
    family-constellation therapy. Denver, Austin, Toronto, Madrid, Amsterdam and
    London the same.
    """
    if not _from_keyword_sweep(rec):
        return False
    text = _classify_text(rec, _DESC_SCAN_CHARS)
    if key == "market":
        return not _MARKET_RX.search(text)
    if key == "kids":
        title = _strip_urls(rec.get("name") or "")
        if _NOT_FOR_KIDS_RX.search(title) or _ABOUT_KIDS_RX.search(title) \
                or _ADULTS_ONLY_RX.search(text):
            return True
        return not (_KIDS_RX.search(text) or _KIDS_BACKING_RX.search(text)
                    or _KIDS_INTL_RX.search(title) or _KIDS_TITLE_RX.search(title))
    return False


def map_category(rec: Dict[str, Any]) -> str:
    """Map the captured category to a Mapsee frontend category KEY (site/js/app.js).
    Accepts a Ticketmaster segment / schema.org @type, OR an already-valid Mapsee
    key (e.g. from an open-data source config). Clearly-volunteer events sitting in a
    generic bucket are promoted to the 'volunteer' layer by keyword.

    Still the PRIMARY-only answer: the pin colour, the emoji and _compute_end all
    need exactly one key. derive_categories() wraps this for the full set."""
    rec = _classifiable(rec)
    raw = (rec.get("category") or "").strip().lower()
    key = raw if raw in MAPSEE_CATEGORY_KEYS else CATEGORY_KEYS.get(raw, DEFAULT_CATEGORY_KEY)
    # A FUZZY SEARCH'S KEYWORD IS NOT A CLASSIFICATION. Meetup's eventSearch is
    # not a phrase match: the adapter sweeps "farmers market" and "night market"
    # and files whatever comes back under `market`, because the keyword that
    # found an event is normally a decent guess at its layer. For `market` it is
    # not, because "market" is a business word before it is a shopping one.
    # Measured 2026-08-16, every `market` event in Berlin was a Meetup row and
    # none was a market: stand-up comedy (x3), a Magic: the Gathering night, a
    # run club, an e-commerce breakfast, a homebuyers' meetup and a meditation.
    # fleabop's whole supply in that city was wrong.
    #
    # Demoted only when the text does not back the claim, and only for a source
    # that GUESSED. Provenance is what makes that safe: market_sources.json and
    # tribe_sources.json state `market` deliberately, and "Randolph Street
    # Market" would not survive a title test — so a text-only rule would throw
    # away the real supply to fix the fake.
    #
    # 'community' rather than 'other', because these are Meetup socials and the
    # promotion rules below can still move them onto a better layer from there.
    #
    # `kids` too, and for the same reason - see _refused_sweep_guess.
    if key in ("market", "kids") and _refused_sweep_guess(rec, key):
        key = "community"
    if key in _PROMOTABLE_TO_VOLUNTEER:
        # URL-stripped like every other description read: a Meetup group slug
        # such as …-volunteer-cleanup-group- is the name of a GROUP, not a
        # statement that this event is a volunteering shift. And the description
        # has to ASK for volunteers, not mention one: see _VOLUNTEER_CALL_RX.
        if _volunteer_hit(rec):
            return "volunteer"
    if key in _PROMOTABLE_TO_THEATER and _THEATER_RX.search(rec.get("name") or ""):
        return "theater"           # comedy/standup/film at a music venue -> stage layer
    # The kids RULE reads the title only, like every other primary rule. The
    # GUARD reads both, because a guard can only ever withhold: "Pokémon TCG
    # League" and "LEGO Night" are kids words in a title with "Adults only —
    # 21+" and "An 18+ evening" in the blurb, and the title-only guard could not
    # see either. _ADULTS_ONLY_RX rather than _NOT_FOR_KIDS_RX for the
    # description half — see its note for the price table that proves the
    # difference.
    if key in _PROMOTABLE_TO_KIDS \
            and (_KIDS_RX.search(rec.get("name") or "") or _KIDS_INTL_RX.search(rec.get("name") or "")
                 or _KIDS_TITLE_RX.search(rec.get("name") or "")) \
            and not _NOT_FOR_KIDS_RX.search(rec.get("name") or "") \
            and not _ABOUT_KIDS_RX.search(rec.get("name") or "") \
            and not _ADULTS_ONLY_RX.search(_classify_text(rec, _DESC_SCAN_CHARS)):
        return "kids"              # storytime/family day hiding in community/learning
    if key in _PROMOTABLE_TO_PARTY and _PARTY_RX.search(rec.get("name") or "") \
            and not _from_civic_timetable(rec):
        return "party"             # crawls/happy hours/karaoke -> the nightlife layer
    # Last in the chain deliberately: a volunteer trail-work party, a kids' karate
    # storytime or a boxing-themed club night should keep the more specific layer
    # the rules above already found for it.
    if key in _PROMOTABLE_TO_FITNESS and _FITNESS_RX.search(rec.get("name") or ""):
        return "fitness"           # yoga/HIIT/climbing hiding in community/learning
    # …and the strong rule, which may also read the description and may override a
    # keyword-derived key (see _WEAK_KEY_FOR_FITNESS). URLs are stripped first —
    # see _strip_urls for why that is not cosmetic.
    if key in _WEAK_KEY_FOR_FITNESS and _fitness_strong_hit(rec):
        return "fitness"
    return key


def primary_url(rec: Dict[str, Any]) -> Optional[str]:
    for s in rec.get("sources", []):
        if s.get("url"):
            return s["url"]
    return None


def venue_show_search_url(rec: Dict[str, Any]) -> Optional[str]:
    """A web-search deep link that lands on the VENUE'S OWN page for this show.
    Ticketmaster/SeatGeek listings routinely omit detail the venue's site has
    (support acts, set times, age limits, doors, parking). Their data carries no
    field for the venue's own URL, so we build a Google search scoped to
    venue + headliner + date — the venue's own event page is almost always the
    top hit. Venue-fed events skip this: their Tickets / info link already IS the
    venue page (see to_row). Returns None without a venue name to search on."""
    venue = (rec.get("venue_name") or "").strip()
    if not venue:
        return None
    terms = [venue]
    artist = _pick_artist(rec)
    if artist and artist.strip().lower() != venue.lower():
        terms.append(artist.strip())
    date = str(rec.get("start_local") or rec.get("start_utc") or "")[:10].strip()
    if date:
        terms.append(date)
    return f"https://www.google.com/search?q={urllib.parse.quote(' '.join(terms))}"


_WS_RUN = re.compile(r"[ \t ]{2,}")


_HTML_TAG = re.compile(
    r"</?(?:p|span|div|br|hr|a|b|i|u|em|strong|ul|ol|li|dl|dt|dd|h[1-6]|table|thead|tbody|tr|td|th|"
    r"img|font|blockquote|pre|code|small|sub|sup|section|article|header|footer|nav|figure|figcaption|"
    r"iframe|style|script)\b[^>]*>", re.I)


def _clean_text(s: Optional[str]) -> Optional[str]:
    """Decode HTML entities the feeds send pre-encoded (&#39; -> ', &#160; -> space,
    &amp; -> &) and normalize whitespace, so the DB stores clean display text for
    EVERY consumer (map pins, push notifications, flyers, OG images, search) instead
    of raw entity codes. Canonical fix: clean once here at the write boundary."""
    if not s:
        return s
    s = html.unescape(str(s))
    s = _HTML_TAG.sub(" ", s)          # strip HTML tags (<p>, <span>, <br>, ...)
    s = s.replace(" ", " ")            # decoded nbsp -> normal space
    s = _WS_RUN.sub(" ", s)                 # collapse space/tab runs (keeps newlines)
    return s.strip()


# --- description size control -------------------------------------------------
# `description` is the single largest thing in the events table: measured at
# ~999 chars average across aggregated rows, ~172 MB of a 380 MB table, with a
# heavy tail (p50 681, p90 1996, p99 5073, max 17090). Migration 0055 sized it at
# "300-500 bytes" when it dropped the column from events_near; it has since
# doubled. At ~1KB most values also sit UNDER Postgres's ~2KB TOAST threshold,
# so they are stored inline and never compressed.
#
# Cap the SOURCE PROSE ONLY, before it becomes parts[0]. The lines appended after
# it - 📍 address, "Tickets / info:", 🔎 More on this show, 🎵 Listen - are the
# deep link and the facts. Truncating the ASSEMBLED string would cut off exactly
# what the aggregator exists to preserve ("we take the facts, keep a deep link").
#
# 800 leaves the median description untouched and trims only the long tail. Cut
# on a sentence boundary where there is one nearby, else a word boundary, so the
# text never ends mid-word.
DESCRIPTION_MAX = 800


# The longest final paragraph this will protect from the cut. An attribution is
# one short line; a genuine closing paragraph of prose is usually longer than
# this, and keeping IT would move the ellipsis somewhere it reads oddly.
TAIL_KEEP_MAX = 200


def _cap_prose(text: Optional[str]) -> Optional[str]:
    """Trim over-long SOURCE prose to DESCRIPTION_MAX, on a natural boundary.

    THE LAST LINE IS LOAD-BEARING AND THIS USED TO CUT IT OFF. Four adapters end
    their description with a LICENCE ATTRIBUTION — OpenActive's "via OpenActive,
    licensed CC-BY 4.0.", and the "OpenStreetMap contributors (ODbL)." line the
    three OSM adapters carry — and every one of them is the term the data is held
    on rather than a footer. Cutting from the END therefore deleted the licence
    from exactly the richest rows: mapsee_ingest_openactive lets a body run to
    900 characters on its own, so anything near that overflowed 800 and lost the
    line, silently, on a row that otherwise looked perfect.

    It cost more than the licence. mapsee_retire_openactive_slots,
    mapsee_retire_perday_osm and mapsee_retire_thin_artwork all identify their
    own rows BY that mark — "a row without it is not ours to judge" — so a
    truncated row could never be retired, audited or corrected by any of them.

    So a SHORT final paragraph survives and the head is trimmed to make room.
    """
    if not text or len(text) <= DESCRIPTION_MAX:
        return text

    head, sep, tail = text.rpartition("\n\n")
    if sep and 0 < len(tail) <= TAIL_KEEP_MAX:
        room = DESCRIPTION_MAX - len(tail) - len(sep) - 1      # -1 for the ellipsis
        if room > 0:
            return _cut(head, room) + "…" + sep + tail
    return _cut(text, DESCRIPTION_MAX - 1) + "…"


def _cut(text: str, limit: int) -> str:
    """`text` shortened to at most `limit`, on the nearest natural boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut < limit * 0.6:                    # no sentence break near the end
        cut = head.rfind(" ")
    if cut <= 0:                             # one enormous unbroken token
        cut = limit
    return head[:cut].rstrip(" ,;:.!?-—")


def _street_address(rec: Dict[str, Any]) -> Optional[str]:
    """The venue's street address (e.g. '200 University St, Seattle, WA 98101'), or
    None when the source has no street line. Shown as a 📍 line in the description;
    the map link still routes to the exact geocoded coordinates regardless."""
    line1 = (rec.get("address") or "").strip().rstrip(".")
    if not line1:
        return None
    state_zip = " ".join(p for p in (rec.get("region"), rec.get("postal_code")) if p)  # "WA 98101"
    return ", ".join(p for p in (line1, rec.get("city"), state_zip) if p)


# A venue name that means "there is no venue".
#
# Matches the WHOLE string, not a prefix. That is the important part, and it was
# not obvious: an earlier prefix-anchored version correctly ignored "The Online
# Lounge" and "Remoteness Gallery" (no word boundary) but dropped "Virtual
# Reality Arcade, 5th Ave" — a real, physical, addressable venue.
#
# The asymmetry decides the rule. A false positive silently deletes a real
# event and nobody finds out; a false negative leaves one online event in the
# corpus, which is the status quo and visible. So this only fires when the venue
# field is a bare placeholder and nothing else — "Online event" yes, anything
# with a street or a proper noun attached to it, no.
_VIRTUAL_WORDS = r"(online|virtual|remote|livestream|live\s*stream|webinar|zoom|" \
                 r"google\s*meet|microsoft\s*teams|ms\s*teams|twitch|youtube\s*live|" \
                 r"web\s*conference|video\s*call|tbd|tba|to\s*be\s*announced|n/?a|none)"
_VIRTUAL_SUFFIX = r"(event|events|meeting|session|class|classes|only|venue|location|" \
                  r"gathering|workshop|webinar|stream|call)"
# Any run of placeholder words and placeholder nouns, and nothing else. The
# repetition is not decoration: a dry run over 1,000 live venue strings caught
# "Online event" and "TBA" but left "Virtual/Online", which is the same
# placeholder written with a slash. Two of these words in a row is still two of
# these words; one real proper noun anywhere in the string and it is a venue.
_VIRTUAL_VENUE_RX = re.compile(
    rf"^{_VIRTUAL_WORDS}([\s\-:/&,]+({_VIRTUAL_WORDS}|{_VIRTUAL_SUFFIX}))*$", re.I)


def is_virtual(rec: Dict[str, Any]) -> bool:
    """Does this event happen at no physical place?

    WHY THIS DROPS THE EVENT
    ------------------------
    mapsee.me is a map. An event with no location is not a thing this product
    can show, and pretending otherwise cost real damage before this existed:
    the venue string "Online event" was handed to the geocoder, which matched
    it to an atoll in the Pacific, and roughly 7% of the sitemap ended up as
    event pages pinned at (-8.521, 179.196) — open water near Tuvalu, ~35km
    from no metro at all. Those pages could never appear on a /c/ page, and
    their Event JSON-LD asserted OfflineEventAttendanceMode plus a fabricated
    GeoCoordinates, which is exactly the kind of invented markup the rest of
    this pipeline is careful never to emit.

    Meetup's adapter already reached this conclusion independently and returns
    None for venue-less events (mapsee_ingest_meetup.py). This applies the same
    rule to every source, in the one place they all funnel through.

    Checked on the SOURCE record rather than the built row so the event is
    dropped before it is geocoded — the false coordinate is never created, not
    created and then discarded.
    """
    for field in ("venue_name", "venue", "place_name"):
        v = rec.get(field)
        if not isinstance(v, str):
            continue
        # Collapse whitespace and drop surrounding punctuation before matching,
        # so "  Online Event. " and "(online)" are the same placeholder as
        # "Online event" rather than three strings that each need their own rule.
        norm = re.sub(r"\s+", " ", v).strip().strip(" .,;:!-()[]\"'")
        if norm and _VIRTUAL_VENUE_RX.match(norm):
            return True
    return False


def _pick_artist(rec: Dict[str, Any]) -> Optional[str]:
    """Best guess at the performer to build listen links from: the headliner
    (lineup[0]) if present, else the event title. Not the promoter — that's the
    booking company, not the act."""
    lu = rec.get("lineup") or []
    if isinstance(lu, list) and lu and str(lu[0]).strip():
        return str(lu[0]).strip()
    name = (rec.get("name") or "").strip()
    return name or None


def _host_name(rec: Dict[str, Any]) -> str:
    """The actual promoter when Ticketmaster gives a real one; otherwise 'mapsee.me'.
    Skips generic filler like 'PROMOTED BY VENUE'."""
    p = (rec.get("promoter") or "").strip()
    if not p or "promoted by venue" in p.lower():
        return "mapsee.me"
    return p


_TF = None                                             # lazily-built TimezoneFinder


def _us_tz_by_lon(lat: float, lon: float) -> Optional[str]:
    """Coarse US timezone from coordinates — fallback when timezonefinder isn't
    installed. Fixes the big offset errors (e.g. Pacific parks); only ~1h off in
    edge cases like Arizona (no DST)."""
    if lat >= 51 and lon <= -129:
        return "America/Anchorage"
    if lon <= -150:
        return "Pacific/Honolulu"
    if lon <= -114:
        return "America/Los_Angeles"
    if lon <= -100:
        return "America/Denver"
    if lon <= -85:
        return "America/Chicago"
    return "America/New_York"


def _tz_for(lat, lon):
    """IANA timezone for coordinates — precise via timezonefinder when available,
    else a coarse US longitude fallback."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    return _tz_at(lat, lon)


# Cached per point: a community centre's hundreds of sessions share one, and the
# daily time-change check (time_changed) asks for every stored naive-time row.
# Measured on 20,000 naive rows: 3.85 s uncached (~19 s per 100k) with
# timezonefinder; see the note in docs/agents/running.md.
@functools.lru_cache(maxsize=65536)
def _tz_at(lat: float, lon: float):
    name = None
    try:
        from timezonefinder import TimezoneFinder
        global _TF
        if _TF is None:
            _TF = TimezoneFinder()
        name = _TF.timezone_at(lat=lat, lng=lon)
    except Exception:
        name = None
    if not name:
        name = _us_tz_by_lon(lat, lon)
    try:
        return ZoneInfo(name) if name else None
    except Exception:
        return None


_OFFSET_RE = re.compile(r"[+-]\d{2}:\d{2}$")


def _to_utc_if_naive(s: Optional[str], lat, lon) -> Optional[str]:
    """A NAIVE local datetime (no 'Z', no offset — e.g. NPS "2026-07-20T10:00:00")
    is converted to real UTC using the event's coordinates, so it isn't stored
    shifted by the local UTC offset. Already-UTC/offset/date-only values pass
    through untouched."""
    if not s:
        return s
    s = str(s).strip()
    if len(s) == 10:                                   # date-only (all-day)
        return s
    if s.endswith("Z") or _OFFSET_RE.search(s):        # already timezone-aware
        return s
    tz = _tz_for(lat, lon)
    if tz is None:
        return s
    try:
        dt = datetime.fromisoformat(s).replace(tzinfo=tz)
    except ValueError:
        return s
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- AN ALL-DAY EVENT HAPPENS ON A DAY, AND A DAY HAS A PLACE ---------------
#
# Half a dozen adapters deliberately emit a bare `YYYY-MM-DD` when the source
# publishes no clock — Ticketmaster's `timeTBA` tours, parkrun, BikeReg,
# RunSignup, Seattle Center's "All Day", every civic feed whose exact-midnight
# stamp is a date in a timestamp column. Their comments all say the same true
# thing: the row claims a DAY, not a minute.
#
# Nothing downstream honoured that. A bare date handed to a `timestamptz` column
# is read at the SERVER's clock, which is UTC, and `_compute_end` pinned the
# other end at a naive `T23:59:59` that landed the same way. So the day the
# adapter meant arrived in the browser shifted by the venue's offset, and every
# all-day event in the Pacific rendered as
#
#     Today, 5:00 PM  →  Tomorrow, 4:59 PM
#
# — a nineteen-hour window on the wrong two days, and hours nobody published.
# East of Greenwich it fails the other way (02:00 → 01:59 the day after). It is
# the same class of mistake `_to_utc_if_naive` already fixes for TIMED rows;
# date-only was simply the branch that returned early.
#
# The day the source names is the day where the EVENT is, so the bracket comes
# from the event's own coordinates. Both edges are converted, because a day is
# 23 or 25 hours long twice a year and only the tz database knows which.
def _day_bounds(day: str, lat, lon) -> tuple[str, str]:
    """The two UTC instants that bracket ONE LOCAL DAY at these coordinates."""
    tz = _tz_for(lat, lon) or timezone.utc
    midnight = datetime.fromisoformat(day).replace(tzinfo=tz)
    end = (midnight + timedelta(days=1)) - timedelta(seconds=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (midnight.astimezone(timezone.utc).strftime(fmt),
            end.astimezone(timezone.utc).strftime(fmt))


def _anchor_all_day(starts_at, end_src, lat, lon):
    """(starts_at, ends_at) with any date-only edge widened to the local day it
    names. A timed row passes through untouched, and so does a row we cannot
    place — coordinates are required for every event this sync writes, but a
    missing one must degrade to the old behaviour rather than raise."""
    try:
        if end_src and len(str(end_src).strip()) == 10:            # multi-day all-day
            end_src = _day_bounds(str(end_src).strip(), lat, lon)[1]
        if starts_at and len(str(starts_at).strip()) == 10:
            lo, hi = _day_bounds(str(starts_at).strip(), lat, lon)
            starts_at, end_src = lo, (end_src or hi)
    except (TypeError, ValueError):
        pass
    return starts_at, end_src


# Typical event length by category (hours) — used to synthesize an end time when
# the source doesn't provide one, so every event gets a sensible ends_at instead
# of the app falling back to a flat +6h.
_DURATION_H = {
    "music": 3.0, "party": 4.0, "sports": 3.0, "arts": 3.0, "food": 3.0,
    "community": 2.0, "market": 5.0, "outdoors": 3.0, "learning": 2.0,
    "kids": 2.0, "volunteer": 3.0, "running": 3.0, "other": 3.0,
}


def _compute_end(starts_at: Optional[str], real_end: Optional[str], category: str) -> Optional[str]:
    """The event's end time: the source's REAL end when we have one, otherwise
    start + a category-typical duration (an educated guess). Robust to date-only
    and non-ISO times, which fall back to an all-day (end-of-day) end."""
    if real_end:
        return real_end
    if not starts_at:
        return None
    s = str(starts_at).strip()
    if len(s) == 10:                                   # date-only -> all-day
        return s + "T23:59:59"
    try:
        dtv = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return (s[:10] + "T23:59:59") if len(s) >= 10 else None   # unparseable time -> all-day
    end = dtv + timedelta(hours=_DURATION_H.get(category, 3.0))
    if s.endswith("Z"):
        return end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return end.isoformat()


def to_row(rec: Dict[str, Any], host_id: str) -> Dict[str, Any]:
    """Map one normalized event -> a public.events row for PostgREST upsert."""
    if "agenda" in rec and rec.get("agenda") is not None:
        rec = dict(rec)
        rec["agenda"] = normalize_agenda(rec["agenda"], rec.get("agenda_tz"),
                                          rec.get("start_utc"), rec.get("end_utc"),
                                          rec.get("start_local"), rec.get("end_local"))
    # Pin color by provenance, so imported listings read differently on the
    # map: TEAL = city open data, VIOLET = big-venue feeds (Ticketmaster).
    # Community-created events keep the app default blue (#2563eb).
    _src = (rec.get("source")
            or ((rec.get("sources") or [{}])[0].get("source")) or "")
    # A city's own recreation timetable is city data too: Toronto's open data,
    # Helsinki/Espoo Linked Events and the PerfectMind booking widgets a city
    # links from its own website (community-centre drop-ins, 2026-10-03).
    color = "#0891b2" if str(_src).startswith(("opendata:", "ics:", "program:") + CIVIC_TIMETABLE_SOURCES) else "#7c3aed"
    category_key, extra_categories = derive_categories(rec)
    # Volunteering is first-class: ROSE pins across every source, so "ways to
    # help nearby" reads as its own layer on the map (not generic civic teal).
    if category_key == "volunteer":
        color = "#e11d48"
    parts = [_cap_prose(_clean_text(rec["description"]))] if rec.get("description") else []
    address = _street_address(rec)
    if address:
        parts.append("📍 " + address)               # human-readable address (map routes to exact coords)
    url = primary_url(rec)
    if url:  # give attendees (and the venue) a click-through; supports promotion
        parts.append(f"Tickets / info: {url}")
    # 🔎 More on this show — the venue's OWN page usually carries detail the
    # ticket aggregators miss (support acts, set times, age limits, parking).
    # Venue-fed / open-data events already point at the source's own page via
    # Tickets / info, so only the big-venue aggregators (Ticketmaster, SeatGeek)
    # get this web-search fallback.
    #
    # `osm-` IS NOT A SHOW. The three OpenStreetMap adapters import PLACES —
    # a takeaway, a charity shop, a drinking fountain — and none of them has
    # support acts or a set time. The line was reaching all of them because
    # this is a DENY-list while the sentence above describes an allow-list, so
    # every adapter added since inherited it by default. On a restaurant it was
    # merely odd; on a civic amenity it is load-bearing, because ../mapsee 0195
    # decides whether a pin opens by asking whether anything survives stripping
    # this row's boilerplate, and a Google search link is not a fact about a
    # drinking fountain. Found by generating the real stored description for a
    # bare fountain and reading it.
    if not str(_src).startswith(("opendata:", "venue:", "ics:", "program:", "osm-") + CIVIC_TIMETABLE_SOURCES):
        show = venue_show_search_url(rec)
        if show:
            parts.append(f"🔎 More on this show: {show}")
    # 🎵 Listen — let people hear the performer before a show. Exact links win
    # (Ticketmaster's attraction externalLinks, or a resolved Spotify artist page
    # from the enrichment pass); otherwise a search deep-link that opens the
    # artist in Spotify / YouTube Music. Only for music (or when a source already
    # handed us a music link), so non-music listings stay clean.
    if category_key == "music" or rec.get("spotify_url") or rec.get("youtube_url"):
        artist = _pick_artist(rec)
        sp = rec.get("spotify_url") or spotify_search_url(artist)
        yt = rec.get("youtube_url") or youtube_search_url(artist)
        bc = bandcamp_search_url(artist)   # search-only (no exact-page API); land on the band
        listen = [f"Spotify: {sp}" if sp else None, f"YouTube: {yt}" if yt else None,
                  f"Bandcamp: {bc}" if bc else None]
        listen = [x for x in listen if x]
        if listen:
            parts.append("🎵 Listen — " + "  ·  ".join(listen))
    description = "\n\n".join(parts) or None
    # Volunteering is free community time — strip any ticket price the source
    # baked in (e.g. a Ticketmaster event promoted to the volunteer layer keeps
    # its "Approx. price: $39 · Rock" line). Drop the price + its " · " separator.
    if category_key == "volunteer" and description:
        description = re.sub(r"Approx\. price:\s*[^\n·]*(?:\s*·\s*)?", "", description)
        description = re.sub(r"\n{3,}", "\n\n", description).strip() or None
    lat, lon = rec.get("latitude"), rec.get("longitude")
    # Normalize naive local times (e.g. NPS "2026-07-20T10:00:00") to real UTC via
    # the event's coordinates, so no source is stored offset by its UTC offset.
    starts_at = _to_utc_if_naive(rec.get("start_utc") or rec.get("start_local"), lat, lon)
    end_src = _to_utc_if_naive(rec.get("end_utc") or rec.get("end_local"), lat, lon)
    # A bare date means "all day HERE", not "midnight in London" — see _day_bounds.
    starts_at, end_src = _anchor_all_day(starts_at, end_src, lat, lon)
    row = {
        "title": _clean_text(rec.get("name")),
        "description": description,
        "lat": lat,
        "lon": lon,                                  # trigger fills events.location
        "place_name": _clean_text(rec.get("venue_name")),   # venue name -> map-pin label + "where"
        # The town this event is actually in. Every adapter already carries it —
        # _street_address() has been folding it into a 📍 line of PROSE since the
        # beginning, which meant the one machine-readable fact about an event's
        # location was only ever available as English text inside a paragraph.
        #
        # It exists as a column so the Worker can put a real PostalAddress in the
        # Event JSON-LD on /e/<id>. Search Console reports "Missing field address
        # (in location)" on those pages, and place_name cannot stand in: it is a
        # venue name far more often than a street ("THE CLOUD ONE LOUNGE"), so
        # using it would be a guess published as structured data.
        "locality": _clean_text(rec.get("city")),
        "region_name": _clean_text(rec.get("region")),      # state/province, when the source gives one
        "postal_code": _clean_text(rec.get("postal_code")),
        "street_address": _clean_text(rec.get("address")),  # line 1 only; may be None
        "host_name": _clean_text(_host_name(rec)),   # actual promoter when available, else "mapsee.me"
        "starts_at": starts_at,
        "ends_at": _compute_end(starts_at, end_src, category_key),
        "created_by": host_id,                       # the aggregator's profiles.id
        "is_private": False,                         # public -> discoverable in events_near
        # NOTE: no "visibility" — that column was dropped in migration 0003; is_private is the flag now.
        "category": category_key,                    # PRIMARY key -> pin colour + emoji (site/js/app.js)
        "categories": extra_categories,              # up to 2 secondaries (migration 0108), or None
        "color_hex": color,                          # provenance pin color (see above)
        "poster_path": rec.get("poster_image_url") or None,   # external URL → banner + og:image (client/worker pass http(s) through)
        # The ADAPTER's glyph when it has one, else None and the app renders the
        # CATEGORY's emoji — which is what every adapter but the civic-amenity
        # one wants, since a music event should look like a music event. Only
        # set where the row's KIND is more specific than its category: three
        # OSM selectors live under `outdoors` and all three drew 🚰.
        # Bounded at 8 to match the client's own icon input (geIcon maxlength).
        "icon": (rec.get("icon") or "").strip()[:8] or None,
        "external_source": "mapsee",                 # provenance (migration 0039)
        "external_id": rec["fingerprint"],           # cross-source dedup key -> idempotent
        # A standing weekly arrangement rather than an occasion (migration 0156).
        # The TIMEZONE is resolved here and stored with the pattern, because this
        # is the only place that knows it: the adapter has coordinates, and
        # turning coordinates into an IANA zone is _tz_for's job. 0149's note
        # applies — longitude cannot know a DST rule, so the zone is stored, not
        # re-derived by whoever reads it later.
        "recurring_hours": _recurring_hours(rec, lat, lon),
        # NOT A LISTING (migration 0194). The map DRAWS these; the Nearby list
        # and the sitemaps skip them. It does NOT mean "nothing worth reading"
        # — that was the original reading and it filled the Nearby list with
        # playgrounds, 745 always-open rows in one Seattle box. The question is
        # whether the thing can be SHUT; what it CARRIES decides whether its pin
        # hovers and opens, which ../mapsee's amenityHasContent works out from
        # the description. See mapsee_ingest.NormalizedEvent.pin_only.
        # Written for every row so a re-sync can move one either way once a
        # mapper adds real opening hours: an upsert cannot delete, and a flag
        # only ever written when true could only ever be turned on.
        "pin_only": bool(rec.get("pin_only")),
    }
    # Optional agenda columns are presence-sensitive. Omitting them lets a
    # source that does not publish a programme preserve one already in DB;
    # [] is retained and intentionally clears it.
    if "agenda" in rec:
        # NULL is the database representation of an explicit empty refresh;
        # omitting the key means preserve the existing source-owned agenda.
        row["agenda"] = rec["agenda"] if rec["agenda"] else None
    if "agenda_tz" in rec:
        # A timezone supplied without an agenda is still source data and must
        # be written; an explicit empty agenda clears both columns.
        row["agenda_tz"] = None if ("agenda" in rec and not rec.get("agenda")) else rec["agenda_tz"]
    if isinstance(rec.get("source_details"), dict):
        # Presence-sensitive, like agendas: a thin source must not erase a
        # richer one. A successful page read may deliberately clear old facts.
        row["source_details"] = rec["source_details"] or None
    return row


def needs_detail_sync(rec, state, moved=(), held=()):
    """A successful exact-detail read refreshes unclaimed rows, even only-new.

    Ticket availability is time-sensitive; waiting until Wednesday would show
    Saturday's sold-out class as available. Failed/thin reads preserve old data.
    """
    key = rec["fingerprint"]
    return key not in held and not state.get(key, False) and (
        key not in state or key in moved or isinstance(rec.get("source_details"), dict))


def should_compare_unchanged(only_new, rows, state):
    # Ordinary only-new batches contain no stored rows. Exact-detail refreshes
    # are the exception; compare those so a daily read costs no needless UPDATE.
    return not only_new or any(r["external_id"] in (state or {}) for r in rows)


def _recurring_hours(rec, lat, lon):
    """{"tz": …, "days": {"0": ["11:00","22:00"], …}} or None."""
    days = rec.get("recurring_days")
    if not days:
        return None
    tz = _tz_for(lat, lon)
    return {"tz": getattr(tz, "key", None) or "UTC",
            "days": {str(k): list(v) for k, v in days.items()}}


def _addr_parts(rec: Dict[str, Any]):
    """(street, city, state) for the US Census batch geocoder, or None.
    Deliberately no ZIP — Ticketmaster's postal is sometimes wrong, and
    street+city+state geocodes reliably on its own."""
    line1 = (rec.get("address") or "").strip().rstrip(".")
    if not line1:
        return None                                   # no street -> would only hit a city centroid
    return (line1, (rec.get("city") or "").strip(), (rec.get("region") or "").strip())


def batch_geocode(session, addr_tuples):
    """Geocode many US addresses at once with the free, key-less US Census BATCH
    geocoder (up to 10k per request) — Ticketmaster's own venue lat/long is often
    imprecise (it placed The Showbox ~0.5 mi off). Input: list of (street, city,
    state). Returns {(street, city, state): (lat, lon)} for confident matches."""
    import io
    import csv
    result: Dict[Any, Any] = {}
    keys = list(addr_tuples)
    for start in range(0, len(keys), 9000):
        chunk = keys[start:start + 9000]
        buf = io.StringIO()
        writer = csv.writer(buf)
        idmap = {}
        for j, (street, city, state) in enumerate(chunk):
            rid = str(start + j)
            idmap[rid] = (street, city, state)
            writer.writerow([rid, street, city, state, ""])   # id, street, city, state, zip(blank)
        try:
            r = session.post(
                "https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
                files={"addressFile": ("addrs.csv", buf.getvalue())},
                data={"benchmark": "Public_AR_Current"},
                timeout=180,
            )
            if r.status_code != 200:
                continue
            for row in csv.reader(io.StringIO(r.text)):
                # id, input, Match/No_Match/Tie, type, matched_addr, "lon,lat", tiger, side
                if len(row) >= 6 and row[2] == "Match" and "," in row[5]:
                    lon, lat = row[5].split(",")[:2]
                    if row[0] in idmap:
                        result[idmap[row[0]]] = (float(lat), float(lon))
        except Exception:
            continue
    return result


def _enrich_music_links(recs: List[Dict[str, Any]], session) -> None:
    """Upgrade music events to EXACT Spotify artist pages when a Spotify app
    credential is set (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET). No-op without
    creds — to_row still emits Spotify/YouTube search deep-links. Resolutions are
    cached by artist name (spotify_cache.json) so a name is queried at most once."""
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and secret):
        return
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify_cache.json")
    try:
        cache = json.loads(open(cache_path, encoding="utf-8").read()) if os.path.exists(cache_path) else {}
    except Exception:
        cache = {}
    resolver = SpotifyResolver(cid, secret, session, cache)
    resolved = 0
    for rec in recs:
        if map_category(rec) != "music" or rec.get("spotify_url"):
            continue                                   # non-music, or TM already gave an exact link
        artist = _pick_artist(rec)
        if not artist:
            continue
        u = resolver.artist_url(artist)
        if u:
            rec["spotify_url"] = u
            resolved += 1
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
    except Exception:
        pass
    print(f"Spotify: resolved {resolved} exact artist pages ({len(cache)} names cached).")


def build_rows(store_path: str, host_id: str, geo_session=None, keep=None) -> List[Dict[str, Any]]:
    """Store -> event rows. `keep`, when given, filters the placeable records
    BEFORE the Spotify lookups, the Census batch and to_row, which is where
    --only-new's existence check belongs: see main()."""
    import time
    data = json.loads(open(store_path, encoding="utf-8").read())
    recs = []
    virtual = 0
    for rec in data.get("events", []):
        if not rec.get("fingerprint") or not (rec.get("start_utc") or rec.get("start_local")):
            continue
        # Before the coordinate check below, and before any geocoding: an online
        # event has no place to be, and asking the geocoder for one invents a
        # location. See is_virtual().
        if is_virtual(rec):
            virtual += 1
            continue
        has_coords = rec.get("latitude") is not None and rec.get("longitude") is not None
        if not has_coords:
            # No SOURCE coordinates. Some adapters (the JSON-LD venue crawler for
            # Sea Monster / Ticket Tomato, …) deliberately arrive coordless and
            # defer geocoding to here. Keep the event only if we can place it: a
            # street address to geocode below AND a network session to do it.
            # Coordless AND address-less is unplaceable — drop it.
            if geo_session is None or _addr_parts(rec) is None:
                continue
        recs.append(rec)

    if virtual:
        print(f"Skipped {virtual} online/virtual events (no physical venue - see is_virtual).")

    # DROP WHAT WILL NOT BE WRITTEN BEFORE PAYING TO PREPARE IT. --only-new used
    # to filter in main(), after this function had resolved Spotify pages,
    # batch-geocoded and built a row for every record in the store: on
    # 2026-09-12 Meetup's final sync geocoded 22,276 of 53,871 rows (12,832
    # unique addresses matched) to write 2,480, and that day's 13 syncs checked
    # 280,223 ids to upsert 15,549 rows.
    if keep is not None:
        recs = keep(recs)

    if geo_session is not None:                       # production run (has network)
        _enrich_music_links(recs, geo_session)        # exact Spotify pages when a key is set
        # Batch-geocode the unique venue addresses ONCE and write the result back
        # into the rec BEFORE to_row. This both (a) supplies coordinates for events
        # that arrived without any (deferred-geocode adapters) and (b) overrides
        # imprecise source coords (Ticketmaster is often ~0.5mi off). Doing it
        # pre-to_row means the map pin AND the naive-local-time→UTC conversion
        # (which needs coords to know the timezone) both use the best coordinates.
        # …EXCEPT where the source's coordinates ARE the fact. OpenStreetMap
        # hands over a surveyed point and derives its address text from it;
        # re-geocoding that text throws away the better number for a worse one.
        # Measured live: a Renton restaurant labelled with its hub's city
        # geocoded "Rainier Avenue South, Seattle" onto SEATTLE's street of the
        # same name, eleven miles off, and a Sequim diner landed sixty miles from
        # itself. coords_exact opts an adapter out; everything else is unchanged.
        # `ex` is carried through the generator rather than read from `r` in the
        # condition: a generator expression's loop variable does NOT leak into
        # the enclosing comprehension, so `not r.get(...)` there is a NameError.
        # It cost a whole area's pull — Portland fetched every candidate, then
        # died at the sync.
        parts_of = {i: p for i, p, ex in
                    ((i, _addr_parts(r), bool(r.get("coords_exact")))
                     for i, r in enumerate(recs))
                    if p and not ex}
        exact = sum(1 for r in recs if r.get("coords_exact"))
        if exact:
            print(f"Kept {exact} source-exact coordinates (not geocoded).")
        unique = list({p for p in parts_of.values()})
        t0 = time.monotonic()
        coords = batch_geocode(geo_session, unique)
        applied = 0
        for i, p in parts_of.items():
            c = coords.get(p)
            if c:
                recs[i]["latitude"], recs[i]["longitude"] = c[0], c[1]
                applied += 1
        print(f"Geocoded {applied}/{len(recs)} rows ({len(coords)} unique addresses matched "
              f"of {len(unique)} sent) in {time.monotonic() - t0:.1f}s.", flush=True)

    rows, dropped = [], 0
    for rec in recs:
        if rec.get("latitude") is None or rec.get("longitude") is None:
            dropped += 1                              # coordless AND the geocoder couldn't place it
            continue
        rows.append(to_row(rec, host_id))
    if dropped:
        print(f"Dropped {dropped} coordless events the geocoder couldn't place (address missing/unmatched).")
    return rows


# PostgREST's answer when a row names a column the schema cache has never heard
# of: {"code":"PGRST204","message":"Could not find the 'x' column of 'events' …"}
_PGRST_UNKNOWN_COLUMN = re.compile(
    r"Could not find the '([A-Za-z_][A-Za-z0-9_]*)' column", re.I)


def _unknown_column(resp) -> Optional[str]:
    """The column name PostgREST says does not exist, or None."""
    try:
        message = str((resp.json() or {}).get("message") or "")
    except Exception:                                       # noqa: BLE001
        message = ""
    found = _PGRST_UNKNOWN_COLUMN.search(message)
    return found.group(1) if found else None


def upsert(rows: List[Dict[str, Any]], url: str, key: str) -> Tuple[int, int, int]:
    """Write rows, and SURVIVE A COLUMN THE DATABASE HAS NOT GOT YET.

    THE BLAST RADIUS IS WHY THIS EXISTS. to_row writes every column for every
    adapter, so a repo that has merged a migration's CODE before the migration
    itself has run against Supabase does not lose one feature — it loses the
    whole night. A 400 is not retryable, so each batch of 50 falls straight
    through to the row-by-row isolation below, every one of those rows fails the
    same way, and all thirty-seven adapters write ZERO rows having made fifty
    times the requests to do it. That is a deploy-ordering mistake with a
    catastrophic, silent-looking failure, and the ordering is between two
    different systems (a git merge and a hand-run `supabase db push`) that
    nothing coordinates.

    So an unknown column is dropped ONCE, loudly, and the run continues without
    it. The feature that needed it is simply absent until the migration lands,
    which is what "not deployed yet" ought to look like. Same discipline as
    _explain(): read what the server actually said instead of treating every
    4xx as one thing.
    """
    import requests, time
    endpoint = url.rstrip("/") + "/rest/v1/events?on_conflict=external_source,external_id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    absent: set = set()          # columns this database has not got yet

    def _strip(batch):
        return ([{k: v for k, v in row.items() if k not in absent} for row in batch]
                if absent else batch)

    def _post(batch):
        return requests.post(endpoint, headers=headers,
                             data=json.dumps(_strip(batch)), timeout=30)

    def _drop_unknown(resp) -> bool:
        """True when a column was newly dropped and the caller should retry."""
        column = _unknown_column(resp)
        if not column or column in absent:
            return False
        absent.add(column)
        print(f"::warning::events.{column} does not exist in this database — "
              f"writing rows WITHOUT it for the rest of this run. The migration "
              f"that adds it has not been applied yet; apply it and re-run to "
              f"populate the column.", flush=True)
        return True

    import random
    from collections import deque
    TRIES = 3                        # named, so the log cannot drift from the loop
    RETRYABLE = {408, 429, 500, 502, 503, 504}   # 57014 surfaces as HTTP 500
    # Once the retries are spent, a final answer in here means the request never
    # happened — the database or its edge did not take it — so nothing about the
    # ROWS is learned and sending them one by one only multiplies the load.
    OUTAGE = {408, 429, 502, 503, 504}
    HALVE_FLOOR = 12                 # a 500 retries 50 -> 25 -> 12, then row by row

    def _backoff(n):
        """2s, 4s, 8s for n = 0, 1, 2, plus up to a quarter again of jitter, so
        the parallel sync jobs of one run do not all come back on the same beat."""
        base = 2.0 ** (n + 1)
        return base + random.uniform(0, base / 4)

    def _answer(resp):
        """'HTTP 503 PGRST002 (Could not query ...)': the server's own words."""
        try:
            body = resp.json()
        except Exception:                                   # noqa: BLE001
            body = None
        body = body if isinstance(body, dict) else {}
        code, message = body.get("code"), body.get("message")
        return (f"HTTP {resp.status_code}" + (f" {code}" if code else "")
                + (f" ({str(message)[:80]})" if message else ""))

    class _Unanswered(Exception):
        """A final 502/503/504/408/429: counted like a transport loss."""

    def _post_retry(batch, tries=TRIES):
        # A batch can fail transiently on a statement timeout (57014) or lock wait,
        # esp. if a moderation trigger is slow. Back off and retry the whole batch;
        # what the FINAL answer means is decided in the loop below.
        #
        # A TIMEOUT IS NOT A STATUS CODE, AND THAT IS THE WHOLE BUG. This loop
        # only ever looked at resp.status_code, so it retried the case where the
        # server said "503" and let the case where the server said NOTHING blow
        # straight out of upsert() and kill the process. They are the same
        # transient condition — Postgres busy — differing only in whether the
        # answer arrived inside our 30s read timeout. On 2026-08-29 that cost a
        # 4h50m Meetup sweep its entire international leg and took the OSM
        # second-hand run with it, both on `ReadTimeout ... (read timeout=30)`
        # raised from this very line.
        #
        # Re-POSTing is safe: the endpoint carries
        # on_conflict=external_source,external_id with merge-duplicates, so a
        # batch that DID land before we gave up reading merges onto itself
        # rather than duplicating.
        resp = None
        for n in range(tries):
            try:
                resp = _post(batch)
            except _TransportError:
                if n == tries - 1:
                    raise                            # caller decides; see below
                time.sleep(_backoff(n))
                continue
            if resp.status_code < 300 or resp.status_code not in RETRYABLE:
                return resp
            if n < tries - 1:
                time.sleep(_backoff(n))
        return resp

    # ONE UNREACHABLE BATCH MUST NOT DISCARD THE OTHER NINE HUNDRED. The batches
    # are independent writes, so a batch the transport could not deliver is a
    # hole, not a reason to throw away the batches that would have landed after
    # it. aggregate-events.yml already had to learn this the expensive way — the
    # Meetup job syncs its US leg before sweeping internationally precisely so
    # "a later timeout must not discard it" — but the protection stopped at the
    # job boundary and left every batch inside one sync sharing a single fate.
    #
    # `lost` is counted apart from `skipped` because they mean opposite things:
    # skipped is Postgres refusing a row (a data problem, the row is the
    # suspect), lost is never having got an answer (an infrastructure problem,
    # the row is fine and should be retried next run). Reporting both as
    # "skipped" would send someone reading per-row reasons that do not exist.
    sent = skipped = lost = 0
    misses = 0                       # CONSECUTIVE batches the transport ate
    GIVE_UP_AFTER = 5                # ...before we call it an outage, not a blip
    # PostgREST's bulk merge treats omitted keys inconsistently when objects in
    # one JSON array have different shapes. Partition first so an agenda-less
    # adapter row can never turn into an explicit null during a mixed batch.
    groups = {}
    for row in rows:
        groups.setdefault(frozenset(row), []).append(row)
    chunks = [group[i:i + 50] for group in groups.values() for i in range(0, len(group), 50)]
    # WHAT THE FINAL ANSWER MEANS DECIDES WHAT HAPPENS NEXT, because two 5xx
    # want opposite things (docs/agents/sync-eventstore-and-paging.md). Every
    # failed batch used to go row by row: 3 tries on the batch, then 3 on each
    # of its 50 rows, 153 POSTs per batch against a database already failing
    # (2026-08-29 13:36:49, OpenActive: 503 "Could not query the database for
    # the schema cache").
    #   * a 4xx (not 408/429): the ROWS are the suspect, so isolate row by row;
    #   * a 500: a statement timeout (57014) wants a SMALLER bite, and a trigger
    #     raising anything but P0001 is also a 500 about the rows, so halve
    #     50 -> 25 -> 12 and isolate only what still fails at 12;
    #   * a 502/503/504 (PGRST002 is a 503), or a 408/429: the request never
    #     happened, so do NOT isolate. The batch is lost, it is a miss toward
    #     GIVE_UP_AFTER, and the next batch waits ~8s.
    queue = deque(chunks)
    while queue:
        chunk = queue.popleft()
        settled = 0                  # rows of THIS chunk already counted sent/skipped
        try:
            resp = _post_retry(chunk)
            # Before falling through to the expensive row-by-row isolation, ask
            # whether the whole batch failed for a reason that is true of EVERY row
            # and cheap to fix. A missing column is exactly that.
            if resp.status_code >= 300 and _drop_unknown(resp):
                resp = _post_retry(chunk)
            if resp.status_code < 300:
                sent += len(chunk)
                misses = 0
                continue
            if resp.status_code in OUTAGE:
                raise _Unanswered(_answer(resp))
            if resp.status_code >= 500 and len(chunk) >= 2 * HALVE_FLOOR:
                half = len(chunk) // 2
                queue.appendleft(chunk[half:])
                queue.appendleft(chunk[:half])
                print(f"  {_answer(resp)} on a {len(chunk)}-row batch: re-sending it "
                      f"as {half} + {len(chunk) - half}", flush=True)
                continue
            # A whole-batch rejection is usually one bad row (e.g. the moderation
            # trigger raising 'content_blocked'). Re-send the batch row-by-row so the
            # clean events still land and only the offending ones are skipped + logged.
            for row in chunk:
                r = _post_retry([row])
                if r.status_code >= 300 and _drop_unknown(r):
                    r = _post_retry([row])
                if r.status_code < 300:
                    sent += 1
                elif r.status_code in OUTAGE:
                    # The database stopped answering mid-isolation: that is not
                    # this row's fault, and the next 40 rows would say the same.
                    raise _Unanswered(_answer(r))
                else:
                    skipped += 1
                    try:
                        reason = r.json().get("message", "")
                    except Exception:
                        reason = r.text[:80]
                    print(f"  skipped [{r.status_code} {reason}] {row.get('title')!r}")
                settled += 1          # counted above; must not be counted again below
            misses = 0
        except (_TransportError, _Unanswered) as e:
            # Only the rows NOT already counted. The row-by-row pass can time out
            # halfway, and blaming the whole chunk would report more rows than the
            # chunk holds — sent + skipped + lost must equal len(rows).
            unwritten = len(chunk) - settled
            lost += unwritten
            misses += 1
            why = (f"Supabase answered {e}" if isinstance(e, _Unanswered)
                   else f"no answer from Supabase ({type(e).__name__})")
            # "The next run will re-send them" was only half true. A NEW row is
            # re-sent by the next run. An UPDATE lost on a refresh day is not:
            # every run in between is --only-new and skips ids already in the
            # table, so the stale row stays until the next refresh day.
            print(f"  ::warning::lost {unwritten} row(s): {why} after {TRIES} attempts. "
                  f"They are unwritten, not rejected: a NEW row is re-sent by the next "
                  f"run, but an UPDATE waits for the next refresh day, because the "
                  f"--only-new runs in between skip ids already in the table.", flush=True)
            # A blip costs one batch. An outage would otherwise cost 3 attempts
            # x 30s on every remaining batch — hours of a job that cannot write
            # a single row — so stop asking once it is clearly not a blip.
            if misses >= GIVE_UP_AFTER:
                remaining = sum(len(c) for c in queue)
                lost += remaining
                print(f"  ::error::giving up: {misses} consecutive batches went "
                      f"unanswered. Abandoning {remaining} further row(s) rather "
                      f"than spending the job's clock on a database that is not "
                      f"answering.", flush=True)
                break
            if queue:
                time.sleep(_backoff(2))      # ~8s before the next batch asks again
    return sent, skipped, lost


_LEET = str.maketrans("@0135$!7", "aoiessit")


class StateColumnMissing(RuntimeError):
    """The server refused a STATE_FIELDS column it has not got (yet)."""


def fetch_import_state(session, url: str, key: str, candidates, fields=(), detail=None):
    """Read existence AND ownership only for the imports this run may write.

    `fields` adds columns to the same bounded reads (STATE_FIELDS: the times and
    the hidden/cancelled stamps), and `detail` collects them per external_id.
    A column the server leaves out is simply absent from `detail`, which every
    caller reads as "unknown, do nothing".

    A small feed must not scan the global catalog. Each indexed IN query holds
    at most 100 IDs and 6 KB of URL. Paging is confined to that bounded set and
    follows returned rows, not an assumed server page size. Fail CLOSED: an
    unavailable ownership check must never become permission to overwrite.
    """
    candidates = list(candidates)
    if any(not isinstance(eid, str) or not eid for eid in candidates):
        raise RuntimeError("Invalid import identity; no events were written.")
    ids = sorted(set(candidates))
    base = url.rstrip("/") + "/rest/v1/events"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}

    def endpoint(chunk, offset=0):
        # JSON quoting also escapes quotes/backslashes in PostgREST IN values.
        values = ",".join(json.dumps(eid, ensure_ascii=False) for eid in chunk)
        params = {"external_source": "eq.mapsee", "select": ",".join(("external_id", "claimed_at") + tuple(fields)),
                  "external_id": f"in.({values})", "order": "external_id.asc",
                  "limit": str(len(chunk)), "offset": str(offset)}
        return base + "?" + urllib.parse.urlencode(params)

    chunks, chunk = [], []
    for eid in ids:
        if chunk and (len(chunk) >= 100 or len(endpoint(chunk + [eid], 99)) > 6000):
            chunks.append(chunk)
            chunk = []
        chunk.append(eid)
        if len(endpoint(chunk, 99)) > 6000:
            raise RuntimeError("Import identity exceeds lookup URL budget; no events were written.")
    if chunk:
        chunks.append(chunk)
    def read_chunk(chunk):
        """One bounded IN query, paged; its rows only once the whole chunk read."""
        got, got_detail = {}, {}
        wanted, seen, offset, expected_count = set(chunk), set(), 0, None
        while expected_count is None or offset < expected_count:
            response = session.get(endpoint(chunk, offset), headers=headers, timeout=30)
            if response.status_code == 400 and fields and _missing_column(response):
                raise StateColumnMissing("unknown state column")
            if response.status_code not in (200, 206):
                raise ValueError(f"HTTP {response.status_code}")
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("invalid response")
            cr = getattr(response, "headers", {}).get("Content-Range", "")
            match = re.fullmatch(r"(\d+)-(\d+)/(\d+)", cr)
            empty_match = re.fullmatch(r"\*/(\d+)", cr)
            if empty_match:
                if rows:
                    raise ValueError("non-empty response has empty range")
                count = int(empty_match.group(1))
                if count > len(chunk) or offset != count:
                    raise ValueError("premature empty response")
            elif match:
                start, end, count = map(int, match.groups())
                if not rows or start != offset or end != start + len(rows) - 1:
                    raise ValueError("inconsistent response range")
                if len(rows) > len(chunk) or count > len(chunk) or not end < count:
                    raise ValueError("inconsistent response count")
            else:
                raise ValueError("missing or malformed response range")
            if expected_count is None:
                expected_count = count
            elif count != expected_count:
                raise ValueError("response count changed during lookup")
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict) or "claimed_at" not in row:
                    raise ValueError("ownership field missing")
                eid = row.get("external_id")
                if eid not in wanted or eid in seen:
                    raise ValueError("unexpected or repeated identity")
                seen.add(eid)
                got[eid] = row["claimed_at"] is not None
                got_detail[eid] = {f: row[f] for f in fields if f in row}
            offset += len(rows)
        return got, got_detail

    state = {}
    for chunk in chunks:
        # A FAILED READ IS RETRIED, then fails closed. One 503 or timeout among
        # ~690 chunks (OpenActive's 68,000 keys) used to stop the whole sync:
        # on 2026-10-09 six jobs lost the day's writes to a 30-minute database
        # blip that the next try would have ridden out. A retry re-reads the
        # whole chunk from offset 0, and nothing from a failed try is kept.
        for attempt in range(STATE_READ_TRIES):
            try:
                got, got_detail = read_chunk(chunk)
                break
            except StateColumnMissing:
                raise
            except Exception as error:                # noqa: BLE001
                if attempt + 1 == STATE_READ_TRIES:
                    # Do not echo request exceptions: their URL/headers can carry
                    # credentials. Our own checks' words are safe; anything else
                    # is named by its class only. A partial guard is never a write.
                    why = str(error) if type(error) is ValueError else type(error).__name__
                    raise RuntimeError(f"Could not verify imported event ownership ({why}, "
                                       f"{STATE_READ_TRIES} tries); no events were written. "
                                       f"Retry this sync when the database is available.") from None
                _patch_sleep(2.0 * (attempt + 1))
        state.update(got)
        if detail is not None:
            detail.update(got_detail)
    return state


def _missing_column(response) -> bool:
    """Postgres 42703 / PostgREST PGRST204: a selected column does not exist."""
    try:
        body = response.json()
    except Exception:                                 # noqa: BLE001
        return False
    return isinstance(body, dict) and (body.get("code") in ("42703", "PGRST204")
                                       or "does not exist" in str(body.get("message", "")))


# --------------------------------------------------------------------------- #
# A ROW WHOSE IDENTITY CHANGED BECAUSE WE LEARNED TO READ ITS SOURCE
# --------------------------------------------------------------------------- #
# external_id is a fingerprint hashed from the title and the venue text, so the
# day an adapter reads a source more correctly, every row it already wrote has
# a new identity. The upsert keys on external_id and cannot delete: left alone,
# the next run inserts the clean row and the garbled one stays beside it until
# the event is past. That was the ics adapter's fix for charset-less feeds (see
# _decode_ics there): OpenAgenda's rows, a few thousand, all of them renamed.
#
# So an adapter that knows an event's old identity says so
# (NormalizedEvent.legacy_fingerprints), and before anything reads existence
# the row still filed under it is MOVED to the new key. Its id, its /e/ link,
# its series and any claim on it survive. Nothing is deleted: a legacy row whose
# new key is already taken is hidden, and a claimed one is left alone.
def legacy_pairs(store_path: str) -> Dict[str, List[str]]:
    """{fingerprint: its legacy fingerprints}, for the records that have any.
    Empty on any error: main() reads the same file next and says what is wrong.
    A store with no legacy key anywhere is not parsed twice: that is every store
    but a feed store written before ics's LEGACY_KEYS_UNTIL."""
    try:
        raw = open(store_path, "rb").read()
        if b'"legacy_fingerprints"' not in raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return {r["fingerprint"]: list(r["legacy_fingerprints"]) for r in data.get("events", [])
            if isinstance(r, dict) and r.get("fingerprint") and r.get("legacy_fingerprints")}


def rekey_legacy(session, url: str, key: str, pairs: Dict[str, List[str]]):
    """Move rows still filed under a legacy fingerprint to their new one.

    Returns (moved, held). `moved` must be WRITTEN this run even under
    --only-new: the row exists under its new key now, but it still carries the
    text it was garbled with. `held` must NOT be written this run: its legacy row
    exists and could not be moved, or could not be looked up, and an insert under
    the new key would put a second copy beside it. A later run tries again.
    Fails closed, like fetch_import_state, whose checked reads it uses."""
    legacy_of: Dict[str, str] = {}
    for fp, olds in pairs.items():
        for old in olds:
            if old and old != fp:
                legacy_of.setdefault(old, fp)
    if not legacy_of:
        return set(), set()
    try:
        found = fetch_import_state(session, url, key, list(legacy_of))
        taken = fetch_import_state(session, url, key, sorted({legacy_of[o] for o in found})) if found else {}
    except RuntimeError:
        print(f"::warning::Legacy re-key: the lookup failed, so the {len(pairs)} record(s) with a "
              f"legacy fingerprint are held back this run rather than risk writing any twice.", flush=True)
        return set(), set(pairs)
    if not found:
        return set(), set()
    moves, hides, left_claimed, planned = [], [], 0, set(taken)
    for old in sorted(found):
        new = legacy_of[old]
        if new in planned:                            # the new key already has (or will have) a row
            if found[old]:
                left_claimed += 1                     # somebody owns the garbled copy: theirs to keep
            else:
                hides.append(old)
        else:
            moves.append((old, new))
            planned.add(new)
    base = url.rstrip("/") + "/rest/v1/events"
    hdr = {"apikey": key, "Authorization": f"Bearer {key}",
           "Content-Type": "application/json", "Prefer": "return=minimal"}

    def patch(old, body):
        q = urllib.parse.urlencode({"external_source": "eq.mapsee", "external_id": f"eq.{old}"})
        try:
            r = session.patch(f"{base}?{q}", headers=hdr, data=json.dumps(body), timeout=30)
            return r.status_code in (200, 204)
        except Exception:                             # noqa: BLE001 - a failed move is held, not raised
            return False

    stamp = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=8) as pool:
        move_ok = list(pool.map(lambda m: patch(m[0], {"external_id": m[1]}), moves))
        hide_ok = list(pool.map(lambda old: patch(old, {"hidden_at": stamp}), hides))
    moved = {new for (old, new), ok in zip(moves, move_ok) if ok}
    held = {new for (old, new), ok in zip(moves, move_ok) if not ok}
    print(f"Legacy re-key: {len(found)} row(s) still under a legacy fingerprint; moved {len(moved)}, "
          f"hid {sum(hide_ok)} of {len(hides)} whose new key already had a row, left {left_claimed} "
          f"claimed, held back {len(held)} whose move failed.", flush=True)
    return moved, held


# --------------------------------------------------------------------------- #
# A ROW THE SOURCE HAS CALLED OFF (owner, 2026-10-05: "we don't want users to go
# to an event or center that is closed")
# --------------------------------------------------------------------------- #
# An upsert cannot delete, so until now a cancellation the adapters could SEE
# only stopped the record being written again: the row from last week stayed on
# the map. Three things now reach it, all here, all on unclaimed imports only:
#
#   * a TOMBSTONE in the store (EventStore.cancel, a notice title, Ticketmaster's
#     status): the publisher said so. Applied in every run.
#   * ABSENCE (--retire-absent --manifest PATH): a source whose whole timetable
#     was read (EventStore.mark_complete) stopped listing a session it listed at
#     the last complete read. For the community-centre feeds that IS how a pool
#     closure or a called-off class shows up.
#   * UN-CANCEL: a fingerprint a source READ WHOLE this run writes live again,
#     whose row still carries the two stamps we wrote.
#
# THE MARKER IS TWO EQUAL STAMPS. ../mapsee's cancel_event sets cancelled_at with
# hidden_at, and so does this sync, in ONE PATCH with ONE value, never re-stamped
# afterwards. Every other writer of hidden_at on an imported row
# (mapsee_prune_cancelled, the retire_* scripts, rekey_legacy, an organizer
# take-down) sets hidden_at ALONE. So a row whose cancelled_at and hidden_at are
# the same instant is ours and untouched; a take-down or a prune that lands on top
# of our cancellation changes hidden_at, and the lift (which names both exact
# stamps in its PATCH filter) can no longer match it - whatever procedure the
# person followed. A row we cancelled that something later UN-hides (a retire
# script's --unhide) is cancelled again, both stamps anew, while a tombstone still
# says cancelled. Every write below carries
# its conditions in the PATCH filter itself (claimed_at=is.null, and the state
# it expects), so a claim that lands between our read and our write is never
# overwritten: the database evaluates the condition, not this process.

# The import-state read also returns these, in the same bounded chunks (no
# extra request): what a daily run needs to see a time change or a cancellation.
STATE_FIELDS = ("starts_at", "ends_at", "hidden_at", "cancelled_at")

# A cancellation younger than this is never undone by a live listing. Only a
# source read WHOLE this run can lift one at all (main), and only a row whose two
# stamps are still the ones we wrote (import_cancelled), so this hold is no longer
# what stops two stores flapping a row: it keeps a session that blinked out of
# one complete read and back into the next from bouncing on and off the map
# within a day. 30 hours = a day plus the daily run's ~5 h spread (06:17 to
# ~11:20 UTC) plus margin. The price: a real relist returns after ~2 days.
UNCANCEL_AFTER = timedelta(hours=30)

# Absence never reaches a session starting within 2 hours (a run is not a
# promise about the next hour, and a same-day change is the source's to make in
# its own words), nor the last day of either read's window (where two reads'
# horizons, computed on different days, disagree by construction).
ABSENT_NEAR = timedelta(hours=2)
ABSENT_FAR_MARGIN = timedelta(days=1)
# More than this share of a source's in-window sessions gone at once is a
# glitch or a schedule rebuild, not a wave of cancellations: cancel none.
ABSENT_MAX_SHARE = 0.10
ABSENT_MAX_FLOOR = 3
# ...and a WHOLE unit emptied at once is the same glitch at a small scale: the
# floor above let a "complete" read listing none of a unit's 1-3 sessions cancel
# them all (revize:olympia holds 1 in-window session, revize:bladensburg 2,
# 2026-10-05). From this many sessions up, all of them gone trips the breaker.
ABSENT_WHOLE_UNIT_MIN = 2
# A failed cancellation write is retried this many times (a transient 503 must
# not cost the night: patch_imports used to give up on the first answer).
PATCH_TRIES = 3
# ...and so is a failed chunk of the import-state read (fetch_import_state).
STATE_READ_TRIES = 3


def _utc(value) -> Optional[datetime]:
    """An aware UTC instant, or None when the stamp names no instant."""
    if not value:
        return None
    s = str(value).strip()
    if len(s) == 10:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else None


def import_cancelled(detail: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """This pipeline cancelled the stored row, nothing has touched it since, and
    long enough ago to lift (see UNCANCEL_AFTER).

    OURS MEANS BOTH STAMPS ARE THE SAME INSTANT. apply_cancellations writes
    cancelled_at and hidden_at in one PATCH with one value, and nothing re-stamps
    either afterwards. A take-down (README Conduct), a prune or a retire script
    sets hidden_at alone, so a row hidden again on top of our cancellation - with
    or without cancelled_at cleared - no longer has equal stamps and is never
    lifted. It does not depend on anyone remembering a procedure."""
    if not detail or not detail.get("cancelled_at") or not detail.get("hidden_at"):
        return False
    at, hid = _utc(detail["cancelled_at"]), _utc(detail["hidden_at"])
    return (at is not None and at == hid
            and at <= (now or datetime.now(timezone.utc)) - UNCANCEL_AFTER)


def time_changed(rec: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> bool:
    """The stored row's starts_at/ends_at differ from what to_row would write.

    Before build_rows, on purpose: it decides whether an --only-new run keeps a
    stored row, so it must cost no geocoding and no extra read. It FAILS TOWARD
    KEEPING NOTHING: a value either side cannot turn into an instant (a naive
    time with no coordinates yet, a missing column) means "leave it to
    Wednesday", because the opposite default would rewrite every stored row
    daily. A standing row's window is rolled by the database (skip_cols)."""
    if not detail or "starts_at" not in detail or rec.get("recurring_days"):
        return False
    start = rec.get("start_utc") or rec.get("start_local")
    end = rec.get("end_utc") or rec.get("end_local")
    if not start:
        return False
    lat, lon = rec.get("latitude"), rec.get("longitude")
    if _utc(start) is None or (end and _utc(end) is None):
        if lat is None or lon is None:
            return False                              # the geocoder places it later
        start, end = _to_utc_if_naive(start, lat, lon), _to_utc_if_naive(end, lat, lon)
        start, end = _anchor_all_day(start, end, lat, lon)
    ours, theirs = _utc(start), _utc(detail.get("starts_at"))
    if ours is None or theirs is None:
        return False
    if ours != theirs:
        return True
    if end and "ends_at" in detail:                   # only a REAL end; a default follows start
        ours, theirs = _utc(end), _utc(detail.get("ends_at"))
        return ours is not None and theirs is not None and ours != theirs
    return False


def cancellation_inputs(store_path: str) -> Dict[str, Any]:
    """The store's tombstones and complete reads, and for each complete unit the
    fingerprints it holds now. Empty on a store that has neither (one byte scan,
    like legacy_pairs), so a store nothing cancelled costs no second parse."""
    out = {"tombstones": {}, "complete": {}, "live": set(), "units": {}, "standing": {}, "seen": set(),
           "ident": {}}
    try:
        raw = open(store_path, "rb").read()
        if b'"tombstones"' not in raw and b'"complete_reads"' not in raw:
            return out
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return out
    out["tombstones"] = {t["fingerprint"]: t for t in data.get("tombstones") or []
                         if isinstance(t, dict) and t.get("fingerprint")}
    complete = data.get("complete_reads")
    out["complete"] = complete if isinstance(complete, dict) else {}
    # Listed by the source, not written by us (EventStore.mark_seen): never absent.
    out["seen"] = {fp for fp in data.get("seen") or [] if isinstance(fp, str)}
    units = {u: {} for u in out["complete"]}
    standing = {u: set() for u in out["complete"]}
    # A record belongs to a unit when one of its source refs carries the unit's
    # source and a source_id starting with its id_prefix (a merged record can
    # belong to several). Indexed by source: a rec group has ~30 units.
    by_source: Dict[Any, List[Tuple[str, str]]] = {}
    for unit, read in out["complete"].items():
        if isinstance(read, dict):
            by_source.setdefault(read.get("source"), []).append((unit, str(read.get("id_prefix") or "")))
    for rec in data.get("events") or []:
        fp = rec.get("fingerprint") if isinstance(rec, dict) else None
        if not fp:
            continue
        out["live"].add(fp)
        for ref in rec.get("sources") or []:
            if not isinstance(ref, dict):
                continue
            for unit, prefix in by_source.get(ref.get("source"), ()):
                if str(ref.get("source_id") or "").startswith(prefix):
                    units[unit][fp] = rec.get("start_utc") or rec.get("start_local")
                    if fp not in out["ident"]:
                        out["ident"][fp] = absence_ident(rec)
                    if rec.get("recurring_days"):
                        standing[unit].add(fp)
    out["units"], out["standing"] = units, standing
    return out


def load_manifest(path: str) -> Optional[Dict[str, Any]]:
    """The last complete reads' fingerprints, or None. No manifest means no
    absence this run: a guess about what was there is never a reason to hide."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as error:                        # noqa: BLE001
        print(f"::warning::Absence: manifest {path} unreadable ({type(error).__name__}); "
              f"no absence this run, and a fresh one is written.", flush=True)
        return None
    units = data.get("units") if isinstance(data, dict) else None
    return units if isinstance(units, dict) else None


def write_manifest(path: str, previous: Optional[Dict[str, Any]], inputs: Dict[str, Any]) -> int:
    """Replace the entries of the units read whole this run; keep the others.

    A session the source still LISTED but we could not write this run (seen)
    keeps its previous entry, so the day it really goes, absence still sees it."""
    units = dict(previous or {})
    seen = inputs.get("seen") or set()
    ident = inputs.get("ident") or {}
    for unit, read in inputs["complete"].items():
        if isinstance(read, dict) and read.get("absence") is False:
            continue                                  # never an absence baseline
        starts = dict(inputs["units"].get(unit, {}))
        prev = (previous or {}).get(unit)
        prev_starts = prev.get("starts") if isinstance(prev, dict) else None
        prev_ident = prev.get("ident") if isinstance(prev, dict) and isinstance(prev.get("ident"), dict) else {}
        if isinstance(prev_starts, dict) and seen:
            for fp, start in prev_starts.items():
                if fp in seen and fp not in starts:
                    starts[fp] = start
        # What absent_successors matches a gone session on next run; a session
        # carried over as seen keeps the identity it was last written with.
        ids = {fp: (ident.get(fp) or prev_ident.get(fp)) for fp in starts
               if ident.get(fp) or prev_ident.get(fp)}
        units[unit] = dict(read, starts=starts, standing=sorted(inputs["standing"].get(unit, ())), ident=ids)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "units": units}, fh, separators=(",", ":"))
    os.replace(tmp, path)
    return len(inputs["complete"])


def absent_fingerprints(previous: Optional[Dict[str, Any]], inputs: Dict[str, Any],
                        now: Optional[datetime] = None):
    """{fingerprint: unit} the last complete read wrote and this one did not.

    Only for a unit read whole THIS run and present in the previous manifest,
    only inside both reads' windows (ABSENT_NEAR, ABSENT_FAR_MARGIN), never a
    fingerprint anything in this store still lists, and none at all for a unit
    past the circuit breaker. Returns (absent, report lines, warnings)."""
    now = now or datetime.now(timezone.utc)
    absent, lines, warnings = {}, [], []
    if not previous:
        return absent, lines, warnings
    gone_anywhere = inputs["live"] | set(inputs["tombstones"]) | set(inputs.get("seen") or ())
    for unit, read in sorted(inputs["complete"].items()):
        if isinstance(read, dict) and read.get("absence") is False:
            continue                                  # whole for LIFTING only (OpenActive's folds)
        prev = previous.get(unit)
        if not isinstance(prev, dict) or not isinstance(prev.get("starts"), dict):
            lines.append(f"  {unit}: no previous complete read, baseline written")
            continue
        edges = [_utc(read.get("from")), _utc(prev.get("from")), _utc(read.get("to")), _utc(prev.get("to"))]
        if any(e is None for e in edges):
            lines.append(f"  {unit}: unreadable window, skipped")
            continue
        lo = max(now + ABSENT_NEAR, edges[0], edges[1])
        hi = min(edges[2], edges[3]) - ABSENT_FAR_MARGIN
        standing = set(prev.get("standing") or [])
        in_window = []
        for fp, start in prev["starts"].items():
            if fp in standing:
                in_window.append(fp)                  # a standing row has no date to be outside of
                continue
            at = _utc(start)
            if at is None and start and len(str(start)) >= 16:
                # A naive local time: an instant somewhere in a 26-hour band.
                try:
                    naive = datetime.fromisoformat(str(start)[:19]).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if lo <= naive - timedelta(hours=14) and naive + timedelta(hours=12) <= hi:
                    in_window.append(fp)
            elif at is not None and lo <= at <= hi:
                in_window.append(fp)
        gone = [fp for fp in in_window if fp not in gone_anywhere]
        limit = max(ABSENT_MAX_FLOOR, int(ABSENT_MAX_SHARE * len(in_window)))
        if len(in_window) >= ABSENT_WHOLE_UNIT_MIN and len(gone) == len(in_window):
            limit = len(gone) - 1                     # the whole unit at once: see ABSENT_WHOLE_UNIT_MIN
        if len(gone) > limit:
            warnings.append(f"::warning::Absence: {unit} dropped {len(gone)} of {len(in_window)} "
                            f"in-window session(s) since its last complete read (limit {limit}); "
                            f"cancelled none. A glitch or a rebuilt schedule is not a wave of "
                            f"cancellations; this read becomes the baseline, so read the source "
                            f"if a whole centre may have closed.")
            continue
        for fp in gone:
            absent[fp] = unit
        lines.append(f"  {unit}: {len(gone)} of {len(in_window)} in-window session(s) gone "
                     f"({lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M} UTC)")
    return absent, lines, warnings


# --------------------------------------------------------------------------- #
# A SESSION THAT CHANGED ITS NAME OR ITS CLOCK IS NOT CANCELLED
# --------------------------------------------------------------------------- #
# A fingerprint hashes the title (and in some timetables the clock), so the day
# a publisher retitles a session or moves it a quarter of an hour, its old key
# is gone from a complete read and a new key is there. Cancelling the old row
# and inserting the new one was right for the map and wrong for everyone who had
# RSVP'd: their row said "Cancelled" for a session that is on (run 116,
# 2026-10-06: Barcelona retitled "Nit d'ànimes" to "Nit d'ànimes a la Ludoteca
# Ca L'Arnó", same register, day, clock and venue; Surrey moved a Pickleball
# from 17:15 to 17:00 on the same class and date). So, BEFORE the upsert, a gone
# key with exactly one clear successor in its unit has its row MOVED to the new
# key, as rekey_legacy moves a row a better reading renamed: same id, /e/ link
# and RSVPs, and a take-down stays a take-down.
#
# A successor is a key NEW to the unit since its last complete read, at the SAME
# VENUE, under a SIMILAR title, matched one to one, and either
#   * on THE SAME OCCURRENCE LINK the same day, where exactly one session carries
#     that link in each read (PerfectMind's classId + occurrenceDate); or
#   * at THE SAME START, where the old title is listed nowhere else at that venue
#     now and the new one was listed nowhere there before: a series renamed, not
#     a class swapped into another's slot.
# SIMILAR is strict, because a wrong move hands someone's RSVP to a different
# session, which is worse than telling them it is off: one title must contain the
# other word for word, and the words that differ must not name an audience, an
# age or a level ("Computer Lab, ages 6+" is not "ages 60+"; run 116 holds 3,457
# same-slot pairs of DIFFERENT sessions that share half their words, e.g. "Drop In
# Badminton - Adult" / "Drop In Volleyball - Adult"). Anything ambiguous,
# unstored, claimed, or whose new key is already stored is left to the
# cancellation, exactly as before. Matching runs only inside the breaker's
# allowance (absent_fingerprints), and only against a manifest that recorded
# identities (written from 2026-10-07 on).
def _ident_words(text) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).lower()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", folded))


def absence_ident(rec: Dict[str, Any]) -> List[str]:
    """[link key, venue key, title words] for one store record: what a gone
    session's successor must share (see above). Hashes keep the manifest small."""
    link = primary_url(rec) or ""
    venue = _ident_words(rec.get("venue_name"))
    if not venue and rec.get("latitude") is not None and rec.get("longitude") is not None:
        venue = f"{round(float(rec['latitude']), 4)},{round(float(rec['longitude']), 4)}"
    return [hashlib.sha1(link.encode("utf-8")).hexdigest()[:12] if link else "",
            hashlib.sha1(venue.encode("utf-8")).hexdigest()[:10] if venue else "",
            _ident_words(rec.get("name"))[:400]]


# A word that tells WHO or WHAT LEVEL a session is for. Two titles that differ by
# one of these are two sessions, however much else they share (run 116: "Adult
# Shinny (18+)" / "... [Goalies]", "Length Swim" / "Length Swim - Ladies Only").
_AUDIENCE_WORD = re.compile(
    r"\d+|adults?|adultes?|youth|teens?|ados?|jeunes?|child|children|enfants?|kids?|infants?|nens|"
    r"seniors?|older|aines?|gent|gran|aikuiset|lapset|nuoret|seniorit|goalies?|gardiens?|ladies|"
    r"women|womens|men|mens|femmes?|hommes?|girls?|boys?|filles?|garcons?|family|families|famille|"
    r"parents?|preschool|toddlers?|baby|babies|bebes?|beginners?|debutants?|intermediate|advanced|"
    r"avances?|junior|juniors|novice|elite|masters?|adapted|adapte|inclusive|members?|tous|all")


def _similar_titles(a: str, b: str) -> bool:
    """One title contains the other WORD FOR WORD, and none of the words that
    differ names an audience, an age or a level (see above)."""
    if not a or not b:
        return False
    if a == b:
        return True
    if f" {a} " not in f" {b} " and f" {b} " not in f" {a} ":
        return False
    return not any(_AUDIENCE_WORD.fullmatch(w) for w in set(a.split()) ^ set(b.split()))


def absent_successors(previous: Optional[Dict[str, Any]], inputs: Dict[str, Any],
                      absent: Dict[str, str]) -> Dict[str, str]:
    """{gone fingerprint: its one clear successor in the same unit} (see above)."""
    out: Dict[str, str] = {}
    if not previous or not absent:
        return out
    ident_now = inputs.get("ident") or {}
    by_unit: Dict[str, List[str]] = {}
    for fp, unit in absent.items():
        by_unit.setdefault(unit, []).append(fp)
    for unit, olds in sorted(by_unit.items()):
        prev = previous.get(unit) if isinstance(previous.get(unit), dict) else {}
        prev_ident = prev.get("ident") if isinstance(prev.get("ident"), dict) else {}
        prev_starts = prev.get("starts") if isinstance(prev.get("starts"), dict) else {}
        now_starts = inputs["units"].get(unit) or {}
        new = [g for g in sorted(now_starts) if g not in prev_starts and ident_now.get(g)]
        if not prev_ident or not new:
            continue
        then_ids = [v for v in prev_ident.values() if isinstance(v, list) and len(v) >= 3]
        now_ids = {g: ident_now[g] for g in now_starts if ident_now.get(g)}
        links_then = Counter(str(v[0]) for v in then_ids if v[0])
        links_now = Counter(str(v[0]) for v in now_ids.values() if v[0])
        # (venue key, title) pairs each read lists: the series guard on the slot rule.
        titles_then = {(v[1], v[2]) for v in then_ids}
        titles_now = Counter((v[1], v[2]) for v in now_ids.values())
        cands: Dict[str, set] = {}
        for f in olds:
            fi = prev_ident.get(f)
            if not isinstance(fi, list) or len(fi) < 3 or not fi[1]:
                continue
            fs = str(prev_starts.get(f) or "")
            for g in new:
                gi, gs = ident_now[g], str(now_starts.get(g) or "")
                if not isinstance(gi, list) or len(gi) < 3 or gi[1] != fi[1] or not _similar_titles(fi[2], gi[2]):
                    continue
                same_link = bool(fi[0] and fi[0] == gi[0] and links_then[str(fi[0])] == 1
                                 and links_now[str(gi[0])] == 1 and len(fs) >= 10 and fs[:10] == gs[:10])
                renamed = bool(fs and fs == gs and fi[2] != gi[2]
                               and titles_now[(fi[1], fi[2])] == 0             # old title gone from the venue
                               and (gi[1], gi[2]) not in titles_then)         # new title new to the venue
                if same_link or renamed:
                    cands.setdefault(f, set()).add(g)
        claimed_by: Dict[str, set] = {}
        for f, gs in cands.items():
            for g in gs:
                claimed_by.setdefault(g, set()).add(f)
        for f, gs in cands.items():
            if len(gs) == 1 and claimed_by[next(iter(gs))] == {f}:
                out[f] = next(iter(gs))
    return out


def rekey_absent(session, url: str, key: str, successors: Dict[str, str]):
    """Move each gone key's row to its successor's key. Returns (new keys whose
    row moved, old keys that moved). Only an unclaimed stored row whose new key
    is not stored yet moves (claimed_at travels in the PATCH filter); every
    other gone key stays gone, and the cancellation after the upsert takes it."""
    if not successors:
        return set(), set()
    try:
        found = fetch_import_state(session, url, key, list(successors))
        taken = fetch_import_state(session, url, key, sorted(set(successors.values())))
    except RuntimeError:
        print(f"::warning::Absence re-key: the lookup failed, so {len(successors)} session(s) "
              f"listed under a new key are treated as gone, as before.", flush=True)
        return set(), set()
    base = url.rstrip("/") + "/rest/v1/events"
    hdr = {"apikey": key, "Authorization": f"Bearer {key}",
           "Content-Type": "application/json", "Prefer": "return=representation"}
    extra = {"external_source": "eq.mapsee", "claimed_at": "is.null", "select": "external_id"}
    moved_new, moved_old, failed = set(), set(), 0
    for old, new in sorted(successors.items()):
        if old not in found and new in taken and not taken[new]:
            # An earlier move landed but its row was never rewritten (a lost
            # reply, a lost upsert batch, a killed step): rewrite it now.
            moved_new.add(new)
            continue
        if old not in found or found[old] or new in taken:
            continue
        # Written this run whatever the reply says: if the move landed unseen the
        # row is rewritten under its new key, and if it did not, the new key is
        # simply inserted, as before. Only a CONFIRMED move spares the old key.
        moved_new.add(new)
        endpoint = next(_id_chunks(base, [old], extra))[0]
        for attempt in range(PATCH_TRIES):
            try:
                r = session.patch(endpoint, headers=hdr, data=json.dumps({"external_id": new}), timeout=30)
                if r.status_code not in (200, 204):
                    raise ValueError(f"HTTP {r.status_code}")
                # The representation is the row AFTER the write, so it carries the
                # NEW key (patch_imports, which counts the keys it sent, would
                # read every successful move as "not moved").
                rows = r.json() if r.status_code == 200 else []
                if any(isinstance(row, dict) and row.get("external_id") == new for row in rows or []):
                    moved_old.add(old)
                break
            except Exception:                         # noqa: BLE001 - the session is then cancelled, as before
                if attempt + 1 == PATCH_TRIES:
                    failed += 1
                else:
                    _patch_sleep(1.5 * (attempt + 1))
    print(f"Absence re-key: {len(successors)} gone session(s) are listed again under a new key "
          f"(renamed or re-timed); moved {len(moved_old)} stored row(s) to it, keeping its id, "
          f"link and RSVPs; the rest are left to the cancellation (not stored, claimed, new key "
          f"already stored{', or the move failed' if failed else ''}).", flush=True)
    return moved_new, moved_old


def _id_chunks(base: str, ids, extra: Dict[str, str]):
    """IN-list chunks of <=100 ids whose URL stays inside fetch_import_state's 6 KB."""
    def endpoint(chunk):
        values = ",".join(json.dumps(eid, ensure_ascii=False) for eid in chunk)
        return base + "?" + urllib.parse.urlencode(dict(extra, external_id=f"in.({values})"))
    chunk = []
    for eid in sorted(set(ids)):
        if chunk and (len(chunk) >= 100 or len(endpoint(chunk + [eid])) > 6000):
            yield endpoint(chunk), chunk
            chunk = []
        chunk.append(eid)
    if chunk:
        yield endpoint(chunk), chunk


def patch_imports(session, url: str, key: str, ids, conditions: Dict[str, str],
                  body: Dict[str, Any]):
    """PATCH unclaimed imported rows among `ids` that still meet `conditions`.
    Returns (ids changed, ids whose request failed). The conditions travel in
    the filter, so the database decides, at write time, which rows qualify."""
    base = url.rstrip("/") + "/rest/v1/events"
    hdr = {"apikey": key, "Authorization": f"Bearer {key}",
           "Content-Type": "application/json", "Prefer": "return=representation"}
    extra = dict({"external_source": "eq.mapsee", "claimed_at": "is.null", "select": "external_id"},
                 **conditions)
    changed, failed = set(), set()
    for endpoint, chunk in _id_chunks(base, ids, extra):
        for attempt in range(PATCH_TRIES):
            try:
                r = session.patch(endpoint, headers=hdr, data=json.dumps(body), timeout=30)
                if r.status_code not in (200, 204):
                    raise ValueError(f"HTTP {r.status_code}")
                rows = r.json() if r.status_code == 200 else []
                changed |= {row.get("external_id") for row in rows or [] if isinstance(row, dict)} & set(chunk)
                break
            except Exception:                         # noqa: BLE001 - owed, counted, never echoed
                # The conditions are in the filter, so a retried PATCH can only
                # match what still qualifies: repeating one is safe.
                if attempt + 1 == PATCH_TRIES:
                    failed |= set(chunk)
                else:
                    _patch_sleep(1.5 * (attempt + 1))
    return changed, failed


def _patch_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


def own_rows(session, url: str, key: str, owned: Dict[str, str]):
    """Of the own-only tombstones' rows ({external_id: listing URL}), the ones
    whose "Tickets / info:" line IS that URL - the rows that platform wrote
    (mapsee_ingest.OWN_ONLY_CANCEL_SOURCES). One bounded read per <=100 ids,
    like fetch_import_state. Returns (ids owned, ids whose read failed): a
    failed read cancels nothing, and the tombstone is read again next run."""
    if not owned:
        return set(), set()
    base = url.rstrip("/") + "/rest/v1/events"
    hdr = {"apikey": key, "Authorization": f"Bearer {key}"}
    extra = {"external_source": "eq.mapsee", "claimed_at": "is.null", "select": "external_id,description"}
    mine, failed = set(), set()
    for endpoint, chunk in _id_chunks(base, owned, extra):
        try:
            r = session.get(endpoint, headers=hdr, timeout=30)
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            for row in r.json() or []:
                eid = row.get("external_id") if isinstance(row, dict) else None
                link = owned.get(eid)
                # The URL ends at whitespace or the end: ".../events/1" never
                # matches a row whose link is ".../events/12".
                if link and re.search(re.escape(f"Tickets / info: {link}") + r"(?=\s|$)",
                                      str(row.get("description") or "")):
                    mine.add(eid)
        except Exception:                             # noqa: BLE001
            failed |= set(chunk)
    return mine, failed


def apply_cancellations(session, url: str, key: str, targets: Dict[str, str], now: datetime):
    """Cancel + hide each target row that is not already hidden for another
    reason: cancelled_at and hidden_at, ONE stamp, one PATCH (import_cancelled
    reads "equal stamps" as "ours and untouched"). A row we already cancelled is
    left exactly as it is: re-stamping cancelled_at alone would break that
    equality, and lifts no longer need the refresh, because only a source that
    read its whole timetable this run can lift (see main). Returns (newly
    cancelled, set(), failed) - the empty set is the old 'refreshed', kept for
    the callers' shape."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not targets:
        return set(), set(), set()
    # Ours, and since UN-hidden (a retire script's --unhide, a hand edit) while
    # the publisher still says cancelled: cancel it again, both stamps anew.
    # Without this the row kept cancelled_at, sat on the map, and nothing could
    # ever lift or re-hide it. claimed_at=is.null still guards it.
    rehidden, failed3 = patch_imports(session, url, key, targets,
                                      {"cancelled_at": "not.is.null", "hidden_at": "is.null"},
                                      {"cancelled_at": stamp, "hidden_at": stamp})
    new, failed2 = patch_imports(session, url, key, targets,
                                 {"cancelled_at": "is.null", "hidden_at": "is.null"},
                                 {"cancelled_at": stamp, "hidden_at": stamp})
    return new | rehidden, set(), failed2 | failed3


def lift_cancellations(session, url: str, key: str, ids, now: datetime,
                       detail: Optional[Dict[str, Dict[str, Any]]] = None):
    """Un-cancel rows written live again. Each PATCH names the EXACT stamp the
    state read saw in both columns (import_cancelled: ours and untouched), so a
    take-down or any other hide that lands between the read and this write makes
    the filter match nothing. Grouped by stamp: one cancellation run's rows share
    one. Returns (lifted, failed)."""
    if not ids:
        return set(), set()
    detail = detail or {}
    by_stamp: Dict[str, List[str]] = {}
    for eid in ids:
        st = (detail.get(eid) or {}).get("cancelled_at")
        if st and import_cancelled(detail.get(eid), now):
            by_stamp.setdefault(str(st), []).append(eid)
    lifted, failed = set(), set()
    for st, group in sorted(by_stamp.items()):
        got, bad = patch_imports(session, url, key, group,
                                 {"cancelled_at": f"eq.{st}", "hidden_at": f"eq.{st}"},
                                 {"cancelled_at": None, "hidden_at": None})
        lifted |= got
        failed |= bad
    return lifted, failed


def owned_tombstones(inputs: Dict[str, Any]) -> Dict[str, str]:
    """{external_id: listing URL} for tombstones an own-only source wrote
    (Meetup, Eventbrite): they may hide only the row that links to that URL."""
    from mapsee_ingest import OWN_ONLY_CANCEL_SOURCES
    from mapsee_spam import source_family
    out = {}
    for fp, tomb in inputs["tombstones"].items():
        if source_family(tomb.get("source")) in OWN_ONLY_CANCEL_SOURCES and tomb.get("url"):
            for eid in [fp] + list(tomb.get("legacy") or []):
                out.setdefault(eid, str(tomb["url"]))
    return out


def cancellation_targets(inputs: Dict[str, Any], absent: Dict[str, str]) -> Dict[str, str]:
    """{external_id: why} for every row to cancel: each tombstone's fingerprint
    and its legacy ones, and each absent fingerprint, minus anything this store
    still lists live (a legacy key can collide with a live one). An own-only
    tombstone with no URL names no row it may hide, so it targets nothing; with
    one, the caller narrows it to the rows own_rows proves are that platform's."""
    from mapsee_ingest import OWN_ONLY_CANCEL_SOURCES
    from mapsee_spam import source_family
    targets: Dict[str, str] = {}
    for fp, tomb in inputs["tombstones"].items():
        if source_family(tomb.get("source")) in OWN_ONLY_CANCEL_SOURCES and not tomb.get("url"):
            continue
        for eid in [fp] + list(tomb.get("legacy") or []):
            targets.setdefault(eid, str(tomb.get("source") or "?"))
    for fp, unit in absent.items():
        targets.setdefault(fp, f"absent:{unit}")
    return {eid: why for eid, why in targets.items() if eid not in inputs["live"]}


def _tally(ids, why: Dict[str, str]) -> str:
    counts: Dict[str, int] = {}
    for eid in ids:
        counts[why.get(eid, "?")] = counts.get(why.get(eid, "?"), 0) + 1
    return ", ".join(f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------- #
# "REWRITE WHAT CHANGED", WHICH IS NOT THE SAME AS "REWRITE EVERYTHING"
# --------------------------------------------------------------------------- #
# --only-new froze every row on the day it first landed, which is a permanent
# staleness bug for any adapter whose columns are all DERIVED from an upstream
# that people edit — the OSM three above all. Dropping the flag fixed that and
# bought a second problem in the same move: an UPDATE that writes the identical
# bytes still costs a dead tuple, a WAL record, an index entry and a relocated
# live row, and almost every row IS identical, because nobody edits a given
# drinking fountain twice in a month.
#
# ../mapsee measured what that costs on the read side: `events` is 993 MB of
# heap against a 256 MB shared_buffers, London's events_near reads 30,279 rows
# off 20,597 heap pages — 1.47 rows per page — and query time is simply
# `reads x ~0.9 ms`. Rows arrive in ingest order, so rewriting one relocates it
# to the end of the table and smears a city's set further apart. Bloat alone
# was cleared as the cause (1.3x, autovacuum keeping up), but a rewrite-
# everything sweep is the one thing here that makes the smear worse on purpose.
#
# So: compare before writing, and send only the rows that differ.
#
# IT FAILS TOWARD WRITING, EVERYWHERE. A fetch that errors, a column the server
# does not return, a value in a shape this cannot normalise — all of them mean
# "changed", so the cost of being wrong is one wasted UPDATE, which is exactly
# what the code did before. The opposite default would be an OSM edit that
# silently never lands, indistinguishable from --only-new, and the entire
# reason --only-new was dropped.
class _Unknown:
    """Never equal to anything, including itself — so a value that could not be
    normalised can only ever compare as CHANGED. `object()` will not do: the
    same instance compares equal to itself, which is the fail-open direction."""
    __slots__ = ()
    def __eq__(self, other): return False
    def __ne__(self, other): return True
    __hash__ = None


_TS_COLS = ("starts_at", "ends_at")


def skip_cols(mine: Dict[str, Any], theirs: Dict[str, Any]):
    """Columns this comparison must NOT look at for this pair of rows.

    THE WINDOW ON A STANDING ROW IS NOT OURS, AND COMPARING IT WOULD HAVE MADE
    THIS WHOLE FILTER A NO-OP ON EXACTLY THE ROWS IT WAS WRITTEN FOR.
    0156's `roll_recurring_windows` rewrites starts_at/ends_at on any row with
    `recurring_hours` whose window has passed — hourly, at :35 — so what is
    STORED is the rolled window and what to_row computes is today's. They differ
    almost always, and every OSM amenity, every imported shop and every
    collapsed OpenActive weekly series is a standing row. Comparing them asks
    "what time is it", not "did OpenStreetMap change".
    Nothing is lost by looking away: if the PATTERN changes, `recurring_hours`
    itself differs and the row is written, window and all; if a row starts or
    stops being standing, that column goes null-to-set or set-to-null and does
    the same. Which is why this is gated on BOTH sides carrying one — a row
    changing shape is a row we must write."""
    return _TS_COLS if (mine.get("recurring_hours") and theirs.get("recurring_hours")) else ()


def _norm_cmp(col: str, v: Any) -> Any:
    """One column's value, in a form the two sides can be compared in."""
    if col in _TS_COLS:
        if v is None:
            return None
        try:
            s = str(v).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            d = datetime.fromisoformat(s)
        except Exception:
            return _Unknown()
        # A NAIVE STAMP IS NOT AN INSTANT, so it cannot be compared to one.
        # Postgres always answers with an offset; if our side has none, the two
        # are not the same kind of thing and the honest answer is "write it".
        return d.astimezone(timezone.utc) if d.tzinfo else _Unknown()
    if isinstance(v, bool) or v is None:
        return v
    if col == "agenda" and isinstance(v, list):
        # Supabase may return equivalent instants with an offset while our
        # canonical writer uses UTC Z. Compare agenda JSON semantically.
        normalized = []
        for item in v:
            if not isinstance(item, dict):
                return _Unknown()
            copy = dict(item)
            for key in ("at", "until"):
                if copy.get(key):
                    try:
                        stamp = str(copy[key]).replace("Z", "+00:00")
                        dt = datetime.fromisoformat(stamp)
                        if dt.tzinfo is None:
                            return _Unknown()
                        copy[key] = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        return _Unknown()
            normalized.append(copy)
        return sorted(normalized, key=lambda item: (item.get("at", ""), item.get("id", "")))
    if isinstance(v, (int, float)):
        return float(v)                      # 47.6 and 47.60000 arrive both ways
    if isinstance(v, (list, dict, str)):
        return v                             # jsonb/text[]/text compare structurally
    return _Unknown()


def unchanged_ids(session, url: str, key: str, rows: List[Dict[str, Any]]):
    """external_ids whose stored row already equals what we are about to write.

    The comparison is driven by OUR row's own keys, never by a hand-kept list:
    add a column to to_row and it is compared from that moment, and a column the
    server does not return reads as changed. A list would be one more pair of
    things that must agree, which is how the 🔎 Google link reached every
    drinking fountain past a check that passed."""
    if not rows:
        return set()
    cols = sorted({c for r in rows for c in r})
    mine = {r["external_id"]: r for r in rows if r.get("external_id")}
    select = ",".join(cols)
    base = url.rstrip("/") + "/rest/v1/events"
    hdr = {"apikey": key, "Authorization": f"Bearer {key}"}
    # WHICH column made each row differ, so a column that differs on EVERY row
    # can be named rather than merely obeyed. See the warning below.
    blamed: Dict[str, int] = {}
    compared = 0
    same = set()
    ids = list(mine)
    i = 0
    while i < len(ids):
        # CHUNKED BY THE LENGTH OF THE URL IT BUILDS, not by a row count. These
        # are sha1 hexdigests today and something else tomorrow; a fixed 100
        # would silently start producing a 414 the day an adapter's fingerprint
        # got longer, and PostgREST answers that with an HTML error page.
        chunk, size = [], 0
        while i < len(ids) and size < 4000:
            chunk.append(ids[i]); size += len(ids[i]) + 4; i += 1
        q = ",".join('"' + c.replace('"', '""') + '"' for c in chunk)
        endpoint = (f"{base}?external_source=eq.mapsee&select={urllib.parse.quote(select)}"
                    f"&external_id=in.({urllib.parse.quote(q)})")
        try:
            r = session.get(endpoint, headers=hdr, timeout=60)
            if r.status_code not in (200, 206):
                continue                     # a refusal means write, not skip
            stored = r.json()
        except Exception:
            continue
        if not isinstance(stored, list):
            continue
        for row in stored:
            eid = row.get("external_id")
            want = mine.get(eid)
            if not want:
                continue
            compared += 1
            diff = [c for c in want if c not in skip_cols(want, row)
                    and (c not in row or _norm_cmp(c, want[c]) != _norm_cmp(c, row[c]))]
            if not diff:
                same.add(eid)
            for c in diff:
                blamed[c] = blamed.get(c, 0) + 1
    # A COLUMN DERIVED FROM THE CLOCK RATHER THAN FROM THE SOURCE UNDOES ALL OF
    # THIS WITH ONE LINE IN ANOTHER FILE, AND THE LOG WOULD READ AS A BUSY WEEK.
    # Add `"last_seen_at": now()` to to_row — the obvious way to make "gone from
    # the feed" detectable, and a thing this repo has already written down as
    # wanting — and every row differs from what is stored on every run, for ever.
    # The filter then skips nothing and reports "all N rows differ", which is
    # exactly what a genuine refresh of a changed catalogue looks like.
    # So: a column that differs on essentially EVERY row is not an edit anybody
    # made, it is a clock, and it is named. Same rule as reading PostgREST's own
    # message out of a PGRST204 instead of guessing which column is missing.
    # ALWAYS NAME THE TOP THREE, not only past 99%. The 2026-09-09 Wednesday
    # refresh rewrote 76,946 of the 221,000 rows it compared, 95% of them from
    # two adapters (OpenActive 46,798 of 63,228; Meetup 16,258 + 10,075), and
    # the warning below stayed silent because no single column reached 99% of
    # a sync — so nothing said WHICH columns made those rows differ.
    # Diagnostics only: what is compared is unchanged.
    if blamed:
        top = sorted(blamed.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        print(f"Skip-unchanged: {compared - len(same)} of {compared} stored rows differ; "
              f"most-blamed columns: " + ", ".join(f"{c} {n}" for c, n in top), flush=True)
    if compared >= 50:
        for col, n in sorted(blamed.items(), key=lambda kv: -kv[1]):
            if n >= compared * 0.99 and col not in _TS_COLS:
                print(f"::warning::skip-unchanged: `{col}` differs on {n} of {compared} rows, "
                      f"so nothing can ever be skipped. A column whose value comes from the "
                      f"CLOCK rather than from the source must not be compared — see "
                      f"unchanged_ids in mapsee_supabase_sync.py.")
                break
    return same


def load_blocklist(session, url: str, key: str):
    """The moderation terms, so we can drop blocked content BEFORE sending it — mirrors
    public.is_clean so those events never hit the slow per-row 'content_blocked' retry."""
    try:
        r = session.get(url.rstrip("/") + "/rest/v1/moderation_terms?select=term",
                        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
        if r.status_code == 200:
            return [row["term"].lower() for row in r.json() if row.get("term")]
    except Exception:
        pass
    return []


def is_clean(text: str, terms) -> bool:
    """Local mirror of public.is_clean: lower-case, common leet swaps, word-boundary match."""
    if not text:
        return True
    norm = text.lower().translate(_LEET)
    return not any(re.search(r"\b" + re.escape(t) + r"\b", norm) for t in terms)


def main() -> None:
    ap = argparse.ArgumentParser(description="Upsert aggregated events into Mapsee's Supabase.")
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--dry-run", action="store_true", help="Print rows that WOULD be upserted; send nothing.")
    ap.add_argument("--only-new", action="store_true",
                    help="Skip events already in Supabase — upsert only new ones (much faster steady-state).")
    ap.add_argument("--skip-unchanged", action="store_true",
                    help="Read each row back first and send only the ones that DIFFER. For an "
                         "adapter that must rewrite (every column derived from an upstream people "
                         "edit) but whose rows rarely change. No-op under --only-new, where every "
                         "row is new by construction.")
    ap.add_argument("--retire-absent", action="store_true",
                    help="Cancel + hide a session a source read WHOLE this run (EventStore."
                         "mark_complete) no longer lists, against --manifest's last complete read. "
                         "Tombstones in the store are applied in every run, with or without this.")
    ap.add_argument("--manifest", default=None,
                    help="The per-source fingerprint manifest --retire-absent compares with and "
                         "rewrites after a successful sync (kept in the Actions cache).")
    a = ap.parse_args()
    if a.retire_absent and not a.manifest:
        raise SystemExit("--retire-absent needs --manifest PATH: absence is measured against the "
                         "last complete read, and without one there is nothing to compare.")

    # Fail LOUDLY on a missing host profile. The old default was the literal
    # placeholder "<MAPSEE_HOST_PROFILE_ID>", which is not a UUID, so a workflow
    # that forgot the secret did all of its fetching and geocoding and then had
    # every insert rejected by Postgres — the error naming a column, not the
    # missing variable. That is exactly what happened to the first OSM takeaway
    # run. --dry-run still works without it, because it never inserts.
    host_id = os.environ.get("MAPSEE_HOST_PROFILE_ID", "").strip()
    if not host_id and not a.dry_run:
        # raise, not return: main() is invoked as a bare `main()` at the bottom
        # of this file, so a returned exit code is discarded and the job goes
        # GREEN having printed an error nobody reads. A guard that does not fail
        # the build is decoration.
        raise SystemExit("MAPSEE_HOST_PROFILE_ID is not set — it is the profile imported "
                         "events are created_by, and without it every insert is rejected.")

    if a.dry_run:
        rows = build_rows(a.store, host_id)           # preview: skip geocoding (no network)
        print(f"Prepared {len(rows)} event rows from {a.store}.")
        for row in rows[:3]:
            print(json.dumps(row, ensure_ascii=False, indent=1))
        inputs = cancellation_inputs(a.store)
        previous = load_manifest(a.manifest) if a.retire_absent else None
        absent, lines, warnings = absent_fingerprints(previous, inputs)
        succ = absent_successors(previous, inputs, absent)
        targets = cancellation_targets(inputs, {fp: u for fp, u in absent.items() if fp not in succ})
        print(f"Cancellations: {len(inputs['tombstones'])} tombstone(s), {len(absent)} absent "
              f"session(s), {len(succ)} of them listed again under a new key (would MOVE, if stored "
              f"and unclaimed) -> {len(targets)} row(s) would be cancelled if present and unclaimed"
              + (f": {_tally(targets, targets)}" if targets else "") + ".")
        for line in lines + warnings:
            print(line)
        print(f"... {len(rows)} rows total. Dry run — nothing sent to Supabase.")
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    if host_id == "<MAPSEE_HOST_PROFILE_ID>":
        sys.exit("Set MAPSEE_HOST_PROFILE_ID to the aggregator's profiles.id (used as created_by).")

    import requests, time                             # batch-geocode venue addresses (TM coords imprecise)
    geo = requests.Session()
    geo.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})

    # Never touch CLAIMED imports (a real user owns them now) — in EITHER mode,
    # so a full refresh can't clobber a claimer's edits and only-new stays correct.
    # ONE read supplies both guards, and it FAILS CLOSED: no write follows it.
    state = None
    detail: Dict[str, Dict[str, Any]] = {}            # STATE_FIELDS per stored row
    daily = set()                                     # stored rows an --only-new run still writes
    now = datetime.now(timezone.utc)

    def read_state(ids):
        t0 = time.monotonic()
        try:
            try:
                got = fetch_import_state(geo, url, key, ids, fields=STATE_FIELDS, detail=detail)
            except StateColumnMissing:
                # A column the database has not got YET costs the daily time
                # changes and un-cancels, never the night (test_sync_unknown_column's
                # rule). Ownership is read again, plainly, and still fails closed.
                print("::warning::Import state: a time/cancellation column is missing from "
                      "events; reading ownership only (no time changes, no un-cancel).", flush=True)
                detail.clear()
                got = fetch_import_state(geo, url, key, ids)
        except RuntimeError as error:
            raise SystemExit(str(error)) from None
        print(f"Import state: {len(got)} of {len(set(ids))} id(s) already in Supabase, "
              f"read in {time.monotonic() - t0:.1f}s.", flush=True)
        return got

    # BEFORE ANYTHING READS EXISTENCE: a row still filed under a legacy
    # fingerprint moves to its new one first, or --only-new would call the clean
    # record new and insert it beside the garbled row. See rekey_legacy.
    moved, held = rekey_legacy(geo, url, key, legacy_pairs(a.store))
    # Absence is read off the store and the manifest, so it is known now; a gone
    # session that is only renamed or re-timed moves to its new key here, before
    # the upsert would insert it beside the old row (see absent_successors).
    inputs = cancellation_inputs(a.store)
    previous = load_manifest(a.manifest) if a.manifest else None
    absent, absent_lines, absent_warnings = (absent_fingerprints(previous, inputs, now)
                                             if a.retire_absent else ({}, [], []))
    try:
        moved_on, gone_moved = rekey_absent(geo, url, key, absent_successors(previous, inputs, absent))
    except Exception as error:                        # noqa: BLE001 - it runs BEFORE the upsert
        print(f"::warning::Absence re-key failed ({type(error).__name__}); gone sessions are "
              f"treated as gone, as before.", flush=True)
        moved_on, gone_moved = set(), set()
    moved |= moved_on
    absent = {fp: unit for fp, unit in absent.items() if fp not in gone_moved}

    def keep_new(recs):
        # --only-new: read the state on the STORE's fingerprints (to_row's
        # external_id) and drop what exists BEFORE build_rows enriches,
        # geocodes and builds it. See build_rows for the 2026-09-12 numbers.
        # A STORED row is kept too when a daily run has something to say about
        # it: an exact-detail refresh (needs_detail_sync), a TIME CHANGE (same
        # fingerprint, new starts_at/ends_at: a class moved from 10:00 to 11:00
        # used to wait for Wednesday), or a row we CANCELLED that is listed live
        # again. All three are read off the state above, so this adds no request.
        nonlocal state
        state = read_state([r["fingerprint"] for r in recs])
        fresh, n_time, n_back = [], 0, 0
        for r in recs:
            fp = r["fingerprint"]
            if needs_detail_sync(r, state, moved, held):
                fresh.append(r)
            elif fp in held or state.get(fp, True):    # claimed, held, or not stored: nothing to add
                continue
            elif import_cancelled(detail.get(fp), now):
                fresh.append(r)
                n_back += 1
            elif time_changed(r, detail.get(fp)):
                fresh.append(r)
                n_time += 1
        daily.update(r["fingerprint"] for r in fresh if r["fingerprint"] in state)
        n_claimed = sum(1 for is_claimed in state.values() if is_claimed)
        print(f"Only-new: {len(fresh)} of {len(recs)} need sync ({len(state)} from this batch "
              f"already in Supabase, {n_claimed} of them claimed; {n_time} stored row(s) with a "
              f"new time and {n_back} cancelled row(s) listed again are kept), dropped before "
              f"geocoding.", flush=True)
        return fresh

    rows = build_rows(a.store, host_id, geo, keep=keep_new if a.only_new else None)
    if held:
        rows = [r for r in rows if r["external_id"] not in held]
    print(f"Prepared {len(rows)} event rows from {a.store}.")
    if state is None:                                 # refresh day: build first, then read, as before
        state = read_state([r["external_id"] for r in rows])

    claimed = {eid for eid, is_claimed in state.items() if is_claimed}
    if claimed:
        before = len(rows)
        rows = [r for r in rows if r["external_id"] not in claimed]
        if before != len(rows):
            print(f"Claimed-guard: skipped {before - len(rows)} claimed events.")

    if a.only_new:                                    # skip events already in the DB
        # A moved row still has to be rewritten, and so does every stored row
        # keep_new kept on purpose: this line used to drop those again, so the
        # daily exact-detail refresh never reached the table.
        existing = set(state) - moved - daily
        before = len(rows)
        rows = [r for r in rows if r["external_id"] not in existing]
        if before != len(rows):                       # already done in keep_new, normally
            print(f"Only-new: {len(rows)} of {before} are new ({len(existing)} from this batch already in Supabase).")

    terms = load_blocklist(geo, url, key) if rows else []  # no DB read for an empty write set
    if terms:
        before = len(rows)
        rows = [r for r in rows if all(is_clean(r.get(f) or "", terms)
                                       for f in ("title", "description", "place_name", "host_name"))]
        print(f"Moderation pre-filter: dropped {before - len(rows)} of {before} rows.")

    # Before skip-unchanged, which drops an identical row from the WRITE but not
    # from the fact that the source lists it live today.
    relist = sorted(r["external_id"] for r in rows if import_cancelled(detail.get(r["external_id"]), now))

    # LAST, so nothing is read back for a row the filters above already dropped.
    if a.skip_unchanged and rows and should_compare_unchanged(a.only_new, rows, state):
        same = unchanged_ids(geo, url, key, rows)
        if same:
            before = len(rows)
            rows = [r for r in rows if r["external_id"] not in same]
            print(f"Skip-unchanged: {len(rows)} of {before} differ from what is stored "
                  f"({len(same)} identical, not rewritten).")
        else:
            print(f"Skip-unchanged: all {len(rows)} rows differ from what is stored "
                  f"(or could not be read back — a refusal means write).")

    t0 = time.monotonic()
    n, skipped, lost = upsert(rows, url, key)
    print(f"Upsert phase: {len(rows)} row(s) in {time.monotonic() - t0:.1f}s.", flush=True)
    # NOT NECESSARILY MODERATION. This used to assert the reason regardless of
    # the status code, and it sent me looking for a content filter that was
    # never involved: Tokyo's entire batch — all 114 rows — failed with
    # "503 upstream connect error" and was reported as blocked content, while a
    # handful elsewhere were 400s for an out-of-range timestamp. The per-row
    # reasons are printed above; point at those instead of inventing one.
    tail = f"; skipped {skipped} (see the per-row reasons above)" if skipped else ""
    tail += f"; LOST {lost} to an unanswering database" if lost else ""
    print(f"Upserted {n} events into Supabase as host {host_id}{tail}. "
          f"They will now appear in events_near / the Nearby map.")

    # AFTER the upsert, so a failure here never costs the night's new rows, and
    # the rows a cancellation lifts are already current when they reappear.
    owed = 0
    if a.retire_absent and previous is None:
        print(f"Absence: no manifest at {a.manifest} (first run or an evicted cache); "
              f"nothing is cancelled for absence this run.", flush=True)
    for line in absent_lines + absent_warnings:
        print(line, flush=True)
    targets = cancellation_targets(inputs, absent)
    owned = {eid: u for eid, u in owned_tombstones(inputs).items() if eid in targets}
    if owned:
        mine, unread = own_rows(geo, url, key, owned)
        owed += len(unread)
        targets = {eid: why for eid, why in targets.items() if eid not in owned or eid in mine}
        print(f"Own-only cancellations: {len(mine)} of {len(owned)} row(s) link to the listing its "
              f"platform called off; the rest are another source's row, or not stored"
              + (f"; {len(unread)} OWED (read failed)" if unread else "") + ".", flush=True)
    if targets:
        new, refreshed, failed = apply_cancellations(geo, url, key, targets, now)
        owed += len(failed)
        print(f"Cancelled + hid {len(new)} of {len(targets)} row(s) the source called off or "
              f"stopped listing" + (f" ({_tally(new, targets)})" if new else "")
              + f"; the rest are already cancelled, not in the table, claimed, or hidden for "
              f"another reason"
              + (f"; {len(failed)} OWED (request failed)" if failed else "") + ".", flush=True)
    # ONLY A SOURCE READ WHOLE THIS RUN MAY LIFT, and only a row it wrote live:
    # a session listed again, a centre reopened. That includes a read marked
    # whole for lifting only (mark_complete(absence=False): OpenActive, whose
    # weekly rows come back under the same key when a closure ends, and which
    # could otherwise never lift one). Two sources can share a
    # fingerprint (a venue's feed and a platform's copy); if one says cancelled
    # and another, read in passing, still lists it, the cancellation stands -
    # the owner's line is that nobody is sent to an event that is off. A row
    # tombstoned in this very store is never lifted by it either.
    complete_live = set().union(*inputs["units"].values()) if inputs["units"] else set()
    held_back = [eid for eid in relist if eid not in complete_live or eid in inputs["tombstones"]]
    relist = [eid for eid in relist if eid in complete_live and eid not in inputs["tombstones"]]
    if held_back:
        print(f"Un-cancel: {len(held_back)} cancelled row(s) listed live by a source not read whole "
              f"this run are left cancelled (only a complete read lifts).", flush=True)
    if relist:
        lifted, failed = lift_cancellations(geo, url, key, relist, now, detail)
        owed += len(failed)
        print(f"Un-cancelled {len(lifted)} of {len(relist)} row(s) listed live again by a source "
              f"read whole" + (f"; {len(failed)} OWED (request failed)" if failed else "") + ".",
              flush=True)
    if a.manifest and inputs["complete"] and not lost and not owed:
        kept = write_manifest(a.manifest, previous, inputs)
        print(f"Absence manifest: {kept} complete read(s) recorded in {a.manifest}.", flush=True)
    elif a.manifest and inputs["complete"]:
        print(f"Absence manifest NOT updated ({lost} row(s) lost, {owed} cancellation write(s) "
              f"owed): the last good baseline stays.", flush=True)
    if owed:
        # A WARNING, NOT A FAILURE. Every owed write is derived again next run (a
        # tombstone is the publisher's word re-read, absence compares with the
        # baseline kept above, a relist is listed again), and an exit here
        # skipped the steps after it: in the Meetup and Ticketmaster jobs the
        # US-leg sync has no continue-on-error, so one 503 cost the day's
        # international sweep (review, 2026-10-06).
        print(f"::warning::{owed} cancellation write(s) never reached Supabase (after "
              f"{PATCH_TRIES} tries); the next run derives them again.", flush=True)
    # Same contract mapsee_indexnow settled on: a run that reported what it
    # could and a run that quietly wrote everything must not look the same. The
    # rows that DID land are already committed above — this only sets the exit
    # code, so the red tick means "some rows are still owed", not "nothing
    # happened". Skipped rows do NOT fail the run: Postgres rejecting a row is a
    # verdict, and re-running will not change it.
    if lost:
        sys.exit(f"{lost} row(s) never reached Supabase — unwritten, not rejected.")


if __name__ == "__main__":
    main()
