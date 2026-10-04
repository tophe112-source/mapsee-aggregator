#!/usr/bin/env python3
"""
test_ingest_toronto_rec.py - Toronto's drop-in schedule and EarlyON hours, and
the ways each is not what it looks like.

Every session, location, point and centre in FIXTURE was read live from the
City's CKAN datastore on 2026-10-03, or on 2026-10-04 for the rows added after
review (trimmed to the columns the adapter reads); synthetic cases are built in
code and say so. The expensive cases are the quiet ones: three lane swims that
merge into one row and keep one time, one row spanning the hours between them,
Pickleball for 60+ folded into Pickleball for 19+, two EarlyON centres with one
name, a staff email published as a link, and a price sentence that says "free"
where the City charges.

    python test_ingest_toronto_rec.py
"""
import json
import os
import re
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261003"

from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_toronto_rec as T  # noqa: E402
from mapsee_ingest import EventStore, VALID_CATEGORIES  # noqa: E402
from mapsee_supabase_sync import derive_categories, _cap_prose, to_row  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CFG = T.load_config(os.path.join(HERE, "toronto_rec_sources.json"))
TZ = ZoneInfo(CFG["timezone"])
TODAY = T._today(TZ)
DROPIN = next(s for s in CFG["sources"] if s["kind"] == "dropin")
EARLYON = next(s for s in CFG["sources"] if s["kind"] == "earlyon")

# ../mapsee migration 0227 reads `offer:free` from a row's own text. This is a
# SUBSET of ../mapsee/tools/measure_deals.py's FREE - the alternatives these
# price sentences could hit - without its "(admission|cost|...) is free" and "no
# cover" branches or FREE_NEG/FREE_COND/FREE_PERK. The full classify, run over
# every synced row on 2026-10-04 (28,007 after the stretch split), disagreed
# with the tier on 0 rows; this subset keeps the gate offline.
FREE_TAG = re.compile(
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|join|enter|"
    r"participate|the\s+public|all|everyone)|and\s+open|for\s+(?:all|everyone|kids|children|the\s+public|"
    r"families)|community\s+event|concert|show|screening|class|classes|workshop|tour|tours|tasting|"
    r"session|lesson|drop[- ]in|museum|comedy|yoga|rsvp|with\s+(?:rsvp|registration|admission|entry)|"
    r"tickets?|family\s+(?:day|fun))\b|\bat\s+no\s+cost\b|\bfree\s*!|"
    r"\bis\s+free\b(?!\s+(?:of|from|to\s+(?:use|choose)))", re.I)

