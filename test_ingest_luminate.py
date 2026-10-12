"""Does the Luminate TeamRaiser adapter write only real, public, placeable walks?

Each request carries a charity's own published api_key. The owner switched
the adapter on 2026-10-11 (luminate_sources.json `_decision`; the charities
were not asked), so these checks pin that the LIVE config runs, records that
decision and how to take it back, and has no parked twin beside it to drift
from - and the rules it runs under.

The records are verbatim from the TeamRaiser answers of 2026-10-11 (ACS Making
Strides and Relay For Life, Walk to End Alzheimer's 2026, Komen), trimmed to
the fields the adapter reads. Each is a trap that pull held:

  * a clock that is noise: San Diego's 00:00:00.568 for an 8:00 AM walk;
  * December 31, the fiscal year-end placeholder (189 of 1,055 Relay records);
  * status 1 (unpublished) and 3 (closed: Lansing, 2026-10-24);
  * "Location TBD" with a street under it, "Students Only", a correctional
    facility, Second Life, "Community Fundraising Event Only";
  * a street that carries its own town, or a lot name before the number;
  * the Alzheimer's Association's pin in `area`, which places "Paducah AK" (and
    whose box says "AK" is a typo: the row carries no state rather than Alaska);
  * "Free to join: there is no registration fee." only where the charity's FAQ
    says so, worded so ../mapsee migration 0227's free predicate tags it
    (offers_0227) - the first draft's "Free to register." was tagged on 0 rows;
  * the run's own deadline, and a refusal that ends the host's reads.

No network: fake readers and literal records.
"""
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_luminate as L
from mapsee_supabase_sync import derive_categories

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


TODAY = date(2026, 10, 11)

