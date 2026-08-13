#!/usr/bin/env python3
"""
test_osm_food.py — opening hours we may act on, and the ones we must refuse.

This parser decides when a takeaway pin is offered to somebody hungry. Getting
it wrong does not produce a wrong category or a missing row; it sends a person
to a locked door. So the bar is not "usually right" — it is that anything not
confidently readable returns None and the place stays off the map entirely.

OSM's opening_hours grammar is large. This supports the common shapes and
REFUSES the rest, and the refusals are the important half of these cases.

Run: python test_osm_food.py
"""
import sys

from mapsee_ingest_osm_food import parse_opening_hours as P

MO, TU, WE, TH, FR, SA, SU = range(7)

CASES = [
    # --- readable, and what they must mean
    ("Mo-Fr 11:00-22:00", {MO: ("11:00", "22:00"), TU: ("11:00", "22:00"), WE: ("11:00", "22:00"),
                           TH: ("11:00", "22:00"), FR: ("11:00", "22:00")}, "weekdays"),
    ("Mo-Su 10:00-20:00", {d: ("10:00", "20:00") for d in range(7)}, "every day"),
    ("24/7", {d: ("00:00", "23:59") for d in range(7)}, "always open"),
    ("Mo,We,Fr 09:00-17:00", {MO: ("09:00", "17:00"), WE: ("09:00", "17:00"), FR: ("09:00", "17:00")},
     "a day list, not a range"),
    ("Mo-Su 10:00-20:00; Su off", {d: ("10:00", "20:00") for d in range(6)},
     "a later rule can close a day the earlier one opened"),
    ("Tu-Sa 17:00-23:00", {TU: ("17:00", "23:00"), WE: ("17:00", "23:00"), TH: ("17:00", "23:00"),
                           FR: ("17:00", "23:00"), SA: ("17:00", "23:00")}, "dinner only"),
    ("Sa-Su 09:00-14:00", {SA: ("09:00", "14:00"), SU: ("09:00", "14:00")},
     "a range that wraps the end of the week"),
    ("Mo-Fr 7:00-15:00", {d: ("07:00", "15:00") for d in range(5)}, "single-digit hour normalises"),

    # --- MUST refuse. Each of these is a way to be confidently wrong.
    ("Mo-Fr 11:00-14:00,17:00-22:00", None, "split service: one row cannot say 'shut 14:00-17:00'"),
    ("Mo-Su 11:00+", None, "open-ended: no closing time to honour"),
    ("Mo-Fr 22:00-02:00", None, "crosses midnight: one row cannot express it"),
    ("Mo-Fr 09:00-17:00; PH off", None, "public holidays: we do not know the calendar"),
    ("Apr-Sep Mo-Su 10:00-20:00", None, "seasonal"),
    ("Mo-Fr sunrise-sunset", None, "astronomical"),
    ("week 1-20 Mo-Fr 10:00-18:00", None, "week numbers"),
    ("Mo-Fr", None, "days with no hours at all"),
    ("Mo-Su 10:00-20:00; Dez 24 off", None,
     "an unreadable CLOSURE rule — the crash that killed the first live run, and the "
     "one rule that must never be shrugged off, because ignoring 'shut' advertises open"),
    ("Mo-Su 10:00-20:00; xyz off", None, "same, unreadable day spec on the closure"),
    ("nonsense", None, "unparseable"),
    ("", None, "empty"),
    (None, None, "missing tag"),
]


def main():
    failed = 0
    for spec, want, why in CASES:
        got = P(spec)
        ok = got == want
        if not ok:
            failed += 1
        label = "refuse" if want is None else "read  "
        print(f"{'ok  ' if ok else 'FAIL'}  {label}  {str(spec)[:34]:<36} {why}")
        if not ok:
            print(f"        wanted {want}")
            print(f"        got    {got}")
    # ---- tiling, and never keeping a partial answer ----------------------
    # A 50-mile radius is a 1.45° box and Overpass answers 504 to that, which is
    # how Seattle and later New York dropped out of whole runs. Tiling fixed the
    # coverage; the first live 50-mile pull then showed the second half of the
    # problem: 2 of Seattle's 35 tiles timed out, the run correctly SAID so, and
    # cached the partial list for thirty days. Every run after that would have
    # looked healthy while two squares of the metro quietly went missing.
    #
    # A partial answer is fine to USE and never fine to KEEP.
    print("\n-- tiles, and what may be cached --")
    import mapsee_ingest_osm_food as m

    box = m.area_bbox({"center": [47.6062, -122.3321], "radius_miles": 50})
    cells = m.tiles(box)
    checks = [
        (len(cells) > 1, f"a 50-mile area is split into tiles ({len(cells)})"),
        (all(c[2] - c[0] <= 0.36 and c[3] - c[1] <= 0.36 for c in cells),
         "no tile is wider than the cell limit"),
        (abs(min(c[0] for c in cells) - box[0]) < 1e-9
         and abs(max(c[2] for c in cells) - box[2]) < 1e-9,
         "the tiles cover the whole box, edge to edge"),
    ]

    real_one, real_sleep = m._overpass_one, m.time.sleep
    try:
        m.time.sleep = lambda *_: None
        n = {"i": 0}

        def flaky(bbox, delay=2.0, tries=4):
            n["i"] += 1
            if n["i"] == 2:
                raise RuntimeError("504")
            return [{"type": "node", "id": n["i"], "tags": {}}]
        m._overpass_one = flaky
        els, complete = m.overpass({"name": "T", "center": [47.6, -122.3], "radius_miles": 50}, delay=0)
        checks.append((complete is False, "one dead tile marks the area INCOMPLETE"))
        checks.append((len(els) == len(cells) - 1, "the surviving tiles are still returned and used"))

        m._overpass_one = lambda bbox, delay=2.0, tries=4: [{"type": "node", "id": 7, "tags": {}}]
        els2, complete2 = m.overpass({"name": "T", "center": [47.6, -122.3], "radius_miles": 50}, delay=0)
        checks.append((complete2 is True, "all tiles answering marks it complete"))
        checks.append((len(els2) == 1, "the same place seen in several tiles is deduped"))
    finally:
        m._overpass_one, m.time.sleep = real_one, real_sleep

    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")

    print(f"\n{len(CASES) + len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
