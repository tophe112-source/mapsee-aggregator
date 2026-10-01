"""Focused regression checks for the ICS importer's durable checkpoints."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_ics as ICS


fails = []


def check(label, condition, detail=""):
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else '   ' + str(detail)}")
    if not condition:
        fails.append(label)


class Store:
    def __init__(self, _path):
        self.records = {}
        self.saves = 0

    def save(self):
        self.saves += 1


sources = [
    {"name": "good", "url": "https://example.test/good.ics"},
    {"name": "bad", "url": "https://example.test/bad.ics"},
]
store = Store("unused.json")
cache_saves = []


def ingest(_store, _session, src, **_budget):
    if src["name"] == "bad":
        raise RuntimeError("feed refused")
    return 3


with patch.object(ICS.json, "loads", return_value=sources), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=store), \
     patch.object(ICS, "ingest_ics", side_effect=ingest), \
     patch.object(ICS, "_save_geo_cache", side_effect=lambda _cache: cache_saves.append("geo")), \
     patch.object(ICS, "_save_feed_cache", side_effect=lambda _cache: cache_saves.append("feed")), \
     patch.object(ICS, "_load_cursor", return_value={}), \
     patch.object(ICS, "_save_cursor"), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json"])

check("main succeeds when one source fails", rc == 0, rc)
check("event store checkpoints after every attempted source", store.saves == 2, store.saves)
check("both caches checkpoint after every attempted source",
      cache_saves == ["geo", "feed", "geo", "feed"], cache_saves)


# --- a source's `venue` pins the VEVENT that names no place at all -----------
# A neighbourhood council's annual yard sale has no LOCATION (it is the whole
# neighbourhood), and it was the one event on such a calendar that got dropped.
class VenueStore:
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev)
        return ev.source_id


VENUE_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:sale\r\nSUMMARY:Montlake Yard Sale\r\nDTSTART:20991010T170000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:meet\r\nSUMMARY:Board meeting\r\nDTSTART:20991011T170000Z\r\n"
    "LOCATION:Nowhere Hall\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
VENUE = {"name": "Montlake neighborhood", "city": "Seattle", "region": "WA",
         "country": "US", "lat": 47.6414, "lon": -122.303}
geocode_calls = []


def _no_hit_geocoder(_session, _suffix):
    def geocode(loc):
        geocode_calls.append(loc)
        return None, None
    return geocode


with patch.object(ICS, "_fetch_ics", return_value=(VENUE_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder):
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "montlake", "url": "x", "venue": VENUE})
    row = vstore.rows[0] if vstore.rows else None
    check("venue pins the VEVENT with no LOCATION", kept == 1 and row is not None
          and row.name == "Montlake Yard Sale" and row.latitude == 47.6414
          and row.longitude == -122.303 and row.venue_name == "Montlake neighborhood"
          and row.city == "Seattle" and row.region == "WA", (kept, row))
    check("a LOCATION that fails to geocode is still dropped, not pinned to the venue",
          geocode_calls == ["Nowhere Hall"] and len(vstore.rows) == 1,
          (geocode_calls, len(vstore.rows)))
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "montlake", "url": "x"})
    check("without venue the same VEVENT is unplaceable", kept == 0, kept)
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "montlake", "url": "x", "venue": {"name": "no pin"}})
    check("a venue without coordinates is ignored", kept == 0, kept)

# ...but not the one whose own title says it is elsewhere. Betlehem in Bergen
# lists an online prayer meeting and street evangelism beside its services.
SKIP_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:svc\r\nSUMMARY:Gudstjeneste. Ingvald Kårbø.\r\nDTSTART:20991011T090000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:web\r\nSUMMARY:Bønnemøte på nett\r\nDTSTART:20991012T170000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:street\r\nSUMMARY:Gateevangelisering\r\nDTSTART:20991014T170000Z\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
with patch.object(ICS, "_fetch_ics", return_value=(SKIP_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder):
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "betlehem", "url": "x", "venue": VENUE,
                                         "venue_skip": r"på nett|gateevangelisering"})
    check("venue_skip keeps an event its title places elsewhere off the venue",
          kept == 1 and [r.name for r in vstore.rows] == ["Gudstjeneste. Ingvald Kårbø."],
          [r.name for r in vstore.rows])
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "betlehem", "url": "x", "venue": VENUE})
    check("...and without it every one of them is pinned there", kept == 3, kept)


# --- a LOCATION that names no place, and a GEO that says "unset" --------------
# Willoughby-Eastlake's "Offsite" rows geocoded 369 km away; GEO:0;0 is the
# null island, not a place.
PLACEHOLDER_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:off\r\nSUMMARY:Book Club at the Park\r\nDTSTART:20991010T170000Z\r\n"
    "LOCATION:Offsite\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:zero\r\nSUMMARY:Craft Night\r\nDTSTART:20991011T170000Z\r\n"
    "GEO:0;0\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:real\r\nSUMMARY:Author Talk\r\nDTSTART:20991012T170000Z\r\n"
    "LOCATION:Offsite Gallery\\, 12 Main St\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:tbd\r\nSUMMARY:Board Games\r\nDTSTART:20991013T170000Z\r\n"
    "LOCATION:TBD\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:mobile\r\nSUMMARY:Library Stop\r\nDTSTART:20991014T170000Z\r\n"
    "LOCATION:Bookmobile -\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:us\r\nSUMMARY:Virtual Storytime\r\nDTSTART:20991015T170000Z\r\n"
    "LOCATION:US\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
geocode_calls.clear()
with patch.object(ICS, "_fetch_ics", return_value=(PLACEHOLDER_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder):
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "willoughby", "url": "x"})
    check("'Offsite' is not sent to the geocoder, and a real place that starts with it is",
          geocode_calls == ["Offsite Gallery, 12 Main St"], geocode_calls)
    check("with no venue, the placeholder and the 0;0 rows are unplaceable", kept == 0, kept)
    geocode_calls.clear()
    vstore = VenueStore()
    kept = ICS.ingest_ics(vstore, None, {"name": "willoughby", "url": "x", "venue": VENUE})
    pinned = sorted(r.name for r in vstore.rows)
    # "TBD" did not say where, so the source's own venue is its best guess too.
    # "Offsite", "Bookmobile -" and "US" said the event is NOT there: the venue
    # would pin each of them exactly where it is not.
    check("with a venue, GEO:0;0 and 'TBD' fall back to it; 'Offsite', 'Bookmobile -' and 'US' do not",
          pinned == ["Board Games", "Craft Night"]
          and all(r.latitude == 47.6414 for r in vstore.rows), [(r.name, r.latitude) for r in vstore.rows])
    check("no placeholder reaches the geocoder", geocode_calls == ["Offsite Gallery, 12 Main St"], geocode_calls)
for loc in ("Offsite", "off-site", "TBD", "To be announced", "Various locations", "See description",
            "Offsite -", "Bookmobile -", "Outreach -", "External -", "US", "U.S.", "United States", "France"):
    check(f"placeholder: {loc!r}", bool(ICS.PLACEHOLDER_LOC_RX.match(loc)))
for loc in ("Offsite", "Offsite -", "Bookmobile -", "Various locations", "US", "France"):
    check(f"elsewhere, never the venue: {loc!r}", bool(ICS.ELSEWHERE_LOC_RX.match(loc)))
for loc in ("TBD", "To be announced", "See description"):
    check(f"unknown, the venue may stand in: {loc!r}", not ICS.ELSEWHERE_LOC_RX.match(loc))
for loc in ("Offsite Gallery", "TBD Brewing Co.", "Various Artists Studio, 3 Elm St", "None Such Farm",
            "US Bank Stadium", "Canada Water Library", "Outreach Center, 5 Oak St", "Bookmobile Garage",
            "Main Library - Room 2"):
    check(f"not a placeholder: {loc!r}", not ICS.PLACEHOLDER_LOC_RX.match(loc))


# --- an online session carries its branch's GEO, or 0;0 ------------------------
# Communico (2026-09-28, 46 library calendars): "Online - Virtual Room" with
# GEO:0;0 was guessed onto a county centroid and a shoe shop; "Virtual Branch -
# Virtual Room 3" carried the Main Library's GEO. Kent County's "&nbsp;" went to
# the geocoder as written, and "Offsite - Offsite" is two placeholders in a row.
import io  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

ONLINE_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:o1\r\nSUMMARY:ESL Conversation\r\nDTSTART:20991010T170000Z\r\n"
    "LOCATION:Online - Virtual Room\r\nGEO:0;0\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:o2\r\nSUMMARY:Resume Help\r\nDTSTART:20991011T170000Z\r\n"
    "LOCATION:Virtual Branch - Virtual Room 3\r\nGEO:30.3288;-81.6597\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:o3\r\nSUMMARY:Author Talk\r\nDTSTART:20991012T170000Z\r\n"
    "LOCATION:Westlake Porter Public Library - Online\r\nGEO:41.45;-81.92\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:vr\r\nSUMMARY:VR Open Lab\r\nDTSTART:20991013T170000Z\r\n"
    "LOCATION:Virtual Reality Lab - Room 2\r\nGEO:41.45;-81.92\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:nb\r\nSUMMARY:Stewardship Day\r\nDTSTART:20991014T170000Z\r\n"
    "LOCATION:Kent County Parks Administration Building&nbsp;\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:off2\r\nSUMMARY:Storytime in the Park\r\nDTSTART:20991015T170000Z\r\n"
    "LOCATION:Offsite - Offsite\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:comm\r\nSUMMARY:Pop-up Library\r\nDTSTART:20991016T170000Z\r\n"
    "LOCATION:In the Community -\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:park\r\nSUMMARY:Pop-up Library at the Park\r\nDTSTART:20991017T170000Z\r\n"
    "LOCATION:In the Community - Lincoln Park\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
geocode_calls.clear()
with patch.object(ICS, "_fetch_ics", return_value=(ONLINE_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder), \
     redirect_stdout(io.StringIO()) as out:
    vstore = VenueStore()
    ICS.ingest_ics(vstore, None, {"name": "communico", "url": "x", "venue": VENUE})
names = sorted(r.name for r in vstore.rows)
check("an online session is skipped whatever GEO its branch gave it, and a VR lab is a place",
      names == ["VR Open Lab"], names)
check("...and the skips are counted on the feed's line", "3 online" in out.getvalue(), out.getvalue())
check("&nbsp; is unescaped before the geocoder sees the LOCATION",
      "Kent County Parks Administration Building" in geocode_calls, geocode_calls)
check("'Offsite - Offsite' and 'In the Community -' reach neither the geocoder nor the venue",
      not {"Offsite - Offsite", "In the Community -"} & set(geocode_calls)
      and "Storytime in the Park" not in names and "Pop-up Library" not in names, (geocode_calls, names))
check("'In the Community - Lincoln Park' names a place, so it is geocoded",
      "In the Community - Lincoln Park" in geocode_calls, geocode_calls)
for loc in ("Online - Virtual Room", "Virtual -", "Virtual Branch - Virtual Room 3",
            "Virtual Library - Virtual Room 1", "Online", "Virtual", "Main Library (Online)",
            "Westlake Porter Public Library - Online", "ONLINE, Gorey Library",
            "Online, Melbourne, VIC, Australia"):
    check(f"online: {loc!r}", bool(ICS.ONLINE_LOC_RX.search(loc)))
for loc in ("Virtual Reality Lab - Room 2", "Online Learning Center, 5 Elm St", "Main Library - Room 2",
            "Zoom Room at Central Library"):
    check(f"not online: {loc!r}", not ICS.ONLINE_LOC_RX.search(loc))


# --- several feeds on one host wait out that host's Crawl-delay ---------------
# LibCal libraries publish a calendar per branch, and their robots.txt asks for
# 10 s between requests. Nine branch feeds used to be nine back-to-back GETs.
paced_sources = [
    {"name": "branch A", "url": "https://lib.example.test/ical_subscribe.php?cid=1", "crawl_delay": 10},
    {"name": "branch B", "url": "https://lib.example.test/ical_subscribe.php?cid=2"},
    {"name": "elsewhere", "url": "https://city.example.test/cal.ics"},
    {"name": "branch C", "url": "https://LIB.example.test/ical_subscribe.php?cid=3"},
]
clock = [100.0]
sleeps = []
order = []


def _paced_ingest(_store, _session, src, **_budget):
    order.append((src["name"], clock[0]))
    clock[0] += 1.0                      # each fetch takes a second of wall clock
    return 1


def _sleep(s):
    sleeps.append(round(s, 3))
    clock[0] += s


with patch.object(ICS.json, "loads", return_value=paced_sources), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=Store("unused.json")), \
     patch.object(ICS, "ingest_ics", side_effect=_paced_ingest), \
     patch.object(ICS, "_save_geo_cache"), patch.object(ICS, "_save_feed_cache"), \
     patch.object(ICS, "_load_cursor", return_value={}), patch.object(ICS, "_save_cursor"), \
     patch.object(ICS.time, "monotonic", side_effect=lambda: clock[0]), \
     patch.object(ICS.time, "sleep", side_effect=_sleep), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json"])

starts = dict(order)
check("paced run succeeds and reads every feed", rc == 0 and len(order) == 4, (rc, order))
check("the second feed on a host waits out the delay a sibling declared",
      starts["branch B"] - starts["branch A"] >= 10, order)
check("a feed on another host does not wait", sleeps.count(0) == 0 and len(sleeps) == 2, sleeps)
check("the host match ignores case, and the wait counts time already spent elsewhere",
      starts["branch C"] - starts["branch B"] >= 10 and sleeps[1] < 10, (order, sleeps))
check("the delay table takes the longest delay per host",
      ICS._crawl_delays([{"url": "https://a.test/x", "crawl_delay": 2},
                         {"url": "https://a.test/y", "crawl_delay": "10"},
                         {"url": "https://b.test/z"}]) == {"a.test": 10.0})

# ...and a wait that would outlast the run's budget ends the run instead.
clock[0] = 100.0
order.clear()
sleeps.clear()
with patch.object(ICS.json, "loads", return_value=paced_sources[:2]), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=Store("unused.json")), \
     patch.object(ICS, "ingest_ics", side_effect=_paced_ingest), \
     patch.object(ICS, "_save_geo_cache"), patch.object(ICS, "_save_feed_cache"), \
     patch.object(ICS, "_load_cursor", return_value={}), patch.object(ICS, "_save_cursor"), \
     patch.object(ICS.time, "monotonic", side_effect=lambda: clock[0]), \
     patch.object(ICS.time, "sleep", side_effect=_sleep), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json", "--max-minutes", "0.1"])
check("a crawl-delay wait longer than the budget stops the run before the fetch",
      rc == 0 and [n for n, _ in order] == ["branch A"] and not sleeps, (order, sleeps))


# --- a feed that names no charset is UTF-8, and its rows remember the old key --
# OpenAgenda serves a bare `text/calendar`; requests read it as ISO-8859-1, and
# every accented title reached the map garbled and was hashed into its row's
# identity. The reader is fixed; the old identity must still be computable, or
# the next sync writes the clean row beside the garbled one.
from requests.models import Response
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers
from mapsee_ingest import make_fingerprint


def _response(body, ctype, status=200, extra=None):
    r = Response()
    r.status_code, r._content = status, body
    r.headers = CaseInsensitiveDict({"Content-Type": ctype, **(extra or {})})
    r.encoding = get_encoding_from_headers(r.headers)    # what requests itself does
    return r


FR_ICS = ("BEGIN:VCALENDAR\r\n"
          "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Ateliers Numériques\r\nDTSTART:20991010T170000Z\r\n"
          "LOCATION:Médiathèque Lisa Bresner\r\nGEO:47.2;-1.55\r\nEND:VEVENT\r\n"
          "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Chess club\r\nDTSTART:20991011T170000Z\r\n"
          "LOCATION:Main Library\r\nGEO:47.2;-1.55\r\nEND:VEVENT\r\n"
          "BEGIN:VEVENT\r\nUID:c\r\nSUMMARY:Fête du quartier\r\nDTSTART:20991012T170000Z\r\nEND:VEVENT\r\n"
          "END:VCALENDAR\r\n")
FR_RAW = FR_ICS.encode("utf-8")
fr_text, fr_enc = ICS._decode_ics(_response(FR_RAW, "text/calendar"))
check("a text/calendar that names no charset is read as UTF-8",
      "Ateliers Numériques" in fr_text and fr_enc == "ISO-8859-1", fr_enc)
t2, e2 = ICS._decode_ics(_response(FR_RAW, "text/calendar; charset=utf-8"))
check("a declared charset is honoured and leaves no legacy reading", "Numériques" in t2 and e2 is None, e2)
_, e3 = ICS._decode_ics(_response(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "text/calendar"))
check("an ASCII feed reads the same either way", e3 is None, e3)
LATIN = "SUMMARY:Fête\r\n".encode("latin-1")
t4, e4 = ICS._decode_ics(_response(LATIN, "text/calendar"))
check("a body that is not UTF-8 is read as it always was", t4 == LATIN.decode("latin-1") and e4 is None, (t4, e4))
bogus = _response(FR_RAW, "text/calendar")
bogus.encoding = "x-no-such-codec"
t6, e6 = ICS._decode_ics(bogus)
check("an encoding Python does not know leaves no legacy reading to fail on", "Numériques" in t6 and e6 is None, e6)
E = "è".encode("utf-8")
FOLDED = b"SUMMARY:M\xc3\xa9diath" + E[:1] + b"\r\n " + E[1:] + b"que\r\n"
t5, _ = ICS._decode_ics(_response(FOLDED, "text/calendar"))
check("a fold that splits a character is joined before decoding", "Médiathèque" in t5, repr(t5))

mis = lambda s: s.encode("utf-8").decode("latin-1")      # what requests made of it
with patch.object(ICS, "_fetch_ics", return_value=(fr_text, "200", fr_enc)), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder):
    vstore = VenueStore()
    ICS.ingest_ics(vstore, None, {"name": "oa", "url": "x",
                                  "venue": {"name": "Médiathèque de quartier", "lat": 47.2, "lon": -1.55}})
by = {r.source_id: r for r in vstore.rows}
a, b, c = by.get("a"), by.get("b"), by.get("c")
check("the title is stored as the source wrote it", a is not None and a.name == "Ateliers Numériques",
      a and a.name)
check("its old key is exactly the one the ISO-8859-1 reading hashed",
      a is not None and a.legacy_fingerprints == [make_fingerprint(
          mis("Ateliers Numériques"), "2099-10-10", mis("Médiathèque Lisa Bresner"))],
      a and a.legacy_fingerprints)
check("an ASCII event carries no old key", b is not None and b.legacy_fingerprints == [],
      b and b.legacy_fingerprints)
check("a venue fallback's name came from the config and was never misread",
      c is not None and c.legacy_fingerprints == [make_fingerprint(
          mis("Fête du quartier"), "2099-10-12", "Médiathèque de quartier")],
      c and c.legacy_fingerprints)


class _Feed:
    def __init__(self, status=200):
        self.status, self.sent = status, []

    def get(self, url, timeout=None, headers=None):
        self.sent.append(dict(headers or {}))
        if self.status == 304:
            return _response(b"", "text/calendar", status=304)
        return _response(FR_RAW, "text/calendar", extra={"ETag": '"v1"'})


OA = "https://oa.test/a.ics"
with patch.dict(ICS._FEED_CACHE, {OA: {"etag": '"v1"', "ts": 9e12, "body": mis(FR_ICS)}}, clear=True):
    feed = _Feed()
    body, how, enc = ICS._fetch_ics(feed, OA)
    check("a cache entry from the old reader is neither revalidated nor reused",
          how == "200" and "If-None-Match" not in feed.sent[0] and "Numériques" in body, (how, feed.sent))
    entry = ICS._FEED_CACHE.get(OA) or {}
    check("the new entry records which reading the old reader used",
          entry.get("v") == ICS.FEED_CACHE_VERSION and entry.get("legacy_enc") == "ISO-8859-1", entry)
    body2, how2, enc2 = ICS._fetch_ics(_Feed(304), OA)
    check("a 304 hands back the same text and the same old reading",
          how2 == "304" and body2 == body and enc2 == enc, (how2, enc2))


# --- a source can exclude known non-events before date/geocode/store work -----
ROTARY_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:public\r\nSUMMARY:Rotary Speaker Meeting\r\nDTSTART:20991014T170000Z\r\n"
    "LOCATION:Rancho Senior Center, 3 Ethel Coplen Way, Irvine 92612\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:no1\r\nSUMMARY:NO Club Weekly Club Meeting\r\nDTSTART:20991025T170000Z\r\n"
    "LOCATION:Do Not Geocode 1\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:no2\r\nSUMMARY:NO Club Weekly Club Meeting\r\nDTSTART:20991125T170000Z\r\n"
    "LOCATION:Do Not Geocode 2\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:no3\r\nSUMMARY:NO Club Weekly Club Meeting\r\nDTSTART:20991225T170000Z\r\n"
    "LOCATION:Do Not Geocode 3\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:members\r\nSUMMARY:Code required: members-only session\r\nDTSTART:20991226T170000Z\r\n"
    "LOCATION:Do Not Geocode members\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:cancelled\r\nSUMMARY:Cancelled public meeting\r\nDTSTART:20991227T170000Z\r\n"
    "STATUS:CANCELLED\r\nLOCATION:Do Not Geocode cancelled\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
rotary_geocodes = []

def _rotary_geocoder(_session, _suffix):
    def geocode(loc):
        rotary_geocodes.append(loc)
        return (33.6646738, -117.8307687) if loc.startswith("Rancho Senior Center") else (1, 2)
    return geocode

with patch.object(ICS, "_fetch_ics", return_value=(ROTARY_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_rotary_geocoder), \
     redirect_stdout(io.StringIO()) as out:
    rotary_store = VenueStore()
    rotary_kept = ICS.ingest_ics(rotary_store, None, {
        "name": "rotary-test", "url": "x",
        "skip_title": r"^NO Club Weekly Club Meeting$|code required",
    })
check("title filters skip Rotary notice and code-gated rows before geocoding or storage",
      rotary_kept == 1 and [r.name for r in rotary_store.rows] == ["Rotary Speaker Meeting"]
      and rotary_geocodes == ["Rancho Senior Center, 3 Ethel Coplen Way, Irvine 92612"],
      (rotary_kept, [r.name for r in rotary_store.rows], rotary_geocodes))
check("title filter and existing cancellation skips are counted",
      "4 title-filtered" in out.getvalue() and "1 cancelled (STATUS:CANCELLED)" in out.getvalue(),
      out.getvalue())

with patch.object(ICS, "_fetch_ics", return_value=(ROTARY_ICS, "200")):
    try:
        ICS.ingest_ics(VenueStore(), None, {"name": "bad-regex", "url": "x", "skip_title": "["})
        bad_regex_failed_source = False
    except ValueError as exc:
        bad_regex_failed_source = "invalid skip_title regex" in str(exc)
check("a malformed title-filter regex fails the source explicitly", bad_regex_failed_source)


if fails:
    raise SystemExit(f"{len(fails)} ICS test(s) failed: {', '.join(fails)}")
print("all ICS tests passed")