# --------------------------------------------------------------------------- #
# fixtures: CRTeamraiserAPI getTeamraisersByInfo, 2026-10-11 18:52-18:55 UTC
# --------------------------------------------------------------------------- #
RECORDS_JSON = r'''{
 "alz_walk2026": [
  {
   "id": "19864",
   "name": "2026 Walk to End Alzheimer's - Central Oregon - Bend, OR",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-11T11:25:37.450-05:00",
   "location_name": "Riverbend Park",
   "street_address": "799 SW Columbia St.",
   "city": "Bend",
   "state": "OR",
   "zip": "97702",
   "area": "44.043368,-121.320267",
   "greeting_url": "https://act.alz.org/site/TR/Walk2026/OR-Oregon?pg=entry&fr_id=19864"
  },
  {
   "id": "19647",
   "name": "2026 Walk to End Alzheimer's - Purchase Area",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-17T07:58:56.926-05:00",
   "location_name": "Paducah",
   "street_address": "2801 Park Ave.",
   "city": "Paducah",
   "state": "AK",
   "zip": "42001",
   "area": "37.085231,-88.618363",
   "greeting_url": "https://act.alz.org/site/TR/Walk2026/KY-Kentucky?pg=entry&fr_id=19647"
  },
  {
   "id": "19903",
   "name": "2026 Walk to End Alzheimer's - Providence, RI",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-18T12:59:08.959-05:00",
   "location_name": "Roger Williams Park - Temple To Music",
   "street_address": "FC Green Memorial Blvd",
   "city": "Providence",
   "state": "RI",
   "zip": "02906",
   "area": "41.8239891,-71.4128343",
   "greeting_url": "https://act.alz.org/site/TR/Walk2026/RI-RhodeIsland?pg=entry&fr_id=19903"
  },
  {
   "id": "19906",
   "name": "2026 Walk to End Alzheimer's - Anderson, SC",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-12-31T06:37:56.780-06:00",
   "location_name": "AnMed Health North Campus",
   "street_address": "2000 E Greenville St",
   "city": "Anderson",
   "state": "SC",
   "zip": "29621",
   "area": "34.548338,-82.630874",
   "greeting_url": "https://act.alz.org/site/TR/Walk2026/SC-SouthCarolina?pg=entry&fr_id=19906"
  }
 ],
 "acs_strides_all": [
  {
   "id": "112394",
   "name": "Making Strides of San Diego",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-18T00:00:00.568-05:00",
   "location_name": "Snapdragon Stadium",
   "street_address": "2101 Stadium Way",
   "city": "San Diego",
   "state": "CA",
   "zip": "92108",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26WER?pg=entry&fr_id=112394"
  },
  {
   "id": "112478",
   "name": "Making Strides of St Louis",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-17T00:00:00.766-05:00",
   "location_name": "Location TBD",
   "street_address": "601 Clark Ave, St. Louis, MO 63102",
   "city": "St. Louis",
   "state": "MO",
   "zip": "63103",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26NOR?pg=entry&fr_id=112478"
  },
  {
   "id": "112418",
   "name": "Making Strides of Central Park",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-18T00:00:00.310-05:00",
   "location_name": "CENTRAL PARK - BANDSHELL",
   "street_address": "West 67th St and Central Park West",
   "city": "Manhattan",
   "state": "NY",
   "zip": "10001",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26NER?pg=entry&fr_id=112418"
  },
  {
   "id": "112469",
   "name": "Making Strides of Lansing",
   "status": "3",
   "accepting_registrations": "false",
   "event_date": "2026-10-24T08:45:56.618-05:00",
   "location_name": "Michigan Capitol Building",
   "street_address": "100 N Capitol Ave",
   "city": "Lansing",
   "state": "MI",
   "zip": "48933",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26NOR?pg=entry&fr_id=112469"
  },
  {
   "id": "112400",
   "name": "Making Strides of Second Life",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-31T00:00:00.319-05:00",
   "location_name": "Relay Sims Virtual World",
   "street_address": "Virtual Relay Track",
   "city": "Second Life",
   "state": "GA",
   "zip": "31302",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26VirtualEvents?pg=entry&fr_id=112400"
  },
  {
   "id": "112379",
   "name": "Making Strides of Atlanta Presented By Havertys Furniture",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-12-31T11:58:23.331-06:00",
   "location_name": "Atlantic Station Pinnacle Lot",
   "street_address": "Pinnacle Lot - 241 20th Street NW",
   "city": "Atlanta",
   "state": "GA",
   "zip": "30363",
   "area": "Midtown",
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26SER?pg=entry&fr_id=112379"
  },
  {
   "id": "112692",
   "name": "Sur Rosado 2",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-31T00:00:00.421-05:00",
   "location_name": "Region Sur",
   "street_address": "Urb Villa Grillasca 3a Bloque P",
   "city": "Yauco",
   "state": "PR",
   "zip": "00716",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26PRR?pg=entry&fr_id=112692"
  },
  {
   "id": "112459",
   "name": "Making Strides of Jersey City",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-11T00:00:00.313-05:00",
   "location_name": "Newport Riverwalk",
   "street_address": "2 Sixth Street, Jersey City, NJ (Thomas Gangemi Dr)",
   "city": "Jersey City",
   "state": "NJ",
   "zip": "07310",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/MakingStridesAgainstBreastCancer/MSABCCY26NER?pg=entry&fr_id=112459"
  }
 ],
 "acs_relay_all": [
  {
   "id": "113401",
   "name": "Relay For Life of Limon Correctional Facility",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-17T00:00:00.612-05:00",
   "location_name": "Limon Correctional Facility",
   "street_address": "49030 CO-71",
   "city": "Limon",
   "state": "CO",
   "zip": "80826",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/RelayForLife/RFLCY26SOR?pg=entry&fr_id=113401"
  },
  {
   "id": "113261",
   "name": "Relay For Life of Hillview Middle School Students Only",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-23T15:35:44.736-05:00",
   "location_name": "Hillview Middle School",
   "street_address": "1100 Elder Ave",
   "city": "Menlo Park",
   "state": "CA",
   "zip": "94025",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/RelayForLife/RFLCY26WER?pg=entry&fr_id=113261"
  },
  {
   "id": "112219",
   "name": "Relay For Life of South Central Kentucky",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-17T00:00:00.360-05:00",
   "location_name": "Bowling Green Ballpark",
   "street_address": "300 8th Ave",
   "city": "Bowling Green",
   "state": "KY",
   "zip": "42101",
   "area": null,
   "greeting_url": "https://secure.acsevents.org/site/TR/RelayForLife/RFLCY26SER?pg=entry&fr_id=112219"
  },
  {
   "id": "112290",
   "name": "Relay For Life Community Movement in Chowan County",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2027-03-31T17:24:05.571-05:00",
   "location_name": "Community Fundraising Event Only",
   "street_address": "Fundraising For Cancer Research",
   "city": "Edenton",
   "state": "NC",
   "zip": "27932",
   "area": "Fundraising For Cancer Research",
   "greeting_url": "https://secure.acsevents.org/site/TR/RelayForLife/RFLCY26SER?pg=entry&fr_id=112290"
  }
 ],
 "komen_rftc": [
  {
   "id": "11255",
   "name": "2026 Massachusetts MORE THAN PINK Walk | Susan G. Komen",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-17T09:51:07.156-05:00",
   "location_name": "UMass Boston - Campus Center Lawn",
   "street_address": "100 William T Morrissey Blvd",
   "city": "Boston",
   "state": "MA",
   "zip": "02125",
   "area": null,
   "greeting_url": "https://secure.info-komen.org/site/TR/RacefortheCure/MoreThanPinkWalk?pg=entry&fr_id=11255"
  },
  {
   "id": "11239",
   "name": "2026 Dallas, TX Race for the Cure | Susan G. Komen",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-17T12:42:32.453-05:00",
   "location_name": null,
   "street_address": "800 West Campbell",
   "city": "Richardson",
   "state": "TX",
   "zip": "75080",
   "area": null,
   "greeting_url": "https://secure.info-komen.org/site/TR/RacefortheCure/RaceforTheCure?pg=entry&fr_id=11239"
  },
  {
   "id": "11275",
   "name": "2026 Virtual MORE THAN PINK Walk | Susan G. Komen",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-10-24T15:36:44.146-05:00",
   "location_name": "Where you Are",
   "street_address": null,
   "city": "Cityname(3.a.3)",
   "state": null,
   "zip": null,
   "area": null,
   "greeting_url": "https://secure.info-komen.org/site/TR/RacefortheCure/MoreThanPinkWalk?pg=entry&fr_id=11275"
  },
  {
   "id": "11405",
   "name": "BB TEST - RFTC 2027 Blueprint Public Name",
   "status": "1",
   "accepting_registrations": "true",
   "event_date": "2027-10-01T08:02:50.166-05:00",
   "location_name": null,
   "street_address": null,
   "city": null,
   "state": "AL",
   "zip": null,
   "area": null,
   "greeting_url": "https://secure.info-komen.org/site/TR/RacefortheCure/General?pg=entry&fr_id=11405"
  },
  {
   "id": "11261",
   "name": "2026 San Diego, CA MORE THAN PINK Walk | Susan G. Komen",
   "status": "2",
   "accepting_registrations": "true",
   "event_date": "2026-11-01T10:23:48.681-06:00",
   "location_name": "Balboa Park",
   "street_address": "Sixth Ave &amp; El Prado",
   "city": "San Diego",
   "state": "CA",
   "zip": "92103",
   "area": null,
   "greeting_url": "https://secure.info-komen.org/site/TR/RacefortheCure/MoreThanPinkWalk?pg=entry&fr_id=11261"
  }
 ]
}'''

