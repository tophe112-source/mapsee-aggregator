#!/usr/bin/env python3
"""
test_sweep_global.py — the international sweep's argv, and the config it reads.

WHY THIS EXISTS
---------------
Measured 2026-08-12, in a production run that reported success: every metro
south of the equator failed, on both sources, with

    mapsee_ingest_meetup.py: error: argument --latlong: expected one argument
    [global] mapsee_ingest_meetup.py exited 2 for ['--latlong', '-34.4278,150.8931', ...]

`argparse` treats any argv token beginning with `-` as an option unless it
matches `^-\\d+$|^-\\d*\\.\\d+$`. A latlong has a comma in it, so it never
matches, and `--latlong -33.8688,151.2093` reads as two options rather than an
option and its value. Australia, New Zealand and South Africa — 22 of the 165
metros — ingested NOTHING from Ticketmaster or Meetup, twice a week, for as
long as the sweep has existed.

That rule is the interpreter's, and it CHANGED: Python 3.14 made the matcher a
prefix match (`-\\.?\\d`), so the split form parses there. It still does not on
3.12, which is what `tests.yml` pins and what CI actually runs, so the fused
form remains required — see `t_argparse_really_does_reject_the_split_form`,
which reports the running interpreter's behaviour instead of asserting one.

Nothing caught it because nothing was looking: `run()` prints the non-zero exit
and carries on (deliberately — one dead metro must not abort a sweep of 165),
the workflow is failure-tolerant by design, and the closing line still said
"swept 165 metros across 28 countries". A green run proving nothing is the
documented hazard of this repo; this is what it looks like in the wild.

The fix is `--latlong=VALUE`. The fused form is not a style preference, it is
the only spelling argparse cannot misread, so these tests pin the FORM rather
than the outcome — a future edit back to the split form fails here instead of
silently losing three countries again.

    python test_sweep_global.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mapsee_sweep_global as sweep

FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def child_parser() -> argparse.ArgumentParser:
    """A stand-in declared the way every ingester declares it.

    mapsee_ingest_meetup.py and mapsee_ingest_seatgeek.py both use
    `add_argument("--latlong", required=True)`; mapsee_ingest.py puts it in a
    group. The `required` part is what turns a misread value into a hard exit
    rather than a silent None, but the misreading itself is argparse's option
    detection and is identical either way.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--latlong", required=True)
    p.add_argument("--radius")
    return p


def captured_argv(latlong: str, sources: str = "ticketmaster,meetup") -> list[list[str]]:
    """Run the sweep over a one-metro config with subprocess.run stubbed out."""
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))
        class R:  # noqa: D401 - a CompletedProcess stand-in
            returncode = 0
        return R()

    cfg = {"countries": [{"code": "AU", "name": "Test",
                          "metros": [{"name": "Test Metro", "latlong": latlong, "radius": 25}]}]}
    tmp = HERE / "_sweep_test_config.json"
    tmp.write_text(json.dumps(cfg), encoding="utf-8")
    real_run, real_sleep = sweep.subprocess.run, sweep.time.sleep
    sweep.subprocess.run = fake_run
    sweep.time.sleep = lambda *_a, **_k: None
    try:
        sweep.main(["--config", tmp.name, "--store", "x.json", "--sources", sources])
    finally:
        sweep.subprocess.run, sweep.time.sleep = real_run, real_sleep
        tmp.unlink(missing_ok=True)
    return calls