FIXTURE = json.loads(r"""{
 "sessions": [
  {"Location ID": 3775, "Course Title": "Aquatic Fitness: Shallow", "Section": "Swim - Drop-In", "Age Min": "13", "Age Max": "None", "Start Hour": 9, "Start Minute": 0, "End Hour": 10, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Aquatic Fitness: Shallow", "Section": "Swim - Drop-In", "Age Min": "13", "Age Max": "None", "Start Hour": 10, "Start Minute": 15, "End Hour": 11, "End Min": 15, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Recreation Fun and Play with Caregiver", "Section": "Early Years - Drop-In", "Age Min": "1", "Age Max": "5", "Start Hour": 10, "Start Minute": 30, "End Hour": 12, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Recreation Fun and Play with Caregiver", "Section": "Early Years - Drop-In", "Age Min": "1", "Age Max": "5", "Start Hour": 16, "Start Minute": 30, "End Hour": 18, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Aquatic Fitness: Shallow", "Section": "Reserve a Spot - Aquatic Fitness", "Age Min": "13", "Age Max": "None", "Start Hour": 9, "Start Minute": 0, "End Hour": 10, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Aquatic Fitness: Shallow", "Section": "Reserve a Spot - Aquatic Fitness", "Age Min": "13", "Age Max": "None", "Start Hour": 10, "Start Minute": 15, "End Hour": 11, "End Min": 15, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Lane Swim", "Section": "Swim - Drop-In", "Age Min": "7", "Age Max": "None", "Start Hour": 7, "Start Minute": 30, "End Hour": 8, "End Min": 45, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Lane Swim", "Section": "Swim - Drop-In", "Age Min": "7", "Age Max": "None", "Start Hour": 11, "Start Minute": 45, "End Hour": 12, "End Min": 45, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Lane Swim", "Section": "Swim - Drop-In", "Age Min": "7", "Age Max": "None", "Start Hour": 20, "Start Minute": 0, "End Hour": 21, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Lane Swim: Older Adult", "Section": "Swim - Drop-In", "Age Min": "60", "Age Max": "None", "Start Hour": 14, "Start Minute": 0, "End Hour": 15, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Weight/Cardio Room", "Section": "FitnessTO - Drop-In", "Age Min": "13", "Age Max": "None", "Start Hour": 7, "Start Minute": 30, "End Hour": 21, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Walking/Running Track", "Section": "FitnessTO - Drop-In", "Age Min": "13", "Age Max": "None", "Start Hour": 7, "Start Minute": 30, "End Hour": 21, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3775, "Course Title": "Lane Swim", "Section": "Swim - Drop-In", "Age Min": "7", "Age Max": "None", "Start Hour": 7, "Start Minute": 30, "End Hour": 8, "End Min": 45, "First Date": "2026-11-02", "Last Date": "2026-11-02"},
  {"Location ID": 3775, "Course Title": "Lane Swim", "Section": "Swim - Drop-In", "Age Min": "7", "Age Max": "None", "Start Hour": 7, "Start Minute": 30, "End Hour": 8, "End Min": 45, "First Date": "2026-10-01", "Last Date": "2026-10-01"},
  {"Location ID": 2791, "Course Title": "Pickleball", "Section": "Sports - Drop-In", "Age Min": "60", "Age Max": "None", "Start Hour": 19, "Start Minute": 15, "End Hour": 20, "End Min": 15, "First Date": "2026-10-08", "Last Date": "2026-10-08"},
  {"Location ID": 2791, "Course Title": "Pickleball", "Section": "Sports - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 13, "Start Minute": 30, "End Hour": 15, "End Min": 0, "First Date": "2026-10-08", "Last Date": "2026-10-08"},
  {"Location ID": 2791, "Course Title": "Pickleball", "Section": "Sports - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 20, "Start Minute": 30, "End Hour": 21, "End Min": 30, "First Date": "2026-10-08", "Last Date": "2026-10-08"},
  {"Location ID": 357, "Course Title": "Humber College Swim", "Section": "Swim - Drop-In", "Age Min": "17", "Age Max": "None", "Start Hour": 6, "Start Minute": 45, "End Hour": 8, "End Min": 55, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 523, "Course Title": "Leisure Swim: Preschool", "Section": "Swim - Drop-In", "Age Min": "0", "Age Max": "5", "Start Hour": 9, "Start Minute": 15, "End Hour": 10, "End Min": 15, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 523, "Course Title": "Leisure Skate", "Section": "Skate - Drop-In", "Age Min": "0", "Age Max": "None", "Start Hour": 16, "Start Minute": 45, "End Hour": 17, "End Min": 45, "First Date": "2026-10-07", "Last Date": "2026-10-07"},
  {"Location ID": 600, "Course Title": "Cards", "Section": "Hobbies and Interests - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 12, "Start Minute": 0, "End Hour": 14, "End Min": 0, "First Date": "2026-10-06", "Last Date": "2026-10-06"},
  {"Location ID": 345, "Course Title": "Knitting", "Section": "Arts - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 13, "Start Minute": 0, "End Hour": 15, "End Min": 0, "First Date": "2026-10-06", "Last Date": "2026-10-06"},
  {"Location ID": 3502, "Course Title": "Pickleball", "Section": "Sports - Drop-In", "Age Min": "60", "Age Max": "None", "Start Hour": 9, "Start Minute": 0, "End Hour": 11, "End Min": 0, "First Date": "2026-10-06", "Last Date": "2026-10-06"},
  {"Location ID": 3502, "Course Title": "AMPED Art of DJing", "Section": "EYS - Arts, Hobbies and Interest Drop-In", "Age Min": "13", "Age Max": "24", "Start Hour": 17, "Start Minute": 0, "End Hour": 20, "End Min": 0, "First Date": "2026-10-06", "Last Date": "2026-10-06"},
  {"Location ID": 3643, "Course Title": "Indoor Playground with Caregiver", "Section": "Hobbies and Interests - Drop-In", "Age Min": "1", "Age Max": "12", "Start Hour": 10, "Start Minute": 0, "End Hour": 11, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Indoor Playground with Caregiver", "Section": "Hobbies and Interests - Drop-In", "Age Min": "1", "Age Max": "12", "Start Hour": 11, "Start Minute": 30, "End Hour": 12, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Indoor Playground with Caregiver", "Section": "Hobbies and Interests - Drop-In", "Age Min": "1", "Age Max": "12", "Start Hour": 13, "Start Minute": 30, "End Hour": 14, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Indoor Playground with Caregiver", "Section": "Hobbies and Interests - Drop-In", "Age Min": "1", "Age Max": "12", "Start Hour": 15, "Start Minute": 0, "End Hour": 16, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Indoor Playground with Caregiver", "Section": "Hobbies and Interests - Drop-In", "Age Min": "1", "Age Max": "12", "Start Hour": 17, "Start Minute": 0, "End Hour": 18, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Indoor Playground with Caregiver", "Section": "Hobbies and Interests - Drop-In", "Age Min": "1", "Age Max": "12", "Start Hour": 18, "Start Minute": 30, "End Hour": 19, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Walking", "Section": "FitnessTO - Drop-In", "Age Min": "13", "Age Max": "None", "Start Hour": 11, "Start Minute": 0, "End Hour": 19, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 3643, "Course Title": "Pickleball", "Section": "Sports - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 18, "Start Minute": 0, "End Hour": 20, "End Min": 0, "First Date": "2026-10-07", "Last Date": "2026-10-07"},
  {"Location ID": 3643, "Course Title": "Pickleball", "Section": "Sports - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 20, "Start Minute": 0, "End Hour": 22, "End Min": 0, "First Date": "2026-10-07", "Last Date": "2026-10-07"},
  {"Location ID": 58, "Course Title": "Table Tennis", "Section": "Sports - Drop-In", "Age Min": "19", "Age Max": "None", "Start Hour": 9, "Start Minute": 15, "End Hour": 21, "End Min": 0, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 474, "Course Title": "Open Gym", "Section": "EYS - Sports Drop-In", "Age Min": "18", "Age Max": "24", "Start Hour": 13, "Start Minute": 0, "End Hour": 15, "End Min": 30, "First Date": "2026-10-05", "Last Date": "2026-10-05"},
  {"Location ID": 2791, "Course Title": "Music: Karaoke", "Section": "Hobbies and Interests - Drop-In", "Age Min": "60", "Age Max": "None", "Start Hour": 9, "Start Minute": 30, "End Hour": 12, "End Min": 0, "First Date": "2026-10-08", "Last Date": "2026-10-08"},
  {"Location ID": 2791, "Course Title": "AMPED Art of DJing", "Section": "EYS - Arts, Hobbies and Interest Drop-In", "Age Min": "13", "Age Max": "24", "Start Hour": 16, "Start Minute": 15, "End Hour": 18, "End Min": 15, "First Date": "2026-10-08", "Last Date": "2026-10-08"}],
 "locations": [
  {"Location ID": 58, "Location Name": "Jimmie Simpson Recreation Centre", "Location Type": "crc", "Street No": "870", "Street No Suffix": "None", "Street Name": "Queen", "Street Type": "St", "Street Direction": "E", "Postal Code": "M4M 3G9"},
  {"Location ID": 155, "Location Name": "Jimmie Simpson Park", "Location Type": "park", "Street No": "870", "Street No Suffix": "None", "Street Name": "Queen", "Street Type": "St", "Street Direction": "E", "Postal Code": "None"},
  {"Location ID": 474, "Location Name": "The New Generation Youth Recreation Centre", "Location Type": "crc", "Street No": "2694", "Street No Suffix": "None", "Street Name": "Eglinton", "Street Type": "Ave.", "Street Direction": "W", "Postal Code": "M6M 1T9"},
  {"Location ID": 3643, "Location Name": "Canoe Landing Community Recreation Centre", "Location Type": "crc", "Street No": "45", "Street No Suffix": "None", "Street Name": "Fort York Blvd.", "Street Type": "None", "Street Direction": "None", "Postal Code": "M5V 0R6"},
  {"Location ID": 345, "Location Name": "Riverdale Farm", "Location Type": "Other", "Street No": "201", "Street No Suffix": "None", "Street Name": "Winchester", "Street Type": "St", "Street Direction": "None", "Postal Code": "None"},
  {"Location ID": 357, "Location Name": "Humber Community Pool", "Location Type": "None", "Street No": "205", "Street No Suffix": "None", "Street Name": "Humber College", "Street Type": "Blvd", "Street Direction": "None", "Postal Code": "M9W 5L7"},
  {"Location ID": 523, "Location Name": "Agincourt Community Recreation Centre", "Location Type": "crc", "Street No": "31", "Street No Suffix": "None", "Street Name": "Glen Watford", "Street Type": "Dr", "Street Direction": "None", "Postal Code": "M1S 2B7"},
  {"Location ID": 600, "Location Name": "Cedarbrook Community Centre", "Location Type": "crc", "Street No": "91", "Street No Suffix": "None", "Street Name": "Eastpark", "Street Type": "Blvd", "Street Direction": "None", "Postal Code": "M1H 1C6"},
  {"Location ID": 2791, "Location Name": "Parkway Forest Community Centre", "Location Type": "crc", "Street No": "55", "Street No Suffix": "None", "Street Name": "Forest Manor", "Street Type": "Rd", "Street Direction": "None", "Postal Code": "M2J 0C2"},
  {"Location ID": 3502, "Location Name": "Regent Park Community Centre", "Location Type": "crc", "Street No": "402", "Street No Suffix": "None", "Street Name": "Shuter", "Street Type": "St", "Street Direction": "None", "Postal Code": "None"},
  {"Location ID": 3775, "Location Name": "Ethennonnhawahstihnen' Community Recreation Centre and Library", "Location Type": "crc", "Street No": "100", "Street No Suffix": "None", "Street Name": "Ethennonnhawahstihnen'", "Street Type": "Ln.", "Street Direction": "None", "Postal Code": "None"}],
 "points": [
  {"LOCATIONID": "58", "TYPE": "Community Centre", "URL": "https://www.toronto.ca/explore-enjoy/parks-recreation/places-spaces/parks-and-recreation-facilities/location/?id=58", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.345313347794, 43.6604475805934]}"},
  {"LOCATIONID": "474", "TYPE": "Community Centre", "URL": "https://www.toronto.ca/explore-enjoy/parks-recreation/places-spaces/parks-and-recreation-facilities/location/?id=474", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.4770361975589, 43.6902863306723]}"},
  {"LOCATIONID": "3643", "TYPE": "Community Centre", "URL": "https://www.toronto.ca/explore-enjoy/parks-recreation/places-spaces/parks-and-recreation-facilities/location/?id=3643", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.394731493621, 43.6394377946956]}"},
  {"LOCATIONID": "883", "TYPE": "Park", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.1400772440666, 43.7798338043484]}"},
  {"LOCATIONID": "2791", "TYPE": "Community Centre", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.3436070411884, 43.7725666040359]}"},
  {"LOCATIONID": "3502", "TYPE": "Community Centre", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.3616922501387, 43.6582745197575]}"},
  {"LOCATIONID": "357", "TYPE": "Community Centre", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.6086678287855, 43.7305517812869]}"},
  {"LOCATIONID": "3775", "TYPE": "Community Centre", "URL": "https://www.toronto.ca/explore-enjoy/parks-recreation/places-spaces/parks-and-recreation-facilities/location/?id=3775", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.3756404303766, 43.7683957667604]}"},
  {"LOCATIONID": "523", "TYPE": "Community Centre", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.2757520164385, 43.7885003824549]}"},
  {"LOCATIONID": "600", "TYPE": "Community Centre", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.2273953320968, 43.7556239062328]}"},
  {"LOCATIONID": "883", "TYPE": "Community Centre", "geometry": "{\"type\": \"Point\", \"coordinates\": [-79.1402808119392, 43.7796658897158]}"}],
 "earlyon": [
  {"loc_id": 13650, "program_name": "101 Spruce St EarlyON Child and Family Centre", "agency": "Regent Park Community Health Centre", "buildingName": "Toronto Kiwanis Boys & Girls Clubs", "address": "101 Spruce St", "full_address": "101 Spruce St, Toronto, ON M5A 2J3", "lat": 43.664194018, "lng": -79.362233991, "website": "https://regentparkchc.org/program/earlyon-drop-in-centre/", "dropinHours": "Wednesday: 9:00 a.m. - 11:30 a.m.  ", "languages": "", "french_language_program": null, "indigenous_program": null},
  {"loc_id": 13038, "program_name": "Eastview EarlyON Child and Family Centre", "agency": "Toronto District School Board", "buildingName": "Eastview Public School", "address": "20 Waldock St", "full_address": "20 Waldock St, Scarborough, ON M1E 2E5", "lat": 43.758963359, "lng": -79.191280443, "website": "https://www.tdsb.on.ca/Find-your/Schools/School-PFLC/schno/4450", "dropinHours": "Monday: 8:30 a.m. - 12:30 p.m.   | Tuesday: 8:30 a.m. - 12:30 p.m.   | Wednesday: 8:30 a.m. - 12:30 p.m.   | Thursday: 8:30 a.m. - 12:30 p.m.   | Friday: 8:30 a.m. - 12:30 p.m.  ", "languages": "", "french_language_program": null, "indigenous_program": null},
  {"loc_id": 6235, "program_name": "Eastview EarlyON Child and Family Centre", "agency": "East Toronto Family Community Centre", "buildingName": "Eastview Neighbourhood Community Centre", "address": "86 Blake St", "full_address": "86 Blake St, Toronto, ON M4J 3C9", "lat": 43.675033897, "lng": -79.339926796, "website": "https://www.eastviewcentre.com/family-resource-early-on", "dropinHours": "Sunday: 1:00 p.m. - 4:00 p.m.   | Monday: 9:30 a.m. - 1:00 p.m.   | Tuesday: 9:30 a.m. - 1:00 p.m.   | Wednesday: 9:30 a.m. - 3:00 p.m.   | Thursday: 9:30 a.m. - 1:00 p.m.   | Friday: 9:30 a.m. - 1:00 p.m.  ", "languages": "Cantonese; Dari; German; Mandarin; Somali; Spanish; Urdu", "french_language_program": null, "indigenous_program": null},
  {"loc_id": 14380, "program_name": "1033 EarlyON Child and Family Centre", "agency": "West Neighbourhood House O/a St. Christopher House", "buildingName": null, "address": "1033 King St W", "full_address": "1033 King St W, Toronto, ON M6K 3N3", "lat": 43.640968279, "lng": -79.416392031, "website": "https://www.westnh.org/earlyon-program/", "dropinHours": "None", "languages": "", "french_language_program": null, "indigenous_program": null},
  {"loc_id": 6275, "program_name": "West Scarborough EarlyON Child and Family Centre", "agency": "West Scarborough Neighbourhood Community Centre", "buildingName": "West Scarborough Neighbourhood Community Centre", "address": "313 Pharmacy Ave", "full_address": "313 Pharmacy Ave, Scarborough, ON M1L 3E7", "lat": 43.701416363, "lng": -79.2846817825, "website": "www.wsncc.org", "dropinHours": "Monday: 9:00 a.m. - 11:30 a.m.   | Tuesday: 10:00 a.m. - 11:00 a.m.  ; 10:00 a.m. - noon   | Wednesday: 9:30 a.m. - 11:30 a.m.   | Thursday: 1:00 p.m. - 2:30 p.m.  ; 9:00 a.m. - 11:30 a.m.  ; 10:00 a.m. - 11:00 a.m.   | Friday: 9:00 a.m. - 11:30 a.m.  ; 1:00 p.m. - 2:30 p.m.  ", "languages": "", "french_language_program": null, "indigenous_program": null}]
}""")


