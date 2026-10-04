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

import mapsee_gcal

HERE = os.path.dirname(os.path.abspath(__file__))

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_BACKOFF_S = 5
# EIGHT ASKS, NOT FOUR, because a refusal costs the RUN and not the metro: the
# caller stops at the first metro Overpass never answered, so the cursor does
# not pass it. Over the community walk's 13 runs to 2026-10-03, 134 asks were
# answered 67 at once, 45 on the second try, 10 on the third, 6 on the fourth,
# and 6 never — each attempt failing about half the time (67/134, 22/67,
# 12/22, 6/12). Every one of the 6 ended its run, two of them before a single
# metro (09-23 and 09-27, under two minutes each); the six used 60 of their
# 330 budgeted minutes. Were the asks independent, four more would leave
# 6 x 0.5^4 = 0.4 of those; they are not quite (Brasilia was refused two days
# running), which is why the wait is capped rather than the asks unbounded. A
# 504 comes back in ~9 s (09-23: four asks and 65 s of waits in 102 s), so
# eight refused asks cost ~6 minutes (8 x 9 s + 305 s of waits) of a budget
# that would otherwise go unspent.
#
# AND A CLOCK, because not every refusal is a quick 504. An ask may hang for
# its whole 200 s read timeout, and eight of those plus the waits is 32
# minutes on ONE metro, outside the caller's --max-minutes deadline (it is
# checked between metros, not inside this call), where four asks were 14.
# So no ask starts once it would begin past OVERPASS_PATIENCE_S: the worst
# case is then 600 s plus one ask, under the old 865 s, and the measured
# refusal shape (~380 s) still gets all eight.
OVERPASS_ATTEMPTS = 8
OVERPASS_BACKOFF_CAP_S = 60
OVERPASS_PATIENCE_S = 600

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

# WHERE A COMMUNITY CENTRE'S TIMETABLE LIVES. Most North American municipal
# centres publish drop-ins through a recreation-booking platform, not their
# own CMS: a 2026-10-03 sample of 117 centres the ledger calls no-calendar
# found ActiveNet linked on 14 (Canada 9 of 25), Xplor 2, RecDesk 1, WebTrac
# 1. Each was measured that day (docs/agents/platforms-probed.md): only
# PerfectMind is both permitted and drop-in (mapsee_ingest_perfectmind), and
# the rest are refused or course catalogues. The link is rarely labelled
# "events" - it says Register, Programs or Drop-in schedule - so for these
# hosts the HOST is the signal, not the text (see find_calendar).
REC_BOOKING_HOSTS = (
    "activecommunities.com", "perfectmind.com", "amilia.com", "recdesk.com",
    "myrec.com", "rec1.com", "myvscloud.com", "capturepoint.com",
)
OFFSITE_HOSTS = (
    "eventbrite.", "meetup.com", "facebook.com", "fb.me", "instagram.com",
    "linktr.ee", "ticketmaster.", "axs.com", "seatgeek.com", "dice.fm",
    "lu.ma", "songkick.com", "bandsintown.com", "tickettailor.com",
    "universe.com", "showpass.com", "humanitix.com", "trybooking.com",
) + REC_BOOKING_HOSTS

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
#
# MULTILINGUAL, for the same reason CAL_LINK_RX is: the civic walk rotates
# countries now, and a town hall in Bern publishes its Gemeinderatssitzungen in
# German. An English-only matcher does not fail loudly here — it passes the
# calendar, and a council meeting schedule verifies perfectly and lands on the
# map as twenty `community` events.
#
# Unlike the English half, which was built from what a live sweep proposed, this
# half is VOCABULARY: nothing has swept a German town yet. What makes that safe
# to write down is governance_heavy's floor — two thirds of a feed's SUMMARY
# lines have to match before anything is refused — so a book club whose entries
# say "Reunião" or "Treffen" is in no danger. Only unambiguous COMPOUNDS are
# here for that reason: `Gemeinderat` and `conseil municipal` name one thing,
# where a bare `Sitzung`, `réunion` or `möte` is just the word "meeting" and
# would refuse a reading group. Replace a guess here with a measurement the
# first time a non-US sweep produces one.
_CIVIC_SUMMARY_EN = (
    r"meetings?|boards?|committees?|commissions?|councils?|hearings?|"
    r"agendas?|caucus|executive session|offices? (?:will be )?closed|"
    r"closed\s*[-–]|holiday observ")
