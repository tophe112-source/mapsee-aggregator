#!/usr/bin/env python3
"""
test_platform_cancellations.py — a ticketing or meetup platform that says an
event is off must take OUR row off, and the only way it can is by naming the
fingerprint that row was stored under.

Prints one line per case and exits non-zero, like the other gate scripts.
Offline: every HTTP call is a stub, and nothing is saved.

WHY. Every one of these adapters refused a cancelled listing at ingest, which
protects only a row not written yet. The row we took while the event was on
sale stayed on the map: measured 2026-09-13 over 502 Meetup rows re-probed at
their own pages, 12.9% were not happening (docs/agents/cancelled-events.md). Now
a cancelled listing is handed to EventStore.cancel, and the sync hides the row
whose external_id is that tombstone's fingerprint. A tombstone under any OTHER
fingerprint hides nothing and says nothing — so the case that matters, for each
adapter, is: build the live listing and the cancelled one from the same raw
record, upsert the first, cancel the second, and the two fingerprints (and
source identities) must be the same. That runs through each adapter's own
ingest loop, so a loop that still upserts a cancelled listing, or cancels a
live one, fails here too.
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MAPSEE_TODAY", "20261005")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_axs as AXS                            # noqa: E402
import mapsee_ingest_dice as DICE                          # noqa: E402
import mapsee_ingest_dice_venue as DV                      # noqa: E402
import mapsee_ingest_eventbrite as EB                      # noqa: E402
import mapsee_ingest_meetup as MEETUP                      # noqa: E402
import mapsee_ingest_moshtix as MOSH                       # noqa: E402
import mapsee_ingest_seatgeek as SG                        # noqa: E402
import mapsee_ingest_venuepilot as VP                      # noqa: E402
from mapsee_ingest import EventStore                       # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


# No adapter may sleep in a gate script.
for _m in (MEETUP, EB, DICE, VP, SG, AXS, MOSH):
    _m.time.sleep = lambda *_a, **_k: None
DV.time.sleep = lambda *_a, **_k: None

_TMP = tempfile.mkdtemp(prefix="platform_cancel_")
_N = [0]


def fresh_store():
    _N[0] += 1
    return EventStore(os.path.join(_TMP, f"s{_N[0]}.json"))     # never saved


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text, self.headers = body, status, "", {}

    def json(self):
        return self._body


class Session:
    """Answers every request with the next body in `bodies` (then empty)."""
    def __init__(self, bodies):
        self.bodies = list(bodies)

    def _next(self, *_a, **_k):
        return Resp(self.bodies.pop(0) if self.bodies else {})
    get = post = _next


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def live_fp(store):
    recs = list(store.records.values())
    return (recs[0]["fingerprint"], recs[0]["sources"][0]["source"],
            str(recs[0]["sources"][0]["source_id"])) if len(recs) == 1 else len(recs)


def tomb_fp(store):
    items = list(store.tombstones.items())
    return (items[0][0], items[0][1]["source"], str(items[0][1]["source_id"])) \
        if len(items) == 1 else len(items)


def same_identity(label, run, live_raw, dead_raw, log_word="cancelled"):
    """run(store, raw) drives the adapter's own loop over one raw record."""
    a, b = fresh_store(), fresh_store()
    quiet(run, a, live_raw)
    _, log = quiet(run, b, dead_raw)
    check(f"{label}: the live listing is a row and no tombstone",
          (len(a.records), len(a.tombstones)), (1, 0))
    check(f"{label}: the cancelled listing is a tombstone and no row",
          (len(b.records), len(b.tombstones)), (0, 1))
    check(f"{label}: tombstone fingerprint/source/source_id == the stored row's",
          tomb_fp(b), live_fp(a))
    if log_word:
        check(f"{label}: the adapter's log line counts it", f"1 {log_word}" in log, True)


SOON = datetime.now(timezone.utc) + timedelta(days=9)
SOON_Z = SOON.strftime("%Y-%m-%dT19:00:00Z")
SOON_D = SOON.strftime("%Y-%m-%d")

# ---- Meetup ------------------------------------------------------------------
MEET = {"id": "311", "title": "Board Game Night", "status": "ACTIVE", "dateTime": SOON_Z,
        "description": "Bring a game.", "group": {"name": "Seattle Gamers", "urlname": "sea-gamers"},
        "venue": {"name": "Raygun Lounge", "address": "501 E Pine St", "city": "Seattle",
                  "lat": 47.615, "lon": -122.324},
        "eventUrl": "https://www.meetup.com/sea-gamers/events/311/"}


def meetup_run(store, *nodes):
    body = {"data": {"eventSearch": {"edges": [{"node": n} for n in nodes]}}}
    return MEETUP.ingest(store, Session([body]), "", "47.6", "-122.3", 25, 200,
                         [("games", "community")], 30)


