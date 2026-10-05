#!/usr/bin/env python3
"""
test_ingest_phl_parks.py - Philadelphia Parks & Recreation Finder programmes,
and the ways a weekly schedule table is not a timetable.

Every schedule, programme, facility, locator point and activity type in
FIXTURE was read live from phl.carto.com on 2026-10-04 (trimmed to the columns
the adapter reads, descriptions cut at 240 characters); synthetic cases are
built in code and say so; the LIVE_CASES carry real catalogue text read on
2026-10-05. The expensive cases are the quiet ones: a sign-up course written as
something anyone can walk into (registration_status says "Registering" on the
open gym AND on the after-school club, so it cannot be the gate; the Finder's
own "To sign up visit:" link and "registration required" in the description
can), "walk in" the verb and "open to all ages" read as drop-in, a camp's "free
play", a "Third Wednesdays" open mic expanded to every Wednesday, an open gym on
Thanksgiving, a 15:00 that moves an hour at the DST change, a row spanning the
gap between two sessions, a recovery group pinned on a map, a paid "Free Play"
tagged free, and a date written outside its programme's season - at either end.

    python test_ingest_phl_parks.py
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from urllib.parse import urlsplit

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261004"

from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_phl_parks as T  # noqa: E402
from mapsee_ingest import EventStore, VALID_CATEGORIES  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "phl_parks_sources.json")
CFG = T.load_config(CFG_PATH)
TZ = ZoneInfo(CFG["timezone"])
TODAY = T._today(TZ)
SRC = CFG["sources"][0]
HORIZON = TODAY + timedelta(days=SRC["horizon_days"])
SKIP = set(CFG["skip_dates"])

# ../mapsee migration 0227 reads `offer:free` from a row's own text. A SUBSET of
# ../mapsee/tools/measure_deals.py, kept offline: FREE's "free drop-in" and
# "price: free" branches on the text, FREE_TITLE's bare "free" on the TITLE
# only, and FREE_NEG over the text. The full classify over the 2026-10-05 dry
# run tagged free exactly the 0.00 rows and no fee row.
FREE_TEXT = re.compile(r"(?<![-\w/])free\s+(?:drop[- ]in|to\s+attend|admission)\b"
                       r"|\b(?:admission|entry|price|cost)\s*(?:is|:|-|\u2013)?\s*free\b", re.I)
FREE_TITLE = re.compile(r"(?<![-\w])free\b(?![-\w])", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b", re.I)


def tagged_free(ev):
    text = (ev.name or "") + "\n" + (ev.description or "")
    return bool(FREE_TEXT.search(text) or FREE_TITLE.search(ev.name or "")) and not FREE_NEG.search(text)


FIXTURE = json.loads(r"""{
 "schedules": [
  {"programs":"[\"689b1163c3a8d3031abfe2c1\"]","date_from":"2026-03-04T00:00:00Z","date_to":"2027-02-01T00:00:00Z","days":"[\"56fcf3238ddf5b391499f6cb\"]","time_from":"2012-01-01T17:00:00Z","time_to":"2012-01-01T17:45:00Z"},
  {"programs":"[\"69a9cdefacad6b2cc3d1f87f\"]","date_from":"2026-01-05T00:00:00Z","date_to":"2027-01-03T00:00:00Z","days":"[\"56fc90d38041752020119b23\", \"56fcf3238ddf5b391499f6cb\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"5e5139f0705fba0015d1d155\"]","date_from":"2026-01-01T00:00:00Z","date_to":"2026-12-31T00:00:00Z","days":"[\"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T19:00:00Z","time_to":"2012-01-01T21:00:00Z"},
  {"programs":"[\"57f41afac79b9e52368e5bbf\"]","date_from":"2026-05-01T00:00:00Z","date_to":"2027-05-28T00:00:00Z","days":"[\"56fcf3238ddf5b391499f6cb\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"6a553db853390233fcd3d315\"]","date_from":"2026-07-13T00:00:00Z","date_to":"2027-12-31T00:00:00Z","days":"[\"56fcf33a2b8f3451146ce0fa\", \"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\", \"56fcf334680e370b56ab5e3a\"]","time_from":"2012-01-01T00:00:00Z","time_to":"2012-01-01T00:00:00Z"},
  {"programs":"[\"6a553ee8d4b0710ca9423878\"]","date_from":"2026-07-13T00:00:00Z","date_to":"2027-12-31T00:00:00Z","days":"[\"56fcf33a2b8f3451146ce0fa\", \"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\", \"56fcf334680e370b56ab5e3a\"]","time_from":"2012-01-01T00:00:00Z","time_to":"2012-01-01T00:00:00Z"},
  {"programs":"[\"63f7c1f5485370002a221b91\"]","date_from":"2026-09-08T00:00:00Z","date_to":"2026-12-08T00:00:00Z","days":"[\"56fc90e5b67a9db679a60d03\"]","time_from":"2012-01-01T15:00:00Z","time_to":"2012-01-01T16:00:00Z"},
  {"programs":"[\"64ecde7d448bd40027ee0ecd\"]","date_from":"2026-09-07T00:00:00Z","date_to":"2026-12-11T00:00:00Z","days":"[\"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T13:15:00Z","time_to":"2012-01-01T15:00:00Z"},
  {"programs":"[\"67d087462b128102c84100e4\"]","date_from":"2026-09-08T00:00:00Z","date_to":"2027-05-21T00:00:00Z","days":"[\"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T13:30:00Z","time_to":"2012-01-01T15:00:00Z"},
  {"programs":"[\"6853155f1a3c7c02e0bcc2c3\"]","date_from":"2026-09-09T00:00:00Z","date_to":"2026-11-18T00:00:00Z","days":"[\"56fcf3238ddf5b391499f6cb\"]","time_from":"2012-01-01T18:30:00Z","time_to":"2012-01-01T20:30:00Z"},
  {"programs":"[\"6a8f269ede02d80882d20ac3\"]","date_from":"2026-09-15T00:00:00Z","date_to":"2026-11-10T00:00:00Z","days":"[\"56fc90e5b67a9db679a60d03\"]","time_from":"2012-01-01T18:30:00Z","time_to":"2012-01-01T19:30:00Z"},
  {"programs":"[\"584ef890e16412d54175d6a9\"]","date_from":"2026-09-09T00:00:00Z","date_to":"2026-12-16T00:00:00Z","days":"[\"56fc90e5b67a9db679a60d03\"]","time_from":"2012-01-01T18:30:00Z","time_to":"2012-01-01T07:30:00Z"},
  {"programs":"[\"57d9c166c51a0070666c74cd\"]","date_from":"2026-09-08T00:00:00Z","date_to":"2027-05-28T00:00:00Z","days":"[\"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T15:00:00Z","time_to":"2012-01-01T18:00:00Z"},
  {"programs":"[\"6a99dc1ffc4607f1d1ab8096\"]","date_from":"2026-09-15T00:00:00Z","date_to":"2026-11-10T00:00:00Z","days":"[\"56fc90e5b67a9db679a60d03\"]","time_from":"2012-01-01T16:00:00Z","time_to":"2012-01-01T17:30:00Z"},
  {"programs":"[\"57f435c851bc09d735bcc568\"]","date_from":"2026-09-01T00:00:00Z","date_to":"2027-09-01T00:00:00Z","days":"[\"56fc90e5b67a9db679a60d03\"]","time_from":"2012-01-01T19:00:00Z","time_to":"2012-01-01T21:00:00Z"},
  {"programs":"[\"57f2ea42887884d34abbf16c\"]","date_from":"2026-09-26T00:00:00Z","date_to":"2026-11-21T00:00:00Z","days":"[\"56fcf334680e370b56ab5e3a\"]","time_from":"2012-01-01T11:00:00Z","time_to":"2012-01-01T12:00:00Z"},
  {"programs":"[\"69c57498f603ea173c09b307\"]","date_from":"2026-09-21T00:00:00Z","date_to":"2026-12-21T00:00:00Z","days":"[\"56fc90d38041752020119b23\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"697d4d6e178250c8b8a05663\"]","date_from":"2026-09-23T00:00:00Z","date_to":"2026-12-23T00:00:00Z","days":"[\"56fcf3238ddf5b391499f6cb\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"650a0f71e62fee00289fc596\"]","date_from":"2026-10-07T00:00:00Z","date_to":"2026-12-16T00:00:00Z","days":"[\"56fcf3238ddf5b391499f6cb\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"650a3cf43e45bc0027b7458b\"]","date_from":"2026-10-06T00:00:00Z","date_to":"2026-12-22T00:00:00Z","days":"[\"56fc90e5b67a9db679a60d03\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"6747761d0b68d802ae3963b9\"]","date_from":"2026-10-05T00:00:00Z","date_to":"2026-12-21T00:00:00Z","days":"[\"56fc90d38041752020119b23\"]","time_from":"2012-01-01T18:00:00Z","time_to":"2012-01-01T20:00:00Z"},
  {"programs":"[\"57ed710245602c493d5bb175\"]","date_from":"2026-09-14T00:00:00Z","date_to":"2027-06-11T00:00:00Z","days":"[\"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T15:00:00Z","time_to":"2012-01-01T18:00:00Z"},
  {"programs":"[\"57d2f53af927e8cd4a9bcfaa\"]","date_from":"2026-08-24T00:00:00Z","date_to":"2027-06-10T00:00:00Z","days":"[\"56fc90d38041752020119b23\", \"56fc90e5b67a9db679a60d03\", \"56fcf3238ddf5b391499f6cb\", \"56fcf3292b8f3451146ce0f8\", \"56fcf32eb3d0292214d21d41\"]","time_from":"2012-01-01T15:00:00Z","time_to":"2012-01-01T18:00:00Z"}
 ],
 "programs": [
  {"id":"57f41afac79b9e52368e5bbf","program_name":"Alcoholics Anonymous","program_description":null,"facility":"[\"56a8f8537a8cee5e3a25aff9\"]","activity_type":"[\"56a8ed1e85a63ab10cff5877\"]","age_high":100,"age_range":"Adult ages 18-100","fee":null,"fee_frequency":"[\"Total\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"57f435c851bc09d735bcc568","program_name":"Holmesburg Civic association ","program_description":"<p>Members and non-members are welcome!  Community meetings to discuss concerns and special events.  Monthly guest speakers.  Some topics including: zoning, construction, elections, L & I concerns, new businesses, existing businesses, educa","facility":"[\"56a8f86c7a8cee5e3a25b0c9\"]","activity_type":"[\"56a8ed1e85a63ab10cff5877\"]","age_high":100,"age_range":"Adult ages 18-100","fee":"5.00","fee_frequency":"[\"Total\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"5e5139f0705fba0015d1d155","program_name":"Friday Night AA","program_description":"<p>Alcholics Anonymous - Social Distance - After Hours</p>","facility":"[\"56a8f82a7a8cee5e3a25ae7d\"]","activity_type":"[\"56a8ed1e85a63ab10cff5877\"]","age_high":99,"age_range":"Adult ages 18-99","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"63f7c1f5485370002a221b91","program_name":"Open Gym - Youth","program_description":"<p>Free play, walk-in gym use for basketball only. Youth not yet in high school must be accompanied by an adult.</p>","facility":"[\"56a8f85e7a8cee5e3a25b05d\"]","activity_type":"[\"56a8ecaa85a63ab10cff57f1\"]","age_high":17,"age_range":"Youth, Family ages 10-17","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"64ecde7d448bd40027ee0ecd","program_name":"Basketball Open Gym - Adult","program_description":"<p>Basketball open gym access for adults 18+ only. Walk-in free play - gym use for basketball only.</p>","facility":"[\"56a8f85e7a8cee5e3a25b05d\"]","activity_type":"[\"56a8ecaa85a63ab10cff57f1\"]","age_high":100,"age_range":"Adult ages 18-100","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"650a0f71e62fee00289fc596","program_name":"Cooking Class","program_description":"<p>Weekly Cooking class teaches basic cooking skills, food preparation, kitchen safety and nutrition.</p><p>This program has limited slots to ensure quality and safety for participants. First come first served.</p><p>A small fee is requeste","facility":"[\"56a8f83d7a8cee5e3a25af3b\"]","activity_type":"[\"56a8ecba85a63ab10cff5814\"]","age_high":16,"age_range":"Youth ages 8-16","fee":"10.00","fee_frequency":"[\"Per month\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":false},
  {"id":"650a3cf43e45bc0027b7458b","program_name":"Video Games & eSports Free Play","program_description":"<p>Join us for video game time. We love to play NBA2K, Madden, Fifa, Mario Kart, Smash Bros and Fortnite.</p><p>Our Video Game Lounge offers Xbox and switch console play time for our community. Come hang out and level up your game.</p><p>Sp","facility":"[\"56a8f83d7a8cee5e3a25af3b\"]","activity_type":"[\"56a8ed4485a63ab10cff58c2\"]","age_high":17,"age_range":"Youth ages 8-17","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"67d087462b128102c84100e4","program_name":"Open Court Basketball","program_description":"<p>Open Court Basketball.  Pickup games or Shoot-Around</p>","facility":"[\"56a8f8437a8cee5e3a25af73\"]","activity_type":"[\"56a8ecaa85a63ab10cff57f1\"]","age_high":19,"age_range":"Youth, Adult ages 14-19","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"6853155f1a3c7c02e0bcc2c3","program_name":"Pickup Volleyball Games","program_description":"<p>Co-Ed Ages 18 and Up </p><p>Intermediate Pick Up Volleyball Games</p><p>6 on 6  Games will be played to 21 points straight.</p><p><br /></p>","facility":"[\"56a8f8437a8cee5e3a25af73\"]","activity_type":"[\"56a8ed4485a63ab10cff58c4\"]","age_high":60,"age_range":"Adult ages 18-60","fee":"3.00","fee_frequency":"[\"Per week\"]","registration_status":"[\"Registering\"]","registration_form_link":"https://opensports.net/athletic-recreation-center","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"689b1163c3a8d3031abfe2c1","program_name":"Game Night ","program_description":"<p>We want to welcome the entire Simons Community to our Game Night, Connect four, Sorry, Uno, and many more games will be played during game night and our game night will be open to all ages. </p>","facility":"[\"56a8f8427a8cee5e3a25af61\"]","activity_type":"[\"56a8ecf685a63ab10cff584e\"]","age_high":90,"age_range":"Youth, Adult, Family ages 7-90","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"697d4d6e178250c8b8a05663","program_name":"Spoken Word Open Mic","program_description":"<p>Join us for an hour of community spoken word and healing! Third Wednesdays March-June. Free to attend. Children must be accompanied by an adult. Call 215-685-4193 to register to perform.</p>","facility":"[\"56a8f8567a8cee5e3a25b013\"]","activity_type":"[\"56a8ed2e85a63ab10cff5898\"]","age_high":100,"age_range":"Youth, Adult, Family ages 12-100","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"69a9cdefacad6b2cc3d1f87f","program_name":"Shredding","program_description":"<p>Community members can securely destroy sensitive documents, such as tax records and bank statements to prevent identity theft.</p>","facility":"[\"56a8f84e7a8cee5e3a25afdd\"]","activity_type":"[\"56a8ed2e85a63ab10cff5898\"]","age_high":100,"age_range":"Adult ages 18-100","fee":"20.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"69c57498f603ea173c09b307","program_name":"Silent Reading Club","program_description":"<p>Calling All Readers who need some space to reeeead!! Join us every 3rd Monday of the month from 6pm-8pm to read in peace! Bring your own book, snacks, pillow, whatever you need to be comfy and have a moment to ENJOY your latest pick! Chi","facility":"[\"56a8f8567a8cee5e3a25b013\"]","activity_type":"[\"56a8ed2485a63ab10cff5881\"]","age_high":100,"age_range":"Youth, Adult, Family ages 12-100","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"6a8f269ede02d80882d20ac3","program_name":"Walking Club Fall 2026","program_description":"<p>2 mile walking club open to all</p>","facility":"[\"56a8f8647a8cee5e3a25b08f\"]","activity_type":"[\"56a8ed2a85a63ab10cff588b\"]","age_high":99,"age_range":"Youth, Adult, Family ages 6-99","fee":"5.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"6a99dc1ffc4607f1d1ab8096","program_name":"stickball","program_description":"<p>Stickball drop in games weather permitting during After School time hours.</p>","facility":"[\"56a8f8207a8cee5e3a25ae1f\"]","activity_type":"[\"56a8ed2c85a63ab10cff588f\"]","age_high":12,"age_range":"Youth, Family ages 6-12","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"6747761d0b68d802ae3963b9","program_name":"Chess Club","program_description":"<p>All are welcome to learn and play chess at Wright</p>","facility":"[\"56a8f83d7a8cee5e3a25af3b\"]","activity_type":"[\"56a8ecb985a63ab10cff580b\"]","age_high":100,"age_range":"Youth, Adult, Family ages 1-100","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"6a553db853390233fcd3d315","program_name":"ROW Offsite ","program_description":"<p>ROW presenting offsite at library, rec centers, schools, commuity groups, etc </p>","facility":"[\"56a8f87c7a8cee5e3a25b155\"]","activity_type":"[\"56a8ece085a63ab10cff5838\"]","age_high":99,"age_range":"Youth, Adult, Family ages 1-99","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"6a553ee8d4b0710ca9423878","program_name":"ROW Tabeling ","program_description":"<p>ROW tabeling at public events, passive engagement. </p>","facility":"[\"56a8f87c7a8cee5e3a25b155\"]","activity_type":"[\"56a8ece085a63ab10cff5838\"]","age_high":99,"age_range":"Youth, Adult, Family ages 1-99","fee":"0.00","fee_frequency":"[\"Total/Season\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"584ef890e16412d54175d6a9","program_name":"Chess Club","program_description":"<p>Beginners Chess Club.</p>","facility":"[\"56a8f85d7a8cee5e3a25b04d\"]","activity_type":"[\"56a8ecb985a63ab10cff580b\"]","age_high":16,"age_range":"Youth ages 6-16","fee":"0.00","fee_frequency":"[\"Total\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"57d9c166c51a0070666c74cd","program_name":"After School Program","program_description":"<p>Homework time, arts and craft, sports and games. </p>","facility":"[\"56a8f83c7a8cee5e3a25af30\"]","activity_type":"[\"56a8eca285a63ab10cff57e4\"]","age_high":12,"age_range":"Youth ages 6-12","fee":"10.00","fee_frequency":"[\"Per week\"]","registration_status":"[\"Registering\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"57d2f53af927e8cd4a9bcfaa","program_name":"After school program","program_description":"<p>  Looking to give your child structured activity during non-school hours?</p><p style=\"background-color:rgb(255, 255, 255);color:rgb(0, 0, 0);\">Our after school programs allow youth to play, learn, and grow.</p><p>Parks & Rec's trained a","facility":"[\"56a8f8137a8cee5e3a25ad8e\"]","activity_type":"[\"56a8eca285a63ab10cff57e4\"]","age_high":12,"age_range":"Youth ages 6-12","fee":"120.00","fee_frequency":"[\"Per month\"]","registration_status":"[\"Closed: Check back again!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true},
  {"id":"57ed710245602c493d5bb175","program_name":"After School Program","program_description":"<p>After school provides, snack, and programmed activities</p>","facility":"[\"56a8f8637a8cee5e3a25b085\"]","activity_type":"[\"56a8eca285a63ab10cff57e4\"]","age_high":12,"age_range":"Youth ages 5-12","fee":"50.00","fee_frequency":"[\"Per week\"]","registration_status":"[\"Closed: Check back again!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":false},
  {"id":"57f2ea42887884d34abbf16c","program_name":"Tumbling For Beginners","program_description":null,"facility":"[\"56a8f82a7a8cee5e3a25ae7d\"]","activity_type":"[\"56a8ecfe85a63ab10cff5853\"]","age_high":12,"age_range":"Youth ages 7-12","fee":"25.00","fee_frequency":"[\"Per month\"]","registration_status":"[\"Planning: Seeking registrants!\"]","program_is_active":true,"program_is_public":true,"program_is_approved":true}
 ],
 "facilities": [
  {"id":"56a8f8437a8cee5e3a25af73","public_name":"Athletic Recreation Center","address":"{\"street\": \"1400 N. 26th St.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19121\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156852709","website_locator_points_link_id":"PK00139"},
  {"id":"56a8f8137a8cee5e3a25ad8e","public_name":"Columbus Square","address":"{\"street\": \"1200 Wharton St.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19147\", \"longitude\": -75.164776, \"latitude\": 39.933155}","facility_is_published":true,"contact_phone":"2156851890","website_locator_points_link_id":"PK00149"},
  {"id":"56a8f8567a8cee5e3a25b013","public_name":"Eastwick Regional Playground","address":"{\"street\": \"80th & Mars Place\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19153\", \"longitude\": -75.250453, \"latitude\": 39.90396}","facility_is_published":true,"contact_phone":"2156854193","website_locator_points_link_id":"PK00013"},
  {"id":"56a8f85e7a8cee5e3a25b05d","public_name":"Happy Hollow Playground","address":"{\"street\": \"4800 Wayne Ave.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19144\", \"longitude\": -75.16598, \"latitude\": 40.024245}","facility_is_published":true,"contact_phone":"2156852195","website_locator_points_link_id":"PK00140"},
  {"id":"56a8f86c7a8cee5e3a25b0c9","public_name":"Holmesburg Recreation Center","address":"{\"street\": \"4500 Rhawn St.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19136\", \"longitude\": -75.02664, \"latitude\": 40.036298}","facility_is_published":true,"contact_phone":"2156858714","website_locator_points_link_id":"PK00135"},
  {"id":"56a8f8637a8cee5e3a25b085","public_name":"Kendrick Recreation Center","address":"{\"street\": \"5822-24 Ridge Ave.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19128\", \"longitude\": -75.210868, \"latitude\": 40.029159}","facility_is_published":true,"contact_phone":"2156852584","website_locator_points_link_id":"PK00086"},
  {"id":"56a8f8647a8cee5e3a25b08f","public_name":"Lackman Memorial Playground","address":"{\"street\": \"1101 Bartlett St.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19115\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156850370","website_locator_points_link_id":"PK00065"},
  {"id":"56a8f82a7a8cee5e3a25ae7d","public_name":"Mitchell Playground","address":"{\"street\": \"3694 Chesterfield Rd.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19114\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156859394","website_locator_points_link_id":"PK00100"},
  {"id":"56a8f8207a8cee5e3a25ae1f","public_name":"Palmer Playground","address":"{\"street\": \"3035 Comly Rd.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19154\", \"longitude\": -74.988203, \"latitude\": 40.093251}","facility_is_published":true,"contact_phone":"2156850371","website_locator_points_link_id":"PK00044"},
  {"id":"56a8f84e7a8cee5e3a25afdd","public_name":"Pleasant Playground","address":"{\"street\": \"6757 Chew Ave.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19119\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156852230","website_locator_points_link_id":"PK00138"},
  {"id":"56a8f85d7a8cee5e3a25b04d","public_name":"Ramp Playground","address":"{\"street\": \"3300-40 Solly Ave.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19136\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156858746","website_locator_points_link_id":"PK00075"},
  {"id":"56a8f83c7a8cee5e3a25af30","public_name":"Rivera Recreation Center","address":"{\"street\": \"3201 N 5th St.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19140\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156859887","website_locator_points_link_id":"PK00118"},
  {"id":"56a8f8537a8cee5e3a25aff9","public_name":"Samuel Playground","address":"{\"street\": \"3539 Gaul St.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19134\", \"longitude\": -75.097079, \"latitude\": 39.991112}","facility_is_published":true,"contact_phone":"2156851246","website_locator_points_link_id":"PK00198"},
  {"id":"56a8f8427a8cee5e3a25af61","public_name":"Simons Recreation Center","address":"{\"street\": \"7200 Woolston Ave\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19138\", \"longitude\": null, \"latitude\": null}","facility_is_published":true,"contact_phone":"2156853551","website_locator_points_link_id":"IR00005"},
  {"id":"56a8f87c7a8cee5e3a25b155","public_name":"Wissahickon Environmental Center","address":"{\"street\": \"300 W Northwestern Ave.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19118\", \"longitude\": -75.232716, \"latitude\": 40.083615}","facility_is_published":true,"contact_phone":"2156859285","website_locator_points_link_id":"EE00003"},
  {"id":"56a8f83d7a8cee5e3a25af3b","public_name":"Wright Playground","address":"{\"street\": \"3320-50 Haverford Ave.\", \"street2\": null, \"city\": \"Philadelphia\", \"state\": \"PA\", \"zip\": \"19104\", \"longitude\": -75.191193, \"latitude\": 39.963908}","facility_is_published":true,"contact_phone":"2156857686","website_locator_points_link_id":"PK00274"}
 ],
 "locators": [
  {"linkid":"EE00003","lat":40.081297204953565,"lon":-75.2338696448079},
  {"linkid":"PK00013","lat":39.904429686918654,"lon":-75.2510992150989},
  {"linkid":"PK00044","lat":40.09569045899062,"lon":-74.98895995718665},
  {"linkid":"PK00065","lat":40.100259849338,"lon":-75.03319967558316},
  {"linkid":"PK00075","lat":40.04735415569246,"lon":-75.02487150426539},
  {"linkid":"PK00086","lat":40.028820266139306,"lon":-75.21135135095268},
  {"linkid":"PK00100","lat":40.06784341855195,"lon":-74.99123931957233},
  {"linkid":"PK00118","lat":40.00043945728681,"lon":-75.13761840753503},
  {"linkid":"PK00135","lat":40.03609316641634,"lon":-75.02713869125847},
  {"linkid":"PK00138","lat":40.05708400451745,"lon":-75.18239574767435},
  {"linkid":"PK00139","lat":39.97794328846211,"lon":-75.17866592174785},
  {"linkid":"PK00140","lat":40.02368511575571,"lon":-75.16627039221798},
  {"linkid":"PK00149","lat":39.933156311897456,"lon":-75.16478513887527},
  {"linkid":"PK00198","lat":39.99062912048393,"lon":-75.09715005494571},
  {"linkid":"PK00274","lat":39.96376023110734,"lon":-75.19086483935446},
  {"linkid":"IR00005","lat":40.06225051102383,"lon":-75.15763855547245}
 ],
 "types": [
  {"id":"56a8eca285a63ab10cff57e4","activity_type_name":"After School"},
  {"id":"56a8ecaa85a63ab10cff57f1","activity_type_name":"Basketball"},
  {"id":"56a8ecb985a63ab10cff580b","activity_type_name":"Chess"},
  {"id":"56a8ecba85a63ab10cff5814","activity_type_name":"Cooking"},
  {"id":"56a8ece085a63ab10cff5838","activity_type_name":"Environmental"},
  {"id":"56a8ecf685a63ab10cff584e","activity_type_name":"Games"},
  {"id":"56a8ecfe85a63ab10cff5853","activity_type_name":"Gymnastics / Tumbling"},
  {"id":"56a8ed1e85a63ab10cff5877","activity_type_name":"Meeting"},
  {"id":"56a8ed2485a63ab10cff5881","activity_type_name":"Monthly Programs"},
  {"id":"56a8ed2a85a63ab10cff588b","activity_type_name":"Other"},
  {"id":"56a8ed2c85a63ab10cff588f","activity_type_name":"Outdoor Recreation"},
  {"id":"56a8ed2e85a63ab10cff5898","activity_type_name":"Public / Open Programming"},
  {"id":"56a8ed4485a63ab10cff58c2","activity_type_name":"Video Games"},
  {"id":"56a8ed4485a63ab10cff58c4","activity_type_name":"Volleyball"}
 ]
}""")

TYPES = {r["id"]: r["activity_type_name"] for r in FIXTURE["types"]}
# Synthetic ids for live activity-type NAMES the fixture's programmes do not use.
for _i, _n in enumerate(["Day Camps", "Camp after care", "Martial Arts", "Fitness", "Environmental camp",
                         "Programs for People with Disabilities"]):
    TYPES[f"bbbbbbbbbbbbbbbbbbbbbbb{_i}"] = _n
TYPE_ID = {v: k for k, v in TYPES.items()}
RULES = T.Rules(CFG)
LOCATORS = {r["linkid"]: (r["lat"], r["lon"]) for r in FIXTURE["locators"]}
FACS = {f["id"]: f for f in FIXTURE["facilities"]}


def progs(rows=None):
    return {p["id"]: p for p in (rows if rows is not None else FIXTURE["programs"])}


def build(schedules=None, programs=None, facilities=None, locators=None, skip=None, cfg=None, stats=None):
    st = {} if stats is None else stats
    return T.build_events(FIXTURE["schedules"] if schedules is None else schedules,
                          progs() if programs is None else programs,
                          FACS if facilities is None else facilities,
                          LOCATORS if locators is None else locators,
                          TYPES, SRC, cfg or CFG, TZ, TODAY, SKIP if skip is None else skip, st)


def by_name(evs, name):
    return [e for e in evs if e.name == name]


def day_of(ev):
    return date.fromisoformat(ev.start_local[:10])


def synth(pid, name, desc, fee="0.00", status="Registering", fac="56a8f85e7a8cee5e3a25b05d",
          age_high=100, types=("56a8ecaa85a63ab10cff57f1",)):
    """A synthetic programme at Happy Hollow (Basketball) unless told otherwise."""
    return {"id": pid, "program_name": name, "program_description": desc,
            "facility": json.dumps([fac]), "activity_type": json.dumps(list(types)),
            "age_high": age_high, "age_range": "Adult ages 18-100", "fee": fee,
            "fee_frequency": '["Per Day"]', "registration_status": json.dumps([status]),
            "program_is_active": True, "program_is_public": True, "program_is_approved": True}


def sched(pid, d0, d1, days, t0, t1):
    ids = {v: k for k, v in CFG["weekday_ids"].items()}
    return {"programs": json.dumps([pid]), "date_from": f"{d0}T00:00:00Z", "date_to": f"{d1}T00:00:00Z",
            "days": json.dumps([ids[d] for d in days]), "time_from": f"2012-01-01T{t0}:00Z",
            "time_to": f"2012-01-01T{t1}:00Z"}


STATS = {}
EVS = build(stats=STATS)
KEPT = {e.name for e in EVS}

# ------------------------------------------------------------- 1. who is in
print("who is in")
check("the six programmes whose OWN words say anyone can turn up, with no sign-up link, are kept, and nothing else",
      KEPT == {"Open Gym - Youth", "Basketball Open Gym - Adult", "Video Games & eSports Free Play",
               "Chess Club", "Open Court Basketball", "Stickball"},
      sorted(KEPT))
status = {(p["program_name"].strip(), json.loads(p["registration_status"])[0]) for p in FIXTURE["programs"]}
check("REGISTRATION_STATUS CANNOT BE THE GATE: the walk-in open gym and the after-school club both say 'Registering'",
      {("Basketball Open Gym - Adult", "Registering"), ("After School Program", "Registering")} <= status
      and "Basketball Open Gym - Adult" in KEPT and "After School Program" not in KEPT, sorted(status))
no_words = dict(CFG, dropin_words=[], open_words=[])
check("the words are the gate: with no drop-in words configured, nothing is kept",
      build(cfg=no_words) == [], len(build(cfg=no_words)))
check("a sign-up course (no drop-in words) is refused and counted, in programmes and occurrences, by its status",
      sum(v for k, v in STATS.items() if k.startswith("programmes refused: sign-up programme:")) == 6
      and sum(v for k, v in STATS.items() if k.startswith("occurrences refused: sign-up programme:")) == 30
      and STATS.get("programmes refused: sign-up programme: status 'Registering', no drop-in words") == 1, STATS)
check("a programme the Finder does not show (unapproved Cooking Class) is refused",
      "Cooking Class" not in KEPT and STATS.get("programmes refused: not on the Finder (inactive, private or unapproved)") == 2,
      STATS)
check("'Closed: Check back again!' is out", STATS.get("programmes refused: status 'Closed: Check back again!'") == 1, STATS)
closed = synth("aaaaaaaaaaaaaaaaaaaaaaa1", "Open Gym", "Walk-in open gym.", status="Closed: Check back again!")
check("a Closed programme is out even when it says walk-in",
      not build([sched(closed["id"], "2026-10-01", "2026-12-01", ["Monday"], "10:00", "11:00")], {closed["id"]: closed}))
check("RECOVERY FELLOWSHIPS ARE NEVER PINNED: AA and NA are refused before their wording is read",
      STATS.get("programmes refused: recovery fellowship (anonymous; never pinned)") == 2
      and not [e for e in EVS if re.search(r"anonymous|\bAA\b", e.name, re.I)], STATS)
na = synth("aaaaaaaaaaaaaaaaaaaaaaa2", "Narcotics Anonymous : Infinity Home Group", "All are welcome to attend. Drop-in.")
check("... even one that says 'All are welcome' and 'Drop-in' (synthetic, from the live NA group's text)",
      not build([sched(na["id"], "2026-10-01", "2026-12-01", ["Monday"], "10:00", "11:00")], {na["id"]: na}))
check("a civic association meeting and a shredding day are refused",
      "Holmesburg Civic association" not in KEPT and "Shredding" not in KEPT
      and STATS.get("programmes refused: governance meeting") == 1
      and STATS.get("programmes refused: a service day, not a programme") == 1, STATS)
check("'open to all' with a $5.00 season fee is a sign-up, not a drop-in (Walking Club Fall 2026)",
      "Walking Club Fall 2026" not in KEPT
      and STATS.get("programmes refused: 'open to all' but charges a fee: a sign-up") == 1, STATS)
check("'All are welcome' with no fee is kept (Chess Club at Wright)", "Chess Club" in KEPT)
check("THE FINDER'S SIGN-UP LINK IS READ: Pickup Volleyball Games ('Pickup' in its name, $3.00 per week, "
      "'To sign up visit: opensports.net/...') is refused and counted",
      "Pickup Volleyball Games" not in KEPT
      and STATS.get("programmes refused: signs up through registration_form_link (a web or e-mail address)") == 1, STATS)
check("'open to all AGES' is who may sign up, not who may turn up: Game Night (status 'Seeking registrants') is refused",
      "Game Night" not in KEPT)

# Live catalogue text (2026-10-05, trimmed), each on a synthetic programme id
# at Happy Hollow; where a case isolates one rule (a camp NAME on a basketball
# type, a live sign-up host on a pickup game) the other fields are synthetic: (name, description, fee, sign-up link,
# activity type, the refusal it must get - None = kept).
LIVE_CASES = [
    ("Maple Sugaring Open House", "All are welcome to stop by the center throughout the day to learn more about maple "
     "sugaring. Registration required by emailing PEC@phila.gov for a time slot.", "0.00", None, "Environmental",
     "registration is required"),
    (" Wildlife Walks", "Adults can join us for a morning walk in search of birds and other wilife. Free. registration "
     "required by emailing pec@phila.gov", "0.00", None, "Environmental", "registration is required"),
    ("adult volleyball", "Adult volleyball is for men and women who want to exercise, have fun and meet new friends. All "
     "are welcome! Please complete a one time 2-page registration form.", "0.00", None, "Volleyball",
     "registration is required"),
    ("In House Basketball", "Open to All Children Ages 8-16 League Play begins 10/15/2025 Games Wednesdays and Thursdays "
     "Must be registered by 9/26/2025", "0.00", None, "Basketball", "registration is required"),
    ("Goals Martial Arts - Karate ", "$5.00 drop in class. 45 minute classes. Registration form is needed prior to start "
     "of class.", "5.00", None, "Martial Arts", "registration is required"),
    ("Andorra Natural Area Walks", "Join Wissahickon Environmental Center staff for a guided nature walk in the historic "
     "Andorra Natural Area.", "0.00", "https://www.eventbrite.com/o/wissahickon-environmental-center-54209991113",
     "Environmental", "registration_form_link"),
    ("Sensory Friendly Swim", "This is an open swim time with a quiet, less-crowded environment.", "0.00",
     "mitchellplayground@phila.gov", "Programs for People with Disabilities", "registration_form_link"),
    ("Pick up games", "Pick up games, come one come all!", "0.00", "treehousewec.eventbrite.com", "Basketball",
     "registration_form_link"),
    ("Summer Camp", "Columbus Square Summer Camp offers swimming, sports, visual arts, drama, dance, computers, free "
     "play, trips, group games, snacks.", "500.00", None, "Day Camps", "camp"),
    ("Summer camp - after care", "After Camp Program 3pm - 6pm. Walk-ins welcome. Free play.", "5.00", None, "Basketball",
     "camp"),
    ("Waterview Chess Club", "The chess club is open to all skill levels.", "0.00", None, "Chess", "no drop-in words"),
    ("Walking Program", "A 30 minute walk in the basketball gym.", "0.00", None, "Fitness", "no drop-in words"),
    ("Adult boot camp fitness", "Adult Boot Camp. Exercise and Fitness program for adults of all ages.", "0.00", None,
     "Fitness", "no drop-in words"),
    ("N.A. Meeting", "This is a support group for individuals in recovery. All are welcome.", "0.00", None, "Meeting",
     "recovery fellowship"),
    ("Gamblers' Anonymous/Accountability", "Accountability Program for men/women struggling with gambling. Drop-in.",
     "0.00", None, "Meeting", "recovery fellowship"),
    ("Learning To Recover", "A narcotics addiction recovery group meeting. All are welcome.", "0.00", None, "Other",
     "recovery fellowship"),
    ("Craft Drop In Session", "Join us for a seasonal craft. No Registration Required.", "0.00", None, "Environmental", None),
    ("Just Dance! On Nintendo switch", "Dance to music and test your skills.", "0.00", "Drop in", "Games", None),
    ("Open Gym", "Open gym. No need to register, just show up.", "0.00", None, "Basketball", None),
]


def live_prog(i, name, desc, fee, link, typ):
    p = synth(f"cccccccccccccccccccccc{i:02d}", name, desc, fee=fee, types=(TYPE_ID[typ],))
    p["registration_form_link"] = link
    return p


for i, (name, desc, fee, link, typ, want) in enumerate(LIVE_CASES):
    why = T.decide(live_prog(i, name, desc, fee, link, typ), RULES, TYPES)[0]
    check(f"live text: {name.strip()!r} -> {want or 'kept'}",
          (why is None) if want is None else (why is not None and want in why), why)
lc_st = {}
lc_evs = build([sched(f"cccccccccccccccccccccc{i:02d}", "2026-10-05", "2026-10-05", ["Monday"], "10:00", "11:00")
                for i in range(len(LIVE_CASES))],
               {f"cccccccccccccccccccccc{i:02d}": live_prog(i, *c[:5]) for i, c in enumerate(LIVE_CASES)}, stats=lc_st)
check("... and through build_events: only the three kept ones write a row, every refusal counted",
      sorted(e.name for e in lc_evs) == ["Craft Drop In Session", "Just Dance! On Nintendo switch", "Open Gym"]
      and sum(v for k, v in lc_st.items() if k.startswith("programmes refused:")) == len(LIVE_CASES) - 3, lc_st)
check("'No registration required' and 'Drop in' are never read as 'registration required' (kept as drop-in)",
      all("Free drop-in" in e.description for e in lc_evs), [e.description[-160:] for e in lc_evs])
bird = synth("aaaaaaaaaaaaaaaaaaaaaab2", "\u200bIntroduction to Birding", "Drop in.")
check("a name's leading zero-width space (15 live names) is gone from the title",
      T.title_of("\u200bCoffee With The Birds") == "Coffee With The Birds"
      and [e.name for e in build([sched(bird["id"], "2026-10-05", "2026-10-05", ["Monday"], "10:00", "11:00")],
                                 {bird["id"]: bird})] == ["Introduction to Birding"])

# ------------------------------------------------------------- 2. dates
print()
print("dates")
check("NOT WEEKLY BY ITS OWN WORDS: 'Third Wednesdays' on a weekly Wednesday schedule is refused, not expanded",
      "Spoken Word Open Mic" not in KEPT and STATS.get("programmes refused: not weekly by its own words") == 1, STATS)
eo = synth("aaaaaaaaaaaaaaaaaaaaaaa3", "Open Gym", "Open gym every other Friday night!")
check("... and 'every other Friday' (synthetic, the live Movie night's words)",
      not build([sched(eo["id"], "2026-10-01", "2026-12-01", ["Friday"], "18:00", "20:00")], {eo["id"]: eo}))
fx_sched = {}
for s in FIXTURE["schedules"]:
    for pid in json.loads(s["programs"]):
        fx_sched.setdefault(pid, []).append(s)


def inside_a_season(ev):
    pid, d = ev.source_id.split("|")[0], day_of(ev)
    return any(date.fromisoformat(s["date_from"][:10]) <= d <= date.fromisoformat(s["date_to"][:10])
               for s in fx_sched[pid])


check("NEVER A DATE OUTSIDE ITS SEASON: every row sits inside its own schedule's date_from..date_to",
      all(inside_a_season(e) for e in EVS), [e.source_id for e in EVS if not inside_a_season(e)][:3])
check("... and inside today..horizon", all(TODAY <= day_of(e) <= HORIZON for e in EVS))
og = sorted(day_of(e) for e in by_name(EVS, "Open Gym - Youth"))
check("Open Gym - Youth: every Tuesday from 2026-10-06 to its season's last day 2026-12-08, both ends in (10 rows)",
      og == [date(2026, 10, 6) + timedelta(weeks=k) for k in range(10)], og)
late = synth("aaaaaaaaaaaaaaaaaaaaaab3", "Open Gym", "Walk-in open gym.")
late_evs = build([sched(late["id"], "2026-10-26", "2026-12-14", ["Monday"], "18:00", "20:00")], {late["id"]: late})
late_days = sorted(day_of(e) for e in late_evs)
check("A SEASON THAT STARTS LATER STARTS LATER: Mondays from 2026-10-26 (synthetic) write nothing on the three "
      "Mondays before it, and the first row is 2026-10-26",
      late_days and late_days[0] == date(2026, 10, 26) and not [d for d in late_days if d < date(2026, 10, 26)]
      and late_days == [date(2026, 10, 26) + timedelta(weeks=k) for k in range(8)], late_days)
check("... and a season's last day is written (Mondays to 2026-12-14 end ON 2026-12-14)",
      late_days and late_days[-1] == date(2026, 12, 14))
bb = {day_of(e) for e in by_name(EVS, "Basketball Open Gym - Adult")}
check("CITY HOLIDAYS ARE CLOSED DAYS: the Mon-Fri open gym skips Indigenous Peoples' Day, Veterans Day, "
      "Thanksgiving and its Friday (46 of 50 weekdays)",
      len(bb) == 46 and not bb & {date(2026, 10, 12), date(2026, 11, 11), date(2026, 11, 26), date(2026, 11, 27)},
      len(bb))
check("Christmas and New Year's Day are skipped on the open court too",
      not {day_of(e) for e in by_name(EVS, "Open Court Basketball")} & {date(2026, 12, 25), date(2027, 1, 1)})
dec = T.build_events(FIXTURE["schedules"], progs(), FACS, LOCATORS, TYPES, SRC, CFG, TZ, date(2026, 12, 1), SKIP, {})
oc = {day_of(e) for e in by_name(dec, "Open Court Basketball")}
check("2027 IS COVERED BEFORE THE CITY PUBLISHES IT (its API answers the current year only): run on 2026-12-01, the "
      "Mon-Fri open court skips MLK Day 2027-01-18 and Presidents' Day 2027-02-15, and no year is thin",
      oc and not oc & {date(2027, 1, 18), date(2027, 2, 15)} and date(2027, 1, 19) in oc
      and T.holiday_gap(SKIP, date(2026, 12, 1), date(2027, 3, 1)) is None, sorted(oc)[-5:])
check("without the holiday list the same gym would have written 50 (so the skip is what removed them)",
      len(by_name(build(skip=set()), "Basketball Open Gym - Adult")) == 50)
ty = {e.start_local[:10]: e for e in by_name(EVS, "Open Gym - Youth")}
check("each row says its days and its season's end: 'Monday to Friday until December 11, 2026', 'Tuesdays until ...'",
      all("Monday to Friday until December 11, 2026." in e.description for e in by_name(EVS, "Basketball Open Gym - Adult"))
      and all("Tuesdays until December 8, 2026." in e.description for e in by_name(EVS, "Open Gym - Youth")))
check("WALL CLOCK, DST-CORRECT: Tuesday 15:00 is 19:00Z on 2026-10-27 (EDT) and 20:00Z on 2026-11-03 (EST)",
      ty["2026-10-27"].start_utc == "2026-10-27T19:00:00Z" and ty["2026-11-03"].start_utc == "2026-11-03T20:00:00Z"
      and ty["2026-11-03"].start_local == "2026-11-03T15:00:00", (ty["2026-10-27"].start_utc, ty["2026-11-03"].start_utc))
check("the 'Z' on 2012-01-01T15:00:00Z is not UTC: the local clock reads 15:00, not 11:00",
      all(e.start_local.endswith("T15:00:00") for e in ty.values()))
bad_st = {}
bad = synth("aaaaaaaaaaaaaaaaaaaaaaa4", "Open Gym", "Walk-in.")
got = build([sched(bad["id"], "2026-10-01", "2026-12-01", ["Monday"], "18:30", "07:30"),
             sched(bad["id"], "2026-10-01", "2026-12-01", ["Tuesday"], "00:00", "00:00")], {bad["id"]: bad}, stats=bad_st)
check("a schedule whose end is not after its start (the live 18:30-07:30, 00:00-00:00) is refused, never repaired",
      got == [] and bad_st.get("schedules with no usable clock (end not after start)") == 2, bad_st)
unk = synth("aaaaaaaaaaaaaaaaaaaaaaa5", "Open Gym", "Walk-in.")
s_unk = sched(unk["id"], "2026-10-01", "2026-12-01", ["Monday"], "10:00", "11:00")
s_unk["days"] = json.dumps(["ffffffffffffffffffffffff"])
unk_st = {}
check("an unknown weekday id writes nothing and is counted (a day is never guessed)",
      build([s_unk], {unk["id"]: unk}, stats=unk_st) == [] and unk_st.get("unknown weekday id (day never guessed)") == 1)

# ------------------------------------------------------------- 3. one row per stretch
print()
print("one row per stretch")
two = synth("aaaaaaaaaaaaaaaaaaaaaaa6", "Open Gym", "Walk-in open gym.")
joined = build([sched(two["id"], "2026-10-05", "2026-10-05", ["Monday"], "10:00", "11:00"),
                sched(two["id"], "2026-10-05", "2026-10-05", ["Monday"], "11:00", "12:00")], {two["id"]: two})
check("back-to-back schedules of one programme on one day are ONE row, 10:00-12:00",
      len(joined) == 1 and joined[0].start_local.endswith("T10:00:00") and joined[0].end_local.endswith("T12:00:00"),
      [(e.start_local, e.end_local) for e in joined])
gap = build([sched(two["id"], "2026-10-05", "2026-10-05", ["Monday"], "10:00", "11:00"),
             sched(two["id"], "2026-10-05", "2026-10-05", ["Monday"], "18:00", "19:00")], {two["id"]: two})
check("A GAP ENDS THE ROW: 10-11 and 18-19 are two rows, each on its own clock, none spanning 11-18",
      sorted((e.start_local[11:16], e.end_local[11:16]) for e in gap) == [("10:00", "11:00"), ("18:00", "19:00")]
      and len({e.fingerprint for e in gap}) == 2 and len({e.source_id for e in gap}) == 2,
      [(e.start_local, e.end_local) for e in gap])
check("... and each names the other ('Also on this day')",
      all("Also on this day:" in e.description for e in gap))

# ------------------------------------------------------------- 4. identity
print()
print("identity")
check("source_id is programme id | date | first clock", all(re.match(r"^[0-9a-f]{24}\|\d{4}-\d{2}-\d{2}\|\d{2}:\d{2}$", e.source_id) for e in EVS))
check("no two rows share a fingerprint", len({e.fingerprint for e in EVS}) == len(EVS))
again = build(schedules=list(reversed(FIXTURE["schedules"])), programs=progs(list(reversed(FIXTURE["programs"]))))
check("a shuffled re-read writes identical rows (keys and fingerprints)",
      sorted((e.source_id, e.fingerprint, e.description) for e in again) == sorted((e.source_id, e.fingerprint, e.description) for e in EVS))
twin = dict(two, id="aaaaaaaaaaaaaaaaaaaaaaa7")
pair = build([sched(two["id"], "2026-10-05", "2026-10-05", ["Monday"], "10:00", "11:00"),
              sched(twin["id"], "2026-10-05", "2026-10-05", ["Monday"], "10:00", "11:00")], {two["id"]: two, twin["id"]: twin})
check("two programme ids of one name, centre and clock share a fingerprint (the store merges them)",
      len(pair) == 2 and pair[0].fingerprint == pair[1].fingerprint)
with tempfile.TemporaryDirectory() as tmp:
    st = EventStore(os.path.join(tmp, "s.json"))
    for e in EVS + build():
        st.upsert(e)
    check("upserting the same read twice adds each row once", len(st.records) == len(EVS), len(st.records))

# ------------------------------------------------------------- 5. price
print()
print("price")
free = [e for e in EVS if "Free drop-in: no fee." in e.description]
paid = [e for e in EVS if "(not free)" in e.description]
check("a 0.00 fee says 'Free drop-in: no fee.' (the Finder prints 'Free' for 0.00) and 0227 reads it free",
      free and all(tagged_free(e) for e in free), len(free))
check("no paid row in the fixture's run reads free", not any(tagged_free(e) for e in paid))
rw = synth("aaaaaaaaaaaaaaaaaaaaaab4", "Ricky wright - open gym", "Basketball Games", fee="5.00")
rwe = build([sched(rw["id"], "2026-10-05", "2026-10-05", ["Monday"], "18:00", "20:00")], {rw["id"]: rw})
check("A PAID DROP-IN SAYS ITS FEE AND 'not free' (the live $5.00 open gym, synthetic schedule)",
      len(rwe) == 1 and "Fee: $5.00 per day (not free)." in rwe[0].description and not tagged_free(rwe[0]),
      rwe[0].description if rwe else None)
chess = by_name(EVS, "Chess Club")
check("LET IN ONLY ON 'All are welcome': Chess Club says 'Price: free (no fee).', never 'drop-in', and 0227 reads it free",
      chess and all("Price: free (no fee)." in e.description and "drop-in" not in e.description.lower()
                    and tagged_free(e) for e in chess), chess[0].description if chess else None)
fp = synth("aaaaaaaaaaaaaaaaaaaaaaa8", "Video Games Free Play", "Free play on the consoles.", fee="2.00")
fpe = build([sched(fp["id"], "2026-10-05", "2026-10-05", ["Monday"], "18:00", "20:00")], {fp["id"]: fp})
check("'Free Play' in a PAID title is vetoed by '(not free)' (synthetic)",
      len(fpe) == 1 and not tagged_free(fpe[0]), fpe[0].description if fpe else None)
nf = synth("aaaaaaaaaaaaaaaaaaaaaaa9", "Open Gym", "Walk-in open gym.", fee=None)
nfe = build([sched(nf["id"], "2026-10-05", "2026-10-05", ["Monday"], "18:00", "20:00")], {nf["id"]: nf})
check("a null fee says nothing about price, and nothing reads free",
      len(nfe) == 1 and "🎟" not in nfe[0].description and not tagged_free(nfe[0]))
check("'open to all' with fee null is kept, and says no price (synthetic)",
      len(build([sched("aaaaaaaaaaaaaaaaaaaaaab1", "2026-10-05", "2026-10-05", ["Monday"], "18:00", "20:00")],
                {"aaaaaaaaaaaaaaaaaaaaaab1": synth("aaaaaaaaaaaaaaaaaaaaaab1", "Walking Club", "Open to all.", fee=None)})) == 1)

# ------------------------------------------------------------- 6. place and category
print()
print("place and category")
check("every row is placed on the Finder's own locator point, coords_exact",
      all(e.latitude is not None and e.coords_exact for e in EVS))
hh = FACS["56a8f85e7a8cee5e3a25b05d"]
e0 = by_name(EVS, "Open Gym - Youth")[0]
check("... which is the ppr_website_locatorpoints point, not the address JSON's",
      (e0.latitude, e0.longitude) == LOCATORS[hh["website_locator_points_link_id"]])
no_loc = build(locators={})
ad = json.loads(hh["address"])
check("with no locator point the address record's point is used, NOT exact (the sync may refine it)",
      all((e.latitude, e.longitude, e.coords_exact) == (ad["latitude"], ad["longitude"], False)
          for e in by_name(no_loc, "Open Gym - Youth")))
bare = {k: dict(v, address=json.dumps({"street": "1 Main St.", "city": "Philadelphia", "state": "PA", "zip": "19100"}))
        for k, v in FACS.items()}
nb = build(facilities=bare, locators={})
check("with neither, the row keeps its street for the sync to geocode and claims no point",
      nb and all(e.latitude is None and not e.coords_exact and e.address == "1 Main St." for e in nb))
check("every category is a lens key", all(e.category in VALID_CATEGORIES and set(e.categories) <= VALID_CATEGORIES for e in EVS))
check("config: every category_by_activity_type value is a lens key",
      set(CFG["category_by_activity_type"].values()) <= VALID_CATEGORIES)
check("basketball is sports, chess is learning; stickball (ages 6-12) adds kids",
      by_name(EVS, "Open Gym - Youth")[0].category == "sports" and by_name(EVS, "Chess Club")[0].category == "learning"
      and "kids" in by_name(EVS, "Stickball")[0].categories)
check("an ADULT programme (18-100) and a teen one (14-19) do not add kids",
      not any("kids" in e.categories for e in by_name(EVS, "Basketball Open Gym - Adult") + by_name(EVS, "Open Court Basketball")))
k13 = synth("aaaaaaaaaaaaaaaaaaaaaab5", "Open Gym", "Walk-in open gym.", age_high=13)
check("the kids cut-off is age_high <= 12: 13 does not add kids (synthetic)",
      "kids" not in build([sched(k13["id"], "2026-10-05", "2026-10-05", ["Monday"], "18:00", "20:00")],
                          {k13["id"]: k13})[0].categories)
unpub_st = {}
unpub = dict(FACS, **{"56a8f85e7a8cee5e3a25b05d": dict(FACS["56a8f85e7a8cee5e3a25b05d"], facility_is_published=False)})
un = build(facilities=unpub, stats=unpub_st)
check("a facility the Finder does not publish writes nothing (Happy Hollow unpublished, synthetic), and is counted",
      not [e for e in un if e.venue_name == "Happy Hollow Playground"] and len(un) > 0
      and unpub_st.get("programmes refused: facility not published by the Finder") == 2, unpub_st)
check("the title is the source's, tidied: 'Game Night ' loses its space, 'stickball' gets a capital",
      T.title_of("Game Night ") == "Game Night" and "Stickball" in KEPT and "stickball" not in KEPT)
check("the Finder link is the programme's own hash route",
      e0.ticket_url == "https://www.phila.gov/parks-rec-finder/#/program/63f7c1f5485370002a221b91")
check("the licence line is the last paragraph and under 200 characters",
      all(e.description.endswith(CFG["attribution"]) for e in EVS) and len(CFG["attribution"]) < 200)

# ------------------------------------------------------------- 7. the run
print()
print("the run")


class Resp:
    def __init__(self, status, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode("utf-8")

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


HOLIDAYS = {"holidays": [{"holiday_label": "Thanksgiving", "start_date": "2026-11-26"},
                         {"holiday_label": "Test day", "start_date": "2026-10-06"}]}


class FakeCarto:
    """phl.carto.com and api.phila.gov, served from FIXTURE. `answer(host, q)`
    may return a status code or raise, to play a refusal or a cancelled step;
    `robots` maps a host to its robots.txt text (default: 404, allow-all, as
    both answered on 2026-10-05)."""

    def __init__(self, answer=None, robots=None):
        self.headers, self.calls, self.answer = {}, [], answer or (lambda host, q: None)
        self.robots = robots or {}

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        host, q = urlsplit(url).netloc, (params or {}).get("q", "")
        if urlsplit(url).path == "/robots.txt":
            self.calls.append((host, "robots.txt"))
            return Resp(200, text=self.robots[host]) if host in self.robots else Resp(404, text="")
        self.calls.append((host, q))
        status = self.answer(host, q)
        if status:
            return Resp(status, {"error": ["no"]})
        if host == "api.phila.gov":
            return Resp(200, HOLIDAYS)
        ids = re.findall(r"'([^']+)'", q.split(" IN ", 1)[1]) if " IN " in q else []
        if "FROM ppr_program_schedules" in q:
            rows = FIXTURE["schedules"]
        elif "FROM ppr_programs " in q:
            rows = [p for p in FIXTURE["programs"] if p["id"] in ids]
        elif "FROM ppr_facilities" in q:
            rows = [f for f in FIXTURE["facilities"] if f["id"] in ids]
        elif "FROM ppr_website_locatorpoints" in q:
            rows = [r for r in FIXTURE["locators"] if r["linkid"] in ids]
        elif "FROM ppr_activity_types" in q:
            rows = FIXTURE["types"]
        else:
            return Resp(400, {"error": ["unknown table"]})
        return Resp(200, {"rows": rows, "total_rows": len(rows)})


def run(fake, *extra):
    real_session, real_reader = T.requests.Session, T.Reader
    T.requests.Session = lambda: fake
    T.Reader = lambda *a, **k: real_reader(*a, sleep=lambda s: None, **k)
    out = io.StringIO()
    path = os.path.join(tmpdir, f"store{len(os.listdir(tmpdir))}.json")
    try:
        with contextlib.redirect_stdout(out):
            got = T.main(["--config", CFG_PATH, "--store", path, *extra])
    except BaseException as exc:                                  # noqa: BLE001
        got = exc
    finally:
        T.requests.Session, T.Reader = real_session, real_reader
    return got, out.getvalue(), path


def stored(path):
    return EventStore(path).records if os.path.exists(path) else {}


with tempfile.TemporaryDirectory() as tmpdir:
    fake = FakeCarto()
    code, out, path = run(fake)
    recs = stored(path)
    tue = [r for r in recs.values() if r["start_local"].startswith("2026-10-06")]
    check("a whole run exits 0 and writes the build's rows less the City's extra holiday",
          code == 0 and len(recs) == len(EVS) - len([e for e in EVS if e.start_local.startswith("2026-10-06")])
          and not tue, (code, len(recs), out[-400:]))
    check("THE CITY'S HOLIDAY LIST IS READ AND JOINED: a date only the API names (synthetic 2026-10-06) is skipped",
          any(h == "api.phila.gov" for h, _ in fake.calls) and not tue)
    check("the run is 2 robots.txt + 1 holiday read + 1 schedules + 1 programmes + 1 types + 1 facilities + 1 locators",
          len(fake.calls) == 8 and [h for h, q in fake.calls if q == "robots.txt"] == ["api.phila.gov", "phl.carto.com"],
          [h + " " + q[:40] for h, q in fake.calls])
    check("ROBOTS.TXT IS READ BEFORE EITHER HOST IS ASKED ANYTHING",
          fake.calls[0] == ("api.phila.gov", "robots.txt")
          and [c for c in fake.calls if c[0] == "phl.carto.com"][0] == ("phl.carto.com", "robots.txt"))
    check("the run's summary counts the robots.txt reads", "6 requests + 2 robots.txt" in out, out[-300:])
    sql = " ".join(q for _, q in fake.calls)
    check("the season filter reaches SQL: date_to >= today AND date_from <= horizon",
          f"date_to >= '{TODAY.isoformat()}'" in sql and f"date_from <= '{HORIZON.isoformat()}'" in sql)

    fake = FakeCarto(lambda host, q: 500 if host == "api.phila.gov" else None)
    code, out, path = run(fake)
    check("a failed holiday read falls back to the config's list, says so, and still writes every row",
          code == 0 and len(stored(path)) == len(EVS) and "config skip_dates only" in out, out[-300:])

    fake = FakeCarto(robots={"phl.carto.com": "User-agent: *\nDisallow: /api/\n"})
    code, out, path = run(fake)
    check("a robots.txt that refuses /api/ to us: no SQL is sent, the source is REFUSED, exit 0, store saved",
          code == 0 and not [q for h, q in fake.calls if h == "phl.carto.com" and q != "robots.txt"]
          and "REFUSED: robots.txt" in out and os.path.exists(path), out[-300:])
    fake = FakeCarto(robots={"phl.carto.com": "User-agent: Googlebot\nDisallow: /api/\n"})
    code, out, path = run(fake)
    check("... while the live file (Disallow: /api/ for Googlebot and four other crawlers only) lets us read",
          code == 0 and len(stored(path)) > 0, out[-300:])
    fake = FakeCarto(robots={"api.phila.gov": "User-agent: *\nDisallow: /\n"})
    code, out, path = run(fake)
    check("a robots.txt that refuses the holiday API: it is not asked, the config list is used, the rows are written",
          code == 0 and ("api.phila.gov", "") not in fake.calls and "config skip_dates only" in out
          and len(stored(path)) == len(EVS), out[-300:])
    fake = FakeCarto(robots={"phl.carto.com": "User-agent: *\nCrawl-delay: 4\n"})
    slept = []
    real_reader = T.Reader
    T.Reader = lambda *a, **k: real_reader(*a, sleep=slept.append, **{x: y for x, y in k.items() if x != "sleep"})
    try:
        code, out, path = run(fake)
    finally:
        T.Reader = real_reader
    check("a Crawl-delay longer than ours is honoured between CARTO requests", slept and max(slept) > 3.5, slept)

    fake = FakeCarto(lambda host, q: 403 if "ppr_programs " in q else None)
    code, out, path = run(fake)
    progs_asked = [q for _, q in fake.calls if "ppr_programs " in q]
    check("a 403 is a refusal: not retried, nothing more asked of that host, exit 0, store saved",
          code == 0 and len(progs_asked) == 1 and "REFUSED" in out and os.path.exists(path)
          and not [q for _, q in fake.calls if "ppr_facilities" in q], (len(progs_asked), out[-300:]))

    fake = FakeCarto()
    code, out, path = run(fake, "--max-minutes", "0.0000001")
    check("--max-minutes: past the deadline no request starts, the run exits 0 and still saves",
          code == 0 and not fake.calls and os.path.exists(path), (fake.calls, out))

    def cancel(host, q):
        if "ppr_activity_types" in q:
            raise KeyboardInterrupt("timeout-minutes cancelled the step")
    code, out, path = run(FakeCarto(cancel))
    check("a step cancelled mid-read still leaves a store file for the sync",
          isinstance(code, KeyboardInterrupt) and os.path.exists(path), repr(code))

    calls = []

    class Rec:
        headers = {}

        def get(self, url, params=None, timeout=None):
            calls.append(params["q"])
            return Resp(200, {"rows": []})
    r = T.Reader(Rec(), sleep=lambda s: None)
    st = {}
    ids = [f"{i:024x}" for i in range(61)] + ["x' OR '1'='1", "57d9c166c51a0070666c74cd"]
    r.sql_in(CFG["api"], "SELECT id FROM ppr_programs WHERE id IN (%s)", ids, st)
    check("IN lists are batched at 60 ids (62 good ids are 2 requests)", len(calls) == 2
          and all(len(re.findall(r"'[0-9a-f]{24}'", q)) <= 60 for q in calls), [len(q) for q in calls])
    check("an id that is not the source's 24-hex shape never reaches SQL, and is counted",
          not any("OR" in q for q in calls) and st.get("id not 24-hex (never put in SQL)") == 1, st)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
