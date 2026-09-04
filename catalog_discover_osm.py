#!/usr/bin/env python3
"""
catalog_discover_osm.py — find venue CALENDARS anywhere on earth, from OSM.

The other discovery backends read CATALOGS: Socrata and CKAN list open-data
datasets, joinmobilizon lists federated instances. That finds civic and
open-data feeds, and it structurally cannot find the long tail — a gallery, a
zendo, a bookshop with a reading series, a brewery with a Tuesday quiz. Nobody
publishes those to a data portal, so no catalog query will ever return them,
however the query is worded.

They are, however, ON THE MAP. OpenStreetMap tags the places that PROGRAMME
things — theatre, arts_centre, community_centre, library, nightclub, museum,
place_of_worship, sports_centre — and a good share of them carry a `website`.
That makes the candidate list a geographic query rather than a text search, and
it works identically in Lisbon, Osaka and Seattle, which is the whole point.

Measured before this was written, against the 1,181 hand-curated Seattle sources
another aggregator publishes (uncouchme.com/about):

    1,656 programme-venues in the Seattle bbox, 599 carrying a website
    98 of those hosts are ALSO on that hand-built list — the method finds the
       same real venues a human found
    387 more are not on it at all, and 114 of those 387 (29%) turned out to
       have a calendar on a platform this repo already ingests

So it is not a way of copying somebody's list; it is a way of generating one,
and the overlap is what says the generated list is about the right places.

WHAT IT CANNOT FIND, and this is worth knowing before trusting it: a Meetup
group, an Eventbrite organiser, a blog, a newspaper's listings column, or any
venue whose OSM entry has no `website`. Those were 638 of the 745 hosts on the
hand-built list. Discovery here is a COMPLEMENT to that kind of curation, not a
replacement for it, and a metro swept clean by this is not a metro finished.

THE PIPELINE
    metro bbox -> Overpass -> venues with a website
               -> fetch the site, find the page that is its calendar
               -> fingerprint the platform  (Squarespace? The Events Calendar?)
               -> emit a candidate typed for THAT adapter's config file
    catalog_curate.py verify then has to prove it returns future events, and
    only then will merge write it into a *_sources.json. Nothing here adds a
    source; it only proposes one.

WHY THE COORDINATES MATTER MORE THAN THEY LOOK
Overpass hands back the venue's surveyed point and its addr:* tags, and a
single-venue calendar needs exactly that: The Royal Room's every event carries
the placeholder location "-", so its config's `venue` block is the only thing
that places it — and, through the coordinates, the only thing that turns its
naive local timestamps into real instants. The discovery source and the missing
half of the config are the same query. That is why a candidate from here ships
a venue block already filled in.

TWO REFUSALS, both deliberate:
  • A BOT CHALLENGE IS A NO. SiteGround answers 202 with an `sgcaptcha` body and
    Cloudflare answers 403 with "Just a moment"; both are somebody deliberately
    turning bot management on, and getting past them means impersonating a
    browser. Recorded as declined, never retried with a browser UA. The live
    example is dmhsus.org, which challenges even /robots.txt.
  • AN OFF-HOST CALENDAR IS NOT THIS BACKEND'S FIND. Plenty of venues keep their
    events on Eventbrite or Meetup. Those need an organiser/group id and belong
    to those adapters; emitting the venue's own domain for them would produce a
    source that ingests nothing. They are counted and reported, not proposed.
"""
from __future__ import annotations

import json
import os
import datetime as _dt
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from html import unescape
from urllib.parse import urljoin, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_BACKOFF_S = 5

# Places that put on a programme. Deliberately narrow: a restaurant that serves
# dinner every night is not a calendar, and sweeping every shop would bury the
# real finds under thousands of sites with nothing to import.
OSM_SELECTORS = [
    'nwr["amenity"~"^(theatre|arts_centre|community_centre|library|nightclub|'
    'cinema|social_facility|events_venue|conference_centre|music_venue)$"]',
    'nwr["tourism"~"^(museum|gallery|zoo|aquarium)$"]',
    'nwr["leisure"~"^(sports_centre|dance|garden)$"]',
    'nwr["club"]',
    'nwr["amenity"="place_of_worship"]',
    'nwr["shop"="books"]',
    'nwr["craft"="brewery"]',
    'nwr["office"="ngo"]',
]

# What kind of place it is -> the config `category` a candidate is proposed with.
# A config category is a DEFAULT the classifier then refines, so the rule is the
# one CLAUDE.md draws from the cycling clubs: a PURE calendar may state its
# specific key, a MIXED one must state `community` and let the promotion rules
# sort it. A community centre or a church hall runs volunteering, a toddler
# group and a concert in the same week, so those stay `community` — from there
# the volunteer and kids rules can rescue what belongs to them, and nothing is
# claimed that was not earned.
KIND_CATEGORY = {
    "theatre": "theater", "cinema": "theater",
    "nightclub": "music", "music_venue": "music",
    "arts_centre": "arts", "museum": "arts", "gallery": "arts",
    "library": "learning", "books": "learning",
    "sports_centre": "fitness", "dance": "fitness",
    "brewery": "food",
    "garden": "outdoors", "zoo": "outdoors", "aquarium": "outdoors",
    "community_centre": "community", "social_facility": "community",
    "place_of_worship": "community", "events_venue": "community",
    "conference_centre": "community", "club": "community",
    "ngo": "community",
}
DEFAULT_CATEGORY = "community"

# href or link text that means "the calendar is through here". Multilingual on
# purpose: the backend sweeps 28 countries and an English-only matcher would
# quietly make it a US/UK tool wearing a global name.
CAL_LINK_RX = re.compile(
    r"(events?|calendar|kalender|calendrier|calendario|agenda|programm|programme|"
    r"programa|whats[-_ ]?on|shows?|gigs?|concerts?|veranstaltung|evenement|"
    r"evenimente|aktiviteter|tapahtumat|arrangement)", re.I)

# A challenge does not answer 403. SiteGround answers 202, and the WAF in front
# of theblackaltar.org answers a clean 200 with a spinner that says "One moment,
# please..." — on `/wp-json/.../events?from=…`, while the SAME endpoint without a
# query string returns real JSON. A 200 is the dangerous case: the body is HTML
# where JSON was expected, so a parser reads it as a broken feed, and an
# HTML-tolerant one reads it as a calendar with nothing on. Neither is true, and
# both are silent. Match the words these pages actually show.
CHALLENGE_RX = re.compile(
    r"sgcaptcha|just a moment|one moment,?\s*please|being verified|"
    r"cf-browser-verification|challenge-platform|captcha-delivery|_Incapsula_|"
    r"/cdn-cgi/challenge|checking your browser|enable javascript and cookies",
    re.I)