_CIVIC_SUMMARY_INTL = (
    # de
    r"gemeinderat\w*|stadtrat\w*|kreistag\w*|ratssitzung\w*|gemeindevertretung|"
    r"ausschuss\w*|ausschüsse|amtsblatt|"
    # fr
    r"conseil municipal|conseil communautaire|conseil départemental|"
    r"commission municipale|séance du conseil|enquête publique|"
    # nl
    r"gemeenteraad\w*|raadsvergadering\w*|raadscommissie\w*|collegevergadering\w*|"
    # es
    r"pleno municipal|pleno del ayuntamiento|junta de gobierno|comisión informativa|"
    # it
    r"consiglio comunale|giunta comunale|seduta del consiglio|"
    # pt (and br)
    r"câmara municipal|assembleia municipal|reunião de câmara|"
    # sv / no / da
    r"kommunfullmäktige|kommunstyrelse\w*|kommunestyre\w*|byrådsmøde\w*|"
    r"byrådsmøte\w*|sammanträde\w*|"
    # fi
    r"kaupunginvaltuusto\w*|kunnanvaltuusto\w*|kaupunginhallitus\w*|lautakunn\w*|"
    # pl
    r"sesja rady|rada miasta|rada gminy|"
    # cs
    r"zastupitelstv\w*|rada města")
CIVIC_SUMMARY_RX = re.compile(
    r"\b(" + _CIVIC_SUMMARY_EN + r"|" + _CIVIC_SUMMARY_INTL + r")", re.I)

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
    # ...with or without a dash before the wrapper: Upper Arlington, OH writes
    # "New Year's Day - Holiday", and five such rows slipped past (2026-09-27).
    rf"(?:\s*[-–:]?\s*(?:day|holiday|observed|obs\.?))*"
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


