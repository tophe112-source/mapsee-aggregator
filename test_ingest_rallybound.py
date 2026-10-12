"""Does the RallyBound adapter put each charity walk at its own place and hour?

The fixtures are rows of the two live EventsLite answers of 2026-10-11
(www.kidneywalk.org and fundraise.cff.org, series 9), verbatim. The traps:

  * THE ZONE IS A SETTING, THE CLOCK IS WHAT THE ORGANISER TYPED. Twin Cities
    (St. Paul, MN) says US/Eastern and EventDateUtc 13:00Z; its own page says
    "Sunday, October 25 | 9:00 am", which in St. Paul is 14:00Z. 3 of 25 placed
    walks had a zone that disagrees with their place (Twin Cities, Memphis,
    Louisville). The row carries the naive wall clock and the sync zones it.
  * "TBD" with no coordinates is not a place.
  * the one-line Address: ZIP and state off the end, a named place before a
    numbered street is the venue, "&amp;" unescaped, and the town that had no
    comma before it ("9151 NW 2nd Street   Plantation", "3000 Downs Blvd.
    Metairie": 2 of 25 placed walks had no town).
  * A SESSION-WIDE "Accept: application/json" MADE BOTH HOSTS ANSWER /robots.txt
    WITH 406 - a 4xx, which RFC 9309 reads as "no rules" - so the robots check
    passed without reading a rule (and Cloudflare cached that 406 for
    kidneywalk.org). Only the API request may ask for JSON.

No network: a fake session, literal JSON.
"""
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_rallybound as RB
import offers_0227 as O
from mapsee_supabase_sync import _to_utc_if_naive, derive_categories, to_row

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


TODAY = date(2026, 10, 11)
NKF_SRC = {"name": "National Kidney Foundation Kidney Walk", "org": "National Kidney Foundation",
           "program": "Kidney Walk", "url": "https://www.kidneywalk.org/widgetapi/EventsLite",
           "about": "A walk raising money for the National Kidney Foundation.",
           "categories": ["fitness", "outdoors"]}
CFF_SRC = {"name": "Cystic Fibrosis Foundation Great Strides", "org": "Cystic Fibrosis Foundation",
           "program": "Great Strides", "url": "https://fundraise.cff.org/widgetapi/EventsLite?eventSeriesId=9",
           "categories": ["fitness", "outdoors"]}

# --------------------------------------------------------------------------- #
# fixtures: the live answers of 2026-10-11 22:18 UTC
# --------------------------------------------------------------------------- #
NKF_JSON = r'''[
 {
  "Name": "2026 Northern California Kidney Walk",
  "EventDateUtc": "2026-11-08T17:00:00Z",
  "EventDateTimeLocal": "2026-11-08T09:00:00",
  "EventTimeZone": "America/Los_Angeles",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "37.3515108",
   "Lon": "-121.9255038",
   "Address": "1123 Coleman Ave , San Jose , CA , 95110",
   "State": "CA"
  },
  "URL": "www.kidneywalk.org/northerncal",
  "Id": 31565,
  "EventSeriesId": 22,
  "AmountRaised": 130311.94,
  "AmountRaisedWithChampions": 130311.94,
  "Image": null
 },
 {
  "Name": "2026 Twin Cities Kidney Walk",
  "EventDateUtc": "2026-10-25T13:00:00Z",
  "EventDateTimeLocal": "2026-10-25T09:00:00",
  "EventTimeZone": "US/Eastern",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "44.9532955",
   "Lon": "-93.16534380000002",
   "Address": "400 Snelling Ave N , St. Paul , MN, 55105",
   "State": "MN"
  },
  "URL": "www.kidneywalk.org/twincities",
  "Id": 32139,
  "EventSeriesId": 22,
  "AmountRaised": 81087.98,
  "AmountRaisedWithChampions": 81087.98,
  "Image": null
 },
 {
  "Name": "2026 Louisville Kidney Walk",
  "EventDateUtc": "2026-10-18T18:00:00Z",
  "EventDateTimeLocal": "2026-10-18T13:00:00",
  "EventTimeZone": "US/Central",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "38.2612727",
   "Lon": "-85.7381367",
   "Address": "1101 E River Rd, Louisville, KY 40206",
   "State": "KY"
  },
  "URL": "www.kidneywalk.org/louisville",
  "Id": 32319,
  "EventSeriesId": 22,
  "AmountRaised": 72216.82,
  "AmountRaisedWithChampions": 72216.82,
  "Image": null
 },
 {
  "Name": "2026 National Capital Area Kidney Walk",
  "EventDateUtc": "2026-10-17T13:00:00Z",
  "EventDateTimeLocal": "2026-10-17T09:00:00",
  "EventTimeZone": "US/Eastern",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "38.8877239",
   "Lon": "-77.0336834",
   "Address": "Independence Ave SW &amp; 15th Street NW , Washington , DC , 20004",
   "State": "DC"
  },
  "URL": "www.kidneywalk.org/nca",
  "Id": 32421,
  "EventSeriesId": 22,
  "AmountRaised": 43675.44,
  "AmountRaisedWithChampions": 43675.44,
  "Image": null
 },
 {
  "Name": "2026 Ft. Lauderdale Kidney Walk",
  "EventDateUtc": "2026-11-14T14:00:00Z",
  "EventDateTimeLocal": "2026-11-14T09:00:00",
  "EventTimeZone": "US/Eastern",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "26.1267707",
   "Lon": "-80.2725524",
   "Address": "9151 NW 2nd Street   Plantation, FL 33324",
   "State": "FL"
  },
  "URL": "www.kidneywalk.org/ftlauderdale",
  "Id": 32547,
  "EventSeriesId": 22,
  "AmountRaised": 65732.86,
  "AmountRaisedWithChampions": 65732.86,
  "Image": null
 },
 {
  "Name": "2027 Long Island Kidney Walk",
  "EventDateUtc": "2027-05-16T13:00:00Z",
  "EventDateTimeLocal": "2027-05-16T09:00:00",
  "EventTimeZone": "US/Eastern",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "40.7341763",
   "Lon": "-73.3445175",
   "Address": "625 Belmont Avenue , West Babylon , NY , 11704",
   "State": "NY"
  },
  "URL": "www.kidneywalk.org/longisland",
  "Id": 33202,
  "EventSeriesId": 22,
  "AmountRaised": 0,
  "AmountRaisedWithChampions": 0,
  "Image": null
 }
]'''