# Hosts whose events belong to a DIFFERENT adapter that needs its own id.
#
# `offsite:<host>` is not a failure, it is a ROUTING SIGNAL: the venue has a
# calendar and it is somebody else's. Counted over the ledger on 2026-08-30, 121
# of 6,624 probes ended here — eventbrite 33, facebook 27, humanitix 20,
# instagram 20, tickettailor 6, trybooking 6, universe 3 — which is the only
# measurement this repo has of what venues WORLDWIDE actually use, so it is the
# ranked list of adapters worth writing. Two of them are structural dead ends
# (facebook and instagram: no API we may read), and one was assessed properly:
#
# HUMANITIX — ASSESSED 2026-08-30 AND NOT INGESTABLE FROM THE PUBLIC SURFACE,
# which is worth writing down because everything ABOUT it says it should be.
# It is the not-for-profit ticketer, its licence is a clean yes where most are
# not (robots.txt gives `User-agent: * / Allow: /` with Content-Signal
# `search=yes, ai-train=no, use=reference` — indexing and referencing permitted,
# training refused, which is not what we do; the named AI crawlers are
# Disallowed and we are not one), and both its listing and its event pages carry
# well-formed schema.org Event with real offset-bearing instants, a structured
# PostalAddress and an offers block that says which events are FREE.
# Three things stop it, and only together:
#   * NO COORDINATES ANYWHERE. Not in the JSON-LD, not in `__NEXT_DATA__` — the
#     only `latLng` on a place page is the CITY being browsed, which is a
#     centroid and precisely the pin `_addr_parts` refuses to make. The only
#     geocoder here is US Census, so every AU/NZ/GB row would ingest and place
#     nothing: "kept 43 events" for Calgary Buddhist Temple, at platform scale.
#   * THE LISTING IS FOUR EVENTS. A place page server-renders its featured
#     carousel only and loads the rest client-side, so the crawl is one request
#     per place for four events, heavily duplicated across neighbouring places,
#     over a sitemap of 35,035 place pages in its first shard alone.
#   * THE API IS ORGANISER-SCOPED. api.humanitix.com answers 403 without a key,
#     and the key an organiser holds covers that organiser's own events.
# So the blocker is OURS as much as theirs, and it is the honest half to state:
# a non-US geocoder would make the address they already publish enough. Until
# there is one, or a feed we can ask them for, this is a decline and not a
# to-do. Re-check if either changes.
OFFSITE_HOSTS = (
    "eventbrite.", "meetup.com", "facebook.com", "fb.me", "instagram.com",
    "linktr.ee", "ticketmaster.", "axs.com", "seatgeek.com", "dice.fm",
    "lu.ma", "songkick.com", "bandsintown.com", "tickettailor.com",
    "universe.com", "showpass.com", "humanitix.com", "trybooking.com",
)

# A website tag that is not a website we can read.
BAD_SITE_RX = re.compile(r"\.(pdf|jpe?g|png|doc x?|zip)$|^mailto:|^tel:", re.I)


# ---- the platform fingerprint ------------------------------------------------
# Each entry: (label, regex over the page source, the adapter that reads it).
# The label is what the ledger and the report show; the adapter decides which
# *_sources.json a verified candidate is merged into.
PLATFORM_SIGNS = [
    ("tribe",            re.compile(r"/wp-content/plugins/the-events-calendar|tribe-events|tribe_events", re.I), "tribe"),
    ("wp-event-manager", re.compile(r"/wp-content/plugins/wp-event-manager", re.I), "jsonld"),
    ("mylisting",        re.compile(r"/themes/my-listing|CASE27", re.I), "mylisting"),
    ("squarespace",      re.compile(r"static1\.squarespace\.com|squarespace\.com/universal|Squarespace\.afterBodyLoad", re.I), "squarespace"),
    ("wix",              re.compile(r"wixstatic\.com|static\.parastorage\.com", re.I), "jsonld"),
    ("localist",         re.compile(r"localist\.com|/api/2/events", re.I), "localist"),
    ("trumba",           re.compile(r"trumba\.com", re.I), "ics"),
    ("libcal",           re.compile(r"libcal\.com", re.I), "ics"),
    ("gancio",           re.compile(r"gancio", re.I), "gancio"),
    ("venuepilot",       re.compile(r"venuepilot", re.I), "venuepilot"),
    # Events Manager does NOT emit schema.org Event blocks — the Bongo Club's
    # page carries WebPage and WebSite and nothing else — so routing it to the
    # JSON-LD adapter proposed a source that could never read it. What it does
    # have is an iCal export on any calendar page.
    ("events-manager",   re.compile(r"/wp-content/plugins/events-manager", re.I), "ics"),
    ("modern-events",    re.compile(r"modern-events-calendar|mec-event", re.I), "jsonld"),
    # My Calendar (100k+ WordPress installs, and the plugin small arts orgs and
    # congregations actually reach for). It publishes iCal at a FIXED path and
    # never links to it from the calendar page, so scraping for an .ics href
    # finds nothing and a perfectly readable site looks unreadable. A platform
    # can imply a feed URL — see FEED_TEMPLATES.
    ("my-calendar",      re.compile(r"/wp-content/plugins/my-calendar|mc-navigation-button|mc-events-link", re.I), "ics"),
    # CivicPlus, which is how a large share of US municipalities publish anything
    # at all. Measured on issaquahwa.gov: find_calendar landed on /calendar.aspx
    # and returned `no-calendar`, because the page links no .ics and matched no
    # sign here — a city hall with eleven readable iCal feeds read as a city hall
    # with none. The signs are the vendor's own footer credit and its module
    # paths; `iCalendar.aspx` alone would be too thin, since the string is a
    # generic enough filename to appear elsewhere.
    ("civicplus",        re.compile(r"civicplus|/[Cc]ommon/[Mm]odules/Calendar|/iCalendar\.aspx", re.I), "ics"),
]