# THE SAME JUDGEMENT, ONE EVENT AT A TIME. governance_heavy condemns a whole
# FEED at two thirds, which is right for a calendar that is nothing but meetings
# and wrong for the mixed ones: Baldwin Park's Main Calendar and Mansfield's
# carry real programmes AND their committee minutes, so they pass the feed test
# and bring 24 and 25 governance rows onto the map with them. Measured across
# eight civic towns live: 64 of 1,150 events, 5%.
#
# IT CANNOT BE THE SAME VOCABULARY, and the reason is the whole difficulty. The
# feed-level rule leans on `\bboards?\b`, which per event would delete "board
# game girlies!", "Board games and pizza at Zaucer Pizza" and "Board Games @
# Servaes Brewing Co." — three real, live, exactly-the-kind-of-thing-we-want
# events found in the same sample. So this is PHRASES, never single words: a
# board that meets is named as one.
CIVIC_TITLE_RX = re.compile(
    r"\b(?:city|town|village|county|borough)\s+(?:council|commission|board)\b|"
    # A THEMED BODY IS STILL A BODY, with or without the word "meeting" after
    # it. Baldwin Park publishes "Recreation and Community Services Commission"
    # bare, and the phrase above misses it by one word.
    r"\b(?:advisory|oversight|planning|zoning|review|development|appeals?|"
    r"services|library|parks?|recreation|arts?|housing|ethics|personnel|traffic|"
    r"utility|water|police|fire|historic|preservation|landmarks?|"
    # St. Charles, MO (2026-09-27): 7 rows past the list above, all of them one
    # of these bodies named bare - "Veterans Commission" x3, "Convention and
    # Visitors Commission" x2 - and "Board of Adjustment" x2 below.
    r"veterans?|visitors?|tourism|beautification|sustainability|environmental|"
    r"transportation|tree|cemetery|finance|audit|civil service)\s+"
    r"(?:board|committee|commission)\b|"
    r"\bboard of (?:adjustment|appeals|zoning|education|directors|trustees|"
    r"supervisors|commissioners|elections|health|review)\b|"
    r"\b(?:board|committee|commission|council|subcommittee)\s+meeting\b|"
    r"\bpublic hearing\b|\bexecutive session\b|\bcaucus\b|"
    r"\boffices?\s+(?:are\s+)?closed\b|\bcity hall closed\b|"
    # A CLOSURE IS NOT AN EVENT. Yukon, OK: four "Library Closed (...)" rows
    # on its main calendar passed the phrases above.
    r"\b(?:library|libraries|branch|facility|facilities|building|pool|town hall)"
    r"\s+(?:is\s+|will be\s+)?closed\b|"
    # ...and the same notice written the other way round: Dormont, PA files six
    # "CLOSED: ..." rows. A colon or dash right after the word, so "Closed
    # Captioned" screenings are untouched.
    r"^\s*closed\s*[:\-\u2013]|"
    r"\bno street sweeping\b|\bwork session\b|\bcanvass\b",
    re.I)


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
    # AN EMBEDDED GOOGLE CALENDAR IS A CALENDAR, though it links no .ics. It is
    # what a club or a neighbourhood group reaches for, and Google Sites' own
    # Calendar block is one: 14 of 600 sites filed `no-calendar` carried one
    # (2026-09-30). Proposed under its export URL, which is the calendar's
    # identity in ics_sources.json. `verify` reads it through the Calendar API,
    # or skips it until GOOGLE_CALENDAR_API_KEY is set (see mapsee_gcal). The
    # first calendar only: a block can carry several, and a candidate is one
    # source.
    else:
        ids = mapsee_gcal.embed_ids(body)
        if ids:
            labels.append("gcal-embed")
            ics = mapsee_gcal.ical_url(ids[0])
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
             "civicplus", "gcal-embed", "jsonld-event", "ics"]
    # gcal-embed outranks the page's own Event block for my-calendar's reason:
    # the embedded calendar is the whole programme, the JSON-LD one view of it.
    labs = set(labels)
    by_label = {name: adapter for name, _, adapter in PLATFORM_SIGNS}
    by_label["gcal-embed"] = "ics"
    by_label["jsonld-event"] = "jsonld"
    by_label["ics"] = "ics"
    for name in order:
        if name in labs:
            return by_label.get(name)
    return None


# ---- Overpass ----------------------------------------------------------------
# Which OSM key each kind in OSM_SELECTORS lives under, read off the selector
# strings so the two can never disagree: `library` -> `amenity`, `museum` ->
# `tourism`, `club` -> `club`. This is what lets a sweep be PINNED to a kind.
_SEL_RX = re.compile(r'nwr\["([a-z_]+)"(?:[=~]"\^?\(?([a-z_|]+)\)?\$?")?\]')


def kind_keys() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for sel in OSM_SELECTORS:
        m = _SEL_RX.match(sel)
        if not m:
            continue
        key, values = m.group(1), m.group(2)
        for v in (values.split("|") if values else [key]):
            out[v] = key
    return out