CFF_JSON = r'''[
 {
  "Name": "Sarasota Great Strides 2026",
  "EventDateUtc": "2026-10-17T13:00:00Z",
  "EventDateTimeLocal": "2026-10-17T09:00:00",
  "EventTimeZone": "US/Eastern",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": "27.2654230",
   "Lon": "-82.5528340",
   "Address": "Siesta Key Beach, 948 Beach Road, Siesta Key, FL 34242",
   "State": "FL"
  },
  "URL": "fundraise.cff.org/Sarasota26",
  "Id": 31688,
  "EventSeriesId": 9,
  "AmountRaised": 11257.25,
  "AmountRaisedWithChampions": 11257.25,
  "Image": null
 },
 {
  "Name": "Ridgecrest Great Strides 2026",
  "EventDateUtc": "2026-12-05T18:00:00Z",
  "EventDateTimeLocal": "2026-12-05T10:00:00",
  "EventTimeZone": "America/Los_Angeles",
  "EndDateUtc": null,
  "EndDateTimeLocal": null,
  "Location": {
   "Lat": null,
   "Lon": null,
   "Address": "TBD",
   "State": null
  },
  "URL": "fundraise.cff.org/Ridgecrest2026",
  "Id": 31702,
  "EventSeriesId": 9,
  "AmountRaised": 2850.0,
  "AmountRaisedWithChampions": 2850.0,
  "Image": null
 }
]'''
NKF = json.loads(NKF_JSON)
CFF = json.loads(CFF_JSON)

# --------------------------------------------------------------------------- #
# the address
# --------------------------------------------------------------------------- #
ADDRS = [
    (("1123 Coleman Ave , San Jose , CA , 95110", "CA"),
     {"venue": None, "street": "1123 Coleman Ave", "city": "San Jose", "region": "CA", "postal_code": "95110"}),
    (("Siesta Key Beach, 948 Beach Road, Siesta Key, FL 34242", "FL"),
     {"venue": "Siesta Key Beach", "street": "948 Beach Road", "city": "Siesta Key", "region": "FL",
      "postal_code": "34242"}),
    # a run of spaces is the comma the form left out (Ft. Lauderdale), and the
    # words after a street's suffix are the town when nothing else is (New Orleans)
    (("9151 NW 2nd Street   Plantation, FL 33324", "FL"),
     {"venue": None, "street": "9151 NW 2nd Street", "city": "Plantation", "region": "FL",
      "postal_code": "33324"}),
    (("3000 Downs Blvd. Metairie, LA 70003", "LA"),
     {"venue": None, "street": "3000 Downs Blvd.", "city": "Metairie", "region": "LA", "postal_code": "70003"}),
    (("1 Main Street NW   Washington, DC 20004", "DC"),
     {"venue": None, "street": "1 Main Street NW", "city": "Washington", "region": "DC", "postal_code": "20004"}),
    (("400 Snelling Ave N, MN 55105", "MN"),
     {"venue": None, "street": "400 Snelling Ave N", "city": None, "region": "MN", "postal_code": "55105"}),
    (("Independence Ave SW &amp; 15th Street NW , Washington , DC , 20004", "DC"),
     {"venue": None, "street": "Independence Ave SW & 15th Street NW", "city": "Washington", "region": "DC",
      "postal_code": "20004"}),
    (("TBD", None), {"venue": None, "street": None, "city": None, "region": None, "postal_code": None}),
]
for (raw, state), want in ADDRS:
    got = RB.split_address(raw, state)
    check(f"address {raw[:52]!r}", got == want, got)

