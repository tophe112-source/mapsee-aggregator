#!/usr/bin/env python3
"""
test_ingest_drupal_fullcalendar.py - a Drupal fullcalendar_view calendar
(Fairfax County, VA), and the ways its events are not where they look.

Every page in PAGES is a real Fairfax County page read on 2026-10-04, trimmed to
the event block (the theme's columns and the directions text cut); the calendar
rows are real rows from that day's drupalSettings; synthetic cases are built in
code and say so. The expensive failures are quiet ones: every event pinned on
the geolocation widget's default centre (the Government Center), a row sent with
only the county's name for a place, a fee row tagged free (or whose price line
the sync's 800-character cut removes), a Zoom training on a map, a
registration-only class whose blurb says so, a group-only booking on a
placeholder clock, a pin "happening now" until 11 pm, one failing page losing a
whole source, and the second of two same-day sessions merging into the first.
One check needs the sync to treat 'drupal-fullcalendar:' as a civic
timetable (CIVIC_TIMETABLE_SOURCES); it fails until that line is there.

Section 10 is OC Parks (Orange County, CA), the 5.x tenant, from real pages of
2026-10-11: a row keyed on the view's row number instead of its node, a blurb
read from the accessibility block ("Body"), an open space's one address pinned
under hikes that start 2.6 to 11.2 km away, a start line in the blurb ignored
for the park's address, a cost sentence cut by the 800, a free walk-in bird
walk told it needs "Tickets", a capped list read as complete, and a member of
staff's name, direct line and own email address carried into a bird walk's row.

    python test_ingest_drupal_fullcalendar.py
"""
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261004"

from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_drupal_fullcalendar as D  # noqa: E402
from mapsee_ingest import EventStore  # noqa: E402
import mapsee_supabase_sync as SYNC  # noqa: E402
from mapsee_supabase_sync import MAPSEE_CATEGORY_KEYS, _addr_parts, derive_categories  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CFG = D.load_config(os.path.join(HERE, "drupal_fullcalendar_sources.json"))
SRC = next(s for s in CFG["sources"] if s["key"] == "fairfax-county")
TZ = ZoneInfo(SRC["timezone"])