def _overpass_query(bbox: str, kinds: Optional[Iterable[str]] = None) -> str:
    """The union for one metro — every programme-venue, or only the KINDS named.

    A pinned query asks Overpass for less and probes fewer sites, which is
    what makes a targeted walk (`--kinds community_centre,library`) cheap
    enough to cover many metros in one run: Washington DC has 198 community
    centres with a website against ~600 programme-venues of every kind. An
    unknown kind is a loud error, not an empty sweep — the parkrun rule.
    """
    if not kinds:
        parts = "".join(f"{sel}({bbox});" for sel in OSM_SELECTORS)
        return f"[out:json][timeout:180];({parts});out tags center;"
    keys = kind_keys()
    by_key: Dict[str, List[str]] = {}
    for k in kinds:
        if k not in keys:
            raise ValueError(f"unknown OSM kind {k!r}; known: {', '.join(sorted(keys))}")
        by_key.setdefault(keys[k], []).append(k)
    parts = ""
    for key, values in by_key.items():
        if values == [key]:                       # a bare key, like `club`
            parts += f'nwr["{key}"]({bbox});'
        else:
            parts += f'nwr["{key}"~"^({"|".join(sorted(values))})$"]({bbox});'
    return f"[out:json][timeout:180];({parts});out tags center;"


def overpass_venues(session, bbox: str, name: str = "?",
                    endpoint: str = OVERPASS_ENDPOINT, quiet: bool = False,
                    kinds: Optional[Iterable[str]] = None):
    """Venues in the bbox that publish a website.

    Returns None if OVERPASS NEVER ANSWERED, and [] if it answered with nothing.
    Those are not the same fact and returning [] for both is what let a sweep
    report "Adelaide: 0 venues publish a website" for nine metros in a row that
    had simply never been asked — while the cursor advanced past all nine. The
    endpoint hands out a couple of slots and answers 429 or 504 when they are
    busy, which is normal traffic across a sweep and worth waiting out; a
    refusal after OVERPASS_ATTEMPTS tries, or OVERPASS_PATIENCE_S of trying,
    is the endpoint declining, and the metro is unread.
    """
    q = _overpass_query(bbox, kinds)
    last = OVERPASS_ATTEMPTS - 1
    t0 = time.monotonic()

    def patient(wait: float) -> bool:
        return time.monotonic() - t0 + wait <= OVERPASS_PATIENCE_S

    for attempt in range(OVERPASS_ATTEMPTS):
        wait = min(OVERPASS_BACKOFF_S * 3 ** attempt, OVERPASS_BACKOFF_CAP_S)
        try:
            r = session.post(endpoint, data=q.encode("utf-8"), timeout=200)
            if r.status_code in (429, 504) and attempt < last:
                wait = int(r.headers.get("Retry-After") or 0) or wait
                if patient(wait):
                    if not quiet:
                        print(f"  overpass {name}: {r.status_code}, retrying in {wait}s")
                    time.sleep(wait)
                    continue
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except Exception as exc:                                  # noqa: BLE001
            if attempt == last or not patient(wait):
                if not quiet:
                    print(f"  overpass {name} FAILED after {attempt + 1} ask(s), "
                          f"{time.monotonic() - t0:.0f}s: {type(exc).__name__}: {exc}")
                return None
            time.sleep(wait)
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


# An offsite link that cannot be anybody's calendar. Found by recording the
# LINK rather than the host and then reading 24 of them: Eventbrite's WordPress
# plugin puts `eventbrite.com/l/wordpress?ref=wpfooter` in the site footer, and
# the footer is on every page — so a venue using the plugin reads as "its events
# are on Eventbrite" whether or not they are. A bare host with no path is the
# same shape: a brand link, not a listing. Both cost more than a wrong tally,
# because `offsite:` parks the venue as dead for the ledger's 90-day TTL.
_OFFSITE_NOT_A_CALENDAR_RX = re.compile(r"^/(l/|$)", re.I)


def _offsite(u: str) -> Optional[str]:
    parts = urlparse(u)
    h = parts.netloc.lower()
    for host in OFFSITE_HOSTS:
        if host in h:
            if _OFFSITE_NOT_A_CALENDAR_RX.match(parts.path or "/"):
                return None
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