def dropin(sessions=None, **over):
    src = dict(DROPIN, **over)
    locs = {T._int(l["Location ID"]): l for l in FIXTURE["locations"]}
    pts = T.location_points(FIXTURE["points"], CFG["bbox"])
    stats = {}
    evs = T.dropin_events(FIXTURE["sessions"] if sessions is None else sessions,
                          locs, pts, src, CFG, TZ, TODAY, stats)
    return evs, stats


def by_name(evs, name, day=None):
    return [e for e in evs if e.name == name and (day is None or e.start_local[:10] == day)]


# ------------------------------------------------------------------ 1. readers
print("small readers")
check("'None' is null: the Locations resource writes a missing value as that word",
      T._none("None") is None and T._none(" none ") is None and T._none("0") == "0")
check("age bands read in YEARS",
      [T.age_label("7", "None"), T.age_label("0", "None"), T.age_label("13", "17"),
       T.age_label("12", "12"), T.age_label("0", "98")]
      == ["ages 7+", "all ages", "ages 13-17", "age 12", "all ages"])
check("clock times as a Canadian page writes them",
      [T.span(540, 690), T.span(705, 765), T.span(720, 960), T.span(990, 1080)]
      == ["9:00-11:30 a.m.", "11:45 a.m.-12:45 p.m.", "noon-4:00 p.m.", "4:30-6:00 p.m."],
      [T.span(540, 690), T.span(705, 765), T.span(720, 960), T.span(990, 1080)])
