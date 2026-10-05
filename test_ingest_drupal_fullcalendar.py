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

print(f"\n{len(fails)} failure(s)" if fails else "\nall passed")
sys.exit(1 if fails else 0)