# WHERE A WIX SITE ROUTES ONE EVENT IS THE SITE'S CHOICE, NOT THE PLATFORM'S.
# to_candidate used to write `/event-info/{}` for every Wix find, which is the
# route of the older sites only. Measured 2026-10-04 on the 14 Wix sites in
# jsonld_sources.json: /event-info/ answers on 2 (Sea Monster Lounge, Remy's),
# /event-details/ on 9 and a renamed /events/ on 3, so 13 of the 15 entries
# were fetching 404s; and on the 6 community-centre seeds whose events page
# answered at all, /event-details/ on every one. So the route is READ off the
# page that fingerprinted as Wix, from its own links to the events it lists —
# a share link, an anchor, a URL in the warmup data, each ending in one of the
# slugs the page lists — and only guessed, as the newer default, when the page
# shows no such link.
#
# THE WHOLE PATH IN FRONT OF THE SLUG, not its last segment, because the
# adapter resolves the template against the listing's ORIGIN. A free Wix site
# lives at <user>.wixsite.com/<site>/, so its events are at
# /<site>/event-details/<slug>, and /event-details/<slug> on that host is a
# 404: centrecultureltheux.wixsite.com/cctheux (2026-10-04) lists 13 events,
# 12 upcoming, and the last-segment route fetched 404 for every one where the
# page's own link answered 200 with its Event block. The ledger holds 61
# wixsite.com hosts, 20 of them Wix candidates that failed verify with "no
# schema.org Event blocks found", every one of them asked at the host root.
# The same holds for a language prefix (/fr/evenements/<slug>): the link the
# site wrote is the one that answers.
#
# The slugs are taken from the EVENT objects in the warmup data (they carry
# `scheduling`), not from every `"slug":` on the page, because a Wix blog
# widget ships its posts the same way and routes them through /post/.
WIX_DEFAULT_ROUTE = "/event-details/{}"
_WIX_ROUTE_NAMES = ("event-details", "event-info")
_WIX_WARMUP_RX = re.compile(
    r'<script[^>]*id="wix-warmup-data"[^>]*>(.*?)</script>', re.S | re.I)
_WIX_SLUG_RX = re.compile(r'"slug":"([a-zA-Z0-9-]+)"')
# A link as written: `//host` and its path, or a path standing on its own (a
# relative link, which is the page's own). The lookbehind keeps a relative
# path from starting inside a word, so `ticketsource.com/x/<slug>` written
# without a scheme is not read as this site's /x/.
_WIX_HREF_RX = re.compile(
    r'(?:(?:https?:)?//([A-Za-z0-9.-]+)|(?<![A-Za-z0-9._~%-]))(/[A-Za-z0-9_.~%/-]*)')


def wix_events(body: str) -> List[Dict[str, Any]]:
    """The Wix Events objects a page ships in its warmup data, one per slug.

    Every one carries `slug`, `title`, `scheduling.config.startDate` and a
    `location`. [] when the page has no events widget, or one that renders
    client-side — which is most of the Wix finds that verify to nothing.
    """
    m = _WIX_WARMUP_RX.search(body or "")
    if not m:
        return []
    try:
        doc = json.loads(m.group(1))
    except ValueError:
        return []
    out: Dict[str, Dict[str, Any]] = {}
    stack: List[Any] = [doc]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("slug"), str) and isinstance(x.get("scheduling"), dict):
                out.setdefault(x["slug"], x)
                continue
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return list(out.values())


