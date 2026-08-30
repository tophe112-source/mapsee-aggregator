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
import json
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


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


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

# --------------------------------------------------------------------------- #
# monthly markets: "second Saturdays", "last Thursdays"
# --------------------------------------------------------------------------- #
# WHY THIS EXISTS. Every schedule this adapter understood was WEEKLY, and most
# flea and night markets are not — they are the second Saturday or the last
# Thursday of the month. Describing one as weekly is not a small error: it
# invents four pins for every real one, all of them well-formed, on the right
# street, at the right time, and wrong. Seattle Local Markets publishes both the
# rule AND the enumerated dates, so its own calendar is the fixture here.
#
# The sharp edge is that LAST is not FIFTH. Fremont's seven published dates
# alternate between the fourth Thursday and the fifth (April 30, May 28, ...),
# because "last" tracks the length of the month. nth=5 would drop four of the
# seven; nth=4 would misplace the other three. Nothing downstream can tell
# either failure from a market that simply moved.

# The operator's own published 2026 dates, copied from
# https://www.seattlelocalmarkets.com/calendar
FREMONT_PUBLISHED = ["2026-04-30", "2026-05-28", "2026-06-25", "2026-07-30",
                     "2026-08-27", "2026-09-24", "2026-10-29"]
MAGNOLIA_PUBLISHED = ["2026-04-11", "2026-05-09", "2026-06-13", "2026-07-11",
                      "2026-08-08", "2026-09-12", "2026-10-10"]


def rule_dates(days, nth, first, last):
    """Every date the adapter's rule selects between `first` and `last`, inclusive.

    This is _occurrences' filter without its dependence on today's date, so the
    assertion below is about the RULE and stays true in any year the suite runs.
    """
    weekdays, nths = M._weekdays(days), M._nth_set(nth)
    a, b = M._as_date(first), M._as_date(last)
    out, d = [], a
    while d <= b:
        if d.weekday() in weekdays and M._matches_nth(d, nths):
            out.append(d.isoformat())
        d += M.timedelta(days=1)
    return out


check("'last Thursdays' reproduces the operator's published dates exactly",
      rule_dates("Thursday", "last", FREMONT_PUBLISHED[0], FREMONT_PUBLISHED[-1])
      == FREMONT_PUBLISHED,
      rule_dates("Thursday", "last", FREMONT_PUBLISHED[0], FREMONT_PUBLISHED[-1]))
check("'second Saturdays' reproduces the operator's published dates exactly",
      rule_dates("Saturday", "second", MAGNOLIA_PUBLISHED[0], MAGNOLIA_PUBLISHED[-1])
      == MAGNOLIA_PUBLISHED,
      rule_dates("Saturday", "second", MAGNOLIA_PUBLISHED[0], MAGNOLIA_PUBLISHED[-1]))

# The trap, stated on its own so a future "simplification" to nth=5 fails loudly.
fifth = rule_dates("Thursday", "fifth", FREMONT_PUBLISHED[0], FREMONT_PUBLISHED[-1])
check("'last' is not 'fifth' — nth=5 would lose most of the season",
      fifth != FREMONT_PUBLISHED and len(fifth) < len(FREMONT_PUBLISHED),
      f"fifth gave {fifth}")
check("an unreadable nth is refused rather than silently ignored",
      _raises(lambda: M._nth_set("penultimate")))
check("no nth still means weekly — every existing market is untouched",
      M._nth_set(None) == set() and rule_dates("Saturday", None, "2026-08-01", "2026-08-31")
      == ["2026-08-01", "2026-08-08", "2026-08-15", "2026-08-22", "2026-08-29"])

# A monthly market must not be DESCRIBED as weekly either; the description line
# is the only place a reader learns the cadence.
evs = M.market_events({"name": "Magnolia Flea Market", "lat": 47.65926, "lon": -122.38796,
                       "days": "Saturday", "nth": "second", "hours": "10 a.m. - 4 p.m."},
                      {"name": "t", "horizon_days": 365, "timezone": "America/Los_Angeles"},
                      None)
check("a second-Saturday market is not advertised as weekly",
      evs and "Weekly" not in evs[0].description, evs and evs[0].description)
check("a year of a second-Saturday market is ~12 dates, not ~52",
      10 <= len(evs) <= 13, len(evs))
check("and every one of them really is a second Saturday",
      all(M._matches_nth(M._as_date(e.start_local), {2}) for e in evs))

# --------------------------------------------------------------------------- #
# the Seattle Local Markets config entry itself
# --------------------------------------------------------------------------- #
# The rule above is only worth pinning if the config still uses it. This is the
# edit somebody makes while "tidying" — dropping nth, or replacing it with the
# frozen date list the page also prints.
SLM = next((s for s in json.load(open("market_sources.json", encoding="utf-8"))
            if s.get("name") == "Seattle Local Markets"), None)
check("seattlelocalmarkets.com is configured", SLM is not None)
if SLM:
    by_name = {m["name"]: m for m in SLM["markets"]}
    check("Fremont Evening Market is monthly, on the LAST Thursday",
          by_name.get("Fremont Evening Market", {}).get("nth") == "last"
          and by_name["Fremont Evening Market"]["days"] == "Thursday")
    check("Magnolia Flea Market is monthly, on the SECOND Saturday",
          by_name.get("Magnolia Flea Market", {}).get("nth") == "second"
          and by_name["Magnolia Flea Market"]["days"] == "Saturday")
    check("Magnolia Vintage Warehouse stays weekly — it is open every weekend",
          "nth" not in by_name.get("Magnolia Vintage Warehouse", {})
          and M._weekdays(by_name["Magnolia Vintage Warehouse"]["days"]) == [5, 6])
    # The seasons stop where the operator's published dates stop; the page says
    # November and December are TBD and that holiday dates get moved. Projecting
    # "last Thursday" into November lands on US Thanksgiving.
    check("no season runs past the last date the operator actually published",
          by_name["Fremont Evening Market"]["season_end"] == FREMONT_PUBLISHED[-1]
          and by_name["Magnolia Flea Market"]["season_end"] == MAGNOLIA_PUBLISHED[-1])
    # Coordinates are pinned in the config, so this source spends nothing from
    # the geocode budget and its geohash identity cannot drift between runs —
    # the failure that filed Columbia City twice. See the cache note in the
    # adapter.
    check("every market is pre-placed, so no run can re-geocode it into a new cell",
          all(m.get("lat") and m.get("lon") for m in SLM["markets"]))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
