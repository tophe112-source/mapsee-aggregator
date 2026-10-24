#!/usr/bin/env python3
"""
test_sync_all_day.py — an all-day row must span the day the SOURCE named, in the
timezone the EVENT is in.

THE FAILURE THIS PREVENTS. Six adapters deliberately emit a bare `YYYY-MM-DD`
when the source publishes no clock, and each one says so in a comment: parkrun,
BikeReg, RunSignup, Seattle Center's "All Day", the civic feeds' exact-midnight
rows, and Ticketmaster's `timeTBA` listings (the Mariners' ballpark tour is one).
A bare date handed to a `timestamptz` column is read at the SERVER's clock, and
`_compute_end`'s naive `T23:59:59` landed the same way — so a Seattle all-day
event reached the phone as

    Today, 5:00 PM  ->  Tomorrow, 4:59 PM

which is the wrong two days, a nineteen-hour window, and hours nobody published.

Run: python test_sync_all_day.py
"""
import sys
from datetime import datetime, timezone

import mapsee_supabase_sync as S

SEATTLE = (47.5914, -122.3325)          # T-Mobile Park
BERLIN = (52.5200, 13.4050)             # the other side of Greenwich, so a bug
                                        # that cancels out in the Americas shows
HOST = "00000000-0000-0000-0000-000000000001"


def row(**over):
    """One store record through to_row — the only path production writes by."""
    rec = {"name": "Seattle Mariners Ballpark Tour", "category": "sports",
           "latitude": SEATTLE[0], "longitude": SEATTLE[1], "promoter": "SEATTLE MARINERS",
           "fingerprint": "tm-ballpark-tour"}
    rec.update(over)
    return S.to_row(rec, HOST)


def local_day(stamp, tz_name):
    """What wall-clock day/time `stamp` reads as where the event is."""
    from zoneinfo import ZoneInfo
    d = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return d.astimezone(ZoneInfo(tz_name))