REC = json.loads(RECORDS_JSON)

# The config the job runs. Switched on by the owner on 2026-10-11; to switch it
# off, rename it to luminate_sources.json.pending-permission and flip the
# first checks below (test_ingest_parkrun.py has the same pattern).
LIVE = os.path.join(HERE, "luminate_sources.json")
TWIN = LIVE + ".pending-permission"
cfg = json.load(open(LIVE if os.path.exists(LIVE) else TWIN, encoding="utf-8"))
ALZ = next(s for s in cfg["sources"] if "alz.org" in s["api"])
STRIDES = next(s for s in cfg["sources"] if s["event_types"] == ["Making Strides Against Breast Cancer"])
RELAY = next(s for s in cfg["sources"] if s["event_types"] == ["Relay For Life"])
KOMEN = next(s for s in cfg["sources"] if "komen" in s["api"])
PH = cfg["placeholder_days"]


def run(src, recs):
    out = {}
    for r in recs:
        out[r["id"]] = L.to_event(r, src, TODAY, cfg["within_days"], PH)
    return out


# --------------------------------------------------------------------------- #
# switched on by the owner, 2026-10-11
# --------------------------------------------------------------------------- #
check("luminate_sources.json exists: the step runs", os.path.exists(LIVE))
check("and no parked .pending-permission twin sits beside it, to drift from the one that runs",
      not os.path.exists(TWIN))