# --------------------------------------------------------------------------- #
# one walk, and the hour
# --------------------------------------------------------------------------- #
host = "www.kidneywalk.org"
ev = {r["Id"]: RB.to_event(r, NKF_SRC, host, TODAY, 180) for r in NKF}
tc, why = ev[32139]
check("Twin Cities is written", tc is not None, why)
check("its start is the wall clock the organiser typed, with no zone attached",
      tc.start_local == "2026-10-25T09:00:00" and tc.start_utc is None and tc.timezone is None,
      (tc.start_local, tc.start_utc, tc.timezone))
utc = _to_utc_if_naive(tc.start_local, tc.latitude, tc.longitude)
check("the sync zones it from St. Paul's coordinates: 9:00 CDT is 14:00Z, not the feed's 13:00Z",
      utc == "2026-10-25T14:00:00Z", utc)
check("the platform's coordinates are kept", (tc.latitude, tc.longitude, tc.coords_exact) ==
      (44.9532955, -93.16534380000002, True), (tc.latitude, tc.longitude))
check("the event page gets its scheme", tc.ticket_url == "https://www.kidneywalk.org/twincities", tc.ticket_url)
check("an Id is the tenant's: source_id carries the host", tc.source_id == "www.kidneywalk.org:32139", tc.source_id)
check("community, with fitness and outdoors", (tc.category, tc.categories) == ("community", ["fitness", "outdoors"]))
check("nothing is said about price", "free" not in tc.description.lower(), tc.description)
row = to_row(tc.as_record("2026-10-11T00:00:00Z"), "host")
check("the row reads '400 Snelling Ave N, St. Paul, MN 55105'",
      "📍 400 Snelling Ave N, St. Paul, MN 55105" in row["description"], row["description"])
check("the sync keeps it on community", derive_categories(tc.as_record("x"))[0] == "community")

li, why = ev[33202]
check("Long Island in May 2027 is beyond 180 days", li is None and why == "beyond the window", why)
past = dict(NKF[2], EventDateTimeLocal="2026-10-10T13:00:00")
check("a walk the day before today is not written", RB.to_event(past, NKF_SRC, host, TODAY, 180) == (None, "past"),
      RB.to_event(past, NKF_SRC, host, TODAY, 180))
today_walk, _w = RB.to_event(dict(NKF[2], EventDateTimeLocal="2026-10-11T07:00:00"), NKF_SRC, host, TODAY, 180)
check("...while one this morning still is (the day is the test, not the hour)", today_walk is not None)
ends = RB.to_event(dict(NKF[2], EndDateTimeLocal="2026-10-18T15:00:00"), NKF_SRC, host, TODAY, 180)[0]
check("an end after the start is kept", ends.end_local == "2026-10-18T15:00:00", ends.end_local)
before = RB.to_event(dict(NKF[2], EndDateTimeLocal="2026-10-18T11:00:00"), NKF_SRC, host, TODAY, 180)[0]
check("an end before the start (13:00) is no end", before.end_local is None, before.end_local)
check("the rest of the kidney walks are written", sum(1 for e, _ in ev.values() if e) == 5,
      {k: w for k, (e, w) in ev.items() if not e})

cff = {r["Id"]: RB.to_event(r, CFF_SRC, "fundraise.cff.org", TODAY, 180) for r in CFF}
check("a walk at 'TBD' with no coordinates is not written", cff[31702] == (None, "no place (TBD)"), cff[31702])
sk = cff[31688][0]
check("Sarasota's venue is the named beach before the street",
      (sk.venue_name, sk.address, sk.city) == ("Siesta Key Beach", "948 Beach Road", "Siesta Key"),
      (sk.venue_name, sk.address, sk.city))

