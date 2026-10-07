"""A cancelled event or a closed centre must come OFF the map, and only ours.

The owner's line (2026-10-05): "we don't want users to go to an event or center
that is closed." An upsert cannot delete, so the adapters that could see a
cancellation only stopped writing the record again; the row from last week stayed.
This gate pins the four halves that reach that row, all offline:

  * EventStore: cancel() records a TOMBSTONE under the fingerprint the live row
    has (events.external_id), a "CANCELLED - X" title tombstones X when its
    recipe can be PROVED, and mark_complete() records a whole read and its window.
  * the sync: tombstones cancel + hide (conditions in the PATCH filter), a
    whole read's missing sessions are cancelled behind a circuit breaker, a row
    listed live again is un-cancelled (only ours, only a day later), and an
    --only-new day now writes time changes and detail refreshes.
  * Ticketmaster's dates.status.code.
  * mapsee_uncancel.py, the escape hatch.

One printed line per case; exits non-zero on any failure. No request leaves this
process: PostgREST is a dictionary below.
"""
import io
import json
import os
import sys
import tempfile
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

os.environ.setdefault("MAPSEE_TODAY", "20261005")

import mapsee_ingest as ING                               # noqa: E402
import mapsee_supabase_sync as sync                       # noqa: E402
import mapsee_uncancel as UNC                             # noqa: E402
from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


DAY3 = NOW + timedelta(days=3)
VENUE = "Main Library"


def ev(name, sid, source="ics:test", when=DAY3, venue=VENUE, fp=None, **kw):
    e = NormalizedEvent(source=source, source_id=sid, name=name, start_utc=stamp(when),
                        start_local=stamp(when)[:19], venue_name=venue, latitude=47.6,
                        longitude=-122.3, **kw)
    e.fingerprint = fp or make_fingerprint(name, stamp(when)[:10], venue)
    return e


TMP = tempfile.TemporaryDirectory()


def store_path(name):
    return os.path.join(TMP.name, name)


def quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


# --------------------------------------------------------------------------- #
print("EventStore.cancel: a tombstone under the fingerprint the live row has")
path = store_path("a.json")
st = EventStore(path)
live = ev("Family Storytime", "a")
check("the storytime is stored", st.upsert(live), "added")
check("its cancellation drops the live record (the publisher's later word)",
      (st.cancel(ev("Family Storytime", "a"), "STATUS:CANCELLED"), live.fingerprint in st.records),
      ("cancelled", False))
check("  ...counted as beating a live record", st.stats["cancel_over_live"], 1)
check("a live upsert AFTER the tombstone is refused too", st.upsert(ev("Family Storytime", "a")), "cancelled")
check("  ...and it is not back in the store", live.fingerprint in st.records, False)
other = ev("Chess Club", "b")
st.upsert(other)
st.save()
raw = json.load(open(path, encoding="utf-8"))
check("saved as a top-level 'tombstones' list, keyed by fingerprint",
      [t["fingerprint"] for t in raw["tombstones"]], [live.fingerprint])
check("  ...with its source, source id and reason", {k: raw["tombstones"][0][k] for k in
      ("source", "source_id", "reason")}, {"source": "ics:test", "source_id": "a", "reason": "STATUS:CANCELLED"})
check("'events' never holds a tombstoned fingerprint (every old reader is unaffected)",
      [e["fingerprint"] for e in raw["events"]], [other.fingerprint])
again = EventStore(path)
check("a second adapter in the same run loads the tombstone back",
      (list(again.tombstones), again.upsert(ev("Family Storytime", "z", source="ics:other"))),
      ([live.fingerprint], "cancelled"))
check("a legacy fingerprint travels with the tombstone",
      EventStore(store_path("leg.json")).cancel(ev("X", "x", legacy_fingerprints=["old1"]), "c"), "cancelled")
leg = EventStore(store_path("leg2.json"))
leg.cancel(ev("X", "x", legacy_fingerprints=["old1", ""]), "c")
check("  ...stored under 'legacy'", leg.tombstones[ev("X", "x").fingerprint].get("legacy"), ["old1"])
plain = EventStore(store_path("plain.json"))
plain.upsert(ev("Lane Swim", "p"))
plain.save()
check("a store nothing cancelled saves no new keys",
      sorted(json.load(open(store_path("plain.json"), encoding="utf-8"))), ["_meta", "events"])
check("no fingerprint, no tombstone", EventStore(store_path("n.json")).cancel(
      NormalizedEvent(source="ics:t", source_id="1", name="Y"), "c"), "unkeyed")

print()
print("Open-registration platforms are not trusted to cancel")
mob = EventStore(store_path("mob.json"))
mob.upsert(ev("Big Concert", "real", source="ticketmaster"))
check("a Mobilizon 'cancelled' copy of a real event's title is refused",
      (mob.cancel(ev("Big Concert", "fake", source="mobilizon:instance"), "CANCELLED"),
       len(mob.records), mob.tombstones), ("untrusted", 1, {}))
check("  ...and so is Gancio's notice title", (mob.upsert(ev("CANCELLED - Big Concert", "g", source="gancio:x")),
      mob.tombstones, mob.stats["cancel_untrusted"]), ("notice", {}, 2))

print()
print("A cancel-and-relist is not a cancellation (Chicago Public Library, 2026-10-05)")
OLD_ID, NEW_ID = "chipublib:6a9addd34995480655ed9088", "chipublib:6abc199f0f56a0002cd4d3c3"
BC = "bibliocommons:chipublib"
r1 = EventStore(store_path("relist1.json"))
r1.upsert(ev("Story and Play Time", NEW_ID, source=BC))
check("live first: the old listing's isCancelled keeps the relisted session live, no tombstone",
      (r1.cancel(ev("Story and Play Time", OLD_ID, source=BC), "isCancelled"), len(r1.records), r1.tombstones,
       r1.stats["cancel_beside_relist"]), ("relisted", 1, {}, 1))
r2 = EventStore(store_path("relist2.json"))
r2.cancel(ev("Story and Play Time", OLD_ID, source=BC), "isCancelled")
check("tombstone first: the relisted session is written and the tombstone dropped",
      (r2.upsert(ev("Story and Play Time", NEW_ID, source=BC)), len(r2.records), r2.tombstones,
       r2.stats["cancelled"]), ("added", 1, {}, 0))
r2.save()
check("  ...so the sync targets nothing for it",
      sync.cancellation_targets(sync.cancellation_inputs(store_path("relist2.json")), {}), {})
r3 = EventStore(store_path("relist3.json"))
r3.upsert(ev("Story and Play Time", NEW_ID, source=BC))
check("another source's cancellation still beats the live listing",
      (r3.cancel(ev("Story and Play Time", "x", source="ics:chipublib"), "STATUS:CANCELLED"), len(r3.records)),
      ("cancelled", 0))

print()
print("Meetup and Eventbrite cancel only what they wrote, in the store")
o1 = EventStore(store_path("own1.json"))
o1.upsert(ev("Big Show", "t1", source="tribe", ticket_url="https://venue.example/big"))
check("a Meetup cancellation beside another family's live listing keeps the listing, no tombstone",
      (o1.cancel(ev("Big Show", "m1", source="meetup", ticket_url="https://www.meetup.com/g/events/1/"),
                 "CANCELLED"), len(o1.records), o1.tombstones), ("unowned", 1, {}))