loc_3775 = next(l for l in FIXTURE["locations"] if l["Location ID"] == 3775)
check("street line from the columns, 'None' dropped and 'Ln.' tidied",
      T.street_line(loc_3775) == "100 Ethennonnhawahstihnen' Ln", T.street_line(loc_3775))
check("a street with no number is the street, not 'None Lake Shore'",
      T.street_line({"Street No": "None", "Street Name": "Lake Shore"}) == "Lake Shore")

# ------------------------------------------------------- 2. points, free centres
print()
print("the join")
pts = T.location_points(FIXTURE["points"], CFG["bbox"])
check("the facility point is GeoJSON [lon, lat] inside a string, read the right way round",
      43.55 < pts["3775"][0] < 43.9 and -79.7 < pts["3775"][1] < -79.05, pts.get("3775"))
port = [p for p in FIXTURE["points"] if p["LOCATIONID"] == "883"]
centre = json.loads(next(p for p in port if p["TYPE"] == "Community Centre")["geometry"])["coordinates"]
check("a LOCATIONID listed twice (centre and its park) takes the Community Centre's point",
      len(port) == 2 and pts["883"][:2] == (centre[1], centre[0]), pts.get("883"))
swapped = dict(port[0], LOCATIONID="9999",
               geometry='{"type": "Point", "coordinates": [43.78, -79.14]}')   # synthetic: lat/lon swapped
check("a swapped pair is outside the box and placed nowhere",
      "9999" not in T.location_points([swapped], CFG["bbox"]))
free = T.FreeCentres(DROPIN["free_centres"])
locs = {l["Location ID"]: l for l in FIXTURE["locations"]}
check("Regent Park, Cedarbrook and Jimmie Simpson are Free Centres, matched on the City's Location ID",
      locs[3502] in free and locs[600] in free and locs[58] in free)
check("...and Agincourt is not", locs[523] not in free)
check("A PARK AT A FREE CENTRE'S ADDRESS IS NOT A FREE CENTRE (Jimmie Simpson Park, 870 Queen St E)",
      locs[155] not in free and (locs[155]["Street No"], locs[155]["Street Name"])
      == (locs[58]["Street No"], locs[58]["Street Name"]))
check("...nor is any address-only match: number + street without the id says nothing",
      {"Street No": "2000", "Street Name": "Mcnicoll", "Location Name": "L'Amoreaux Community Recreation Centre"}
      not in free and {"Location ID": 788} in free)

# ------------------------------------------------------------- 3. the drop-ins
print()
print("drop-in rows")
evs, stats = dropin()
check("a weight room, a running track, an all-day walking track and an all-day table-tennis room "
      "are facilities, not programmes",
      stats.get("facility use, not a programme") == 4
      and not by_name(evs, "Walking, ages 13+") and not by_name(evs, "Table Tennis, ages 19+"), stats)
check("Reserve a Spot needs a booking and is refused - its twin drop-in is kept",
      stats.get("reservation required (Reserve a Spot)") == 2
      and by_name(evs, "Aquatic Fitness: Shallow, ages 13+", "2026-10-05"), stats)
check("Humber College Swim is one college's pool time", stats.get("restricted audience") == 1, stats)
check("a session before today is past", stats.get("past") == 1, stats)
check("Riverdale Farm has no point: counted unplaceable, never guessed",
      stats.get("unplaceable (no point)") == 1 and not [e for e in evs if "Riverdale" in (e.venue_name or "")],
      stats)
lane = sorted(by_name(evs, "Lane Swim, ages 7+", "2026-10-05"), key=lambda e: e.start_local)
check("THREE LANE SWIMS WITH GAPS ARE THREE ROWS, each on its own clock - never one row 07:30-21:00 "
      "that ../mapsee pulses 'happening now' through the gaps",
      [(e.start_local[11:16], e.end_local[11:16]) for e in lane]
      == [("07:30", "08:45"), ("11:45", "12:45"), ("20:00", "21:00")],
      [(e.start_local, e.end_local) for e in lane])
check("...three fingerprints and three source_ids, so the store keeps all three",
      len({e.fingerprint for e in lane}) == 3 and len({e.source_id for e in lane}) == 3)
if lane:
    e = lane[0]
    check("...each naming the day's other stretches, and never claiming it 'does not run in between'",
          "Also on this day: 11:45 a.m.-12:45 p.m. and 8:00-9:00 p.m." in e.description
          and "in between" not in e.description, e.description)
    check("EDT in October: 07:30 local is 11:30Z", e.start_utc == "2026-10-05T11:30:00Z", e.start_utc)
