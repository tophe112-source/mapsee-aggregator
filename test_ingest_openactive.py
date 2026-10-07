#!/usr/bin/env python3
"""
test_ingest_openactive.py — the four ways an OpenActive feed lies to a reader.

Every case here was bought with a measurement on 2026-08-26, and three of the
four are indistinguishable from a healthy read unless you already know to look.

Run: python test_ingest_openactive.py
"""
import sys
from datetime import datetime, timedelta, timezone

import mapsee_ingest_openactive as OA

NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
SRC = {"name": "Test Publisher", "category": "fitness", "country": "GB"}


def _page(items, nxt=None):
    return {"items": items, "next": nxt, "license": "https://creativecommons.org/licenses/by/4.0/"}


def _item(ident, start, name="Yoga", state="updated", geo=(51.5, -0.12)):
    data = {"@id": f"https://example.test/s/{ident}", "name": name, "startDate": start}
    if geo:
        data["location"] = {"@type": "Place", "name": "Leisure Centre",
                            "geo": {"latitude": geo[0], "longitude": geo[1]},
                            "address": {"streetAddress": "1 High St",
                                        "addressLocality": "London",
                                        "postalCode": "N1 1AA"}}
    return {"id": ident, "state": state, "data": data}


def main():
    checks = []
    soon = (NOW + timedelta(days=3)).isoformat()
    later = (NOW + timedelta(days=9)).isoformat()

    # ---------------------------------------------------------------- 1. RPDE
    #
    # THE FIRST PAGE IS THE OLDEST DATA. Measured: Our Parks reports 0 future
    # sessions on page one and 854 when walked to the end; England Netball
    # reports 0 either way and has been dead since 2019. A reader that stops at
    # page one gets both of those wrong, in opposite directions — which is why
    # "it answered 200 and I saw records" is not evidence of anything.
    pages = {
        "u0": _page([_item("a", "2015-01-01T10:00:00Z", "Ancient")], "u1"),
        "u1": _page([_item("b", soon, "Live one")], "u2"),
        "u2": _page([], "u2"),
    }
    OA._get = lambda url, timeout=30.0: pages[url]
    data, why = OA.walk("u0", delay=0, sleep=lambda _: None)
    checks.append((len(data) == 2, "walk() reads PAST page one — the live session is on page two"))
    checks.append(("end of feed" in why, "and it reports reaching the end"))

    # A self-referential `next` is how several implementations spell "done".
    # Without this the walk spins to the page cap and reports a false cap-hit.
    spin = {"u0": _page([_item("a", soon)], "u0")}
    OA._get = lambda url, timeout=30.0: spin[url]
    data, why = OA.walk("u0", delay=0, sleep=lambda _: None)
    checks.append((len(data) == 1 and "end of feed" in why,
                   "a `next` pointing at the page you just read is the end, not a loop"))

    # A TOMBSTONE MUST REMOVE. `state: deleted` is how a publisher cancels a
    # session; dropping it on the floor leaves a cancelled class on the map.
    pages2 = {
        "v0": _page([_item("x", soon, "Cancelled class")], "v1"),
        "v1": _page([{"id": "x", "state": "deleted"}], "v2"),
        "v2": _page([], "v2"),
    }
    OA._get = lambda url, timeout=30.0: pages2[url]
    data, _ = OA.walk("v0", delay=0, sleep=lambda _: None)
    checks.append((data == {}, "a deleted tombstone REMOVES the session it names"))

    # A cap that bites must SAY so. A silent one reads as "we read the feed".
    deep = {f"w{i}": _page([_item(f"i{i}", soon)], f"w{i+1}") for i in range(20)}
    OA._get = lambda url, timeout=30.0: deep[url]
    _, why = OA.walk("w0", max_pages=3, delay=0, sleep=lambda _: None)
    checks.append(("PAGE CAP" in why, "hitting the page cap is reported loudly, never silently"))

    OA._get = lambda url, timeout=30.0: pages[url]

    # ------------------------------------------------- 2. the superEvent join
    #
    # A ScheduledSession knows its DATE and nothing a human could read. Merged
    # the wrong way round, every occurrence of a fifty-week block inherits the
    # series' own startDate and lands on one Monday — the MyListing bug exactly.
    series = {"@id": "S1", "name": "Beginners Swim", "location":
              {"geo": {"latitude": 51.5, "longitude": -0.12}},
              "startDate": "2023-01-02T10:00:00Z", "offers": [{"price": 0}]}
    occurrence = {"@id": "O9", "startDate": soon, "superEvent": "S1"}
    merged = OA.merge_occurrence(occurrence, series)
    checks.append((merged["startDate"] == soon,
                   "the OCCURRENCE's date wins — never the series' own startDate"))
    checks.append((merged["name"] == "Beginners Swim",
                   "and the series supplies the name the occurrence does not have"))
    checks.append((OA._series_key({"superEvent": {"@id": "S1"}}) == "S1",
                   "superEvent is read as an object as well as a bare string"))

    ev, why = OA.to_event(merged, SRC, NOW, 120)
    checks.append((ev is not None and ev.name == "Beginners Swim",
                   "a joined occurrence becomes a placeable row"))
    checks.append((ev is not None and "Free to attend" in (ev.description or ""),
                   "an all-zero offer set reads as free"))

    # ------------------------------------------------------ 3. sentinel dates
    #
    # British Cycling's Let's Ride publishes 172 rides dated in the YEAR 2500.
    # mapsee_cleanup.py deletes the PAST, so a 2500 ride is a pin nothing can
    # ever remove. The horizon is what stops it entering at all.
    far = {"@id": "F1", "name": "Ride", "startDate": "2500-06-01T09:00:00Z",
           "location": {"geo": {"latitude": 51.5, "longitude": -0.12}}}
    ev, why = OA.to_event(far, SRC, NOW, 120)
    checks.append((ev is None and why == "beyond horizon",
                   "a session dated 2500 is refused by the horizon, not stored forever"))

    past = {"@id": "P1", "name": "Ride", "startDate": "2019-06-01T09:00:00Z",
            "location": {"geo": {"latitude": 51.5, "longitude": -0.12}}}
    checks.append((OA.to_event(past, SRC, NOW, 120)[1] == "past",
                   "and England Netball's 2019 archive is refused as past"))

    # ------------------------------------------------------- 4. no coordinate
    #
    # The only geocoder in this pipeline is US Census. A GB session with no
    # geo is dropped by the SYNC, silently, after the adapter has reported
    # "kept N" — the Calgary Tribe bug. Drop it here, where it can be counted.
    nogeo = {"@id": "N1", "name": "Class", "startDate": soon}
    checks.append((OA.to_event(nogeo, SRC, NOW, 120)[1] == "no coordinates",
                   "a session with no coordinates is refused HERE, where it is counted"))

    zero = {"@id": "N2", "name": "Class", "startDate": soon,
            "location": {"geo": {"latitude": 0, "longitude": 0}}}
    checks.append((OA.to_event(zero, SRC, NOW, 120)[1] == "no coordinates",
                   "0,0 is the Atlantic, i.e. an unfilled form — not a location"))

    # ------------------------------------------------------------ identity
    #
    # One series, many occurrences. Keying on the series would make each week
    # DELETE the one before it (BikeReg: 1,269 in, 1,148 surviving).
    a = OA.to_event({**merged, "startDate": soon}, SRC, NOW, 120)[0]
    b = OA.to_event({**merged, "startDate": later}, SRC, NOW, 120)[0]
    checks.append((a.fingerprint != b.fingerprint,
                   "two dates of one weekly class are two rows, not one that overwrites"))

    # ------------------------------------------------------------- licensing
    checks.append(("CC-BY 4.0" in (a.description or "") and SRC["name"] in (a.description or ""),
                   "every row carries the publisher and the CC-BY licence it is held under"))
    checks.append((a.coords_exact is True,
                   "OpenActive coordinates are surveyed — never geocode over them"))

    # ------------------------------------------------------------ free/paid
    checks.append((OA.is_free({"offers": [{"price": 0}, {"price": 5}]}) is False,
                   "a free taster beside a paid block is NOT a free session"))
    checks.append((OA.is_free({"offers": []}) is False, "no offers is not a price of zero"))

    # -------------------------------------------------- address, not a guess
    parts = OA.address_parts({"address": {"streetAddress": "1 High St",
                                          "addressLocality": "London",
                                          "addressRegion": "Greater London",
                                          "postalCode": "N1 1AA"}})
    checks.append((parts["address"] == "1 High St" and parts["city"] == "London",
                   "a structured PostalAddress is read field by field, never by comma position"))
    checks.append((OA.address_parts({"address": {"addressLocality": "London"}})["address"] is None,
                   "a city never gets promoted into the street field — that moves the pin"))

    # ------------------------------------------------- 5. the booking grid
    #
    # A session published every ten minutes is a bookable slot wearing a
    # ScheduledSession's clothes, and it passes the FacilityUse/Slot refusal.
    # Measured in a ±0.03 central-London box: ONE pool published "Swim For
    # Fitness" 255 times in a week, 110 of them on one day from 05:40 at
    # ten-minute spacing, and three pairs like it were about half of everything
    # events_near returns for that viewport.
    from mapsee_ingest import NormalizedEvent

    def _slot(day, minutes, name="Swim For Fitness", lat=51.5, lon=-0.12, dur=10):
        """`minutes` is minutes past midnight — the arithmetic has to carry into
        the hour, which the first version of this fixture did not, and it built
        an 05:00 row in a run that was meant to start at 05:40."""
        def hhmm(m):
            return f"{m // 60:02d}:{m % 60:02d}"
        return NormalizedEvent(
            source="openactive:Pool", source_id=f"{name}-{day}-{minutes}", name=name,
            start_utc=f"2026-09-{day:02d}T{hhmm(minutes)}:00+01:00",
            end_utc=f"2026-09-{day:02d}T{hhmm(minutes + dur)}:00+01:00",
            venue_name="The Pool", latitude=lat, longitude=lon, category="fitness")

    OPEN = 5 * 60 + 40                                  # 05:40, as the live pool does
    grid = [_slot(7, OPEN + i * 10) for i in range(11)]
    out, dropped, notes = OA.collapse_booking_grids(list(grid), OA.GRID_MIN_PER_DAY)
    checks.append((len(out) == 1 and dropped == 10,
                   "eleven ten-minute slots in one day collapse to ONE row"))
    checks.append((out[0].start_utc == grid[0].start_utc,
                   "the day's row opens when the FIRST slot opens"))
    checks.append((out[0].end_utc == max(g.end_utc for g in grid),
                   "and closes when the LAST one closes — not ten minutes later"))
    checks.append(("11 bookable slots" in (out[0].description or ""),
                   "the row says how many slots it stands for; nothing is dropped silently"))

    # Below the threshold is a real programme and must be left alone.
    few = [_slot(7, 9 * 60), _slot(7, 12 * 60), _slot(7, 18 * 60)]
    out2, dropped2, _ = OA.collapse_booking_grids(list(few), OA.GRID_MIN_PER_DAY)
    checks.append((len(out2) == 3 and dropped2 == 0,
                   "three sessions in a day are a schedule, not a grid"))

    # A grid running on two days is two rows: the collapse is per DAY.
    twodays = [_slot(7, OPEN + i * 10) for i in range(7)] + \
              [_slot(8, OPEN + i * 10) for i in range(7)]
    out3, _, _ = OA.collapse_booking_grids(list(twodays), OA.GRID_MIN_PER_DAY)
    checks.append((len(out3) == 2, "a grid on two days collapses to two rows, one each"))

    # Same title at a different pool is a different thing.
    twopools = [_slot(7, OPEN + i * 10) for i in range(7)] + \
               [_slot(7, OPEN + i * 10, lat=51.6, lon=-0.2) for i in range(7)]
    out4, _, _ = OA.collapse_booking_grids(list(twopools), OA.GRID_MIN_PER_DAY)
    checks.append((len(out4) == 2, "the same title at two venues stays two rows"))

    # IDENTITY IS THE DAY. Keyed on whichever slot happened to be first, a pool
    # opening ten minutes later tomorrow would write a second row and orphan
    # today's — which is the BikeReg collision running backwards.
    later = [_slot(7, OPEN + 10 + i * 10) for i in range(11)]
    out5, _, _ = OA.collapse_booking_grids(list(later), OA.GRID_MIN_PER_DAY)
    checks.append((out5[0].fingerprint == out[0].fingerprint
                   and out5[0].source_id == out[0].source_id,
                   "the day's identity does not move when the first slot does"))
    checks.append((out3[0].fingerprint != out3[1].fingerprint,
                   "but two days of the same grid get different identities"))

    # A publisher whose classes really do run often can say so.
    out6, dropped6, _ = OA.collapse_booking_grids(list(grid), 99)
    checks.append((len(out6) == 11 and dropped6 == 0,
                   "grid_min_per_day is an override, not a fixed rule"))

    # -------------------------------- 6. a transient 500 is not a dead feed
    #
    # A ScheduledSession carries neither title nor place — it names the series
    # it belongs to. So when the SessionSeries feed dies, the occurrences are
    # unreadable rather than absent. Live on Better (GLL): its series feed
    # returned HTTP 500 on page four, 1,500 series were read, and 63,141
    # occurrences were then dropped for naming a series nobody had. One retry
    # was worth all of them.
    import urllib.error

    calls = {"n": 0}

    def _flaky(url, timeout=30.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(url, 500, "Server Error", None, None)
        return {"items": [_item("a", soon)], "next": None}
    OA._get = _flaky
    data, why = OA.walk("u0", delay=0, sleep=lambda _: None)
    checks.append((len(data) == 1 and calls["n"] == 3,
                   "a 500 is retried, and the third attempt reads the page"))

    calls["n"] = 0

    def _gone(url, timeout=30.0):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
    OA._get = _gone
    data, why = OA.walk("u0", delay=0, sleep=lambda _: None)
    checks.append((calls["n"] == 1 and "404" in why,
                   "a 404 is the feed's answer, not a blip — asked once, reported"))

    calls["n"] = 0

    def _dead(url, timeout=30.0):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 503, "Unavailable", None, None)
    OA._get = _dead
    data, why = OA.walk("u0", delay=0, sleep=lambda _: None)
    checks.append((calls["n"] == 3 and "stopped after 0 page" in why,
                   "a feed that is really down is given up on, and SAID to be partial"))

    # ------------------------------------------- 7. the duplicate underneath
    #
    # 255 live rows across 217 distinct start times: every slot published twice.
    # Above the grid threshold the collapse absorbs that either way; below it
    # both would survive and the list would stutter.
    twice = [_slot(7, 9 * 60), _slot(7, 9 * 60), _slot(7, 12 * 60)]
    out7, folded, notes7 = OA.collapse_booking_grids(list(twice), OA.GRID_MIN_PER_DAY)
    checks.append((len(out7) == 2 and folded == 1,
                   "the same title at the same venue at the same INSTANT is one row"))
    checks.append((any("duplicate" in n for n in notes7),
                   "and the fold is reported, not silent"))
    near = [_slot(7, 9 * 60), _slot(7, 9 * 60 + 1)]
    out8, folded8, _ = OA.collapse_booking_grids(list(near), OA.GRID_MIN_PER_DAY)
    checks.append((len(out8) == 2 and folded8 == 0,
                   "a minute apart is two sessions — only an exact instant folds"))

    # ------------------------------------- 8. the weekly class is ONE row
    #
    # The booking grid was the visible tenth of this. Everyone Active is not a
    # grid — its per-day collapse found 714 rows in 94 groups — and it wrote
    # 98,871 upcoming sessions in one run, 88% of everything this adapter
    # produced. A real timetable, published one occurrence at a time, seventeen
    # weeks deep. That is what 0156 already models for a restaurant: one row
    # carrying a weekly pattern, rolled forward hourly, with a stable id.
    from datetime import timezone as _tz
    BST = _tz(timedelta(hours=1))
    BASE = datetime(2026, 9, 1, tzinfo=BST)          # a Tuesday

    def _occ(name, offset_days, hh, mm=0, lat=51.5, lon=-0.12, dur=60):
        st = BASE + timedelta(days=offset_days, hours=hh, minutes=mm)
        return NormalizedEvent(
            source="openactive:X", source_id=f"{name}{offset_days}{hh}{mm}", name=name,
            start_utc=st.isoformat(), end_utc=(st + timedelta(minutes=dur)).isoformat(),
            venue_name="Centre", latitude=lat, longitude=lon, category="fitness")

    wk = [_occ("Yoga", 7 * i, 19) for i in range(8)]
    out9, folded9, _ = OA.collapse_weekly_series(list(wk), OA.WEEKLY_MIN_REPEATS)
    checks.append((len(out9) == 1 and folded9 == 7,
                   "eight weekly occurrences become ONE standing row"))
    checks.append((out9[0].recurring_days == {"1": [["19:00", "20:00"]]},
                   "and the weekly pattern is Tuesday 19:00-20:00, in LOCAL time"))
    checks.append((out9[0].start_utc == wk[0].start_utc,
                   "the standing row starts at the SOONEST occurrence, not the last"))
    checks.append(("Runs weekly" in (out9[0].description or ""),
                   "the row says it is a weekly arrangement"))

    # A one-off at the same venue is an OCCASION and must survive as its own row.
    mixed = wk + [_occ("Yoga", 2, 11)]
    out10, _, _ = OA.collapse_weekly_series(list(mixed), OA.WEEKLY_MIN_REPEATS)
    checks.append((len(out10) == 2,
                   "a one-off beside the weekly class stays a dated row of its own"))
    checks.append((sum(1 for e in out10 if e.recurring_days) == 1,
                   "and only the standing row carries a pattern"))

    # Two weekly slots at one venue are ONE row with two days in the pattern.
    two = ([_occ("Swim", 7 * i, 7) for i in range(5)]
           + [_occ("Swim", 2 + 7 * i, 18) for i in range(5)])
    out11, _, _ = OA.collapse_weekly_series(list(two), OA.WEEKLY_MIN_REPEATS)
    checks.append((len(out11) == 1 and set(out11[0].recurring_days) == {"1", "3"},
                   "two weekly slots at one venue are one row, two days"))

    # TWO CONSECUTIVE WEEKS IS THE EVIDENCE A FORTNIGHT-DEEP PUBLISHER CAN GIVE.
    # Everyone Active holds exactly two occurrences for 38,562 of its 55,771
    # groups and three for none of them, so a threshold of three is inert there.
    pair = [_occ("Aqua", 0, 10), _occ("Aqua", 7, 10)]
    out12, folded12, _ = OA.collapse_weekly_series(list(pair), OA.WEEKLY_MIN_REPEATS)
    checks.append((len(out12) == 1 and folded12 == 1,
                   "two CONSECUTIVE weeks at the same hour is a weekly class"))

    # ...but the same two points three weeks apart are a course that ran twice,
    # and a standing row never dies on its own — the roller moves it forward for
    # ever and cleanup only deletes the past. So the gap has to be seven days.
    apart = [_occ("Workshop", 0, 10), _occ("Workshop", 21, 10)]
    out12b, folded12b, _ = OA.collapse_weekly_series(list(apart), OA.WEEKLY_MIN_REPEATS)
    checks.append((len(out12b) == 2 and folded12b == 0,
                   "same weekday three weeks apart is NOT a cadence — left as two occasions"))
    checks.append((len([e for e in out12b if e.recurring_days]) == 0,
                   "and neither gets a weekly pattern it would be rolled on for ever"))

    # THE SAME CLASS AT TWO VENUES IS TWO ARRANGEMENTS, and their ids must differ
    # or one venue's row would evict the other's on every run.
    twov = ([_occ("Gym", 7 * i, 9) for i in range(4)]
            + [_occ("Gym", 7 * i, 9, lat=51.9, lon=-0.4) for i in range(4)])
    out13, _, _ = OA.collapse_weekly_series(list(twov), OA.WEEKLY_MIN_REPEATS)
    checks.append((len(out13) == 2 and len({e.fingerprint for e in out13}) == 2,
                   "one class at two venues is two standing rows with two identities"))

    # Identity must not move week to week, or the roller has nothing to roll.
    later_weeks = [_occ("Yoga", 7 * i, 19) for i in range(1, 9)]
    out14, _, _ = OA.collapse_weekly_series(list(later_weeks), OA.WEEKLY_MIN_REPEATS)
    checks.append((out14[0].fingerprint == out9[0].fingerprint,
                   "next week's read produces the SAME id — a standing row is stable"))

    checks.append((OA.collapse_weekly_series(list(wk), 1)[1] == 0,
                   "weekly_min_repeats=1 disables the collapse for a publisher that needs it"))

    # ------------------------------- THE LICENCE LINE MUST SURVIVE THE SYNC
    #
    # All 127 OpenActive dataset pages are CC-BY 4.0 and the attribution is the
    # term the data is held on, not a footer — so it is the LAST line of every
    # description this adapter writes. mapsee_supabase_sync trims an over-long
    # description to DESCRIPTION_MAX from the END, and _text here allows a
    # 900-character body on its own, so anything near that overflowed 800 and
    # lost the licence silently, on a row that otherwise looked perfect. It cost
    # the retirement scripts too: all three identify their own rows BY that mark.
    import mapsee_supabase_sync as _S
    _long = {"name": "Aqua Aerobics",
             "description": "A friendly session for all abilities. " * 24,
             "startDate": "2026-09-07T19:40:00+01:00",
             "location": {"geo": {"latitude": 51.5, "longitude": -0.12},
                          "name": "Test Pool"}}
    _out = OA.to_event(_long, {"name": "Everyone Active", "slug": "ea"}, NOW, 120)
    _ev = _out[0] if isinstance(_out, tuple) else _out
    checks.append((len(_ev.description) > _S.DESCRIPTION_MAX,
                   "a long session description does overflow the sync's cap"))
    _stored = _S._cap_prose(_S._clean_text(_ev.description))
    checks.append((len(_stored) <= _S.DESCRIPTION_MAX,
                   "and the stored description still respects that cap"))
    checks.append(("via OpenActive" in _stored,
                   "but the CC-BY attribution survives the trim, every time"))

    _weekly = "\U0001F501 Runs weekly — 17 sessions.\n\n" + _ev.description
    _sw = _S._cap_prose(_S._clean_text(_weekly))
    checks.append(("via OpenActive" in _sw and "Runs weekly" in _sw,
                   "the collapse's own opener does not push the licence off the end"))


    checks.extend(fix_checks_2026_10_05())
    checks.extend(cancel_checks_2026_10_05())

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 2026-10-05: the UK OpenActive survey's review found four adapter bugs on live
# feeds (GoodGym: 17 of 38 standing rows an hour off, "Free to attend" cut from
# 271 of 508 rows by the sync's cap; Our Parks Live online classes; cancelled
# sessions). Their own clock: the cases straddle the 2026-10-25 change.
import mapsee_supabase_sync as S
NOW2 = datetime(2026, 10, 5, tzinfo=timezone.utc)
SRC2 = {"name": "Test Publisher", "category": "fitness", "country": "GB"}
GEO2 = {"name": "Church Hall", "geo": {"latitude": 51.51, "longitude": -0.3}}