print("Meetup (the measured case: 12.9% of its rows were not happening)")
for state in ("CANCELLED", "CANCELLED_PERM", "AUTOSCHED_CANCELLED"):
    same_identity(f"  meetup {state}", meetup_run, MEET, dict(MEET, status=state))
for state in ("DRAFT", "PAST", "BLOCKED", "TEMPLATE", "PROPOSED"):
    s = fresh_store()
    quiet(meetup_run, s, dict(MEET, status=state))
    check(f"  meetup {state} is refused, NOT a tombstone (its name does not say off)",
          (len(s.records), len(s.tombstones)), (0, 0))
s = fresh_store()
quiet(meetup_run, s, MEET, dict(MEET, status="CANCELLED"))
check("  live and cancelled in one sweep: the tombstone wins", (len(s.records), len(s.tombstones)), (0, 1))
# The keyword sweep overlaps: live 2026-10-05, Seattle, one cancelled event came
# back under 3 keywords. The log line counts EVENTS, not hits.
dead = {"data": {"eventSearch": {"edges": [{"node": dict(MEET, status="CANCELLED")}]}}}
_, log = quiet(MEETUP.ingest, fresh_store(), Session([dead, dead, dead]), "", "47.6", "-122.3",
               25, 200, [("games", "community"), ("social", "community"), ("music", "music")], 30)
check("  one cancelled event found by three keywords is counted once", "; 1 cancelled" in log, True)
s = fresh_store()
quiet(meetup_run, s, dict(MEET, status="CANCELLED", title="Board Game Night via Zoom - join from home",
                          description="Online only. Join from home on Zoom."))
check("  a cancelled Zoom call is no tombstone (no row could exist)", len(s.tombstones), 0)
# Called off AND retitled: the stored row has the old title, so must the tombstone.
same_identity("  meetup CANCELLED + retitled 'CANCELLED: Board Game Night'", meetup_run, MEET,
              dict(MEET, status="CANCELLED", title="CANCELLED: Board Game Night"))
s = fresh_store()
quiet(meetup_run, s, dict(MEET, status="CANCELLED", title="Library closed"))
check("  a closure title names no event: no tombstone", len(s.tombstones), 0)
check("  to_event still refuses CANCELLED (test_cancelled_events' contract)",
      MEETUP.to_event(dict(MEET, status="CANCELLED")), None)

# ---- Eventbrite ----------------------------------------------------------------
EBEV = {"id": "9001", "status": "live", "name": {"text": "Sunset Salsa"},
        "start": {"utc": SOON_Z, "local": SOON_D + "T12:00:00", "timezone": "America/Los_Angeles"},
        "venue": {"name": "Cal Anderson Park", "address": {"latitude": "47.617", "longitude": "-122.319",
                                                          "city": "Seattle"}},
        "url": "https://www.eventbrite.com/e/9001"}


def eb_run(store, ev):
    return EB.hydrate(store, Session([ev]), ev["id"])


print("Eventbrite")
same_identity("  eventbrite canceled", eb_run, EBEV, dict(EBEV, status="canceled"), log_word=None)
same_identity("  eventbrite canceled + retitled 'Sunset Salsa - CANCELLED'", eb_run, EBEV,
              dict(EBEV, status="canceled", name={"text": "Sunset Salsa - CANCELLED"}), log_word=None)
check("  hydrate reports what it did, for the metro log line",
      [eb_run(fresh_store(), dict(EBEV, status=st)) for st in ("live", "canceled", "draft")],
      ["kept", "cancelled", None])
for state in ("draft", "ended", "completed"):
    s = fresh_store()
    quiet(eb_run, s, dict(EBEV, status=state))
    check(f"  eventbrite {state} is refused, not a tombstone", (len(s.records), len(s.tombstones)), (0, 0))
_orig_sd = EB._server_data
EB._server_data = lambda _html: {"search_data": {"events": {"results": [
    {"eventbrite_event_id": "111"}, {"eventbrite_event_id": "222", "is_cancelled": True},
    {"eventbrite_event_id": "333", "is_online_event": True}]}}}
ids, _ = quiet(EB.discover_event_ids, Session([{}]), "wa--seattle", 1)
EB._server_data = _orig_sd
check("  discovery hands a cancelled listing to hydration (it used to skip it)", ids, ["111", "222"])

# ---- DICE (partner API) and DICE venue pages ----------------------------------------
DICEEV = {"id": "d1", "name": "Night Tapes", "status": "on-sale", "date": SOON_Z,
          "venues": [{"name": "Neumos", "city": "Seattle", "location": {"lat": 47.614, "lng": -122.319}}]}


def dice_run(store, ev):
    return DICE.ingest(store, Session([{"data": [ev]}]), 30, "Seattle", None, None, None)


