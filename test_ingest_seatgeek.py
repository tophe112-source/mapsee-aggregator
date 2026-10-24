#!/usr/bin/env python3
"""
test_ingest_seatgeek.py — a SeatGeek listing with no date or time set, or a
game that may never be played, is not ingested.

Prints one line per case and exits non-zero, like the other gate scripts.
Offline: the HTTP call is a stub, and nothing is saved.

WHY. Live 2026-10-23, 4 rows read "TBD at <team> - World Series (Home Game N)"
(Chicago 2, Seattle 2), every one at 03:30 local: a time nobody set, for games
that depend on a series not yet played, and at least 2 of them cannot both
happen. The adapter read neither SeatGeek's title nor its *_tbd flags. They are
dropped, never kept as a bare day: the owner's call for Ticketmaster
(tm_has_clock, docs/agents/dates-and-timezones.md) is the same, because an
all-day row sorts above every real event.

Only a LIVE listing is refused. A postponed game often loses its time while the
row stored last month still has one, so a cancelled or postponed listing is a
tombstone however undecided it now is (Ticketmaster's loop reads the status
before tm_has_clock for the same reason).
"""
import io
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_seatgeek as SG                        # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


SG.time.sleep = lambda *_a, **_k: None
SOON = (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%d")


def game(eid, title="Seattle Mariners at Houston Astros", **kw):
    ev = {"id": eid, "title": title, "status": "normal",
          "datetime_local": SOON + "T19:10:00", "datetime_utc": SOON + "T02:10:00",
          "url": f"https://seatgeek.com/e/{eid}", "type": "mlb",
          "venue": {"name": "T-Mobile Park", "city": "Seattle", "location": {"lat": 47.59, "lon": -122.33}}}
    ev.update(kw)
    return ev


print("a contingent game, by its title")
for title in ("TBD at Seattle Mariners - World Series (Home Game 1)",
              "TBD at Chicago Cubs - World Series (Home Game 2)",
              "TBD vs. Seattle Kraken", "TBD vs Seattle Kraken", "  tbd at Seattle Mariners"):
    check(f"  {title.strip()[:52]!r} is refused", SG.to_event(game("t", title)), None)
for title in ("Seattle Mariners at Houston Astros", "TBDs at the Crocodile",
              "Matt Berry: TBD at the Neptune"):
    check(f"  {title!r} is a listing", SG.to_event(game("k", title)) is None, False)

print()
print("a date or a time SeatGeek has not set")
for flag in ("date_tbd", "time_tbd", "datetime_tbd"):
    check(f"  {flag} true is refused", SG.to_event(game("f", **{flag: True})), None)
    check(f"  {flag} false is a listing", SG.to_event(game("f", **{flag: False})) is None, False)
check("  no flags at all is a listing, exactly as before", SG.to_event(game("n")) is None, False)
check("  a refused listing is refused whole, never kept as a bare day",
      SG.to_record(game("d", time_tbd=True)), (None, None))
# The status is read FIRST, as Ticketmaster's loop reads it before tm_has_clock:
# a postponed game often loses its time while the row stored last month still
# has one, and an upsert cannot delete it.
WS1 = "TBD at Seattle Mariners - World Series (Home Game 1)"
nev, why = SG.to_record(game("c", WS1, status="cancelled"))
check("  ...but a cancelled contingent game is a tombstone, under the fingerprint its row was stored by",
      (nev is not None and nev.fingerprint == SG.make_fingerprint(WS1, SOON, "T-Mobile Park", "Seattle"), why),
      (True, "status:cancelled"))
live_fp = SG.to_record(game("p"))[0].fingerprint
for flag in ("date_tbd", "time_tbd", "datetime_tbd"):
    nev, why = SG.to_record(game("p", status="postponed", **{flag: True}))
    check(f"  a postponed game with {flag} is a tombstone under the stored row's fingerprint",
          (nev is not None and nev.fingerprint == live_fp, why), (True, "status:postponed"))


class _Resp:
    status_code, headers = 200, {}

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class _Session:
    def __init__(self, body):
        self.body = body

    def get(self, *_a, **_k):
        return _Resp(self.body)


class _Store:
    def __init__(self):
        self.rows = []

    def upsert(self, ev):
        self.rows.append(ev.source_id)

    def cancel(self, ev, why):
        return "cancelled"


print()
print("the fetch loop")
store, out = _Store(), io.StringIO()
body = {"events": [game("live"), game("tbd", "TBD at Seattle Mariners - World Series (Home Game 1)"),
                   game("clock", time_tbd=True)],
        "meta": {"per_page": 100, "total": 3}}
with redirect_stdout(out):
    kept = SG.ingest(store, _Session(body), "id", None, "47.6", "-122.3", 25, 30)
check("  stores only the game with a date and a time", (store.rows, kept), (["live"], 1))
check("  and its log line counts the two it dropped", "2 with no date or time set" in out.getvalue(), True)

# Through the real EventStore: day 1 stores the game at 19:10; day 2 SeatGeek
# says postponed and has dropped the time. The stored row must be tombstoned,
# not left on the map because the listing is now undecided.
import tempfile                                            # noqa: E402
from mapsee_ingest import EventStore                       # noqa: E402

real = EventStore(os.path.join(tempfile.mkdtemp(), "seatgeek_events.json"))
with redirect_stdout(io.StringIO()):
    SG.ingest(real, _Session({"events": [game("991")], "meta": {"per_page": 100, "total": 1}}),
              "id", None, "47.6", "-122.3", 25, 30)
check("  day 1: the game is stored", (len(real.records), len(real.tombstones)), (1, 0))
out = io.StringIO()
with redirect_stdout(out):
    SG.ingest(real, _Session({"events": [game("991", status="postponed", time_tbd=True,
                                              datetime_local=SOON + "T03:30:00")],
                              "meta": {"per_page": 100, "total": 1}}),
              "id", None, "47.6", "-122.3", 25, 30)
check("  day 2: postponed with time_tbd tombstones the stored row",
      (len(real.records), len(real.tombstones)), (0, 1))
check("  and is counted as postponed, not as undecided",
      "1 cancelled/postponed, 0 with no date or time set" in out.getvalue(), True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all seatgeek checks passed")