o2 = EventStore(store_path("own2.json"))
o2.cancel(ev("Big Show", "e1", source="eventbrite", ticket_url="https://www.eventbrite.com/e/1"), "is_cancelled")
check("an Eventbrite tombstone carries its listing URL (the sync's filter)",
      o2.tombstones[ev("Big Show", "e1").fingerprint].get("url"), "https://www.eventbrite.com/e/1")
check("  ...and gives way to another family's live listing that arrives after it",
      (o2.upsert(ev("Big Show", "t1", source="tribe")), o2.tombstones), ("added", {}))
check("an own-only cancellation with no URL names no row: refused",
      EventStore(store_path("own3.json")).cancel(ev("Big Show", "m2", source="meetup"), "CANCELLED"), "unowned")

print()
print("A notice title tombstones the event it names, when the recipe is proved")
n = EventStore(store_path("notice.json"))
real = ev("Family Storytime", "a")
n.upsert(real)
notice = ev("CANCELLED - Family Storytime", "b")
check("'CANCELLED - Family Storytime' is still refused as a notice", n.upsert(notice), "notice")
check("  ...and tombstones the storytime's own fingerprint", list(n.tombstones), [real.fingerprint])
check("  ...which takes the live storytime out of this store", real.fingerprint in n.records, False)
hm = DAY3.strftime("%H:%M")
tor = NormalizedEvent(source="toronto-rec", source_id="t", name="Lane Swim CANCELLED",
                      start_utc=stamp(DAY3), start_local=stamp(DAY3)[:19], venue_name="Antibes CC")
tor.fingerprint = make_fingerprint(f"Lane Swim CANCELLED {hm}", stamp(DAY3)[:10], "Antibes CC")
n.upsert(tor)
check("Toronto's recipe (title + HH:MM) is proved too",
      make_fingerprint(f"Lane Swim {hm}", stamp(DAY3)[:10], "Antibes CC") in n.tombstones, True)
odd = ev("CANCELLED - Mystery Walk", "m", fp="f" * 40)
n.upsert(odd)
check("an unprovable recipe cancels nothing, and is counted",
      (len(n.tombstones), n.stats["notice_unproven"]), (2, 1))
n.upsert(ev("Library Closed: Thanksgiving Day", "c"))
check("a closure names no event: no tombstone", len(n.tombstones), 2)
check("strip_notice keeps the event's own words",
      [ING.strip_notice(t) for t in ("Chess Club CANCELLED", "***ABGESAGT***  Pflanzen-Flohmarkt   ***ABGESAGT***",
                                     "(CANCELADO) Habitar la voz", "CANCELLED", "NATURE CENTER CLOSED")],
      ["Chess Club", "Pflanzen-Flohmarkt", "Habitar la voz", None, None])

print()
print("EventStore.mark_complete: a whole read, and its window rounded inward")
mc = EventStore(store_path("mc.json"))
mc.mark_complete("perfectmind", "2026-10-05", "2027-01-03", id_prefix="burnaby:")
mc.mark_complete("toronto-rec", datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc),
                 datetime(2027, 1, 3, tzinfo=timezone.utc))
check("a date 'from' is its latest start on earth (12:00Z), a date 'to' its earliest end (10:00Z)",
      (mc.complete_reads["perfectmind|burnaby:"]["from"], mc.complete_reads["perfectmind|burnaby:"]["to"]),
      ("2026-10-05T12:00:00Z", "2027-01-03T10:00:00Z"))
check("an aware edge is exact", (mc.complete_reads["toronto-rec"]["from"], mc.complete_reads["toronto-rec"]["to"]),
      ("2026-10-05T07:00:00Z", "2027-01-03T00:00:00Z"))
try:
    mc.mark_complete("x", "2026-10-05", "2026-10-04")
    check("an empty window is refused", False, True)
except ValueError:
    check("an empty window is refused", True, True)
mc.save()
check("saved as top-level 'complete_reads', loaded back",
      sorted(EventStore(store_path("mc.json")).complete_reads), ["perfectmind|burnaby:", "toronto-rec"])

print()
print("Ticketmaster: dates.status.code")


def tm(eid, code, local_time="19:30:00"):
    start = {"localDate": stamp(DAY3)[:10], "dateTime": stamp(DAY3)}
    if local_time:
        start["localTime"] = local_time
    return {"id": eid, "name": f"Show {eid}", "dates": {"start": start, "status": {"code": code}},
            "_embedded": {"venues": [{"name": "Arena", "location": {"latitude": "47.6", "longitude": "-122.3"},
                                      "city": {"name": "Seattle"}}]}}


class _R:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


tms = EventStore(store_path("tm.json"))
args = Namespace(classification=None, latlong="47.6,-122.3", radius=25, unit="miles", city=None, country=None,
                 start=None, end=None, within_days=90, size=100, max_pages=1)
page = {"_embedded": {"events": [tm("1", "onsale"), tm("2", "canceled"), tm("3", "postponed", local_time=None),
                                 tm("4", "rescheduled"), tm("5", "offsale")]},
        "page": {"totalPages": 1, "number": 0}}
with patch.object(ING, "http_get", return_value=_R(page)):
    quiet(ING.ingest_ticketmaster, tms, None, None, args, "k")
check("canceled and postponed (even with its time gone) are tombstones; the rest are live",
      (sorted(t["source_id"] for t in tms.tombstones.values()),
       sorted(r["sources"][0]["source_id"] for r in tms.records.values())),
      (["2", "3"], ["1", "4", "5"]))

# --------------------------------------------------------------------------- #
print()
print("The sync's pieces: time changes, our cancellations, absence")
base = {"fingerprint": "fp", "start_utc": "2099-10-03T16:00:00Z", "latitude": 47.6, "longitude": -122.3}
check("the same instant spelled with an offset is not a change",
      sync.time_changed(base, {"starts_at": "2099-10-03T16:00:00+00:00"}), False)
check("an hour later is", sync.time_changed(base, {"starts_at": "2099-10-03T15:00:00+00:00"}), True)
check("a new REAL end is", sync.time_changed(dict(base, end_utc="2099-10-03T18:00:00Z"),
                                            {"starts_at": "2099-10-03T16:00:00+00:00",
                                             "ends_at": "2099-10-03T17:00:00+00:00"}), True)
check("no real end: the default end follows start, so it is not compared",
      sync.time_changed(base, {"starts_at": "2099-10-03T16:00:00+00:00", "ends_at": "2099-10-03T23:00:00+00:00"}),
      False)
check("a naive time with no coordinates is left to Wednesday (never a mass rewrite)",
      sync.time_changed({"start_local": "2099-10-03T09:00:00"}, {"starts_at": "2099-10-03T01:00:00+00:00"}), False)
check("a standing row's window is the database's to roll",
      sync.time_changed(dict(base, recurring_days={"mon": "9-5"}), {"starts_at": "2099-01-01T00:00:00+00:00"}), False)
check("a column the server did not return is unknown, not changed", sync.time_changed(base, {}), False)
old = stamp(NOW - timedelta(hours=40))
young = stamp(NOW - timedelta(hours=2))
check("our cancellation, nobody saying so again for 40 h: lift it",
      sync.import_cancelled({"cancelled_at": old, "hidden_at": old}, NOW), True)
check("our cancellation from this run or the last: hold it (another store may still say so)",
      sync.import_cancelled({"cancelled_at": young, "hidden_at": young}, NOW), False)
check("21 h after the canceller's stamp (a live store syncing 4 h earlier in the next day's run): "
      "still held, so the two stores do not flap it daily",
      sync.import_cancelled({"cancelled_at": stamp(NOW - timedelta(hours=21)), "hidden_at": old}, NOW), False)