def rec2(ident, start, end=None, name="Strength and Balance", **extra):
    r = {"@id": f"https://example.test/s/{ident}", "name": name, "startDate": start,
         "location": GEO2}
    if end:
        r["endDate"] = end
    r.update(extra)
    return r


def rows2(records):
    return [e for e in (OA.to_event(r, SRC2, NOW2, 120)[0] for r in records) if e]


def fix_checks_2026_10_05():
    """The survey review's adapter changes (London clock, eventStatus, online
    only, admission first), each failing 2-7 of these when reverted."""
    checks = []
    # ------------------------------------------------ 1. THE WALL CLOCK IS LONDON'S
    # GoodGym, SportSuite and Upshot send 'Z'. A Tuesday 10:00 London class is
    # 09:00Z until 2026-10-25 and 10:00Z after it; reading the clock off the Z
    # stamp gave TWO windows (18 of GoodGym's 38 standing rows on 2026-10-05).
    z = [rec2(f"z{i}", f"2026-10-{d:02d}T{h:02d}:00:00Z", f"2026-10-{d:02d}T{h+1:02d}:00:00Z")
         for i, (d, h) in enumerate([(6, 9), (13, 9), (20, 9), (27, 10)])]
    z += [rec2("z4", "2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z")]
    out, folded, _ = OA.collapse_weekly_series(rows2(z), 2)
    checks.append((len(out) == 1 and folded == 4,
                   "Z stamps across the 2026-10-25 change fold into ONE standing row"))
    checks.append((out[0].recurring_days == {"1": [["10:00", "11:00"]]},
                   "with ONE window, at the London clock (10:00-11:00), not 09:00 and 10:00"))
    off = [rec2(f"o{i}", s, e) for i, (s, e) in enumerate([
        ("2026-10-06T10:00:00+01:00", "2026-10-06T11:00:00+01:00"),
        ("2026-10-13T10:00:00+01:00", "2026-10-13T11:00:00+01:00"),
        ("2026-10-27T10:00:00+00:00", "2026-10-27T11:00:00+00:00")])]
    out2, _, _ = OA.collapse_weekly_series(rows2(off), 2)
    checks.append((out2[0].recurring_days == {"1": [["10:00", "11:00"]]},
                   "a feed that sends the London offset gives the same pattern (control)"))
    late_ev = OA.to_event(rec2("late", "2026-10-06T23:30:00Z"), SRC2, NOW2, 120)[0]
    checks.append((OA._grid_key(late_ev)[3] == "2026-10-07",
                   "a 23:30Z session in summer time is grouped on its LONDON day (00:30 the next)"))
    sat = [rec2(f"g{i}", f"2026-10-10T{8 + i // 6:02d}:{(i % 6) * 10:02d}:00Z",
               name="Swim For Fitness") for i in range(11)]
    grid, dropped, _ = OA.collapse_booking_grids(rows2(sat), OA.GRID_MIN_PER_DAY)
    checks.append((len(grid) == 1 and "09:00" in (grid[0].description or "")
                   and "08:00 to" not in (grid[0].description or ""),
                   "a Z booking grid states its opening time on the London clock (09:00, not 08:00)"))

    # ------------------------------------------------ 2. eventStatus
    for status, why in (("https://schema.org/EventCancelled", "cancelled"),
                        ("EventPostponed", "cancelled"),
                        ("https://schema.org/EventMovedOnline", "moved online")):
        got = OA.to_event(rec2("c", "2026-10-06T10:00:00+01:00", eventStatus=status), SRC2, NOW2, 120)
        checks.append((got[0] is None and got[1] == why,
                       f"eventStatus {status.rsplit('/', 1)[-1]} is refused as '{why}'"))
    kept = OA.to_event(rec2("k", "2026-10-06T10:00:00+01:00",
                           eventStatus="https://schema.org/EventScheduled"), SRC2, NOW2, 120)
    checks.append((kept[0] is not None, "EventScheduled is kept"))
    wk = [rec2("w1", "2026-10-06T10:00:00+01:00"),
          rec2("w2", "2026-10-13T10:00:00+01:00", eventStatus="https://schema.org/EventCancelled"),
          rec2("w3", "2026-10-20T10:00:00+01:00")]
    out3, folded3, _ = OA.collapse_weekly_series(rows2(wk), 2)
    checks.append((len(out3) == 2 and folded3 == 0 and not any(e.recurring_days for e in out3),
                   "a cancelled week is NOT weekly evidence: weeks 1 and 3 stay two dated rows"))

    # ------------------------------------------------ 2b. NOW2HERE TO TURN UP
    # 'Our Parks Live' is a Place with a London coordinate and Offline attendance
    # mode; only its words say online (60 future records, 2026-10-05).
    live = rec2("ol", "2026-10-06T07:30:00+01:00", name="RISE & SHINE LIVE YOGA",
               description="This live online session is only available through Our Parks Plus.",
               eventAttendanceMode="https://schema.org/OfflineEventAttendanceMode",
               offers=[{"price": 0, "priceCurrency": "GBP"}])
    live["location"] = dict(GEO2, name="Our Parks Live")
    got = OA.to_event(live, SRC2, NOW2, 120)
    checks.append((got[0] is None and got[1] == "online only",
                   "an 'online session' pinned to a Place marked Offline is refused as online only"))
    mode = OA.to_event(rec2("om", "2026-10-06T10:00:00+01:00",
                           eventAttendanceMode="https://schema.org/OnlineEventAttendanceMode"), SRC2, NOW2, 120)
    checks.append((mode[0] is None and mode[1] == "online only",
                   "OnlineEventAttendanceMode is refused as online only"))
    hybrid = OA.to_event(rec2("oh", "2026-10-06T10:00:00+01:00",
                             description="Join in person at the hall, or our live online session from home."),
                         SRC2, NOW2, 120)
    mixed_mode = OA.to_event(rec2("mm", "2026-10-06T10:00:00+01:00",
                                 eventAttendanceMode="https://schema.org/MixedEventAttendanceMode"), SRC2, NOW2, 120)
    checks.append((hybrid[0] is not None and mixed_mode[0] is not None,
                   "a session that is ALSO in person, in words or by Mixed mode, stays"))

    # ------------------------------------------------ 3. ADMISSION, FIRST, SHARED MODULE
    body = "A friendly falls-prevention class for all abilities. " * 20     # ~1,060 chars
    free = OA.to_event(rec2("f", "2026-10-06T10:00:00+01:00", description=body,
                           offers=[{"@type": "Offer", "price": 0, "priceCurrency": "GBP"}]),
                       SRC2, NOW2, 120)[0]
    stored = S._cap_prose(S._clean_text(free.description))
    checks.append(("Free to attend" in stored,
                   "a 1,000-character body still says 'Free to attend' after the sync's cap"))
    weekly = S._cap_prose(S._clean_text("\U0001F501 Runs weekly — 17 sessions.\n\n" + free.description))
    checks.append(("Free to attend" in weekly and "via OpenActive" in weekly,
                   "and so does the weekly opener's version, with the licence still on the end"))
    checks.append((free.source_details == {"free": True, "offer": {"price": "0", "currency": "GBP",
                                                                    "url": "https://example.test/s/f"}}
                   and free.admission_checked is True,
                   "the free row carries source_details for mapsee 0229, admission_checked"))
    fee = OA.to_event(rec2("p", "2026-10-06T10:00:00+01:00", description=body,
                          offers=[{"@type": "Offer", "price": 4.1, "priceCurrency": "GBP"}]),
                      SRC2, NOW2, 120)[0]
    fee_stored = S._cap_prose(S._clean_text(fee.description))
    checks.append((fee_stored.startswith("Some admission options are not free. Admission: GBP 4.1.")
                   and "Free to attend" not in fee_stored,
                   "a fee row leads with its price and the words 'not free', through the cap"))
    mixed = OA.to_event(rec2("m", "2026-10-06T10:00:00+01:00",
                            offers=[{"price": 0}, {"price": 5}]), SRC2, NOW2, 120)[0]
    checks.append(("Free to attend" not in mixed.description and (mixed.source_details or {}).get("free") is False,
                   "a free taster beside a paid block is not a free session"))
    member = OA.to_event(rec2("mb", "2026-10-06T10:00:00+01:00",
                             offers=[{"name": "Members", "price": 0, "priceCurrency": "GBP"}]),
                         SRC2, NOW2, 120)[0]
    checks.append(("Free to attend" not in member.description
                   and (member.source_details or {}).get("restricted") is True,
                   "a 0-price 'Members' offer is restricted, never 'Free to attend'"))
    none = OA.to_event(rec2("n", "2026-10-06T10:00:00+01:00"), SRC2, NOW2, 120)[0]
    checks.append((none.source_details is None and none.admission_checked is False
                   and "free" not in none.description.lower(),
                   "no offers: nothing claimed either way"))
    row = S.to_row(dict(free.as_record("2026-10-05T00:00:00+00:00")), "host")
    checks.append(((row.get("source_details") or {}).get("free") is True,
                   "to_row carries source_details through to the table row"))
    return checks