def _bare_host(h: str) -> str:
    h = (h or "").lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def wix_route(body: str, page_url: Optional[str] = None) -> Optional[str]:
    """`<path>/{}` — the route this Wix site's own links give its events.

    None when the page links none of them; the caller picks the default. A
    link counts only when a segment of it is a slug the page itself lists
    AND it sits on the page's own host, so a renamed events page
    (`/events/<slug>`, three of the configured entries) is read as readily as
    either Wix default, while a ticketer that reuses the slug is not: HEART
    Headingley's 13 events each carry an external registration link,
    ticketsource.com/heartcentreheadingley/<slug>, and without the host test
    that is the route this read. Everything in front of the slug is kept
    (see the block comment): `/cctheux/event-details/{}`, not the 404 that
    `/event-details/{}` is on a wixsite.com host.
    """
    text = (body or "").replace("\\/", "/")
    own = _bare_host(urlparse(page_url).netloc) if page_url else None
    slugs = {e["slug"] for e in wix_events(text)}
    if not slugs:
        # No warmup events: the page's own slugs are still the best key.
        slugs = set(_WIX_SLUG_RX.findall(text))
    votes: Dict[str, int] = {}
    named: Dict[str, int] = {}
    for m in _WIX_HREF_RX.finditer(text):
        if own and m.group(1) and _bare_host(m.group(1)) != own:
            continue
        segs = m.group(2).split("/")
        for i in range(2, len(segs)):
            route = "/".join(segs[:i]) + "/{}"
            if segs[i] in slugs:
                votes[route] = votes.get(route, 0) + 1
                break
            if segs[i] and segs[i - 1] in _WIX_ROUTE_NAMES:
                # No slug-anchored link: a link through either Wix route
                # is weaker evidence, and still better than the guess.
                named[route] = named.get(route, 0) + 1
                break
    votes = votes or named
    if not votes:
        return None
    return max(votes, key=lambda k: votes[k])


def wix_default_route(listing: str) -> str:
    """The newer Wix route, under the site's own path on a free wixsite.com
    host, where the origin is not the site (see the block comment)."""
    o = urlparse(listing or "")
    first = [p for p in o.path.split("/") if p][:1]
    if o.netloc.lower().endswith(".wixsite.com") and first:
        return f"/{first[0]}{WIX_DEFAULT_ROUTE}"
    return WIX_DEFAULT_ROUTE