decision = cfg.get("_decision") or ""
check("it records the owner's decision: who, when, that the charities were not asked, and the way back",
      bool(decision.strip()) and "owner" in decision and "2026-10-11" in decision
      and "NOT asked" in decision and ".pending-permission" in decision, decision[:200])
check("nothing in the live config still calls it parked",
      "PARKED" not in json.dumps({k: v for k, v in cfg.items() if k != "_decision"}))
wf = open(os.path.join(HERE, ".github", "workflows", "aggregate-events.yml"), encoding="utf-8").read()
check("the workflow step runs only when luminate_sources.json exists",
      "if [ -f luminate_sources.json ]; then\n            python mapsee_ingest_luminate.py --config "
      "luminate_sources.json" in wf)
step = wf[wf.index("id: ingest_luminate"):]
step = step[:step.index("\n      - name:")]
cap = re.search(r"timeout-minutes:\s*(\d+)", step)
mm = re.search(r"--max-minutes\s+(\d+(?:\.\d+)?)", step)
check("the step passes a deadline that leaves one 90 s request and a robots.txt read under its cap",
      cap and mm and float(mm.group(1)) + 1.5 + 0.25 < int(cap.group(1)), step)
check("four programmes, each on its charity's own TeamRaiser API",
      len(cfg["sources"]) == 4 and all(s["api"].endswith("/site/CRTeamraiserAPI") and s.get("api_key")
                                       for s in cfg["sources"]), [s.get("api") for s in cfg["sources"]])

# --------------------------------------------------------------------------- #
# the request is the one the charities' finders make
# --------------------------------------------------------------------------- #
check("the request URL is byte for byte the measured one",
      L.request_url(ALZ, "Walk 2026", 1) ==
      "https://act.alz.org/site/CRTeamraiserAPI?method=getTeamraisersByInfo&api_key=ALZADARDA&v=1.0"
      "&response_format=json&list_page_size=500&list_page_offset=1&name=%25%25%25&event_type=Walk%202026",
      L.request_url(ALZ, "Walk 2026", 1))
check("the Alzheimer's type is named for its year: this year's and next",
      L._types(ALZ, TODAY) == ["Walk 2026", "Walk 2027"], L._types(ALZ, TODAY))

# --------------------------------------------------------------------------- #
# Walk to End Alzheimer's
# --------------------------------------------------------------------------- #
alz = run(ALZ, REC["alz_walk2026"])
e, _ = alz["19864"]
check("Bend is written as an all-day row on its own date", e is not None and e.start_local == "2026-10-11",
      e and e.start_local)
check("the walk carries the Association's own pin from `area`", (e.latitude, e.longitude) == (44.043368, -121.320267),
      (e.latitude, e.longitude))
check("and is not marked exact: Census still replaces it when the street matches", not e.coords_exact)
check("Bend says it is free to join first (the Association's FAQ)",
      e.description.startswith("Free to join: there is no registration fee."), e.description[:40])
check("an Id is the host's: source_id carries act.alz.org", e.source_id == "act.alz.org:19864", e.source_id)
check("community, with fitness and outdoors", (e.category, e.categories) == ("community", ["fitness", "outdoors"]))
check("the link is the walk's entry page", e.ticket_url.startswith("https://act.alz.org/site/TR/Walk2026/"),
      e.ticket_url)
e, _ = alz["19903"]
check("Providence's 12:59:08.959 is noise: the row is the day", e.start_local == "2026-10-18", e.start_local)
check("a park road with no number is no street, but the pin still places it",
      e.address is None and e.latitude is not None, (e.address, e.latitude))
e, _ = alz["19647"]
check("'Paducah AK 42001' (a Kentucky ZIP) is placed by the pin, not lost", e is not None and e.latitude == 37.085231,
      e and e.latitude)
check("...and its pin, outside Alaska's box, drops the mistyped state rather than publish it",
      e is not None and e.region is None and e.city == "Paducah", e and (e.city, e.region))
check("a pin inside its own state's box keeps the state (Bend, OR)", alz["19864"][0].region == "OR")
gone = dict(REC["alz_walk2026"][0], event_date="2026-10-10T11:25:37.450-05:00")
check("a walk the day before today is not written", L.to_event(gone, ALZ, TODAY, 180, PH) == (None, "past"),
      L.to_event(gone, ALZ, TODAY, 180, PH))