print("DICE")
for state in ("cancelled", "postponed"):
    same_identity(f"  dice {state}", dice_run, DICEEV, dict(DICEEV, status=state))
s = fresh_store()
quiet(dice_run, s, dict(DICEEV, status="sold-out"))
check("  dice sold-out is still a live show", (len(s.records), len(s.tombstones)), (1, 0))

DVEV = {"id": "v1", "name": "Wet Leg", "status": "on-sale", "perm_name": "wet-leg-x",
        "dates": {"event_start_date": SOON.strftime("%Y-%m-%dT20:00:00-07:00"),
                  "timezone": "America/Los_Angeles"},
        "venues": [{"name": "The Showbox", "address": "1426 1st Ave, Seattle",
                    "city": {"name": "Seattle", "country_code": "US"},
                    "location": {"lat": 47.608, "lng": -122.339}}]}
DV_CFG = dict(DV.DEFAULTS, venues=[{"name": "The Showbox", "url": "https://dice.fm/venue/x"}])
_orig_fetch = DV.fetch_events


def dv_run(store, ev):
    DV.fetch_events = lambda *_a, **_k: [ev]
    try:
        return DV.ingest(store, None, DV_CFG)
    finally:
        DV.fetch_events = _orig_fetch


for state in ("cancelled", "postponed"):
    same_identity(f"  dice_venue {state}", dv_run, DVEV, dict(DVEV, status=state),
                  log_word="cancelled/postponed")
past = dict(DVEV, status="cancelled",
            dates={"event_start_date": "2020-01-01T20:00:00-08:00", "timezone": "America/Los_Angeles"})
s = fresh_store()
quiet(dv_run, s, past)
check("  dice_venue: a past cancelled show is no tombstone (the live rule refuses it too)",
      len(s.tombstones), 0)

# ---- VenuePilot ---------------------------------------------------------------------
VPEV = {"id": 77, "name": "The Dip", "date": SOON_D, "startTime": "20:00:00", "status": "active"}
VP_SITE = {"name": "Tractor", "account_ids": [1], "venue": {"name": "Tractor Tavern", "lat": 47.66,
                                                             "lon": -122.38, "city": "Seattle"}}


def vp_run(store, ev):
    body = {"data": {"paginatedEvents": {"collection": [ev], "metadata": {"totalPages": 1}}}}
    return VP.ingest_site(store, Session([body]), VP_SITE)


print("VenuePilot")
same_identity("  venuepilot cancelled", vp_run, VPEV, dict(VPEV, status="cancelled"))

# ---- SeatGeek, AXS --------------------------------------------------------------------
SGEV = {"id": 5, "title": "Mariners vs Astros", "status": "normal",
        "datetime_local": SOON_D + "T19:10:00", "datetime_utc": SOON_D + "T02:10:00",
        "venue": {"name": "T-Mobile Park", "city": "Seattle", "location": {"lat": 47.59, "lon": -122.33}}}


def sg_run(store, ev):
    return SG.ingest(store, Session([{"events": [ev], "meta": {"per_page": 100, "total": 1}}]),
                     "id", None, "47.6", "-122.3", 25, 30)


print("SeatGeek / AXS")
for state in ("cancelled", "postponed"):
    same_identity(f"  seatgeek {state}", sg_run, SGEV, dict(SGEV, status=state),
                  log_word="cancelled/postponed")
s = fresh_store()
quiet(sg_run, s, dict(SGEV, status="rescheduled"))
check("  seatgeek: an unknown status is a live listing, as before", len(s.records), 1)

AXSEV = {"id": "a1", "title": "Comedy Night", "eventDateTime": SOON_Z,
         "venue": {"name": "The Moore", "city": "Seattle", "lat": 47.61, "lon": -122.34}}


def axs_run(store, ev):
    return AXS.ingest(store, Session([{"events": [ev]}]), 30, "47.6", "-122.3", 25)


same_identity("  axs cancelled", axs_run, AXSEV, dict(AXSEV, status="Cancelled"))

# ---- Moshtix: no status, but a notice title (measured 1 in 200 on 2026-10-05) ----------
print("Moshtix (no cancelled state in its API; the title is the only signal)")
MOEV = {"id": 42, "name": "Definitely Oasis (UK) (Oasis Tribute)", "startDate": SOON_Z,
        "venue": {"name": "The Gov", "location": {"latitude": -34.9, "longitude": 138.6},
                  "address": {"locality": "Adelaide"}}}
a, b = fresh_store(), fresh_store()
a.upsert(MOSH.to_event(MOEV, None, 30))
quiet(b.upsert, MOSH.to_event(dict(MOEV, name=MOEV["name"] + " - CANCELLED"), None, 30))
check("  moshtix '... - CANCELLED' tombstones the live title's own fingerprint",
      (tomb_fp(b), len(b.records)), (live_fp(a), 0))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all platform cancellation cases pass")