check("hidden with no cancelled_at (a take-down, a prune) is never ours",
      sync.import_cancelled({"cancelled_at": None, "hidden_at": old}, NOW), False)
check("cancelled_at with the row no longer hidden is not a cancellation to lift",
      sync.import_cancelled({"cancelled_at": old, "hidden_at": None}, NOW), False)
check("hidden again ON TOP of our cancellation (a take-down that left cancelled_at in place): the stamps "
      "differ, so it is not ours to lift - whatever procedure the person followed",
      sync.import_cancelled({"cancelled_at": old, "hidden_at": stamp(NOW - timedelta(hours=35))}, NOW), False)


def manifest_inputs(complete, live, units, tombstones=(), standing=None):
    return {"tombstones": {t: {"source": "s"} for t in tombstones}, "complete": complete,
            "live": set(live), "units": units, "standing": standing or {u: set() for u in complete}}


W = {"source": "toronto-rec", "id_prefix": "", "from": stamp(NOW - timedelta(hours=1)),
     "to": stamp(NOW + timedelta(days=90))}
prev_starts = {f"s{i}": stamp(NOW + timedelta(days=1 + i % 60)) for i in range(40)}
prev_starts.update({"soon": stamp(NOW + timedelta(minutes=90)), "edge": stamp(NOW + timedelta(days=89, hours=12)),
                    "naive": (NOW + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")})
prev = {"toronto-rec": dict(W, starts=prev_starts, standing=[])}
live_now = [f"s{i}" for i in range(40) if i not in (3, 7)]
inp = manifest_inputs({"toronto-rec": W}, live_now, {"toronto-rec": {}})
absent, lines, warns = sync.absent_fingerprints(prev, inp, NOW)
check("two sessions gone from a whole read are absent, and so is a naive time well inside the window; "
      "one starting within 2 h and one on the window's last day are not judged",
      sorted(absent), ["naive", "s3", "s7"])
inp_t = manifest_inputs({"toronto-rec": W}, live_now, {"toronto-rec": {}}, tombstones=["s3"])
check("a tombstoned one is the tombstone's, not absence's", sorted(sync.absent_fingerprints(prev, inp_t, NOW)[0]),
      ["naive", "s7"])
check("no manifest: nothing is absent", sync.absent_fingerprints(None, inp, NOW)[0], {})
check("a source not read whole this run: nothing is absent",
      sync.absent_fingerprints(prev, manifest_inputs({}, live_now, {}), NOW)[0], {})
gone_many = manifest_inputs({"toronto-rec": W}, [f"s{i}" for i in range(30)], {"toronto-rec": {}})
a2, _, w2 = sync.absent_fingerprints(prev, gone_many, NOW)
check("CIRCUIT BREAKER: 11 of 41 gone at once cancels none, and warns", (a2, len(w2)), ({}, 1))
check("  ...max(3, 10%): 4 of 41 is allowed", len(sync.absent_fingerprints(
    prev, manifest_inputs({"toronto-rec": W}, [f"s{i}" for i in range(40) if i not in (1, 2, 3)],
                          {"toronto-rec": {}}), NOW)[0]), 4)
stand_prev = {"facility-hours:nyc": dict(W, source="facility-hours:nyc", starts={"c1": stamp(NOW - timedelta(hours=5)),
                                                                                  "c2": stamp(NOW)},
                                         standing=["c1", "c2"])}
check("a standing centre gone from the data is absent whatever its rolled window says",
      sorted(sync.absent_fingerprints(stand_prev, manifest_inputs({"facility-hours:nyc": dict(W, source="facility-hours:nyc")},
                                                                   ["c2"], {"facility-hours:nyc": {}}), NOW)[0]), ["c1"])
check("absent and tombstoned targets never include a fingerprint the store lists live",
      sync.cancellation_targets({"tombstones": {"t1": {"source": "ics:x", "legacy": ["L", "s0"]}}, "live": {"s0"}},
                                {"s3": "toronto-rec"}),
      {"t1": "ics:x", "L": "ics:x", "s3": "absent:toronto-rec"})
prev_s = {"toronto-rec": {"starts": {f"s{i}": stamp(NOW + timedelta(days=3)) for i in range(30)},
                          "from": stamp(NOW - timedelta(hours=1)), "to": stamp(NOW + timedelta(days=90))}}
inp_s = {"complete": {"toronto-rec": dict(prev_s["toronto-rec"])}, "live": {f"s{i}" for i in range(28)},
         "tombstones": {}, "units": {"toronto-rec": {f"s{i}": stamp(NOW + timedelta(days=3)) for i in range(28)}},
         "standing": {}, "seen": {"s28"}}
check("a session the source still LISTS but we could not place (seen) is never absent",
      sorted(sync.absent_fingerprints(prev_s, inp_s, NOW)[0]), ["s29"])
mf = store_path("seen_manifest.json")
sync.write_manifest(mf, prev_s, inp_s)
check("  ...and it keeps its baseline entry, so the day it really goes absence still sees it",
      sorted(json.load(open(mf))["units"]["toronto-rec"]["starts"]) == sorted(f"s{i}" for i in range(29)), True)
tiny = {"revize:olympia": {"starts": {"o1": stamp(NOW + timedelta(days=3)), "o2": stamp(NOW + timedelta(days=4))},
                           "from": stamp(NOW - timedelta(hours=1)), "to": stamp(NOW + timedelta(days=90))}}
inp_t2 = {"complete": {"revize:olympia": dict(tiny["revize:olympia"])}, "live": set(), "tombstones": {},
          "units": {}, "standing": {}}
a3, _l3, w3 = sync.absent_fingerprints(tiny, inp_t2, NOW)
check("a whole small unit gone at once (2 of 2, under the max(3, 10%) floor) trips the breaker", (a3, len(w3)),
      ({}, 1))
inp_t2["live"] = {"o2"}
check("  ...while one of two gone is allowed", sorted(sync.absent_fingerprints(tiny, inp_t2, NOW)[0]), ["o1"])


# --------------------------------------------------------------------------- #
class FakeDB:
    """PostgREST over a dict: the filters this pipeline sends, nothing more."""

    def __init__(self, rows):
        self.rows = {k: dict(dict.fromkeys(("claimed_at",) + sync.STATE_FIELDS), **v) for k, v in rows.items()}
        self.gets, self.patches, self.headers, self.fail_patch = [], [], {}, False

    @staticmethod
    def _match(row, col, cond):
        v = row.get(col)
        if cond == "is.null":
            return v is None
        if cond == "not.is.null":
            return v is not None
        if cond.startswith("lt."):
            return v is not None and sync._utc(v) < sync._utc(cond[3:])
        if cond.startswith("eq."):
            return str(v) == cond[3:]
        raise AssertionError(f"unexpected filter {col}={cond}")

    def _select(self, url):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        ids = json.loads("[" + q.pop("external_id")[4:-1] + "]")
        q.pop("external_source")
        for k in ("select", "order", "limit", "offset"):
            q.pop(k, None)
        return [i for i in sorted(ids) if i in self.rows and all(self._match(self.rows[i], c, f) for c, f in q.items())]

    def get(self, url, headers=None, timeout=None):
        self.gets.append(url)
        q = parse_qs(urlsplit(url).query)
        fields = q["select"][0].split(",")
        hit = self._select(url)
        page = [{f: (i if f == "external_id" else self.rows[i].get(f)) for f in fields} for i in hit]
        return type("R", (), {"status_code": 200, "json": lambda s: page,
                              "headers": {"Content-Range": f"0-{len(page) - 1}/{len(page)}" if page else "*/0"}})()

    def patch(self, url, headers=None, data=None, timeout=None):
        q = parse_qs(urlsplit(url).query)
        self.patches.append(({k: v[0] for k, v in q.items() if k != "external_id"}, json.loads(data),
                             sorted(json.loads("[" + q["external_id"][0][4:-1] + "]"))))
        if self.fail_patch:
            return type("R", (), {"status_code": 503, "json": lambda s: []})()
        hit = self._select(url)
        body = json.loads(data)
        out = []
        for i in hit:
            self.rows[i].update({k: v for k, v in body.items() if k != "external_id"})
            if body.get("external_id") and body["external_id"] != i:      # a re-key moves the row itself
                self.rows[body["external_id"]] = self.rows.pop(i)
            # PostgREST's return=representation is the row AFTER the write: a
            # re-keyed row comes back under its NEW external_id.
            out.append({"external_id": body.get("external_id") or i})
        return type("R", (), {"status_code": 200, "json": lambda s: out})()


def run_sync(store, db, flags, manifest=None):
    written = []

    def up(rows, url, key):
        written.extend(r["external_id"] for r in rows)
        return len(rows), 0, 0
    argv = ["sync", "--store", store] + flags + (["--manifest", manifest] if manifest else [])
    code = None
    with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                 "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key"}), \
         patch("sys.argv", argv), patch("requests.Session", return_value=db), \
         patch.object(sync, "batch_geocode", return_value={}), \
         patch.object(sync, "_enrich_music_links"), \
         patch.object(sync, "load_blocklist", return_value=[]), \
         patch.object(sync, "_patch_sleep"), \
         patch.object(sync, "upsert", side_effect=up):
        try:
            _, log = quiet(sync.main)
        except SystemExit as e:
            code, log = e.code, ""
    return written, log, code


def rec(fp, when, sid=None, source="toronto-rec", **kw):
    return dict(dict(fingerprint=fp, name=f"Session {fp}", start_utc=stamp(when), latitude=43.7, longitude=-79.4,
                     coords_exact=True, sources=[{"source": source, "source_id": sid or fp, "url": None}]), **kw)


def write_store(name, events, tombstones=(), complete=None):
    p = store_path(name)
    data = {"_meta": {}, "events": events}
    if tombstones:
        data["tombstones"] = list(tombstones)
    if complete:
        data["complete_reads"] = complete
    json.dump(data, open(p, "w", encoding="utf-8"))
    return p


print()
print("A unit is a source, or one tenant of it (id_prefix)")
p = write_store("units.json", [rec("b1", DAY3, sid="burnaby:1", source="perfectmind"),
                               rec("s1", DAY3, sid="surrey:1", source="perfectmind"),
                               rec("h1", DAY3, source="facility-hours:nyc", recurring_days={"mon": "9-17"})],
                complete={"perfectmind|burnaby:": {"source": "perfectmind", "id_prefix": "burnaby:"},
                          "facility-hours:nyc": {"source": "facility-hours:nyc", "id_prefix": ""}})
inp = sync.cancellation_inputs(p)
check("Burnaby read whole holds Burnaby's sessions only; a standing centre is marked standing",
      ({u: sorted(v) for u, v in inp["units"].items()}, inp["standing"]["facility-hours:nyc"], sorted(inp["live"])),
      ({"perfectmind|burnaby:": ["b1"], "facility-hours:nyc": ["h1"]}, {"h1"}, ["b1", "h1", "s1"]))

print()
print("The sync, --only-new: what a daily run now writes")
T = NOW + timedelta(days=4)
WD = {"source": "toronto-rec", "id_prefix": "", "from": stamp(NOW - timedelta(hours=1)),
      "to": stamp(NOW + timedelta(days=90)), "at": stamp(NOW)}
p = write_store("daily.json", [rec("new", T), rec("same", T), rec("moved", T), rec("claimed", T),
                               rec("detail", T, source_details={"free": True}), rec("back", T), rec("young", T)],
                complete={"toronto-rec": WD})
db = FakeDB({"same": {"starts_at": stamp(T)}, "moved": {"starts_at": stamp(T - timedelta(hours=1))},
             "claimed": {"claimed_at": "2026-09-01", "starts_at": stamp(T - timedelta(hours=1))},
             "detail": {"starts_at": stamp(T)},
             "back": {"starts_at": stamp(T), "cancelled_at": old, "hidden_at": old},
             "young": {"starts_at": stamp(T), "cancelled_at": young, "hidden_at": young}})
written, log, code = run_sync(p, db, ["--only-new"])
check("new, a TIME CHANGE, a detail refresh and a cancelled row listed again are written; "
      "the unchanged, the claimed and a cancellation from this run are not",
      sorted(written), ["back", "detail", "moved", "new"])
check("  ...on ONE state read (the time columns ride in it)", (len(db.gets), "starts_at" in unquote(db.gets[0])),
      (1, True))
check("  ...and the row listed again is un-cancelled with BOTH exact stamps and the ownership in the filter",
      [(c.get("cancelled_at"), c.get("hidden_at"), c.get("claimed_at"), b, ids) for c, b, ids in db.patches],
      [(f"eq.{old}", f"eq.{old}", "is.null", {"cancelled_at": None, "hidden_at": None}, ["back"])])
check("  ...so it is back on the map, and the young one is not",
      (db.rows["back"]["hidden_at"], db.rows["young"]["hidden_at"] is not None), (None, True))

print()
print("Only a source read WHOLE this run may lift a cancellation")
p = write_store("notwhole.json", [rec("back", T)])          # same row, no complete read
db = FakeDB({"back": {"starts_at": stamp(T), "cancelled_at": old, "hidden_at": old}})
written, log, code = run_sync(p, db, ["--only-new"])
check("listed live by a source read in passing (no mark_complete): the cancellation stands",
      (db.rows["back"]["cancelled_at"], db.rows["back"]["hidden_at"], [b for _, b, _ in db.patches]),
      (old, old, []))
check("  ...and the log says why", "only a complete read lifts" in log, True)
p = write_store("whole_tomb.json", [rec("back", T)], tombstones=[{"fingerprint": "back", "source": "ics:lib"}],
                complete={"toronto-rec": WD})
db = FakeDB({"back": {"starts_at": stamp(T), "cancelled_at": old, "hidden_at": old}})
written, log, code = run_sync(p, db, ["--only-new"])
check("a row this very store also tombstones is never lifted by it",
      (db.rows["back"]["cancelled_at"], db.rows["back"]["hidden_at"]), (old, old))

print()
print("The sync: tombstones in every run")
p = write_store("tomb.json", [rec("live", T)],
                tombstones=[{"fingerprint": "gone", "source": "ics:lib", "reason": "STATUS:CANCELLED"},
                            {"fingerprint": "again", "source": "ics:lib"},
                            {"fingerprint": "takedown", "source": "ics:lib"},
                            {"fingerprint": "theirs", "source": "ics:lib"},
                            {"fingerprint": "missing", "source": "ics:lib"}])
db = FakeDB({"live": {"starts_at": stamp(T)}, "gone": {"starts_at": stamp(T)},
             "again": {"cancelled_at": old, "hidden_at": old}, "takedown": {"hidden_at": old},
             "theirs": {"claimed_at": "2026-09-01"}})
written, log, code = run_sync(p, db, ["--only-new"])
check("a tombstoned row is cancelled AND hidden", (db.rows["gone"]["cancelled_at"] is not None,
                                                  db.rows["gone"]["hidden_at"] is not None), (True, True))
check("one we already cancelled is left exactly as it is (re-stamping one column would break the "
      "equal stamps a lift matches)", (db.rows["again"]["cancelled_at"], db.rows["again"]["hidden_at"]), (old, old))
check("a taken-down row is NOT given cancelled_at (that would make it liftable)",
      db.rows["takedown"]["cancelled_at"], None)
check("a claimed row is not touched", (db.rows["theirs"]["cancelled_at"], db.rows["theirs"]["hidden_at"]), (None, None))
check("every cancellation PATCH carries claimed_at=is.null and external_source=mapsee",
      all(c.get("claimed_at") == "is.null" and c.get("external_source") == "eq.mapsee" for c, _, _ in db.patches),
      True)
check("the log counts it", "Cancelled + hid 1 of 5 row(s)" in log, True)
db = FakeDB({"gone": {}})
db.fail_patch = True
written, log, code = run_sync(p, db, ["--only-new"])
check("a failed cancellation write is retried, then WARNED, not failed (it is derived again next run, "
      "and an exit skipped the Meetup job's international sweep)",
      (code, "::warning::" in log and "cancellation write" in log, written,
       len(db.patches) == sync.PATCH_TRIES * 2), (None, True, ["live"], True))


class Flaky(FakeDB):
    """One 503, then answers: a transient the retry must absorb."""
    def patch(self, url, headers=None, data=None, timeout=None):
        if not getattr(self, "_failed_once", False):
            self._failed_once = True
            self.patches.append(({}, {}, []))
            return type("R", (), {"status_code": 503, "json": lambda s: []})()
        return FakeDB.patch(self, url, headers, data, timeout)


db = Flaky({"gone": {}})
written, log, code = run_sync(p, db, ["--only-new"])
check("  ...and a single 503 is absorbed by the retry: the row is cancelled, nothing owed",
      (db.rows["gone"]["hidden_at"] is not None, "OWED" in log, code), (True, False, None))

print()
print("Ours, un-hidden since, while the publisher still says cancelled")
p = write_store("rehide.json", [], tombstones=[{"fingerprint": "unhid", "source": "ics:lib"}])
db = FakeDB({"unhid": {"cancelled_at": old, "hidden_at": None}, "plain": {"hidden_at": None}})
written, log, code = run_sync(p, db, ["--only-new"])
check("a row we cancelled that a retire script's --unhide put back is hidden again",
      (db.rows["unhid"]["hidden_at"] is not None, db.rows["unhid"]["cancelled_at"] > old), (True, True))
check("  ...with both stamps written as one, so it stays ours to lift later",
      db.rows["unhid"]["hidden_at"] == db.rows["unhid"]["cancelled_at"], True)

print()
print("A take-down (hidden_at alone) is never lifted by a live listing")
p = write_store("takedown.json", [rec("down", T)])
db = FakeDB({"down": {"starts_at": stamp(T), "cancelled_at": None, "hidden_at": stamp(NOW - timedelta(days=1))}})
written, log, code = run_sync(p, db, ["--only-new"])
check("a row we once cancelled, then taken down (hidden_at set, cancelled_at CLEARED), stays down "
      "when the source lists it live", (db.rows["down"]["hidden_at"] is not None,
                                         [b for _, b, _ in db.patches]), (True, []))

print()
print("Meetup and Eventbrite cancel only the row they wrote")
U = "https://www.eventbrite.com/e/big-show-123"
p = write_store("own.json", [], tombstones=[
    {"fingerprint": "tribe_row", "source": "eventbrite", "source_id": "123", "url": U},
    {"fingerprint": "eb_row", "source": "eventbrite", "source_id": "124", "url": U + "4"},
    {"fingerprint": "nourl", "source": "meetup", "source_id": "9"}])
db = FakeDB({"tribe_row": {"description": "Doors 8.\n\nTickets / info: https://venue.example/shows/big"},
             "eb_row": {"description": f"Tickets / info: {U}4\n\n🔎 More on this show: x"},
             "nourl": {"description": "Tickets / info: https://www.meetup.com/g/events/9/"}})
written, log, code = run_sync(p, db, ["--only-new"])
check("an Eventbrite tombstone does not hide the venue's own row of the same fingerprint",
      db.rows["tribe_row"]["hidden_at"], None)
check("  ...but does hide the row whose Tickets / info line is that listing's own URL",
      db.rows["eb_row"]["hidden_at"] is not None, True)
check("  ...and an own-only tombstone with no URL hides nothing", db.rows["nourl"]["hidden_at"], None)
db = FakeDB({"tribe_row": {"description": f"Tickets / info: {U}4"}})
run_sync(p, db, ["--only-new"])
check("  ...a longer URL that merely starts with the listing's is not the listing",
      db.rows["tribe_row"]["hidden_at"], None)

print()
print("The sync, --retire-absent: a whole read's missing sessions")
man = store_path("manifest.json")
W1 = {"source": "toronto-rec", "id_prefix": "", "from": stamp(NOW - timedelta(hours=1)),
      "to": stamp(NOW + timedelta(days=90)), "at": stamp(NOW)}
first = [rec(f"r{i}", NOW + timedelta(days=2 + i)) for i in range(30)]
p = write_store("abs1.json", first, complete={"toronto-rec": W1})
db = FakeDB({f"r{i}": {"starts_at": stamp(NOW + timedelta(days=2 + i))} for i in range(30)})
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=man)
check("first run: no manifest, nothing cancelled, a baseline written",
      (db.patches, sorted(json.load(open(man))["units"]["toronto-rec"]["starts"])[:2]), ([], ["r0", "r1"]))