far = dict(REC["alz_walk2026"][0], event_date="2027-04-10T11:25:37.450-05:00")
check("a walk 181 days out is beyond the 180-day window", L.to_event(far, ALZ, TODAY, 180, PH) == (None, "beyond the window"),
      L.to_event(far, ALZ, TODAY, 180, PH))
edge = dict(REC["alz_walk2026"][0], event_date="2027-04-09T11:25:37.450-05:00")
check("...while day 180 is inside it", L.to_event(edge, ALZ, TODAY, 180, PH)[0] is not None)
check("an Alzheimer's walk on December 31 is a placeholder", alz["19906"] == (None, "placeholder date"),
      alz["19906"])

# --------------------------------------------------------------------------- #
# Making Strides
# --------------------------------------------------------------------------- #
st = run(STRIDES, REC["acs_strides_all"])
e, _ = st["112394"]
check("San Diego's 00:00:00.568 becomes the day 2026-10-18", e.start_local == "2026-10-18", e.start_local)
check("Making Strides says nothing about price (ACS fees load by script)",
      "free" not in e.description.lower(), e.description)
check("no pin of its own: placed by Census from the street",
      (e.latitude, e.address, e.city, e.region, e.postal_code) == (None, "2101 Stadium Way", "San Diego", "CA", "92108"),
      (e.latitude, e.address, e.city, e.region, e.postal_code))
check("'Location TBD' over a street is not a venue yet", st["112478"] == (None, "location TBD"), st["112478"])
check("an intersection is no street Census can read", st["112418"] == (None, "no street address"), st["112418"])
check("status 3 is closed (Lansing, registrations off)", st["112469"] == (None, "not active"), st["112469"])
check("Second Life is not a place", st["112400"] == (None, "test, virtual or not public"), st["112400"])
check("Atlanta on December 31 waits for its date", st["112379"] == (None, "placeholder date"), st["112379"])
check("a name that is not the programme's is skipped", st["112692"] == (None, "another programme"), st["112692"])
e, _ = st["112459"]
check("'2 Sixth Street, Jersey City, NJ (Thomas Gangemi Dr)' is cut to its street", e.address == "2 Sixth Street",
      e.address)

# --------------------------------------------------------------------------- #
# Relay For Life
# --------------------------------------------------------------------------- #
rl = run(RELAY, REC["acs_relay_all"])
check("a relay inside a correctional facility is not public", rl["113401"] == (None, "test, virtual or not public"),
      rl["113401"])
check("'Students Only' is not public", rl["113261"] == (None, "test, virtual or not public"), rl["113261"])
check("'Community Fundraising Event Only' is a campaign, not a place",
      rl["112290"] == (None, "test, virtual or not public"), rl["112290"])
e, _ = rl["112219"]
check("Bowling Green's relay is written, fitness only (a relay is often a gym or a track)",
      e is not None and e.categories == ["fitness"], e and e.categories)

# --------------------------------------------------------------------------- #
# Komen
# --------------------------------------------------------------------------- #
ko = run(KOMEN, REC["komen_rftc"])
e, _ = ko["11255"]
check("Komen's ' | Susan G. Komen' comes off the name", e.name == "2026 Massachusetts MORE THAN PINK Walk", e.name)
check("a MORE THAN PINK Walk says it is free to join", e.description.startswith(L.FREE_LINE), e.description[:50])
e, _ = ko["11239"]
check("a Race for the Cure says nothing about price", e is not None and "free" not in e.description.lower(),
      e and e.description)
check("the virtual walk is not a place", ko["11275"] == (None, "test, virtual or not public"), ko["11275"])
check("status 1 is unpublished", ko["11405"] == (None, "not active"), ko["11405"])
check("'Sixth Ave &amp; El Prado' is no street Census can read", ko["11261"] == (None, "no street address"),
      ko["11261"])

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
for raw, city, want in [("Pinnacle Lot - 241 20th Street NW", "Atlanta", "241 20th Street NW"),
                        ("311 Polo Rd, College Station, TX, 77840.", "College Station", "311 Polo Rd"),
                        ("The Grove  514 Warhawk Way", "Monroe", "514 Warhawk Way"),
                        ("7800", "El Paso", None), ("Belmont Lake State Park", "North Babylon", None),
                        ("street address", "Cityname(3.a.3)", None)]:
    check(f"street {raw!r} -> {want!r}", L.clean_street(raw, city) == want, L.clean_street(raw, city))
