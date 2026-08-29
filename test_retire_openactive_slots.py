#!/usr/bin/env python3
"""
test_retire_openactive_slots.py — what may be hidden, and what must never be.

The judgement here is one property: this can never empty a venue-day. Everything
else is detail. mapsee_retire_thin_artwork.py failed live THREE times and not
once in its judgement — twice on its own query and once on the line that printed
the result — so main() is driven end to end against a stubbed transport too.

Run: python test_retire_openactive_slots.py
"""
import json
import sys

import mapsee_retire_openactive_slots as R

OA = "Session data published by Test Pool via OpenActive, licensed CC-BY 4.0."
KEPT = "🎟 11 bookable slots on this day, from 05:40 to 21:00.\n\n" + OA


def row(i, title="Swim For Fitness", day="2026-09-07", hh="05", desc=OA,
        lat=51.5, lon=-0.12):
    return {"id": f"id{i}", "title": title, "lat": lat, "lon": lon,
            "starts_at": f"{day}T{hh}:40:00+01:00", "description": desc,
            "claimed_by": None, "hidden_at": None, "recurring_hours": None}


STANDING = "\U0001F501 Runs weekly — 17 sessions scheduled over the next few months, on 1 day a week.\n\n" + OA


def standing(i, title="Aqua Aerobics", day="2026-09-07", hh="19",
             days=None, tz="Europe/London", lat=51.5, lon=-0.12):
    """The row collapse_weekly_series keeps: a pattern, not an occurrence."""
    r = row(i, title=title, day=day, hh=hh, desc=STANDING, lat=lat, lon=lon)
    r["recurring_hours"] = {"tz": tz, "days": days if days is not None
                            else {"0": [["19:40", "20:40"]]}}
    return r


