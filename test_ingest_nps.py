#!/usr/bin/env python3
"""
test_ingest_nps.py - what the National Park Service /events API hands over,
and how much of it the old reader threw away.

Every event below is a row read live off developer.nps.gov/api/v1/events
(stateCode=VA) on 2026-10-11, trimmed to the fields the adapter reads and to
dates up to 2026-11-01 (the Shepherd Parkway clean-up keeps its two, in
November and December); the visitor centres are the /visitorcenters rows for
the Blue Ridge Parkway from the same day. The fake API behaves the way the
live one was measured to: it pages with pageNumber and never reads `start`,
caps a page at 50 whatever pageSize asks for, and echoes "pagesize": "50".

The expensive cases are the ones that looked fine in the log:

  * PAGING. The old reader sent `start`, so every state was its first page
    read again and again: Virginia was 50 events of 254, logged as "903".
  * NO COORDINATES. 30 of Virginia's first 100 events have none. A Craggy
    Gardens hike names its visitor centre, which is the place. The Parkway
    itself is 470 km of visitor centres, so its park point places nothing,
    and a park we know nothing else about places nothing either.
  * A PLACE FINER THAN THE PARK. Civil War Defenses of Washington's four
    coordinated programmes sit within 9 m of each other at Battleground
    National Cemetery, so a park point near them passes the 5 km size test,
    and the clean-up at 555 Newcomb St SE, 14.1 km south, took it. Its
    street address goes to the sync's geocoder instead.
  * THE DOOR. The old rule matched "art", "kid" and "volunteer" anywhere in
    the raw HTML: "shook the earth" was arts, "font-family" kids, a bird
    walk "Volunteer Guided" volunteer, "Hike Smart" arts.
  * DAYS THE PARK CALLED OFF are still in `dates`: an EXDATE, a cancelled
    day. And the Mabry Mill open house is published twice, one copy
    excluding the weekend days the other lists live.
  * THE KEY. 1,000 requests an hour: a 429 or a 403 ends the run unretried.

PARKS: /parks was not read live (the DEMO_KEY quota ended the session's
reads first), so the five /parks rows here follow the API's swagger (parkCode,
latitude and longitude as strings) with points set by hand: each compact
park's within a kilometre of its own events' coordinates (Civil War Defenses
of Washington's 1.2 km from Battleground), the Parkway's in its middle. The
PAGING-* events are copies of a real one under made-up ids, and the
FORT_DUPONT row and one "(Folk Art Center - Milepost 382)" title are made up.

    python test_ingest_nps.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261011"

import requests  # noqa: E402

import mapsee_ingest_nps as N  # noqa: E402
import mapsee_supabase_sync as S  # noqa: E402
from mapsee_ingest import make_fingerprint  # noqa: E402

if hasattr(N, "MIN_INTERVAL_S"):
    N.MIN_INTERVAL_S = 0                                          # no sleeping in a test

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


# ---------------------------------------------------------------- fixtures --
HAFE_SUN = {"id": "6C379815-E9FE-E050-73B4202C9C070029", "title": "The Story Behind the Scenery", "location": "Arsenal Square, near the John Brown Fort in Lower Town.", "sitecode": "hafe", "sitetype": "park", "parkfullname": "Harpers Ferry National Historical Park", "latitude": "39.322795", "longitude": "-77.729709", "dates": ["2026-10-11", "2026-10-18", "2026-10-25", "2026-11-01"], "recurrencerule": "DTSTART=20260301T050000Z;UNTIL=20270103T050000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU;COUNT=0;INTERVAL=1|EXDATE=2026-10-18", "cancelleddatelist": [], "times": [{"timestart": "02:00 PM", "timeend": "03:00 PM"}], "types": ["Talk"], "isfree": "true"}  # noqa: E501
HAFE_SAT = {"id": "6C31EB7F-9A3A-6605-93D93CE4A803358C", "title": "The Story Behind the Scenery", "location": "Arsenal Square in historic Lower Town, Harpers Ferry", "sitecode": "hafe", "sitetype": "park", "parkfullname": "Harpers Ferry National Historical Park", "latitude": "", "longitude": "", "dates": ["2026-10-17", "2026-10-24", "2026-10-31"], "recurrencerule": "DTSTART=20260307T050000Z;UNTIL=20270102T050000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SA;COUNT=0;INTERVAL=1|EXDATE=2026-05-23,2026-05-30,2026-06-20,2026-10-10,2026-10-17,2026-11-14,2026-04-25,2026-04-04,2026-09-05", "cancelleddatelist": [], "times": [{"timestart": "11:00 AM", "timeend": "12:00 PM"}], "types": ["Talk"], "isfree": "true"}  # noqa: E501
BOWA_TOUR = {"id": "B2FC6D20-C58F-8E33-877E55097BB07E19", "title": "Born Here, Freed Here Walking Tour", "location": "", "sitecode": "bowa", "sitetype": "park", "parkfullname": "Booker T. Washington National Monument", "latitude": "37.119489", "longitude": "-79.731654", "dates": ["2026-10-11", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"], "recurrencerule": "DTSTART=20261001T040000Z;UNTIL=20270331T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU,WE,TH,FR,SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "02:00 PM", "timeend": "02:45 PM"}], "types": ["Guided Tour", "Talk", "Walk"], "isfree": "true"}  # noqa: E501
BOWA_HIKE = {"id": "B895E3B2-9D16-28E9-AB8DA576513C7790", "title": "Footsteps of Freedom Guided Hike", "location": "", "sitecode": "bowa", "sitetype": "park", "parkfullname": "Booker T. Washington National Monument", "latitude": "", "longitude": "", "dates": ["2026-10-17", "2026-10-24", "2026-10-31"], "recurrencerule": "DTSTART=20261001T040000Z;UNTIL=20270331T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "10:00 AM", "timeend": "11:30 AM"}], "types": ["Guided Tour", "Hike", "Walk"], "isfree": "true"}  # noqa: E501
GEWA = {"id": "FA0B4C09-E90E-06E9-98A4347E94A3D11B", "title": "Step into the Memorial House Museum", "location": "", "sitecode": "gewa", "sitetype": "park", "parkfullname": "George Washington Birthplace National Monument", "latitude": "", "longitude": "", "dates": ["2026-10-11", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-21"], "recurrencerule": "DTSTART=20260526T040000Z;UNTIL=20261231T050000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU,WE,TH,FR,SA;COUNT=0;INTERVAL=1|EXDATE=2026-06-17", "cancelleddatelist": [], "times": [{"timestart": "10:00 AM", "timeend": "12:00 PM"}], "types": ["Guided Tour"], "isfree": "true"}  # noqa: E501
CRAGGY = {"id": "ECDD046B-9D54-4DEE-0133CFB91A7066FC", "title": "The Ramblin’ Bald: Guided High Elevation Hike (Craggy Gardens Visitor Center - Milepost 364)", "location": "Meet in front of the Craggy Gardens Visitor Center.", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "", "longitude": "", "dates": ["2026-10-11"], "recurrencerule": "", "cancelleddatelist": [], "times": [{"timestart": "01:45 PM", "timeend": "03:15 PM"}], "types": ["Hike", "Walk"], "isfree": "true"}  # noqa: E501
BUCK = {"id": "EA1C0944-00B5-9485-8B4223BBEE439CAB", "title": "Friends of the Forests: Guided Hikes near Mt Pisgah and Buck Spring (Buck Spring Gap Overlook-MP 407.6)", "location": "Meet at the Buck Spring Overlook, at Milepost 407.6. It’s the first overlook after you turn off the main Parkway, heading in the direction of the Mt Pisgah Parking Area.", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "", "longitude": "", "dates": ["2026-10-11"], "recurrencerule": "", "cancelleddatelist": ["10/11/2026"], "times": [{"timestart": "10:45 AM", "timeend": "12:15 PM"}], "types": ["Hike"], "isfree": "true"}  # noqa: E501
BUCK_TALK = dict(BUCK, id="EA365DB5-D034-9A6F-501C2969789A20D7", cancelleddatelist=[], dates=["2026-10-12"],
                 title="The View from Here: Ranger-Led Talks and Activities (Buck Spring Gap Overlook-MP 407.6)",
                 location="This talk will be at the Buck Spring Overlook, Milepost 407.6.",
                 types=["Cultural/Craft Demonstration", "Other", "Talk"])
MABRY_A = {"id": "BE052848-9A01-D282-DEC221000669E709", "title": "Historic Mill Open House (Mabry Mill - Milepost 176.2)", "location": "Blue Ridge Parkway - Mabry Mill - Milepost 176.2", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "36.751712", "longitude": "-80.405357", "dates": ["2026-10-11", "2026-10-17", "2026-10-18", "2026-10-24", "2026-10-25", "2026-10-31", "2026-11-01"], "recurrencerule": "DTSTART=20260606T040000Z;UNTIL=20261101T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU,SA;COUNT=0;INTERVAL=1|EXDATE=2026-06-27,2026-06-28,2026-07-18,2026-10-11,2026-10-18,2026-10-25,2026-10-10,2026-10-17,2026-10-24,2026-10-31,2026-11-01", "cancelleddatelist": [], "times": [{"timestart": "10:30 AM", "timeend": "02:00 PM"}], "types": ["Talk"], "isfree": "true"}  # noqa: E501
MABRY_B = {"id": "231194B0-B92C-0355-920979708938A3E2", "title": "Historic Mill Open House (Mabry Mill - Milepost 176.2)", "location": "Blue Ridge Parkway - Mabry Mill - Milepost 176.2", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "36.751712", "longitude": "-80.405357", "dates": ["2026-10-11", "2026-10-12", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"], "recurrencerule": "DTSTART=20260905T040000Z;UNTIL=20261101T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU,MO,TH,FR,SA;COUNT=0;INTERVAL=1|EXDATE=2026-10-11", "cancelleddatelist": [], "times": [{"timestart": "10:30 AM", "timeend": "04:00 PM"}], "types": ["Talk"], "isfree": "true"}  # noqa: E501

# Civil War Defenses of Washington: four programmes at Battleground National
# Cemetery, all within 9 m of each other, and the Shepherd Parkway clean-up,
# 14.1 km south of them by the Census geocoder (555 Newcomb St SE ->
# 38.8457,-76.9992, read 2026-10-11). The clean-up keeps both its dates.
CW_JR = {"id": "A736D2E5-9089-8284-5E46F94050628669", "title": "Junior Ranger Discovery Club: Explore, Learn, Protect (CANCELLED)", "location": "Battleground National Cemetery \n6625 Georgia Ave NW\nWashington DC", "sitecode": "cwdw", "sitetype": "park", "parkfullname": "Civil War Defenses of Washington ", "latitude": "38.970787", "longitude": "-77.026745", "dates": ["2026-10-15", "2026-10-22", "2026-10-29"], "recurrencerule": "DTSTART=20261001T040000Z;UNTIL=20270325T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=TH;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "03:00 PM", "timeend": "04:00 PM"}], "isfree": "true", "types": ["Children’s Program", "Cultural/Craft Demonstration", "Talk"], "description": "<div>Calling all future Junior Rangers! Get ready to travel back in time, uncover amazing stories, and discover what life was really like during the Civil War!</div>"}  # noqa: E501
CW_STONE = {"id": "A7115921-B645-232C-5FDF613CC8CA5248", "title": "Stories in Stone: Monuments at Battleground National Cemetery", "location": "6625 Georgia Ave NW, Washington, DC", "sitecode": "cwdw", "sitetype": "park", "parkfullname": "Civil War Defenses of Washington ", "latitude": "38.970754", "longitude": "-77.026697", "dates": ["2026-10-17", "2026-10-24", "2026-10-31"], "recurrencerule": "DTSTART=20261003T040000Z;UNTIL=20270327T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "10:00 AM", "timeend": "10:30 AM"}], "isfree": "true", "types": ["Talk"], "description": "<p>Join a National Park Service ranger for a guided walk-through Battleground National Cemetery to explore Civil War monuments, their symbols, and the stories they were built to preserve. Discover how art, memory, and history come together in this quiet landscape where Washington, DC’s past is written in stone. As a part of the Freedom 250 commemoration, this program invites visitors to reflect on sacrifice, citizenship, and the enduring power of public memory.   <br /> <br />Please note that parking is limited to public streets.   </p>"}  # noqa: E501
CW_HEROES = {"id": "A6E146DE-B965-23A4-793385D42D061348", "title": "Heroes Remembered: Stories from Battleground National Cemetery", "location": "Battleground National Cemetery\n6625 Georgia Avenue NW \nWashington, DC", "sitecode": "cwdw", "sitetype": "park", "parkfullname": "Civil War Defenses of Washington ", "latitude": "38.970712", "longitude": "-77.026777", "dates": ["2026-10-14", "2026-10-21", "2026-10-28"], "recurrencerule": "DTSTART=20261007T040000Z;UNTIL=20270331T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=WE;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "11:00 AM", "timeend": "11:30 AM"}], "isfree": "true", "types": ["Talk"], "description": "<p><span data-contrast=\"auto\"> Join a ranger for a 30-minute, family-friendly walk through Battleground National Cemetery and discover the stories of the soldiers buried here.</span></p>"}  # noqa: E501
CW_HEROES_2 = {"id": "A7286A74-FAE3-A88E-739FE5F812F8CF52", "title": "Heroes Remembered: Stories from Battleground National Cemetery", "location": "Battleground National Cemetery\n6625 Georgia Avenue NW \nWashington, DC", "sitecode": "cwdw", "sitetype": "park", "parkfullname": "Civil War Defenses of Washington ", "latitude": "38.970712", "longitude": "-77.026777", "dates": ["2026-10-17", "2026-10-24", "2026-10-31"], "recurrencerule": "DTSTART=20261007T040000Z;UNTIL=20270331T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "12:30 PM", "timeend": "01:00 PM"}], "isfree": "true", "types": ["Talk"], "description": "<p><span data-contrast=\"auto\"> Join a ranger for a 30-minute, family-friendly walk through Battleground National Cemetery and discover the stories of the soldiers buried here.</span></p>"}  # noqa: E501
CLEANUP = {"id": "1B8EC426-E91D-BCB0-3AD2BCD43E0521A1", "title": "Monthly Shepherd Parkway Community Clean-Up", "location": "555 Newcomb St SE \nWashington, DC 20032", "sitecode": "cwdw", "sitetype": "park", "parkfullname": "Civil War Defenses of Washington ", "latitude": "", "longitude": "", "dates": ["2026-11-14", "2026-12-12"], "recurrencerule": "DTSTART=20260509T040000Z;UNTIL=20261212T050000Z;FREQ=MONTHLY;BYSETPOS=2;WKST=SU;BYDAY=SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "10:30 AM", "timeend": "01:00 PM"}], "isfree": "true", "types": ["Volunteer Event"], "regresurl": "https://www.volunteer.gov/s/global-search/shepherd%20parkway", "description": "<p>Come cleanup Shepherd Parkway with Ward 8 Woods!</p>\n<p>Meet at 555 Newcomb St SE at 10:30 AM to receive instructions and supplies (gloves, bags, and trash grabbers will be provided.)</p>\n<p><strong>Sign-up here: </strong><a href=\"https://www.volunteer.gov/s/global-search/shepherd%20parkway\">https://www.volunteer.gov/s/global-search/shepherd%20parkway</a></p>"}  # noqa: E501
# Made up: the clean-up's row with a location that names a place inside the
# park and no street, so neither a centre nor the sync's geocoder can place it.
FORT_DUPONT = dict(CLEANUP, id="MADE-UP-FORT-DUPONT", title="Fort Dupont Park Community Clean-Up",
                   location="Fort Dupont Park, at the Activity Center")
CWDW = [CW_JR, CW_STONE, CW_HEROES, CW_HEROES_2, CLEANUP, FORT_DUPONT]
STONE_POINT = (38.970754, -77.026697)

# Doors: rows whose blurb or title fooled the old substring rule. Descriptions
# are cut to the sentence that did it, HTML and all.
CRATER = {"id": "6ABAC5E9-C366-5BCB-07D54B8E11A07F51", "title": "Battle of the Crater", "location": "", "sitecode": "pete", "sitetype": "park", "parkfullname": "Petersburg National Battlefield", "latitude": "37.216821", "longitude": "-77.377037", "dates": ["2026-10-11", "2026-10-17", "2026-10-18", "2026-10-24", "2026-10-25", "2026-10-31"], "recurrencerule": "DTSTART=20260905T040000Z;UNTIL=20261031T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU,SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "11:00 AM", "timeend": "11:45 AM"}], "isfree": "true", "types": ["Talk"], "description": "<p>An explosion shook the earth early in the morning, on July 30, 1864. Soon after the battle of the Crater begins.</p>\n<p>Join a Park Ranger for a 30-45-minute program discussing the Pennsylvania soldiers who engineered and constructed the over 500-foot tunnel.</p>"}  # noqa: E501
GROUND_UP = {"id": "0C93CCCE-CCEE-1667-5626A884B312D2CA", "title": "From the Ground Up", "location": "Fort Monroe Visitor & Education Center\n30 Ingalls Road\nFort Monroe, VA 23651", "sitecode": "fomr", "sitetype": "park", "parkfullname": "Fort Monroe National Monument", "latitude": "37.002193", "longitude": "-76.310863", "dates": ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-27", "2026-10-28", "2026-10-29"], "recurrencerule": "DTSTART=20260901T040000Z;UNTIL=20261029T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=TU,WE,TH;COUNT=0;INTERVAL=1|EXDATE=2026-09-02,2026-09-03,2026-09-16,2026-09-23,2026-09-24,2026-10-01,2026-09-30", "cancelleddatelist": [], "times": [{"timestart": "01:00 PM", "timeend": "02:00 PM"}], "isfree": "true", "types": ["Guided Tour", "Walk"], "description": "<p class=\"xmsonormal\" style=\"background: white; vertical-align: baseline;\"><span style=\"font-family: 'Calibri',sans-serif; color: black;\">Join us on a ranger-led walk to explore and learn about some of the individually listed buildings on the National Register. Discover how these locations contribute the \"building\" of Fort Monroe \"From the Ground Up.\" Comfortable footwear and water are recommended. Exterior views of the included structures only.  </span></p>"}  # noqa: E501
BIRD_WALK = {"id": "25675C61-09EB-8A68-D0D7DE78C187294F", "title": "Bird Walk at Great Falls Park", "location": "", "sitecode": "gwmp", "sitetype": "park", "parkfullname": "George Washington Memorial Parkway", "latitude": "38.996876", "longitude": "-77.254821", "dates": ["2026-10-11", "2026-10-18", "2026-10-25", "2026-11-01"], "recurrencerule": "DTSTART=20260927T040000Z;UNTIL=20261231T050000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SU;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "08:00 AM", "timeend": "11:00 AM"}], "isfree": "true", "types": ["Guided Tour", "Volunteer Event", "Walk"], "description": "<p>Bird Walk at Great Falls Park</p>\n<p>Volunteer Guided - Every Sunday 8:00 am. Meet in Great Falls Park Main Parking Lot.</p>"}  # noqa: E501
HIKE_SMART = {"id": "87DE22FB-C639-B37B-26BF070448FD51B3", "title": "Hike Smart: The Ten Essentials and Leave No Trace Principles (Peaks of Otter - Milepost 86)", "location": "Located by the Sharp Top Store.", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "37.443389", "longitude": "-79.609278", "dates": ["2026-10-14"], "recurrencerule": "", "cancelleddatelist": [], "times": [{"timestart": "02:00 PM", "timeend": "03:00 PM"}], "isfree": "true", "types": ["Talk"], "description": "<div class=\"x_elementToProof\" data-ogsc=\"rgb(0, 0, 0)\" data-olk-copy-source=\"MessageBody\">Before your next adventure in the park, talk to a Ranger about the Ten Essentials and Leave No Trace Principles.</div>"}  # noqa: E501
POLLY = {"id": "ED95A928-05D1-B6F3-6A7AEB1926FDDD6E", "title": "Polly Wood's Ordinary Open House (Peaks of Otter - Milepost 86)", "location": "Polly Wood's Ordinary can be accessed from the Peaks of Otter Picnic Area or the Abbott Lake Trail located behind the Peaks of Otter Lodge.", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "37.443650", "longitude": "-79.599822", "dates": ["2026-10-17"], "recurrencerule": "DTSTART=20260613T040000Z;UNTIL=20261017T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=SA;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "01:00 PM", "timeend": "04:00 PM"}], "isfree": "true", "types": ["Guided Tour", "Talk"], "description": "<p>Step into history with a ranger as we unveil the fascinating story of the Wood Family, their Ordinary, and tourism at Peaks of Otter!</p>"}  # noqa: E501
BRINEGAR = {"id": "90BFD138-E6AA-3CBA-B6DF090507BC716F", "title": "Brinegar Cabin Open House (Doughton Park - Milepost 238.5)", "location": "Brinegar Cabin is located at milepost 238.5 on the Blue Ridge Parkway. Park in the parking and follow the paved walkway to the cabin.", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "36.418721", "longitude": "-81.146435", "dates": ["2026-10-12", "2026-10-15", "2026-10-19", "2026-10-22", "2026-10-26", "2026-10-29"], "recurrencerule": "DTSTART=20260511T040000Z;UNTIL=20261031T040000Z;FREQ=WEEKLY;WKST=SU;BYDAY=MO,TH;COUNT=0;INTERVAL=1|EXDATE=2026-06-15,2026-08-31,2026-08-24", "cancelleddatelist": [], "times": [{"timestart": "01:00 PM", "timeend": "04:00 PM"}], "isfree": "true", "types": ["Talk"], "description": "<div>Step back into the late 1800s at one of Doughton Park’s most cherished historic sites. Completed in 1889, the Brinegar family home offers visitors a window into mountain life.</div>\n<div>Rangers and volunteers are on site to share stories of the Brinegar Family.</div>"}  # noqa: E501
MIDDAY = {"id": "EB178B2A-BA9F-55CE-1A9EB5756E17B294", "title": "Midday Mountain Music (Blue Ridge Music Center - Milepost 213)", "location": "The Blue Ridge Music Center is located on Music Center Road, Galax, VA 24333 or milepost 213 on the Blue Ridge Parkway", "sitecode": "blri", "sitetype": "park", "parkfullname": "Blue Ridge Parkway", "latitude": "36.573197", "longitude": "-80.850001", "dates": ["2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"], "recurrencerule": "DTSTART=20261001T040000Z;UNTIL=20261101T040000Z;FREQ=DAILY;WKST=SU;COUNT=0;INTERVAL=1", "cancelleddatelist": [], "times": [{"timestart": "12:00 PM", "timeend": "03:00 PM"}], "isfree": "true", "types": ["Partner Program", "Performance"], "description": "<p>Midday Mountain Music is a free, informal music presentation featuring local and regional musicians held in the Blue Ridge Music Center's covered breezeway.</p>"}  # noqa: E501
DOORS = [CRATER, GROUND_UP, BIRD_WALK, HIKE_SMART, POLLY, BRINEGAR, MIDDAY]

CENTERS = [
    {"id": "378AF0F6-F593-496E-890E-3C5B5AF07D58", "parkCode": "blri", "name": "Waterrock Knob Visitor Center (Milepost 451.2)", "latitude": "35.46018574549927", "longitude": "-83.14102427298599"},  # noqa: E501
    {"id": "72C788FB-B248-454E-A6E2-E728210B913D", "parkCode": "blri", "name": "Blue Ridge Parkway Visitor Center (Milepost 384)", "latitude": "35.56582264608358", "longitude": "-82.48632347368624"},  # noqa: E501
    {"id": "2FC73AF9-095D-40DF-8D7C-2314C3866295", "parkCode": "blri", "name": "Folk Art Center (Milepost 382)", "latitude": "35.59258704624832", "longitude": "-82.48171397272336"},  # noqa: E501
    {"id": "FB928789-B6F1-4BE8-BA1D-8B9A59BE4954", "parkCode": "blri", "name": "Craggy Gardens Visitor Center (Milepost 364)", "latitude": "35.700272546663854", "longitude": "-82.37964797314828"},  # noqa: E501
    {"id": "1FF8ED1A-E4F2-4264-A406-A2DADC5E00F7", "parkCode": "blri", "name": "Museum of North Carolina Minerals (Milepost 331)", "latitude": "35.85413194653189", "longitude": "-82.05144267379194"},  # noqa: E501
    {"id": "1F3A3F9B-4E7F-4B86-B69A-E0F98A231CF6", "parkCode": "blri", "name": "Linville Falls Visitor Center (Milepost 316)", "latitude": "35.95456944576354", "longitude": "-81.92773877250863"},  # noqa: E501
    {"id": "1AAE8238-CF5A-4CAD-8139-D97F42557D86", "parkCode": "blri", "name": "Linn Cove Viaduct Visitor Center (Milepost 304)", "latitude": "36.09018314574874", "longitude": "-81.8150071734816"},  # noqa: E501
    {"id": "3815A0FA-8EF3-4B88-A394-FA328929497A", "parkCode": "blri", "name": "Moses Cone Manor House and Visitor Center (Milepost 294)", "latitude": "36.14920704552366", "longitude": "-81.69296297346614"},  # noqa: E501
    {"id": "D5A81D25-1F20-476D-AF29-60205A3F057F", "parkCode": "blri", "name": "Blue Ridge Music Center (Milepost 213)", "latitude": "36.57319744648645", "longitude": "-80.85000087193924"},  # noqa: E501
    {"id": "C00FDDD1-6E2E-4B20-8CAD-F18A38C28E4D", "parkCode": "blri", "name": "Doughton Park Visitor Center (Milepost 241)", "latitude": "36.433691039", "longitude": "-81.1775776"},  # noqa: E501
    {"id": "E97699E2-26B0-4E77-A7FA-2CD307EC87F4", "parkCode": "blri", "name": "Rocky Knob Visitor Center (Milepost 169)", "latitude": "36.81150144495113", "longitude": "-80.34987657202316"},  # noqa: E501
    {"id": "0831D9CA-E08E-41CE-B8D4-A7C951FD3033", "parkCode": "blri", "name": "Peaks of Otter Visitor Center (Milepost 85.6)", "latitude": "37.445606544928985", "longitude": "-79.60995817217201"},  # noqa: E501
    {"id": "3800D3AA-5049-4FA0-9200-7B828A37009F", "parkCode": "blri", "name": "James River Visitor Center (Milepost 63.6)", "latitude": "37.55516874534699", "longitude": "-79.36589267109593"},  # noqa: E501
    {"id": "0128B257-D489-4C15-807E-6C73A76CD8F9", "parkCode": "blri", "name": "Humpback Rocks Visitor Center (milepost 5.8)", "latitude": "37.97272404524165", "longitude": "-78.89946447123623"},  # noqa: E501
]
CRAGGY_VC = (35.700272546663854, -82.37964797314828)

# Set by hand (see PARKS in the docstring): the shape is the swagger's.
PARKS = [
    {"id": "P-hafe", "parkCode": "hafe", "fullName": "Harpers Ferry National Historical Park", "latitude": "39.3220", "longitude": "-77.7290"},  # noqa: E501
    {"id": "P-bowa", "parkCode": "bowa", "fullName": "Booker T. Washington National Monument", "latitude": "37.1220", "longitude": "-79.7330"},  # noqa: E501
    {"id": "P-gewa", "parkCode": "gewa", "fullName": "George Washington Birthplace National Monument", "latitude": "38.1860", "longitude": "-76.9200"},  # noqa: E501
    {"id": "P-blri", "parkCode": "blri", "fullName": "Blue Ridge Parkway", "latitude": "36.6000", "longitude": "-80.4000"},  # noqa: E501
    {"id": "P-cwdw", "parkCode": "cwdw", "fullName": "Civil War Defenses of Washington", "latitude": "38.9600", "longitude": "-77.0300"},  # noqa: E501
]
CWDW_PARK = (38.9600, -77.0300)                  # 1.2 km from Battleground: the size test passes
BOWA_PARK = (37.1220, -79.7330)
HAFE_PARK = (39.3220, -77.7290)

VA_SAMPLE = [HAFE_SUN, HAFE_SAT, BOWA_TOUR, BOWA_HIKE, GEWA, CRAGGY, BUCK, BUCK_TALK, MABRY_A, MABRY_B]


def paging_events(n):
    return [dict(HAFE_SUN, id=f"PAGING-{i:04d}", title=f"Paging probe {i}") for i in range(n)]


# ------------------------------------------------------------------ fakes --
class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeAPI:
    """developer.nps.gov as measured: pageNumber pages, `start` is never read,
    a page is at most `cap` rows, and the echo says the page it served."""

    def __init__(self, states, cap=50, honour_page=True, script=None, remaining=None):
        self.states, self.cap, self.honour_page = states, cap, honour_page
        self.script = list(script or [])      # (status, body) answers served first, in order
        self.remaining = remaining
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        ep = url.rsplit("/", 1)[-1]
        params = dict(params or {})
        self.calls.append((ep, params, dict(headers or {})))
        hdr = {"X-RateLimit-Remaining": str(self.remaining)} if self.remaining is not None else {}
        if self.script:
            status, body = self.script.pop(0)
            return Resp(status, body, hdr)
        if ep == "events":
            rows = self.states.get(params.get("stateCode"), [])
            size = min(int(params.get("pageSize", 10)), self.cap)
            page = int(params.get("pageNumber", 1)) if self.honour_page else 1
            chunk = rows[(page - 1) * size: page * size]
            return Resp(200, {"total": str(len(rows)), "pagesize": str(size), "pagenumber": str(page),
                              "data": chunk, "errors": []}, hdr)
        codes = set(str(params.get("parkCode", "")).split(","))
        if ep == "parks":
            rows = [p for p in PARKS if p["parkCode"] in codes]
        elif ep == "visitorcenters":
            rows = [c for c in CENTERS if c["parkCode"] in codes]
        else:
            rows = []
        return Resp(200, {"total": str(len(rows)), "limit": str(params.get("limit", 50)),
                          "start": str(params.get("start", 0)), "data": rows}, hdr)

    def events_calls(self):
        return [p for ep, p, _ in self.calls if ep == "events"]


def run_main(api, argv):
    """main() against the fake, into a scratch store: (exit code, stdout, store dict)."""
    real = requests.Session
    requests.Session = lambda: api
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "nps.json")
        try:
            with contextlib.redirect_stdout(out):
                rc = N.main(["--api-key", "test-key", "--store", path] + argv)
        except SystemExit as exc:                                 # an argument the reader lacks
            rc = exc.code
        except Exception as exc:                                  # noqa: BLE001
            rc = f"{type(exc).__name__}: {exc}"
        finally:
            requests.Session = real
        store = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"events": []}
    return rc, out.getvalue(), store


def rows_of(store, event):
    """The rows written for one NPS event, by its id, as {day: record}."""
    got = {}
    for rec in store.get("events", []):
        for ref in rec.get("sources") or []:
            sid = str(ref.get("source_id") or "")
            if ref.get("source") == "nps" and sid.startswith(event["id"] + "|"):
                got[sid.split("|", 1)[1]] = rec
    return got


def event_ids(store):
    return {str(ref.get("source_id")).split("|", 1)[0]
            for rec in store.get("events", []) for ref in rec.get("sources") or []}


def tomb_fps(store):
    return {t.get("fingerprint") for t in store.get("tombstones") or []}


def fp(event, day):
    return make_fingerprint(event["title"], day, event.get("location") or event["parkfullname"])


# ------------------------------------------------------------------ paging --
api = FakeAPI({"VA": paging_events(103)})
rc, out, store = run_main(api, ["--states", "VA"])
check("a 3-page state is read whole: all 103 events (the old reader got page 1, 50)",
      len(event_ids(store)) == 103, f"{len(event_ids(store))} events, rc {rc}")
calls = api.events_calls()
check("pages are asked for by pageNumber 1, 2, 3 and never by start",
      [c.get("pageNumber") for c in calls] == [1, 2, 3] and not any("start" in c for c in calls),
      calls)
check("the read is windowed by the API: dateStart today, dateEnd today + 90, expandRecurring false",
      bool(calls) and all(c.get("dateStart") == "2026-10-11" and c.get("dateEnd") == "2027-01-09"
                          and str(c.get("expandRecurring")).lower() == "false" for c in calls), calls[:1])
check("the key travels in the X-Api-Key header, never in a URL",
      all(h.get("X-Api-Key") == "test-key" and "api_key" not in p for _, p, h in api.calls), api.calls[:1])

api = FakeAPI({"VA": paging_events(103)}, cap=40)
rc, out, store = run_main(api, ["--states", "VA"])
check("a page smaller than asked for is not a short page: 40/40/23 reads all 103",
      len(event_ids(store)) == 103 and len(api.events_calls()) == 3,
      f"{len(event_ids(store))} events in {len(api.events_calls())} requests")

api = FakeAPI({"VA": paging_events(103)}, honour_page=False)
rc, out, store = run_main(api, ["--states", "VA"])
check("an API that ignores the page number costs 2 requests, not a loop, and says so",
      len(api.events_calls()) == 2 and "repeats page 1" in out and len(event_ids(store)) == 50,
      f"{len(api.events_calls())} requests, {len(event_ids(store))} events")

api = FakeAPI({"VA": paging_events(100)})
run_main(api, ["--states", "VA"])
check("a state of exactly 100 is 2 requests, with no empty third page",
      len(api.events_calls()) == 2, len(api.events_calls()))

# ------------------------------------------------------------------ states --
api = FakeAPI({})
run_main(api, [])
asked = [c.get("stateCode") for c in api.events_calls()]
check("the default sweep is every state, DC and the five territories (56; the old list had 23)",
      len(asked) == 56 and len(set(asked)) == 56, f"{len(asked)}: {asked}")
check("states the old list never asked are asked: WV, KY, OH, MD, ME, MT, WY, PR, VI, GU, AS, MP",
      {"WV", "KY", "OH", "MD", "ME", "MT", "WY", "PR", "VI", "GU", "AS", "MP"} <= set(asked), asked)

# ---------------------------------------------- one sweep, two states, places --
api = FakeAPI({"VA": VA_SAMPLE, "NC": [CRAGGY, MABRY_A, MABRY_B, BUCK]})
rc, out, store = run_main(api, ["--states", "VA,NC"])
nc_line = next((ln for ln in out.splitlines() if ln.startswith("[nps] NC:")), "")
check("an event a second state lists again is read once: NC adds 0 new",
      "0 new to this run" in nc_line, nc_line or out)
done = next((ln for ln in out.splitlines() if ln.startswith("[nps] done:")), "")
logged = [int(w) for w, nxt in zip(done.split(), done.split()[1:]) if w.isdigit() and nxt.startswith("row")]
check("the log's row count is the store's (Virginia's log said 903 for 129)",
      logged == [len(store["events"])], f"{done!r} vs {len(store['events'])} rows")

sweep_store = store
craggy = rows_of(store, CRAGGY)
check("a hike with no coordinates that names its visitor centre is pinned there",
      bool(craggy) and all(abs(r["latitude"] - CRAGGY_VC[0]) < 1e-9 and abs(r["longitude"] - CRAGGY_VC[1]) < 1e-9
                           for r in craggy.values()), craggy)
check("a Parkway programme naming no centre is left out: the Parkway's centres span 470 km",
      not rows_of(store, BUCK_TALK), rows_of(store, BUCK_TALK))
hike = rows_of(store, BOWA_HIKE)
check("a compact park's event without coordinates gets the park's point",
      bool(hike) and all((r["latitude"], r["longitude"]) == BOWA_PARK for r in hike.values()), hike)
sat = rows_of(store, HAFE_SAT)
check("Harpers Ferry's Saturday talk (no coordinates) is placed at the park, 0.1 km from its Sunday one",
      bool(sat) and all((r["latitude"], r["longitude"]) == HAFE_PARK for r in sat.values()), sat)
check("a park we know nothing else about places nothing (George Washington Birthplace)",
      not rows_of(store, GEWA), rows_of(store, GEWA))
parks_calls = [p for ep, p, _ in api.calls if ep == "parks"]
center_calls = [p for ep, p, _ in api.calls if ep == "visitorcenters"]
check("/parks and /visitorcenters are each asked once, with one comma list of the waiting parks",
      len(parks_calls) == 1 and len(center_calls) == 1
      and set(parks_calls[0].get("parkCode", "").split(",")) == {"blri", "bowa", "gewa", "hafe"},
      (parks_calls, center_calls))
check("a placed event keeps its own fingerprint and source_id (title | day | place)",
      "2026-10-11" in craggy and craggy["2026-10-11"]["fingerprint"] == fp(CRAGGY, "2026-10-11"),
      list(craggy))

# ------------------------------------------------------- called-off days --
sun = rows_of(store, HAFE_SUN)
check("an EXDATE the API still lists in `dates` is not written (Harpers Ferry, 2026-10-18)",
      "2026-10-18" not in sun and {"2026-10-11", "2026-10-25", "2026-11-01"} <= set(sun), sorted(sun))
check("a day in cancelleddatelist is a tombstone under the live row's fingerprint (Buck Spring, 10/11/2026)",
      fp(BUCK, "2026-10-11") in tomb_fps(store) and not rows_of(store, BUCK), sorted(tomb_fps(store))[:3])
check("Harpers Ferry's Saturday EXDATE inside the week is a tombstone too (2026-10-17)",
      fp(HAFE_SAT, "2026-10-17") in tomb_fps(store) and "2026-10-17" not in sat, sorted(sat))
mabry = {}
for ev in (MABRY_A, MABRY_B):
    mabry.update(rows_of(store, ev))
check("a day both Mabry Mill copies exclude is a tombstone and no row (2026-10-11)",
      fp(MABRY_B, "2026-10-11") in tomb_fps(store) and "2026-10-11" not in mabry, sorted(mabry))
check("a day one copy excludes and the other lists live stays live (2026-10-17)",
      "2026-10-17" in mabry and fp(MABRY_B, "2026-10-17") not in tomb_fps(store), sorted(mabry))
check("an excluded day beyond the next week is neither written nor tombstoned yet (2026-10-24)",
      fp(MABRY_A, "2026-10-24") not in tomb_fps(store) and "2026-10-24" not in rows_of(store, MABRY_A),
      sorted(tomb_fps(store))[:3])

# ------------------------------------------------------------- recurrence --
tour = rows_of(store, BOWA_TOUR)
check("a programme on 5 days a week is written for every listed day of the next week",
      set(tour) == {"2026-10-11", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"}, sorted(tour))
check("a weekly one still gets its next 3 (Footsteps of Freedom: 10-17, 10-24, 10-31)",
      set(hike) == {"2026-10-17", "2026-10-24", "2026-10-31"}, sorted(hike))
r = tour.get("2026-10-14") or {}
check("identity is unchanged: fingerprint title|day|park, source_id id|day, clock from times[0]",
      r.get("fingerprint") == fp(BOWA_TOUR, "2026-10-14")
      and any(s.get("source_id") == f"{BOWA_TOUR['id']}|2026-10-14" for s in r.get("sources") or [])
      and r.get("start_local") == "2026-10-14T14:00:00", r)

# ------------------------------------------------ a place finer than the park --
api = FakeAPI({"DC": CWDW, "VA": DOORS})
rc, out, store = run_main(api, ["--states", "DC,VA"])
clean = rows_of(store, CLEANUP)
check("a clean-up 14 km from the park's other events is not pinned at the park's point (Shepherd Parkway)",
      bool(clean) and all(r.get("latitude") is None and r.get("longitude") is None for r in clean.values()),
      {d: (r.get("latitude"), r.get("longitude")) for d, r in clean.items()} or out)
r = next(iter(clean.values()), {})
check("...it goes to the sync as street, city and state, which the sync's Census pass reads",
      (r.get("address"), r.get("city"), r.get("region"), r.get("postal_code"))
      == ("555 Newcomb St SE", "Washington", "DC", "20032")
      and S._addr_parts(r) == ("555 Newcomb St SE", "Washington", "DC"),
      {k: r.get(k) for k in ("address", "city", "region", "postal_code")})
check("a location naming a place inside the park, and no street, places nothing (made-up Fort Dupont row)",
      not rows_of(store, FORT_DUPONT), rows_of(store, FORT_DUPONT))
stone = rows_of(store, CW_STONE)
check("an event with its own point keeps it, with no address for the sync's geocoder to move it by",
      bool(stone) and all((s_["latitude"], s_["longitude"]) == STONE_POINT and not s_.get("address")
                          for s_ in stone.values()), stone)

sa = getattr(N, "street_address", None)
check("street_address reads the four shapes NPS writes: lines, commas, no ZIP, in brackets",
      sa is not None
      and sa("555 Newcomb St SE \nWashington, DC 20032") == ("555 Newcomb St SE", "Washington", "DC", "20032")
      and sa("Cold Harbor Battlefield Visitor Center,5515 Anderson-Wright Drive, Mechanicsville, VA 23111.")
      == ("5515 Anderson-Wright Drive", "Mechanicsville", "VA", "23111")
      and sa("Battleground National Cemetery \n6625 Georgia Ave NW\nWashington DC")
      == ("6625 Georgia Ave NW", "Washington", "DC", None)
      and sa("Meet outside behind the Peaks of Otter Lodge (85554 Blue Ridge Parkway, Bedford, VA 24523) by the lake.")
      == ("85554 Blue Ridge Parkway", "Bedford", "VA", "24523"), "street_address" if sa is None else "")
check("...and nothing that is not a whole one: no city, a milepost, a street run into its town",
      sa is not None
      and sa("Rain location will be inside Visitor Center, Great Falls Park, "
             "9200 Old Dominion Dr. McLean, VA 22102") is None
      and sa("Meet at the Buck Spring Overlook, at Milepost 407.6.") is None
      and sa("Meet at St. Thomas Chapel, 7854 Church Street in Middletown, Va.") is None)

# ------------------------------------------------------------------- doors --
def door(st, ev):
    got = rows_of(st, ev)
    rec = got[min(got)] if got else {}
    return rec.get("category"), rec.get("categories") or []


check("a talk whose blurb says the explosion 'shook the earth' is learning, not arts (Battle of the Crater)",
      door(store, CRATER) == ("learning", []), door(store, CRATER))
check("a walk whose HTML says font-family is outdoors and learning, not kids (From the Ground Up)",
      door(store, GROUND_UP) == ("outdoors", ["learning"]), door(store, GROUND_UP))
check("a bird walk 'Volunteer Guided' is outdoors, not volunteer (its leaders volunteer; it asks nobody to)",
      door(store, BIRD_WALK)[0] == "outdoors", door(store, BIRD_WALK))
check("'Hike Smart' is outdoors, not arts (sm-ART)", door(store, HIKE_SMART)[0] == "outdoors", door(store, HIKE_SMART))
check("'the Wood Family' and 'rangers and volunteers are on site' are learning (Polly Wood's, Brinegar Cabin)",
      door(store, POLLY)[0] == "learning" and door(store, BRINEGAR)[0] == "learning",
      (door(store, POLLY), door(store, BRINEGAR)))
check("a Volunteer Event that signs up on volunteer.gov is volunteer (the clean-up)",
      door(store, CLEANUP)[0] == "volunteer", door(store, CLEANUP))
check("a Performance by musicians is music (Midday Mountain Music)", door(store, MIDDAY)[0] == "music",
      door(store, MIDDAY))
check("'family-friendly' and 'art, memory' in a talk's blurb are learning (Battleground's Heroes and Stones)",
      door(store, CW_HEROES)[0] == "learning" and door(store, CW_STONE)[0] == "learning",
      (door(store, CW_HEROES), door(store, CW_STONE)))
check("every hike and walk title in the fixtures is outdoors (the old rule sent 14 of Virginia's 23 elsewhere)",
      all(door(st, ev)[0] == "outdoors" for st, ev in
          ((store, GROUND_UP), (store, BIRD_WALK), (store, HIKE_SMART),
           (sweep_store, CRAGGY), (sweep_store, BOWA_TOUR), (sweep_store, BOWA_HIKE))),
      [door(st, ev) for st, ev in ((store, GROUND_UP), (sweep_store, BOWA_TOUR), (sweep_store, BOWA_HIKE))])
check("a title's '(Folk Art Center - Milepost 382)' is a place, not an art word (made-up title)",
      N._category(dict(HAFE_SUN, title="Ranger Talk (Folk Art Center - Milepost 382)")) == "learning",
      N._category(dict(HAFE_SUN, title="Ranger Talk (Folk Art Center - Milepost 382)")))
check("with no types, the blurb is read as whole words: 'earth' is not art",
      N._category(dict(CRATER, types=[])) != "arts", N._category(dict(CRATER, types=[])))
check("the sync keeps each door: derive_categories on the stored rows",
      [S.derive_categories(next(iter(rows_of(store, ev).values()), {}))[0]
       for ev in (CRATER, GROUND_UP, BIRD_WALK, HIKE_SMART, POLLY, CLEANUP)]
      == ["learning", "outdoors", "outdoors", "outdoors", "learning", "volunteer"],
      [S.derive_categories(next(iter(rows_of(store, ev).values()), {"name": ""}))[0]
       for ev in (CRATER, GROUND_UP, BIRD_WALK, HIKE_SMART, POLLY, CLEANUP)])

# ------------------------------------------------------------- the budget --
api = FakeAPI({"VA": paging_events(103)})
rc, out, store = run_main(api, ["--states", "VA", "--max-requests", "2"])
check("--max-requests 2 stops after 2 requests and keeps what they read (100 events)",
      rc == 0 and len(api.calls) == 2 and len(event_ids(store)) == 100,
      f"rc {rc}, {len(api.calls)} requests, {len(event_ids(store))} events")

over = {"error": {"code": "OVER_RATE_LIMIT", "message": "You have exceeded your rate limit."}}
api = FakeAPI({"VA": VA_SAMPLE, "NC": VA_SAMPLE}, script=[(429, over)])
rc, out, store = run_main(api, ["--states", "VA,NC"])
check("a 429 ends the run after that one request: not retried, no next state",
      rc == 0 and len(api.calls) == 1 and "STOPPED" in out, f"rc {rc}, {len(api.calls)} requests")

bad = {"error": {"code": "API_KEY_INVALID", "message": "An invalid api_key was supplied."}}
api = FakeAPI({"VA": VA_SAMPLE, "NC": VA_SAMPLE}, script=[(403, bad)])
rc, out, store = run_main(api, ["--states", "VA,NC"])
check("a 403 ends the run after that one request", rc == 0 and len(api.calls) == 1,
      f"rc {rc}, {len(api.calls)} requests")

api = FakeAPI({"VA": paging_events(103)}, remaining=5)
rc, out, store = run_main(api, ["--states", "VA"])
check("X-RateLimit-Remaining at 5 stops the next request (the key's hour is nearly spent)",
      len(api.calls) == 1 and len(event_ids(store)) == 50, f"{len(api.calls)} requests")

api = FakeAPI({"VA": VA_SAMPLE}, script=[(502, None)])
rc, out, store = run_main(api, ["--states", "VA"])
check("a 502 is retried once, and the state is then read",
      len(api.events_calls()) == 2 and bool(rows_of(store, BOWA_TOUR)), f"{len(api.events_calls())} requests")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