# ../mapsee migration 0227's strict `free` and its veto, the alternatives a
# price line or these blurbs could hit (a subset of tools/measure_deals.py's
# FREE / FREE_NEG, as test_ingest_toronto_rec.py keeps it, so the gate stays
# offline).
FREE_TAG = re.compile(
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|join|enter|"
    r"participate|the\s+public|all|everyone)|and\s+open|for\s+(?:all|everyone|kids|children|the\s+public|"
    r"families)|community\s+event|concert|show|screening|class|classes|workshop|tour|tours|tasting|"
    r"session|lesson|drop[- ]in|museum|comedy|yoga|rsvp|with\s+(?:rsvp|registration|admission|entry)|"
    r"tickets?|family\s+(?:day|fun))\b|\bat\s+no\s+cost\b|\bfree\s*!|"
    r"\b(?:admission|entry|entrance|attendance|cost|price)\s*(?:is|:|-|–)?\s*free\b|"
    r"\bis\s+free\b(?!\s+(?:of|from|to\s+(?:use|choose)))", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b", re.I)


def FREE_TAG_NEG_OK(name, description):
    body = (name or "") + "\n" + (description or "")
    return bool(FREE_TAG.search(body)) and not FREE_NEG.search(body)


def tagged_free(ev):
    return FREE_TAG_NEG_OK(ev.name, ev.description)


# Real pages, trimmed to the event block (2026-10-04).
PAGES = {
    'burke_lake': '<div class="row header">\n<div class="col-md-6" style="padding-left: 0px;">\n<div class="headerRowDetails mainpage" style="padding-bottom: 0;">\n<h6 class="event_type label">Community Event</h6>\n<div class="page-title">\n<h1>Bird Walk With a Naturalist</h1>\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_date label">Date & Time</div>\n<div class="event_date value">11/06/2026 - 5:00 pm to 6:00 pm</div>\n<!-- CHILD EVENTS -->\n<div class="child-events-container" style="padding-left: 0px;">\n</div>\n<!-- END CHILD EVENTS -->\n</div>\n</div>\n<div class="hidden startDate">\n2026-11-06T17:00:00\n</div>\n<div class="hidden endDate">\n2026-11-06T18:00:00\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_cost label">Price Registration</div>\n<div class="event_cost value">$9.00</div>\n</div>\n</div>\n<div class="row event_location" style="padding-bottom: 0;">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px; padding-bottom: 0;">\n<div class="label">Location</div>\n<div class="value" style="padding-bottom: 0;">\n<div class="event_location_address">\n<div>Burke Lake Park Location</div>\n<div>7315 Ox Road</div>\n<div>Fairfax Station, VA 22039\t</div>\n</div>\n</div>\n</div>\n</div>\n</div>\n</div>\n<div class="col-md-6">\n<div class="row">\n<div class="col-md-12">\n<div class="event_image" >\n<img src="/theme-images/seal.png" alt="Fairfax County Seal" title="Fairfax County Seal">\n</div>\n</div>\n<div class="col-md-12 d-flex justify-content-center">\n\n</div>\n</div>\n</div>\n<div class="clearboth">&nbsp;</div>\n<div class="eventcontent">\n<div class="event_description_content">\n<div class="clearboth">\n<div class="card">\n<div class="event_description">\n<div class="event_description_content_inner">\n<p>(6-Adult) Use your eyes and ears to identify native birds as you embark on a guided walk with one of our naturalists. Both beginner birders and veterans welcome. Bring your own binoculars or use one of ours.&nbsp;</p>\n</div>\n</div>\n</div>\n</div>\n<p class="registration_link">\n<b>Registration:</b>\n<a href="https://fairfax.usedirect.com/FairfaxFCPAWeb/Activities/Details.aspx?session_id=593392&amp;back_url=fi9BY3Rpdml0aWVzL1NlYXJjaC5hc3B4" >Register Online</a>\n</p>\n</div>\n<div class="event_body_location">\n<hr >\n<div id="event_addtocalendar">Add to calendar</div>\n</div>\n<div class="event_body_location">\n<hr>\n<p>\n<article data-history-node-id="411" about="/parks/burke-lake-park-location" class="location map-and-address clearfix">\n<div class="content">\n<div class="field field--name-field-geolocation field--type-geolocation field--label-hidden field--item"><div  class="geolocation-map-wrapper" id="map-6ac259c69bffa" data-map-type="leaflet" data-centre-lat="38.853854" data-centre-lng="-77.356967">\n<div class="geolocation-map-controls">\n</div>\n<div class="geolocation-map-container js-show"></div>\n<div  class="geolocation-location js-hide" id="6ac259c6d1c35" data-lat="38.7607784" data-lng="-77.306883" data-set-marker="true" typeof="Place">\n<span property="geo" typeof="GeoCoordinates">\n<meta property="latitude" content="38.7607784" />\n<meta property="longitude" content="-77.306883" />\n</span>\n<h2 class="location-title" property="name">Click to view in Google Maps</h2>\n<div class="location-content"><p><a href="https://www.google.com/maps/place/38.7607784,-77.306883" target="_blank">Click to view in Google Maps</a></p></div>\n</div>\n</div>\n</div>\n<h3>\n<a href="/parks/burke-lake-park-location" rel="bookmark">\n<span>Burke Lake Park Location</span>\n</a>\n</h3>\n7315 Ox Road<br>\nFairfax Station,\nVA,\n22039<br>\n<a href="https://www.google.com/maps/search/?api=1&query=%207315 Ox Road%20Fairfax Station%20VA%2022039" target="_blanks" aria-label="Link to Google Maps - Burke Lake Park Location">Link to Google Maps</a>\n<br>\n<i><p><a href="/parks/sites/parks/files/Assets/documents/picnics/maps/burke-lake-park.pdf">Map of Burke Lake Park Reservable Areas</a></p>\n</i>\n</div>\n</article>\n</p>\n</div>\n</div>\n</section>\n</div>\n</div>\n',
    'government_center': '<div class="row header">\n<div class="col-md-6" style="padding-left: 0px;">\n<div class="headerRowDetails mainpage" style="padding-bottom: 0;">\n<h6 class="event_type label">Public Meeting</h6>\n<div class="page-title">\n<h1>Board of Supervisors Audit Committee Meeting: Oct. 6, 2026</h1>\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_date label">Date & Time</div>\n<div class="event_date value">10/06/2026 - 9:30 am</div>\n<!-- CHILD EVENTS -->\n<div class="child-events-container" style="padding-left: 0px;">\n</div>\n<!-- END CHILD EVENTS -->\n</div>\n</div>\n<div class="hidden startDate">\n2026-10-06T09:30:00\n</div>\n<div class="hidden endDate">\n2026-10-06T09:30:00\n</div>\n<div class="row event_location" style="padding-bottom: 0;">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px; padding-bottom: 0;">\n<div class="label">Location</div>\n<div class="value" style="padding-bottom: 0;">\n<div class="event_location_address">\n<div>Fairfax County Government Center</div>\n<div>12000 Government Center Parkway</div>\n<div>Fairfax, VA 22035\t</div>\n</div>\n</div>\n</div>\n</div>\n</div>\n</div>\n<div class="col-md-6">\n<div class="row">\n<div class="col-md-12">\n<div class="event_image" >\n<img src="/theme-images/seal.png" alt="Fairfax County Seal" title="Fairfax County Seal">\n</div>\n</div>\n<div class="col-md-12 d-flex justify-content-center">\n\n</div>\n</div>\n</div>\n<div class="clearboth">&nbsp;</div>\n<div class="eventcontent">\n<div class="event_description_content">\n<div class="clearboth">\n<div class="card">\n<div class="event_description">\n<div class="event_description_content_inner">\n<p>The Board of Supervisors <a href="https://www.fairfaxcounty.gov/boardofsupervisors/audit-committee">Audit Committee</a> is scheduled to meet at 9:30 a.m. on Oct. 6, 2026, at the Fairfax County Government Center (Conference Room 11).</p><h2>Meeting Materials</h2><ul><li><a href="/boardofsupervisors/sites/boardofsupervisors/files/Assets/documents/AC-Meeting-Agenda-October-2026.pdf" target="_blank">Agenda</a></li><li><a href="/boardofsupervisors/sites/boardofsupervisors/files/Assets/documents/AC-Meeting-Minutes-July-7-2026.pdf" target="_blank">Meeting Minutes - July 7, 2026</a></li><li><a href="/boardofsupervisors/sites/boardofsupervisors/files/Assets/documents/AC-Presentation-Oct-2026.pdf" target="_blank">Audit Committee Meeting Presentation </a></li><li><a href="/boardofsupervisors/sites/boardofsupervisors/files/Assets/documents/Evaluating-the-Restaurant-Permitting-Process-Study-Draft.pdf" target="_blank">FY27 Evaluating the Restaurant Permitting Process Study</a></li></ul>\n</div>\n</div>\n</div>\n</div>\n</div>\n<div class="event_body_location">\n<hr >\n<div id="event_addtocalendar">Add to calendar</div>\n</div>\n<div class="event_body_location">\n<hr>\n<p>\n<article data-history-node-id="41" about="/boardofsupervisors/location/fairfax-county-government-center" class="location map-and-address clearfix">\n<div class="content">\n<div class="field field--name-field-geolocation field--type-geolocation field--label-hidden field--item"><div  class="geolocation-map-wrapper" id="map-6abf6e8bd7ede" data-map-type="leaflet" data-centre-lat="38.853854" data-centre-lng="-77.356967">\n<div class="geolocation-map-controls">\n</div>\n<div class="geolocation-map-container js-show"></div>\n<div  class="geolocation-location js-hide" id="6abf6e8c0f79e" data-lat="38.853854" data-lng="-77.356967" data-set-marker="true" typeof="Place">\n<span property="geo" typeof="GeoCoordinates">\n<meta property="latitude" content="38.853854" />\n<meta property="longitude" content="-77.356967" />\n</span>\n<h2 class="location-title" property="name">Click to view in Google Maps</h2>\n<div class="location-content"><p><a href="https://www.google.com/maps/place/38.853854,-77.356967" target="_blank">Click to view in Google Maps</a></p></div>\n</div>\n</div>\n</div>\n<h3>\n<a href="/boardofsupervisors/location/fairfax-county-government-center" rel="bookmark">\n<span>Fairfax County Government Center</span>\n</a>\n</h3>\n12000 Government Center Parkway<br>\nFairfax,\nVA,\n22035<br>\n<a href="https://www.google.com/maps/search/?api=1&query=%2012000 Government Center Parkway%20Fairfax%20VA%2022035" target="_blanks" aria-label="Link to Google Maps - Fairfax County Government Center">Link to Google Maps</a>\n</div>\n</article>\n</p>\n</div>\n<div class="widget callout singlecolorblock theme-grayblue noimg">\n<div class="image image image-fill" style="background-image:url(\'/boardofsupervisors/sites/boardofsupervisors/files/Assets/images/committee-meeting-small-update.jpg\');">\n</div>\n<div class="content right">\n<div class="row">\n<div class="col-12 col-md-12">\n<ul class="nav nav-bordered">\n<li class="nav-item">\n<a class="nav-link" href="https://www.fairfaxcounty.gov/boardofsupervisors/2026-board-meetings">2026 Board &amp; Committee Meetings Schedule</a>\n</li>\n<li class="nav-item">\n<a class="nav-link" href="/boardofsupervisors/board-supervisors-meetings-archive"> Board &amp; Committee Meetings Archive</a>\n</li>\n<li class="nav-item">\n<a class="nav-link" href="/boardofsupervisors/board-meeting-summaries">Official Board Meeting Summaries</a>\n</li>\n<li class="nav-item">\n<a class="nav-link" href="/boardofsupervisors/../calendar/?c=1">Public Meetings Calendar</a>\n</li>\n<li class="nav-item">\n<a class="nav-link" href="/boardofsupervisors/../boardofsupervisors/about-board-meetings">About Board Meetings</a>\n</li>\n</ul>\n</div>\n</div>\n</div>\n</div>\n</div>\n</section>\n</div>\n</div>\n',
    'sherwood': '<div class="row header">\n<div class="col-md-6" style="padding-left: 0px;">\n<div class="headerRowDetails mainpage" style="padding-bottom: 0;">\n<h6 class="event_type label">Community Event</h6>\n<div class="page-title">\n<h1>Smart Strategies to Protect Yourself from Scams</h1>\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_date label">Date & Time</div>\n<div class="event_date value">10/12/2026 - 11:00 am to 12:00 pm</div>\n<!-- CHILD EVENTS -->\n<div class="child-events-container" style="padding-left: 0px;">\n</div>\n<!-- END CHILD EVENTS -->\n</div>\n</div>\n<div class="hidden startDate">\n2026-10-12T11:00:00\n</div>\n<div class="hidden endDate">\n2026-10-12T12:00:00\n</div>\n<div class="row event_location" style="padding-bottom: 0;">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px; padding-bottom: 0;">\n<div class="label">Location</div>\n<div class="value" style="padding-bottom: 0;">\n<div class="event_location_address">\nSherwood Hall Library<br>\n2501 Sherwood Hall Ln<br>\nAlexandria,\nVA\n22306\n</div>\n</div>\n</div>\n</div>\n</div>\n</div>\n<div class="col-md-6">\n<div class="row">\n<div class="col-md-12">\n<div class="event_image" >\n<img src="/theme-images/seal.png" alt="Fairfax County Seal" title="Fairfax County Seal">\n</div>\n</div>\n<div class="col-md-12 d-flex justify-content-center">\n\n</div>\n</div>\n</div>\n<div class="clearboth">&nbsp;</div>\n<div class="eventcontent">\n<div class="event_description_content">\n<div class="clearboth">\n<div class="card">\n<div class="event_description">\n<div class="event_description_content_inner">\n<p>Scammers will say or do almost anything to get your money. From common fraud schemes and early warning signs to smart strategies for protecting your personal information and finances, you’ll gain practical tools to stay one step ahead.</p><p>Learn how to safeguard yourself, protect your money, and navigate today’s scams with greater confidence. The Fairfax County Silver Shield Anti-Scam Program is dedicated to protecting the community, especially older adults, people with disabilities, and family caregivers—from scams and fraud. <a href="https://librarycalendar.fairfaxcounty.gov/event/17539044" target="_blank"><strong>Register today</strong></a>!</p>\n</div>\n</div>\n</div>\n</div>\n</div>\n<div class="event_body_location">\n<hr >\n<div id="event_addtocalendar">Add to calendar</div>\n</div>\n<div class="event_body_location">\n<hr>\n<p>\n</p>\n</div>\n</div>\n</section>\n</div>\n</div>\n',
    'no_location': '<div class="row header">\n<div class="col-md-6" style="padding-left: 0px;">\n<div class="headerRowDetails mainpage" style="padding-bottom: 0;">\n<h6 class="event_type label">Community Event</h6>\n<div class="page-title">\n<h1>Fall Color Snap Sketch Hike</h1>\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_date label">Date & Time</div>\n<div class="event_date value">10/24/2026 - 10:00 am to 12:00 pm</div>\n<!-- CHILD EVENTS -->\n<div class="child-events-container" style="padding-left: 0px;">\n</div>\n<!-- END CHILD EVENTS -->\n</div>\n</div>\n<div class="hidden startDate">\n2026-10-24T10:00:00\n</div>\n<div class="hidden endDate">\n2026-10-24T12:00:00\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_cost label">Price Registration</div>\n<div class="event_cost value">$18.00</div>\n</div>\n</div>\n</div>\n</div>\n<div class="col-md-6">\n<div class="row">\n<div class="col-md-12">\n<div class="event_image" >\n<img src="/theme-images/seal.png" alt="Fairfax County Seal" title="Fairfax County Seal">\n</div>\n</div>\n<div class="col-md-12 d-flex justify-content-center">\n\n</div>\n</div>\n</div>\n<div class="clearboth">&nbsp;</div>\n<div class="eventcontent">\n<div class="event_description_content">\n<div class="clearboth">\n<div class="card">\n<div class="event_description">\n<div class="event_description_content_inner">\n<p>(10-Adult) Join us for a hike and learn how to draw a beautiful autumn scene.</p>\n</div>\n</div>\n</div>\n</div>\n<p class="registration_link">\n<b>Registration:</b>\n<a href="https://fairfax.usedirect.com/FairfaxFCPAWeb/Activities/Details.aspx?session_id=599158&amp;back_url=fi9BY3Rpdml0aWVzL1NlYXJjaC5hc3B4" >Register Online</a>\n</p>\n</div>\n<div class="event_body_location">\n<hr >\n<div id="event_addtocalendar">Add to calendar</div>\n</div>\n<div class="event_body_location">\n<hr>\n<p>\n</p>\n</div>\n</div>\n</section>\n</div>\n</div>\n',
    'zoom_training': '<div class="row header">\n<div class="col-md-6" style="padding-left: 0px;">\n<div class="headerRowDetails mainpage" style="padding-bottom: 0;">\n<h6 class="event_type label">Community Event</h6>\n<div class="page-title">\n<h1>QPR Suicide Prevention Training - October 13, 2026</h1>\n</div>\n<div class="row">\n<div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n\n</div>\n<div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n<div class="event_date label">Date & Time</div>\n<div class="event_date value">10/13/2026 - 1:00 pm to 2:30 pm</div>\n<!-- CHILD EVENTS -->\n<div class="child-events-container" style="padding-left: 0px;">\n</div>\n<!-- END CHILD EVENTS -->\n</div>\n</div>\n<div class="hidden startDate">\n2026-10-13T13:00:00\n</div>\n<div class="hidden endDate">\n2026-10-13T14:30:00\n</div>\n</div>\n</div>\n<div class="col-md-6">\n<div class="row">\n<div class="col-md-12">\n<div class="event_image" >\n<img src="/community-services-board/sites/community-services-board/files/Assets/images/qpr-button-600x600.png" alt="The words &#039;QPR (Question, Persuade, and Refer) Suicide Prevention Training&#039;." title="Join us for QPR Training">\n</div>\n</div>\n<div class="col-md-12 d-flex justify-content-center">\n\n</div>\n</div>\n</div>\n<div class="clearboth">&nbsp;</div>\n<div class="eventcontent">\n<div class="event_description_content">\n<div class="clearboth">\n<div class="card">\n<div class="event_description">\n<div class="event_description_content_inner">\n<p><em>Ask a Question, Save A Life.</em> There are three steps anyone can take to help prevent suicide. You can be a resource for someone who might be struggling. Let’s make sure everyone in our community knows how much they matter.</p><p><a href="/community-services-board/prevention/qpr"><img src="/community-services-board/sites/community-services-board/files/Assets/images/QPR-training-header.png" data-entity-uuid="" data-entity-type="" alt="Photo of woman holding chalkboard that says &quot;Empower your community&quot; - QPR training" width="800" height="200"></a></p><p><a href="/community-services-board/prevention/qpr" title="QPR Suicide Prevention Training"><strong>Question, Persuade, Refer</strong></a> (QPR) virtual training is offered free to the community for anyone ages 16 and older.</p><p>Learn to recognize the warning signs, how to intervene, and where to refer someone who is in crisis. Are you QPR ready?</p>\n</div>\n</div>\n</div>\n</div>\n<p class="registration_link">\n<b>Registration:</b>\n<a href="https://us02web.zoom.us/meeting/register/hjOWWZSXThOUPbLc6VVsLg" >Sign up to attend this virtual session</a>\n</p>\n</div>\n<div class="event_body_location">\n<hr >\n<div id="event_addtocalendar">Add to calendar</div>\n</div>\n<div class="event_body_location">\n<hr>\n<p>\n</p>\n</div>\n</div>\n</section>\n</div>\n</div>\n',
    # Animal Services' Reading Tails: no visible Date & Time, only a hidden
    # startDate, and a group-only Calendly booking.
    'reading_tails': '<div class="row header">\n <div class="col-md-6" style="padding-left: 0px;">\n <div class="headerRowDetails mainpage" style="padding-bottom: 0;">\n <h6 class="event_type label">Community Event</h6>\n <div class="page-title">\n <h1>October Reading Tails | Lorton Campus</h1>\n </div>\n <div class="hidden startDate">\n 2026-10-29T18:00:00\n </div>\n <div class="hidden endDate">\n 2026-10-29T19:00:00\n </div>\n <div class="row"> \n <div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n  </div>\n <div class="col-md-11 col-sm-unset" style="padding-left: 5px;">\n <div class="event_cost label">Price Registration</div>\n <div class="event_cost value">$.00 - Free</div>\n </div>\n </div>\n <div class="row event_location" style="padding-bottom: 0;"> \n <div class="col-md-1 col-sm-unset" style="padding-left: 0px;">\n  </div>\n <div class="col-md-11 col-sm-unset" style="padding-left: 5px; padding-bottom: 0;">\n <div class="label">Location</div>\n <div class="value" style="padding-bottom: 0;">\n <div class="event_location_address">\n <div>Fairfax County Animal Services — Lorton Campus</div>\n <div>8875 Lorton Road</div>\n <div>Lorton, VA 22079 </div>\n </div>\n </div>\n </div>\n </div>\n </div>\n </div>\n <div class="clearboth">&nbsp;</div>\n <div class="eventcontent">\n <div class="event_description_content">\n <div class="clearboth"> \n <div class="card">\n <div class="event_description">\n <div class="event_description_content_inner">\n <p>Reading Tails is a fun and engaging program where kids learn about FCAS and improve their reading skills by sharing bedtime stories with shelter pets! The program includes a behind‑the‑scenes tour of the shelter and plenty of time for participating kids to read a bedtime story to a shelter pet of their choosing, whether it’s a dog, cat, rabbit, guinea pig, or even a mouse.</p><p>Reading Tails is offered on various evenings at both our campuses. <strong>This event page is for our Lorton Campus sessions. Be sure to check the exact date and time in the registration link below.</strong></p><h2>Registration Information</h2><p><strong>Registration is required to participate in the program.</strong> Reading Tails is currently open for group registrations only (e.g., Girl Scout troops, school groups, or similar organizations). <strong>Groups must include 8 to 20 total attendees</strong>, including children and chaperones. For safety reasons, we are unable to accommodate groups larger than 20 people.</p><ul><li>Registration: To register your group or family, please choose the campus you want to attend.</li><li>What to Bring: Kids just need to bring their favorite books and a flashlight.</li></ul><p>If there are no available dates for your preferred month, it means those sessions are currently full and additional spots will not open. Please check back for future months as new sessions are added.</p>\n </div>\n </div>\n </div>\n </div>\n <p class="registration_link">\n <b>Registration:</b> \n <a href="https://calendly.com/dascommunications-fairfaxcounty/reading-tails-lorton-campus-2026" >Register Here</a>\n </p>\n </div>\n <div class="event_body_location">\n <hr >\n <div id="event_addtocalendar">Add to calendar</div>\n </div>\n <div class="event_body_location">\n <hr>\n <p>\n <article data-history-node-id="234" about="/animalservices/fairfax-county-animal-services-lorton-campus" class="location full clearfix">\n <div class="content">\n <p>\n <div class="field field--name-field-geolocation field--type-geolocation field--label-hidden field--item"><div class="geolocation-map-wrapper" id="map-6a98cd98c339a" data-map-type="leaflet" data-centre-lat="38.853854" data-centre-lng="-77.356967">\n <div class="geolocation-map-controls">\n </div>\n <div class="geolocation-map-container js-show"></div>\n <div class="geolocation-location js-hide" id="6a98cd99bbe08" data-lat="38.706566" data-lng="-77.252769" data-set-marker="true" typeof="Place">\n <span property="geo" typeof="GeoCoordinates">\n <meta property="latitude" content="38.706566" />\n <meta property="longitude" content="-77.252769" />\n </span>\n <h2 class="location-title" property="name">Click to view in Google Maps</h2>\n <div class="location-content"><p><a href="https://www.google.com/maps/place/38.706566,-77.252769" target="_blank">Click to view in Google Maps</a></p></div>\n </div>\n </div>\n</div>\n </p>\n 8875 Lorton Road<br>\n Lorton, \n VA, \n 22079\n </div>\n</article>',
}
# Real blurbs (2026-10-04) of two kept-then rows whose own words say "class".
BLURB_LITTLE_HANDS = '(2-3 yrs.) Join us to learn about a different element of farm life each week. Classes held during the same week will cover repeat topics. Other activities can include story time, crafts and small farm chores. One adult must attend with each child. Please limit one parent to attend with each child.'
BLURB_FORAGING = '(9-Adult) With every season there are different blooms, leaves, roots, seeds and mushrooms that you can identify and eat. Learn how to eat from the forest safely and ethically as you join a Naturalist on a walk by the Lake. Please be aware that foraging is prohibited in Fairfax County parks. This class will focus on how to safely identify various edible plants, as well as their uses. No plants will be picked during the course of the class.'
# Real rows from the calendar's drupalSettings (2026-10-04), descriptions cut to 160 characters.
CALENDAR_ROWS = json.loads('[{"title": "Board of Supervisors Meeting: Oct. 13, 2026", "description": "10/13 9:30am", "id": "32403", "url": "/events/boardofsupervisors/oct-13-2026-meeting", "fulldescriptionimage": null, "fulldescription": "The Board of Supervisors will meet at 9:30 a.m. on Oct. 13, 2026, in the Board Auditorium of the Fairfax County Government Center.Meeting MaterialsDraft AgendaF", "eventtype": "Public Meeting", "editable": false, "start": "2026-10-13T09:30:00-04:00", "end": "2026-10-13T09:30:00-04:00", "backgroundColor": "#3a87ad"}, {"title": "QPR Suicide Prevention Training - October 13, 2026", "description": "10/13 1:00pm", "id": "32373", "url": "/events/community-services-board/events/2026-10-13-qpr", "fulldescriptionimage": null, "fulldescription": "QPR stands for Question, Persuade, and Refer—3 simple yet powerful steps anyone can learn to help save a life from suicide.", "eventtype": "Community Event", "editable": false, "start": "2026-10-13T13:00:00-04:00", "end": "2026-10-13T14:30:00-04:00", "backgroundColor": "#3a87ad"}, {"title": "Bird Walk With a Naturalist", "description": "11/6 5:00pm", "id": "32367", "url": "/events/parks/burke-lake/bird-walk-naturalist/110626", "fulldescriptionimage": null, "fulldescription": "(6-Adult) Use your eyes and ears to identify native birds as you embark on a guided walk with one of our naturalists. Both beginner birders and veterans welcome", "eventtype": "Community Event", "editable": false, "start": "2026-11-06T17:00:00-05:00", "end": "2026-11-06T18:00:00-05:00", "backgroundColor": "#3a87ad"}, {"title": "Botanical Journaling", "description": "10/7 1:30pm", "id": "32160", "url": "/events/parks/green-spring/botanical-journaling/100726", "fulldescriptionimage": null, "fulldescription": "(16-Adult) Create your own botanical journal. Practice drawing exercises, take a seasonal walk for inspiration, and learn tips for layout and media options. Sta", "eventtype": "Community Event", "editable": false, "start": "2026-10-07T13:30:00-04:00", "end": "2026-10-21T15:30:00-04:00", "backgroundColor": "#3a87ad"}, {"title": "Cider Making (Class)", "description": "10/10 11:00am", "id": "32123", "url": "/events/parks/colvin-run-mill/cider-making/101026", "fulldescriptionimage": null, "fulldescription": "<?xml encoding=\\"utf-8\\" ?><p>(All Ages) Bring up to eight people and learn how to use an old-fashioned cider press by making your own cider. Your group needs to ", "eventtype": "Community Event", "editable": false, "start": "2026-10-10T11:00:00-04:00", "end": "2026-10-10T16:00:00-04:00", "backgroundColor": "#3a87ad"}]')
CALENDAR_PAGE = ('<script type="application/json" data-drupal-selector="drupal-settings-json">'
                 + json.dumps({"path": {"baseUrl": "/events/"}, "fullCalendarView": CALENDAR_ROWS}) + "</script>")


def settings_page(fcv):
    return ('<html><head><script type="application/json" data-drupal-selector="drupal-settings-json">'
            + json.dumps({"path": {"baseUrl": "/events/"}, "fullCalendarView": fcv}) + "</script></head></html>")


def row(**over):
    base = {"title": "Bird Walk With a Naturalist", "id": "32001", "url": "/events/parks/burke-lake/bird-walk-naturalist/110626",
            "eventtype": "Community Event", "start": "2026-11-06T17:00:00-05:00", "end": "2026-11-06T18:00:00-05:00",
            "description": "11/06 5:00pm", "fulldescription": "(6-Adult) Use your eyes and ears ..."}
    base.update(over)
    return base


def build(ev, page, src=SRC):
    stats = {}
    out = D.build_event(ev, D.parse_event_page(page), "https://www.fairfaxcounty.gov" + D.re.sub("^/events/", "/", ev["url"]),
                        src, TZ, stats)
    return out, stats


# ---------------------------------------------------------------------------
# 1. the calendar's JSON, in both shapes the module has shipped
# ---------------------------------------------------------------------------
evs = D.calendar_events(CALENDAR_PAGE)
check("8.x-2.x: drupalSettings.fullCalendarView IS the event list (Fairfax's live shape)",
      len(evs) == len(CALENDAR_ROWS) and evs[0]["title"] == CALENDAR_ROWS[0]["title"], len(evs))
five = settings_page({"0": {"calendar_options": json.dumps({"events": CALENDAR_ROWS[:3], "locale": "en"}),
                            "entityType": "node"}})
check("5.x: one entry per view, its events inside calendar_options (a JSON string) - read too (synthetic)",
      [e["id"] for e in D.calendar_events(five)] == [e["id"] for e in CALENDAR_ROWS[:3]])
try:
    D.calendar_events(settings_page(None).replace('"fullCalendarView": null', '"other": 1'))
    check("a page that stopped carrying its events is an ERROR, not an empty calendar", False)
except RuntimeError:
    check("a page that stopped carrying its events is an ERROR, not an empty calendar", True)

# ---------------------------------------------------------------------------
# 2. refusals before any page is asked for (every one counted by the caller)
# ---------------------------------------------------------------------------
cases = [
    ("Board of Supervisors Meeting: Oct. 13, 2026", "Public Meeting", "eventtype Public Meeting"),
    # Synthetic: the same real titles with the type left blank (11 of the
    # window's 195 rows carry an empty eventtype) must still be refused.
    ("Board of Supervisors Audit Committee Meeting: Oct. 6, 2026", "", "governance meeting"),
    ("CSB Fiscal Oversight Committee Meeting - October 21, 2026", "", "governance meeting"),
    ("UPDATE: Event is SOLD OUT - Shot in the Dark (Shotgun Start)", "Community Event", "sold out"),
    ("Virtual Opioid Overdose & Naloxone Education Training - October 5, 2026", "Community Event", "online"),
    ("Martes, 13 de Octubre – Café Virtual Para Padres Gratuito: Hable, ellos escuchan", "Community Event", "online"),
    ("Foster Care Monthly Information Meeting (Virtual)", "Community Event", "online"),
    ("Lake Accotink Park Closed for Dam Work", "Community Event", "closure or cancellation"),
]
for title, etype, want in cases:
    got = D.json_exclusion(row(title=title, eventtype=etype), SRC)
    check(f"refused before the page: {title[:58]!r} -> {want}", got == want, got)
for title in ("Foster Care Monthly Information Meeting (In-Person)", "Goblin Golf", "Vaccine and Microchip Clinic",
              "Open House: Rediscover, Reconnect and Recreate!", "October Foster Info Session", "Campfire Fridays",
              "October Reading Tails | Lorton Campus"):
    got = D.json_exclusion(row(title=title), SRC)
    check(f"...and kept before the page: {title!r}", got is None, got)

# ---------------------------------------------------------------------------
# 3. the page: the event's own point, never the map's centre
# ---------------------------------------------------------------------------
info = D.parse_event_page(PAGES["burke_lake"])
check("the location node's point is read (Burke Lake Park, 38.7607784,-77.306883)",
      info["points"] == [(38.7607784, -77.306883)], info["points"])
check("...and NOT data-centre-lat/lng (38.853854,-77.356967), which every page carries",
      (38.853854, -77.356967) not in info["points"])
ev, st = build(row(), PAGES["burke_lake"])
check("a page point places the row exactly: coords_exact, the publisher's pin",
      ev and (ev.latitude, ev.longitude) == (38.7607784, -77.306883) and ev.coords_exact, ev and (ev.latitude, ev.longitude))
check("venue is the location node's title without its ' Location' suffix",
      ev and ev.venue_name == "Burke Lake Park", ev and ev.venue_name)
check("street, town, state and ZIP come from the node ('Fairfax Station,' / 'VA,' / '22039' on three lines)",
      ev and (ev.address, ev.city, ev.region, ev.postal_code) == ("7315 Ox Road", "Fairfax Station", "VA", "22039"),
      ev and (ev.address, ev.city, ev.region, ev.postal_code))
check("a ParkTakes form is the ticket link, and the row says registration is required",
      ev and ev.ticket_url.startswith("https://fairfax.usedirect.com/") and "Registration required." in ev.description)
check("EST in November: 17:00 local is 22:00Z", ev and ev.start_utc == "2026-11-06T22:00:00Z", ev and ev.start_utc)

# A location node left on the map's default centre under somebody else's
# street (synthetic: the Burke Lake page with its marker moved to the centre).
moved = PAGES["burke_lake"].replace('data-lat="38.7607784" data-lng="-77.306883"',
                                    'data-lat="38.853854" data-lng="-77.356967"')
ev, st = build(row(), moved)
check("a marker ON the map's default centre under another street is refused, never pinned (synthetic)",
      ev is None or ev.latitude != 38.853854, ev and (ev.latitude, ev.longitude))
check("...the refusal is counted", st.get("page point refused: the map's default centre under another street") == 1, st)
check("...and the row falls back to the page's street, for the sync to geocode",
      ev is not None and ev.latitude is None and ev.address == "7315 Ox Road" and not ev.coords_exact,
      ev and (ev.latitude, ev.address, ev.coords_exact))
info = D.parse_event_page(PAGES["government_center"])
pt = D.page_point(info, "12000 Government Center Parkway", SRC, {})
check("...but the Government Center's OWN node, whose pin is the centre, keeps it (street matches)",
      pt == (38.853854, -77.356967), pt)

# ---------------------------------------------------------------------------
# 4. no point: a street the sync can geocode, the venue book, or nothing
# ---------------------------------------------------------------------------
ev, st = build(row(title="Smart Strategies to Protect Yourself from Scams", id="32100",
                   url="/events/familyservices/smart-strategies-protect-yourself-scams",
                   start="2026-10-12T11:00:00-04:00", end="2026-10-12T12:00:00-04:00"), PAGES["sherwood"])
check("a street on the page and no point: sent with the street and no coordinates",
      ev and ev.latitude is None and ev.address == "2501 Sherwood Hall Ln" and ev.city == "Alexandria"
      and ev.postal_code == "22306" and not ev.coords_exact, ev and (ev.latitude, ev.address, ev.city))
check("...which the sync WILL geocode (_addr_parts has a street)",
      ev and _addr_parts({"address": ev.address, "city": ev.city, "region": ev.region}) is not None)
check("...and the venue is the block's first line", ev and ev.venue_name == "Sherwood Hall Library", ev and ev.venue_name)

# A street the US Census batch cannot place (Fire Station 40's: 3 Vaccine and
# Microchip Clinic rows on 2026-10-04, dropped by the sync for want of a match):
# the config's street book holds its surveyed pin. Synthetic: the Sherwood page
# with Legato Road's street in it.
legato = PAGES["sherwood"].replace("Sherwood Hall Library<br>\n2501 Sherwood Hall Ln<br>\nAlexandria,\nVA\n22306",
                                   "Fire Station 40<br>\n4621 Legato Road<br>\nFairfax,\nVA\n22033")
ev, st = build(row(title="Vaccine and Microchip Clinic", id="31308", url="/events/animalservices/vaccine-clinic-dec-2026",
                   start="2026-12-11T09:00:00-05:00", end="2026-12-11T12:00:00-05:00"), legato)
pin = D.street_pin("4621 Legato Rd", SRC.get("street_pins"))
check("the street book matches '4621 Legato Rd' to '4621 Legato Road' (normalised, house number included)",
      pin is not None and D.street_pin("4622 Legato Road", SRC.get("street_pins")) is None, pin)
check("...so a street the Census cannot place is pinned from the book, exactly, under the page's own venue",
      ev and pin and (ev.latitude, ev.longitude) == (pin["lat"], pin["lon"]) and ev.coords_exact
      and ev.venue_name == "Fire Station 40" and ev.address == "4621 Legato Road"
      and st.get("placed: the street book (a surveyed pin for a street the Census misses)") == 1, (ev and ev.venue_name, st))
check("every street pin lies inside the box and off the map centre",
      all(D._in_box((v["lat"], v["lon"]), SRC["bbox"]) and D._metres((v["lat"], v["lon"]), (38.853854, -77.356967)) > 100
          for v in SRC.get("street_pins") or []) and len(SRC.get("street_pins") or []) >= 3)

hike = row(title="Fall Color Snap Sketch Hike", id="32200", url="/events/parks/huntley-meadows/fall-color-snap-sketch-hike/102426",
           start="2026-10-24T10:00:00-04:00", end="2026-10-24T12:00:00-04:00")
nobook = dict(SRC, venues=[])
ev, st = build(hike, PAGES["no_location"], nobook)
check("NO POINT, NO STREET, NO BOOK: the row is dropped, never sent with the county for a place",
      ev is None and st.get("dropped: unplaceable (no point, no street, no venue-book entry)") == 1, st)
book = [v for v in SRC.get("venues") or [] if hike["url"].startswith(v["path"])]
check("the config's venue book knows Huntley Meadows (the Park Authority's own location node)", bool(book), book)
ev, st = build(hike, PAGES["no_location"])
check("...so the same row is placed from the book, exactly",
      ev and ev.coords_exact and book and (ev.latitude, ev.longitude) == (book[0]["lat"], book[0]["lon"])
      and st.get("placed: the venue book") == 1, st)
check("the book's longest prefix wins: golf at Burke Lake is the golf centre, not the park",
      (D.book_entry("/events/parks/golf/burke-lake/x/1", [{"path": "/events/parks/burke-lake/", "lat": 1, "lon": 1},
                                                         {"path": "/events/parks/golf/burke-lake/", "lat": 2, "lon": 2}])
       or {}).get("lat") == 2)
for v in SRC.get("venues") or []:
    if not D._in_box((v["lat"], v["lon"]), SRC["bbox"]) or abs(v["lat"] - 38.853854) + abs(v["lon"] + 77.356967) < 0.001:
        check(f"venue book entry {v.get('name')} is inside the box and not the map centre", False, v)
check("every venue-book entry has a street and lies inside the box, off the map centre",
      all(v.get("address") and D._in_box((v["lat"], v["lon"]), SRC["bbox"]) for v in SRC.get("venues") or []))

# ---------------------------------------------------------------------------
# 5. refusals that need the page
# ---------------------------------------------------------------------------
ev, st = build(row(title="QPR Suicide Prevention Training - October 13, 2026", id="32300",
                   url="/events/community-services-board/events/2026-10-13-qpr"), PAGES["zoom_training"])
check("a training whose form is a Zoom registration and that names no place is online: out",
      ev is None and st.get("excluded: online") == 1, st)
ev, st = build(row(title="Titleist Fitting Day"), PAGES["burke_lake"].replace(
    "Bring your own binoculars", "Appointments required. Bring your own binoculars"))
check("'Appointments required' is a private appointment: out", ev is None and st.get("excluded: private appointment") == 1, st)
ev, st = build(row(title="Cider Making (Class)"), PAGES["burke_lake"])
check("a CLASS with a registration form is a registration-only class: out",
      ev is None and st.get("excluded: registration-only class") == 1, st)
ev, st = build(row(title="Vaccine and Microchip Clinic"), PAGES["burke_lake"])
check("...but a vaccine clinic is a service, not a class: kept", ev is not None, st)
ev, st = build(row(title="Campfire Fridays"), PAGES["burke_lake"])
check("'Campfire' is not 'camp' (4 live rows were refused as courses before the closing \\b)", ev is not None, st)
check("...nor is 'Campus' (the title regex alone: 'October Reading Tails | Lorton Campus')",
      not D.CLASS_RX.search("October Reading Tails | Lorton Campus"))
rt = row(title="October Reading Tails | Lorton Campus", id="31739", url="/events/animalservices/october-reading-tails-lorton-campus",
         start="2026-10-29T18:00:00-04:00", end="2026-10-29T19:00:00-04:00")
ev, st = build(rt, PAGES["reading_tails"])
check("Reading Tails' real page shows NO date (only a hidden startDate, a placeholder): out, counted",
      ev is None and st.get("excluded: no date shown on the page (the clock is a placeholder)") == 1, st)
dated = PAGES["reading_tails"].replace('<div class="hidden startDate">',
                                       '<div class="event_date value">10/29/2026 - 6:00 pm to 7:00 pm</div>\n<div class="hidden startDate">')
ev, st = build(rt, dated)
check("...and with a date shown it is still out: 'group registrations only' is a private group booking (synthetic date)",
      ev is None and st.get("excluded: private group booking") == 1, st)
check("...the empty modal twin ('related_modal_event_cost event_date value') is not a shown date",
      D.parse_event_page('<div class="related_modal_event_cost event_date value"></div>' + PAGES["reading_tails"])["visible_date"] is None)
check("...while the Burke Lake page's own date IS read", D.parse_event_page(PAGES["burke_lake"])["visible_date"]
      == "11/06/2026 - 5:00 pm to 6:00 pm", D.parse_event_page(PAGES["burke_lake"])["visible_date"])
for label, blurb in (("Little Hands on the Farm", BLURB_LITTLE_HANDS), ("Foraging for Wild Edibles", BLURB_FORAGING)):
    page = PAGES["burke_lake"].replace("(6-Adult) Use your eyes and ears to identify native birds as you embark on a guided "
                                       "walk with one of our naturalists. Both beginner birders and veterans welcome. Bring "
                                       "your own binoculars or use one of ours.&nbsp;", blurb)
    ev, st = build(row(title=label), page)
    check(f"{label!r}: a title with no class word, a blurb that says it is a class, a ParkTakes form: out",
          ev is None and st.get("excluded: registration-only class") == 1, st)
for title in ("Teacup Topiary Workshop", "Writing Workshop - Wild World-Building"):
    ev, st = build(row(title=title), PAGES["burke_lake"])
    check(f"{title!r} (a workshop with a form) is a registration-only class: out",
          ev is None and st.get("excluded: registration-only class") == 1, st)
for blurb, want in (("In this hands-on workshop, participants will experiment with printmaking", True),
                    ("Bring the medium of your choice to the class.", True),
                    ("After class, view the night sky through observatory telescopes", True),
                    ("Students learn how to roll, shape, and glue strips", True),
                    ("a conversation with the film's star, plus a resource fair, workshops, and lunch!", False),
                    ("Both beginner birders and veterans welcome.", False)):
    check(f"the blurb's class words: {blurb[:52]!r} -> {want}", bool(D.CLASS_BODY_RX.search(blurb)) == want)
ev, st = build(row(title="Campfire Fridays"), PAGES["burke_lake"])
check("...and a one-off public event that takes registration is a ticketed event: kept, its fee said",
      ev is not None and "Price: $9.00 (not free)." in ev.description, ev and ev.description)

# ---------------------------------------------------------------------------
# 6. price: free only in the source's words, a fee never reads as free
# ---------------------------------------------------------------------------
check("'$12.00' -> 'Price: $12.00 (not free).'", D.price_line("$12.00") == ("🎟 Price: $12.00 (not free).", "fee"))
check("'$44.00 - Supply fee $30' keeps the source's words", D.price_line("$44.00 - Supply fee $30")[1] == "fee")
check("'Free' -> 'Admission: free.' (0227's strict pattern)", D.price_line("Free") == ("🎟 Admission: free.", "free"))
check("Animal Services' '$.00 - Free' (2 live rows) is free, not an unclear price",
      D.price_line("$.00 - Free") == ("🎟 Admission: free.", "free"), D.price_line("$.00 - Free"))
check("'$0.00 - $18.25' is not free for everyone", D.price_line("$0.00 - $18.25")[0].endswith("(not free for everyone)."))
check("'Free with $5 parking' is never written as free", D.price_line("Free with $5 parking")[1] == "fee")
check("no price field -> no price line, and nothing claims a fee or free", D.price_line(None) == (None, "no price stated"))
golf = PAGES["burke_lake"].replace("Bring your own binoculars or use one of ours.",
                                   "Personalized instruction for golfers of all abilities. Free!")
ev, _ = build(row(title="Adaptive Golf Play Day"), golf)
check("a $9.00 row whose blurb says 'Free!' is NOT tagged free by 0227 (the fee line's 'not free' vetoes)",
      ev and not tagged_free(ev), ev and ev.description)
ev, _ = build(row(title="Adaptive Golf Play Day"), golf.replace('<div class="event_cost value">$9.00</div>',
                                                                 '<div class="event_cost value">Free</div>'))
check("...and the same row priced 'Free' by the source IS tagged free", ev and tagged_free(ev), ev and ev.description)
check("the adapter's 800 is the sync's DESCRIPTION_MAX", D.SYNC_DESCRIPTION_MAX == SYNC.DESCRIPTION_MAX)
# Synthetic: a $9 row whose 900-character blurb says "Free!" (the sync's
# _cap_prose cuts the END of long prose; the price line used to sit there).
long_golf = golf.replace("Free!", "Free! " + " ".join(["Clubs, balls and a coach are on hand at every station."] * 15))
ev, _ = build(row(title="Adaptive Golf Play Day"), long_golf)
db = SYNC.to_row(ev.as_record("2026-10-04T00:00:00Z"), "00000000-0000-0000-0000-000000000000") if ev else {}
dbd = db.get("description") or ""
check("a fee row with a 900-character blurb: the price line is FIRST and '(not free)' survives the sync's to_row",
      ev and len(ev.description) <= 800 and dbd.startswith("🎟 Price: $9.00 (not free).") and "Free!" in dbd,
      (ev and len(ev.description), dbd[:120]))
check("...so 0227 does not tag it free in the database either",
      ev and not FREE_TAG_NEG_OK(ev.name, dbd), dbd[:200])
check("...and 'Registration required.' and the attribution survive too",
      "Registration required." in dbd and SRC["attribution"] in dbd, dbd[-160:])
door = PAGES["burke_lake"].replace("Bring your own binoculars or use one of ours.",
                                   "Online tickets: $12<br>Tickets at the Door: $15")
ev, st = build(row(title="Haunted Mini Golf"), door)
check("a form AND 'Tickets at the Door' (Haunted Mini Golf): 'Tickets online or at the door.', not 'Registration required.'",
      ev and "Tickets online or at the door." in ev.description and "Registration required." not in ev.description,
      ev and ev.description[:160])

# ---------------------------------------------------------------------------
# 7. one row per occasion; times; identity
# ---------------------------------------------------------------------------
ev, st = build(row(start="2026-11-21T18:30:00-05:00", end="2026-11-21T18:30:00-05:00"), PAGES["burke_lake"])
check("end == start (41 of 195 live rows) is written with NO end, not a zero-length row",
      ev and ev.end_local is None and ev.end_utc is None, ev and ev.end_local)
ev, st = build(row(title="Autumn Leaves Hike and Craft", start="2026-10-31T10:00:00-04:00", end="2026-10-31T23:00:00-04:00"),
               PAGES["burke_lake"])
check("10:00-23:00 the same day (an am/pm slip; the page says 10 am to 10 pm) is written with NO end, counted",
      ev and ev.end_local is None
      and st.get("end over 12 h after a same-day start (an am/pm slip): written with no end") == 1, (ev and ev.end_local, st))
ev, st = build(row(title="Goblin Golf", start="2026-10-17T11:00:00-04:00", end="2026-10-17T21:00:00-04:00"), PAGES["burke_lake"])
check("...while a 10-hour day keeps its end", ev and ev.end_local == "2026-10-17T21:00:00", ev and ev.end_local)

with tempfile.TemporaryDirectory() as tmp:
    class FakeResp:
        def __init__(self, code, text="", location=None):
            self.status_code, self.text, self.headers = code, text, ({"location": location} if location else {})

    class Site:
        """Serves the calendar and one page per path; counts what it is asked."""
        def __init__(self, pages):
            self.pages, self.calls = pages, []

        def get(self, url, timeout=None, allow_redirects=True):
            self.calls.append(url)
            body = self.pages.get(url)
            return FakeResp(404, "Not found") if body is None else FakeResp(200, body)

    class Robots:
        _files = {"https://www.fairfaxcounty.gov": {}}

        def __init__(self, deny=()):
            self.deny = deny

        def check(self, url):
            ok = not any(d in url for d in self.deny)
            return {"allowed": ok, "status": "ok", "rule": None if ok else "Disallow: /x", "crawl_delay": None}

    base = "https://www.fairfaxcounty.gov"
    rows = [
        # Real: E.C. Lawrence posted "House of Reptiles" as TWO nodes, both at
        # 18:00 on 10-24 - one occasion, which must merge into one row.
        row(title="House of Reptiles", id="33001", url="/events/parks/eclawrence/house-of-reptiles/102426",
            start="2026-10-24T18:00:00-04:00", end="2026-10-24T19:00:00-04:00"),
        row(title="House of Reptiles", id="33002", url="/events/parks/eclawrence/house-reptiles/102426",
            start="2026-10-24T18:00:00-04:00", end="2026-10-24T19:00:00-04:00"),
        # Synthetic: a second SESSION the same evening is a different occasion.
        row(title="House of Reptiles", id="33007", url="/events/parks/eclawrence/house-of-reptiles/102426b",
            start="2026-10-24T19:15:00-04:00", end="2026-10-24T20:15:00-04:00"),
        row(title="Botanical Journaling", id="33003", url="/events/parks/green-spring/botanical-journaling/100726",
            start="2026-10-07T13:30:00-04:00", end="2026-10-21T15:30:00-04:00"),
        row(title="Board of Supervisors Meeting: Oct. 13, 2026", id="33004", eventtype="Public Meeting",
            url="/events/boardofsupervisors/oct-13-2026-meeting", start="2026-10-13T09:30:00-04:00",
            end="2026-10-13T09:30:00-04:00"),
        row(title="Last Year's Fair", id="33005", url="/events/parks/x/old", start="2026-09-04T12:00:00-04:00"),
        row(title="Smart Strategies to Protect Yourself from Scams", id="33006",
            url="/events/familyservices/smart-strategies-protect-yourself-scams",
            start="2026-10-12T11:00:00-04:00", end="2026-10-12T12:00:00-04:00"),
        # Synthetic dates (the real one was 09-12, before the window).
        row(title="Family Campout", id="33008", url="/events/parks/burke-lake/family-campout/102426",
            start="2026-10-24T14:30:00-04:00", end="2026-10-25T11:30:00-04:00"),
    ]
    pages = {base + "/events/": settings_page(rows),
             base + "/parks/eclawrence/house-of-reptiles/102426": PAGES["burke_lake"],
             base + "/parks/eclawrence/house-reptiles/102426": PAGES["burke_lake"],
             base + "/parks/eclawrence/house-of-reptiles/102426b": PAGES["burke_lake"],
             base + "/parks/burke-lake/family-campout/102426": PAGES["burke_lake"],
             # the agency copy is gone; the calendar's own page answers
             base + "/events/familyservices/smart-strategies-protect-yourself-scams": PAGES["sherwood"]}
    site = Site(pages)
    path = os.path.join(tmp, "store.json")
    store = EventStore(path)
    reader = D.Reader(site, robots=Robots(), min_interval=0, sleep=lambda s: None)
    stats = {}
    D.ingest_source(store, reader, dict(SRC, calendar_url=base + "/events/"), 90, stats)
    store.save()
    recs = list(store.records.values()) if isinstance(store.records, dict) else list(store.records)
    reps = [r for r in recs if r.get("name") == "House of Reptiles"]
    check("one occasion posted as two nodes merges into ONE row; a second session at 19:15 stays its own "
          "(the clock is in the fingerprint)",
          len(reps) == 2 and sorted(r.get("start_local")[11:16] for r in reps) == ["18:00", "19:15"]
          and store.stats.get("merged") == 1, ([r.get("start_local") for r in reps], store.stats))
    check("a timed row from 10-07 to 10-21 is a series written as one row: refused, never a two-week pin",
          stats.get("excluded: a timed span of a day or more (a series as one row)") == 1, stats)
    camp = [r for r in recs if r.get("name") == "Family Campout"]
    check("...but a 21-hour overnight Family Campout (14:30 to 11:30 next day) is ONE occasion: kept, with its end",
          len(camp) == 1 and camp[0].get("end_local") == "2026-10-25T11:30:00", [r.get("end_local") for r in camp])
    check("public meetings and past rows are refused WITHOUT asking for their pages",
          not any("boardofsupervisors" in u or "/x/old" in u for u in site.calls), site.calls)
    check("an agency page that 404s falls back to the calendar's own page, counted",
          stats.get("agency page 404: read the calendar's own page") == 1
          and any(r.get("address") == "2501 Sherwood Hall Ln" for r in recs), stats)
    check("every row's category is a real lens key, before and after derive_categories",
          all(r.get("category") in MAPSEE_CATEGORY_KEYS and derive_categories(r)[0] in MAPSEE_CATEGORY_KEYS
              for r in recs), [(r.get("category"), derive_categories(r)) for r in recs])
    check("the run asked for 1 calendar + 4 agency pages + 1 that 404'd + its fallback, and nothing else",
          len(site.calls) == 7, site.calls)
    # identity: a second run over the same calendar updates, never adds
    store2 = EventStore(path)
    before = len(store2.records)
    D.ingest_source(store2, D.Reader(Site(pages), robots=Robots(), min_interval=0, sleep=lambda s: None),
                    dict(SRC, calendar_url=base + "/events/"), 90, {})
    check("a second run adds nothing (identity is the node id and its day)",
          len(store2.records) == before and store2.stats.get("added", 0) == 0, store2.stats)

    # -----------------------------------------------------------------------
    # 8. the reader: refusals, robots.txt, the deadline
    # -----------------------------------------------------------------------
    site = Site({})
    site.get = lambda url, timeout=None, allow_redirects=True: (site.calls.append(url), FakeResp(403, "Forbidden"))[1]
    r = D.Reader(site, robots=Robots(), min_interval=0, sleep=lambda s: None)
    for _ in range(2):
        try:
            r.get(base + "/parks/a")
        except D.Refused:
            pass
    check("a 403 is a refusal: never retried, and the host is asked nothing more this run",
          len(site.calls) == 1, site.calls)
    site = Site({base + "/x": "<html>Just a moment...</html>"})
    r = D.Reader(site, robots=Robots(), min_interval=0, sleep=lambda s: None)
    try:
        r.get(base + "/x")
        check("a bot challenge page is a refusal", False)
    except D.Refused:
        check("a bot challenge page (even as a 200) is a refusal", True)
    site = Site({base + "/parks/a": "ok"})
    r = D.Reader(site, robots=Robots(deny=("/parks/",)), min_interval=0, sleep=lambda s: None)
    try:
        r.get(base + "/parks/a")
        check("robots.txt Disallow: no request is made", False)
    except D.Refused:
        check("robots.txt Disallow: the page is refused and NO request is made", site.calls == [], site.calls)
    site = Site({"https://elsewhere.example/p": "ok"})
    site.get = lambda url, timeout=None, allow_redirects=True: (
        site.calls.append(url),
        FakeResp(301, "", "https://elsewhere.example/p") if "fairfax" in url else FakeResp(200, "ok"))[1]
    asked = []

    class Spy(Robots):
        def check(self, url):
            asked.append(url)
            return super().check(url)
    r = D.Reader(site, robots=Spy(), min_interval=0, sleep=lambda s: None)
    final, _ = r.get(base + "/moved")
    check("a redirect to another host is followed only after that host's robots.txt is asked",
          final == "https://elsewhere.example/p" and "https://elsewhere.example/p" in asked, asked)
    clock = [100.0]
    site = Site({base + "/a": "ok"})
    r = D.Reader(site, robots=Robots(), min_interval=0, sleep=lambda s: None, clock=lambda: clock[0], deadline=99.0)
    try:
        r.get(base + "/a")
        check("past the deadline no request starts", False)
    except D.OutOfTime:
        check("past the deadline no request starts (OutOfTime, 0 requests)", site.calls == [], site.calls)
    naps, now = [], [0.0]
    site = Site({base + "/a": "ok", base + "/b": "ok"})
    r = D.Reader(site, robots=Robots(), sleep=lambda s: (naps.append(s), now.__setitem__(0, now[0] + s)),
                 clock=lambda: now[0])
    r.get(base + "/a")
    r.get(base + "/b")
    check("requests to one host are paced >= 1.1 s apart", naps and abs(naps[0] - 1.1) < 1e-9, naps)

    # -----------------------------------------------------------------------
    # 8b. one page failing costs that page, never the source
    # -----------------------------------------------------------------------
    three = [row(title="Bird Walk With a Naturalist", id=f"3410{i}", url=f"/events/parks/burke-lake/bird-walk/11{i}626",
                 start=f"2026-11-{i + 10}T17:00:00-05:00", end=f"2026-11-{i + 10}T18:00:00-05:00") for i in range(3)]

    def run_three(fail, robots=None, clock=None, deadline=None):
        site = Site({base + "/events/": settings_page(three),
                     **{base + r["url"][len("/events"):]: PAGES["burke_lake"] for r in three}})
        plain = site.get

        def get(url, timeout=None, allow_redirects=True):
            if url.endswith(fail[0]):
                site.calls.append(url)
                return FakeResp(fail[1], "boom")
            return plain(url, timeout, allow_redirects)
        site.get = get
        kw = {"clock": clock, "deadline": deadline} if clock else {}
        rd = D.Reader(site, robots=robots or Robots(), min_interval=0, sleep=lambda s: None, **kw)
        st_, stats_ = EventStore(os.path.join(tmp, f"three{fail[1]}.json")), {}
        try:
            with redirect_stdout(io.StringIO()):          # the adapter's own "page failed" log line
                D.ingest_source(st_, rd, dict(SRC, calendar_url=base + "/events/"), 90, stats_)
            raised = None
        except Exception as exc:  # noqa: BLE001
            raised = exc
        return stats_, raised, site
    stats3, raised, site3 = run_three(("/bird-walk/111626", 500))
    check("a 500 on the 2nd of 3 pages (after its retries) drops THAT page, counted, and the other 2 are written",
          raised is None and stats3.get("rows written") == 2
          and stats3.get("dropped: page failed (5xx, timeout or redirects)") == 1, (raised, stats3))
    stats3, raised, _ = run_three(("/nothing", 500), robots=Robots(deny=("/bird-walk/111626",)))
    check("a robots.txt Disallow on one page drops that page only (never fetched), counted",
          raised is None and stats3.get("rows written") == 2
          and stats3.get("dropped: page refused (robots.txt or a redirect's host)") == 1, (raised, stats3))
    stats3, raised, site3 = run_three(("/bird-walk/111626", 403))
    check("...but a 403 from the calendar's host stops the source (Refused), the row before it kept and counted",
          isinstance(raised, D.Refused) and stats3.get("rows written") == 1
          and not any("/bird-walk/112626" in u for u in site3.calls), (raised, stats3, site3.calls))
    tick = [0.0]

    def clock3():
        tick[0] += 1.0
        return tick[0]
    stats3, raised, _ = run_three(("/nothing", 500), clock=clock3, deadline=9.5)
    check("when --max-minutes runs out mid-source, 'rows written' already says what was kept (the STOPPED line is true)",
          isinstance(raised, D.OutOfTime) and 1 <= stats3.get("rows written", 0) < 3, (raised, stats3))

    # -----------------------------------------------------------------------
    # 8b2. completeness: only a whole read may let absence cancel a row
    # -----------------------------------------------------------------------
    done = "read complete (absence may cancel)"
    stats3, raised, _ = run_three(("/nothing", 500))
    check("COMPLETE: every page read", raised is None and stats3.get(done) == 1, stats3)
    st_e, stats_e = EventStore(os.path.join(tmp, "empty.json")), {}
    D.ingest_source(st_e, D.Reader(Site({base + "/events/": settings_page([])}), robots=Robots(), min_interval=0,
                                   sleep=lambda s: None), dict(SRC, calendar_url=base + "/events/"), 90, stats_e)
    check("NOT complete: a calendar that lists nothing (a broken page, not a county that cancelled all)",
          stats_e.get(done) is None and not st_e.complete_reads, stats_e)
    stats3, raised, _ = run_three(("/bird-walk/111626", 500))
    check("NOT complete: one page failed", stats3.get(done) is None
          and any(k.startswith("read NOT complete") for k in stats3), stats3)
    stats3, raised, _ = run_three(("/nothing", 500), robots=Robots(deny=("/bird-walk/111626",)))
    check("NOT complete: one page refused by robots.txt", stats3.get(done) is None, stats3)
    stats3, raised, _ = run_three(("/bird-walk/111626", 404))
    check("NOT complete: a listed occasion whose page is gone (404) was not read", stats3.get(done) is None, stats3)
    stats3, raised, _ = run_three(("/bird-walk/111626", 403))
    check("NOT complete: the host refused mid-read", stats3.get(done) is None, stats3)
    tick[0] = 0.0
    stats3, raised, _ = run_three(("/nothing", 500), clock=clock3, deadline=9.5)
    check("NOT complete: stopped by the deadline", stats3.get(done) is None, stats3)

    # -----------------------------------------------------------------------
    # 8b3. a called-off occasion is a tombstone with the live row's fingerprint
    # -----------------------------------------------------------------------
    # 2026-10-05 (the owner): "we don't want users to go to an event that is
    # closed". The county retitles the occasion's own node; the row we wrote
    # under its own title stays on the map unless the run tombstones THAT
    # row's fingerprint (EventStore.cancel).
    for t, want in (("CANCELED: Campfire Fridays", "Campfire Fridays"), ("Goblin Golf - Postponed", "Goblin Golf"),
                    ("Bird Walk (Cancelled)", "Bird Walk"), ("CANCELLED TODAY- Bird Walk", "Bird Walk"),
                    ("Park Closed", None), ("Closures: Lake Fairfax", None), ("Cancelled", None),
                    ("CANCELED: Lake Closed for the Season", None),
                    ("Cancellation Policy Workshop", None)):
        check(f"called_off({t!r}) is {want!r}", D.called_off(t) == want, D.called_off(t))

    def run_cal(rows_, name):
        site = Site({base + "/events/": settings_page(rows_),
                     **{base + r["url"][len("/events"):]: PAGES["burke_lake"] for r in rows_}})
        st_, stats_ = EventStore(os.path.join(tmp, name)), {}
        with redirect_stdout(io.StringIO()):
            D.ingest_source(st_, D.Reader(site, robots=Robots(), min_interval=0, sleep=lambda s: None),
                            dict(SRC, calendar_url=base + "/events/"), 90, stats_)
        return st_, stats_, site
    st_live, stats_live, _ = run_cal([three[0]], "live.json")
    st_off, stats_off, site_off = run_cal([dict(three[0], title="CANCELED: Bird Walk With a Naturalist")], "off.json")
    check("the called-off node writes no row; it is excluded and counted as cancelled",
          not st_off.records and stats_off.get("excluded: closure or cancellation") == 1
          and stats_off.get("rows cancelled (a called-off title: a tombstone for the row we wrote)") == 1
          and not stats_off.get("rows written"), stats_off)
    check("ITS TOMBSTONE IS THE LIVE ROW'S FINGERPRINT (events.external_id), source and source_id",
          len(st_live.records) == 1 and list(st_off.tombstones) == list(st_live.records)
          and [(t["source"], t["source_id"]) for t in st_off.tombstones.values()]
          == [("drupal-fullcalendar:fairfax-county", "fairfax-county:34100:2026-11-10")],
          (list(st_live.records), st_off.tombstones))
    cr = st_off.complete_reads.get("drupal-fullcalendar:fairfax-county") or {}
    check("the read is marked complete under the rows' own source, over [today, today + 90 days]",
          cr.get("from", "")[:10] == "2026-10-04" and cr.get("to", "")[:10] == "2027-01-02", cr)
    check("...read from the node's own page (one request, like the live row)",
          any("/bird-walk/110626" in u for u in site_off.calls), site_off.calls)
    check("...and the live tallies are the live run's (the tombstone's build is counted apart)",
          not any(k.startswith("placed") or k.startswith("price") for k in stats_off), stats_off)
    st_both, _, _ = run_cal([three[0], dict(three[0], id="34199", title="CANCELED: Bird Walk With a Naturalist")],
                            "both.json")
    check("a live node and a called-off twin of it in one read: the tombstone wins",
          not st_both.records and list(st_both.tombstones) == list(st_live.records), list(st_both.records))
    _, stats_cl, site_cl = run_cal([dict(three[0], title="Park Closed")], "closed.json")
    check("a closure names no occasion: refused, no tombstone, no page asked",
          stats_cl.get("excluded: closure or cancellation") == 1 and len(site_cl.calls) == 1, site_cl.calls)

    # -----------------------------------------------------------------------
    # 8c. what the sync makes of these rows
    # -----------------------------------------------------------------------
    boo = PAGES["burke_lake"].replace(
        "(6-Adult) Use your eyes and ears to identify native birds as you embark on a guided walk with one of our "
        "naturalists. Both beginner birders and veterans welcome. Bring your own binoculars or use one of ours.&nbsp;",
        "Embrace the magic of the season by attending this enchanting event at Family Recreation Area, Franconia Rec "
        "Center.<br>Join in activities and adventures the entire family will enjoy:<br>Monster mash dance party<br>"
        "Costume parade (wear your best costume!)<br>S'mores<br>Seasonal drink<br>Popcorn<br>Carousel ride<br>Scavenger hunt")
    ev, _ = build(row(title="Chessie’s Backyard Boo-gie", url="/events/parks/reccenter/franconia/chessies-backyard-boo-gie/101726",
                      start="2026-10-17T14:00:00-04:00", end="2026-10-17T16:00:00-04:00"), boo)
    cats = derive_categories(ev.as_record("2026-10-04T00:00:00Z")) if ev else None
    check("a rec centre's family Halloween ('Monster mash dance party') never reaches the party door: "
          "the sync treats 'drupal-fullcalendar:' as a civic timetable (needs it in CIVIC_TIMETABLE_SOURCES)",
          cats is not None and "party" not in (cats[1] or []) and cats[0] != "party", cats)

    # -----------------------------------------------------------------------
    # 9. main(): the store is saved and the exit is 0 even when the site fails
    # -----------------------------------------------------------------------
    cfgp = os.path.join(tmp, "cfg.json")
    json.dump({"sources": [dict(SRC, calendar_url="https://unreachable.invalid/events/")]}, open(cfgp, "w"))
    out = os.path.join(tmp, "main.json")
    orig = D.requests.Session

    class Dead:
        headers = {}

        def get(self, *a, **k):
            raise ConnectionError("no network in the gate")
    D.requests.Session = lambda: Dead()
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = D.main(["--config", cfgp, "--store", out, "--max-minutes", "1"])
    finally:
        D.requests.Session = orig
    check("a run whose only source fails exits 0 (like its siblings) and still writes the store",
          code == 0 and os.path.exists(out), (code, buf.getvalue()[-300:]))
    check("...and says why in the log", "FAILED" in buf.getvalue() or "REFUSED" in buf.getvalue(), buf.getvalue()[-300:])

with tempfile.TemporaryDirectory() as tmp:
    for label, bad in (("an attribution longer than the sync keeps (200)", dict(SRC, attribution="x" * 201)),
                       ("a street pin on the map's default centre", dict(SRC, street_pins=[
                           {"street": "1 Somewhere Road", "lat": 38.853854, "lon": -77.356967}]))):
        p = os.path.join(tmp, "dfc_bad.json")
        with open(p, "w") as fh:
            json.dump({"sources": [bad]}, fh)
        try:
            D.load_config(p)
            check(f"{label} is refused at load", False)
        except ValueError:
            check(f"{label} is refused at load", True)

# ---------------------------------------------------------------------------
# 10. OC Parks (Orange County, CA): a 5.x tenant, and where its programmes are
# ---------------------------------------------------------------------------
# Real pages read 2026-10-11, trimmed to what the adapter reads and what used to
# fool it: the theme's accessibility block (the page's FIRST .field-wysiwyg),
# the date and the location field above <main>, <main> itself, and the related
# event card after it. Each trimmed page parsed exactly like the full one when
# it was cut. The calendar rows are real rows of that day's drupalSettings.
# One thing is NOT real: the staff contact sentences in 'yorba' and 'walk_ins'
# keep their shape ("contact Resource Specialist <First> <Last> at (714) ...
# or <first>.<last>@...") with a made-up person, a 555-01xx number and an
# example.org address, so this public repo publishes nobody's direct line.
OC = next(s for s in CFG["sources"] if s["key"] == "oc-parks")
OC_TZ = ZoneInfo(OC["timezone"])
OC_PAGES = {
    'open_space': '<html><body>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-block_content-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-block_content-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <script async src="https://docaccess.com/docbox.js"></script>\n</div>\n<div class="info-date-time">\n <div class="info-date-time__content">\n <div class="info-date-time__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">calendar_today</span></div>\n </div>\n <div class="info-date-time__content-right">\n <div class="info-date-time__heading">\n Date and time\n </div>\n <div class="info-date-time__body">\n Sat, November 7, 2026<br />\n 2:30 pm - 5:00 pm PST\n </div>\n </div>\n </div>\n</div>\n<div class="info-location">\n <div class="info-location__content">\n <div class="info-location__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">location_on</span></div>\n </div>\n <div class="info-location__content-right">\n <div class="info-location__heading">\n Location\n </div>\n <div class="info-location__body">\n<p class="info-location__paragraph">\n <div class="field-location" id="field-1186505886">\n <span\n id="field-1186505886-label"\n class="visually-hidden"\n >\n Location\n </span>\n <div\n role="group"\n aria-labelledby="field-1186505886-label"\n >\n <div >\n<article class="node">\n <div>\n <div class="field-address" id="field-1994914447">\n <span\n id="field-1994914447-label"\n class="visually-hidden"\n >\n Address\n </span>\n <div\n role="group"\n aria-labelledby="field-1994914447-label"\n >\n <p class="address" translate="no"><span class="address-line1">13333 Black Star Canyon Rd</span><br>\n<span class="locality">Silverado</span>, <span class="administrative-area">CA</span> <span class="postal-code">92676</span><br>\n<span class="country">United States</span></p>\n </div>\n </div>\n </div>\n</article>\n </div>\n </div>\n </div>\n </p>\n </div>\n<a\n class="info-location__link"\n href="/node/76#geofield-map-node-location-76-field-lat-long"\n title="/node/76#geofield-map-node-location-76-field-lat-long"\n >\n View Map\n </a>\n </div>\n </div>\n</div>\n </div>\n<main id="main-content" tabindex="-1">\n <section\n aria-label="Content"\n >\n<div\n id="block-countyoc-content" class="block system-main-block"\n id="block-countyoc-content"\n role="region"\n aria-labelledby="block-countyoc-content-title"\n>\n <p\n id="block-countyoc-content-title"\n class="visually-hidden"\n >\n Content block block-countyoc-content\n </p>\n<article class="node">\n <div>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-node-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <p>Enjoy nature with friends and family in Round Canyon. We hike along a fairly flat trail as we watch for red-tailed hawks and mule deer. This activity originates in the City of Irvine and travels into OC Parks’ Irvine Ranch Open Space. The hike averages 2-3 miles round trip at an easy pace with a medium level of interpretation. This program is for participants who would like a slower-paced and level hike. Wear comfortable clothes and sturdy shoes and bring water for the hike.</p><p>Please note, advanced registration required, <strong>no walk-up registration is permitted.</strong></p><p>Reviewing the 1-through-5 <a href="https://letsgooutside.org/resources/difficulty-ratings/hiking-and-fitness-difficulty-rating/">Difficulty Ratings</a> will help you evaluate your readiness to participate in this level-2 activity on the land. Please visit our <a href="https://letsgooutside.org/activities/faq/">FAQ</a><a href="https://letsgooutside.org/resources/faq/">&nbsp;page</a> for answers to activity specific questions you may have prior to registering. To learn more about the staging areas and activity locations, please visit our <a href="https://www.youtube.com/playlist?list=PL2ElxBcaA80CrAEO6BWSO5A4uNsSc4t19">Staging Areas Playlist</a> page.</p><p><a href="https://letsgooutside.org/activities/2026/11/07/family-hike-round-canyon-136/">Learn more and register here</a></p>\n <span class="visually-hidden">\n Links in this section relate to Body\n </span>\n</div>\n </div>\n</article>\n</div>\n </section>\n </main>\n<div class="event-card__body"><div class="body field-wysiwyg text-long">\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n Join OC Parks staff and volunteers for a slow paced, beginner friendly mountain bike ride through Limestone Canyon to The Sinks. This trip is approximately 8 miles total, and 400 foot elevation gain. Riders must supply their own mountain bike and helmet. Please bring sufficient water and food for a...\n</div></div>\n</body></html>',
    'willow': '<html><body>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-block_content-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-block_content-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <script async src="https://docaccess.com/docbox.js"></script>\n</div>\n<div class="info-date-time">\n <div class="info-date-time__content">\n <div class="info-date-time__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">calendar_today</span></div>\n </div>\n <div class="info-date-time__content-right">\n <div class="info-date-time__heading">\n Date and time\n </div>\n <div class="info-date-time__body">\n Wed, October 14, 2026<br />\n 8:30 am - 11:00 am PST\n </div>\n </div>\n </div>\n</div>\n<div class="info-location">\n <div class="info-location__content">\n <div class="info-location__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">location_on</span></div>\n </div>\n <div class="info-location__content-right">\n <div class="info-location__heading">\n Location\n </div>\n <div class="info-location__body">\n<p class="info-location__paragraph">\n <div class="field-location" id="field-1305794208">\n <span\n id="field-1305794208-label"\n class="visually-hidden"\n >\n Location\n </span>\n <div\n role="group"\n aria-labelledby="field-1305794208-label"\n >\n <div >\n<article class="node">\n <div>\n <div class="field-address" id="field-1084981408">\n <span\n id="field-1084981408-label"\n class="visually-hidden"\n >\n Address\n </span>\n <div\n role="group"\n aria-labelledby="field-1084981408-label"\n >\n <p class="address" translate="no"><span class="address-line1">18751 Laguna Canyon Road</span><br>\n<span class="locality">Laguna Beach</span>, <span class="administrative-area">CA</span> <span class="postal-code">92651</span><br>\n<span class="country">United States</span></p>\n </div>\n </div>\n </div>\n</article>\n </div>\n </div>\n </div>\n </p>\n </div>\n<a\n class="info-location__link"\n href="/node/86#geofield-map-node-location-86-field-lat-long"\n title="/node/86#geofield-map-node-location-86-field-lat-long"\n >\n View Map\n </a>\n </div>\n </div>\n</div>\n </div>\n<main id="main-content" tabindex="-1">\n <section\n aria-label="Content"\n >\n<div\n id="block-countyoc-content" class="block system-main-block"\n id="block-countyoc-content"\n role="region"\n aria-labelledby="block-countyoc-content-title"\n>\n <p\n id="block-countyoc-content-title"\n class="visually-hidden"\n >\n Content block block-countyoc-content\n </p>\n<article class="node">\n <div>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-node-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <p><span class="expand-sub"></span></p><p class="visually-hidden">Content block block-countyoc-content</p><p><span class="visually-hidden">Body</span></p><p><em><strong>*This program begins at the Willow Staging Area, 20070 Laguna Canyon Road, Laguna Beach,&nbsp;CA&nbsp;92651*</strong></em></p><p><span>Join OC Parks on a guided hike and learn about key landmarks, trail connections, scenic viewpoints and the incredible wildlife and native plants within Laguna Coast Wilderness Park. We’ll share tips for exploring and stewarding the land responsibly along the way. This program is perfect for first-time visitors, new volunteers or anyone who wants to feel more at home in the coastal canyons.&nbsp;</span></p><p><span>This moderate difficulty hike is approximately 3.5 miles with 600 feet of elevation&nbsp;gain. Closed-toe shoes, sun protection and plenty of water are recommended.&nbsp;</span></p><p>The program is designed for ages 12 and older. Participants 18 and younger must be accompanied by a guardian.&nbsp;</p><p><span>Advance registration required. </span><a class="ext" href="https://lagunacanyon.org/event/explorers-hike-laurel-canyon-loop-3/" title="(opens in a new window)" target="_blank">Learn more and register for Explorers Hike: Laurel Canyon Loop.</a></p><p>The program is free and parking is $3 per vehicle.</p><p>Adverse weather may cancel the program. &nbsp;</p>\n <span class="visually-hidden">\n Links in this section relate to Body\n </span>\n</div>\n </div>\n</article>\n</div>\n </section>\n </main>\n<div class="event-card__body"><div class="body field-wysiwyg text-long">\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n Join OC Parks staff and volunteers for a slow paced, beginner friendly mountain bike ride through Limestone Canyon to The Sinks. This trip is approximately 8 miles total, and 400 foot elevation gain. Riders must supply their own mountain bike and helmet. Please bring sufficient water and food for a...\n</div></div>\n</body></html>',
    'aliso': '<html><body>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-block_content-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-block_content-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <script async src="https://docaccess.com/docbox.js"></script>\n</div>\n<div class="info-date-time">\n <div class="info-date-time__content">\n <div class="info-date-time__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">calendar_today</span></div>\n </div>\n <div class="info-date-time__content-right">\n <div class="info-date-time__heading">\n Date and time\n </div>\n <div class="info-date-time__body">\n Wed, November 11, 2026<br />\n 8:00 am - 11:00 am PST\n </div>\n </div>\n </div>\n</div>\n<div class="info-location">\n <div class="info-location__content">\n <div class="info-location__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">location_on</span></div>\n </div>\n <div class="info-location__content-right">\n <div class="info-location__heading">\n Location\n </div>\n <div class="info-location__body">\n<p class="info-location__paragraph">\n <div class="field-location" id="field-499172085">\n <span\n id="field-499172085-label"\n class="visually-hidden"\n >\n Location\n </span>\n <div\n role="group"\n aria-labelledby="field-499172085-label"\n >\n <div >\n<article class="node">\n <div>\n <div class="field-address" id="field-257869035">\n <span\n id="field-257869035-label"\n class="visually-hidden"\n >\n Address\n </span>\n <div\n role="group"\n aria-labelledby="field-257869035-label"\n >\n <p class="address" translate="no"><span class="address-line1">28373 Alicia Parkway</span><br>\n<span class="locality">Laguna Niguel</span>, <span class="administrative-area">CA</span> <span class="postal-code">92677</span><br>\n<span class="country">United States</span></p>\n </div>\n </div>\n </div>\n</article>\n </div>\n </div>\n </div>\n </p>\n </div>\n<a\n class="info-location__link"\n href="/node/6#geofield-map-node-location-6-field-lat-long"\n title="/node/6#geofield-map-node-location-6-field-lat-long"\n >\n View Map\n </a>\n </div>\n </div>\n</div>\n </div>\n<main id="main-content" tabindex="-1">\n <section\n aria-label="Content"\n >\n<div\n id="block-countyoc-content" class="block system-main-block"\n id="block-countyoc-content"\n role="region"\n aria-labelledby="block-countyoc-content-title"\n>\n <p\n id="block-countyoc-content-title"\n class="visually-hidden"\n >\n Content block block-countyoc-content\n </p>\n<article class="node">\n <div>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-node-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <p>Please be aware this is a fitness hike and not for beginners; the pace is approximately a 19-minute mile.</p><p>Raise your heart rate and your spirits on this strenuous, fast-paced, 8-mile hike over very steep and uneven terrain with approximately 730 feet of elevation gain. This route begins at Aliso Creek Trail East, continues up Sheep Run and down Meadows Trail. Your hard work will be rewarded with sweeping views of Aliso and Wood Canyons Wilderness Park!&nbsp;</p><p><span>Please wear long pants, closed-toe shoes and sun protection, and bring water. </span>Participants must be 16 or older. Minors under 18 must be accompanied by a guardian.&nbsp;</p><p>The program will meet at the visitor center.</p><p>Advance registration required. <a class="ext" href="https://lagunacanyon.org/event/advanced-fitness-hike-3/" title="(opens in a new window)" target="_blank">Learn more and register for Advanced Fitness Hike - Aliso and Wood Canyons.</a></p><p>The program is free and parking is $3 per vehicle.</p><p>Please review safety precautions and protocols when registering.&nbsp;</p><p>For questions, call 949-497-8324 on weekdays or 949-923-2200 on weekends.</p>\n <span class="visually-hidden">\n Links in this section relate to Body\n </span>\n</div>\n </div>\n</article>\n</div>\n </section>\n </main>\n<div class="event-card__body"><div class="body field-wysiwyg text-long">\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n Join OC Parks staff and volunteers for a slow paced, beginner friendly mountain bike ride through Limestone Canyon to The Sinks. This trip is approximately 8 miles total, and 400 foot elevation gain. Riders must supply their own mountain bike and helmet. Please bring sufficient water and food for a...\n</div></div>\n</body></html>',
    'yorba': '<html><body>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-block_content-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-block_content-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <script async src="https://docaccess.com/docbox.js"></script>\n</div>\n<div class="info-date-time">\n <div class="info-date-time__content">\n <div class="info-date-time__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">calendar_today</span></div>\n </div>\n <div class="info-date-time__content-right">\n <div class="info-date-time__heading">\n Date and time\n </div>\n <div class="info-date-time__body">\n Wed, November 4, 2026<br />\n 2:00 pm - 4:00 pm PST\n </div>\n </div>\n </div>\n</div>\n<div class="info-location">\n <div class="info-location__content">\n <div class="info-location__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">location_on</span></div>\n </div>\n <div class="info-location__content-right">\n <div class="info-location__heading">\n Location\n </div>\n <div class="info-location__body">\n<p class="info-location__paragraph">\n Multiple Locations\n </p>\n </div>\n </div>\n </div>\n</div>\n </div>\n<main id="main-content" tabindex="-1">\n <section\n aria-label="Content"\n >\n<div\n id="block-countyoc-content" class="block system-main-block"\n id="block-countyoc-content"\n role="region"\n aria-labelledby="block-countyoc-content-title"\n>\n <p\n id="block-countyoc-content-title"\n class="visually-hidden"\n >\n Content block block-countyoc-content\n </p>\n<article class="node">\n <div>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-node-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <p><em><strong>*This program takes places at Yorba Regional Park, </strong><span class="address-line1"><strong>7600 E. La Palma Ave., </strong></span><span class="locality"><strong>Anaheim</strong></span><strong>,&nbsp;</strong><span class="administrative-area"><strong>CA</strong></span><strong>&nbsp;</strong><span class="postal-code"><strong>92807</strong></span><strong>*</strong></em></p><p>The OC Parks Jr. Ranger Program encourages park guests to explore the outdoor world within OC Parks by developing a sense of belonging, becoming a part of nature and feeling empowered to be stewards of Orange County’s natural and cultural resources.</p><p>Participants will explore Yorba Regional Park, complete activities and take the OC Parks Jr. Ranger Oath to earn a unique Jr. Ranger badge.</p><p>The program will meet in parking lot J.</p><p>This program is designed for ages 7 to 12, but all are welcome.</p><p>Minors must be accompanied by a parent or guardian during the program. Sunscreen, sturdy footwear and water are recommended.</p><p>The program is free and parking is $3 per vehicle. Inclement weather will cancel the program.</p><p>Advance registration is required. <a href="https://letsgooutside.org/activities/2026/11/04/jr-ranger-adventure-yorba-regional-park-4/">Learn more and register for Jr. Ranger Adventure: Yorba Regional Park.</a></p><p>For questions, please contact Resource Specialist Alex Example at&nbsp;<a href="mailto:alex.example@example.org">alex.example@example.org</a>.</p><p><a href="https://ocparks.com/jr-ranger">Learn more about the OC Parks Jr. Ranger Program.</a></p><p>&nbsp;</p>\n <span class="visually-hidden">\n Links in this section relate to Body\n </span>\n</div>\n </div>\n</article>\n</div>\n </section>\n </main>\n<div class="event-card__body"><div class="body field-wysiwyg text-long">\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n Join OC Parks staff and volunteers for a slow paced, beginner friendly mountain bike ride through Limestone Canyon to The Sinks. This trip is approximately 8 miles total, and 400 foot elevation gain. Riders must supply their own mountain bike and helmet. Please bring sufficient water and food for a...\n</div></div>\n</body></html>',
    'walk_ins': '<html><body>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-block_content-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-block_content-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <script async src="https://docaccess.com/docbox.js"></script>\n</div>\n<div class="info-date-time">\n <div class="info-date-time__content">\n <div class="info-date-time__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">calendar_today</span></div>\n </div>\n <div class="info-date-time__content-right">\n <div class="info-date-time__heading">\n Date and time\n </div>\n <div class="info-date-time__body">\n Wed, October 21, 2026<br />\n 7:30 am - 9:30 am PST\n </div>\n </div>\n </div>\n</div>\n<div class="info-location">\n <div class="info-location__content">\n <div class="info-location__content-left">\n <div class="ico-location"><span class="material-icons" translate="no" aria-hidden="true">location_on</span></div>\n </div>\n <div class="info-location__content-right">\n <div class="info-location__heading">\n Location\n </div>\n <div class="info-location__body">\n<p class="info-location__paragraph">\n <div class="field-location" id="field-463594646">\n <span\n id="field-463594646-label"\n class="visually-hidden"\n >\n Location\n </span>\n <div\n role="group"\n aria-labelledby="field-463594646-label"\n >\n <div >\n<article class="node">\n <div>\n <div class="field-address" id="field-1608719418">\n <span\n id="field-1608719418-label"\n class="visually-hidden"\n >\n Address\n </span>\n <div\n role="group"\n aria-labelledby="field-1608719418-label"\n >\n <p class="address" translate="no"><span class="address-line1">8548 E. Canyon View Ave.</span><br>\n<span class="locality">Orange</span>, <span class="administrative-area">CA</span> <span class="postal-code">92869</span><br>\n<span class="country">United States</span></p>\n </div>\n </div>\n </div>\n</article>\n </div>\n </div>\n </div>\n </p>\n </div>\n<a\n class="info-location__link"\n href="/node/126#geofield-map-node-location-126-field-lat-long"\n title="/node/126#geofield-map-node-location-126-field-lat-long"\n >\n View Map\n </a>\n </div>\n </div>\n</div>\n </div>\n<main id="main-content" tabindex="-1">\n <section\n aria-label="Content"\n >\n<div\n id="block-countyoc-content" class="block system-main-block"\n id="block-countyoc-content"\n role="region"\n aria-labelledby="block-countyoc-content-title"\n>\n <p\n id="block-countyoc-content-title"\n class="visually-hidden"\n >\n Content block block-countyoc-content\n </p>\n<article class="node">\n <div>\n<div\n class="body field-wysiwyg text-long" id="wysiwyg-body-node-na-0" data-wysiwyg-a11y="true"\n>\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n <p>Join an experienced OC Parks volunteer and stroll through Peters Canyon Regional Park searching high in the trees for flittering friends and low in the scrubs for bouncing birds. Bring your binoculars, sun protection, comfortable shoes and an eye for spotting birds!</p><p>This program begins near the flagpole by the park office.</p><p>Advance registration is recommended, but walk-ins are welcome. <a class="ext" href="https://letsgooutside.org/activities/2026/10/21/sage-scrub-bird-walk-3/" title="(opens in a new window)" target="_blank">Learn more and register for the Sage Scrub Bird Walk.</a></p><p>The program is free and parking is $3 per vehicle.</p><p>For questions, please contact Resource Specialist Sam Sample at (714) 555-0100 or&nbsp;<a href="mailto:sam.sample@example.org">sam.sample@example.org.</a></p><p>Inclement weather will cancel the program.</p>\n <span class="visually-hidden">\n Links in this section relate to Body\n </span>\n</div>\n </div>\n</article>\n</div>\n </section>\n </main>\n<div class="event-card__body"><div class="body field-wysiwyg text-long">\n <span\n id="wysiwyg-body-node-na-0-label"\n class="visually-hidden"\n >\n Body\n </span>\n Join OC Parks staff and volunteers for a slow paced, beginner friendly mountain bike ride through Limestone Canyon to The Sinks. This trip is approximately 8 miles total, and 400 foot elevation gain. Riders must supply their own mountain bike and helmet. Please bring sufficient water and food for a...\n</div></div>\n</body></html>',
}
OC_ROWS = {r["eid"]: r for r in json.loads('[{"title": "Family Hike: Round Canyon", "id": "0-0", "eid": "43451", "url": "/events/family-hike-round-canyon-0", "des": "", "editable": false, "start": "2026-11-07T14:30:00", "end": "2026-11-07T17:00:00", "backgroundColor": "#3b87ae"}, {"title": "Explorers Hike: Laurel Canyon Loop", "id": "143-0", "eid": "42681", "url": "/events/explorers-hike-laurel-canyon-loop-1", "des": "", "editable": false, "start": "2026-10-14T08:30:00", "end": "2026-10-14T11:00:00", "backgroundColor": "#3b87ae"}, {"title": "Advanced Fitness Hike – Aliso and Wood Canyons", "id": "38-0", "eid": "43261", "url": "/events/advanced-fitness-hike-aliso-and-wood-canyons-7", "des": "", "editable": false, "start": "2026-11-11T08:00:00", "end": "2026-11-11T11:00:00", "backgroundColor": "#3b87ae"}, {"title": "Jr. Ranger Adventure: Yorba Regional Park", "id": "60-0", "eid": "43141", "url": "/events/jr-ranger-adventure-yorba-regional-park", "des": "", "editable": false, "start": "2026-11-04T14:00:00", "end": "2026-11-04T16:00:00", "backgroundColor": "#3b87ae"}, {"title": "Sage Scrub Bird Walk", "id": "193-0", "eid": "42421", "url": "/events/sage-scrub-bird-walk-1", "des": "", "editable": false, "start": "2026-10-21T07:30:00", "end": "2026-10-21T09:30:00", "backgroundColor": "#3b87ae"}, {"title": "EarthCraft", "id": "30-0", "eid": "43301", "url": "/events/earthcraft-6", "des": "", "editable": false, "start": "2026-11-21T14:00:00", "end": "2026-11-21T15:30:00", "backgroundColor": "#3b87ae"}, {"title": "Land Tending with LCF – Aliso Creek", "id": "31-0", "eid": "43296", "url": "/events/land-tending-lcf-aliso-creek-2", "des": "", "editable": false, "start": "2026-11-21T08:00:00", "end": "2026-11-21T11:00:00", "backgroundColor": "#3b87ae"}, {"title": "Birds of a Feather: Beginners Birding", "id": "32-0", "eid": "43291", "url": "/events/birds-feather-beginners-birding-9", "des": "", "editable": false, "start": "2026-11-21T07:30:00", "end": "2026-11-21T10:00:00", "backgroundColor": "#3b87ae"}, {"title": "Upper Newport Bay Habitat Stewards ", "id": "44-0", "eid": "43226", "url": "/events/upper-newport-bay-habitat-stewards-7", "des": "", "editable": false, "start": "2026-11-21T09:00:00", "end": "2026-11-21T11:00:00", "backgroundColor": "#3b87ae"}, {"title": "EVENT FULL: Boo at the Oaks", "id": "189-0", "eid": "42441", "url": "/events/event-full-boo-oaks", "des": "", "editable": false, "start": "2026-10-17T17:30:00", "end": "2026-10-17T21:00:00", "backgroundColor": "#3b87ae"}]')}


def oc_settings(rows):
    """A 5.x drupalSettings: one view, its events inside calendar_options (a JSON string)."""
    view = {"eventBundleType": "event", "startField": "field_start_date", "endField": "field_end_date",
            "entityType": "node", "calendar_options": json.dumps(
                {"initialView": "dayGridMonth", "timeZone": "America/Los_Angeles", "events": rows})}
    return ('<html><head><script type="application/json" data-drupal-selector="drupal-settings-json">'
            + json.dumps({"path": {"baseUrl": "/"}, "fullCalendarView": [view]}) + "</script></head></html>")


def oc_build(eid, page_key, src=OC, page=None, **over):
    ev = dict(OC_ROWS[eid], **over)
    stats = {}
    out = D.build_event(ev, D.parse_event_page(page or OC_PAGES[page_key], src.get("page_selectors")),
                        "https://parks.oc.gov" + ev["url"], src, OC_TZ, stats)
    return out, stats


# 10a. identity: 5.x's `id` is a row in the view, its node is `eid`
evs = D.calendar_events(oc_settings(list(OC_ROWS.values())))
check("5.x: OC Parks' events are read from calendar_options (real rows)", len(evs) == len(OC_ROWS), len(evs))
check("the node is `eid` (43451), not the view's row `id` ('0-0'); 8.x-2.x's `id` is still the node",
      D.node_id(OC_ROWS["43451"]) == "43451" and D.node_id({"id": "32403"}) == "32403", D.node_id(OC_ROWS["43451"]))

# 10b. the page: the event's own blurb, the shown date, the location field, the form
info = D.parse_event_page(OC_PAGES["open_space"], OC["page_selectors"])
desc = info["description"] or ""
check("the blurb is the event's own (inside <main>), not the accessibility block's 'Body' that comes first",
      desc.startswith("Enjoy nature with friends and family in Round Canyon."), desc[:80])
check("...without a screen reader's labels ('Body', 'Links in this section relate to Body')",
      "Links in this section" not in desc and "Body" not in desc.split("\n"), desc[-120:])
check("...and not the related event's card after </main>", "beginner friendly mountain bike ride" not in desc)
check("the shown date and time are read ('Sat, November 7, 2026 2:30 pm - 5:00 pm PST')",
      info["visible_date"] == "Sat, November 7, 2026 2:30 pm - 5:00 pm PST", info["visible_date"])
check("the location field is the node's <p class=address>, without its 'Location'/'Address' labels",
      info["location_lines"][:2] == ["13333 Black Star Canyon Rd", "Silverado , CA 92676"], info["location_lines"])
check("no form block: the registration is the blurb's link that SAYS register ('Learn more and register here')",
      info["registration"] == "https://letsgooutside.org/activities/2026/11/07/family-hike-round-canyon-136/",
      info["registration"])

# 10c. an open space's address is not where anybody meets
ev, st = oc_build("43451", "open_space")
check("an Irvine Ranch Open Space hike whose page names no start is NOT pinned on the open space's address "
      "(its stated starts sit 2.6 to 11.2 km from it)",
      ev is None and st.get("dropped: only an open space's address, and no start named") == 1
      and st.get("page street refused: an open space's address, not where it meets") == 1, (ev and ev.address, st))

# 10d. the start the blurb names beats the park's address
ev, st = oc_build("42681", "willow")
check("Laguna Coast: '*This program begins at the Willow Staging Area, 20070 Laguna Canyon Road...*' beats "
      "the location field's 18751 Laguna Canyon Road (3.2 km away)",
      ev and (ev.venue_name, ev.address, ev.city, ev.region, ev.postal_code)
      == ("Willow Staging Area", "20070 Laguna Canyon Road", "Laguna Beach", "CA", "92651"),
      ev and (ev.venue_name, ev.address, ev.city))
check("...sent with no coordinates for the sync's Census pass (86 m from OSM's Willow Staging Area)",
      ev and ev.latitude is None and not ev.coords_exact
      and _addr_parts({"address": ev.address, "city": ev.city, "region": ev.region}) is not None)
check("...the blurb's pasted <p class=visually-hidden>Content block ...</p> is not text",
      ev and "Content block" not in ev.description, ev and ev.description[:200])
check("...the blurb's own cost sentence first (no price field; the 800 cut took it from 30 of 57 rows), "
      "then 'Registration required.'",
      ev and ev.description.split("\n\n")[:2] == ["🎟 The program is free and parking is $3 per vehicle.",
                                                  "Registration required."]
      and ev.description.count("The program is free") == 1, ev and ev.description[:160])
check("...and the ticket link is the organiser's own registration page",
      ev and ev.ticket_url == "https://lagunacanyon.org/event/explorers-hike-laurel-canyon-loop-3/", ev and ev.ticket_url)
ev, st = oc_build("42681", "willow", src=dict(OC, cost_in_blurb=False))
check("...without `cost_in_blurb` (Fairfax) no sentence is lifted", ev and not ev.description.startswith("🎟"),
      ev and ev.description[:80])
willow = OC_PAGES["willow"].replace("the Willow Staging Area, 20070 Laguna Canyon Road, Laguna Beach,",
                                    "Irvine Ranch Open Space, 13333 Black Star Canyon Rd, Silverado,")
ev, st = oc_build("42681", "willow", page=willow)
check("a start line naming the open space's address is refused too, and the park's own street places it "
      "(synthetic)", ev and st.get("start line refused: an open space's address") == 1
      and ev.venue_name == "Laguna Coast Wilderness Park (Nix Nature Center)", (ev and ev.venue_name, st))

# 10e. the park's street: OC Parks' own pin, named for the park
ev, st = oc_build("43261", "aliso")
pin = D.street_pin("28373 Alicia Parkway", OC.get("street_pins"))
check("Aliso and Wood Canyons (no start line; the Census has no match for 28373 Alicia Parkway): OC Parks' own "
      "node pin, exactly, under the park's name",
      ev and pin and (ev.latitude, ev.longitude) == (pin["lat"], pin["lon"]) and ev.coords_exact
      and ev.venue_name == "Aliso and Wood Canyons Wilderness Park", ev and (ev.venue_name, ev.latitude))

# 10f. a page with no address at all, and a start line that has one
ev, st = oc_build("43141", "yorba")
check("Jr. Ranger at Yorba: no address on the page; '*This program takes places at Yorba Regional Park, 7600 E. "
      "La Palma Ave., Anaheim , CA 92807*' places it on OC Parks' own Yorba pin",
      ev and ev.venue_name == "Yorba Regional Park" and ev.coords_exact
      and (ev.latitude, ev.longitude) == (33.86917, -117.76625) and ev.city == "Anaheim"
      and st.get("placed: the start the blurb names (the street book's pin)") == 1, (ev and ev.venue_name, st))
check("...its form is the first link that says register, not 'Learn more about the OC Parks Jr. Ranger Program'",
      ev and ev.ticket_url.startswith("https://letsgooutside.org/activities/2026/11/04/jr-ranger-adventure-yorba"),
      ev and ev.ticket_url)

# 10g. walk-ins, and free in the source's own words
ev, st = oc_build("42421", "walk_ins")
check("'registration is recommended, but walk-ins are welcome' says so: no 'Registration required.', no 'Tickets'",
      ev and ev.description.split("\n\n")[1:2] == ["Registration online, or walk in."]
      and "Tickets" not in ev.description and "Registration required." not in ev.description,
      ev and ev.description[:120])
check("...and 'The program is free' reaches 0227's free tag in the source's words, with no price invented",
      ev and tagged_free(ev) and "Price:" not in ev.description, ev and ev.description[:300])
for text, want in (("Please wear closed-toe shoes.\nThe program is free and parking is $3 per vehicle.\nFor questions, call.",
                    "The program is free and parking is $3 per vehicle."),
                   ("Cost is $5 per child (one parent included). Parent must accompany child for entire program.",
                    "Cost is $5 per child (one parent included)."),
                   ("Reservations are not needed. Activities are free with paid zoo admission ($2 per person, ages 2 & "
                    "under are free).", "Activities are free with paid zoo admission ($2 per person, ages 2 & under are free)."),
                   ("Parking fee is $5 per vehicle on weekends.", None),
                   ("This program is designed for ages 7 to 12, but all are welcome.", None)):
    check(f"the blurb's cost sentence: {text[:44]!r} -> {want and want[:30]!r}", D.cost_sentence(text) == want,
          D.cost_sentence(text))

# 10h. the start line itself (real sentences)
for text, want in (
        ("*This program begins at the Willow Staging Area, 20070 Laguna Canyon Road, Laguna Beach, CA 92651*",
         ("Willow Staging Area", "20070 Laguna Canyon Road", "Laguna Beach", "92651")),
        ("* This program begins at Laguna Canyon Foundation Headquarters, 10 Phillips St. Laguna Beach, CA 92651.*",
         ("Laguna Canyon Foundation Headquarters", "10 Phillips St.", "Laguna Beach", "92651")),
        ("*This program begins at the Hollyleaf cul-de-sac near Aliso and Wood Canyons Wilderness Park (where West "
         "Ridge Trail meets Hollyleaf, near 65 Hollyleaf, Aliso Viejo, CA 92656). *",
         ("Hollyleaf cul-de-sac near Aliso and Wood Canyons Wilderness Park", "65 Hollyleaf", "Aliso Viejo", "92656")),
        ("*This program takes places at Yorba Regional Park, 7600 E. La Palma Ave., Anaheim , CA 92807 *",
         ("Yorba Regional Park", "7600 E. La Palma Ave.", "Anaheim", "92807"))):
    got = D.start_place(text)
    check(f"start line {text[1:48]!r}... -> {want[1]!r}, {want[2]!r}",
          got is not None and (got["venue"], got["address"], got["city"], got["postal"]) == want, got)
for text in ("This program begins at the visitor center. Please wear closed-toe shoes and bring snacks and water.",
             "This ride starts at the Augustine Staging Area in Limestone Canyon Nature Preserve and rides out "
             "Limestone Canyon Road to the geological feature call The Sinks.",
             "This activity originates in the City of Irvine (Portola Staging Area) and travels into OC Parks' "
             "Irvine Ranch Open Space.",
             "The program will meet in parking lot J.",
             "We will start with a gentle climb up Gypsum Canyon Road for about one mile."):
    check(f"...a start with no street is left to the location field: {text[:50]!r}", D.start_place(text) is None,
          D.start_place(text))
pin = D.street_pin("10 Phillips St.", OC.get("street_pins"))
check("'10 Phillips St.' carries OSM's pin: the Census batch put it on Phillips Circle in Laguna Niguel, 10.1 km off",
      pin is not None and D._metres((pin["lat"], pin["lon"]), (33.506572673424, -117.699942442402)) > 9000, pin)

# 10i. refused by the title
check("'EVENT FULL: Boo at the Oaks' (real; 'no walkups') is sold out by another name",
      D.json_exclusion(OC_ROWS["42441"], OC) == "sold out", D.json_exclusion(OC_ROWS["42441"], OC))
check("...while 'Full Moon Hike' is not (synthetic)",
      D.json_exclusion(dict(OC_ROWS["42441"], title="Full Moon Hike"), OC) is None)

# 10j. identity across reads, and a capped list is never a complete read
with tempfile.TemporaryDirectory() as tmp:
    base = "https://parks.oc.gov"
    # Real rows, all on 2026-11-21 (EarthCraft, Land Tending, Birds of a
    # Feather), numbered as the view numbers them; then a node created ahead of
    # them (Upper Newport Bay Habitat Stewards, the same day) renumbers all three.
    day = [OC_ROWS[e] for e in ("43301", "43296", "43291")]
    first = [dict(r, id=f"{i}-0") for i, r in enumerate(day)]
    second = [dict(OC_ROWS["43226"], id="0-0")] + [dict(r, id=f"{i + 1}-0") for i, r in enumerate(day)]
    served = {base + r["url"]: OC_PAGES["aliso"] for r in day + [OC_ROWS["43226"]]}

    def oc_run(rows, path, src=OC):
        site = Site(dict(served, **{base + "/events": oc_settings(rows)}))
        store, stats = EventStore(path), {}
        with redirect_stdout(io.StringIO()):
            D.ingest_source(store, D.Reader(site, robots=Robots(), min_interval=0, sleep=lambda s: None),
                            dict(src, calendar_url=base + "/events"), 90, stats)
        store.save()
        return store, stats
    path = os.path.join(tmp, "oc.json")
    st1, s1 = oc_run(first, path)
    ids = sorted(ref["source_id"] for rec in st1.records.values() for ref in rec.get("sources") or [])
    check("each row's source_id is the node and its day ('oc-parks:43291:2026-11-21'), never the view's row",
          ids == ["oc-parks:43291:2026-11-21", "oc-parks:43296:2026-11-21", "oc-parks:43301:2026-11-21"], ids)
    st2, s2 = oc_run(second, path)
    check("a node created ahead of three others renumbers them: the next read adds 1 row and rekeys none",
          st2.stats.get("added") == 1 and st2.stats.get("rekeyed", 0) == 0 and st2.stats.get("updated") == 3
          and len(st2.records) == 4, st2.stats)
    _, s3 = oc_run(first, os.path.join(tmp, "cap.json"), dict(OC, list_cap=3))
    check("a calendar listing exactly its cap (OC Parks: 500, newest node first) is never a complete read",
          s3.get("read complete (absence may cancel)") is None
          and any(k.startswith("read NOT complete") and "cap of 3" in k for k in s3), s3)
    check("...under the cap it is, and the log says how deep the last upcoming event sits",
          s1.get("read complete (absence may cancel)") == 1 and s1.get("deepest in-window event: list position") == 3, s1)

# 10k. the config
check("OC Parks' street book: every pin inside its box, one entry per street, and the open space's street not in it",
      all(D._in_box((v["lat"], v["lon"]), OC["bbox"]) for v in OC["street_pins"])
      and len({D._norm_street(v["street"]) for v in OC["street_pins"]}) == len(OC["street_pins"])
      and not any(D.area_street(v["street"], OC) for v in OC["street_pins"]))
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "dfc_area.json")
    with open(p, "w") as fh:
        json.dump({"sources": [dict(OC, street_pins=OC["street_pins"] + [
            {"street": "13333 Black Star Canyon Rd", "lat": 33.764933, "lon": -117.678412}])]}, fh)
    try:
        D.load_config(p)
        check("an open space's address that is also a street pin is refused at load", False)
    except ValueError:
        check("an open space's address that is also a street pin is refused at load", True)