p = write_store("abs2.json", first[:27] + first[28:], complete={"toronto-rec": W1})
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=man)
check("next run: the one session gone is cancelled and hidden",
      (db.rows["r27"]["cancelled_at"] is not None, db.rows["r27"]["hidden_at"] is not None,
       sum(1 for r in db.rows.values() if r["cancelled_at"])), (True, True, 1))
check("  ...and the manifest now holds this read", "r27" in json.load(open(man))["units"]["toronto-rec"]["starts"],
      False)
p = write_store("abs3.json", first[:10], complete={"toronto-rec": W1})
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=man)
check("a third of the timetable gone at once trips the breaker: nothing more cancelled",
      (sum(1 for r in db.rows.values() if r["cancelled_at"]), "::warning::Absence" in log), (1, True))
before = json.load(open(man))
p = write_store("abs3b.json", first[:8] + first[9:10], complete={"toronto-rec": W1})
db.fail_patch = True
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=man)
db.fail_patch = False
check("a run whose cancellation writes failed keeps the last good baseline (and warns)",
      (json.load(open(man)) == before, code, "::warning::" in log), (True, None, True))
p = write_store("abs4.json", first[:5], complete=None)
before = json.load(open(man))
run_sync(p, db, ["--only-new", "--retire-absent"], manifest=man)
check("a run that did not read the source whole leaves its baseline alone", json.load(open(man)), before)
try:
    with patch("sys.argv", ["sync", "--store", p, "--retire-absent"]), redirect_stdout(io.StringIO()):
        sync.main()
    check("--retire-absent without --manifest is refused", False, True)