def cancel_checks_2026_10_05():
    """A session the publisher marks EventCancelled/EventPostponed is a
    TOMBSTONE on the row an earlier read stored for it - whichever fold that
    row was - and never on a row this read still writes live."""
    import contextlib
    import io
    import json as _json
    import os
    import tempfile
    from mapsee_ingest import EventStore
    checks = []
    CANCELLED = "https://schema.org/EventCancelled"

    def read(records):
        real = OA.walk
        OA.walk = lambda url, *a, **k: ({r["@id"]: r for r in records}, "end of feed (1 page(s))")
        try:
            return OA.read_source(dict(SRC2, feeds=[{"kind": "Event", "url": "https://example.test/feed"}]),
                                  120, 5, 0, now=NOW2)
        finally:
            OA.walk = real

    def tue(i, ident, h=10, **extra):
        day = 6 + 7 * i                       # London leaves summer time on 10-25
        off = "+01:00" if day < 25 else "+00:00"
        return rec2(ident, f"2026-10-{day:02d}T{h:02d}:00:00{off}", f"2026-10-{day:02d}T{h:02d}:45:00{off}", **extra)

    # 1. A dated session: the tombstone IS the stored row's key.
    one = [rec2("d1", "2026-10-09T18:00:00+01:00", name="Walking Netball")]
    live, _ = read(one)
    gone, rep = read([dict(one[0], eventStatus=CANCELLED)])
    tomb = (rep.get("cancel") or [None])[0]
    checks.append((not gone and tomb is not None and tomb.fingerprint == live[0].fingerprint
                   and (tomb.source, tomb.source_id) == (live[0].source, live[0].source_id),
                   "a cancelled dated session: one tombstone, with the live row's fingerprint, source and id"))
    with tempfile.TemporaryDirectory() as d:
        yday, today_st = EventStore(os.path.join(d, "a.json")), EventStore(os.path.join(d, "b.json"))
        yday.upsert(live[0])
        res = today_st.cancel(tomb, "eventStatus cancelled or postponed") if tomb else None
        checks.append((res == "cancelled" and list(today_st.tombstones) == list(yday.records),
                       "store.cancel's key == store.upsert's key"))
    _ev, why = OA.to_event(dict(one[0], eventStatus="EventPostponed"), SRC2, NOW2, 120)
    checks.append((why == "cancelled", "to_event still refuses it (its contract is unchanged)"))

    # 2. ONE WEEK OFF A CLASS THAT STILL FOLDS: the standing row stays, says so.
    weeks = [tue(i, f"w{i}") for i in range(4)]
    all_live, _ = read(weeks)
    w_fp = next(e.fingerprint for e in all_live if e.recurring_days)
    out, rep = read([dict(r, eventStatus=CANCELLED) if r["@id"].endswith("w1") else r for r in weeks])
    stand = [e for e in out if e.recurring_days]
    tfps = {e.fingerprint for e in rep["cancel"]}
    checks.append((len(stand) == 1 and stand[0].fingerprint == w_fp and w_fp not in tfps,
                   "one cancelled week of four: the weekly row is still written live and is NOT a tombstone"))
    checks.append(((stand[0].description if stand else "").startswith(
        "\u26a0\ufe0f Not on Tue 13 Oct: cancelled by the publisher."),
        "...and it names the week that is off"))

    # 3. Two weeks listed, one off: nothing live folds any more.
    two = [tue(0, "a0"), tue(1, "a1")]
    w2 = next(e.fingerprint for e in read(two)[0] if e.recurring_days)
    out, rep = read([two[0], dict(two[1], eventStatus=CANCELLED)])
    checks.append((len(out) == 1 and not out[0].recurring_days and w2 in {e.fingerprint for e in rep["cancel"]},
                   "two weeks listed and one off: the live week is a dated row, the standing row a tombstone "
                   "(a live write lifts it when the arrangement is listed again)"))
    out, rep = read([dict(r, eventStatus=CANCELLED) for r in weeks])
    checks.append((not out and w_fp in {e.fingerprint for e in rep["cancel"]},
                   "every listed week called off: the standing row is a tombstone"))

    # 4. A grid day: the day row is what was stored.
    slots = [rec2(f"g{i}", f"2026-10-10T{9 + i:02d}:00:00+01:00", name="Swim For Fitness") for i in range(7)]
    g_fp = read(slots)[0][0].fingerprint
    out, rep = read([dict(r, eventStatus=CANCELLED) for r in slots])
    checks.append((not out and g_fp in {e.fingerprint for e in rep["cancel"]},
                   "every slot of a grid day called off: the day row is a tombstone"))
    out, rep = read([dict(r, eventStatus=CANCELLED) if r["@id"].endswith("g0") else r for r in slots])
    checks.append((len(out) == 1 and out[0].fingerprint == g_fp and g_fp not in {e.fingerprint for e in rep["cancel"]},
                   "one slot of seven called off: the day row is still live and never a tombstone"))
    live_fps = {e.fingerprint for e in out}
    checks.append((not (live_fps & {e.fingerprint for e in rep["cancel"]}),
                   "no tombstone ever names a row this read writes live"))

    # 5. The CLI: tombstones in the store, counted, and NEVER a complete read.
    real, real_read = OA.walk, OA.read_source
    OA.walk = lambda url, *a, **k: ({r["@id"]: r for r in [one[0], dict(tue(0, "x"), eventStatus=CANCELLED)]},
                                    "end of feed (1 page(s))")
    # The CLI reads the wall clock; its fixtures are dated, so pin it to NOW2 like
    # every other case here (it went red on 2026-10-07, the day after tue(0)).
    OA.read_source = lambda *a, **k: real_read(*a, **dict(k, now=NOW2))
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "c.json")
            _json.dump({"sources": [dict(SRC2, feeds=[{"kind": "Event", "url": "https://example.test/f"}])]},
                       open(cfg, "w", encoding="utf-8"))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                OA.main(["--config", cfg, "--store", os.path.join(d, "s.json"), "--horizon-days", "400"])
            whole = _json.load(open(os.path.join(d, "s.json"), encoding="utf-8"))
    finally:
        OA.walk, OA.read_source = real, real_read
    checks.append((len(whole.get("tombstones") or []) == 1 and len(whole["events"]) == 1
                   and "cancelled 1" in buf.getvalue(),
                   "the CLI writes the tombstone beside the live row, and its log line counts it"))
    checks.append((not whole.get("complete_reads"),
                   "OpenActive is never a complete read: its folds move as sessions pass, so absence would guess"))
    return checks


if __name__ == "__main__":
    sys.exit(main())
