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
import html
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from mapsee_music_links import spotify_search_url, youtube_search_url, bandcamp_search_url, SpotifyResolver

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
    r"days?\s+of\s+service)\b", re.I)
_PROMOTABLE_TO_VOLUNTEER = {"community", "learning", "outdoors", "other"}


# Comedy / standup / film / theater booked at a MUSIC venue inherit that venue
# feed's hardcoded "music" category. High-precision title signals move them onto
# the 'theater' layer. Kept tight (e.g. "comedy night/show", not a bare "comedy"
# that could be a band name) so a real concert is never miscast, and only from
# music/other — a strong existing category (sports, food…) is left alone.
_THEATER_RX = re.compile(
    r"\b(stand[\s-]?up|comedy\s+(?:night|show|hour|special|showcase)|open\s+mic\s+comedy|"
    r"improv|sketch\s+comedy|burlesque|drag\s+(?:show|brunch|bingo|race)|"
    r"film\s+screening|movie\s+night|screening\s+of|\bplay(?:\s+reading)?\b|"
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


# KIDS. Measured 2026-07-27: exactly ONE 'kids' event existed across six major
# metros, because only Ticketmaster's rare "family" segment mapped to it - while
# storytimes, family days and children's workshops sat in community/learning.
# Promoted out of generic buckets so the Kids filter reflects what is actually
# happening for families.
_KIDS_RX = re.compile(
    r"\b(story\s?time|family\s+(?:day|fun|friendly|workshop|concert)|"
    r"kids?\s+(?:club|craft|workshop|class|hour|day|camp|activit)|"
    r"children'?s?\s+(?:workshop|hour|program|craft|story|activit)|"
    r"toddler|preschool|pre[\s-]?k\b|baby\s+(?:time|rhyme|song)|rhyme\s?time|"
    r"puppet\s+show|petting\s+zoo|face\s+painting|egg\s+hunt|"
    r"lego\s+(?:club|build)|youth\s+(?:program|workshop|club))\b", re.I)
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
    ("market", re.compile(
        r"\b(flea\s+market|farmers?\s+market|night\s+market|craft\s+fair|"
        r"makers?\s+market|artisan\s+market|vintage\s+(?:market|fair|sale|pop[\s-]?up)|"
        r"swap\s+meet|clothing\s+swap|rummage\s+sale|estate\s+sale|holiday\s+market|"
        r"bazaar|street\s+fair|pop[\s-]?up\s+shop|craft\s+market|record\s+fair)\b", re.I)),
    ("food", re.compile(
        r"\b(food\s+trucks?|food\s+hall|food\s+vendors?|supper\s+club|tasting\s+menu|"
        r"pop[\s-]?up\s+(?:dinner|kitchen)|beer\s+garden|brewery|winery|cidery|"
        r"wine\s+tasting|beer\s+(?:tasting|festival)|bbq|barbecue|"
        r"chili\s+cook[\s-]?off|bake\s+sale|farm\s+dinner|potluck)\b", re.I)),
    ("music", re.compile(
        r"\b(live\s+music|live\s+bands?|dj\s+set|open\s+mic|acoustic\s+set|"
        r"concert\s+series|jazz\s+night|drum\s+circle)\b", re.I)),
    ("outdoors", re.compile(
        r"\b(hik(?:e|ing)|trail\s+(?:run|walk|day)|nature\s+walk|guided\s+walk|"
        r"bird\s?watching|kayak|canoe|paddle\s?board|camp(?:ing|out)|"
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
        r"kayak|canoe|paddle\s?board|paddling|"
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
        r"book\s+club|author\s+talk|guest\s+speaker|bootcamp|intro\s+to\s+)\b", re.I)),
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


def derive_categories(rec: Dict[str, Any]) -> Tuple[str, Optional[List[str]]]:
    """(primary, secondaries) for one record. Secondaries is None, never [], so
    the column stays NULL — 0108's CHECK rejects an empty array."""
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
    if base != primary and base != DEFAULT_CATEGORY_KEY \
       and not (primary == "fitness" and _strong_fitness_override(rec, base)):
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
    for key, rx in _SECONDARY_RX:
        if len(extras) >= MAX_EXTRA_CATEGORIES:
            break
        if key == "fitness" and primary in _NO_FITNESS_SECONDARY_FROM:
            continue
        if rx.search(text):
            add(key)

    return primary, (extras[:MAX_EXTRA_CATEGORIES] or None)


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


def map_category(rec: Dict[str, Any]) -> str:
    """Map the captured category to a Mapsee frontend category KEY (site/js/app.js).
    Accepts a Ticketmaster segment / schema.org @type, OR an already-valid Mapsee
    key (e.g. from an open-data source config). Clearly-volunteer events sitting in a
    generic bucket are promoted to the 'volunteer' layer by keyword.

    Still the PRIMARY-only answer: the pin colour, the emoji and _compute_end all
    need exactly one key. derive_categories() wraps this for the full set."""
    raw = (rec.get("category") or "").strip().lower()
    key = raw if raw in MAPSEE_CATEGORY_KEYS else CATEGORY_KEYS.get(raw, DEFAULT_CATEGORY_KEY)
    if key in _PROMOTABLE_TO_VOLUNTEER:
        # URL-stripped like every other description read: a Meetup group slug
        # such as …-volunteer-cleanup-group- is the name of a GROUP, not a
        # statement that this event is a volunteering shift.
        if _VOLUNTEER_RX.search(_strip_urls(f"{rec.get('name') or ''} {rec.get('description') or ''}")):
            return "volunteer"
    if key in _PROMOTABLE_TO_THEATER and _THEATER_RX.search(rec.get("name") or ""):
        return "theater"           # comedy/standup/film at a music venue -> stage layer
    if key in _PROMOTABLE_TO_KIDS and _KIDS_RX.search(rec.get("name") or ""):
        return "kids"              # storytime/family day hiding in community/learning
    if key in _PROMOTABLE_TO_PARTY and _PARTY_RX.search(rec.get("name") or ""):
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


def _cap_prose(text: Optional[str]) -> Optional[str]:
    """Trim over-long SOURCE prose to DESCRIPTION_MAX, on a natural boundary."""
    if not text or len(text) <= DESCRIPTION_MAX:
        return text
    head = text[:DESCRIPTION_MAX]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut < DESCRIPTION_MAX * 0.6:          # no sentence break near the end
        cut = head.rfind(" ")
    if cut <= 0:                             # one enormous unbroken token
        cut = DESCRIPTION_MAX
    return head[:cut].rstrip(" ,;:.!?-—") + "…"


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
    # Pin color by provenance, so imported listings read differently on the
    # map: TEAL = city open data, VIOLET = big-venue feeds (Ticketmaster).
    # Community-created events keep the app default blue (#2563eb).
    _src = (rec.get("source")
            or ((rec.get("sources") or [{}])[0].get("source")) or "")
    color = "#0891b2" if str(_src).startswith(("opendata:", "ics:", "program:")) else "#7c3aed"
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
    if not str(_src).startswith(("opendata:", "venue:", "ics:", "program:")):
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
    return {
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
        "icon": None,                                # let the app render the category's emoji
        "external_source": "mapsee",                 # provenance (migration 0039)
        "external_id": rec["fingerprint"],           # cross-source dedup key -> idempotent
    }


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


def build_rows(store_path: str, host_id: str, geo_session=None) -> List[Dict[str, Any]]:
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

    if geo_session is not None:                       # production run (has network)
        _enrich_music_links(recs, geo_session)        # exact Spotify pages when a key is set
        # Batch-geocode the unique venue addresses ONCE and write the result back
        # into the rec BEFORE to_row. This both (a) supplies coordinates for events
        # that arrived without any (deferred-geocode adapters) and (b) overrides
        # imprecise source coords (Ticketmaster is often ~0.5mi off). Doing it
        # pre-to_row means the map pin AND the naive-local-time→UTC conversion
        # (which needs coords to know the timezone) both use the best coordinates.
        parts_of = {i: p for i, p in ((i, _addr_parts(r)) for i, r in enumerate(recs)) if p}
        coords = batch_geocode(geo_session, list({p for p in parts_of.values()}))
        applied = 0
        for i, p in parts_of.items():
            c = coords.get(p)
            if c:
                recs[i]["latitude"], recs[i]["longitude"] = c[0], c[1]
                applied += 1
        print(f"Geocoded {applied}/{len(recs)} rows ({len(coords)} unique addresses matched).")

    rows, dropped = [], 0
    for rec in recs:
        if rec.get("latitude") is None or rec.get("longitude") is None:
            dropped += 1                              # coordless AND the geocoder couldn't place it
            continue
        rows.append(to_row(rec, host_id))
    if dropped:
        print(f"Dropped {dropped} coordless events the geocoder couldn't place (address missing/unmatched).")
    return rows


def upsert(rows: List[Dict[str, Any]], url: str, key: str) -> int:
    import requests, time
    endpoint = url.rstrip("/") + "/rest/v1/events?on_conflict=external_source,external_id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    def _post(batch):
        return requests.post(endpoint, headers=headers, data=json.dumps(batch), timeout=30)

    def _post_retry(batch, tries=3):
        # A batch can fail transiently on a statement timeout (57014) or lock wait,
        # esp. if a moderation trigger is slow. Back off and retry the whole batch
        # before falling back to costly row-by-row isolation.
        retryable = {408, 429, 500, 502, 503, 504}   # 57014 surfaces as HTTP 500
        for n in range(tries):
            resp = _post(batch)
            if resp.status_code < 300 or resp.status_code not in retryable:
                return resp
            time.sleep(0.5 * (n + 1))
        return resp

    sent = skipped = 0
    for i in range(0, len(rows), 50):               # fast path: batch upserts
        chunk = rows[i:i + 50]
        resp = _post_retry(chunk)
        if resp.status_code < 300:
            sent += len(chunk)
            continue
        # A whole-batch rejection is usually one bad row (e.g. the moderation
        # trigger raising 'content_blocked'). Re-send the batch row-by-row so the
        # clean events still land and only the offending ones are skipped + logged.
        for row in chunk:
            r = _post([row])
            if r.status_code < 300:
                sent += 1
            else:
                skipped += 1
                try:
                    reason = r.json().get("message", "")
                except Exception:
                    reason = r.text[:80]
                print(f"  skipped [{r.status_code} {reason}] {row.get('title')!r}")
    return sent, skipped


_LEET = str.maketrans("@0135$!7", "aoiessit")


def fetch_existing_ids(session, url: str, key: str):
    """Set of external_ids already in Supabase (external_source='mapsee'), paged."""
    ids = set()
    endpoint = url.rstrip("/") + "/rest/v1/events?external_source=eq.mapsee&select=external_id"
    base = {"apikey": key, "Authorization": f"Bearer {key}", "Range-Unit": "items"}
    start, size = 0, 10000
    while True:
        try:
            r = session.get(endpoint, headers=dict(base, Range=f"{start}-{start + size - 1}"), timeout=60)
        except Exception:
            break
        if r.status_code not in (200, 206):
            break
        rows = r.json()
        if not rows:
            break
        for row in rows:
            if row.get("external_id"):
                ids.add(row["external_id"])
        if len(rows) < size:
            break
        start += size
    return ids


def fetch_claimed_ids(session, url: str, key: str):
    """external_ids of imported events a real user has CLAIMED (0043). We must
    never touch those on sync — the claimer owns the row now, and re-upserting
    would clobber their edits. Excluding them (not re-inserting) is safe: the row
    already exists, so no duplicate appears."""
    ids = set()
    endpoint = (url.rstrip("/")
                + "/rest/v1/events?external_source=eq.mapsee&claimed_at=not.is.null&select=external_id")
    base = {"apikey": key, "Authorization": f"Bearer {key}", "Range-Unit": "items"}
    start, size = 0, 10000
    while True:
        try:
            r = session.get(endpoint, headers=dict(base, Range=f"{start}-{start + size - 1}"), timeout=60)
        except Exception:
            break
        if r.status_code not in (200, 206):
            break                                        # column missing (pre-0043) → nothing to skip
        rows = r.json()
        if not rows:
            break
        for row in rows:
            if row.get("external_id"):
                ids.add(row["external_id"])
        if len(rows) < size:
            break
        start += size
    return ids


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
    a = ap.parse_args()

    host_id = os.environ.get("MAPSEE_HOST_PROFILE_ID", "<MAPSEE_HOST_PROFILE_ID>")

    if a.dry_run:
        rows = build_rows(a.store, host_id)           # preview: skip geocoding (no network)
        print(f"Prepared {len(rows)} event rows from {a.store}.")
        for row in rows[:3]:
            print(json.dumps(row, ensure_ascii=False, indent=1))
        print(f"... {len(rows)} rows total. Dry run — nothing sent to Supabase.")
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    if host_id == "<MAPSEE_HOST_PROFILE_ID>":
        sys.exit("Set MAPSEE_HOST_PROFILE_ID to the aggregator's profiles.id (used as created_by).")

    import requests                                   # batch-geocode venue addresses (TM coords imprecise)
    geo = requests.Session()
    geo.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    rows = build_rows(a.store, host_id, geo)
    print(f"Prepared {len(rows)} event rows from {a.store}.")

    # Never touch CLAIMED imports (a real user owns them now) — in EITHER mode,
    # so a full refresh can't clobber a claimer's edits and only-new stays correct.
    claimed = fetch_claimed_ids(geo, url, key)
    if claimed:
        before = len(rows)
        rows = [r for r in rows if r["external_id"] not in claimed]
        print(f"Claimed-guard: skipped {before - len(rows)} claimed events.")

    if a.only_new:                                    # skip events already in the DB
        existing = fetch_existing_ids(geo, url, key)
        before = len(rows)
        rows = [r for r in rows if r["external_id"] not in existing]
        print(f"Only-new: {len(rows)} of {before} are new ({len(existing)} already in Supabase).")

    terms = load_blocklist(geo, url, key)             # drop blocked content before the slow retry
    if terms:
        before = len(rows)
        rows = [r for r in rows if all(is_clean(r.get(f) or "", terms)
                                       for f in ("title", "description", "place_name", "host_name"))]
        print(f"Moderation pre-filter: dropped {before - len(rows)} of {before} rows.")

    n, skipped = upsert(rows, url, key)
    tail = f"; skipped {skipped} (blocked by your moderation filter — see log above)" if skipped else ""
    print(f"Upserted {n} events into Supabase as host {host_id}{tail}. "
          f"They will now appear in events_near / the Nearby map.")


if __name__ == "__main__":
    main()