# --------------------------------------------------------------------------- #
# ingest_source over a fake reader
# --------------------------------------------------------------------------- #
got = []
rep = RB.ingest_source(NKF_SRC, lambda u: NKF + [NKF[0]], TODAY, 180, got.append)
check("a repeated Id is read once", rep["listed"] == 7 and rep["kept"] == 5 and len(got) == 5, rep)

# --------------------------------------------------------------------------- #
# HTTP: refusals, and robots.txt read without the API's Accept header
# --------------------------------------------------------------------------- #


class Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text
        self.content = text.encode()
        self.url = ""


class FakeSession:
    """Answers like the hosts did: robots.txt is 406 to anybody asking for JSON."""

    def __init__(self):
        self.headers, self.calls = {}, []

    def get(self, url, **kw):
        sent = dict(self.headers, **(kw.get("headers") or {}))
        self.calls.append((url, sent.get("Accept")))
        if url.endswith("/robots.txt"):
            if "json" in (sent.get("Accept") or ""):
                return Resp(406, "<title>406 - Client browser does not accept the MIME type</title>")
            return Resp(200, "User-agent: *\nDisallow: /Admin/\nAllow: /\n")
        return Resp(200, json.dumps(NKF if "kidneywalk" in url else CFF))


import requests  # noqa: E402

sessions = []
saved = requests.Session


def make():
    s = FakeSession()
    sessions.append(s)
    return s


tmp = tempfile.mkdtemp()
cfg_path, store_path = os.path.join(tmp, "rb.json"), os.path.join(tmp, "store.json")
json.dump({"within_days": 180, "pause": 0, "sources": [NKF_SRC, CFF_SRC]}, open(cfg_path, "w"))
requests.Session = make
try:
    os.environ["MAPSEE_TODAY"] = "20261011"
    RB.main(["--config", cfg_path, "--store", store_path])
finally:
    requests.Session = saved
calls = sessions[0].calls
robots = [a for u, a in calls if u.endswith("/robots.txt")]
api = [a for u, a in calls if "widgetapi" in u]
check("robots.txt is read once per host, without asking for JSON",
      len(robots) == 2 and not any("json" in (a or "") for a in robots), calls)
check("the API request asks for JSON", api == ["application/json", "application/json"], calls)
store = json.load(open(store_path, encoding="utf-8"))
check("a whole run writes the 5 kidney walks and the placed Great Strides walk",
      len(store["events"]) == 6, len(store["events"]))


class Robots:
    def check(self, url):
        return {"allowed": False, "status": "ok", "rule": "Disallow: /widgetapi/"}


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


class One:
    def __init__(self, code, text=""):
        self.r, self.calls = Resp(code, text), 0

    def get(self, url, **kw):
        self.calls += 1
        return self.r


s = One(200, "[]")
check("robots.txt says no: refused before the request",
      raises(lambda: RB.fetch(s, "https://www.kidneywalk.org/widgetapi/EventsLite", Robots()), RB.Refused)
      and s.calls == 0)
check("a 403 is a refusal", raises(lambda: RB.fetch(One(403), "https://x/widgetapi/EventsLite"), RB.Refused))
check("a 429 is a refusal, not a retry", raises(lambda: RB.fetch(One(429), "https://x/widgetapi/EventsLite"), RB.Refused))
check("a bot challenge is a refusal",
      raises(lambda: RB.fetch(One(200, "<title>Just a moment...</title>"), "https://x/widgetapi/EventsLite"),
             RB.Refused))

# --------------------------------------------------------------------------- #
# the config the job runs
# --------------------------------------------------------------------------- #
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rallybound_sources.json"),
                     encoding="utf-8"))
urls = [s["url"] for s in cfg["sources"]]
check("rallybound_sources.json lists the Kidney Walk and Great Strides finders",
      urls == ["https://www.kidneywalk.org/widgetapi/EventsLite",
               "https://fundraise.cff.org/widgetapi/EventsLite?eventSeriesId=9"], urls)
check("no source claims a free registration it has not measured",
      not any("free" in (s.get("about") or "").lower() for s in cfg["sources"]))
# ...and the row the sync stores says nothing ../mapsee migration 0227 reads as
# a price: no free tag, no deal (offers_0227 is 0227's predicate, vendored).
row = to_row(tc.as_record("2026-10-11T00:00:00Z"), "host")
tags = O.classify({"title": row["title"], "description": row["description"]})[0]
check("a kidney walk is not tagged free, nor a discount or an intro offer, by 0227",
      not (tags & {"free", "discount", "intro", "pwyw", "sale"}), tags)

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all RallyBound checks passed")