play = by_name(evs, "Indoor Playground with Caregiver, ages 1-12", "2026-10-05")
check("six one-hour playground sessions with gaps (live, Canoe Landing 10:00-19:30) are six rows",
      len(play) == 6 and max(e.end_local for e in play) == "2026-10-05T19:30:00", len(play))
b2b = by_name(evs, "Pickleball, ages 19+", "2026-10-07")
check("BACK-TO-BACK sessions are one stretch: Pickleball 18:00-20:00 + 20:00-22:00 is one row 18:00-22:00",
      len(b2b) == 1 and (b2b[0].start_local[11:16], b2b[0].end_local[11:16]) == ("18:00", "22:00"),
      [(e.start_local, e.end_local) for e in b2b])
check("...listing its sessions, with no 'also' and no gap claimed",
      b2b and "Sessions: 6:00-8:00 p.m. and 8:00-10:00 p.m." in b2b[0].description
      and "Also on this day" not in b2b[0].description and "in between" not in b2b[0].description,
      b2b and b2b[0].description)


def _m(hm):
    return int(hm[11:13]) * 60 + int(hm[14:16])


spans = []
for e in evs:
    lid, _, day, _ = e.source_id.split("|")
    mine = sorted((s["Start Hour"] * 60 + s["Start Minute"], s["End Hour"] * 60 + s["End Min"])
                  for s in FIXTURE["sessions"] if str(s["Location ID"]) == lid and s["First Date"] == day
                  and e.name.startswith(s["Course Title"] + ", "))
    cur, a, b = _m(e.start_local), _m(e.start_local), _m(e.end_local)
    for x, y in mine:
        if y <= cur or x >= b:
            continue
        if x > cur + T.JOIN_MINUTES:
            spans.append((e.name, e.start_local, e.end_local))
            break
        cur = max(cur, y)
check("NO ROW SPANS A GAP: every minute of every row is inside a published session", not spans, spans)
syn = [dict(FIXTURE["sessions"][-2], **{"Course Title": "Table Tennis", "Section": "Sports - Drop-In",
                                        "Age Min": "19", "Age Max": "None", "Start Hour": h, "Start Minute": 0,
                                        "End Hour": h + 3, "End Min": 0, "First Date": "2026-10-06",
                                        "Last Date": "2026-10-06"}) for h in (9, 12, 17)]   # synthetic
got, st_syn = dropin(syn)
check("a table-tennis room published as back-to-back 3-hour blocks is read on its STRETCH (9-15 refused), "
      "while a separate 3-hour club hour stays",
      [(e.start_local[11:16], e.end_local[11:16]) for e in got] == [("17:00", "20:00")]
      and st_syn.get("facility use, not a programme") == 2, ([(e.start_local, e.end_local) for e in got], st_syn))
yz = [dict(FIXTURE["sessions"][-2], **{"Course Title": "Youth Zone", "Age Min": "13", "Age Max": "24",
                                       "Start Hour": 11, "Start Minute": 0, "End Hour": 19,
                                       "End Min": 0})]   # synthetic, the live 11:00-19:00 shape
got, _ = dropin(yz)
check("...but a staffed all-day Youth Zone is a programme and is kept", len(got) == 1)
nov = by_name(evs, "Lane Swim, ages 7+", "2026-11-02")
check("EST after 1 November: 07:30 local is 12:30Z",
      nov and nov[0].start_utc == "2026-11-02T12:30:00Z", [x.start_utc for x in nov])
pk19 = by_name(evs, "Pickleball, ages 19+", "2026-10-08")
pk60 = by_name(evs, "Pickleball, ages 60+", "2026-10-08")
check("PICKLEBALL FOR 60+ IS NOT PICKLEBALL FOR 19+: their own rows and fingerprints "
      "(19+ runs 13:30 and 20:30, two stretches)",
      len(pk19) == 2 and len(pk60) == 1 and pk60[0].fingerprint not in {e.fingerprint for e in pk19},
      [(e.name, e.start_local) for e in pk19 + pk60])
repeat = [dict(s) for s in FIXTURE["sessions"] if s["Course Title"] == "Lane Swim: Older Adult"]
again, st2 = dropin(repeat + repeat)
check("the same session published twice is one session",
      len(again) == 1 and "times this day" not in again[0].description
      and st2.get("repeat sessions folded") == 1, st2)
check("every row is placed on the City's own point and says so (coords_exact)",
      evs and all(e.coords_exact and e.latitude and e.longitude for e in evs))
check("address is line 1 only, with city, province and postal code in their own fields",
      lane and lane[0].address == "100 Ethennonnhawahstihnen' Ln"
      and (lane[0].city, lane[0].region, lane[0].country) == ("Toronto", "ON", "CA"))
check("the link is the centre's toronto.ca page",
      lane and lane[0].ticket_url.endswith("location/?id=3775"), lane and lane[0].ticket_url)

print()
print("categories")
cat = {e.name: (e.category, e.categories) for e in evs}
check("Lane Swim -> fitness", cat.get("Lane Swim, ages 7+", ("",))[0] == "fitness")
check("Pickleball -> sports", cat.get("Pickleball, ages 19+", ("",))[0] == "sports")
check("an Early Years drop-in -> kids",
      cat.get("Recreation Fun and Play with Caregiver, ages 1-5", ("",))[0] == "kids")
check("a preschool swim stays a swim and ALSO reaches the family door",
      cat.get("Leisure Swim: Preschool, ages 0-5") == ("fitness", ["kids"]),
      cat.get("Leisure Swim: Preschool, ages 0-5"))
check("Cards for adults -> community, no kids", cat.get("Cards, ages 19+") == ("community", []),
      cat.get("Cards, ages 19+"))
for e in evs:
    if not (e.category in VALID_CATEGORIES and all(c in VALID_CATEGORIES for c in e.categories)):
        check(f"{e.name}: only real lens keys", False, (e.category, e.categories))
        break
else:
    check("every key is a real lens key", True)
routed = {e.name: derive_categories(e.as_record("2026-10-03T00:00:00Z"))[0] for e in evs}
check("the sync keeps the routing: lane swim on fitness, pickleball on sports",
      routed.get("Lane Swim, ages 7+") == "fitness" and routed.get("Pickleball, ages 60+") == "sports",
      routed)
karaoke = by_name(evs, "Music: Karaoke, ages 60+")
kd = karaoke and derive_categories(karaoke[0].as_record("2026-10-03T00:00:00Z"))
check("SENIORS' MORNING KARAOKE IS NOT NIGHTLIFE: the real derive_categories gives it no party door",
      kd and "party" not in [kd[0]] + (kd[1] or []) and "community" in (kd[1] or []), kd)