for raw, want in [("35.28477798200414,-86.37056262613896", (35.28477798200414, -86.37056262613896)),
                  ("30.0267° N, -90.0652° W", (30.0267, -90.0652)), ("Midtown", None), ("0.0,0.0", None)]:
    check(f"area {raw!r} -> {want}", L.area_point(raw) == want, L.area_point(raw))

# --------------------------------------------------------------------------- #
# paging, and an error answer
# --------------------------------------------------------------------------- #
pages = {0: {"getTeamraisersResponse": {"totalNumberResults": "624", "teamraiser": [{"id": str(i)} for i in range(500)]}},
         1: {"getTeamraisersResponse": {"totalNumberResults": "624", "teamraiser": [{"id": str(i)} for i in range(500, 624)]}}}
asked = []


def reader(url):
    page = int(re.search(r"list_page_offset=(\d+)", url).group(1))
    asked.append(page)
    return pages[page]


rows, total, err = L.read_type(ALZ, "Walk 2026", reader, lambda: None)
check("624 records in two pages of 500, and no third request", (len(rows), total, asked) == (624, 624, [0, 1]),
      (len(rows), total, asked))
rows, total, err = L.read_type(ALZ, "Walk 2027", lambda u: {"errorResponse": {"code": "1", "message": "x"}},
                               lambda: None)
check("an errorResponse is reported, not raised", rows == [] and err and "errorResponse" in err, err)
one = {"getTeamraisersResponse": {"totalNumberResults": "1", "teamraiser": REC["komen_rftc"][0]}}
rows, total, err = L.read_type(KOMEN, "Race for the Cure", lambda u: one, lambda: None)
check("a single record arrives as an object, not a list", len(rows) == 1 and total == 1, (len(rows), total))

got = []
rep = L.ingest_source(ALZ, lambda u: {"getTeamraisersResponse": {"totalNumberResults": "4",
                                                                  "teamraiser": REC["alz_walk2026"]}}
                      if "Walk%202026" in u else {"getTeamraisersResponse": {"totalNumberResults": "0"}},
                      lambda: None, TODAY, cfg["within_days"], PH, got.append)
check("a whole programme: 3 walks written, the placeholder skipped, all 3 on the Association's pins",
      (rep["kept"], rep["placed_by_area"], rep["skipped"]) == (3, 3, {"placeholder date": 1}), rep)
check("the sync keeps them on community",
      {derive_categories(e.as_record("x"))[0] for e in got} == {"community"})

# --------------------------------------------------------------------------- #
# a refusal ends the host's reads; the deadline ends the run and keeps what was read
# --------------------------------------------------------------------------- #
asked = []


def refuse(url):
    asked.append(url)
    raise L.Refused("HTTP 403")


try:
    L.ingest_source(ALZ, refuse, lambda: None, TODAY, cfg["within_days"], PH, lambda e: None)
    raised = False
except L.Refused:
    raised = True
check("a 403 on 'Walk 2026' is raised, and 'Walk 2027' is never asked of that host",
      raised and len(asked) == 1 and "Walk%202026" in asked[0], asked)
got, asked = [], []


def one_page_then_deadline(url):
    asked.append(url)
    if "list_page_offset=0" in url:
        return {"getTeamraisersResponse": {"totalNumberResults": "900", "teamraiser": REC["alz_walk2026"]}}
    raise L.Stop("run deadline reached")


try:
    L.ingest_source(ALZ, one_page_then_deadline, lambda: None, TODAY, cfg["within_days"], PH, got.append)
    raised = False
except L.Stop:
    raised = True
check("the deadline on page 2 ends the run after page 1's walks are written",
      raised and len(got) == 3 and len(asked) == 2, (raised, len(got), len(asked)))


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Resp:
    def __init__(self, code, text):
        self.status_code, self.text, self.content, self.url = code, text, text.encode(), ""


