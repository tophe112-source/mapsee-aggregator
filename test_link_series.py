#!/usr/bin/env python3
"""Grouping rules for mapsee_link_series — no network, no database."""
import mapsee_link_series as S

def row(i, title, lat=47.6685, lon=-122.3838, date="2026-08-01", hh="17", series=None, claimed=None):
    return {"id": f"{i:08d}-0000-4000-8000-000000000000", "title": title, "lat": lat, "lon": lon,
            "starts_at": f"{date}T{hh}:00:00+00:00", "series_id": series, "claimed_at": claimed}

def run(rows, **kw):
    kw = {"radius_km": 0.2, "min_dates": 2, "max_gap_days": 45, **kw}
    return S.find_series(rows, kw["radius_km"], kw["min_dates"], kw["max_gap_days"])

ok = fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1; print(f"  ok   {name}")
    else:    fail += 1; print(f"  FAIL {name}")

print("weekly market chains")
weekly = [row(i, "Ballard Farmers Market", date=d) for i, d in
          enumerate(["2026-08-02","2026-08-09","2026-08-16","2026-08-23"], 1)]
s = run(weekly)
check("one series", len(s) == 1)
check("root is the earliest", s[0][0] == weekly[0]["id"])
# the root is stamped with its OWN id too — that is what the app writes when a
# host links a series by hand, and it is what makes a second run a no-op
check("all four rows stamped, root included", len(s[0][1]) == 4)

print("\na one-off is not a series")
check("no series", run([row(1, "Block Party")]) == [])

print("\nsame title, same DAY = duplicates, not a series")
same_day = [row(1, "Night Market", hh="17"), row(2, "Night Market", hh="18")]
check("no series", run(same_day) == [])

print("\nduplicates inside a real series still chain")
dupes = [row(1,"Trivia",date="2026-08-04"), row(2,"Trivia",date="2026-08-04"), row(3,"Trivia",date="2026-08-11")]
s = run(dupes)
check("one series holding all three rows", len(s) == 1 and len(s[0][1]) == 3)

print("\ndifferent venues do not chain")
apart = [row(1,"Open Mic",lat=47.60,lon=-122.33,date="2026-08-02"),
         row(2,"Open Mic",lat=47.70,lon=-122.40,date="2026-08-09")]
check("no series", run(apart) == [])

print("\nannual repeat splits on the gap")
annual = [row(1,"Winter Market",date="2026-01-10"), row(2,"Winter Market",date="2027-01-09")]
check("no series", run(annual) == [])

print("\na season that returns next year is two series, not one")
season = [row(i,"Summer Market",date=d) for i,d in enumerate(
    ["2026-06-06","2026-06-13","2027-06-05","2027-06-12"], 1)]
s = run(season)
check("two series", len(s) == 2)
check("roots differ", s[0][0] != s[1][0])

print("\nan existing series_id wins (host's chain is extended, not replaced)")
host = "aaaaaaaa-0000-4000-8000-000000000000"
mixed = [row(1,"Yoga",date="2026-08-03",series=host), row(2,"Yoga",date="2026-08-10")]
s = run(mixed)
check("root is the host's", len(s) == 1 and s[0][0] == host)
check("only the unchained row is stamped", len(s[0][1]) == 1 and s[0][1][0]["id"] == mixed[1]["id"])

print("\nidempotent: a fully chained series writes nothing")
chained = [row(1,"Yoga",date="2026-08-03",series=host), row(2,"Yoga",date="2026-08-10",series=host)]
check("nothing to do", run(chained) == [])

print("\nidempotent from cold: a second pass over what the first pass wrote is a no-op")
cold = [row(i,"Ballard Farmers Market",date=d) for i,d in enumerate(
    ["2026-08-02","2026-08-09","2026-08-16"], 1)]
for root, todo in run(cold):                      # apply the first pass in memory
    for r in todo:
        r["series_id"] = root
check("second pass writes nothing", run(cold) == [])

print("\nclaimed rows are never touched")
cl = [row(1,"Yoga",date="2026-08-03",claimed="2026-07-01T00:00:00Z"), row(2,"Yoga",date="2026-08-10")]
check("no series (only one unclaimed row left)", run(cl) == [])

print("\ntitles normalize (case, punctuation, leading 'the')")
norm = [row(1,"The Night Market!",date="2026-08-02"), row(2,"night market",date="2026-08-09")]
check("one series", len(run(norm)) == 1)

print("\nlocal date, not UTC date: 7pm Pacific on two nights is two dates")
pac = [row(1,"Trivia",lon=-122.3,date="2026-08-05",hh="03"),   # Aug 4, 7pm PDT
       row(2,"Trivia",lon=-122.3,date="2026-08-12",hh="03")]   # Aug 11, 7pm PDT
s = run(pac)
check("one series", len(s) == 1)
check("dates read local", {r["_date"] for r in pac} == {"2026-08-04","2026-08-11"})

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
