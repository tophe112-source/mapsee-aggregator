#!/usr/bin/env python3
"""Offline gate for mapsee_ingest_facility_hours.py. One printed line per case;
exits non-zero on any failure. MAPSEE_TODAY is fixed, nothing touches the network.

Every case pins a behaviour that went wrong, or would, on the live data of
2026-10-05: NYC Aging's am/pm-less closing times, Seattle's two inconsistently
labelled seasons and its DAY_ flags that contradict the hours (the cell wins:
Garfield is open on Saturday), free text written for two buildings at once,
Late Night on Christmas or from stale open data, a second dot for a centre
OpenStreetMap already lists, a refused centre whose old row would roll on for
ever, the phone the product hides, and the OSM phrase a retirement keys on.
"""
import json
import os
import re
import sys
from datetime import date

os.environ["MAPSEE_TODAY"] = "20261005"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_facility_hours as F  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' :: ' + str(detail)}")
    if not cond:
        FAILED.append(name)


H = lambda h, m=0: h * 60 + m  # noqa: E731

# --- NYC Aging: 12-hour clocks with no am/pm ---------------------------------
check("nyc 08:00-04:00 is 8 AM-4 PM", F.nyc_pair("08:00", "04:00") == (H(8), H(16)))
check("nyc 11:00-08:00 is 11 AM-8 PM", F.nyc_pair("11:00", "08:00") == (H(11), H(20)))
check("nyc 09:00-01:00 is 9 AM-1 PM", F.nyc_pair("09:00", "01:00") == (H(9), H(13)))
check("nyc '8 :00'-'3 :00' is read", F.nyc_pair("8 :00", "3 :00") == (H(8), H(15)))
check("nyc 00:00-00:00 is a closed day", F.nyc_pair("00:00", "00:00") == "closed")
check("nyc blanks and ':' are a closed day", F.nyc_pair(None, None) == "closed" and F.nyc_pair(":", ":") == "closed")
check("nyc 05:00-05:00 is refused, not read as 5 AM-5 PM", F.nyc_pair("05:00", "05:00") is None)
check("nyc ':30' is refused", F.nyc_pair(":30", "03:30") is None)

# --- one range ---------------------------------------------------------------
R = F.parse_range
check("range 9am-2pm", R("9am-2pm") == (H(9), H(14)))
check("range 9:00 AM - 7:00 PM", R("9:00 AM - 7:00 PM") == (H(9), H(19)))
check("range 715am-645pm (Yesler) reads 7:15", R("715am-645pm") == (H(7, 15), H(18, 45)))
check("range 2-9pm lends PM to the start", R("2-9pm") == (H(14), H(21)))
check("range 12:30-8pm starts at half past noon", R("12:30-8pm") == (H(12, 30), H(20)))
check("range 10-2pm keeps the start in the morning", R("10-2pm") == (H(10), H(14)))
check("range '3 PM – 12 AM' closes at midnight", R("3 PM – 12 AM") == (H(15), H(24)))
check("range bare 10-9 is 10 AM-9 PM", R("10-9") == (H(10), H(21)))
check("range bare 1-5 is refused (which half of the day?)", R("1-5") is None)
check("range 8:30 a.m. to 4:30 p.m.", R("8:30 a.m. to 4:30 p.m.") == (H(8, 30), H(16, 30)))
check("range 8am-45pm (Montlake summer) is refused", R("8am-45pm") is None)
check("range past midnight is refused", R("7pm-1am") is None)

# --- free text ---------------------------------------------------------------
W = F.parse_week_text
d, why = W("M-TH 10-9; FRI 10-6; SA 9-7")
check("text Phoenix bare numbers", why is None and d[0] == [(H(10), H(21))] and d[4] == [(H(10), H(18))]
      and d[5] == [(H(9), H(19))] and 6 not in d, (d, why))
d, why = W("M, T, Th 3-7 PM ; W 2-7 PM; Fri 3-6 PM")
check("text day lists", why is None and sorted(d) == [0, 1, 2, 3, 4] and d[2] == [(H(14), H(19))], (d, why))
d, why = W("Mon - Fri 8:30 a.m. to 4:30 p.m. Sat - Sun 9:00 a.m. to 4:00 p.m")
check("text Chicago two segments, no separator", why is None and sorted(d) == list(range(7))
      and d[6] == [(H(9), H(16))], (d, why))