# DETECTING A SITE BUILDER IS NOT DETECTING A CALENDAR, and conflating the two
# is where this backend wastes most of its verification budget. tribe,
# my-calendar and wp-event-manager are calendar PLUGINS: finding one means the
# site has an events system, and a feed follows. Squarespace and Wix are how the
# whole site is BUILT — every page of them matches, including a hand-written
# "What's On" with nothing behind it. Measured on the first London sweep: 4 of
# the 5 candidates that failed verification with "no schema.org Event blocks"
# were Wix sites detected this way, and White Bear Theatre's turned out not to
# use Wix Events at all.
#
# So a builder has to show its EVENTS app before it may be proposed. Squarespace
# names the collection in the body class and stamps every rendered item; Wix
# routes its events through /event-info/. Volunteer Park Trust (a real events
# collection) matches 2 and 448 times; Tramshed's What's On page, 0 and 0.
BUILDER_EVIDENCE = {
    "squarespace": re.compile(r"collection-type-events|eventlist-meta|sqs-events", re.I),
    "wix": re.compile(r"/event-info/|wix-events|events-page-app", re.I),
}

# Feeds that live at a known path rather than in a link. Probed only when the
# platform was actually detected, and only believed when the fetch comes back a
# calendar: a constructed URL that is merged unproven is a source ingesting zero.
# {origin} is the site root, {cal} the calendar page we landed on. Prefer {cal}
# where the plugin scopes its export to the page: the Bongo Club's site root
# gives 27KB of iCal and its /events-main/ page gives 3.1MB of the same feed.
FEED_TEMPLATES = {
    "my-calendar": "{origin}/?feed=my-calendar-ics",
    "events-manager": "{cal}?ical=1",
}

# ---- platforms whose feed cannot be written down as one URL -------------------
# CivicPlus has NO whole-calendar export. Checked on issaquahwa.gov: `catID=all`
# is a 404, `catID=` with no value is an empty 200, and `catID=0` and an
# unused id both return a 486-byte VCALENDAR with zero VEVENTs — a valid,
# permanently empty feed, which is the worst possible answer because it verifies.
# The feeds are per CATEGORY, and the list of them lives on /iCalendar.aspx.
#
# WHICH CATEGORIES, THOUGH. The twenty-four on that one site are half a city's
# programme and half its governance: Community Events, Concerts on the Green,
# Farmers Market, Pickering Barn and 4th of July next to City Council, Boards &
# Commissions, Public Hearings and City Hall Closures. A planning-commission
# agenda is not an event anybody opens a map to find.
#
# WRITTEN AS A KEEP LIST FIRST, AND THAT WAS THE WRONG WAY ROUND. The reasoning
# was that a keep list fails CLOSED, and failing closed is the safe direction for
# content quality. Run against those twenty-four real names it got five wrong,
# and the five say why the reasoning was bad: it threw away "4th of July",
# "Juneteenth", "Halloween" and "Pickering Barn" — a festival, two holidays and a
# venue — while keeping "Waste Collection Events" on the word `event` and
# "Transportation" because `sport` is a substring of `tran-sport-ation`.
#
# The asymmetry is the point. GOVERNANCE vocabulary is small, stable and
# near-universal: council, commission, hearing, agenda. PROGRAMME vocabulary is
# unbounded and local — "Concerts on the Green", "Salmon Days", "Pickering Barn"
# — and no word list will ever hold it. So the list names what we are sure we do
# NOT want and keeps the rest, and the rejects are reported rather than silently
# dropped. Word boundaries throughout, because that substring accident is the
# failure mode of every list like this.
CIVICPLUS_ICAL_RX = re.compile(
    r'href="([^"]*iCalendar\.aspx\?catID=(\d+)[^"]*)"[^>]*>\s*([^<]{0,80})', re.I)
CIVIC_DENY_RX = re.compile(
    r"\b(councils?|committees?|commissions?|boards?|hearings?|meetings?|agendas?|"
    r"closures?|elections?|budget(?:ing|s)?|permits?|courts?|deadlines?|zoning|"
    r"planning|public works|notices?|city hall|town hall|bids?|rfps?|"
    r"waste|recycling|garbage|refuse|collection|transportation|roadwork|"
    r"construction|detours?|utilit(?:y|ies)|development|"
    # ...and the second half of this list is what a live sweep taught it. New
    # Rochelle's 26 categories included Finance (65 future entries), Tax (65),
    # City Clerk (31), Civil Service, Assessor, Paving Schedule and Down Payment
    # Assistance; Baytown's included Warrant Resolution, Docket Calendar,
    # Mosquito Control, Police Academy Trainings and Fire Training Facility.
    # Every one of them is a real, populated, forward-looking calendar, which is
    # exactly why proving the feed cannot replace reading the name: a tax
    # deadline schedule verifies perfectly and belongs on nobody's map.
    r"finance|tax(?:es)?|assessor|clerks?|civil service|dockets?|warrants?|payroll|"
    r"billing|payments?|parking|paving|snow|plow|mosquito|inspections?|"
    r"licens(?:e|es|ing)|pre-?application|code enforcement|"
    r"fire training|police academy|assistance program)\b", re.I)

_LD_EVENT_RX = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_LD_IS_EVENT = re.compile(r'"@type"\s*:\s*(?:"[A-Za-z]*Event"|\[[^\]]*Event)', re.I)
_ICS_RX = re.compile(r'href="([^"]*\.ics(?:\?[^"]*)?)"|href="(webcal://[^"]+)"', re.I)


def future_vevents(body: str, today: Optional[str] = None) -> int:
    """How many events this calendar still has to come, read the way the ics
    adapter will.

    Not `BEGIN:VEVENT`. Measured across two cities' 47 categories, 30 of them
    parse as perfectly valid iCalendar and contain nothing at all, and several
    more contain only past dates — Baytown publishes five separate 75th-birthday
    calendars, all empty, and its Senior Center feed is three events that already
    happened. Proposing those is proposing sources that ingest zero, and every
    one costs a ledger row, a verify request and a config line to find that out.

    A COUNT rather than a yes/no, because the count is also the only ranking
    available when a city publishes more categories than it should be allowed to
    contribute — see the cap in civicplus_feeds.
    """
    if today is None:
        today = _dt.date.today().strftime("%Y%m%d")
    return sum(1 for d in re.findall(r"DTSTART[^:]*:(\d{8})", body) if d >= today)


# What a governance calendar's entries are CALLED. Used on the feed's own
# SUMMARY lines, not on the category name — see governance_heavy.
CIVIC_SUMMARY_RX = re.compile(
    r"\b(meetings?|boards?|committees?|commissions?|councils?|hearings?|"
    r"agendas?|caucus|executive session|offices? (?:will be )?closed|"
    r"closed\s*[-–]|holiday observ)", re.I)

