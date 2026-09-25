#!/usr/bin/env python3
"""
test_ingest_parkrun.py — the worldwide free-weekly-run layer, against features
taken verbatim from images.parkrun.com/events.json.

Prints one line per case and exits non-zero on failure, like the other 19.

WHY THIS EXISTS AT ALL. The adapter was written, tested by hand, and wired into
aggregate-events.yml — and `parkrun_sources.json` was never committed. The step
is guarded `if [ -f parkrun_sources.json ]`, so every scheduled run since has
printed "no parkrun_sources.json — skipping parkrun" and the whole `running`
layer stayed empty, in all 20 countries, without one red tick. A config file
that must exist for a job to do anything is part of the job.

What is pinned:

  * A START TIME IS NOT IN THE FEED, so it is not invented. parkrun start times
    vary by country AND season — a UK 9am is an Australian 7am in summer, and
    some UK events start at 9:30. Absent a checked entry the event is ALL-DAY
    and says where to find the time, which is honest; a plausible country-wide
    guess is the well-formed-and-wrong failure this repo keeps paying for.
  * COUNTRY COMES FROM DATA, NOT FROM A TLD PARSE. parkrun's country codes are
    opaque integers and the feed carries only a domain, so `97 -> GB` is written
    down in the config where it can be read and checked, rather than derived
    from `parkrun.org.uk` by a regex that has to know org.uk is not UK.
  * THE JUNIOR 2K IS A KIDS EVENT. seriesid 2 runs on Sunday and carries `kids`
    as its secondary, which is the only thing putting parkrun on that lens.
  * IDENTITY IS THE EVENT NAME PLUS THE DATE, so a re-run over the same rolling
    horizon regenerates byte-identical rows instead of duplicating the world.
"""
import os
import sys
from datetime import date, timedelta

import mapsee_ingest_parkrun as pr

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# --- fixtures: verbatim shape from images.parkrun.com/events.json ------------
COUNTRIES = {
    "97": {"url": "www.parkrun.org.uk", "bounds": [-8.6, 49.9, 1.8, 60.9]},
    "3":  {"url": "www.parkrun.com.au", "bounds": [112.9, -43.6, 153.6, -10.1]},
    "999": {"url": None, "bounds": []},
}
CFG = {"countries": {"97": "GB", "3": "AU"}, "horizon_days": 21}


def feature(seriesid=1, code=97, name="Bushy parkrun", eventname="bushy",
            lon=-0.335, lat=51.411):
    return {"geometry": {"coordinates": [lon, lat]},
            "properties": {"seriesid": seriesid, "countrycode": code,
                           "eventname": eventname, "EventLongName": name,
                           "EventLocation": "Bushy Park, Teddington"}}