d, why = W("M-TH 9AM-9PM; F-SAT 9AM-5PM; SUN Closed")
check("text 'SUN Closed' is a closed day", why is None and 6 not in d and d[5] == [(H(9), H(17))], (d, why))
check("text two buildings in one string is refused whole",
      W("East: M-F 8am-5PM ; West: M-TH 9AM-6PM; Sat: 10-6")[1] == "unreadable text")
check("text a broken range refuses the whole week", W("M-F 4PM-PM; SAT 10AM-3PM; SUN Closed")[0] is None)
check("text renovation is a closure", W("Temporarily closed for renovation") == (None, "closure"))
check("text CLOSED is a closure", W("CLOSED") == (None, "closure"))
d, why = W("Fri 7pm-12am  (no Saturday)")
check("text '(no Saturday)' is read, not left over", why is None and d == {4: [(H(19), H(24))]}, (d, why))
d, why = W("Fri 6:30pm-10:30pm and Sat 3:30pm-8:30pm")
check("text 'and' between segments", why is None and d == {4: [(H(18, 30), H(22, 30))], 5: [(H(15, 30), H(20, 30))]})
d, why = W("Everyday 7 AM - 6 PM")
check("text Everyday", why is None and sorted(d) == list(range(7)))

# --- Seattle's two seasons -------------------------------------------------
def sea(s1, c1, s2=None, c2=None, flags1=None):
    a = {"SCHEDULING_SEASON": s1, "SCHEDULING_SEASON2": s2}
    for i, day in enumerate(F.LONG_DAYS):
        a[f"HOURS_{day}"] = c1.get(i)
        a[f"DAY_{day}"] = (flags1 or {}).get(i, "Yes" if c1.get(i) else "No")
        if c2 is not None:
            a[f"HOURS_{day}2"] = c2.get(i)
            a[f"DAY_{day}2"] = "Yes" if c2.get(i) else "No"
    return a


summer = {i: "7:30 AM - 7:00 PM" for i in range(5)}
school = {i: "9am-8pm" for i in range(5)}
OCT, JUL = date(2026, 10, 5), date(2026, 7, 15)
WIN = ["06-20", "09-05"]
lab, days, late, _, why = F.seattle_week(sea("Summer", summer, "School Year", school), OCT, WIN)
check("season October picks School Year from the second set", why is None and lab == "School Year"
      and days[0] == [(H(9), H(20))], (lab, why))
lab, days, late, _, why = F.seattle_week(sea("Fall, Winter, Spring", school, "Summer Hours", summer), JUL, WIN)
check("season July picks Summer from the second set", why is None and lab == "Summer Hours"
      and days[0] == [(H(7, 30), H(19))], (lab, why))
check("season summer-only centre in October is refused, not given summer hours",
      F.seattle_week(sea("Summer", summer), OCT, WIN)[4] == "no hours for the current season")
check("season 'Childcare Only' is a closure",
      F.seattle_week(sea("Childcare Only ", {i: "CLOSED" for i in range(7)}), OCT, WIN)[4] == "closure")
lab, days, late, contra, why = F.seattle_week(sea("Year Round", {0: "9am-5pm", 5: "10am-5pm"},
                                                  flags1={0: "Yes", 5: "No"}), OCT, WIN)
check("season a 'No' flag beside parsable hours is overruled: Garfield's Saturday is open, and counted",
      why is None and days.get(5) == [(H(10), H(17))] and contra == 1, (days, contra))
LD = ["06-20", "labor-day"]
check("season summer ends on Labor Day (Sept 7 2026), school year from Sept 8",
      F.in_summer(date(2026, 9, 7), LD) and not F.in_summer(date(2026, 9, 8), LD)
      and F.in_summer(date(2027, 9, 6), LD) and not F.in_summer(date(2027, 9, 7), LD)
      and F.in_summer(JUL, LD) and not F.in_summer(OCT, LD))
