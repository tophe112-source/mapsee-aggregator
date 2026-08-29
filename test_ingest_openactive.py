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

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
