"""Does the OSM market sweep survive a metro that loses its slot, and does it
say so?

WHY THIS EXISTS. The Overpass sweep is 245 bboxes against a free endpoint, so a
handful lose their slot on any given run — measured on an 8-metro sample, New
York and Los Angeles both came back empty while the other six answered. The loop
simply moved on. Because this source runs twice a week, the two largest markets
in the US could be absent for days behind one line of log, and a sweep that
covered 243 of 245 metros printed the same closing line as one that covered all
of them.

Also pinned here: the city is not a street. OSM marketplaces mostly carry no
`addr:street`, and the loader used to glue the bbox's city into `address` and
set no city at all — so every row reached the database with locality NULL and
street_address "Berlin". That is not cosmetic: mapsee_supabase_sync._addr_parts
treats `address` as a street and hands it to the US Census batch geocoder, so a
SURVEYED OSM point was offered up to be overwritten by a lookup of a bare city
name. It survived only because Census returns nothing for "Berlin".

No network: the Overpass endpoint is a stub that fails on demand.
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_markets as M

# The retry LOGIC is what is under test, not the waiting: at 5s/15s/45s a single
# failing bbox costs 65s and this suite would take minutes and get skipped.
M.OVERPASS_BACKOFF_S = 0

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


BBOXES = [{"name": "Alpha", "city": "Alphaville", "s": 1, "w": 1, "n": 2, "e": 2},
          {"name": "Beta", "city": "Betaville", "s": 3, "w": 3, "n": 4, "e": 4}]


def market_el(name="Grand Market", street=True):
    tags = {"name": name, "opening_hours": "Su 10:00-16:00"}
    if street:
        tags["addr:housenumber"] = "12"
        tags["addr:street"] = "Market Street"
    return {"lat": 1.5, "lon": 1.5, "tags": tags}


class _Resp:
    def __init__(self, status=200, els=None):
        self.status_code = status
        self._els = els or []
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"elements": self._els}


class _Session:
    """Fails the named bboxes for the first `fail_times` calls, then answers."""

    def __init__(self, fail_names, fail_times=99, els=None):
        self.fail_names, self.fail_times = set(fail_names), fail_times
        self.els = els                       # None = one DISTINCT market per bbox
        self.calls = []

    def post(self, endpoint, data=None, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data)
        # the bbox is in the query text; map it back to a name by its south edge
        name = "Alpha" if "1,1,2,2" in body else "Beta"
        self.calls.append(name)
        if name in self.fail_names and self.calls.count(name) <= self.fail_times:
            return _Resp(504)
        if self.els is not None:
            return _Resp(200, self.els)
        # Distinct per bbox on purpose. `seen` dedupes on (name, lat, lon)
        # because configured bboxes overlap in real life, so handing both metros
        # the same market would test the dedupe rather than the sweep.
        el = market_el(name=f"{name} Market")
        el["lat"] = 1.5 if name == "Alpha" else 3.5
        el["lon"] = 1.5 if name == "Alpha" else 3.5
        return _Resp(200, [el])


SRC = {"bboxes": BBOXES, "pause_s": 0}

# --------------------------------------------------------------------------- #
# a metro that loses its slot gets a SECOND PASS
# --------------------------------------------------------------------------- #
# Beta 504s four times in the main pass (exhausting its retries) and answers on
# the second pass. Nothing about that is exotic — it is what a busy free
# endpoint does — and before the second pass existed it cost the whole metro.
sess = _Session(fail_names=["Beta"], fail_times=4)
buf = io.StringIO()
with redirect_stdout(buf):
    out = M.load_overpass(sess, SRC)
log = buf.getvalue()
check("a metro that failed the main pass is retried and recovered",
      len(out) == 2, f"{len(out)} market(s): {[m['name'] for m in out]}")
check("the second pass is announced", "second pass" in log, log.strip()[:120])
check("the outcome is COUNTED, not implied",
      "recovered 1" in log and "still missing 0" in log, log.strip()[:200])

# A metro that never answers must be reported by name, not silently dropped.
sess = _Session(fail_names=["Beta"], fail_times=99)
buf = io.StringIO()
with redirect_stdout(buf):
    out = M.load_overpass(sess, SRC)
log = buf.getvalue()
check("a metro that never answers still yields the others", len(out) == 1, len(out))
check("and is named in the summary rather than vanishing",
      "still missing 1" in log and "Beta" in log, log.strip()[:200])

# The happy path must not pay for any of this.
sess = _Session(fail_names=[])
buf = io.StringIO()
with redirect_stdout(buf):
    out = M.load_overpass(sess, SRC)
log = buf.getvalue()
check("a clean sweep says nothing about a second pass", "second pass" not in log, log)
check("a clean sweep still returns every metro's markets", len(out) == 2, len(out))

# --------------------------------------------------------------------------- #
# the city is not a street
# --------------------------------------------------------------------------- #
sess = _Session(fail_names=[], els=[market_el(street=True)])
with redirect_stdout(io.StringIO()):
    out = M.load_overpass(sess, SRC)
mk = out[0]
check("a real street lands in address", mk["address"] == "12 Market Street", mk["address"])
check("the city lands in city, not glued onto the street",
      mk["city"] == "Alphaville", mk["city"])

# Most OSM marketplaces have no addr:street at all — this is the case that put
# "Berlin" in street_address and offered a surveyed point to the geocoder.
sess = _Session(fail_names=[], els=[market_el(street=False)])
with redirect_stdout(io.StringIO()):
    out = M.load_overpass(sess, SRC)
mk = out[0]
check("with no street, address is EMPTY rather than the city name",
      mk["address"] is None, mk["address"])
check("the city is still recorded", mk["city"] == "Alphaville", mk["city"])
check("OSM coordinates are marked exact so the Census pass cannot move them",
      mk["coords_exact"] is True, mk.get("coords_exact"))

# --------------------------------------------------------------------------- #
# the pin's label
# --------------------------------------------------------------------------- #
# Same rule as mapsee_ingest_runsignup: a street beginning with a house number
# makes a poor label. "12 Market Street" tells a reader nothing; the market's
# own name does.
evs = M.market_events({"name": "Flohmarkt am Arkonaplatz", "lat": 52.5, "lon": 13.4,
                       "address": "12 Market Street", "city": "Berlin",
                       "days": "Sunday", "hours": "10:00-16:00"},
                      {"name": "t", "horizon_days": 14}, None)
check("a house-numbered street is not used as the pin label",
      evs and evs[0].venue_name == "Flohmarkt am Arkonaplatz", evs and evs[0].venue_name)
evs = M.market_events({"name": "Ballard Farmers Market", "lat": 47.6, "lon": -122.3,
                       "address": "Ballard Ave NW & Vernon Pl NW", "city": "Seattle",
                       "days": "Sunday", "hours": "9:00-14:00"},
                      {"name": "t", "horizon_days": 14}, None)
check("a street that is not a house number still labels the pin",
      evs and evs[0].venue_name == "Ballard Ave NW & Vernon Pl NW", evs and evs[0].venue_name)
check("the city reaches the event, so locality is not NULL",
      evs and evs[0].city == "Seattle", evs and evs[0].city)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