# ...AND THE OTHER WAY A CALENDAR SAYS "WE ARE SHUT", which is to say nothing at
# all beyond the name of the day. Mansfield's City Holidays is 70 entries of
# "Christmas Day" / "Independence Day" / "New Year's Day"; Dothan's and St
# Joseph's are the same list under different names ("City Holiday Calendar",
# "Facility Closings"). Not one of them carries a governance word, so the rule
# above passes all three.
#
# MATCHED WHOLE, NEVER AS A SUBSTRING, and that is the entire difference between
# this and a word list that would do real damage. A city that programmes its
# holidays has entries like "Down Home 4th of July Parade" and "Halloween
# Spooktacular at Pickering Barn" — Visit Issaquah and Issaquah's own CivicPlus
# both carry exactly those. The bare day is a closure; the day with an event
# attached to it is an event.
_HOLIDAY = (r"new year'?s?(?: day| eve)?|christmas(?: day| eve)?|thanksgiving|"
            r"independence day|4th of july|fourth of july|labou?r day|"
            r"memorial day|veterans?'? day|juneteenth|presidents'? day|"
            r"martin luther king,? jr\.?,? day|mlk day|columbus day|"
            r"indigenous peoples'? day|easter|good friday|new year")
CIVIC_HOLIDAY_RX = re.compile(
    # ...and the wrapper words a city puts around the day, because Blaine's
    # closure list is 95 entries of "Juneteenth Day Holiday" and the bare
    # anchored name misses every one of them. Optional on both sides, still
    # anchored: "Juneteenth Day Holiday" matches, "Juneteenth Jubilee in the
    # Park" does not.
    rf"^\s*(?:holiday\s*[-–:]?\s*)?(?:{_HOLIDAY})"
    rf"(?:\s+(?:day|holiday|observed|obs\.?))*"
    rf"\s*(?:\((?:observed|obs\.?)\))?\s*$", re.I)


def placeable_share(body: str, today: Optional[str] = None) -> Tuple[int, int]:
    """(with a place, upcoming) — how much of this feed can be put on a map.

    VERIFYING IS NOT INGESTING. A feed proved to hold future events contributes
    nothing if those events carry no LOCATION and no GEO: the ics adapter drops
    them, and until it grew a counter the only symptom was a source that looked
    two thirds empty. Seattle Parks Foundation is that adapter's own worked
    example — 30 events, 20 with no LOCATION at all — and it wanted a different
    adapter entirely.

    THIS IS PRESENCE, NOT GEOCODABILITY, and the difference cost a wrong
    diagnosis worth recording. Four newly-found city feeds ingested 0 of 34, 0
    of 6, 0 of 3 and 1 of 16, the log said "no LOCATION/GEO", and this filter
    was written to catch them. It does not, because all four carry a full street
    address on every single event. What they carry it in is CivicPlus's "Venue
    Name - 123 Street  City ST ZIP", which Photon cannot read at all; the fix
    was in the geocoder, not here — see location_attempts in mapsee_ingest_ics,
    after which those same four ingest 34/34, 6/6, 3/3 and 16/16. This still
    refuses the feed that genuinely says nothing about where, which is a real
    class, but it was never the one it was written for and it must not be
    trusted to stand in for the geocoder.

    Per VEVENT rather than by counting lines, because a feed with ten events and
    one heavily-wrapped LOCATION would otherwise read as fully placeable.
    """
    if today is None:
        today = _dt.date.today().strftime("%Y%m%d")
    placed = upcoming = 0
    for block in body.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        m = re.search(r"DTSTART[^:]*:(\d{8})", block)
        if not m or m.group(1) < today:
            continue
        upcoming += 1
        if re.search(r"(?m)^(?:LOCATION|GEO)[;:]\s*\S", block):
            placed += 1
    return placed, upcoming


def governance_heavy(body: str, floor: float = 0.66) -> bool:
    """True when this calendar is a meeting schedule or a list of days the
    office is shut, wearing an events hat.

    THE NAME TEST CANNOT REACH THIS AND THE CONTENT TEST DOES IT FOR FREE. A
    first sweep proposed all of these, every one past a deny list built from
    category names and every one proved to hold future events:

        Woodbury — Holidays          4x "City Offices Closed - Christmas"
        Missoula — Holidays          8x "City Offices will be Closed"
        Mount Vernon — IDA Calendar  4x "IDA Meeting"
        Missoula — Redevelopment     4x "MRA Board Meeting"
        4 Missoula neighborhoods     1x "…Council General Meeting" each
        Mansfield — City Holidays    70x "Christmas Day", "Independence Day"
        Dothan, St Joseph            the same list, named differently

    Extending the name list would have meant guessing at `holidays`, `IDA`,
    `redevelopment` and `neighborhood` — and `neighborhood` is exactly the word
    a block-party calendar uses, so the guess costs real content. The ENTRIES
    say it plainly instead, and they say it the same way in every city.

    Two-thirds rather than any, because a parks calendar legitimately carries
    the odd "Parks Board Meeting" and that is not what this is for. Measured
    against those seven: every one is 100%, and the ones kept alongside them —
    Redmond's Environmental Sustainability (Youth Climate Action Fund office
    hours, a Community Preparedness Fair), its Volunteer Opportunities, and
    Mansfield's Household Hazardous Waste Drop-Off — are all 0%.
    """
    sums = re.findall(r"(?m)^SUMMARY:(.+)$", body)
    if not sums:
        return False
    hits = sum(1 for x in sums
               if CIVIC_SUMMARY_RX.search(x) or CIVIC_HOLIDAY_RX.match(x.strip()))
    return hits / len(sums) >= floor