def t_argparse_really_does_reject_the_split_form():
    """The premise — which is a property of the INTERPRETER, not of this repo.

    Python 3.14 rewrote `_negative_number_matcher` from a full match
    (`^-\\d+$|^-\\d*\\.\\d+$`) to a prefix match (`-\\.?\\d`), so a token like
    `-33.8688,151.2093` now reads as a value rather than an unknown option and
    the split form works. Asserting the bug unconditionally therefore FAILED on
    3.14 while the code was perfectly correct — and the tempting "fix" for that
    red line is to drop the fusing, which would resurrect the outage the moment
    it ran on CI, where `tests.yml` pins python-version 3.12.

    So the premise is reported for whichever argparse is running, and what is
    ASSERTED is the thing that has to be true on every version: the fused form
    parses, and it is what the sweep emits (the next two tests).
    """
    p = child_parser()
    try:
        p.parse_args(["--latlong", "-33.8688,151.2093"])
        rejected = False
    except SystemExit:
        rejected = True
    print(f"[note] python {sys.version_info.major}.{sys.version_info.minor}: "
          f"argparse {'REJECTS' if rejected else 'accepts'} the split form "
          f"`--latlong -33.87,151.21`"
          + ("  (the original bug — fusing is load-bearing here)" if rejected else
             "  (3.14+ negative-number matcher; fusing is still required for the "
             "3.12 CI pins and every older runtime)"))

    ns = p.parse_args(["--latlong=-33.8688,151.2093"])
    check("argparse accepts the fused form and keeps the value intact",
          ns.latlong == "-33.8688,151.2093", repr(ns.latlong))

    # Northern-hemisphere metros never reproduced this, which is exactly why it
    # survived: every US and European metro in the config worked perfectly.
    ns = p.parse_args(["--latlong", "47.6062,-122.3321"])
    check("a positive latitude parses either way — why this hid for so long",
          ns.latlong == "47.6062,-122.3321", repr(ns.latlong))


def t_sweep_emits_the_fused_form():
    calls = captured_argv("-33.8688,151.2093")
    check("both sources are invoked for one metro", len(calls) == 2, f"{len(calls)} calls")
    for cmd in calls:
        script = Path(cmd[1]).name
        check(f"{script}: no bare --latlong token", "--latlong" not in cmd, " ".join(cmd[2:]))
        fused = [c for c in cmd if c.startswith("--latlong=")]
        check(f"{script}: passes --latlong=<value>", len(fused) == 1, " ".join(cmd[2:]))
        if fused:
            check(f"{script}: the child parses what the sweep sends",
                  child_parser().parse_known_args(cmd[2:])[0].latlong == "-33.8688,151.2093")


def t_every_configured_metro_survives_the_round_trip():
    """The real config, every metro, through the real argv builder.

    Cheap, and it is the assertion that would have caught this on the day: 22
    of these come back negative and every one of them used to die.
    """
    cfg = json.loads((HERE / "metros_global.json").read_text(encoding="utf-8"))
    metros = [(c.get("name"), m.get("name"), m["latlong"])
              for c in cfg.get("countries", []) for m in c.get("metros", []) if m.get("latlong")]
    check("the config still has metros to sweep", len(metros) > 100, f"{len(metros)}")

    southern = [m for m in metros if m[2].strip().startswith("-")]
    check("southern-hemisphere metros are present (else this test proves nothing)",
          len(southern) >= 20, f"{len(southern)}")

    bad = []
    for country, name, ll in metros:
        try:
            lat, lon = (float(x) for x in ll.split(","))
        except ValueError:
            bad.append(f"{country}/{name} unparseable: {ll!r}")
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            bad.append(f"{country}/{name} out of range: {ll}")
        got = child_parser().parse_known_args([f"--latlong={ll}"])[0].latlong
        if got != ll:
            bad.append(f"{country}/{name} mangled: {ll!r} -> {got!r}")
    check("every metro is a valid lat,lon that survives argv", not bad, "; ".join(bad[:4]))