except SystemExit as e:
    check("--retire-absent without --manifest is refused", "--manifest" in str(e.code), True)

print()
print("A gone session that is only RENAMED or RE-TIMED moves to its new key; it is not cancelled")
CLS = "https://surrey.perfectmind.com/24063/Clients/BookMe4LandingPages/Class?classId={}&occurrenceDate=20261012"
SHARED = "https://example.org/centre/calendar"
UNIT = "perfectmind|surrey:"
WS = {UNIT: {"source": "perfectmind", "id_prefix": "surrey:", "from": stamp(NOW - timedelta(hours=1)),
             "to": stamp(NOW + timedelta(days=90)), "at": stamp(NOW)}}
D = NOW + timedelta(days=6)


def srec(fp, when, name, url=None, venue="Cloverdale Recreation Centre"):
    return dict(fingerprint=fp, name=name, start_utc=stamp(when), venue_name=venue, latitude=49.1, longitude=-122.7,
                coords_exact=True, sources=[{"source": "perfectmind", "source_id": "surrey:" + fp, "url": url}])


keep = [srec(f"k{i}", NOW + timedelta(days=2 + i), f"Keep {i}") for i in range(60)]
then = keep + [srec("ren_old", D, "Nit d'ànimes", venue="Ludoteca Ca L'Arnó"),
               srec("time_old", D + timedelta(minutes=15), "Drop In Pickleball - 13+", url=CLS.format("1752")),
               srec("swap_old", D + timedelta(hours=3), "Lane Swim"),
               srec("shared_old", D + timedelta(hours=5), "Yoga", url=SHARED),
               srec("shared_sib", D + timedelta(hours=6), "Pilates", url=SHARED)]
