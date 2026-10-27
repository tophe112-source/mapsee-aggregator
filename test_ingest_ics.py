"""Focused regression checks for the ICS importer's durable checkpoints."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_ics as ICS

# THE FIXTURES DATE THEIR EVENTS 2099 so they stay upcoming whatever day the
# suite runs, and HORIZON_DAYS (365) would refuse every one of them. Lifted for
# the file; the horizon's own cases, at the end, put it back.
HORIZON_DAYS = ICS.HORIZON_DAYS
ICS.HORIZON_DAYS = 100 * 365


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


import io  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

with patch.object(ICS.json, "loads", return_value=sources), \
     patch.object(ICS.requests, "Session", create=True), \
     patch.object(ICS, "EventStore", return_value=store), \
     patch.object(ICS, "ingest_ics", side_effect=ingest), \
     patch.object(ICS, "_save_geo_cache", side_effect=lambda _cache: cache_saves.append("geo")), \
     patch.object(ICS, "_save_feed_cache", side_effect=lambda _cache: cache_saves.append("feed")), \
     patch.object(ICS, "_load_cursor", return_value={}), \
     patch.object(ICS, "_save_cursor"), \
     patch("builtins.open"), \
     redirect_stdout(io.StringIO()) as out:
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json"])

check("main succeeds when one source fails", rc == 0, rc)
check("a run shorter than SAVE_EVERY_SECONDS saves the store and both caches once, at exit",
      store.saves == 1 and cache_saves == ["geo", "feed"], (store.saves, cache_saves))
check("a FAILED source's line says how long it took",
      "[ics] bad FAILED: feed refused in 0.0s" in out.getvalue(), out.getvalue())


# --- the store, both caches and the cursor checkpoint TOGETHER, at most every ---
# SAVE_EVERY_SECONDS. One save of the 198.6 MB store took 3.1-3.5 s, and saving
# after every source cost 40.2 min of the 220-min budget on 2026-10-23. The
# cursor is only ever written after the store, so it never names a source the
# saved store does not hold; the budget, the end, and a cancelled step's SIGINT
# always checkpoint.
class ClockStore(Store):
    def save(self):
        super().save()
        saved.append(("store", clock_now[0]))


saved = []
clock_now = [0.0]
gated_sources = [{"name": f"s{i}", "url": f"https://example.test/s{i}.ics"} for i in range(7)]


def run_gated(ingest_fn, argv=("--max-minutes", "4"), srcs=gated_sources):
    """main() over `srcs` with a fake clock; every ingest takes 50 s."""
    saved.clear()
    clock_now[0] = 0.0
    gstore = ClockStore("unused.json")

    def timed(store_, session_, src, **kw):
        clock_now[0] += 50
        return ingest_fn(store_, session_, src, **kw)

    with patch.object(ICS.json, "loads", return_value=srcs), \
         patch.object(ICS.requests, "Session", create=True), \
         patch.object(ICS, "EventStore", return_value=gstore), \
         patch.object(ICS, "ingest_ics", side_effect=timed), \
         patch.object(ICS, "_save_geo_cache", side_effect=lambda _c: saved.append(("geo", clock_now[0]))), \
         patch.object(ICS, "_save_feed_cache", side_effect=lambda _c: saved.append(("feed", clock_now[0]))), \
         patch.object(ICS, "_load_cursor", return_value={}), \
         patch.object(ICS, "_save_cursor",
                      side_effect=lambda _p, url, offset=0, kept=0: saved.append(
                          ("cursor", url.rsplit("/", 1)[-1], offset, kept))), \
         patch.object(ICS.time, "monotonic", side_effect=lambda: clock_now[0]), \
         patch("builtins.open"), \
         redirect_stdout(io.StringIO()):
        return ICS.main(["--config", "unused.json", "--store", "unused-store.json", *argv])


def store_saves():
    return [e[1] for e in saved if e[0] == "store"]


def cursor_follows_store(log):
    """Every cursor write comes straight after a store save and both caches."""
    return all([k[0] for k in log[i - 3:i]] == ["store", "geo", "feed"]
               for i, entry in enumerate(log) if entry[0] == "cursor")


def ok_or_refused(_store, _session, src, **_kw):
    if src["name"] == "s1":
        raise RuntimeError("feed refused")
    return 1


check("SAVE_EVERY_SECONDS is the interval PerfectMind uses for its store",
      ICS.SAVE_EVERY_SECONDS == 120, ICS.SAVE_EVERY_SECONDS)
rc = run_gated(ok_or_refused)
store_times = store_saves()
cursors = [e[1:] for e in saved if e[0] == "cursor"]
# s0 ends at 50 s, s1 (FAILED) 100, s2 150 -> checkpoint; s3 200, s4 250;
# at s5 the 240 s budget is spent -> checkpoint.
check("a budgeted run saves at most once per interval, and once more at the budget",
      rc == 0 and store_times == [150, 250], (rc, saved))
check("...each checkpoint writes the store, both caches and then the cursor",
      cursors == [("s3.ics", 0, 0), ("s5.ics", 0, 0)] and cursor_follows_store(saved)
      and len(saved) == 8, saved)

# Six sources, 300 s: the interval's save after the last one is the exit's save.
rc = run_gated(lambda *_a, **_k: 1, argv=(), srcs=gated_sources[:6])
check("an unbudgeted pass saves on the interval, does not save twice at the end, and writes no cursor",
      rc == 0 and store_saves() == [150, 300]
      and not [e for e in saved if e[0] == "cursor"], (rc, saved))


def stops_inside_s2(_store, _session, src, **_kw):
    if src["name"] == "s2":
        raise ICS.BudgetExpired(7, 2)
    return 1


rc = run_gated(stops_inside_s2)
check("a budget reached inside a source checkpoints at once with its VEVENT offset",
      rc == 0 and [e[0] for e in saved] == ["store", "geo", "feed", "cursor"]
      and saved[-1] == ("cursor", "s2.ics", 7, 2), (rc, saved))


def interrupted_at(name):
    def ingest_fn(_store, _session, src, **_kw):
        if src["name"] == name:
            raise KeyboardInterrupt
        return 1
    return ingest_fn


try:
    run_gated(interrupted_at("s3"))
    interrupted = False
except KeyboardInterrupt:
    interrupted = True
check("a cancelled step's SIGINT checkpoints, and the cursor re-reads the source it was in",
      interrupted and store_saves() == [150, 200]
      and saved[-1] == ("cursor", "s3.ics", 0, 0) and cursor_follows_store(saved), (interrupted, saved))
try:
    run_gated(interrupted_at("s0"))
    interrupted = False
except KeyboardInterrupt:
    interrupted = True
check("...and inside the first source it saves the store but leaves the cursor it started from",
      interrupted and [e[0] for e in saved] == ["store", "geo", "feed"], (interrupted, saved))


# --- a source's `venue` pins the VEVENT that names no place at all -----------
# A neighbourhood council's annual yard sale has no LOCATION (it is the whole
# neighbourhood), and it was the one event on such a calendar that got dropped.
class VenueStore:
    def __init__(self):
        self.rows = []
        self.cancelled = []

    def upsert(self, ev):
        self.rows.append(ev)
        return ev.source_id

    def cancel(self, ev, reason):
        self.cancelled.append(ev)
        return "cancelled"


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
            "Online, Melbourne, VIC, Australia",
            # A LOCATION that is only the call's software (2026-10-23: 8 UW rows
            # pinned at the campus geocode, 0 matched before).
            "On Zoom", "Teams", "Online (Zoom)", "Zoom (see registration link)", "via Zoom",
            "Zoom Meeting", "Microsoft Teams", "MS Teams - link sent on registration",
            "Google Meet", "Webex", "Virtual (via Webex)", "Zoom webinar: link in description"):
    check(f"online: {loc!r}", bool(ICS.ONLINE_LOC_RX.search(loc)))
for loc in ("Virtual Reality Lab - Room 2", "Online Learning Center, 5 Elm St", "Main Library - Room 2",
            "Zoom Room at Central Library",
            # Zoom Room is a US chain of dog-agility gyms: a place, so no "room" suffix.
            "Zoom Room - Fremont", "Zoom Room", "Teamsters Local 117 Hall",
            "Google Meetinghouse Road Park"):
    check(f"not online: {loc!r}", not ICS.ONLINE_LOC_RX.search(loc))

# ...but a CANCELLED online session still reaches store.cancel. The 8 UW rows
# were written, pinned at the campus geocode, before the pattern knew "On Zoom",
# and an upsert cannot delete: skipping the cancellation too left such a row
# live, at its old time, until its date passed (review, 2026-10-24).
import re  # noqa: E402
from mapsee_ingest import EventStore as _OnlineStore  # noqa: E402


def _zoom_feed(status):
    return ("BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\nUID:u1\r\nSUMMARY:Seminar\r\nDTSTART:20991020T170000Z\r\n"
            f"LOCATION:On Zoom\r\n{status}END:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:u2\r\nSUMMARY:Office Hours\r\nDTSTART:20991021T170000Z\r\n"
            "LOCATION:On Zoom\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n")


def _campus_geocoder(_session, _suffix):
    def geocode(loc):
        geocode_calls.append(loc)
        return 47.6553, -122.3035
    return geocode


from mapsee_ingest import make_fingerprint as _fp  # noqa: E402
zoom_key = _fp("Seminar", "2099-10-20", "On Zoom")
with tempfile.TemporaryDirectory() as _d:
    zstore = _OnlineStore(os.path.join(_d, "s.json"))
    # Yesterday: the pattern did not know Zoom yet, so the row was geocoded and written.
    with patch.object(ICS, "_fetch_ics", return_value=(_zoom_feed(""), "200")), \
         patch.object(ICS, "make_location_geocoder", side_effect=_campus_geocoder), \
         patch.object(ICS, "ONLINE_LOC_RX", re.compile(r"(?!)")), \
         redirect_stdout(io.StringIO()):
        ICS.ingest_ics(zstore, None, {"name": "uw", "url": "x"})
    stored_before = zoom_key in zstore.records
    geocode_calls.clear()
    # Today: the pattern knows it, and the publisher cancels the seminar.
    with patch.object(ICS, "_fetch_ics", return_value=(_zoom_feed("STATUS:CANCELLED\r\n"), "200")), \
         patch.object(ICS, "make_location_geocoder", side_effect=_campus_geocoder), \
         redirect_stdout(io.StringIO()) as out:
        ICS.ingest_ics(zstore, None, {"name": "uw", "url": "x"})
    tomb = zstore.tombstones.get(zoom_key)
check("a cancelled 'On Zoom' VEVENT tombstones the row written before the pattern knew Zoom",
      stored_before and zoom_key not in zstore.records and tomb is not None and tomb["source_id"] == "u1",
      (stored_before, sorted(zstore.tombstones)))
check("...without a geocode, and the live online sibling is still skipped and counted",
      geocode_calls == [] and "1 cancelled (STATUS:CANCELLED), tombstoned" in out.getvalue()
      and "1 online (a virtual room, not a place)" in out.getvalue(), (geocode_calls, out.getvalue()))

# ...and so does one the publisher calls off ONLY in its title. Placed, that is
# the store's notice rule in upsert; online, the skip ran first and the row
# stayed live (review, 2026-10-24). A bare "CANCELLED" names no event: online.
def _zoom_titled(summary):
    return ("BEGIN:VCALENDAR\r\n"
            f"BEGIN:VEVENT\r\nUID:u1\r\nSUMMARY:{summary}\r\nDTSTART:20991020T170000Z\r\n"
            "LOCATION:On Zoom\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:u2\r\nSUMMARY:Office Hours\r\nDTSTART:20991021T170000Z\r\n"
            "LOCATION:On Zoom\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:u3\r\nSUMMARY:CANCELLED\r\nDTSTART:20991022T170000Z\r\n"
            "LOCATION:On Zoom\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n")


with tempfile.TemporaryDirectory() as _d:
    tstore = _OnlineStore(os.path.join(_d, "s.json"))
    with patch.object(ICS, "_fetch_ics", return_value=(_zoom_titled("Seminar"), "200")), \
         patch.object(ICS, "make_location_geocoder", side_effect=_campus_geocoder), \
         patch.object(ICS, "ONLINE_LOC_RX", re.compile(r"(?!)")), \
         redirect_stdout(io.StringIO()):
        ICS.ingest_ics(tstore, None, {"name": "uw", "url": "x"})
    stored_before = zoom_key in tstore.records
    geocode_calls.clear()
    with patch.object(ICS, "_fetch_ics", return_value=(_zoom_titled("CANCELLED - Seminar"), "200")), \
         patch.object(ICS, "make_location_geocoder", side_effect=_campus_geocoder), \
         redirect_stdout(io.StringIO()) as out:
        ICS.ingest_ics(tstore, None, {"name": "uw", "url": "x"})
    tomb = tstore.tombstones.get(zoom_key)
check("'CANCELLED - Seminar' at 'On Zoom', no STATUS, tombstones the 'Seminar' row as a notice",
      stored_before and zoom_key not in tstore.records and tomb is not None
      and tomb["source_id"] == "u1" and tomb.get("notice") is True
      and tomb["reason"] == "cancelled in the title" and len(tstore.tombstones) == 1,
      (stored_before, tstore.tombstones))
check("...without a geocode; the live sibling and the bare 'CANCELLED' are counted online",
      geocode_calls == [] and "1 online and cancelled in the title, tombstoned" in out.getvalue()
      and "2 online (a virtual room, not a place)" in out.getvalue(), (geocode_calls, out.getvalue()))


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

# A CANCELLED VEVENT is tombstoned under both keys the live row could be stored
# by, and the old key is the misread title INSIDE the notice: "CANCELLED - " was
# added when the event was called off, after the row was stored.
from mapsee_ingest import EventStore as _CxStore  # noqa: E402
CX_RAW = FR_ICS.replace("SUMMARY:Ateliers Numériques\r\n",
                        "SUMMARY:CANCELLED - Ateliers Numériques\r\nSTATUS:CANCELLED\r\n").encode("utf-8")
cx_text, cx_enc = ICS._decode_ics(_response(CX_RAW, "text/calendar"))
with tempfile.TemporaryDirectory() as _d, \
        patch.object(ICS, "_fetch_ics", return_value=(cx_text, "200", cx_enc)), \
        patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder), \
        redirect_stdout(io.StringIO()):
    cstore = _CxStore(os.path.join(_d, "s.json"))
    ICS.ingest_ics(cstore, None, {"name": "oa", "url": "x",
                                  "venue": {"name": "Médiathèque de quartier", "lat": 47.2, "lon": -1.55}})
tomb = cstore.tombstones.get(a.fingerprint if a else "")
check("a cancelled VEVENT is tombstoned under the live row's key",
      tomb is not None and tomb["source_id"] == "a", sorted(cstore.tombstones))
check("...and under its old key, unwrapped from the notice like the title",
      tomb is not None and tomb.get("legacy") == a.legacy_fingerprints, tomb)


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
check("...and the cancelled meeting, never geocoded above, went to store.cancel",
      [r.source_id for r in rotary_store.cancelled] == ["cancelled"],
      [r.source_id for r in rotary_store.cancelled])



class _ReadOnly:                       # mapsee_gcal's smoke read: rows, no cancel
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev)
        return "new"


with patch.object(ICS, "_fetch_ics", return_value=(ROTARY_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_rotary_geocoder), \
     redirect_stdout(io.StringIO()):
    ro = _ReadOnly()
    ICS.ingest_ics(ro, None, {"name": "ro", "url": "x"})
check("a collector with no cancel still reads a feed that carries a cancellation",
      "Rotary Speaker Meeting" in [r.name for r in ro.rows]
      and "Cancelled public meeting" not in [r.name for r in ro.rows], [r.name for r in ro.rows])

with patch.object(ICS, "_fetch_ics", return_value=(ROTARY_ICS, "200")):
    try:
        ICS.ingest_ics(VenueStore(), None, {"name": "bad-regex", "url": "x", "skip_title": "["})
        bad_regex_failed_source = False
    except ValueError as exc:
        bad_regex_failed_source = "invalid skip_title regex" in str(exc)
check("a malformed title-filter regex fails the source explicitly", bad_regex_failed_source)


# CivicPlus's URL is sometimes its subscription, while DESCRIPTION carries the
# exact event page. This is the Santa Rosa volunteer-feed shape, without fetching
# individual event pages or guessing a URL from the UID.
LINK_FEED = 'https://city.test/common/modules/iCalendar/iCalendar.aspx?catID=31&feed=calendar'
LINK_HOME = 'https://city.test/calendar.aspx?CID=31'
LINK_ICS = 'BEGIN:VCALENDAR\r\n' + ''.join(
    'BEGIN:VEVENT\r\nUID:' + uid + '\r\nSUMMARY:Volunteer cleanup ' + uid +
    '\r\nDTSTART:20991010T160000Z\r\nGEO:38.45;-122.74\r\n' +
    ('URL:' + url + '\r\n' if url else '') + 'DESCRIPTION:' + desc + '\r\nEND:VEVENT\r\n'
    for uid, url, desc in [
        ('2237', '/common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID=31',
         'Bring your friends. https://city.test/calendar.aspx?EID=2237'),
        ('2238', 'https://signup.test/volunteer?shift=2238',
         'https://city.test/calendar.aspx?EID=2238'),
        ('2239', '/common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID=31',
         'https://other.test/calendar.aspx?EID=2239 https://city.test/calendar.aspx?EID=999'),
        ('2240', '', 'https://city.test/calendar.aspx?EID=2240'),
        ('2241', '/calendar.aspx?EID=2241', 'https://city.test/calendar.aspx?EID=999'),
        ('2242', '/common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID=31', ''),
        ('2243', 'http://[', ''),
    ]
) + 'END:VCALENDAR\r\n'
with patch.object(ICS, '_fetch_ics', return_value=(LINK_ICS, '200')):
    link_store = VenueStore()
    ICS.ingest_ics(link_store, None, {'name': 'volunteer-links', 'url': LINK_FEED, 'url_home': LINK_HOME})
links = {r.source_id: r for r in link_store.rows}
check('a relative subscription URL uses its exact publisher event link',
      links['2237'].ticket_url == 'https://city.test/calendar.aspx?EID=2237', links['2237'].ticket_url)
check('a third-party signup URL remains exact',
      links['2238'].ticket_url == 'https://signup.test/volunteer?shift=2238', links['2238'].ticket_url)
check('foreign and wrong-event description links cannot replace a subscription',
      links['2239'].ticket_url == LINK_HOME, links['2239'].ticket_url)
check('an omitted URL can use an exact publisher event link already in the description',
      links['2240'].ticket_url == 'https://city.test/calendar.aspx?EID=2240', links['2240'].ticket_url)
check('an ordinary relative event URL becomes absolute',
      links['2241'].ticket_url == 'https://city.test/calendar.aspx?EID=2241', links['2241'].ticket_url)
check('a subscription without an exact event link falls back to the configured calendar page',
      links['2242'].ticket_url == LINK_HOME, links['2242'].ticket_url)
check('a malformed event URL does not lose the feed or other events',
      len(links) == 7 and links['2243'].ticket_url == LINK_HOME, links['2243'].ticket_url)
with patch.object(ICS, '_fetch_ics', return_value=(LINK_ICS, '200')):
    no_home = VenueStore()
    ICS.ingest_ics(no_home, None, {'name': 'no-home', 'url': LINK_FEED})
check('an unresolved subscription without a home is not an event info link',
      next(r for r in no_home.rows if r.source_id == '2242').ticket_url is None)
from mapsee_ingest import EventStore
from mapsee_supabase_sync import to_row
with tempfile.TemporaryDirectory() as td:
    persisted = EventStore(os.path.join(td, 'volunteer-links.json'))
    persisted.upsert(links['2237'])
    rendered = to_row(next(iter(persisted.records.values())), 'host')
check('the stored source link reaches the displayed Tickets / info line',
      'Tickets / info: https://city.test/calendar.aspx?EID=2237' in rendered['description'], rendered['description'])
HOME_ONLY_ICS = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:2242\r\nSUMMARY:Volunteer shift\r\n'
                 'DTSTART:20991010T160000Z\r\nGEO:38.45;-122.74\r\n'
                 'URL:/common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID=31\r\n'
                 'END:VEVENT\r\nBEGIN:VEVENT\r\nUID:2244\r\nSUMMARY:Another volunteer shift\r\n'
                 'DTSTART:20991011T160000Z\r\nGEO:38.45;-122.74\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
detail_calls = []
with patch.object(ICS, '_fetch_ics', return_value=(HOME_ONLY_ICS, '200')), \
     patch('mapsee_event_details.ramart_reader', return_value=lambda *args: detail_calls.append(args)):
    detail_store = VenueStore()
    ICS.ingest_ics(detail_store, None, {'name': 'home-only', 'url': LINK_FEED,
                                      'url_home': LINK_HOME, 'details': 'ramart'})
check('a home-only info fallback never fetches per-event source details',
      not detail_calls and len(detail_store.rows) == 2
      and all(r.ticket_url == LINK_HOME for r in detail_store.rows), detail_calls)

# --- `limit` keeps the SOONEST future events, whatever order the file is in ---
# Capitol Heights, MD's CivicPlus feed is written newest first (790 VEVENTs,
# 2034 down to tomorrow); in file order limit=100 kept only 2032-2034 rows.
NEWEST_FIRST_ICS = "BEGIN:VCALENDAR\r\n" + "".join(
    f"BEGIN:VEVENT\r\nUID:g{y}\r\nSUMMARY:Grocery Giveaway {y}\r\nDTSTART:{y}0110T150000Z\r\n"
    f"LOCATION:Town Hall\r\nEND:VEVENT\r\n" for y in range(2099, 2089, -1)) + "END:VCALENDAR\r\n"
with patch.object(ICS, "_fetch_ics", return_value=(NEWEST_FIRST_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=lambda *_: (lambda loc: (38.9, -76.9))):
    order_store = VenueStore()
    ICS.ingest_ics(order_store, None, {"name": "capitol-heights", "url": "x", "limit": 3})
    check("a newest-first feed over its limit keeps its SOONEST events, not the farthest",
          sorted(r.name for r in order_store.rows) == ["Grocery Giveaway 2090", "Grocery Giveaway 2091",
                                                      "Grocery Giveaway 2092"],
          [r.name for r in order_store.rows])


# --- publisher-verified attendance notes survive the public prose cap -------
# The Grove calendar's important cancellation caveat is near the END of a long
# recurring-event description. The database row caps prose at 800 characters,
# so a source-authored, concise attendance_note must precede that cap without
# replacing the publisher's original text or changing the event's identity.
from mapsee_supabase_sync import build_rows, to_row

ATTENDANCE_NOTE = "Check the organizer's newsletter or Instagram for same-day cancellations."
LONG_SOURCE_DESCRIPTION = ("Original volunteer work party details remain source-authored. " * 30
                           + "Publisher cancellation caveat at the tail.")
ATTENDANCE_ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:grove-work-party\r\n"
    "SUMMARY:Grove Community Garden Work Party\r\n"
    "DTSTART:20991010T210000Z\r\nDTEND:20991010T230000Z\r\n"
    "GEO:44.0398546;-123.0659835\r\n"
    "URL:https://calendar.example.test/event/grove-work-party\r\n"
    "DESCRIPTION:" + LONG_SOURCE_DESCRIPTION + "\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)

def _ingest_build_ics_row(directory, *, attendance_note=None):
    path = os.path.join(directory, "events.json")
    source = {"name": "Grove Community Garden", "url": "https://calendar.example.test/grove.ics",
              "category": "volunteer"}
    if attendance_note is not None:
        source["attendance_note"] = attendance_note
    persisted = EventStore(path)
    with patch.object(ICS, "_fetch_ics", return_value=(ATTENDANCE_ICS, "200")):
        ICS.ingest_ics(persisted, None, source)
    persisted.save()
    record = next(iter(persisted.records.values()))
    return record, build_rows(path, "host")[0]

with tempfile.TemporaryDirectory() as td:
    noted_dir = os.path.join(td, "noted")
    plain_dir = os.path.join(td, "plain")
    os.makedirs(noted_dir)
    os.makedirs(plain_dir)
    noted_record, noted_row = _ingest_build_ics_row(noted_dir, attendance_note=ATTENDANCE_NOTE)
    plain_record, plain_row = _ingest_build_ics_row(plain_dir)
    prior_plain_row = to_row(plain_record, "host")
check("the configured attendance note leads the capped public description",
      noted_row["description"].startswith(ATTENDANCE_NOTE)
      and ATTENDANCE_NOTE in noted_row["description"], noted_row["description"][:180])
check("the note does not replace the original source prose in the normalized store",
      noted_record["description"] == ATTENDANCE_NOTE + "\n\n" + LONG_SOURCE_DESCRIPTION
      and "Original volunteer work party details" in noted_row["description"],
      noted_record.get("description"))
check("the public row keeps the source event link and stable identity with the note",
      noted_row["external_id"] == plain_row["external_id"]
      and noted_record["sources"] == plain_record["sources"]
      and "Tickets / info: https://calendar.example.test/event/grove-work-party" in noted_row["description"],
      (noted_row.get("external_id"), plain_row.get("external_id"), noted_record.get("sources")))
check("omitting attendance_note leaves the existing capped row unchanged",
      plain_record["description"] == LONG_SOURCE_DESCRIPTION
      and plain_row == prior_plain_row
      and "Publisher cancellation caveat at the tail." not in plain_row["description"],
      (plain_record.get("description"), plain_row.get("description")))


# --- exact configured venue pins survive production geocoding ---------------
# A stale raw GEO can point to another city; an explicitly verified LOCATION
# override corrects it. When that override opts into coords_exact, build_rows
# must preserve its point. An otherwise identical, unmarked override keeps the
# established behavior of allowing the geocoder to improve source coordinates.
from mapsee_supabase_sync import build_rows

EXACT_PIN_ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:grove-exact-pin\r\n"
    "SUMMARY:Grove Community Garden Work Party\r\n"
    "DTSTART:20991010T210000Z\r\nDTEND:20991010T230000Z\r\n"
    "LOCATION:Grove Community Garden\r\n"
    "GEO:30.246447;-92.006407\r\n"
    "URL:https://calendar.example.test/event/grove-exact-pin\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)
VERIFIED_GROVE = {
    "name": "Grove Community Garden", "address": "1605 Moss Street",
    "city": "Eugene", "region": "OR", "country": "US",
    "postal_code": "97403", "lat": 44.0398546, "lon": -123.0659835,
}
WRONG_GEOCODE = (30.246447, -92.006407)
geo_calls = []


def _build_exact_pin(directory, *, exact):
    path = os.path.join(directory, "events.json")
    venue = dict(VERIFIED_GROVE)
    if exact:
        venue["coords_exact"] = True
    source = {
        "name": "Grove Community Garden", "url": "https://calendar.example.test/grove.ics",
        "category": "volunteer",
        "location_overrides": [{"location": "Grove Community Garden", "venue": venue}],
    }
    persisted = EventStore(path)
    with patch.object(ICS, "_fetch_ics", return_value=(EXACT_PIN_ICS, "200")):
        ICS.ingest_ics(persisted, None, source)
    persisted.save()
    record = next(iter(persisted.records.values()))
    row = build_rows(path, "host", geo_session=object())[0]
    return record, row


def _wrong_geocode(_session, address_tuples):
    geo_calls.append(list(address_tuples))
    return {address: WRONG_GEOCODE for address in address_tuples}


with tempfile.TemporaryDirectory() as td, \
     patch("mapsee_supabase_sync.batch_geocode", side_effect=_wrong_geocode):
    exact_dir = os.path.join(td, "exact")
    ordinary_dir = os.path.join(td, "ordinary")
    os.makedirs(exact_dir)
    os.makedirs(ordinary_dir)
    exact_record, exact_row = _build_exact_pin(exact_dir, exact=True)
    ordinary_record, ordinary_row = _build_exact_pin(ordinary_dir, exact=False)
check("an exact LOCATION override corrects stale raw GEO in the normalized event",
      exact_record["latitude"] == VERIFIED_GROVE["lat"]
      and exact_record["longitude"] == VERIFIED_GROVE["lon"]
      and exact_record["coords_exact"] is True,
      (exact_record.get("latitude"), exact_record.get("longitude"), exact_record.get("coords_exact")))
check("production build_rows bypasses geocoding and preserves opted-in venue coordinates",
      geo_calls[0] == [] and exact_row["lat"] == VERIFIED_GROVE["lat"]
      and exact_row["lon"] == VERIFIED_GROVE["lon"],
      (geo_calls, exact_row.get("lat"), exact_row.get("lon")))
check("an unmarked venue override retains existing geocoder behavior",
      ordinary_record["coords_exact"] is False
      and bool(geo_calls[1])
      and ordinary_row["lat"] == WRONG_GEOCODE[0]
      and ordinary_row["lon"] == WRONG_GEOCODE[1],
      (ordinary_record.get("coords_exact"), geo_calls, ordinary_row.get("lat"), ordinary_row.get("lon")))
check("coordinate opt-in leaves source event identity and info link unchanged",
      exact_record["sources"] == ordinary_record["sources"]
      and exact_row["external_id"] == ordinary_row["external_id"]
      and "Tickets / info: https://calendar.example.test/event/grove-exact-pin" in exact_row["description"],
      (exact_record.get("sources"), ordinary_record.get("sources"), exact_row.get("description")))

# --- a source's line counts the Photon requests it paid for, and the misses ----
# Misses are not cached, so the same one is paid on every run (up to the 5,000
# GEOCODE_MAX_NEW budget at ~1.1 s each); until this a run's log could not say
# which source spent them.
class PhotonSession:
    def __init__(self):
        self.queries = []

    def get(self, _url, params, timeout):
        self.queries.append(params["q"])
        hit = params["q"].startswith("Qx Photon Test Hall")
        return type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"features": [{"geometry": {"coordinates": [-122.3, 47.6]}}] if hit else []},
        })()


PHOTON_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:p1\r\nSUMMARY:Talk\r\nDTSTART:20991010T170000Z\r\n"
    "LOCATION:Qx Photon Test Hall\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:p2\r\nSUMMARY:Walk\r\nDTSTART:20991011T170000Z\r\n"
    "LOCATION:Qx Photon Nowhere\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:p3\r\nSUMMARY:Talk again\r\nDTSTART:20991012T170000Z\r\n"
    "LOCATION:Qx Photon Test Hall\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
photon_session = PhotonSession()
with patch.object(ICS, "_fetch_ics", return_value=(PHOTON_ICS, "200")), \
     patch.object(ICS, "geocode_allowed", return_value=True), \
     patch.object(ICS.time, "sleep"), \
     patch.dict(ICS._GEO_CACHE, {}), \
     redirect_stdout(io.StringIO()) as out:
    ICS.ingest_ics(VenueStore(), photon_session, {"name": "photon", "url": "x"})
check("a source's line says how long it took and how many Photon lookups it sent, and missed",
      "[ics] photon: kept 2 of 3 VEVENTs" in out.getvalue()
      and " in 0.0s; 2 Photon lookup(s), 1 missed" in out.getvalue()
      and len(photon_session.queries) == 2, (out.getvalue(), photon_session.queries))
with patch.object(ICS, "_fetch_ics", return_value=(PHOTON_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_no_hit_geocoder), \
     redirect_stdout(io.StringIO()) as out:
    ICS.ingest_ics(VenueStore(), None, {"name": "cached", "url": "x"})
check("...and a source that sent none says nothing about Photon",
      "in 0.0s" in out.getvalue() and "Photon" not in out.getvalue(), out.getvalue())


# --- a LOCATION on several lines is asked line by line -------------------------
# Shift2Bikes writes "Name\nStreet\nnotes". Joined, Photon placed 21 of 152
# upcoming VEVENTs; line by line, under a check on Photon's own answer, 125
# (2026-10-25). The answers below are the shapes Photon gave for its LOCATIONs.
def _feature(lat, lon, **props):
    return {"geometry": {"coordinates": [lon, lat]}, "properties": props}


class AnswerSession:
    """Photon stand-in: one answer per query (without the suffix), or none."""
    def __init__(self, answers, suffix):
        self.answers, self.suffix, self.queries = answers, suffix, []

    def get(self, _url, params, timeout):
        query = params["q"][:-len(self.suffix)] if params["q"].endswith(self.suffix) else params["q"]
        self.queries.append(query)
        feature = self.answers.get(query)

        class R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"features": [feature] if feature else []}
        return R()


# Read through getattr, so a checkout without the rule FAILS line by line.
_location_lines = getattr(ICS, "_location_lines", lambda _ev: [])
line_attempts = getattr(ICS, "line_attempts", lambda _loc, _lines: [])
_street_confirms = getattr(ICS, "_street_confirms", lambda _line, _props: None)
_name_confirms = getattr(ICS, "_name_confirms", lambda _name, _props: None)


def ask(geocode, loc, lines):
    try:
        return geocode(loc, lines)
    except TypeError:                                 # a geocoder that takes the LOCATION alone
        return "no lines"


SUFFIX = ", Portland, OR"
BOTELLON = "Bar Botellon\\n606 NE Davis St\\, Portland\\, OR 97232\\nmeet inside\\, drink wine\\, eat tapas"
BOTELLON_JOINED = "Bar Botellon 606 NE Davis St, Portland, OR 97232 meet inside, drink wine, eat tapas"
check("a LOCATION's lines are read from the escaped newlines; one line gives none",
      _location_lines({"LOCATION": (BOTELLON, {})})
      == ["Bar Botellon", "606 NE Davis St, Portland, OR 97232", "meet inside, drink wine, eat tapas"]
      and _location_lines({"LOCATION": ("Polish Home, 1714 18th Ave, Seattle", {})}) == []
      and _location_lines({"LOCATION": ("111 Alamo Plaza<br>San Antonio", {})}) == []
      and ICS._location({"LOCATION": (BOTELLON, {})}) == BOTELLON_JOINED,
      _location_lines({"LOCATION": (BOTELLON, {})}))
check("several lines: name and street, the street alone (checked), the joined string, the name (checked)",
      line_attempts(BOTELLON_JOINED, _location_lines({"LOCATION": (BOTELLON, {})})) == [
          ("Bar Botellon, 606 NE Davis St, Portland, OR 97232", None),
          ("606 NE Davis St, Portland, OR 97232", "street"),
          (BOTELLON_JOINED, None),
          ("Bar Botellon", "name")],
      line_attempts(BOTELLON_JOINED, _location_lines({"LOCATION": (BOTELLON, {})})))
check("a placeholder line and a link are never asked; a one-word or generic name is never asked alone",
      line_attempts("x", ["Salmon Street Springs", "TBA"]) == [("x", None), ("Salmon Street Springs", "name")]
      and line_attempts("y", ["205 path", "https://goo.gl/maps/Y8sy"]) == [("205 path", "street"), ("y", None)]
      and line_attempts("z", ["NoPo", "Columbia Park"]) == [("z", None)]
      and line_attempts("w", ["Community Center", "Gym"]) == [("w", None)]
      and line_attempts("v", ["Pioneer Courthouse Square (upper level)", "SW Broadway & SW Morrison St"])
      == [("v", None), ("Pioneer Courthouse Square", "name")],
      line_attempts("x", ["Salmon Street Springs", "TBA"]))
check("the street check takes the house it names and refuses another number, street or direction",
      _street_confirms("606 NE Davis St, Portland, OR 97232",
                           {"housenumber": "606", "street": "Northeast Davis Street"})
      and _street_confirms("16365 NW Twin Oaks Dr Beaverton, OR 97006",
                               {"housenumber": "16365", "street": "Northwest Twin Oaks Drive"})
      and not _street_confirms("606 NE Davis St", {"housenumber": "815", "street": "Northeast Davis Street"})
      and not _street_confirms("1120 SE Madison St", {"housenumber": "1120", "street": "Southwest 5th Avenue"})
      and not _street_confirms("700 NE Multnomah St 3rd floor",
                                   {"housenumber": "700", "street": "Southwest 3rd Avenue"})
      and not _street_confirms("3947 N Williams Ave", {"street": "North Williams Avenue"}))
check("the name check wants exactly the name's words",
      _name_confirms("Cycle Town Coffee Roasters", {"name": "Cycle Town Coffee Roasters"})
      and _name_confirms("Bar Botellon", {"name": "BAR Botellón"})
      and not _name_confirms("Go Lloyd", {"name": "Lloyd District"})
      and not _name_confirms("Xplore Food Carts", {"name": "Portland Mercado Food Carts"}))

ANSWERS = {
    "Bar Botellon, 606 NE Davis St, Portland, OR 97232":
        _feature(45.52425, -122.65953, name="BAR Botellón", housenumber="606", street="Northeast Davis Street"),
    "606 NE Davis St, Portland, OR 97232":
        _feature(45.5245, -122.65719, name="Legacy Hospice", housenumber="815", street="Northeast Davis Street"),
    "Cycle Town Coffee Roasters": _feature(45.53549, -122.68825, name="Cycle Town Coffee Roasters"),
    "700 NE Multnomah St 3rd floor": _feature(45.5179, -122.67537, housenumber="700", street="Southwest 3rd Avenue"),
    "Go Lloyd": _feature(45.53138, -122.66008, name="Lloyd District"),
    "TBD TBD": _feature(45.70807, -121.51359, name="TBD - Female Figure"),
}
session = AnswerSession(ANSWERS, SUFFIX)
cache = {}
with patch.object(ICS, "_GEO_CACHE", cache), \
     patch.object(ICS, "geocode_allowed", return_value=True), \
     patch.object(ICS.time, "sleep"):
    geocode = ICS.make_location_geocoder(session, SUFFIX)
    lines = ["Cycle Town Coffee Roasters", "NW 16th and Thurman", "meet at the parking lot"]
    joined = " ".join(lines)
    town = ask(geocode, joined, lines)
    check("an intersection is not a street line: the joined string, then the name, which Photon confirms",
          town == (45.53549, -122.68825) and session.queries == [joined, "Cycle Town Coffee Roasters"]
          and cache.get(f"{joined}|{SUFFIX}".lower()) == town, (town, session.queries))
    check("...and its answer is cached under the LOCATION, never under the checked name",
          f"cycle town coffee roasters|{SUFFIX}".lower() not in cache, sorted(cache))
    session.queries.clear()
    lloyd = ["Go Lloyd", "700 NE Multnomah St 3rd floor", "Conference room is around the corner"]
    got = ask(geocode, " ".join(lloyd), lloyd)
    check("a street answer at another street and a name answer at another place are both refused",
          got == (None, None) and session.queries == ["Go Lloyd, 700 NE Multnomah St 3rd floor",
                                                      "700 NE Multnomah St 3rd floor", " ".join(lloyd), "Go Lloyd"]
          and not any("700 ne multnomah" in k and "go lloyd" not in k for k in cache), (got, session.queries))
    session.queries.clear()
    again = ask(geocode, " ".join(lloyd), lloyd)
    check("what missed is not asked again in the same run (one request per attempt, not per VEVENT)",
          again == (None, None) and session.queries == [], session.queries)
    session.queries.clear()
    bot = _location_lines({"LOCATION": (BOTELLON, {})})
    got = ask(geocode, BOTELLON_JOINED, bot)
    check("name and street answer first, so the hospice at 815 is never asked for",
          got == (45.52425, -122.65953) and session.queries == ["Bar Botellon, 606 NE Davis St, Portland, OR 97232"],
          (got, session.queries))

SHIFT_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:s1\r\nSUMMARY:Tapas Tuesday\r\nDTSTART:20991010T020000Z\r\n"
    f"LOCATION:{BOTELLON}\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:s2\r\nSUMMARY:Mystery Ride\r\nDTSTART:20991011T020000Z\r\n"
    "LOCATION:TBD\\nTBD\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:s3\r\nSUMMARY:Path Ride\r\nDTSTART:20991012T020000Z\r\n"
    "LOCATION:205 path\\nhttps://goo.gl/maps/Y8sy\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:s4\r\nSUMMARY:Bike Social Hour\r\nDTSTART:20991013T020000Z\r\n"
    "LOCATION:Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
session = AnswerSession(ANSWERS, SUFFIX)
store = VenueStore()
with patch.object(ICS, "_fetch_ics", return_value=(SHIFT_ICS, "200")), \
     patch.object(ICS, "_GEO_CACHE", {}), \
     patch.object(ICS, "geocode_allowed", return_value=True), \
     patch.object(ICS.time, "sleep"), \
     redirect_stdout(io.StringIO()) as out:
    ICS.ingest_ics(store, session, {"name": "shift", "url": "https://shift.test/cal.ics", "geocode_suffix": SUFFIX,
                                    "location_lines": True})
row = next((r for r in store.rows if r.source_id == "s1"), None)
check("a multi-line LOCATION is placed, and keeps the joined text as its place name and identity",
      row is not None and (row.latitude, row.longitude) == (45.52425, -122.65953)
      and row.venue_name == BOTELLON_JOINED
      and row.fingerprint == _fp("Tapas Tuesday", "2099-10-10", BOTELLON_JOINED),
      row and (row.latitude, row.venue_name))
check("'TBD\\nTBD' is a placeholder, never sent (Photon put 'TBD TBD' in Hood River), and a link is never sent",
      not any(q.startswith("TBD") for q in session.queries)
      and not any("://" in q for q in session.queries) and [r.source_id for r in store.rows] == ["s1"],
      (session.queries, [r.source_id for r in store.rows]))
check("a LOCATION on one line is asked exactly as before (whole, address half, venue)",
      session.queries[-3:] == ["Elmer W. Oliver Nature Park - 1650 Matlock Road Mansfield TX 76063",
                               "1650 Matlock Road Mansfield TX 76063", "Elmer W. Oliver Nature Park"],
      session.queries)

# Without location_lines a multi-line LOCATION is asked exactly as before:
# the joined string through location_attempts, never a line on its own.
session = AnswerSession(ANSWERS, SUFFIX)
store = VenueStore()
with patch.object(ICS, "_fetch_ics", return_value=(SHIFT_ICS, "200")), \
     patch.object(ICS, "_GEO_CACHE", {}), \
     patch.object(ICS, "geocode_allowed", return_value=True), \
     patch.object(ICS.time, "sleep"), \
     redirect_stdout(io.StringIO()):
    ICS.ingest_ics(store, session, {"name": "shift-off", "url": "https://shift.test/cal.ics", "geocode_suffix": SUFFIX})
check("a source that did not opt in asks no line on its own (the joined string, as it always has)",
      BOTELLON_JOINED in session.queries
      and "Bar Botellon, 606 NE Davis St, Portland, OR 97232" not in session.queries
      and "606 NE Davis St, Portland, OR 97232" not in session.queries, session.queries)

# Opted in, the lines go first and the joined string's own attempts follow,
# so the CivicPlus address half is still asked when the lines miss.
HALF = "1650 Matlock Road  Mansfield TX 76063 Pavilion 2"     # location_attempts' address half of the joined string
session = AnswerSession({HALF: _feature(32.56, -97.14)}, SUFFIX)
with patch.object(ICS, "_GEO_CACHE", {}), \
     patch.object(ICS, "geocode_allowed", return_value=True), \
     patch.object(ICS.time, "sleep"):
    geocode = ICS.make_location_geocoder(session, SUFFIX)
    civic = ["Elmer W. Oliver Nature Park - 1650 Matlock Road  Mansfield TX 76063", "Pavilion 2"]
    got = ask(geocode, " ".join(civic), civic)
check("...and when every line misses, the joined string's address half is still asked and places it",
      got == (32.56, -97.14) and session.queries[-1] == HALF, (got, session.queries))

# --- one UID on several dates is one row per date ------------------------------
# Repair Cafe Toronto expands its series as RECURRENCE-ID instances of one UID:
# 31 VEVENTs kept and 18 rows written, each survivor carrying the LAST date's
# fingerprint and the FIRST date's start (2026-10-23).
UID_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:rc-1\r\nSUMMARY:Port Union Repair Cafe\r\nDTSTART:20991114T150000Z\r\n"
    "RECURRENCE-ID:20991114T150000Z\r\nGEO:43.78;-79.14\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:rc-1\r\nSUMMARY:Port Union Repair Cafe\r\nDTSTART:20991212T150000Z\r\n"
    "RECURRENCE-ID:20991212T150000Z\r\nGEO:43.78;-79.14\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:rc-1\r\nSUMMARY:Port Union Repair Cafe\r\nDTSTART:20991010T150000Z\r\n"
    "GEO:43.78;-79.14\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:rc-1\r\nSUMMARY:Port Union Repair Cafe\r\nDTSTART:20991010T180000Z\r\n"
    "GEO:43.78;-79.14\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:rc-1\r\nSUMMARY:Port Union Repair Cafe\r\nDTSTART:20991109T150000Z\r\n"
    "STATUS:CANCELLED\r\nGEO:43.78;-79.14\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:rc-2\r\nSUMMARY:OCAD Repair Cafe\r\nDTSTART:20991028T150000Z\r\n"
    "GEO:43.65;-79.39\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
with tempfile.TemporaryDirectory() as tmp:
    store = EventStore(os.path.join(tmp, "uid.json"))
    with patch.object(ICS, "_fetch_ics", return_value=(UID_ICS, "200")), \
         redirect_stdout(io.StringIO()) as out:
        kept = ICS.ingest_ics(store, None, {"name": "rc", "url": "https://rc.test/feed"})
    recs = list(store.records.values())
    ids = sorted(s["source_id"] for r in recs for s in r["sources"])
    check("each date of a shared UID is its own row, and nothing is re-keyed",
          kept == 5 and len(recs) == 4 and store.stats["rekeyed"] == 0,
          (kept, len(recs), store.stats["rekeyed"]))
    check("the first date keeps the bare UID; later dates are '<UID>/<date>'; one date, one key",
          ids == ["rc-1", "rc-1/2099-11-14", "rc-1/2099-12-12", "rc-2"], ids)
    check("every row's start is the date its fingerprint was made from",
          all(r["fingerprint"] == _fp(r["name"], r["start_utc"][:10], None) for r in recs),
          [(r["name"], r["start_utc"]) for r in recs])
    check("a later date's cancellation tombstones that date, and the first date's row stays",
          set(store.tombstones) == {_fp("Port Union Repair Cafe", "2099-11-09", None)}
          and _fp("Port Union Repair Cafe", "2099-10-10", None) in store.records, sorted(store.tombstones))
    check("the log counts the dates keyed this way",
          "; 2 keyed by date (a UID an earlier date already used)" in out.getvalue(), out.getvalue())


# --- a year ahead and no further, unless the source says otherwise ----------
# The 2026-10-26 store held 7,574 of 134,814 ics rows starting more than 365
# days out, from 224 rule-expanded calendars (Roeland Park 293 of 300, to 2069).
# Dated from today, because the horizon is. A far row is counted, never kept,
# never geocoded and never takes a near row's `limit` slot; a far cancellation
# still tombstones the row stored before the horizon existed.
from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402


def _day(n):
    return (_dt.now(_tz.utc) + _td(days=n)).strftime("%Y%m%d")


def _horizon_feed(rows):
    return "BEGIN:VCALENDAR\r\n" + "".join(
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{uid}\r\nDTSTART;VALUE=DATE:{_day(n)}\r\n{extra}END:VEVENT\r\n"
        for uid, n, extra in rows) + "END:VCALENDAR\r\n"


HORIZON_ICS = _horizon_feed([                     # newest first, as CivicPlus writes them
    ("far-cancelled", 500, "STATUS:CANCELLED\r\nLOCATION:Far Hall cancelled\r\n"),
    ("far-400", 400, "LOCATION:Far Hall 400\r\n"),
    ("far-366", 366, "LOCATION:Far Hall 366\r\n"),
    ("edge-365", 365, "GEO:47.6;-122.3\r\n"),
    ("near-30", 30, "GEO:47.6;-122.3\r\n"),
    ("past", -3, "GEO:47.6;-122.3\r\n"),
])
far_geocodes = []


def _far_geocoder(_session, _suffix):
    def geocode(loc):
        far_geocodes.append(loc)
        return 47.6, -122.3
    return geocode


with patch.object(ICS, "HORIZON_DAYS", HORIZON_DAYS), \
     patch.object(ICS, "_fetch_ics", return_value=(HORIZON_ICS, "200")), \
     patch.object(ICS, "make_location_geocoder", side_effect=_far_geocoder):
    with redirect_stdout(io.StringIO()) as out:
        hz = VenueStore()
        hz_kept = ICS.ingest_ics(hz, None, {"name": "civicplus", "url": "x", "limit": 3})
    check("the default horizon is 365 days", HORIZON_DAYS == 365, HORIZON_DAYS)
    check("within 365 days is kept, the 365th day included; beyond it is not",
          hz_kept == 2 and sorted(r.name for r in hz.rows) == ["edge-365", "near-30"],
          (hz_kept, [r.name for r in hz.rows]))
    check("a far row does not take the slot `limit` left: 2 kept of a limit of 3",
          len(hz.rows) == 2, [r.name for r in hz.rows])
    check("nothing beyond the horizon is geocoded", far_geocodes == [], far_geocodes)
    check("a cancellation beyond the horizon still goes to store.cancel",
          [r.source_id for r in hz.cancelled] == ["far-cancelled"], [r.source_id for r in hz.cancelled])
    check("the log counts what the horizon refused",
          "; 2 beyond the 365-day horizon" in out.getvalue(), out.getvalue())

    with redirect_stdout(io.StringIO()) as out:
        hz = VenueStore()
        hz_kept = ICS.ingest_ics(hz, None, {"name": "civicplus", "url": "x", "within_days": 400})
    check("a source's within_days moves its horizon out",
          sorted(r.name for r in hz.rows) == ["edge-365", "far-366", "far-400", "near-30"]
          and "beyond the" not in out.getvalue(), ([r.name for r in hz.rows], out.getvalue()))

    with redirect_stdout(io.StringIO()) as out:
        hz = VenueStore()
        ICS.ingest_ics(hz, None, {"name": "santa-rosa", "url": "x", "within_days": 90})
    check("...and in (a 90-day volunteer calendar keeps 90 days)",
          [r.name for r in hz.rows] == ["near-30"]
          and "; 3 beyond the 90-day horizon" in out.getvalue(), ([r.name for r in hz.rows], out.getvalue()))

    FAR_ONLY = _horizon_feed([("far-400", 400, "GEO:47.6;-122.3\r\n"), ("past", -3, "GEO:47.6;-122.3\r\n")])
    with patch.object(ICS, "_fetch_ics", return_value=(FAR_ONLY, "200")), \
         redirect_stdout(io.StringIO()) as out:
        hz = VenueStore()
        ICS.ingest_ics(hz, None, {"name": "far-only", "url": "x"})
    check("a feed of past and far rows is not reported as wholly past",
          not hz.rows and "in the PAST" not in out.getvalue()
          and "; 1 beyond the 365-day horizon" in out.getvalue(), out.getvalue())

if fails:
    raise SystemExit(f"{len(fails)} ICS test(s) failed: {', '.join(fails)}")
print("all ICS tests passed")