print()
print("the price sentence is a contract with ../mapsee 0227")
tier = {}
for e in evs:
    tier[(e.name, e.venue_name)] = ("free" if "Free drop-in" in e.description else
                                    "fee" if "Drop-in fee:" in e.description else "may")
AG, PF, ET = ("Agincourt Community Recreation Centre", "Parkway Forest Community Centre",
              "Ethennonnhawahstihnen' Community Recreation Centre and Library")
check("leisure swim says free (all City pools)", tier.get(("Leisure Swim: Preschool, ages 0-5", AG)) == "free")
check("every skating drop-in says free", tier.get(("Leisure Skate, all ages", AG)) == "free")
check("older-adult lane swim says free (the fee table says Free)",
      tier.get(("Lane Swim: Older Adult, ages 60+", ET)) == "free")
check("anything at a Free Centre says free - even Pickleball, which charges elsewhere",
      [e for e in evs if e.venue_name == "Regent Park Community Centre"
       and e.name.startswith("Pickleball")][0].description.count("Free drop-in") == 1)
check("lane swim at an ordinary indoor pool says Drop-in fee", tier.get(("Lane Swim, ages 7+", ET)) == "fee")
check("aquatic fitness says Drop-in fee", tier.get(("Aquatic Fitness: Shallow, ages 13+", ET)) == "fee")
check("pickleball elsewhere says fees MAY apply - the City's own words", tier.get(("Pickleball, ages 19+", PF)) == "may")
check("an Enhanced Youth Space ARTS drop-in says free (the page names DJing) at an ordinary centre",
      tier.get(("AMPED Art of DJing, ages 13-24", PF)) == "free")
check("...but an EYS SPORTS drop-in does not: the page names no sport, so fees may apply",
      tier.get(("Open Gym, ages 18-24", "The New Generation Youth Recreation Centre")) == "may",
      tier.get(("Open Gym, ages 18-24", "The New Generation Youth Recreation Centre")))
wrong = [e.name for e in evs
         if bool(FREE_TAG.search(e.name + "\n" + e.description)) != (tier[(e.name, e.venue_name)] == "free")]
check("0227's free reading agrees with the tier on every row", not wrong, wrong)
check("a row that is not free never contains the word at all",
      not [e.name for e in evs if tier[(e.name, e.venue_name)] != "free" and "free" in e.description.lower()])

print()
print("the licence line")
check("the attribution is the LAST paragraph of every row",
      all(e.description.rsplit("\n\n", 1)[-1] == CFG["attribution"] for e in evs))
long_desc = "x " * 600 + "\n\n" + CFG["attribution"]
check("...and survives the sync's trim, because it is under 200 characters",
      len(CFG["attribution"]) <= 200 and _cap_prose(long_desc).endswith(CFG["attribution"]))

print()
print("identity")
again, _ = dropin(list(reversed(FIXTURE["sessions"])))
check("a re-read in another order writes the same rows (fingerprint and source_id)",
      sorted((e.fingerprint, e.source_id) for e in evs) == sorted((e.fingerprint, e.source_id) for e in again))
check("source_id is place + title + day + the stretch's first clock, never the City's row number",
      lane and lane[0].source_id == "3775|laneswimages7|2026-10-05|07:30", lane and lane[0].source_id)
moved = [dict(s) for s in FIXTURE["sessions"] if s["Course Title"] == "Lane Swim" and s["First Date"] == "2026-10-05"]
moved[2]["End Min"] = 30   # synthetic: the evening swim runs half an hour longer
again2, _ = dropin(moved)
check("a stretch that ends later keeps its identity (same source_id and fingerprint, new end)",
      sorted((e.source_id, e.fingerprint) for e in again2) == sorted((e.source_id, e.fingerprint) for e in lane)
      and max(e.end_local for e in again2) == "2026-10-05T21:30:00")
with tempfile.TemporaryDirectory() as tmp:
    store = EventStore(os.path.join(tmp, "s.json"))
    for e in evs:
        store.upsert(e)
    first = dict(store.stats)
    for e in dropin()[0]:
        store.upsert(e)
    check("a second run updates in place: nothing added, nothing rekeyed",
          first["added"] == len(evs) and store.stats["added"] == len(evs)
          and store.stats["rekeyed"] == 0 and store.stats["updated"] == len(evs), store.stats)
    check("...and nothing was refused as spam or a notice",
          store.stats["rejected"] == 0 and store.stats["notices"] == 0, store.stats)

# ----------------------------------------------------------------- 4. EarlyON
print()
print("EarlyON hours")
week, bad = T.parse_hours("Monday: 9:00 a.m. - noon  ; 1:00 p.m. - 3:30 p.m.   | Tuesday: noon - 4:00 p.m.  ")
check("days split on |, ranges on ;, 'noon' is 12:00",
      week == {0: [(540, 720), (780, 930)], 1: [(720, 960)]} and not bad, (week, bad))
west = next(c for c in FIXTURE["earlyon"] if c["loc_id"] == 6275)
week, bad = T.parse_hours(west["dropinHours"])
check("a live day written out of order is sorted (Thursday: 1:00 p.m. before 9:00 a.m.)",
      week[3][0][0] == 540 and not bad, week.get(3))
_, bad = T.parse_hours("Monday: 9 to 5 | Funday: 10:00 a.m. - noon")
check("what does not parse is reported, not guessed", len(bad) == 2, bad)

check("a website field holding an EMAIL is not a link (live 2026-10-04: a staff address, 11 rows)",
      [T._website(u) for u in ("a.person@example.org", "https://a.person@example.org",
                               "https://user:pw@example.org/x", "mailto:a.person@example.org",
                               "www", "https://localhost/", "not a url.org")] == [None] * 7)
check("...while a real page, bare or schemed, still is",
      [T._website(u) for u in ("www.wsncc.org", "https://regentparkchc.org/program/earlyon-drop-in-centre/")]
      == ["https://www.wsncc.org", "https://regentparkchc.org/program/earlyon-drop-in-centre/"])

rows = [dict(c) for c in FIXTURE["earlyon"]]
rows[0].update(contact_fullname="A Person", email="a.person@example.org",   # synthetic, must not leak
               website="a.person@example.org")                              # synthetic: the live shape
st = {}
ee = T.earlyon_events(rows, EARLYON, CFG, TZ, TODAY, st)
check("a centre whose hours are the word 'None' writes nothing and is counted - not as an unparsed fragment",
      st.get("centre with no drop-in hours") == 1 and not st.get("unparsed hour fragments"), st)