# 10l. no person reaches a row: 31 of 111 pages give an email in the blurb, 29
# of them a member of staff's own (six people), most beside a direct line; 4 of
# 58 rows kept one past the 800 cut (2026-10-11). Names here are made up.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}\b")
ev, st = oc_build("42421", "walk_ins")
check("Sage Scrub Bird Walk: 'For questions, please contact Resource Specialist <name> at (714) ... or <address>' "
      "is left out of the row: no address, no name, no direct line",
      ev and not EMAIL.search(ev.description) and "Sam Sample" not in ev.description
      and "555-0100" not in ev.description and "Resource Specialist" not in ev.description
      and st.get("contact sentences left out (an email address or a named person)") == 1, ev and ev.description)
check("...and the blurb around it stays (the sentence after it: 'Inclement weather will cancel the program.')",
      ev and "Inclement weather will cancel the program." in ev.description
      and "searching high in the trees" in ev.description, ev and ev.description[-200:])
ev, st = oc_build("43141", "yorba")
check("Jr. Ranger at Yorba: 'contact Resource Specialist <name> at <address>' is left out too, before the 800 cut "
      "(which happened to take it here, and kept it on 4 other rows)",
      ev and not EMAIL.search(ev.description) and "Alex Example" not in ev.description
      and st.get("contact sentences left out (an email address or a named person)") == 1, (st, ev and ev.description))