def civicplus_feeds(session, origin: str, timeout: int = 15, prove: bool = True,
                    cap: int = 6) -> Tuple[List[Tuple[str, str]], List[str]]:
    """(kept, dropped) per-category iCal feeds on a CivicPlus site.

    Returns every category except the ones whose NAME is governance and — when
    `prove` — the ones whose feed has nothing still to come. `dropped` carries
    both, each tagged with which test it failed, so a sweep reports what it
    walked past rather than quietly halving a city.

    The list is read off /iCalendar.aspx, which is the page the site's own
    "subscribe" link points at, not a path this guesses: it is where CivicPlus
    puts one href per category with the category's name as the link text.

    THE NAME TEST RUNS FIRST AND IT IS FREE. Proving costs one fetch per
    surviving category, which is this backend's whole budget — Gloucester
    publishes 60 — so the categories that can be refused on their name are
    refused before anything is fetched.

    AND THEN A CAP, because a city is not supposed to be twenty sources. One
    config line per category per city puts 5,770 US cities somewhere north of
    20,000 entries in ics_sources.json, which is a file a person has to be able
    to read. Ranked by how many future events the category actually holds —
    which proving has already counted, so the ranking is free — and the tail is
    reported as dropped rather than lost silently. `cap=0` lifts it.
    """
    o = urlparse(origin)
    page = f"{o.scheme}://{o.netloc}/iCalendar.aspx"
    try:
        r = session.get(page, timeout=timeout, allow_redirects=True)
    except Exception:                                             # noqa: BLE001
        return [], []
    if r.status_code >= 400 or CHALLENGE_RX.search(r.text[:6000]):
        return [], []
    kept, dropped, seen = [], [], set()
    for href, cat_id, label in CIVICPLUS_ICAL_RX.findall(r.text):
        name = re.sub(r"\s+", " ", unescape(label)).strip()
        if cat_id in seen:
            continue
        seen.add(cat_id)
        if not name:
            dropped.append(f"catID={cat_id} (unnamed)")
            continue
        if CIVIC_DENY_RX.search(name):
            dropped.append(f"{name} (governance)")
            continue
        url = urljoin(str(r.url), unescape(href))
        if not prove:
            kept.append((url, name, 0))
            continue
        try:
            f = session.get(url, timeout=timeout)
        except Exception:                                         # noqa: BLE001
            dropped.append(f"{name} (unreachable)")
            continue
        n = future_vevents(f.text) if f.status_code == 200 else 0
        if not n:
            dropped.append(f"{name} (nothing upcoming)")
            continue
        if governance_heavy(f.text):
            dropped.append(f"{name} (meeting schedule)")
            continue
        placed, upcoming = placeable_share(f.text)
        # Half, which is the same line the ics adapter's own warning draws. A
        # source that hands over fewer than half its events is not worth a daily
        # fetch, and one that hands over none is worse than nothing.
        if upcoming and placed * 2 < upcoming:
            dropped.append(f"{name} ({placed}/{upcoming} placeable)")
            continue
        kept.append((url, name, n))
    kept.sort(key=lambda k: -k[2])
    if cap and len(kept) > cap:
        for _u, name, n in kept[cap:]:
            dropped.append(f"{name} ({n} upcoming, past the cap of {cap})")
        kept = kept[:cap]
    return [(u, name) for u, name, _n in kept], dropped


def _civicplus_one(session, origin: str, timeout: int = 15) -> Optional[str]:
    """The single feed constructed_feed's contract can return.

    A city is many calendars and this is one of them, which is why the civic
    backend calls civicplus_feeds directly and proposes each. This exists so the
    OSM backend — which finds the odd library or community centre running
    CivicPlus — stops reporting them as `ics-without-feed`. First kept category
    wins; there is no ranking to be had from a name.
    """
    kept, _ = civicplus_feeds(session, origin, timeout)
    return kept[0][0] if kept else None


def constructed_feed(session, origin: str, labels: Iterable[str],
                     timeout: int = 15, cal: Optional[str] = None) -> Optional[str]:
    """A feed URL implied by the PLATFORM, proved by fetching it.

    Constructing a URL is a guess; a guess that is merged into a config becomes a
    source that ingests nothing. So the guess is fetched and has to come back as
    a calendar — and the challenge check runs first, because the WAF that sits in
    front of these sites answers the guess with 200 and a spinner.
    """
    for label in labels:
        # CivicPlus has no single-URL form — see the note above CIVICPLUS_ICAL_RX.
        if label == "civicplus":
            one = _civicplus_one(session, origin, timeout)
            if one:
                return one
            continue
        tmpl = FEED_TEMPLATES.get(label)
        if not tmpl:
            continue
        base = (cal or origin).split("?")[0]
        url = tmpl.format(origin=origin.rstrip("/"),
                          cal=base if base.endswith("/") else base + "/")
        try:
            r = session.get(url, timeout=timeout)
        except Exception:                                         # noqa: BLE001
            continue
        if r.status_code != 200 or CHALLENGE_RX.search(r.text[:6000]):
            continue
        if "BEGIN:VCALENDAR" in r.text[:4000]:
            return url
    return None


def _has_event_block(body: str) -> bool:
    """True when the adapter that would ingest this page can find an Event on it."""
    import mapsee_ingest_jsonld as jl
    for blk in jl._LD_RX.findall(body):
        doc = jl._parse_ld(blk)
        if doc is None:
            continue
        for item in jl._iter_items(doc):
            if jl._is_event(item):
                return True
    return False


def fingerprint(body: str) -> Tuple[List[str], Optional[str]]:
    """(labels, ics_url). Labels are every platform the page shows signs of —
    except a site builder with no sign of its events app, which is dropped: see
    BUILDER_EVIDENCE."""
    labels = []
    for name, rx, _ in PLATFORM_SIGNS:
        if not rx.search(body):
            continue
        ev = BUILDER_EVIDENCE.get(name)
        if ev and not ev.search(body):
            continue
        labels.append(name)
    # READ IT THE WAY THE INGESTER WILL. This used to be a regex over the raw
    # block, which said yes to 40 Event blocks on the Royal Lyceum's programme
    # that mapsee_ingest_jsonld could not parse at all — so discovery proposed
    # the page, verification found "no Event blocks", and the disagreement was
    # invisible from either end. Parse with the adapter's own parser and the two
    # cannot drift: whatever it can read is what gets proposed.
    if _has_event_block(body):
        labels.append("jsonld-event")
    m = _ICS_RX.search(body)
    ics = _https((m.group(1) or m.group(2)) if m else None)
    if ics:
        labels.append("ics")
    return labels, ics


def adapter_for(labels: Iterable[str]) -> Optional[str]:
    """The adapter to propose this to. Order matters: a Squarespace site that
    ALSO ships an Event block is still a Squarespace site, and its adapter reads
    the collection page rather than crawling per-event URLs."""
    # my-calendar outranks the page's own Event block deliberately: its iCal
    # export is the WHOLE calendar, where the JSON-LD on the page is whatever
    # that one view happened to render.
    # civicplus sits ABOVE the bare `ics` label and below every plugin: a city
    # site that also runs The Events Calendar is a tribe source, but one whose
    # page happens to link a stray .ics is not — CivicPlus's own per-category
    # feeds are the whole calendar and the stray link is one slice of it.
    order = ["tribe", "mylisting", "localist", "gancio", "venuepilot",
             "squarespace", "my-calendar", "trumba", "libcal",
             "wp-event-manager", "events-manager", "modern-events", "wix",
             "civicplus", "jsonld-event", "ics"]
    labs = set(labels)
    by_label = {name: adapter for name, _, adapter in PLATFORM_SIGNS}
    by_label["jsonld-event"] = "jsonld"
    by_label["ics"] = "ics"
    for name in order:
        if name in labs:
            return by_label.get(name)
    return None