lab, days, late, _, why = F.seattle_week(sea("Year Round", {4: "3:00 PM – 7 PM (Late Night 7 PM – 12 AM)",
                                                          5: "5 PM – 12 AM  (Late Night 7 PM – 12 AM)"}), OCT, WIN)
check("season Late Night in a cell joins the day and is a programme",
      why is None and days[4] == [(H(15), H(24))] and late == {4: (H(19), H(24)), 5: (H(19), H(24))}, (days, late))
check("recurring days write midnight as 23:59 for 0188",
      F.to_recurring(days) == {"4": [["15:00", "23:59"]], "5": [["17:00", "23:59"]]}, F.to_recurring(days))
check("hours text folds equal days", F.hours_text({0: [(H(9), H(17))], 1: [(H(9), H(17))], 2: [(H(9), H(17))],
                                                   5: [(H(10), H(14))]}) == "Mon-Wed 9 AM-5 PM; Sat 10 AM-2 PM")

# --- holidays ----------------------------------------------------------------
hol = F.us_holidays(2026, ["thanksgiving+1"]) | F.us_holidays(2027, ["thanksgiving+1"])
check("holidays: Christmas, New Year, Thanksgiving and the day after",
      {date(2026, 12, 25), date(2027, 1, 1), date(2026, 11, 26), date(2026, 11, 27)} <= hol)
check("holidays: Saturday July 4 2026 is observed on Friday the 3rd", date(2026, 7, 3) in hol)

# --- names and identity -------------------------------------------------------
NYC = {"key": "nyc", "fields": {"name": "programname"}, "keep_upper": ["JASA"],
       "name_rewrites": {"\\bOAC\\b": "Older Adult Center", "\\bCen$": "Center"}}
check("name: contract code dropped, words cased, initials kept",
      F.centre_name(NYC, {"programname": "CPC PROJECT OPEN DOOR NEIGHBORHOOD SENIOR CEN (C21)"})
      == "CPC Project Open Door Neighborhood Senior Center")
check("name: OAC written out", F.centre_name(NYC, {"programname": "JASA SUE GINSBURG OAC (W22)"})
      == "JASA Sue Ginsburg Older Adult Center")
check("phone: Seattle's 7 digits get the configured area code", F.tidy_phone("684-7524", "206") == "(206) 684-7524")
check("name: ordinals and street words are not left shouting",
      F.tidy_name("AMICO 59TH ST SENIOR CITIZEN CENTER") == "Amico 59th St Senior Citizen Center"
      and F.tidy_name("4554 NE 41ST ST") == "4554 NE 41st St"
      and F.tidy_name("4530 N 67TH Ave") == "4530 N 67th Ave",
      (F.tidy_name("AMICO 59TH ST SENIOR CITIZEN CENTER"), F.tidy_name("4554 NE 41ST ST")))

import mapsee_ingest_osm_amenities as A  # noqa: E402
el = {"type": "way", "id": 53589225, "center": {"lat": 47.6591627, "lon": -122.2779938},
      "tags": {"amenity": "community_centre", "name": "Laurelhurst Community Center"}}
osm_ev = A.to_event(el, {"name": "Seattle", "region": "WA", "country": "US"})
check("a 'same' centre is written under the OSM listing's own fingerprint",
      osm_ev is not None and F.osm_fingerprint("way/53589225") == osm_ev.fingerprint,
      (osm_ev and osm_ev.fingerprint, F.osm_fingerprint("way/53589225")))

# --- matching a city's centres to OSM -----------------------------------------
def E(ref, name, lat, lon, amenity="community_centre", **t):
    return {"ref": ref, "tags": dict(amenity=amenity, name=name, **t), "lat": lat, "lon": lon}


cs = [{"id": "1", "name": "Cudell Recreation Center", "kind": "general", "lat": 41.4790, "lon": -81.7530},
      {"id": "2", "name": "Cudell Fine Arts Center", "kind": "general", "lat": 41.4795, "lon": -81.7530},
      {"id": "3", "name": "Southwest Teen Life Center", "kind": "teen", "lat": 47.5278, "lon": -122.3690},
      {"id": "4", "name": "Marc Atkinson Recreation Center", "kind": "general", "lat": 33.4, "lon": -112.1},
      {"id": "5", "name": "Rochdale Village Senior Center", "kind": "senior", "lat": 40.6750, "lon": -73.7750},
      {"id": "6", "name": "Grand St Settlement Older Adult Center", "kind": "senior", "lat": 40.7160, "lon": -73.9840}]