spruce = [e for e in ee if e.venue_name.startswith("101 Spruce")]
check("a Wednesday-only centre has two Wednesdays in 14 days (7th and 14th)",
      [e.start_local for e in spruce] == ["2026-10-07T09:00:00", "2026-10-14T09:00:00"],
      [e.start_local for e in spruce])
check("Thanksgiving Monday is skipped, and counted",
      not [e for e in ee if e.start_local.startswith("2026-10-12")] and st.get("holiday skipped") == 3, st)
east = [e for e in ee if e.venue_name.startswith("Eastview") and e.start_local.startswith("2026-10-05")]
check("TWO CENTRES CALLED 'Eastview EarlyON ...' ARE TWO ROWS, not one merged into the other",
      len(east) == 2 and len({e.fingerprint for e in east}) == 2, [e.address for e in east])
gap = sorted((e for e in ee if e.venue_name.startswith("West Scarborough")
               and e.start_local.startswith("2026-10-08")), key=lambda e: e.start_local)
check("A DAY WITH A CLOSED GAP IS TWO ROWS (9:00-11:30, 1:00-2:30), the 10-11 inside 9-11:30 folded in",
      [(e.start_local[11:16], e.end_local[11:16]) for e in gap] == [("09:00", "11:30"), ("13:00", "14:30")]
      and len({e.fingerprint for e in gap}) == 2, [(e.start_local, e.end_local) for e in gap])
check("...and each says the day's hours and that it is closed in between",
      all("Drop-in hours this day: 9:00-11:30 a.m. and 1:00-2:30 p.m. It is closed in between."
          in e.description for e in gap), gap and gap[0].description)
tue = [e for e in ee if e.venue_name.startswith("West Scarborough") and e.start_local.startswith("2026-10-06")]
check("overlapping ranges (10-11 inside 10-noon) are one row, with no gap claimed",
      len(tue) == 1 and (tue[0].start_local[11:16], tue[0].end_local[11:16]) == ("10:00", "12:00")
      and "in between" not in tue[0].description, [(e.start_local, e.end_local) for e in tue])
check("EarlyON is free in the package's own words, and 0227 reads it so",
      ee and all(FREE_TAG.search(e.description) for e in ee))
check("kids, on the City's point, with the province and postal code",
      ee and all(e.category == "kids" and e.coords_exact and e.region == "ON" and e.postal_code for e in ee))
check("a bare 'www.' website becomes a link",
      [e.ticket_url for e in east if "Waldock" in (e.address or "")][0].startswith("https://www."))
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
spruce_url = {e.ticket_url for e in ee if e.venue_name.startswith("101 Spruce")}
check("NO EMAIL IS A LINK: the centre whose website is an address links the City's finder page instead",
      spruce_url == {EARLYON["finder_url"]} and st.get("website refused (not a web address)") == 1,
      (spruce_url, st))
synced = [to_row(e.as_record("2026-10-03T00:00:00Z"), "fixture-host") for e in ee + evs]
check("...and no synced row, EarlyON or drop-in, carries an email in its ticket link or its description",
      not [r["description"] for r in synced if EMAIL.search(r.get("description") or "")]
      and not [e.ticket_url for e in ee + evs if "@" in (e.ticket_url or "")])
check("no contact person, email or phone reaches a row",
      not [e for e in ee if "A Person" in e.description or "example.org" in e.description
           or re.search(r"\d{3}-\d{3}-\d{4}", e.description)])
check("the licence line closes every EarlyON row too",
      all(e.description.endswith(CFG["attribution"]) for e in ee))
check("identity is the centre, the day and the stretch's first clock",
      spruce and spruce[0].source_id == "13650|2026-10-07|09:00", spruce and spruce[0].source_id)

# ------------------------------------------------------------------ 5. HTTP
print()
print("the reader")


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Session:
    def __init__(self, answers):
        self.answers, self.calls, self.timeouts = list(answers), [], []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        self.timeouts.append(timeout)
        return self.answers.pop(0)


def ok(records, total):
    return Resp(200, {"success": True, "result": {"records": records, "total": total}})


naps = []
now = [0.0]
r = T.Reader(Session([Resp(403)]), sleep=naps.append, clock=lambda: now[0])
try:
    r.call({})
    check("a 403 is a refusal", False)
except T.Refused:
    check("a 403 is a refusal, raised at once and never asked again", r.requests == 1 and not naps)
except Exception as exc:                                          # noqa: BLE001
    check("a 403 is a refusal (Refused), not a generic failure", False, repr(exc))
sess = Session([Resp(403), ok([], 0)])
r = T.Reader(sess, sleep=naps.append, clock=lambda: now[0])
for _ in range(2):
    try:
        r.call({})
    except T.Refused:
        pass
check("...and the host is asked NOTHING more this run: both sources live on it, and a 429 means stop",
      len(sess.calls) == 1 and r.refused, sess.calls)
sess = Session([Resp(503), ok([], 0)])
r = T.Reader(sess, sleep=naps.append, clock=lambda: now[0])
check("a 503 is retried", r.call({}) == {"records": [], "total": 0} and r.requests == 2)
check("each request waits at most 30 s, so the worst call (3 tries + 2 + 4 s backoff) is 96 s, not 186",
      sess.timeouts and max(sess.timeouts) <= 30
      and T.Reader(None).tries * T.REQUEST_TIMEOUT_S + 2 + 4 <= 96, sess.timeouts)
sess = Session([ok([], 0)])
clock = [100.0]
r = T.Reader(sess, sleep=lambda s: None, clock=lambda: clock[0], deadline=99.0)
try:
    r.call({})
    check("past the run deadline no request starts", False, sess.calls)
except T.OutOfTime:
    check("past the run deadline no request starts (OutOfTime, 0 requests)", not sess.calls and r.requests == 0)
sess = Session([ok([{"_id": 1}, {"_id": 2}], 3), ok([{"_id": 3}], 3)])
naps.clear()
r = T.Reader(sess, sleep=naps.append, clock=lambda: now[0])
got = r.resource("rid", limit=2)
check("pages by offset to the end, in _id order",
      [g["_id"] for g in got] == [1, 2, 3] and [c["offset"] for c in sess.calls] == [0, 2]
      and all(c["sort"] == "_id asc" for c in sess.calls), sess.calls)
check("and waits >= 1.1 s between requests to the host", naps and min(naps) >= 1.1, naps)
sess = Session([ok([{"_id": 1}, {"_id": 2}], 4), ok([{"_id": 3}], 5),
                ok([{"_id": 1}, {"_id": 2}], 5), ok([{"_id": 3}, {"_id": 4}], 5), ok([{"_id": 5}], 5)])