# ---- Overpass ----------------------------------------------------------------
def _overpass_query(bbox: str) -> str:
    parts = "".join(f"{sel}({bbox});" for sel in OSM_SELECTORS)
    return f"[out:json][timeout:180];({parts});out tags center;"


def overpass_venues(session, bbox: str, name: str = "?",
                    endpoint: str = OVERPASS_ENDPOINT, quiet: bool = False):
    """Venues in the bbox that publish a website.

    Returns None if OVERPASS NEVER ANSWERED, and [] if it answered with nothing.
    Those are not the same fact and returning [] for both is what let a sweep
    report "Adelaide: 0 venues publish a website" for nine metros in a row that
    had simply never been asked — while the cursor advanced past all nine. The
    endpoint hands out a couple of slots and answers 429 or 504 when they are
    busy, which is normal traffic across a sweep and worth waiting out; a
    connection reset after four tries is the endpoint declining, and the metro is
    unread.
    """
    q = _overpass_query(bbox)
    for attempt in range(4):
        try:
            r = session.post(endpoint, data=q.encode("utf-8"), timeout=200)
            if r.status_code in (429, 504) and attempt < 3:
                wait = int(r.headers.get("Retry-After") or 0) or (OVERPASS_BACKOFF_S * 3 ** attempt)
                if not quiet:
                    print(f"  overpass {name}: {r.status_code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except Exception as exc:                                  # noqa: BLE001
            if attempt == 3:
                if not quiet:
                    print(f"  overpass {name} FAILED: {type(exc).__name__}: {exc}")
                return None
            time.sleep(OVERPASS_BACKOFF_S * 3 ** attempt)
    else:
        return None
    out = []
    for e in elements:
        t = e.get("tags") or {}
        site = t.get("website") or t.get("contact:website")
        if not site or BAD_SITE_RX.search(site):
            continue
        if not site.startswith(("http://", "https://")):
            site = "https://" + site.lstrip("/")
        kind = (t.get("amenity") or t.get("tourism") or t.get("leisure")
                or ("club" if t.get("club") else None) or t.get("shop")
                or t.get("craft") or t.get("office") or "")
        out.append({
            "name": t.get("name") or t.get("operator") or "?",
            "url": site,
            "kind": kind,
            "lat": e.get("lat") or (e.get("center") or {}).get("lat"),
            "lon": e.get("lon") or (e.get("center") or {}).get("lon"),
            "street": " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x) or None,
            "city": t.get("addr:city"),
            "region": t.get("addr:state") or t.get("addr:province"),
            "postal_code": t.get("addr:postcode"),
            "country": t.get("addr:country"),
        })
    return out


# ---- finding the calendar on a venue's own site ------------------------------
def _https(u: Optional[str]) -> Optional[str]:
    """webcal:// is https:// wearing a hat.

    It is the standard scheme for "subscribe to this calendar", and plenty of
    parish and club sites publish their .ics that way. requests has no adapter
    for it, so a candidate carrying one does not fail verification for a reason
    about the FEED — it raises InvalidSchema and reads as a broken source. Two
    Sydney candidates were lost to that before this existed, and the ics adapter
    would have raised the same way on the merged config.
    """
    if u and u.lower().startswith("webcal://"):
        return "https://" + u[9:]
    return u


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower().lstrip("www.") == urlparse(b).netloc.lower().lstrip("www.")


def _offsite(u: str) -> Optional[str]:
    h = urlparse(u).netloc.lower()
    for host in OFFSITE_HOSTS:
        if host in h:
            return host.strip(".")
    return None


def _rank(u: str) -> Tuple[int, int]:
    """A bare /events beats /about/events-policy. Shorter path wins ties."""
    p = urlparse(u).path.lower().rstrip("/")
    exact = 0 if re.fullmatch(
        r"/(events?|calendar|agenda|shows?|gigs?|programme?|whats-on|kalender|"
        r"calendrier|calendario|veranstaltungen)", p) else 1
    return (exact, len(p))


def _prefer_listing(session, deep_url: str, timeout: int = 18) -> Optional[str]:
    """Walk up from a single event's page to the calendar it sits in.

    A JSON-LD site usually carries its Event blocks on the EVENT pages and
    nothing on the index — the Royal Lyceum's /events/ has three blocks and not
    one Event, while /events/guys-dolls has forty. So the fingerprint lands
    deep, and a config listing one show is a source that dies the day that show
    closes, silently, having only ever imported one production.

    The adapter is built to crawl: give it the index as `listing` and a
    link_pattern for the show pages and it reads the whole programme. So walk up
    one segment and take the parent INSTEAD — but only once it has been fetched
    and actually links to sibling event pages, because a parent that does not is
    a listing of nothing.
    """
    o = urlparse(deep_url)
    parts = [p for p in o.path.split("/") if p]
    if len(parts) < 2:
        return None
    parent = f"{o.scheme}://{o.netloc}/" + "/".join(parts[:-1]) + "/"
    try:
        r = session.get(parent, timeout=timeout, allow_redirects=True)
    except Exception:                                             # noqa: BLE001
        return None
    if r.status_code >= 400 or CHALLENGE_RX.search(r.text[:6000]):
        return None
    sibling = re.compile(re.escape("/" + "/".join(parts[:-1]) + "/") + r"[A-Za-z0-9\-]+")
    if len(set(sibling.findall(r.text))) < 2:
        return None                       # not an index of anything
    return str(r.url)


def find_calendar(session, home_url: str, timeout: int = 18,
                  max_follow: int = 2, on_home=None) -> Dict[str, Any]:
    """Locate the calendar on a venue site and say what runs it.

    status is one of: ok | no-calendar | offsite:<host> | bot-challenge |
    unreachable | http<code>.

    `on_home(url, body)` is handed the HOMEPAGE the moment it is fetched, and
    exists so a caller can ask that page a second question without paying for a
    second request. catalog_discover_civic reads the city's outbound links for
    its tourism board that way. It is deliberately a callback rather than a
    returned body: this function is called once per venue across a whole metro,
    and handing every caller a megabyte of HTML it did not ask for is how a
    sweep starts running out of memory instead of time.
    """
    out = {"cal_url": None, "labels": [], "adapter": None, "ics": None,
           "status": None, "offsite": None, "extra": {}}
    try:
        r = session.get(home_url, timeout=timeout, allow_redirects=True)
    except Exception as exc:                                      # noqa: BLE001
        out["status"] = "unreachable"
        out["note"] = type(exc).__name__
        return out
    if CHALLENGE_RX.search(r.text[:6000]):
        out["status"] = "bot-challenge"
        return out
    if r.status_code >= 400:
        out["status"] = f"http{r.status_code}"
        return out

    base, body = str(r.url), r.text
    if on_home:
        try:
            on_home(base, body)
        except Exception:                                         # noqa: BLE001
            pass                    # a caller's extra question must not cost the find
    home_labels, home_ics = fingerprint(body)

    onsite, offsite_hit = [], None
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if not (CAL_LINK_RX.search(href) or CAL_LINK_RX.search(text)):
            continue
        u = urljoin(base, href)
        if not u.startswith(("http://", "https://")):
            continue
        off = _offsite(u)
        if off:
            offsite_hit = offsite_hit or off
        elif _same_host(u, base):
            onsite.append(u)

    for u in sorted(dict.fromkeys(onsite), key=_rank)[:max_follow]:
        try:
            r2 = session.get(u, timeout=timeout, allow_redirects=True)
        except Exception:                                          # noqa: BLE001
            continue
        if CHALLENGE_RX.search(r2.text[:6000]):
            out["status"] = "bot-challenge"
            return out
        if r2.status_code >= 400:
            continue
        labels, ics = fingerprint(r2.text)
        if labels:
            ics = ics or constructed_feed(session, base, labels, cal=str(r2.url))
            m = _VP_IDS_RX.search(r2.text) or _VP_IDS_RX.search(body)
            if m:
                out["extra"]["account_ids"] = [int(x) for x in m.group(1).replace(" ", "").split(",") if x]
            adapter = adapter_for(labels)
            cal_url = str(r2.url)
            if adapter == "jsonld":
                cal_url = _prefer_listing(session, cal_url, timeout) or cal_url
            out.update(cal_url=cal_url, labels=labels, ics=ics,
                       adapter=adapter, status="ok")
            return out
        out["cal_url"] = out["cal_url"] or str(r2.url)

    m = _VP_IDS_RX.search(body)
    if m:
        out["extra"]["account_ids"] = [int(x) for x in m.group(1).replace(" ", "").split(",") if x]
    if home_labels:
        # The platform is visible but no page announced itself as the calendar.
        # Still worth proposing — verification is what decides — but the URL is
        # the weaker one, so say so rather than dress it up as a calendar page.
        out.update(cal_url=out["cal_url"] or base, labels=home_labels,
                   ics=home_ics or constructed_feed(session, base, home_labels),
                   adapter=adapter_for(home_labels), status="ok-homepage")
        return out
    if offsite_hit:
        out.update(status=f"offsite:{offsite_hit}", offsite=offsite_hit)
        return out
    out["status"] = "no-calendar"
    return out


# ---- turning a find into a candidate ----------------------------------------
def _venue_block(v: Dict[str, Any]) -> Dict[str, Any]:
    """The config `venue` block, from the survey. This is what makes a
    single-venue calendar placeable AND time-correct — see the module header."""
    b = {"name": v.get("name")}
    for src, dst in (("street", "address"), ("city", "city"), ("region", "region"),
                     ("postal_code", "postal_code"), ("country", "country")):
        if v.get(src):
            b[dst] = v[src]
    if v.get("lat") is not None:
        b["lat"], b["lon"] = round(float(v["lat"]), 7), round(float(v["lon"]), 7)
    return b


# The adapters to_candidate knows how to write a config entry for. Anything
# adapter_for can NAME but this cannot SHAPE is a find that reaches the end of
# the pipeline and evaporates, so the two lists have to be compared out loud.
SHAPEABLE = {"ics", "tribe", "squarespace", "localist", "jsonld", "gancio",
             "venuepilot"}

# VenuePilot's public GraphQL wants ACCOUNT IDS, and a venue's own embedded
# widget carries them in plain sight — window.venuepilotSettings.general
# .accountIds. Without them the find is unusable, which is why every venuepilot
# detection used to evaporate; with them it is an ordinary config entry.
_VP_IDS_RX = re.compile(r"accountIds\s*:\s*\[([\d,\s]+)\]", re.I)


def why_no_candidate(found: Dict[str, Any]) -> str:
    """Why a successful probe still produced nothing to verify.

    These used to be counted as "ok", which is how 25 finds in one sweep came to
    be reported under the same word as a success. A skip counter that cannot say
    what it skipped is a measurement that reads as coverage.
    """
    adapter, cal = found.get("adapter"), found.get("cal_url")
    if not adapter:
        return "no-adapter(" + ("+".join(found.get("labels") or []) or "nothing") + ")"
    if not cal:
        return "no-listing-url"
    if adapter == "ics" and not found.get("ics"):
        # A platform that HAS a calendar and did not hand over a feed URL: the
        # page linked no .ics and no FEED_TEMPLATE matched or fetched.
        return "ics-without-feed(" + ("+".join(found.get("labels") or []) or "?") + ")"
    if adapter == "venuepilot" and not (found.get("extra") or {}).get("account_ids"):
        return "venuepilot-without-accountIds"
    if adapter not in SHAPEABLE:
        return "no-config-shape(" + adapter + ")"
    return "unknown"


def to_candidate(v: Dict[str, Any], found: Dict[str, Any],
                 metro: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A verify-able candidate in the shape its adapter's config file wants."""
    adapter, cal = found.get("adapter"), found.get("cal_url")
    if not adapter or not cal:
        return None
    cat = KIND_CATEGORY.get(v.get("kind") or "", DEFAULT_CATEGORY)
    name = v.get("name") or urlparse(cal).netloc
    common = {"name": name, "category": cat,
              "_found": f"osm:{v.get('kind')} -> {'+'.join(found.get('labels') or [])}"}
    if adapter == "ics":
        ics = found.get("ics")
        if not ics:
            return None                       # the platform said ics, the site did not serve one
        return dict(common, type="ics", url=_https(urljoin(cal, ics)),
                    geocode_suffix=_suffix(v, metro), limit=300)
    if adapter == "tribe":
        o = urlparse(cal)
        return dict(common, type="tribe", base_url=f"{o.scheme}://{o.netloc}",
                    within_days=120, max_pages=10, venue=_venue_block(v))
    if adapter == "squarespace":
        return dict(common, type="squarespace", collection=cal,
                    max_events=200, venue=_venue_block(v))
    if adapter == "gancio":
        o = urlparse(cal)
        return dict(common, type="gancio", base_url=f"{o.scheme}://{o.netloc}",
                    within_days=180, max=500,
                    default_city=v.get("city") or (metro or "").split(",")[0].strip() or None,
                    default_country=v.get("country"))
    if adapter == "venuepilot":
        ids = (found.get("extra") or {}).get("account_ids")
        if not ids:
            return None                       # no ids = nothing to ask the API for
        return dict(common, type="venuepilot", account_ids=ids,
                    within_days=120, venue=_venue_block(v))
    if adapter == "localist":
        o = urlparse(cal)
        return dict(common, type="localist", base_url=f"{o.scheme}://{o.netloc}", days=90)
    if adapter == "jsonld":
        o = urlparse(cal)
        host = re.escape(o.netloc)
        if "wix" in (found.get("labels") or []):
            # Wix Events does not put its listing in anchors — it ships the whole
            # set as {"slug": ...} and routes each one through /event-info/. A
            # WordPress-shaped /event/<slug>/ pattern matches none of it, which is
            # how four London candidates were proposed with a link_pattern that
            # could never fire. Same shape as the Sea Monster Lounge entry.
            return dict(common, type="jsonld", listing=[cal],
                        link_pattern=r'"slug":"([a-zA-Z0-9-]+)"',
                        url_template="/event-info/{}",
                        max_events=100, venue=_venue_block(v))
        return dict(common, type="jsonld", listing=[cal],
                    link_pattern=rf"https://{host}/(?:event|events)/[a-z0-9\-]+/?",
                    max_events=150, venue=_venue_block(v))
    return None


def _suffix(v: Dict[str, Any], metro: Optional[str] = None) -> str:
    """What to append to an iCal event's LOCATION before geocoding it.

    An ics config carries no coordinates — geocode_suffix is the ONLY thing
    placing its events — so an empty one is not a harmless default, it hands the
    geocoder a bare venue name and lets it land anywhere on earth that shares it.
    Erith Yacht Club has no addr:city in OSM and would have shipped exactly that.
    The metro being swept is always known, so it is the floor.
    """
    bits = [x for x in (v.get("city"), v.get("region")) if x]
    if not bits and metro:
        bits = [p.strip() for p in metro.split(",") if p.strip()]
    return (", " + ", ".join(bits)) if bits else ""


# ---- the metro walk ----------------------------------------------------------
def _bbox_from(latlong: str, radius_km: float) -> str:
    lat, lon = (float(x) for x in latlong.split(","))
    dlat = radius_km / 111.0
    import math
    dlon = radius_km / max(1e-6, 111.0 * math.cos(math.radians(lat)))
    return f"{lat - dlat:.4f},{lon - dlon:.4f},{lat + dlat:.4f},{lon + dlon:.4f}"


# Catalog sources per country, measured 2026-08-30 with `catalog_curate.py
# coverage`. Nothing else reads this: it is the SWEEP ORDER, thinnest first.
#
# metros() has always promised the budget goes "where the catalog is thinnest",
# and until this existed it did not. metros_global.json is ordered by the order
# the countries were ADDED — GB, CA, AU, IE, NZ, FR — which is very nearly
# richest-first: GB has 48 sources and Hong Kong has 1. Measured from the live
# cursor at three metros a run, the walk reached Germany on day 4 and Brazil —
# the one country here with a purpose-built adapter, mapsee_ingest_mapasculturais
# — on day 39, immediately before 80 US metros with 576 sources between them
# took the next 27 days. The thinnest countries in the file (HK 1, KR 2, AE 2,
# PT 2, DK 2) waited longest, which is the rule exactly backwards.
#
# A country absent from this table has no sources at all, so it sorts FIRST.
# That is the same rule said the other way round, and it means a metro added for
# a country the catalog has never reached is swept next rather than in a year.
# Re-measure by re-running coverage and editing this dict.
CATALOG_SOURCES = {
    "US": 576, "GB": 48, "AU": 46, "CA": 36, "FR": 33, "DE": 25, "IE": 17,
    "NZ": 16, "NL": 16, "IN": 15, "BR": 14, "MX": 13, "ZA": 11, "JP": 11,
    "IT": 10, "CH": 9, "ES": 8, "BE": 7, "SG": 5, "PL": 5, "AT": 4, "NO": 4,
    "SE": 4, "FI": 3, "CZ": 3, "DK": 2, "PT": 2, "KR": 2, "AE": 2, "HK": 1,
}


def metro_key(m: Dict[str, Any]) -> str:
    """The cursor's name for a metro. NOT its position — see _discover_osm."""
    return f"{m.get('country', '??')}:{m.get('name', '?')}"


def metros(path_global: str = "metros_global.json",
           path_us: str = "metros_us.txt") -> List[Dict[str, Any]]:
    """Every metro this repo already sweeps, as (name, country, bbox).

    Ordered THINNEST CATALOG FIRST, by CATALOG_SOURCES above, so a cursor that
    has only ever run a few times has spent its budget where the catalog is
    thinnest. The US sorts last on the same rule that orders everything else —
    576 sources — rather than by a special case, because it is the part already
    covered by the ticketing APIs.

    Ties keep the order the config file gives them, so the walk inside one
    country stays the order somebody wrote down.
    """
    out: List[Dict[str, Any]] = []
    p = os.path.join(HERE, path_global)
    if os.path.exists(p):
        for c in json.load(open(p, encoding="utf-8")).get("countries", []):
            for m in c.get("metros", []):
                if not m.get("latlong"):
                    continue
                out.append({"name": m["name"], "country": c.get("code", "??"),
                            "bbox": _bbox_from(m["latlong"], float(m.get("radius") or 25))})
    p = os.path.join(HERE, path_us)
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            latlong, _, name = line.partition("#")
            latlong = latlong.strip()
            if not re.match(r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$", latlong):
                continue
            out.append({"name": (name.strip() or latlong), "country": "US",
                        "bbox": _bbox_from(latlong, 25.0)})
    # Stable, so a tie inside one country keeps the order the config wrote down.
    out.sort(key=lambda m: CATALOG_SOURCES.get(m.get("country"), 0))
    return out