els = [E("way/1", "Cudell Recreation Center", 41.47905, -81.75302),
       E("way/2", "Southwest Community Center", 47.52795, -122.36895),
       E("node/3", "Mark Atkinson Recreation Center", 33.40001, -112.1),
       E("node/4", "Queens Public Library at Rochdale Village", 40.67525, -73.7750, amenity="library"),
       E("node/5", "Grand St. Settlement Community Center", 40.71645, -73.9840)]
cs.append({"id": "7", "name": "Bensonhurst Senior Center", "kind": "senior", "lat": 40.6100, "lon": -73.9900,
           "address": "7802 Bay Pkwy"})
els.append(E("way/7", "Edith and Carl Marks Jewish Community House of Bensonhurst", 40.6104, -73.9900,
             **{"addr:housenumber": "7802", "addr:street": "Bay Parkway"}))
cs.append({"id": "8", "name": "Kew Gardens Older Adult Center", "kind": "senior", "lat": 40.7100, "lon": -73.8300,
           "address": "80-02 Kew Gardens Rd"})
els.append(E("way/8", "Queens Community Hall", 40.7104, -73.8300,
             **{"addr:housenumber": "80-10", "addr:street": "Kew Gardens Road"}))
got = F.propose_osm(cs, els)
check("osm: same name, same kind -> same", got.get("1", {}).get("mode") == "same", got.get("1"))
check("osm: one element is taken over by one centre only", "2" not in got, got.get("2"))
check("osm: a teen centre in a community centre is colocated, not a takeover",
      got.get("3", {}).get("mode") == "colocated", got.get("3"))
check("osm: Marc / Mark is one centre", got.get("4", {}).get("mode") == "same", got.get("4"))
check("osm: a library next door is never matched", "5" not in got, got.get("5"))
check("osm: an older adult centre 50 m from its settlement house is left alone", "6" not in got, got.get("6"))
check("osm: the same street address 44 m away is the same building (colocated)",
      got.get("7", {}).get("mode") == "colocated" and got["7"]["ref"] == "way/7", got.get("7"))
check("osm: a neighbouring house number 44 m away is not", "8" not in got, got.get("8"))
check("osm: addresses compare across abbreviations and ordinals",
      F.addr_key("415 E 93rd St") == F.addr_key("415 East 93rd Street") != ""
      and F.addr_key("58-20 Little Neck Pkwy") == F.addr_key("58-20 Little Neck Parkway")
      and F.addr_key("Bay Parkway") == "")

# --- a whole run on small fixtures ------------------------------------------------
def feature(attrs, lat, lon):
    return {"attributes": attrs, "geometry": {"x": lon, "y": lat}}