class Hosts:
    """The three hosts as a run meets them: robots.txt allows, ACS refuses its
    API, and each Alzheimer's page costs 61 s of a 1-minute deadline."""

    def __init__(self, clock):
        self.headers, self.calls, self.clock = {}, [], clock

    def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return Resp(200, "User-agent: *\nAllow: /\n")
        if "acsevents" in url:
            return Resp(403, "Forbidden")
        self.clock.t += 61
        return Resp(200, json.dumps({"getTeamraisersResponse": {"totalNumberResults": "900",
                                                                "teamraiser": REC["alz_walk2026"]}}))


import tempfile  # noqa: E402

import requests  # noqa: E402

clock, made = Clock(), []
with tempfile.TemporaryDirectory() as d:
    cpath, spath = os.path.join(d, "lum.json"), os.path.join(d, "store.json")
    json.dump(dict(cfg, pause=0, sources=[STRIDES, RELAY, ALZ]), open(cpath, "w"))
    saved = (requests.Session, L.time.monotonic, L.time.sleep)
    requests.Session = lambda: made.append(Hosts(clock)) or made[-1]
    L.time.monotonic, L.time.sleep = clock, (lambda s: None)
    os.environ["MAPSEE_TODAY"] = "20261011"
    try:
        L.main(["--config", cpath, "--store", spath, "--max-minutes", "1"])
    finally:
        requests.Session, L.time.monotonic, L.time.sleep = saved
        os.environ.pop("MAPSEE_TODAY", None)
    calls = made[0].calls
    api = [u for u in calls if "CRTeamraiserAPI" in u]
    check("ACS refuses Making Strides with a 403, and Relay For Life is not asked of the same host",
          sum("acsevents" in u for u in api) == 1 and not any("Relay" in u for u in api), api)
    check("no request starts after the deadline: Walk 2026 page 1 only, at 61 s of 60",
          [u for u in api if "alz" in u] == [L.request_url(ALZ, "Walk 2026", 0)], [u for u in api if "alz" in u])
    stored = json.load(open(spath, encoding="utf-8"))["events"] if os.path.exists(spath) else []
    check("...and the walks read before it are saved", len(stored) == 3, len(stored))

# --------------------------------------------------------------------------- #
# the free line is a contract with ../mapsee migration 0227's offer tagger,
# judged on the row the sync stores (offers_0227 is that predicate, vendored)
# --------------------------------------------------------------------------- #
import offers_0227 as O  # noqa: E402
from mapsee_ingest import EventStore  # noqa: E402
from mapsee_supabase_sync import to_row  # noqa: E402

drift = O.twin_drift()
if drift is None:
    print("skip the vendored 0227 patterns are not compared: ../mapsee is not checked out here")
else:
    check("offers_0227's patterns equal ../mapsee/tools/measure_deals.py's", drift == [], drift)


def stored_tags(e):
    with tempfile.TemporaryDirectory() as d:
        st = EventStore(os.path.join(d, "s.json"))
        st.upsert(e)
        rec = next(iter(st.records.values()))
    row = to_row(rec, "00000000-0000-0000-0000-000000000000")
    return O.classify({"title": row["title"], "description": row["description"]})[0]


free_rows = [alz["19864"][0], alz["19903"][0], ko["11255"][0]]
silent = [st["112394"][0], st["112459"][0], rl["112219"][0], ko["11239"][0]]
check("the Alzheimer's walks and the MORE THAN PINK Walk ARE tagged free by 0227",
      all("free" in stored_tags(e) for e in free_rows), [stored_tags(e) for e in free_rows])
check("Making Strides, Relay and Race for the Cure, which say nothing about price, are NOT tagged free",
      not any("free" in stored_tags(e) for e in silent), [stored_tags(e) for e in silent])
check("the old 'Free to register.' was never tagged free: the wording is the contract",
      "free" not in O.classify({"title": "2026 Walk to End Alzheimer's - Bend, OR",
                                "description": "Free to register."})[0])
check("no row is tagged a discount, an intro offer, a sale or pay-what-you-can",
      not any(stored_tags(e) & {"discount", "intro", "sale", "pwyw", "happyhr"} for e in free_rows + silent))
# Luminate writes no walker fee: no charity's fee is in the record, so no row
# carries one (the AVA and RallyBound tests hold the fee and no-price cases too).

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all Luminate checks passed")