now_ = keep + [srec("ren_new", D, "Nit d'ànimes a la Ludoteca Ca L'Arnó", venue="Ludoteca Ca L'Arnó"),
               srec("time_new", D, "Drop In Pickleball - 13+", url=CLS.format("1752")),
               srec("swap_new", D + timedelta(hours=3), "Aquafit"),
               srec("shared_new", D + timedelta(hours=5, minutes=30), "Yoga Flow", url=SHARED),
               srec("shared_sib", D + timedelta(hours=6), "Pilates", url=SHARED)]
mf = store_path("succ_manifest.json")
if os.path.exists(mf):
    os.remove(mf)
sync.write_manifest(mf, None, sync.cancellation_inputs(write_store("succ_then.json", then, complete=WS)))
prev_m = sync.load_manifest(mf)
check("the manifest records an identity for every session it records a start for",
      sorted(prev_m[UNIT]["ident"]) == sorted(prev_m[UNIT]["starts"]), True)
inp_now = sync.cancellation_inputs(write_store("succ_now.json", now_, complete=WS))
gone, _l, _w = sync.absent_fingerprints(prev_m, inp_now, NOW)
check("four sessions are gone from the whole read", sorted(gone), ["ren_old", "shared_old", "swap_old", "time_old"])
check("a retitle (same start, venue, a title that contains the old one) and a 15-minute move on the same "
      "occurrence link are each matched to their one successor; another class in the same slot and a "
      "calendar page every session shares are not",
      sync.absent_successors(prev_m, inp_now, gone), {"ren_old": "ren_new", "time_old": "time_new"})

old_fmt = {UNIT: {k: v for k, v in prev_m[UNIT].items() if k != "ident"}}
check("a manifest written before identities were recorded matches nothing (absence as before)",
      sync.absent_successors(old_fmt, inp_now, gone), {})


def succ_case(then_recs, now_recs):
    m = store_path("succ_case.json")
    if os.path.exists(m):
        os.remove(m)
    sync.write_manifest(m, None, sync.cancellation_inputs(write_store("sc_then.json", keep + then_recs, complete=WS)))
    pm = sync.load_manifest(m)
    inp_c = sync.cancellation_inputs(write_store("sc_now.json", keep + now_recs, complete=WS))
    return sync.absent_successors(pm, inp_c, sync.absent_fingerprints(pm, inp_c, NOW)[0])


check("one gone session, two equally good successors: ambiguous, so it is cancelled as before",
      succ_case([srec("a", D, "Lane Swim")],
                [srec("b1", D, "Lane Swim - Pool 1"), srec("b2", D, "Lane Swim - Pool 2")]), {})
check("two gone sessions, one successor that fits both: ambiguous, both cancelled as before",
      succ_case([srec("c1", D, "Badminton Court 1"), srec("c2", D, "Badminton Court 2")],
                [srec("c", D, "Badminton")]), {})
check("a successor that was already listed at the last read is not new, so it is not a successor",
      succ_case([srec("d_old", D, "Family Swim"), srec("d_new", D + timedelta(days=7), "Family Swim Time")],
                [srec("d_new", D + timedelta(days=7), "Family Swim Time")]), {})
check("the same occurrence link on ANOTHER day is not the same occurrence",
      succ_case([srec("e_old", D, "Spin", url=CLS.format("9"))],
                [srec("e_new", D + timedelta(days=1), "Spin", url=CLS.format("9"))]), {})
check("a renamed session at the same start in ANOTHER venue is not matched",
      succ_case([srec("f_old", D, "Zumba")], [srec("f_new", D, "Zumba Gold", venue="South Surrey Rec")]), {})
check("accents and punctuation do not make a rename look new ('Nit d'ànimes' ~ 'nit d animes')",
      sync._similar_titles(sync._ident_words("Nit d'ànimes"), sync._ident_words("NIT D'ANIMES (Halloween)")), True)

# A WRONG MOVE hands an RSVP to a different session (review, 2026-10-07): every
# one of these is a DIFFERENT session in the same slot, and must be cancelled.
check("another activity for the same audience in the slot is not a rename (run 116 has 3,457 such pairs)",
      succ_case([srec("x_old", D, "Drop In Badminton - Adult")], [srec("x_new", D, "Drop In Volleyball - Adult")]), {})
check("an age band is not a rename ('ages 6+' is not 'ages 60+')",
      succ_case([srec("y_old", D, "Computer Lab, ages 6+")], [srec("y_new", D, "Computer Lab, ages 60+")]), {})
check("a narrower audience is not a rename ('Adult Shinny (18+)' -> '... [Goalies]', 'Length Swim' -> "
      "'... Ladies Only')",
      (succ_case([srec("z_old", D, "Adult Shinny (18+)")], [srec("z_new", D, "Adult Shinny (18+) [Goalies]")]),
       succ_case([srec("w_old", D, "Length Swim")], [srec("w_new", D, "Length Swim - Ladies Only")])), ({}, {}))
check("titles are compared word for word, not as strings ('art' is not in 'party', 'yoga' not in 'yogafusion')",
      (sync._similar_titles("art", "party"), sync._similar_titles("yoga", "yogafusion")), (False, False))
check("a class the venue ALREADY ran, put into this slot, is a swap, not a rename",
      succ_case([srec("v_old", D, "Lane Swim"), srec("v_plus", D + timedelta(days=1), "Lane Swim Plus")],
                [srec("v_plus", D + timedelta(days=1), "Lane Swim Plus"), srec("v_new", D, "Lane Swim Plus")]), {})
check("one date of a series dropped while a new title takes its slot: the series is still listed, so the "
      "date is gone, not renamed",
      succ_case([srec("u_old", D, "Aqua Fit"), srec("u_wk", D + timedelta(days=7), "Aqua Fit")],
                [srec("u_wk", D + timedelta(days=7), "Aqua Fit"), srec("u_new", D, "Aqua Fit Deep Water")]), {})