def main():
    checks = []

    # ---- 1. The Mariners' ballpark tour: a date, no clock.
    r = row(start_local="2026-09-04")
    lo = local_day(r["starts_at"], "America/Los_Angeles")
    hi = local_day(r["ends_at"], "America/Los_Angeles")
    checks.append((lo.date().isoformat() == "2026-09-04" and (lo.hour, lo.minute) == (0, 0),
                   f"an all-day row starts at local midnight ON the named day ({lo})"))
    checks.append((hi.date().isoformat() == "2026-09-04" and (hi.hour, hi.minute) == (23, 59),
                   f"...and ends on that SAME local day, not the next one ({hi})"))
    checks.append((r["starts_at"] == "2026-09-04T07:00:00Z",
                   f"the stored instant carries the venue's offset ({r['starts_at']})"))

    # ---- 2. The direction of the shift is not an Americas coincidence.
    r = row(start_local="2026-09-04", latitude=BERLIN[0], longitude=BERLIN[1])
    lo = local_day(r["starts_at"], "Europe/Berlin")
    hi = local_day(r["ends_at"], "Europe/Berlin")
    checks.append((lo.date().isoformat() == hi.date().isoformat() == "2026-09-04",
                   f"east of Greenwich the day is the named one too ({lo} -> {hi})"))
    checks.append((r["starts_at"] == "2026-09-03T22:00:00Z",
                   f"...which means a UTC stamp on the PREVIOUS date ({r['starts_at']})"))

    # ---- 3. A multi-day all-day listing keeps both of its ends.
    r = row(start_local="2026-09-04", end_local="2026-09-06")
    hi = local_day(r["ends_at"], "America/Los_Angeles")
    checks.append((hi.date().isoformat() == "2026-09-06" and (hi.hour, hi.minute) == (23, 59),
                   f"a multi-day all-day row ends at the close of its LAST day ({hi})"))

    # ---- 4. A day is 23 hours twice a year, and only the tz database knows it.
    lo, hi = S._day_bounds("2027-03-14", *SEATTLE)         # US spring-forward
    span = (datetime.strptime(hi, "%Y-%m-%dT%H:%M:%SZ")
            - datetime.strptime(lo, "%Y-%m-%dT%H:%M:%SZ")).total_seconds()
    checks.append((span == 23 * 3600 - 1,
                   f"a spring-forward day is bracketed as 23 hours, not 24 ({span/3600:.2f}h)"))

    # ---- 5. A TIMED row is not touched. This is the whole rest of the corpus,
    # and the fix has to be invisible to it.
    r = row(start_utc="2026-09-04T01:40:00Z")
    checks.append((r["starts_at"] == "2026-09-04T01:40:00Z",
                   f"a timed row passes through untouched ({r['starts_at']})"))
    checks.append((r["ends_at"] == "2026-09-04T04:40:00Z",
                   f"...and still gets its category-typical end ({r['ends_at']})"))
    r = row(start_local="2026-09-04T18:40:00")             # naive local
    checks.append((r["starts_at"] == "2026-09-05T01:40:00Z",
                   f"a naive local time is still converted via the coordinates ({r['starts_at']})"))

    # ---- 6. Coordinates are required for every row this sync writes, but a
    # missing one must degrade to the old behaviour rather than raise.
    checks.append((S._anchor_all_day("2026-09-04", None, None, None)
                   == ("2026-09-04T00:00:00Z", "2026-09-04T23:59:59Z"),
                   "no coordinates falls back to UTC instead of throwing"))
    checks.append((S._anchor_all_day("not-a-date", None, *SEATTLE) == ("not-a-date", None),
                   "an unparseable value is left alone, not guessed at"))

    # ---- 7. An end at or before the start is no end (2026-10-23: 33 of 5,880
    # live rows). berlin.de's JSON-LD ended a 16:30 handball match at 00:00Z
    # that morning, so it left the map 15.5 h before it began.
    r = row(name="Füchse Berlin - HSG Wetzlar", category="arts", latitude=BERLIN[0],
            longitude=BERLIN[1], start_utc="2026-10-25T15:30:00Z", end_utc="2026-10-25T00:00:00Z")
    checks.append((r["ends_at"] == "2026-10-25T18:30:00Z",
                   f"an end before the start gives the category's +3 h instead ({r['ends_at']})"))
    end = S._compute_end("2026-10-24T02:30:00Z", "2026-10-24T02:30:00Z", "community")
    checks.append((end == "2026-10-24T04:30:00Z",
                   f"an end AT the start (Town Hall Seattle) gives community's +2 h ({end})"))
    end = S._compute_end("2026-10-24T20:00:00+02:00", "2026-10-24T18:00:00Z", "music")
    checks.append((end == "2026-10-24T23:00:00+02:00",
                   f"...compared as instants, not as text ({end})"))
    end = S._compute_end("2026-10-24T21:00:00Z", "2026-10-25T02:00:00Z", "party")
    checks.append((end == "2026-10-25T02:00:00Z",
                   f"a real overnight end the next day is kept as given ({end})"))
    end = S._compute_end("2026-10-24T21:00:00", "2026-10-24T09:00:00", "party")
    checks.append((end == "2026-10-24T09:00:00",
                   f"a naive pair is passed through as before, not guessed at ({end})"))

    # ---- 8. ...and time_changed reads it the same way. Once the Wednesday
    # refresh stores the default end, comparing the source's raw end with it
    # would read as a new time on every daily --only-new run, for good.
    for label, cat, start, end_, lat, lon in [
        ("an end AT the start (Town Hall)", "community", "2026-10-28T02:30:00Z", "2026-10-28T02:30:00Z", *SEATTLE),
        ("an end before the start (berlin.de)", "arts", "2026-10-25T15:30:00Z", "2026-10-25T00:00:00Z", *BERLIN),
    ]:
        rec = {"name": "x", "category": cat, "latitude": lat, "longitude": lon,
               "fingerprint": "fp", "start_utc": start, "end_utc": end_}
        r = S.to_row(rec, HOST)
        stored = {"starts_at": r["starts_at"], "ends_at": r["ends_at"]}
        checks.append((S.time_changed(rec, stored) is False,
                       f"{label}, stored with its default end {r['ends_at']}, is not a time change"))
        moved = dict(rec, start_utc="2026-10-28T03:30:00Z" if cat == "community" else "2026-10-25T16:30:00Z")
        checks.append((S.time_changed(moved, stored) is True,
                       f"...but the same row an hour later still is"))
    rec = {"name": "x", "category": "community", "latitude": SEATTLE[0], "longitude": SEATTLE[1],
           "fingerprint": "fp", "start_utc": "2026-10-28T02:30:00Z", "end_utc": "2026-10-28T05:00:00Z"}
    checks.append((S.time_changed(rec, {"starts_at": "2026-10-28T02:30:00Z", "ends_at": "2026-10-28T04:30:00Z"}) is True,
                   "a REAL end after the start that moved is still a time change"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