SEA_CC = {"features": [
    feature(dict(sea("Summer", summer, "School Year", school), PMAID="1", NAME="Laurelhurst Community Center",
                 ADDRESS="4554 NE 41st St", PHONE="684-7529", OPEN_="Year-round", OPERATIONALSTATUS="Open Regular Hours",
                 NOTES=None, LN_LOCATION="Yes", LN_HOURS="Fri & Sat, 7pm-12am",
                 WEBSITE_LINK="https://www.seattle.gov/parks/x"), 47.65917, -122.27786),
    feature(dict(sea("Summer", summer), PMAID="2", NAME="Northgate Community Center", OPEN_="Year-round"), 47.706, -122.32),
    feature(dict(sea("Childcare Only ", {i: "CLOSED" for i in range(7)}), PMAID="3", NAME="Alki Community Center",
                 OPEN_="Closed", NOTES="Closed - Childcare Site Only"), 47.5776, -122.407),
    feature(dict(sea("Year Round", {0: "9am-5pm"}), PMAID="4", NAME="Hutchinson Community Center",
                 OPEN_="Year-round"), 47.5148, -122.2598),
]}
NYC_ROWS = [
    {"dfta_id": "C0601", "programname": "ABSW OAC (C06)", "programaddress": "221 West 107th Street",
     "programcity": "NEW YORK", "programzipcode": "10025", "programphone": "212-749-8400",
     "sponsorname": "ASSOCIATION OF BLACK SOCIAL WORKERS INC", "latitude": "40.801704", "longitude": "-73.966593",
     **{f"{d}houropen": "08:00" for d in ("mon", "tue", "wed", "thu", "fri")},
     **{f"{d}hourclose": "04:00" for d in ("mon", "tue", "wed", "thu", "fri")},
     "sathouropen": "00:00", "sathourclose": "00:00", "sunhouropen": "00:00", "sunhourclose": "00:00"},
    {"dfta_id": "X1", "programname": "BROKEN OAC (C99)", "latitude": "40.7", "longitude": "-73.9",
     "monhouropen": "05:00", "monhourclose": "05:00"},
    {"dfta_id": "X2", "programname": "NO HOURS OAC (C98)", "latitude": "40.7", "longitude": "-73.9"},
]
CLE = {"features": [
    feature({"Facility_Name": "Glenville-James Hubbard Recreation Center", "Facility_Type": "Neighborhood Resource",
             "Operational_Status": "Open", "Hours": "M-F 11:30 AM - 8 PM, Sat 9:30 AM - 6 PM"}, 41.53, -81.62),
    feature({"Facility_Name": "Washington Golf Course", "Facility_Type": "Golf Course",
             "Operational_Status": "Open", "Hours": "Everyday 7 AM - 6 PM"}, 41.47, -81.65),
    feature({"Facility_Name": "Clark Recreation Center", "Facility_Type": "Neighborhood Resource",
             # the status says closed while the hours text still reads as open
             "Operational_Status": "Closed for Renovation", "Hours": "M-F 11:30 AM - 8 PM"}, 41.47, -81.70),
]}
CFG = json.loads(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "facility_hours_sources.json"), encoding="utf-8").read())
by_key = {s["key"]: s for s in CFG["sources"]}


class FakeReader:
    def __init__(self, payload):
        self.payload, self.requests = payload, 0

    def get(self, url, params=None, tries=3):
        self.requests += 1
        if params and "outFields" not in params and "$limit" not in params:
            return {"editingInfo": {"lastEditDate": 1790642518788}}       # 2026-09-29
        if "/api/views/" in url:
            return {"rowsUpdatedAt": 1777939200}                         # 2026-05-05
        return self.payload


class Store:
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev)


def run(key, payload, **over):
    src = dict(by_key[key], **over)
    st = Store()
    stats = F.ingest(st, FakeReader(payload), src, CFG, F.today())
    return st.rows, stats


LAU = {"ref": "way/53589225", "mode": "same", "lat": 47.6591627, "lon": -122.2779938}
NG = {"ref": "way/34151089", "mode": "same", "lat": 47.7054567, "lon": -122.3222908, "osm_name": "Northgate Community Center"}
GONE = {"ref": "node/927614329", "mode": "same", "lat": 47.5775704, "lon": -122.4070101,
        "osm_name": "Old Hall Community Center"}
rows, stats = run("seattle-cc", SEA_CC, late_night_days_ahead=90, osm={"1": LAU, "2": NG, "9": GONE})
standing = [r for r in rows if r.recurring_days]
dated = [r for r in rows if not r.recurring_days]
lau = [r for r in standing if r.name.startswith("Laurelhurst")][0]
ng = [r for r in standing if r.name.startswith("Northgate")][0]
alki = [r for r in rows if (r.name or "").startswith("Alki")]
gone = [r for r in standing if r.name.startswith("Old Hall")]
check("run seattle: refusals are counted by reason",
      stats.get("excluded: closure") == 1 and stats.get("excluded: no hours for the current season") == 1
      and stats.get("excluded: gone from the city's data (claimed OSM listing)") == 1, dict(stats))
check("run seattle: the matched centre takes over the OSM row at the OSM point",
      lau.fingerprint == F.osm_fingerprint("way/53589225") and lau.latitude == 47.6591627, lau.fingerprint)
check("run seattle: Laurelhurst in October carries its school-year hours",
      lau.recurring_days.get("0") == [["09:00", "20:00"]] and "School Year" in lau.description)