# THE CONNECT HALF OF A PROBE'S TIMEOUT, separately. An unreachable venue is
# not a fast failure on this walk: on 2026-09-25 155 of the 163 sites probed
# in three Mexican metros were `unreachable`, and the run read those three in
# 62 minutes, ~23 s a site, so the 18 s timeout fired rather than DNS saying
# no. A host that has not taken a TCP connection in 6 s (three SYNs) is not
# coming back inside the read timeout either. The log does not say which
# timeout fired, so this bounds the connect half only; `unreachable` is not
# parked in the ledger, so a host this misses is asked again next sweep.
PROBE_CONNECT_S = 6


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
           "status": None, "offsite": None, "offsite_url": None, "extra": {}}
    try:
        r = session.get(home_url, timeout=(PROBE_CONNECT_S, timeout),
                        allow_redirects=True)
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

    onsite, offsite_hit, offsite_url = [], None, None
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        u = urljoin(base, href)
        if not u.startswith(("http://", "https://")):
            continue
        if not (CAL_LINK_RX.search(href) or CAL_LINK_RX.search(text)
                or any(h in urlparse(u).netloc.lower() for h in REC_BOOKING_HOSTS)):
            continue
        off = _offsite(u)
        if off:
            # THE URL, NOT JUST THE HOST. `offsite:eventbrite` names an adapter
            # this repo already has and cannot be acted on without the organizer
            # id, which is in the link and nowhere else — eventbrite.com/o/
            # <slug>-<id>. Recording the host alone made 46 venues a statistic
            # instead of 46 candidates, and re-finding each one costs the fetch
            # again. First hit wins, matching the host rule it replaces.
            if offsite_hit is None:
                offsite_hit, offsite_url = off, u
        elif _same_host(u, base):
            onsite.append(u)

    for u in sorted(dict.fromkeys(onsite), key=_rank)[:max_follow]:
        try:
            r2 = session.get(u, timeout=(PROBE_CONNECT_S, timeout),
                             allow_redirects=True)
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
            if "wix" in labels:
                # Read here because the page is in hand; to_candidate never
                # sees it. The homepage is the second witness, not a fetch.
                route = wix_route(r2.text, cal_url) or wix_route(body, base)
                if route:
                    out["extra"]["wix_route"] = route
                # A WIX EVENT'S OWN PAGE IS ONE EVENT, and a homepage that
                # features events links straight to them, so this is where
                # the walk lands: 13 of the 15 configured Wix entries had
                # one event page as their `listing` (2026-10-04), each a
                # source that dies with that event. The homepage that linked
                # it ships the list it was picked from — 11, 13, 18 and 20
                # upcoming on four of them — and is already in hand.
                # _prefer_listing cannot find it: Wix answers the parent
                # path, /event-details/, with a page that indexes nothing.
                # The same for a page that ships NO events: `wix-events` is
                # on every page of a site with the app, so a link that says
                # "programma" passes the fingerprint with nothing behind it
                # (teatrodelburatto.com/ilgiardinodellestorie: 0 events, its
                # homepage 20) — unless the page carries Event blocks of its
                # own, which the adapter reads off the listing without a slug.
                here = wix_events(r2.text)
                one = (len(here) == 1 and here[0]["slug"]
                       == urlparse(cal_url).path.rstrip("/").rsplit("/", 1)[-1])
                if ((one or (not here and "jsonld-event" not in labels))
                        and wix_events(body)):
                    cal_url = base
            if adapter == "jsonld" and cal_url == str(r2.url):
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
        route = wix_route(body, base) if "wix" in home_labels else None
        if route:
            out["extra"]["wix_route"] = route
        out.update(cal_url=out["cal_url"] or base, labels=home_labels,
                   ics=home_ics or constructed_feed(session, base, home_labels),
                   adapter=adapter_for(home_labels), status="ok-homepage")
        return out
    if offsite_hit:
        out.update(status=f"offsite:{offsite_hit}", offsite=offsite_hit,
                   offsite_url=offsite_url)
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
        # NO `venue` BLOCK, unlike the adapters below, and on purpose. An ics
        # source's venue pins every event that names no place, and a calendar
        # found through a venue is often not that venue's programme. Of the 9
        # OSM-found Google calendars whose events carried no LOCATION at all
        # (2026-09-30), 4 were right to pin. The rest were a diocese's whole
        # agenda, two scout troops' trips, and a rowing club's meetings in
        # Lausanne and camp in St. Moritz. Pin by hand, from the events.
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
            # set as {"slug": ...} and routes each one through a page of its
            # own. A WordPress-shaped /event/<slug>/ pattern matches none of it,
            # which is how four London candidates were proposed with a
            # link_pattern that could never fire. WHICH page is the site's: read
            # off its own links by find_calendar (see wix_route), the newer
            # default when it linked none.
            return dict(common, type="jsonld", listing=[cal],
                        link_pattern=r'"slug":"([a-zA-Z0-9-]+)"',
                        url_template=(found.get("extra") or {}).get("wix_route")
                        or wix_default_route(cal),
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
                            # The country SPELLED OUT, carried beside the code
                            # because the two are read by different things: the
                            # cursor keys on the code (metro_key), and the
                            # geocode suffix a candidate ships with has to be
                            # readable by a geocoder and by the coverage report.
                            # ", Zurich, CH" was neither — Photon has to guess at
                            # it, and catalog_curate read the code itself as the
                            # METRO and filed 20 Swiss venue calendars under the
                            # United States. metros_global.json has carried the
                            # name all along.
                            "country_name": c.get("name"),
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
                        "country_name": "United States",
                        "bbox": _bbox_from(latlong, 25.0)})
    # Stable, so a tie inside one country keeps the order the config wrote down.
    out.sort(key=lambda m: CATALOG_SOURCES.get(m.get("country"), 0))
    return out