def main():
    print("the country map is DATA, and it is applied")
    evs = pr.parkrun_events(feature(), COUNTRIES, CFG)
    check_true("a 5k produces occurrences", len(evs) > 0)
    check("the country comes from the config map", evs[0].country, "GB")
    check("an Australian event maps too",
          pr.parkrun_events(feature(code=3, eventname="albert"), COUNTRIES, CFG)[0].country, "AU")
    check("a code the config does not know invents nothing",
          pr.parkrun_events(feature(code=999, eventname="x"), COUNTRIES, CFG)[0].country, None)

    print()
    print("no start time in the feed, so none is invented")
    check("the event is all-day — a bare date, not a guessed instant",
          len(evs[0].start_local), 10)
    check_true("and it says where the real time is published",
               "Start time on the event page." in (evs[0].description or ""))
    timed = pr.parkrun_events(feature(), COUNTRIES, dict(CFG, start_times={"97": "09:00:00"}))
    check("a CHECKED time is used as naive local, for the sync to convert",
          timed[0].start_local[10:], "T09:00:00")
    check_true("and then the description stops pointing at the page",
               "Start time on the event page." not in (timed[0].description or ""))

    print()
    print("the junior 2k is what puts parkrun on the kids lens")
    jr = pr.parkrun_events(feature(seriesid=2, eventname="bushy-juniors"), COUNTRIES, CFG)
    check("the 5k's secondary is outdoors", evs[0].categories, ["outdoors"])
    check("the junior 2k's is kids", jr[0].categories, ["kids"])
    check("both are primarily running", (evs[0].category, jr[0].category), ("running", "running"))
    check("the 5k falls on Saturdays",
          sorted({date.fromisoformat(e.start_local[:10]).weekday() for e in evs}), [5])
    check("the junior 2k on Sundays",
          sorted({date.fromisoformat(e.start_local[:10]).weekday() for e in jr}), [6])

    print()
    print("a bounded horizon, and an identity that survives re-running")
    horizon = date.today() + timedelta(days=CFG["horizon_days"])
    check_true("nothing is projected past the configured horizon",
               all(date.fromisoformat(e.start_local[:10]) <= horizon for e in evs))
    again = pr.parkrun_events(feature(), COUNTRIES, CFG)
    check("a second expansion produces the same fingerprints — re-runs do not duplicate",
          [e.fingerprint for e in again], [e.fingerprint for e in evs])
    check("each occurrence is distinct within the run",
          len({e.fingerprint for e in evs}), len(evs))
    check_true("every occurrence keeps the surveyed coordinates",
               all(e.latitude == 51.411 and e.longitude == -0.335 for e in evs))
    check("the event's own page is the link",
          evs[0].ticket_url, "https://www.parkrun.org.uk/bushy/")

    print()
    print("a feature the feed cannot place is dropped, not guessed at")
    check("no coordinates, no event",
          pr.parkrun_events({"geometry": {"coordinates": []}, "properties":
                             {"seriesid": 1, "countrycode": 97, "eventname": "x",
                              "EventLongName": "X"}}, COUNTRIES, CFG), [])
    check("an unknown series is skipped rather than filed as running",
          pr.parkrun_events(feature(seriesid=99), COUNTRIES, CFG), [])

    print()
    print("parked until parkrun says yes in writing")
    # The job is guarded `if [ -f parkrun_sources.json ]`, so the LIVE file is the
    # on-switch. parkrun's terms reserve all rights and reference an anti-scraping
    # policy, and the README says publicly that this adapter is disabled. The
    # file was re-created once as a "missing config" and imported the worldwide
    # list until 2026-09-25, when 30 of the 91 running/sports/fitness rows within
    # 35 km of Ottawa were parkrun. Flip the first check only WITH the permission.
    import json, os
    check("parkrun_sources.json does not exist — parkrun is parked pending permission",
          os.path.exists("parkrun_sources.json"), False)
    parked = "parkrun_sources.json.pending-permission"
    check_true("the parked config is kept, so re-enabling is one rename", os.path.exists(parked))
    cfg = json.load(open(parked, encoding="utf-8"))
    check_true("it says why it is parked", "PERMISSION" in cfg.get("_DISABLED", ""))
    check_true("it is the WORKING shape: parkrun's country id -> ISO map, 20+ countries",
               isinstance(cfg.get("countries"), dict) and len(cfg["countries"]) >= 20)
    check("start_times is empty on purpose — none has been checked", cfg["start_times"], {})
    check_true("and it runs on more than one weekday, so a lost run is not a lost week",
               len(cfg["run_weekdays"]) >= 2)
    check_true("and the adapter accepts it exactly as it stands",
               len(pr.parkrun_events(feature(), COUNTRIES, cfg)) > 0)

    print()
    print("the backfill hides exactly what this adapter wrote")
    import mapsee_retire_parkrun as rp

    def stored(ev):
        # What the sync keeps: the adapter's description, then its own lines.
        return {"title": ev.name, "description": (ev.description or "")
                + "\n📍 Bushy Park, Teddington\nTickets / info: " + (ev.ticket_url or "")}
    check_true("a 5k this adapter wrote is retired", rp.should_retire(stored(evs[0])))
    check_true("so is a junior 2k", rp.should_retire(stored(jr[0])))
    check("a parkrun social somebody else published is not",
          rp.should_retire({"title": "Coffee after Bushy parkrun",
                            "description": "Meet at the cafe after the run."}), False)
    check("nor a row that carries the blurb without naming parkrun",
          rp.should_retire({"title": "Community 5k", "description": evs[0].description}), False)
    check("nor a row with no description at all",
          rp.should_retire({"title": "Bushy parkrun", "description": None}), False)

    # The walk itself, against an in-memory table with a page of 3, so the
    # cursor correction is exercised across pages: under --apply, rows hidden on
    # one page leave the result set, and stepping a full page would skip rows.
    import re as _re, sys as _sys, time as _time, urllib.parse
    t0 = _time.time()
    iso = lambda days: _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(t0 + days * 86400))
    table = []
    def row(i, title, desc, claimed=None, days=1.0):
        table.append({"id": str(i), "title": title, "description": desc, "claimed_by": claimed,
                      "starts_at": iso(days), "hidden_at": None,
                      "external_source": "mapsee", "is_private": False})
    blurb5, blurb2 = (s["blurb"] for s in pr.SERIES.values())
    for i in range(1, 8):
        row(i, f"Park {i} parkrun", blurb5 + " Start time on the event page.", days=1 + i * 0.01)
    row(8, "Park 8 junior parkrun", blurb2, days=1.2)
    row(9, "Coffee after Bushy parkrun", "Meet at the cafe.", days=1.3)
    row(10, "Park 10 parkrun", blurb5, claimed="someone", days=1.4)
    row(11, "Jazz night", "Live jazz.", days=1.5)
    row(12, "Park 12 parkrun", blurb5, days=30)
    for i in (13, 14, 15, 16):   # one Saturday, one timestamp: the keyset's tie-break
        row(i, f"Park {i} parkrun", blurb5, days=2.0)
    calls = []

    def fake_sb(path, method="GET", body=None, prefer=""):
        calls.append((method, path))
        if method == "PATCH":
            ids = set(_re.search(r"id=in\.\(([^)]*)\)", path).group(1).split(","))
            for r in table:
                if r["id"] in ids:
                    r["hidden_at"] = body["hidden_at"]
            return None
        if "id=in.(" in path:
            ids = set(_re.search(r"id=in\.\(([^)]*)\)", path).group(1).split(","))
            return [{"id": r["id"], "description": r["description"]} for r in table if r["id"] in ids]
        assert "ilike" not in path, "a text filter in the walk is a sequential scan"
        hidden = _re.search(r"hidden_at=([a-z.]+)", path).group(1)
        a = _re.search(r"starts_at=gte\.([^&]+)", path).group(1)
        b = _re.search(r"starts_at=lt\.([^&]+)", path).group(1)
        limit = int(_re.search(r"limit=(\d+)", path).group(1))
        assert "offset=" not in path, "a deep OFFSET 500'd in production; the walk is a keyset"
        key = lambda r: (r["starts_at"], int(r["id"]))
        hit = sorted((r for r in table
                      if a <= r["starts_at"] < b and (r["hidden_at"] is None) == (hidden == "is.null")),
                     key=key)
        m = _re.search(r"&or=([^&]+)", path)
        if m:   # PostgREST's meaning for the two shapes a keyset can take; nothing else
            expr = urllib.parse.unquote(m.group(1))
            full = _re.fullmatch(r'\(starts_at\.gt\."([^"]+)",and\(starts_at\.eq\."\1",id\.gt\."([^"]+)"\)\)', expr)
            bare = _re.fullmatch(r'\(starts_at\.gt\."([^"]+)"\)', expr)
            if full:
                hit = [r for r in hit if key(r) > (full.group(1), int(full.group(2)))]
            elif bare:          # no tie-break: rows sharing the last timestamp are lost
                hit = [r for r in hit if r["starts_at"] > bare.group(1)]
            else:
                raise ValueError(f"unexpected keyset expression {expr!r}")
        return [{k: r[k] for k in ("id", "title", "claimed_by", "starts_at")} for r in hit[:limit]]

    saved = (rp.sb, rp.PAGE, rp.SUPABASE_URL, rp.SERVICE_KEY, list(_sys.argv))
    rp.sb, rp.PAGE, rp.SUPABASE_URL, rp.SERVICE_KEY = fake_sb, 3, "https://x.supabase.co", "k"
    try:
        _sys.argv = ["mapsee_retire_parkrun.py"]
        rp.main()
        check("a dry run writes nothing", [c for c in calls if c[0] == "PATCH"], [])
        _sys.argv = ["mapsee_retire_parkrun.py", "--apply"]
        rp.main()
        hidden_ids = sorted((r["id"] for r in table if r["hidden_at"]), key=int)
        check("--apply hides every adapter row across pages, and nothing else",
              hidden_ids, ["1", "2", "3", "4", "5", "6", "7", "8", "12", "13", "14", "15", "16"])
        check_true("descriptions are read only for rows whose title names parkrun",
                   all("11" not in p.split("id=in.(")[1] for m, p in calls if m == "GET" and "id=in.(" in p))
        _sys.argv = ["mapsee_retire_parkrun.py", "--apply", "--unhide"]
        rp.main()
        check("--unhide puts every one of them back", [r["id"] for r in table if r["hidden_at"]], [])
    finally:
        rp.sb, rp.PAGE, rp.SUPABASE_URL, rp.SERVICE_KEY, _sys.argv[:] = saved

    print()
    print("no OTHER adapter is a silent no-op for a config nobody committed")
    # The general shape of the bug this file exists for. A workflow step guarded
    # `if [ -f X_sources.json ]` prints a friendly skip and returns 0 when the
    # file is absent, so a source config that was never committed looks exactly
    # like a source deliberately not configured — for as long as nobody counts.
    #
    # `ckan` is the one legitimate absence: catalog_curate MERGES into it, and
    # nothing has ever verified (3 ledger rows, all fail), so the file does not
    # exist yet and should not. Anything else appearing here is a job doing
    # nothing.
    import re
    KNOWN_EMPTY = {"ckan_sources.json",
                   "parkrun_sources.json"}   # parked pending permission (above)
    wf = open(os.path.join(".github", "workflows", "aggregate-events.yml"),
              encoding="utf-8").read()
    guarded = set(re.findall(r"if \[ -f ([a-z_]+_sources\.json) \]", wf))
    check_true("the workflow does guard several source configs", len(guarded) >= 10)
    absent = sorted(c for c in guarded if not os.path.exists(c) and c not in KNOWN_EMPTY)
    check("every guarded source config is present", absent, [])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all parkrun checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