check("run seattle: a taken-over centre with no hours this season is still WRITTEN, under the OSM "
      "fingerprint, as a listing saying so (Northgate's summer hours must not roll on all winter)",
      ng.fingerprint == F.osm_fingerprint("way/34151089") and not ng.pin_only
      and ng.recurring_days == F.ALWAYS and "Opening times are not listed" in ng.description
      and "Opening hours (" not in ng.description and F.OSM_LISTING_LINE in ng.description, ng.description)
check("run seattle: a claimed OSM listing the city no longer carries is rewritten too, never left frozen",
      len(gone) == 1 and gone[0].fingerprint == F.osm_fingerprint("node/927614329")
      and "no longer lists" in gone[0].description and not gone[0].pin_only, [g.description for g in gone])
check("run seattle: every 'same' element in the config has a row this run",
      {F.osm_fingerprint(m["ref"]) for m in (LAU, NG, GONE)} <= {r.fingerprint for r in rows})
# A refused OWN row is not written: as pin_only scenery ../mapsee still opened
# a sheet and drew the all-week window as a 24-hour clock (review, 2026-10-05).
check("run seattle: an own row that is refused (Alki, closed) is not written at all",
      alki == [] and stats.get("refused, not written (own row)", 0) >= 1, [a.description for a in alki])
check("run seattle: no own row is ever pin_only scenery",
      not [r for r in rows if r.pin_only], [r.name for r in rows if r.pin_only])
check("run seattle: a taken-over listing keeps the marker lines the civic card renders",
      "\n☎ Phone: (206) 684-7529\n" in lau.description and lau.description.endswith(F.OSM_LISTING_LINE)
      and "community centre in Seattle:" in lau.description.split("\n")[0], lau.description)
check("run seattle: standing rows carry the community-centre glyph", all(r.icon == "🏘" for r in standing))
check("run seattle: the hours line comes before anything the sync could cut",
      lau.description.index("🕘 Opening hours") < 120 and lau.coords_exact is True)
check("run seattle: nothing says free", not any(re.search(r"\bfree\b", r.description, re.I) for r in rows))
fri = sorted(r.start_local for r in dated if date.fromisoformat(r.start_local[:10]).weekday() == 4)
check("run seattle: Late Night skips the day after Thanksgiving, Christmas and New Year's Day",
      not {"2026-11-27", "2026-12-25", "2027-01-01"} & {s[:10] for s in fri} and len(fri) == 10, fri)
check("run seattle: a Late Night evening ends at midnight on the next date",
      dated[0].start_local.endswith("T19:00:00") and dated[0].end_local.endswith("T00:00:00")
      and dated[0].end_local[:10] > dated[0].start_local[:10], (dated[0].start_local, dated[0].end_local))
check("run seattle: Late Night rows sit on the centre's dot", {(r.latitude, r.longitude) for r in dated}
      == {(lau.latitude, lau.longitude)})
check("run seattle: a dated row on an OSM point credits it without the phrase mapsee_retire_perday_osm "
      "selects by", all("© OpenStreetMap (ODbL)" in r.description and "OpenStreetMap contributors" not in r.description
                        for r in dated), dated[0].description)
check("run seattle: a dated row's phone is a sentence the product leaves in the text",
      all("Call (206) 684-7529." in r.description and "☎" not in r.description for r in dated))
again, _ = run("seattle-cc", {"features": list(reversed(SEA_CC["features"]))}, late_night_days_ahead=90,
               osm={"1": LAU, "2": NG, "9": GONE})
check("run seattle: identity is stable across runs and orders",
      sorted(r.fingerprint for r in again) == sorted(r.fingerprint for r in rows)
      and len({r.fingerprint for r in rows}) == len(rows))
rows21, _ = run("seattle-cc", SEA_CC, osm={"1": LAU})
last = max(r.start_local for r in rows21 if not r.recurring_days)
check("run seattle: the programme is projected only late_night_days_ahead (21) days, not 90",
      CFG.get("late_night_days_ahead") == 21 and "2026-10-24" <= last[:10] < "2026-10-26", last)