synced = []
for eid, key in (("42421", "walk_ins"), ("43141", "yorba"), ("43261", "aliso"), ("42681", "willow")):
    ev, _ = oc_build(eid, key)
    if ev:
        synced.append(SYNC.to_row(ev.as_record("2026-10-11T00:00:00Z"), "00000000-0000-0000-0000-000000000000"))
check("...no synced OC row carries an email or a staff member's name (as test_ingest_toronto_rec.py asks)",
      len(synced) == 4 and not [r["title"] for r in synced if EMAIL.search(r.get("description") or "")
                                 or re.search(r"Sam Sample|Alex Example", r.get("description") or "")],
      [r["description"][-160:] for r in synced])
for text, want in (
        # Real sentences of 2026-10-11 (people renamed), one blurb line each.
        ("For questions, please contact Resource Specialist, Sam Sample at 714-555-0100 or sam.sample@example.org .",
         ""),
        ("For large group registration or general questions, please contact Resource Specialist "
         "sam.sample@example.org or call (714)555-0101.", ""),
        ("Si tienes alguna pregunta, comunícate con la Especialista en Recursos Alex Example a "
         "alex.example@example.org .", ""),
        ("For questions, please email unbic@ocparks.com or call (949) 923-2290.", ""),
        ("If you are an equestrian, there is a separate registration for you. Once registered, please email "
         "irvineranchopenspace@ocparks.com at least 72 hours prior to the event. Trailer parking is very limited.",
         "If you are an equestrian, there is a separate registration for you. Trailer parking is very limited."),
        # A venue's number, in a sentence that names nobody: kept (17 on 2026-10-11).
        ("For questions, call 949-497-8324 on weekdays or 949-923-2200 on weekends.",
         "For questions, call 949-497-8324 on weekdays or 949-923-2200 on weekends."),
        ("We will be meeting at the Library of the Canyons. You can contact the library at 714-649-2216 if you "
         "need directions.", "We will be meeting at the Library of the Canyons. You can contact the library at "
         "714-649-2216 if you need directions."),
        # Synthetic: a person and a direct line with no address; an initial.
        ("Bring water. For questions, please contact Resource Specialist Sam Sample at (714) 555-0100.",
         "Bring water."),
        ("Please contact Ranger J. Sample at (714) 555-0102 to reserve. The walk is free.", "The walk is free.")):
    got, n = D.without_contacts(text)
    check(f"contact sentences: {text[:58]!r}... -> {want[:30]!r}", got == want, got)
page = OC_PAGES["walk_ins"].replace('href="https://letsgooutside.org/activities/2026/10/21/sage-scrub-bird-walk-3/"',
                                    'href="mailto:sam.sample@example.org"')
ev, st = oc_build("42421", "walk_ins", page=page)
check("a 'register' link that is mailto: is no ticket link: the row links its own page (synthetic)",
      ev and ev.ticket_url == "https://parks.oc.gov/events/sage-scrub-bird-walk-1"
      and st.get("registration link refused: not a web page (mailto: or tel:)") == 1, ev and ev.ticket_url)

print(f"\n{len(fails)} failure(s)" if fails else "\nall passed")
sys.exit(1 if fails else 0)
