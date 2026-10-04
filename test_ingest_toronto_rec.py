#!/usr/bin/env python3
"""
test_ingest_toronto_rec.py - Toronto's drop-in schedule and EarlyON hours, and
the ways each is not what it looks like.

Every session, location, point and centre below was read live from the City's
CKAN datastore on 2026-10-03 (trimmed to the columns the adapter reads). The
expensive cases are the quiet ones: three lane swims that merge into one row
and keep one time, Pickleball for 60+ folded into Pickleball for 19+, two
EarlyON centres with one name, and a price sentence that says "free" where the
City charges.

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
from mapsee_supabase_sync import derive_categories, _cap_prose  # noqa: E402

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

# ../mapsee migration 0227 reads `offer:free` from a row's own text. This is its
# English core, copied from ../mapsee/tools/measure_deals.py (FREE), so the
# wording contract is tested against the reader that enforces it.
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
  {"Location ID": 3502, "Course Title": "AMPED Art of DJing", "Section": "EYS - Arts, Hobbies and Interest Drop-In", "Age Min": "13", "Age Max": "24", "Start Hour": 17, "Start Minute": 0, "End Hour": 20, "End Min": 0, "First Date": "2026-10-06", "Last Date": "2026-10-06"}],
 "locations": [
  {"Location ID": 345, "Location Name": "Riverdale Farm", "Location Type": "Other", "Street No": "201", "Street No Suffix": "None", "Street Name": "Winchester", "Street Type": "St", "Street Direction": "None", "Postal Code": "None"},
  {"Location ID": 357, "Location Name": "Humber Community Pool", "Location Type": "None", "Street No": "205", "Street No Suffix": "None", "Street Name": "Humber College", "Street Type": "Blvd", "Street Direction": "None", "Postal Code": "M9W 5L7"},
  {"Location ID": 523, "Location Name": "Agincourt Community Recreation Centre", "Location Type": "crc", "Street No": "31", "Street No Suffix": "None", "Street Name": "Glen Watford", "Street Type": "Dr", "Street Direction": "None", "Postal Code": "M1S 2B7"},
  {"Location ID": 600, "Location Name": "Cedarbrook Community Centre", "Location Type": "crc", "Street No": "91", "Street No Suffix": "None", "Street Name": "Eastpark", "Street Type": "Blvd", "Street Direction": "None", "Postal Code": "M1H 1C6"},
  {"Location ID": 2791, "Location Name": "Parkway Forest Community Centre", "Location Type": "crc", "Street No": "55", "Street No Suffix": "None", "Street Name": "Forest Manor", "Street Type": "Rd", "Street Direction": "None", "Postal Code": "M2J 0C2"},
  {"Location ID": 3502, "Location Name": "Regent Park Community Centre", "Location Type": "crc", "Street No": "402", "Street No Suffix": "None", "Street Name": "Shuter", "Street Type": "St", "Street Direction": "None", "Postal Code": "None"},
  {"Location ID": 3775, "Location Name": "Ethennonnhawahstihnen' Community Recreation Centre and Library", "Location Type": "crc", "Street No": "100", "Street No Suffix": "None", "Street Name": "Ethennonnhawahstihnen'", "Street Type": "Ln.", "Street Direction": "None", "Postal Code": "None"}],
 "points": [
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
  {"loc_id": 14380, "program_name": "1033 EarlyON Child and Family Centre", "agency": "West Neighbourhood House O/a St. Christopher House", "buildingName": null, "address": "1033 King St W", "full_address": "1033 King St W, Toronto, ON M6K 3N3", "lat": 43.640968279, "lng": -79.416392031, "website": "https://www.westnh.org/earlyon-program/", "dropinHours": null, "languages": "", "french_language_program": null, "indigenous_program": null},
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
check("Regent Park and Cedarbrook are Free Centres, matched on number + street",
      locs[3502] in free and locs[600] in free)
check("...and Agincourt is not", locs[523] not in free)
check("L'Amoreaux matches the DATA's spelling (Mcnicoll), not the page's (McNicholl)",
      {"Street No": "2000", "Street Name": "Mcnicoll", "Location Name": "x"} in free)

# ------------------------------------------------------------- 3. the drop-ins
print()
print("drop-in rows")
evs, stats = dropin()
check("a weight room and a running track are facilities, not programmes",
      stats.get("facility use, not a programme") == 2, stats)
check("Reserve a Spot needs a booking and is refused - its twin drop-in is kept",
      stats.get("reservation required (Reserve a Spot)") == 2
      and by_name(evs, "Aquatic Fitness: Shallow, ages 13+", "2026-10-05"), stats)
check("Humber College Swim is one college's pool time", stats.get("restricted audience") == 1, stats)
check("a session before today is past", stats.get("past") == 1, stats)
check("Riverdale Farm has no point: counted unplaceable, never guessed",
      stats.get("unplaceable (no point)") == 1 and not [e for e in evs if "Riverdale" in (e.venue_name or "")],
      stats)
lane = by_name(evs, "Lane Swim, ages 7+", "2026-10-05")
check("THREE LANE SWIMS ARE ONE ROW, not three rows the store would merge into one time",
      len(lane) == 1, [e.start_local for e in lane])
if lane:
    e = lane[0]
    check("...opening with the first session and closing with the last",
          (e.start_local, e.end_local) == ("2026-10-05T07:30:00", "2026-10-05T21:00:00"),
          (e.start_local, e.end_local))
    check("...and saying every time, and that it does not run in between",
          "Runs 3 times this day: 7:30-8:45 a.m., 11:45 a.m.-12:45 p.m. and 8:00-9:00 p.m. "
          "It does not run in between." in e.description, e.description)
    check("EDT in October: 07:30 local is 11:30Z", e.start_utc == "2026-10-05T11:30:00Z", e.start_utc)
nov = by_name(evs, "Lane Swim, ages 7+", "2026-11-02")
check("EST after 1 November: 07:30 local is 12:30Z",
      nov and nov[0].start_utc == "2026-11-02T12:30:00Z", [x.start_utc for x in nov])
pk19 = by_name(evs, "Pickleball, ages 19+", "2026-10-08")
pk60 = by_name(evs, "Pickleball, ages 60+", "2026-10-08")
check("PICKLEBALL FOR 60+ IS NOT PICKLEBALL FOR 19+: two rows, two fingerprints",
      len(pk19) == 1 and len(pk60) == 1 and pk19[0].fingerprint != pk60[0].fingerprint)
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
check("source_id is place + title + day, never the City's row number",
      lane and lane[0].source_id == "3775|laneswimages7|2026-10-05", lane and lane[0].source_id)
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

rows = [dict(c) for c in FIXTURE["earlyon"]]
rows[0].update(contact_fullname="A Person", email="a.person@example.org")   # synthetic, must not leak
st = {}
ee = T.earlyon_events(rows, EARLYON, CFG, TZ, TODAY, st)
check("a centre with no drop-in hours writes nothing and is counted",
      st.get("centre with no drop-in hours") == 1, st)
spruce = [e for e in ee if e.venue_name.startswith("101 Spruce")]
check("a Wednesday-only centre has two Wednesdays in 14 days (7th and 14th)",
      [e.start_local for e in spruce] == ["2026-10-07T09:00:00", "2026-10-14T09:00:00"],
      [e.start_local for e in spruce])
check("Thanksgiving Monday is skipped, and counted",
      not [e for e in ee if e.start_local.startswith("2026-10-12")] and st.get("holiday skipped") == 3, st)
east = [e for e in ee if e.venue_name.startswith("Eastview") and e.start_local.startswith("2026-10-05")]
check("TWO CENTRES CALLED 'Eastview EarlyON ...' ARE TWO ROWS, not one merged into the other",
      len(east) == 2 and len({e.fingerprint for e in east}) == 2, [e.address for e in east])
gap = [e for e in ee if e.venue_name.startswith("West Scarborough") and e.start_local.startswith("2026-10-08")]
check("a day with a closed gap says so",
      gap and "It is closed in between." in gap[0].description
      and (gap[0].start_local, gap[0].end_local) == ("2026-10-08T09:00:00", "2026-10-08T14:30:00"),
      gap and gap[0].description)
check("EarlyON is free in the package's own words, and 0227 reads it so",
      ee and all(FREE_TAG.search(e.description) for e in ee))
check("kids, on the City's point, with the province and postal code",
      ee and all(e.category == "kids" and e.coords_exact and e.region == "ON" and e.postal_code for e in ee))
check("a bare 'www.' website becomes a link",
      [e.ticket_url for e in east if "Waldock" in (e.address or "")][0].startswith("https://www."))
check("no contact person, email or phone reaches a row",
      not [e for e in ee if "A Person" in e.description or "example.org" in e.description
           or re.search(r"\d{3}-\d{3}-\d{4}", e.description)])
check("the licence line closes every EarlyON row too",
      all(e.description.endswith(CFG["attribution"]) for e in ee))
check("identity is the centre and the day", spruce and spruce[0].source_id == "13650|2026-10-07",
      spruce and spruce[0].source_id)

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
        self.answers, self.calls = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
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
r = T.Reader(Session([Resp(503), ok([], 0)]), sleep=naps.append, clock=lambda: now[0])
check("a 503 is retried", r.call({}) == {"records": [], "total": 0} and r.requests == 2)
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
check("38 Free Centres, each with a street number and a street",
      len(DROPIN["free_centres"]) == 38 and all(e["number"] and e["street"] for e in DROPIN["free_centres"]))
keys = list(DROPIN["category_by_section_prefix"].values()) + list(DROPIN["category_by_title_prefix"].values())
check("every configured category is a real lens key", all(k in VALID_CATEGORIES for k in keys + [EARLYON["category"]]))
days = EARLYON["skip_dates"]
check("skip_dates are real dates, in order, a year deep",
      days == sorted(days) and all(T.date.fromisoformat(d) for d in days) and days[-1] >= "2027-10-01", days)
check("EarlyON reads named fields, and none of them is a person",
      EARLYON["fields"] and not [f for f in EARLYON["fields"] if re.search(r"contact|email|consultant|phone", f)])
check("the declines are recorded with reasons",
      all(isinstance(v, str) and len(v) > 20 for v in CFG["_not_included"].values()))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