stale_feat = {"features": [feature(dict(SEA_CC["features"][0]["attributes"], PMAID="467",
                                        NAME="South Park Community Center",
                                        LN_HOURS="Fri 6:30pm-10:30pm and Sat 3:30pm-8:30pm"), 47.528, -122.324)]}
srows, sstats = run("seattle-cc", stale_feat, osm={})
check("run seattle: Late Night is skipped while the open data still says what contradicts the centre's page",
      [r for r in srows if r.recurring_days] and not [r for r in srows if not r.recurring_days]
      and sstats.get("Late Night skipped: open data contradicts the centre's page") == 1, dict(sstats))
fixed_feat = {"features": [feature(dict(stale_feat["features"][0]["attributes"], LN_HOURS="Fri & Sat, 7pm-12am"),
                                   47.528, -122.324)]}
frows, _ = run("seattle-cc", fixed_feat, osm={})
check("run seattle: ...and comes back on its own the day the city corrects it",
      len([r for r in frows if not r.recurring_days]) == 6, len(frows))
erows, estats = run("seattle-cc", {"features": []}, osm={"9": GONE})
check("run seattle: an empty answer writes nothing (no claimed listing is rewritten as gone)",
      not erows, dict(estats))

rows, stats = run("nyc-aging-oac", NYC_ROWS, osm={})
listed = [r for r in rows if not r.pin_only]
pins = [r for r in rows if r.pin_only]
check("run nyc: 8 AM-4 PM weekdays, weekend closed",
      len(listed) == 1 and listed[0].recurring_days == {str(i): [["08:00", "16:00"]] for i in range(5)},
      listed and listed[0].recurring_days)
check("run nyc: unreadable and missing hours are counted, not guessed, and not written",
      stats.get("excluded: unreadable time") == 1 and stats.get("excluded: no hours") == 1
      and not pins and stats.get("refused, not written (own row)") == 2, dict(stats))
check("run nyc: the name and the operator are tidied",
      listed[0].name == "ABSW Older Adult Center"
      and "Run by Association of Black Social Workers Inc." in listed[0].description, listed[0].name)
check("run nyc: an own row's phone and operator are sentences ../mapsee parseImportedDesc leaves visible",
      "\nCall (212) 749-8400.\n" in listed[0].description
      and not re.search(r"^(?:☎|Phone:|🏛\s*Run by:)", listed[0].description, re.M)
      and "OpenStreetMap" not in listed[0].description, listed[0].description)

rows, stats = run("cleveland-rec", CLE, osm={})
check("run cleveland: a golf course is never written; a renovation is counted and not written",
      len(rows) == 1 and stats.get("excluded: not a centre (Facility_Type)") == 1
      and stats.get("excluded: closure") == 1
      and "Clark Recreation Center" not in [r.name for r in rows], dict(stats))

rows, stats = run("chicago-dfss-senior", {"features": []}, max_age_days=5)   # data 6 days old
check("run: a source whose data is older than max_age_days is refused",
      not rows and any(k.startswith("source refused") for k in stats), dict(stats))


# --- reading ------------------------------------------------------------------------
class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body, text or json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Session:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.answers.pop(0)


class Robots:
    def __init__(self, verdict):
        self.verdict = verdict

    def check(self, url):
        return self.verdict


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def reader(answers, verdict=None, deadline=None):
    clk = Clock()
    rd = F.Reader(Session(answers), deadline=deadline, sleep=clk.sleep, clock=clk)
    rd.robots = Robots(verdict or {"status": "ok", "crawl_delay": None})
    return rd, clk


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:          # noqa: BLE001
        return False
    return False


U = "https://services.arcgis.com/x/FeatureServer/0/query"
rd, clk = reader([Resp(200, {"a": 1}), Resp(200, {"b": 2})], {"status": "ok", "crawl_delay": 3})
rd.get(U); rd.get(U)
check("reader: robots.txt's Crawl-delay spaces two requests to one host", clk.slept == [3.0], clk.slept)
for code in (401, 403, 429):
    rd, _ = reader([Resp(code, None, "no"), Resp(200, {})])
    first = raises(lambda: rd.get(U), F.Refused)
    check(f"reader: HTTP {code} is a refusal, never retried, and the host is refused for the run",
          first and raises(lambda: rd.get(U), F.Refused) and len(rd.session.calls) == 1, rd.session.calls)
