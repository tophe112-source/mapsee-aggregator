#!/usr/bin/env python3
"""
test_ingest_programs.py - the monthly rules behind the free museum days, and the
wording contract with ../mapsee's offer tagger (migration 0227).

A museum that is free on the first Sunday is not free on the other three, so a
wrong date here puts a free day on the map that is not one. And the free/not-free
reading is made in the DATABASE from each row's own text, so a programme's blurb
is a contract: "free for everyone" must be said in words the tagger reads as
free, and "free for cardholders" must never be.

    python test_ingest_programs.py
"""
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

os.environ["MAPSEE_TODAY"] = "20260927"
import mapsee_ingest_programs as P  # noqa: E402

checks = []


def check(cond, label):
    checks.append((bool(cond), label))


def days(weekdays=(6,), horizon=70, **kw):
    return [d.isoformat() for d in P._occurrences(list(weekdays), horizon, None, None, **kw)]


# ---------------------------------------------------------------- 1. the rules
check(days(nth=1) == ["2026-10-04", "2026-11-01"],
      "first Sunday: 4 Oct and 1 Nov 2026 (6 Dec is past the 70-day horizon)")
check(days(nth=1, months=[1, 2, 3, 11, 12]) == ["2026-11-01"],
      "November to March only: the first Sunday in October is not free at a CMN monument")
fw = days(weekdays=(), rule="first_full_weekend")
check(fw == ["2026-10-03", "2026-10-04", "2026-11-07", "2026-11-08", "2026-12-05"],
      f"first full weekends: Sat+Sun 3-4 Oct and 7-8 Nov, Sat 5 Dec (got {fw})")
check("2026-11-01" not in fw,
      "1 Nov 2026 is a Sunday whose Saturday was in October - not the first FULL weekend")
check(days(weekdays=(0, 1, 2, 3, 4), horizon=14, nth=None) == [
      "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
      "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"],
      "a weekday programme with no monthly rule is unchanged (Mon-Fri, every week)")


# ------------------------------------------------ 2. a site, the way it is written
class NoNetwork:
    def get(self, *a, **k):
        raise AssertionError("geocoder called for a site that carries its own coordinates")


prog = {"name": "Domenica al museo", "category": "arts", "categories": ["learning"],
        "days": "Sunday", "nth": 1, "horizon_days": 70, "free_for": "everyone",
        "title_prefix": "Free first Sunday",
        "blurb": "Free admission for everyone on the first Sunday of the month.",
        "url": "https://cultura.gov.it/domenicalmuseo",
        "sites": [{"name": "Pantheon", "city": "Roma", "region": "Lazio", "country": "Italy",
                   "lat": 41.8986, "lon": 12.4769, "notes": "Booking required."}]}
try:
    evs = P.program_events(prog, NoNetwork())
    check(True, "a site with lat/lon is never geocoded")
except AssertionError as exc:
    evs = []
    check(False, str(exc))
check(len(evs) == 2, f"one event per free Sunday in the horizon (got {len(evs)})")
if evs:
    e = evs[0]
    check(e.start_local == "2026-10-04" and e.end_local == "2026-10-04",
          "all-day: the museum's own hours decide, so a date and no clock")
    check((e.city, e.region, e.country) == ("Roma", "Lazio", "Italy"), "city, region and country pass through")
    check(e.category == "arts" and e.categories == ["learning"], "category plus a learning secondary")
    check("Booking required." in (e.description or ""), "per-site notes reach the description")
    check(e.ticket_url == "https://cultura.gov.it/domenicalmuseo", "the official page is the link")
    check(e.name == "Free first Sunday - Pantheon", "title is prefix - site")
    check(len({ev.fingerprint for ev in evs}) == 2, "each date is its own row")

# ------------------------------------------- 3. the wording contract, on the live file
# The phrases 0227 reads as FREE FOR EVERYONE. A conditional programme must not
# contain them, and must not open its title with "Free".
FREE_FOR_ALL = re.compile(r"\bfree\s+(?:admission|entry|entrance)\b(?!\s+for\s+(?:\w+\s+){0,3}"
                          r"(?:members|cardholders|subscribers|students|seniors|residents|veterans|military)\b)"
                          r"|\b(?:admission|entry)\s+is\s+free\b", re.I)
try:
    live = json.load(open("program_sources.json", encoding="utf-8"))
except FileNotFoundError:
    live = []
monthly = [p for p in live if p.get("nth") or p.get("rule")]
for p in monthly:
    name = p.get("name", "?")
    who = p.get("free_for")
    check(bool(who), f"{name}: says who it is free for (free_for)")
    blurb = p.get("blurb") or ""
    if who == "everyone":
        check("Free admission for everyone" in blurb,
              f"{name}: free for everyone, and says so in words the tagger reads as free")
    else:
        check(who and who.lower() in blurb.lower(), f"{name}: the blurb names who it is free for ({who})")
        check(not FREE_FOR_ALL.search(blurb), f"{name}: no free-for-everyone phrase in a conditional offer")
        check(not (p.get("title_prefix") or "").lower().startswith("free"),
              f"{name}: a conditional offer's title does not open with 'Free'")
    for s in p.get("sites", []):
        ok = isinstance(s.get("lat"), (int, float)) and isinstance(s.get("lon"), (int, float)) and s.get("_coords")
        if not ok:
            check(False, f"{name}: {s.get('name')} carries coordinates with a provenance (_coords)")
            break
    else:
        check(True, f"{name}: every site carries coordinates with a provenance ({len(p.get('sites', []))} sites)")

failed = [label for ok, label in checks if not ok]
for ok, label in checks:
    print(("ok  " if ok else "FAIL") + "  " + label)
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