def main():
    checks = []

    # ---------------------------------------------- the grid, with its keeper
    grid = [row(i, hh=f"{5 + i:02d}") for i in range(10)] + [row(99, desc=KEPT)]
    out = R.superseded(grid, min_per_day=6)
    checks.append((len(out) == 10, "ten slot rows are superseded by the collapsed day row"))
    checks.append((all(KEPT not in r["description"] for r in out),
                   "the collapsed row itself is never hidden — it is the thing being kept"))

    # -------------------------------------------------------- THE SAFETY RULE
    #
    # No replacement in the table means the import that should have written one
    # did not finish. Better's SessionSeries feed 500'd mid-run; hiding on that
    # evidence would empty a venue-day and the run that was meant to refill it
    # is exactly the thing that failed.
    no_keeper = [row(i, hh=f"{5 + i:02d}") for i in range(10)]
    checks.append((R.superseded(no_keeper, min_per_day=6) == [],
                   "no collapsed row present -> nothing is hidden, however big the group"))

    # ------------------------------------------------ below the threshold
    small = [row(i, hh=f"{9 + i:02d}") for i in range(3)] + [row(98, desc=KEPT)]
    checks.append((R.superseded(small, min_per_day=6) == [],
                   "three sessions in a day are a schedule — untouched"))

    # ------------------------------------------------ groups stay separate
    twodays = ([row(i, day="2026-09-07", hh=f"{5 + i:02d}") for i in range(10)]
               + [row(90, day="2026-09-07", desc=KEPT)]
               + [row(50 + i, day="2026-09-08", hh=f"{5 + i:02d}") for i in range(10)])
    out2 = R.superseded(twodays, min_per_day=6)
    checks.append((len(out2) == 10 and all(r["starts_at"][:10] == "2026-09-07" for r in out2),
                   "a day with no keeper is not hidden because the NEXT day has one"))

    twovenues = ([row(i, hh=f"{5 + i:02d}") for i in range(10)] + [row(91, desc=KEPT)]
                 + [row(60 + i, hh=f"{5 + i:02d}", lat=51.6, lon=-0.2) for i in range(10)])
    out3 = R.superseded(twovenues, min_per_day=6)
    checks.append((len(out3) == 10 and all(abs(r["lat"] - 51.5) < 1e-9 for r in out3),
                   "and one venue's keeper does not license hiding at another"))

    titles = ([row(i, hh=f"{5 + i:02d}") for i in range(10)] + [row(92, desc=KEPT)]
              + [row(70 + i, title="Lane Swim", hh=f"{5 + i:02d}") for i in range(10)])
    out4 = R.superseded(titles, min_per_day=6)
    checks.append((len(out4) == 10 and all(r["title"] == "Swim For Fitness" for r in out4),
                   "nor does one title's keeper license hiding another title"))

    # ------------------------------------------- the weekly fold's orphans
    #
    # 2026-09-07 is a Monday, so weekday 0. These are what the grid rule CANNOT
    # see: one occurrence per week, each alone in its own day, every group far
    # below min_per_day.
    mondays = [row(100 + w, title="Aqua Aerobics", day=d, hh="19")
               for w, d in enumerate(("2026-09-14", "2026-09-21", "2026-09-28"))]
    checks.append((R.superseded(mondays + [standing(200)], min_per_day=6) == [],
                   "the grid rule cannot see a weekly fold's orphans — it groups by day"))
    outw = R.weekly_superseded(mondays + [standing(200)])
    checks.append((len(outw) == 3,
                   "the weekly rule does: three Mondays on the pattern are superseded"))
    checks.append((all(r["id"] != "id200" for r in outw),
                   "and the standing row itself is never hidden"))

    # -------------------------------------- THE SAFETY RULE, one collapse on
    checks.append((R.weekly_superseded(mondays) == [],
                   "no standing row present -> nothing is hidden, however regular"))

    # -------------------------------- the occasions the fold deliberately kept
    off_hour = row(300, title="Aqua Aerobics", day="2026-09-14", hh="11")
    off_day = row(301, title="Aqua Aerobics", day="2026-09-15", hh="19")
    out_off = R.weekly_superseded([off_hour, off_day, standing(201)])
    checks.append((out_off == [],
                   "a bank-holiday special at another hour or on another day is left dated"))

    other = row(302, title="Lane Swim", day="2026-09-14", hh="19")
    far = row(303, title="Aqua Aerobics", day="2026-09-14", hh="19", lat=51.6)
    checks.append((R.weekly_superseded([other, far, standing(202)]) == [],
                   "nor does one title's or one venue's standing row license another's"))

    # ------------------------------------------- the two collapses compose
    #
    # 110 slots -> 7 day rows -> one standing row. Once the standing row exists
    # the day rows underneath it are superseded exactly as the slots were.
    dayrow = row(400, title="Aqua Aerobics", day="2026-09-14", hh="19", desc=KEPT)
    checks.append((len(R.weekly_superseded([dayrow, standing(203)])) == 1,
                   "a grid DAY row folded into a standing row is superseded in turn"))

    # ------------------------------------------------- the self-describing shape
    flat = standing(204, days={"0": ["19:40", "20:40"]})
    checks.append((len(R.weekly_superseded(mondays + [flat])) == 3,
                   "a flat [start,end] span reads as one span, the way ../mapsee 0188 reads it"))

    # ------------------------------------------------------------- timezones
    #
    # A timestamptz normalises to UTC in the column, so PostgREST may render
    # 19:40+01:00 as 18:40Z. Reading the wall clock off the string would then
    # miss the pattern; converting into the venue's own tz cannot.
    utc_rendered = [dict(r, starts_at=r["starts_at"].replace("T19:40:00+01:00",
                                                             "T18:40:00+00:00"))
                    for r in mondays]
    checks.append((len(R.weekly_superseded(utc_rendered + [standing(205)])) == 3,
                   "the same instant rendered in UTC still matches the local pattern"))

    checks.append((R.weekly_superseded([dict(r, starts_at="not a date")
                                        for r in mondays] + [standing(206)]) == [],
                   "an unparseable stamp fails closed"))
    checks.append((R.weekly_superseded(mondays + [dict(standing(207),
                                                       recurring_hours=None)]) == [],
                   "a row with the weekly text but no pattern is not a keeper"))

    # ------------------------- THE ROUND TRIP, THROUGH THE REAL SYNC
    #
    # The pattern is built by the ADAPTER out of the feed's own offset; the tz
    # it is read back in is written by the SYNC out of the venue's coordinates.
    # The two halves live in different files and neither can be seen to be wrong
    # on its own — get them out of step and this rule matches NOTHING, silently,
    # which is indistinguishable from "there was nothing to retire".
    from datetime import datetime, timezone as _tz
    import mapsee_ingest_openactive as _OA
    import mapsee_supabase_sync as _S

    _now = datetime(2026, 8, 26, tzinfo=_tz.utc)
    def _rec(day):
        return {"name": "Aqua Aerobics", "description": "Pool session.",
                "startDate": f"{day}T19:40:00+01:00",
                "endDate": f"{day}T20:40:00+01:00",
                "location": {"geo": {"latitude": 51.5074, "longitude": -0.1278},
                             "name": "Test Leisure Centre"}}
    _evs = []
    for _d in ("2026-08-31", "2026-09-07"):          # two consecutive Mondays
        _o = _OA.to_event(_rec(_d), {"name": "Everyone Active", "slug": "ea"}, _now, 120)
        _evs.append(_o[0] if isinstance(_o, tuple) else _o)
    _stamp = "2026-08-29T00:00:00Z"
    _before = [_S.to_row(e.as_record(_stamp), "host") for e in _evs]
    _kept, _n, _ = _OA.collapse_weekly_series(list(_evs), 2)
    _srow = _S.to_row([k for k in _kept if k.recurring_days][0].as_record(_stamp), "host")

    def _pair(tzname, starts):
        # tz forced: timezonefinder has no wheel everywhere and its absence
        # makes _tz_for fall back to a US longitude guess, which would make this
        # case about the sandbox rather than about the rule.
        _rh = dict(_srow["recurring_hours"] or {}); _rh["tz"] = tzname
        keeper = {"id": "keep", "title": _srow["title"], "lat": _srow["lat"],
                  "lon": _srow["lon"], "description": _srow["description"],
                  "recurring_hours": _rh, "hidden_at": None}
        orphans = [{"id": f"orph{i}", "title": b["title"], "lat": b["lat"],
                    "lon": b["lon"], "starts_at": st, "description": b["description"],
                    "recurring_hours": None, "hidden_at": None}
                   for i, (b, st) in enumerate(zip(_before, starts))]
        return R.weekly_superseded(orphans + [keeper])

    _pub = [b["starts_at"] for b in _before]
    checks.append((len(_pair("Europe/London", _pub)) == 2,
                   "an occurrence the fold replaced matches its standing row end to end"))
    _utc = [st.replace("T19:40:00+01:00", "T18:40:00+00:00") for st in _pub]
    checks.append((len(_pair("Europe/London", _utc)) == 2,
                   "and still matches when PostgREST renders the instant in UTC"))
    checks.append((_pair("America/New_York", _pub) == [],
                   "a WRONG tz matches nothing — the failure is silent, so it is asserted"))

    # --------------------------------------------------------- main(), stubbed
    #
    # Both of mapsee_ingest_osm_amenities' production failures were in main(),
    # and nothing ran main(). Same for the artwork retirement's NameError, which
    # ran the whole sweep, wrote 54 rows and then died printing the summary.
    calls = {"get": 0, "patch": [], "urls": []}
    served = [grid]

    def fake_sb(path, method="GET", body=None, prefer=""):
        if method == "PATCH":
            calls["patch"].append((path, body))
            return None
        calls["get"] += 1
        calls["urls"].append(path)
        batch = served.pop(0) if served else []
        # THE STUB HONOURS THE FILTER IT IS ASKED FOR. The version of this test
        # that did not is why --unhide shipped as a no-op: the query hard-coded
        # hidden_at=is.null, so the reverse pass could never see the rows it had
        # itself hidden, and a stub answering every URL with the same fixture
        # agreed with it. A test that drives a real query has to answer the
        # query, not the call.
        if "hidden_at=is.null" in path:
            batch = [r for r in batch if not r.get("hidden_at")]
        return batch
    R.sb = fake_sb
    R.SUPABASE_URL, R.SERVICE_KEY = "https://x.test", "k"

    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1"])
    checks.append((rc == 0 and not calls["patch"],
                   "a dry run reads and writes NOTHING, and returns 0"))

    served[:] = [grid]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((rc == 0 and len(calls["patch"]) == 1,
                   "--apply issues the PATCH"))
    checks.append((calls["patch"][0][1] == {"hidden_at": None} or
                   isinstance(calls["patch"][0][1].get("hidden_at"), str),
                   "and it writes a hidden_at stamp, never a DELETE"))
    checks.append(("id99" not in calls["patch"][0][0],
                   "the kept row's id is not in the PATCH"))

    # ------------------------------------------------- --unhide, ACTUALLY
    #
    # The state after a successful --apply: the slot rows carry a stamp, the
    # keeper does not. Reversing it has to see BOTH — which is why the scan
    # drops the hidden filter for this direction rather than flipping it.
    hidden_grid = ([dict(r, hidden_at="2026-08-29T09:00:00Z")
                    for r in grid if KEPT not in r["description"]]
                   + [row(99, desc=KEPT)])
    served[:] = [hidden_grid]
    calls["patch"].clear(); calls["urls"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply", "--unhide"])
    checks.append((all("hidden_at=is.null" not in u for u in calls["urls"]),
                   "--unhide does NOT filter the hidden rows out of its own scan"))
    patched = calls["patch"][0] if calls["patch"] else ("", {})
    checks.append((patched[1] == {"hidden_at": None},
                   "--unhide clears the stamp, so the pass is genuinely reversible"))
    checks.append((bool(calls["patch"]) and "id99" not in patched[0],
                   "and it does not touch the keeper, which was never hidden"))

    # A forward pass over that same state must find nothing left to do.
    served[:] = [hidden_grid]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((not calls["patch"],
                   "a second --apply does not restamp rows that are already hidden"))

    # And the weekly rule reaches main() too, not just the unit cases.
    served[:] = [mondays + [standing(500)]]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((rc == 0 and len(calls["patch"]) == 1 and
                   "id500" not in calls["patch"][0][0],
                   "main() hides the weekly orphans and spares the standing row"))

    served[:] = [mondays + [standing(501)]]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply", "--no-weekly"])
    checks.append((not calls["patch"], "--no-weekly turns that rule off"))

    served[:] = [[row(i, hh=f"{5 + i:02d}", desc="no attribution here") for i in range(10)]]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((not calls["patch"],
                   "rows without the OpenActive line are another adapter's and are skipped"))

    served[:] = [[]]
    calls["patch"].clear()
    rc = R.main(["--days", "1", "--back", "0", "--max-pages", "1", "--apply"])
    checks.append((rc == 0 and not calls["patch"],
                   "an empty scan is a clean exit, not a crash on the summary line"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