rd, _ = reader([Resp(503, None, "busy"), Resp(200, {"ok": 1})])
check("reader: a 503 is retried and the answer returned", rd.get(U) == {"ok": 1} and rd.requests == 2)
rd, _ = reader([Resp(200, {"error": {"code": 498, "message": "Invalid token"}})])
check("reader: an ArcGIS token error inside a 200 is a refusal", raises(lambda: rd.get(U), F.Refused))
rd, _ = reader([Resp(200, {"error": {"code": 400, "message": "bad where"}})])
check("reader: any other ArcGIS error fails the source without calling it a refusal",
      raises(lambda: rd.get(U), RuntimeError) and not rd.refused)
rd, _ = reader([Resp(200, {})], {"status": "challenge"})
check("reader: a bot challenge on robots.txt is a refusal, and nothing is requested",
      raises(lambda: rd.get(U), F.Refused) and not rd.session.calls)
rd, _ = reader([Resp(200, {})], deadline=-1.0)
check("reader: no request starts after the deadline",
      raises(lambda: rd.get(U), F.OutOfTime) and not rd.session.calls)
pg = lambda ids, more: Resp(200, {"features": [{"attributes": {"id": i}, "geometry": {"x": 1, "y": 2}}
                                               for i in ids], "exceededTransferLimit": more})
rd, _ = reader([pg([1, 2], True), pg([3], False)])
got = F.read_arcgis(rd, {"url": "https://h/FeatureServer/0"})
check("read_arcgis: pages on exceededTransferLimit with resultOffset until the last page",
      [g["attrs"]["id"] for g in got] == [1, 2, 3] and rd.session.calls[1][1].get("resultOffset") == 2
      and "resultOffset" not in rd.session.calls[0][1], rd.session.calls)
rd, _ = reader([Resp(200, {"features": [{"attributes": {"NAME": "X"}, "geometry": {}}]})])
asked = []
check("propose: a source with no placeable record proposes nothing instead of crashing",
      F.propose_source(rd, {"type": "arcgis", "url": "https://h/0", "fields": {"name": "NAME"}},
                       lambda bbox: asked.append(bbox) or []) is None and not asked)

# --- the shipped config ---------------------------------------------------------
import mapsee_supabase_sync as S  # noqa: E402
refs = [m["ref"] for s in CFG["sources"] for m in (s.get("osm") or {}).values() if m.get("mode") == "same"]
check("config: no OSM element is claimed twice", len(refs) == len(set(refs)), len(refs))
check("config: claimed_osm_refs reads exactly the 'same' entries", F.claimed_osm_refs(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "facility_hours_sources.json")) == set(refs))
check("config: every category is a lens key and every source names its publisher",
      all(s.get("category") in S.MAPSEE_CATEGORY_KEYS and s.get("publisher") for s in CFG["sources"]))
check("config: a missing config claims nothing", F.claimed_osm_refs("/nonexistent.json") == set())

# --- main's --propose-osm path --------------------------------------------------
# It crashed with a NameError (`cs`) on the first source with a placeable centre,
# after its Postpass query had been sent (review, 2026-10-05).
import contextlib as _ctx  # noqa: E402
import io as _io  # noqa: E402
_real_ps = F.propose_source
F.propose_source = lambda reader, src, fetch: {"way/1": {"mode": "same", "ref": "way/1"},
                                                "node/2": {"mode": "colocated", "ref": "node/2"}}
try:
    _buf = _io.StringIO()
    with _ctx.redirect_stdout(_buf):
        _rc = F.main(["--config", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "facility_hours_sources.json"),
                      "--propose-osm", "--only", "phoenix"])
    _out, _err = _buf.getvalue(), None
except Exception as exc:                                         # noqa: BLE001
    _rc, _out, _err = None, "", f"{type(exc).__name__}: {exc}"
finally:
    F.propose_source = _real_ps
check("main --propose-osm prints each source's map and its counts, without crashing",
      _err is None and "2 centres" in _out and '"osm"' in _out, _err or _out[:300])

print(f"\n{len(FAILED)} failed" if FAILED else "\nall passed")
sys.exit(1 if FAILED else 0)