check("a link only one session carries, carried over to a DIFFERENT event at another venue the same day "
      "(a small town's one calendar page), is not the same occurrence",
      succ_case([srec("t_old", D, "Indigenous Peoples' Day Celebration", url=SHARED, venue="East Bay Dr")],
                [srec("t_new", D + timedelta(hours=4), "Harvest Market", url=SHARED, venue="City Hall")]), {})
long = "Yau Ma Tei Theatre Venue Partnership Scheme Elite Young Talents Cantonese Opera Performance "
check("absence_ident keeps a long title whole (a cut title makes 'X' contain 'X Book Display for Children')",
      "for children" in sync.absence_ident({"name": long + "Book Display for Children"})[2], True)
check("titles are kept whole: two long titles with the same first 80 characters are not one session",
      succ_case([srec("s_old", D, long + "Power and Dilemma")], [srec("s_new", D, long + "Farewell My Husband")]), {})
check("a series renamed on every date moves date by date (the old title is gone from the venue, the new "
      "one is new to it)",
      succ_case([srec("q1", D, "Toddler Time"), srec("q2", D + timedelta(days=7), "Toddler Time")],
                [srec("r1", D, "Toddler Time Stories"), srec("r2", D + timedelta(days=7), "Toddler Time Stories")]),
      {"q1": "r1", "q2": "r2"})

print()
print("The sync moves the row (id, link, RSVPs) instead of cancelling it")
mf2 = store_path("succ_sync_manifest.json")
if os.path.exists(mf2):
    os.remove(mf2)
p = write_store("succ_sync_then.json", then, complete=WS)
db = FakeDB({r["fingerprint"]: {"starts_at": r["start_utc"], "id": 100 + i} for i, r in enumerate(then)})
run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf2)
check("first read: a baseline, nothing moved or cancelled", (db.patches, os.path.exists(mf2)), ([], True))
db.rows["claimed_twin_old"] = {"claimed_at": "2026-09-01", "starts_at": stamp(D + timedelta(hours=9)),
                               "cancelled_at": None, "hidden_at": None, "id": 900}
p = write_store("succ_sync_now.json", now_, complete=WS)
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf2)
check("the renamed and the re-timed rows now live under their new keys, as the SAME rows (same id)",
      (db.rows.get("ren_new", {}).get("id"), db.rows.get("time_new", {}).get("id"), "ren_old" in db.rows,
       "time_old" in db.rows), (160, 161, False, False))
check("  ...not cancelled, not hidden", [(db.rows[k]["cancelled_at"], db.rows[k]["hidden_at"])
                                         for k in ("ren_new", "time_new")], [(None, None), (None, None)])
check("  ...and rewritten this run with the new title and time (--only-new still writes a moved row)",
      ("ren_new" in written, "time_new" in written), (True, True))
check("the slot another class took and the session behind a shared calendar link are cancelled, as before",
      [db.rows[k]["cancelled_at"] is not None and db.rows[k]["hidden_at"] is not None
       for k in ("swap_old", "shared_old")], [True, True])
check("  ...and the log says what moved", "Absence re-key: 2 gone session(s)" in log and "moved 2 stored row(s)"
      in log, True)
check("  ...and a moved key is no longer a cancellation target (2 targets, not 4)",
      "Cancelled + hid 2 of 2 row(s)" in log, True)
check("every move PATCH carries claimed_at=is.null and external_source=mapsee",
      all(c.get("claimed_at") == "is.null" and c.get("external_source") == "eq.mapsee"
          for c, b, _ in db.patches if "external_id" in b), True)
check("the next manifest records the new keys with their identities",
      all(k in json.load(open(mf2))["units"][UNIT]["ident"] for k in ("ren_new", "time_new")),
      True)

then3 = keep + [srec("g_old", D, "Story Time"), srec("h_old", D + timedelta(hours=2), "Chess Club")]
now3 = keep + [srec("g_new", D, "Story Time at the Branch"), srec("h_new", D + timedelta(hours=2), "Chess Club Night")]
mf3 = store_path("succ_sync3.json")
if os.path.exists(mf3):
    os.remove(mf3)
p = write_store("succ3_then.json", then3, complete=WS)
db = FakeDB({r["fingerprint"]: {"starts_at": r["start_utc"]} for r in then3})
run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf3)
db.rows["g_new"] = {"starts_at": stamp(D), "claimed_at": None, "hidden_at": None, "cancelled_at": None}
db.rows["h_old"]["claimed_at"] = "2026-09-01"
p = write_store("succ3_now.json", now3, complete=WS)
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf3)
check("  (both pairs are matched as successors, so the cases below test the move, not the matcher)",
      "Absence re-key: 2 gone session(s)" in log, True)
check("a successor whose key is ALREADY stored: the old row is cancelled (never hidden alone, which "
      "nothing would ever lift), and the stored new row is left as it is",
      (db.rows.get("g_old", {}).get("cancelled_at") is not None,
       db.rows.get("g_old", {}).get("cancelled_at") == db.rows.get("g_old", {}).get("hidden_at"),
       db.rows["g_new"]["cancelled_at"]), (True, True, None))
check("a CLAIMED old row is neither moved nor cancelled (its owner's)",
      ("h_old" in db.rows, db.rows["h_old"]["cancelled_at"], "h_new" in db.rows and db.rows["h_new"].get("claimed_at")),
      (True, None, False))


class LostReply(FakeDB):
    """The re-key PATCH commits, and its reply never arrives."""

    def patch(self, url, headers=None, data=None, timeout=None):
        reply = super().patch(url, headers, data, timeout)
        if "external_id" in json.loads(data):
            raise ConnectionError("reset by peer")
        return reply


then4 = keep + [srec("m_old", D, "Story Time")]
now4 = keep + [srec("m_new", D, "Story Time at the Branch")]
mf4 = store_path("succ_sync4.json")
if os.path.exists(mf4):
    os.remove(mf4)
p = write_store("succ4_then.json", then4, complete=WS)
db = LostReply({r["fingerprint"]: {"starts_at": r["start_utc"], "id": 7} for r in then4})
run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf4)
baseline4 = open(mf4, encoding="utf-8").read()
p = write_store("succ4_now.json", now4, complete=WS)
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf4)
check("a move that landed but whose reply was lost: the row is still rewritten under its new key this run "
      "(not left with its old title until Wednesday), and nothing is cancelled",
      ("m_new" in written, db.rows.get("m_new", {}).get("id"), db.rows.get("m_new", {}).get("cancelled_at"),
       "m_old" in db.rows), (True, 7, None, False))
# A lost upsert keeps the last good baseline (main: no manifest write when rows were lost).
open(mf4, "w", encoding="utf-8").write(baseline4)
db = FakeDB({"m_new": {"starts_at": stamp(D), "id": 7}} | {r["fingerprint"]: {"starts_at": r["start_utc"]}
                                                            for r in keep})
written, log, code = run_sync(p, db, ["--only-new", "--retire-absent"], manifest=mf4)
check("a move that landed in a run whose upsert never wrote it (lost batch, killed step): the next run "
      "finds the old key gone and the new one stored, and rewrites it",
      "m_new" in written, True)

dm = store_path("succ_dry_manifest.json")
if os.path.exists(dm):
    os.remove(dm)