got = T.Reader(sess, sleep=lambda s: None, clock=lambda: now[0]).resource("rid", limit=2)
check("a table rebuilt mid-read (total moved) is read again from the start",
      [g["_id"] for g in got] == [1, 2, 3, 4, 5], [g["_id"] for g in got])

# ---------------------------------------------------------- 6. the config
print()
print("the shipped config")
uuid = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
check("every resource id is a CKAN uuid",
      all(uuid.match(v) for v in DROPIN["resources"].values()) and uuid.match(EARLYON["resource"]))
check("every price rule has a tier, a sentence and its evidence",
      all(r.get("tier") in ("free", "fee", "may") and r.get("text") and r.get("evidence")
          for r in DROPIN["price_rules"]))
check("only a free rule may say free, and every free rule says it in words 0227 reads",
      all(bool(FREE_TAG.search(r["text"])) == (r["tier"] == "free")
          and (r["tier"] == "free" or "free" not in r["text"].lower()) for r in DROPIN["price_rules"]))
check("the last price rule is the catch-all", not ({"free_centre", "section_prefix", "title_prefix",
                                                    "title_contains"} & set(DROPIN["price_rules"][-1])))
check("38 Free Centres, each with its own Location ID (and the page's name and street as provenance)",
      len(DROPIN["free_centres"]) == 38
      and len({e["location_id"] for e in DROPIN["free_centres"]}) == 38
      and all(isinstance(e["location_id"], int) and e["name"] and e["number"] and e["street"]
              for e in DROPIN["free_centres"]))
eys = [r for r in DROPIN["price_rules"] if r["tier"] == "free" and any(
    p.startswith("EYS") for p in r.get("section_prefix") or ())]
check("the free EYS rule names the arts, hobbies and interest section only - never EYS - Sports",
      eys and all(not T._starts("EYS - Sports Drop-In", r["section_prefix"]) for r in eys), eys)
shapes = DROPIN["exclude"].get("facility_use_shapes") or []
check("every facility shape has title prefixes and a length of at least six hours",
      shapes and all(r.get("title_prefix") and float(r.get("min_hours", 0)) >= 6 for r in shapes), shapes)
keys = list(DROPIN["category_by_section_prefix"].values()) + list(DROPIN["category_by_title_prefix"].values())
check("every configured category is a real lens key", all(k in VALID_CATEGORIES for k in keys + [EARLYON["category"]]))
days = EARLYON["skip_dates"]
check("skip_dates are real dates, in order, a year deep",
      days == sorted(days) and all(T.date.fromisoformat(d) for d in days) and days[-1] >= "2027-10-01", days)
check("EarlyON reads named fields, and none of them is a person",
      EARLYON["fields"] and not [f for f in EARLYON["fields"] if re.search(r"contact|email|consultant|phone", f)])
check("the declines are recorded with reasons",
      all(isinstance(v, str) and len(v) > 20 for v in CFG["_not_included"].values()))

# ------------------------------------------------------------- 7. the run
print()
print("the run")
import contextlib  # noqa: E402
import io  # noqa: E402

RID = {DROPIN["resources"]["sessions"]: FIXTURE["sessions"], DROPIN["resources"]["locations"]: FIXTURE["locations"],
       DROPIN["resources"]["points"]: FIXTURE["points"], EARLYON["resource"]: FIXTURE["earlyon"]}


class FakeCkan:
    """The datastore, served from FIXTURE. `answer(rid)` may return a status
    code or raise, to play a refusal or a cancelled step."""

    def __init__(self, answer=None):
        self.headers, self.calls, self.answer = {}, [], answer or (lambda rid: None)

    def get(self, url, params=None, timeout=None):
        rid = params["resource_id"]
        self.calls.append(rid)
        status = self.answer(rid)
        if status:
            return Resp(status)
        rows = RID[rid]
        o, n = int(params.get("offset", 0)), int(params.get("limit", T.PAGE_LIMIT))
        return ok(rows[o:o + n], len(rows))


def run(fake, *extra):
    """main() against a fake CKAN, unpaced, into a fresh store. Returns
    (exit code or the exception, stdout, store path)."""
    real_session, real_reader = T.requests.Session, T.Reader
    T.requests.Session = lambda: fake
    T.Reader = lambda *a, **k: real_reader(*a, sleep=lambda s: None, **k)
    out = io.StringIO()
    path = os.path.join(tmpdir, f"store{len(os.listdir(tmpdir))}.json")
    try:
        with contextlib.redirect_stdout(out):
            got = T.main(["--config", os.path.join(HERE, "toronto_rec_sources.json"), "--store", path, *extra])
    except BaseException as exc:                                  # noqa: BLE001
        got = exc
    finally:
        T.requests.Session, T.Reader = real_session, real_reader
    return got, out.getvalue(), path


def stored(path):
    return EventStore(path).records if os.path.exists(path) else {}


with tempfile.TemporaryDirectory() as tmpdir:
    code, out, path = run(FakeCkan())
    n = len(stored(path))
    check("a whole run writes both sources and exits 0",
          code == 0 and n == len(evs) + len(T.earlyon_events(FIXTURE["earlyon"], EARLYON, CFG, TZ, TODAY, {})),
          (code, n, out[-300:]))
    check("the summary says added / updated / rekeyed, not '+N rows' for rows that were only updated",
          re.search(r"added \d+, updated 0, merged 0, rekeyed 0", out) and "done: +" not in out, out[-300:])

    def cancel_earlyon(rid):
        if rid == EARLYON["resource"]:
            raise KeyboardInterrupt("timeout-minutes cancelled the step")   # what a cancelled step looks like
    code, out, path = run(FakeCkan(cancel_earlyon))
    check("A STEP CANCELLED DURING EARLYON KEEPS THE DROP-INS: the store is saved after every source",
          isinstance(code, KeyboardInterrupt) and len(stored(path)) == len(evs), (repr(code), len(stored(path))))

    fake = FakeCkan(lambda rid: 403 if rid == DROPIN["resources"]["sessions"] else None)
    code, out, path = run(fake)
    check("a refusal on the first source means the second (same host) is never asked",
          code == 0 and fake.calls == [DROPIN["resources"]["sessions"]] and "NOT READ" in out, (fake.calls, out))

    fake = FakeCkan()
    code, out, path = run(fake, "--max-minutes", "0.0000001")
    check("--max-minutes: past the deadline no request starts, the run exits 0 and still saves",
          code == 0 and not fake.calls and "STOPPED" in out and os.path.exists(path), (fake.calls, out))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