def t_deadline_stops_before_spawning_and_is_not_reported_as_swept():
    """The Meetup job ran to 326 of its 360 minutes (2026-09-04), and 360 is
    GitHub's own hard job limit, where no `always()` sync can run. The sweep now
    stops spawning metros at the job's deadline; a metro not started must not be
    counted in "swept N metros", and the per-metro timeout is 600s, not 1800."""
    import contextlib
    import io

    cfg = {"countries": [{"code": "GB", "name": "Test", "metros": [
        {"name": f"M{i}", "latlong": f"5{i}.0,-1.0", "radius": 25} for i in range(3)]}]}
    import tempfile

    # A temp dir, not the repo root: a killed run must not leave a stray config.
    # The sweep reads HERE / --config, and an absolute path replaces HERE.
    folder = tempfile.TemporaryDirectory()
    tmp = Path(folder.name) / "sweep_deadline_config.json"
    tmp.write_text(json.dumps(cfg), encoding="utf-8")
    clock = {"now": 1_000_000.0}
    calls: list = []

    def fake_run(cmd, **kw):
        calls.append((Path(cmd[1]).name, next(c for c in cmd if c.startswith("--latlong=")),
                      kw.get("timeout")))
        clock["now"] += 100                       # each metro costs 100 fake seconds
        class R:  # noqa: D401
            returncode = 0
        return R()

    real = (sweep.subprocess.run, sweep.time.sleep, sweep.time.time)
    sweep.subprocess.run, sweep.time.sleep = fake_run, (lambda *_a, **_k: None)
    sweep.time.time = lambda: clock["now"]
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # Metros start at t=0 and t=100; the one at t=200 is past t=150.
            sweep.main(["--config", str(tmp), "--store", "x.json", "--sources", "meetup",
                        "--deadline", str(clock["now"] + 150)])
        log = buf.getvalue()
        check("the deadline is checked before spawning: 2 of 3 metros started",
              [c[1] for c in calls] == ["--latlong=50.0,-1.0", "--latlong=51.0,-1.0"], str(calls))
        check("a metro not started is not reported as swept",
              "swept 2 of 3 metros" in log and "swept 3" not in log, log.strip()[-160:])
        check("the stop is named, with the count of metros not started",
              "deadline reached before Test / M2" in log and "1 of 3 metros not started" in log,
              log.strip()[-200:])
        check("...as a GitHub annotation: the line opens with ::warning::",
              any(l.startswith("::warning::") and "deadline reached" in l for l in log.splitlines()),
              log.strip()[-200:])
        check("each metro's subprocess timeout is 600s, not 1800",
              all(c[2] == 600 for c in calls), str(calls))

        calls.clear()
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            sweep.main(["--config", str(tmp), "--store", "x.json", "--sources", "meetup"])
        check("no deadline sweeps every metro and reports it the way it always has",
              len(calls) == 3 and "swept 3 metros across 1 countries" in buf.getvalue(),
              buf.getvalue().strip()[-120:])
    finally:
        sweep.subprocess.run, sweep.time.sleep, sweep.time.time = real
        folder.cleanup()


def t_the_country_the_deadline_drops_is_not_always_the_same_one():
    """The sweep has no cursor: it walks the config from the top and stops when
    the job's clock runs out. metros_global.json is in the order countries were
    ADDED, which leaves Brazil, Hong Kong, the UAE, South Korea, Singapore and
    South Africa at the bottom — six of the eight thinnest countries in the
    catalog, permanently first to be dropped."""
    cfg = json.loads((HERE / "metros_global.json").read_text(encoding="utf-8"))
    countries = cfg.get("countries", [])
    n = len([c for c in countries if any(m.get("latlong") for m in c.get("metros", []))])
    check("the config has enough countries for a rotation to mean anything",
          n > 5, f"{n} countries")

    firsts = {sweep.rotate_countries(countries, day)[0].get("code") for day in range(n)}
    check("every country leads the sweep at least once in one turn of the wheel",
          len(firsts) == n, f"{len(firsts)} of {n} lead")

    # The set is preserved; only where it STARTS moves.
    for day in (0, 1, 7, n - 1):
        rot = sweep.rotate_countries(countries, day)
        check(f"day {day} sweeps every country exactly once",
              sorted(c.get("code") for c in rot)
              == sorted(c.get("code") for c in countries
                        if any(m.get("latlong") for m in c.get("metros", []))))

    # A country is swept whole or not at all — its metros are written
    # largest-first and splitting one is how you keep half a country for ever.
    rot = sweep.rotate_countries(countries, 3)
    check("a country's own metro order is untouched",
          all(rot[i].get("metros") == next(c for c in countries
                                           if c.get("code") == rot[i].get("code")).get("metros")
              for i in range(len(rot))))

    tail = countries[-1].get("code")
    leads = [sweep.rotate_countries(countries, d)[0].get("code") for d in range(n)]
    check(f"the country at the bottom of the file ({tail}) leads on one of them",
          tail in leads)

    check("MAPSEE_TODAY fixes the rotation the way it fixes everything else",
          sweep._day_ordinal.__doc__ and "MAPSEE_TODAY" in sweep._day_ordinal.__doc__)


def main() -> int:
    for t in (t_argparse_really_does_reject_the_split_form,
              t_sweep_emits_the_fused_form,
              t_every_configured_metro_survives_the_round_trip,
              t_deadline_stops_before_spawning_and_is_not_reported_as_swept,
              t_the_country_the_deadline_drops_is_not_always_the_same_one):
        print(f"\n--- {t.__name__} ---")
        t()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("all global-sweep tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