sync.write_manifest(dm, None, sync.cancellation_inputs(write_store("succ_dry_then.json", then, complete=WS)))
with patch("sys.argv", ["sync", "--store", write_store("succ_dry_now.json", now_, complete=WS), "--dry-run",
                        "--retire-absent", "--manifest", dm]), patch.object(sync, "build_rows", return_value=[]):
    _, dry = quiet(sync.main)
check("--dry-run says which gone sessions would MOVE, and leaves them out of what would be cancelled",
      "2 of them listed again under a new key" in dry and "-> 2 row(s) would be cancelled" in dry, True)

print()
print("The extended state read stays inside the URL budget")
ids = [make_fingerprint(f"Session {i}", "2026-10-05", "Centre") for i in range(250)]
db = FakeDB({i: {} for i in ids})
detail = {}
got = sync.fetch_import_state(db, "https://db.example", "k", ids, fields=sync.STATE_FIELDS, detail=detail)
check("250 fingerprints still cost 3 requests of <= 100 ids, each under 6 KB",
      (len(db.gets), max(len(u) for u in db.gets) <= 6000, len(detail), set(detail[ids[0]])),
      (3, True, 250, set(sync.STATE_FIELDS)))



class NoColumn(FakeDB):
    def get(self, url, headers=None, timeout=None):
        if "cancelled_at" in unquote(url):
            self.gets.append(url)
            return type("R", (), {"status_code": 400, "headers": {},
                                  "json": lambda s: {"code": "42703",
                                                     "message": "column events.cancelled_at does not exist"}})()
        return FakeDB.get(self, url, headers, timeout)


p = write_store("nocol.json", [rec("new", T), rec("same", T)])
db = NoColumn({"same": {"starts_at": stamp(T)}})
written, log, code = run_sync(p, db, ["--only-new"])
check("a state column the database has not got costs the feature, not the night",
      (code, written, len(db.gets)), (None, ["new"], 2))
db = FakeDB({"same": {}})
db.get = lambda url, headers=None, timeout=None: type("R", (), {"status_code": 503, "headers": {},
                                                               "json": lambda s: {}})()
written, log, code = run_sync(p, db, ["--only-new"])
check("...while any other failed read still stops the sync before a write",
      ("no events were written" in str(code), written), (True, []))

# --------------------------------------------------------------------------- #
print()
print("mapsee_uncancel.py: the escape hatch")


class FakeREST:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, path, method="GET", body=None, prefer=""):
        self.calls.append((method, path, body))
        q = parse_qs(urlsplit("x?" + path.split("?", 1)[1]).query)
        since = unquote(q["cancelled_at"][0])[4:]
        keep = [r for r in self.rows if r.get("claimed_at") is None and r.get("cancelled_at")
                and r["cancelled_at"] >= since]
        if method == "PATCH":
            if getattr(self, "fail_patch", False):
                raise OSError("503")
            ids = q["id"][0][4:-1].split(",")
            hit = [r for r in keep if str(r["id"]) in ids]
            for r in hit:
                r.update(body)
            return [{"id": r["id"]} for r in hit]
        return [dict(id=r["id"], title=r["title"], starts_at=r["starts_at"], cancelled_at=r["cancelled_at"],
                     hidden_at=r.get("hidden_at"))
                for r in keep][int(q["offset"][0]):int(q["offset"][0]) + 500] if q["starts_at"][0] <= "gte." + stamp(NOW) else []


_c1, _c2 = stamp(NOW - timedelta(hours=3)), stamp(NOW - timedelta(days=9))
rows = [dict(id=1, title="Lane Swim", starts_at=stamp(T), cancelled_at=_c1, hidden_at=_c1),
        dict(id=2, title="Older", starts_at=stamp(T), cancelled_at=_c2, hidden_at=_c2),
        dict(id=3, title="Claimed", starts_at=stamp(T), cancelled_at=stamp(NOW), claimed_at="y", hidden_at=stamp(NOW)),
        dict(id=4, title="Taken down on top", starts_at=stamp(T), cancelled_at=_c1,
             hidden_at=stamp(NOW - timedelta(hours=1)))]
api = FakeREST(rows)
out = []
UNC.run(UNC.parse_args(["--since", stamp(NOW - timedelta(days=1)), "--days", "8"]), sb=api,
        now=NOW.timestamp(), out=out.append)
check("dry run: lists the cancellations since --since, writes nothing",
      (sum(1 for m, _, _ in api.calls if m == "PATCH"), rows[0]["cancelled_at"] is not None,
       any("2 imported row(s)" in line for line in out),
       any("1 of them were hidden again since by someone else" in line for line in out)), (0, True, True, True))
rc = UNC.run(UNC.parse_args(["--since", stamp(NOW - timedelta(days=1)), "--days", "8", "--apply"]), sb=api,
             now=NOW.timestamp(), out=out.append)
check("  ...counts the rows the database changed, and exits 0 when the sweep was whole",
      (rc, any("un-cancelled 1 row(s)" in line for line in out)), (0, True))
patches = [(p, b) for m, p, b in api.calls if m == "PATCH"]
check("--apply clears cancelled_at and hidden_at, with the ownership AND both exact stamps in the PATCH filter",
      (rows[0]["cancelled_at"], rows[0]["hidden_at"], len(patches),
       all("claimed_at=is.null" in p and "external_source=eq.mapsee" in p and "hidden_at=eq." in p
           for p, _ in patches)),
      (None, None, 1, True))
check("a row hidden again on top of our cancellation (a take-down that left cancelled_at) is never lifted",
      (rows[3]["cancelled_at"], rows[3]["hidden_at"]) != (None, None), True)
check("an older cancellation and a claimed row are left alone",
      (rows[1]["cancelled_at"] is not None, rows[2]["cancelled_at"] is not None), (True, True))
bad = FakeREST([dict(id=4, title="Swim", starts_at=stamp(T), cancelled_at=stamp(NOW), hidden_at=stamp(NOW))])
bad.fail_patch = True
out = []
check("a failed write makes the escape hatch exit non-zero",
      UNC.run(UNC.parse_args(["--since", stamp(NOW - timedelta(days=1)), "--days", "8", "--apply"]), sb=bad,
              now=NOW.timestamp(), out=out.append), 1)
try:
    UNC.since_stamp("2026-10-06T06:00:00")
    check("a --since with no zone is refused", False, True)
except ValueError:
    check("a --since with no zone is refused", True, True)

print()
print("A retire script's --unhide never puts back a row the sync cancelled")
import mapsee_retire_online_events as RET                 # noqa: E402
calls = []


def ret_sb(path, method="GET", body=None, prefer=""):
    calls.append((method, path))
    if method == "GET" and len([c for c in calls if c[0] == "GET"]) == 1:
        return [{"id": 7, "title": "Zoom Book Club (online)", "description": "Join us on Zoom. Online only.",
                 "claimed_by": None, "starts_at": stamp(T)}]
    return []


with patch.object(RET, "sb", ret_sb), patch.object(RET, "SUPABASE_URL", "https://db.example"), \
        patch.object(RET, "SERVICE_KEY", "unused-test-key"), \
        patch("sys.argv", ["ret", "--apply", "--unhide", "--days", "2", "--back", "0", "--max-pages", "1"]):
    quiet(RET.main)
check("retire_online_events --unhide reads AND writes only rows with cancelled_at null",
      (bool(calls), all("cancelled_at=is.null" in path for _m, path in calls),
       any(m == "PATCH" for m, _p in calls)), (True, True, True))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all cancellation checks passed")
