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
from datetime import date

from mapsee_ingest_osm_food import parse_opening_hours as P

MO, TU, WE, TH, FR, SA, SU = range(7)

CASES = [
    # --- readable, and what they must mean. A day is a LIST of windows (0188).
    ("Mo-Fr 11:00-22:00", {d: [("11:00", "22:00")] for d in range(5)}, "weekdays"),
    ("Mo-Su 10:00-20:00", {d: [("10:00", "20:00")] for d in range(7)}, "every day"),
    ("24/7", {d: [("00:00", "23:59")] for d in range(7)}, "always open"),
    ("Mo,We,Fr 09:00-17:00", {MO: [("09:00", "17:00")], WE: [("09:00", "17:00")],
                              FR: [("09:00", "17:00")]}, "a day list, not a range"),
    ("Mo-Su 10:00-20:00; Su off", {d: [("10:00", "20:00")] for d in range(6)},
     "a later rule can close a day the earlier one opened"),
    ("Tu-Sa 17:00-23:00", {d: [("17:00", "23:00")] for d in (TU, WE, TH, FR, SA)}, "dinner only"),
    ("Sa-Su 09:00-14:00", {SA: [("09:00", "14:00")], SU: [("09:00", "14:00")]},
     "a range that wraps the end of the week"),
    ("Mo-Fr 7:00-15:00", {d: [("07:00", "15:00")] for d in range(5)}, "single-digit hour normalises"),

    # --- SPLIT SERVICE. Refused until 0188 made a day a list of windows.
    # Measured 2026-08-20, that refusal cost Madrid 117 of 126 shops.
    ("Mo-Fr 11:00-14:00,17:00-22:00",
     {d: [("11:00", "14:00"), ("17:00", "22:00")] for d in range(5)},
     "lunch service and dinner service, with the kitchen shut between"),
    ("Mo-Sa 10:00-14:00,17:00-20:00",
     {d: [("10:00", "14:00"), ("17:00", "20:00")] for d in range(6)},
     "the siesta — the shape that was costing us Spain"),
    ("Mo-Su 09:00-12:00,14:00-17:00,19:00-22:00",
     {d: [("09:00", "12:00"), ("14:00", "17:00"), ("19:00", "22:00")] for d in range(7)},
     "three windows in a day"),
    ("Mo,We 10:00-12:00,14:00-16:00",
     {MO: [("10:00", "12:00"), ("14:00", "16:00")], WE: [("10:00", "12:00"), ("14:00", "16:00")]},
     "a comma in the DAYS and a comma in the TIMES, in one rule"),
    ("Mo-Fr 17:00-22:00,11:00-14:00",
     {d: [("11:00", "14:00"), ("17:00", "22:00")] for d in range(5)},
     "written out of order — sorted, because the roller takes the FIRST window"),
    ("Mo-Fr 10:00-14:00,14:00-20:00", {d: [("10:00", "20:00")] for d in range(5)},
     "windows that merely touch are one window written as two, and merge"),

    # --- PAST MIDNIGHT, which used to be refused for the same reason split
    # service was: one window could not say "two days". It can now — the second
    # half is written onto the day it lands on.
    #
    # This is not hypothetical tidying. 251 rows in ../mapsee still hold
    # "24:00".."27:00" in recurring_hours, written before the refusal existed
    # and never overwritten since, because the parser declines those venues.
    # Postgres rejects '2026-08-21 27:00'::timestamp, so under 0156's unguarded
    # roller ONE of those rows aborted the entire hourly roll for every venue.
    ("Mo-Su 11:00-27:00",
     {d: [("00:00", "03:00"), ("11:00", "23:59")] for d in range(7)},
     "11am to 3am, every day — so every day also carries YESTERDAY's tail"),
    ("Mo-Fr 22:00-02:00",
     {MO: [("22:00", "23:59")],
      **{d: [("00:00", "02:00"), ("22:00", "23:59")] for d in (TU, WE, TH, FR)},
      SA: [("00:00", "02:00")]},
     "the other way OSM writes it. Monday has no tail (Sunday is shut) and "
     "Saturday is ONLY a tail"),
    ("Sa 18:00-26:00; Su off",
     {SA: [("18:00", "23:59")], SU: [("00:00", "02:00")]},
     "Sunday is closed AND has a window — no service starts, the Saturday "
     "night session finishes. That is what the sign on the door says"),
    ("Mo-Fr 11:00-14:00,17:00-26:00",
     {MO: [("11:00", "14:00"), ("17:00", "23:59")],
      **{d: [("00:00", "02:00"), ("11:00", "14:00"), ("17:00", "23:59")]
         for d in (TU, WE, TH, FR)},
      SA: [("00:00", "02:00")]},
     "a siesta AND a late finish in one rule"),
    ("Mo-Su 11:00-24:00", {d: [("11:00", "23:59")] for d in range(7)},
     "24:00 is midnight exactly — the end of today, and NOT a zero-length "
     "window on tomorrow"),
    ("Mo-Su 22:00-00:00", {d: [("22:00", "23:59")] for d in range(7)},
     "same, written as 00:00"),
    ("Mo-Su 10:00-24:45", {d: [("00:00", "00:45"), ("10:00", "23:59")] for d in range(7)},
     "a quarter to one in the morning"),

    # --- MUST refuse. Each of these is a way to be confidently wrong.
    ("Mo-Fr 10:00-14:00,12:00-18:00", None,
     "windows that genuinely OVERLAP: a malformed rule, and which one the mapper "
     "meant is exactly the guess this parser does not make"),
    ("Mo-Su 25:00-27:00", None, "an OPENING past midnight is meaningless"),
    ("Mo-Su 11:00-49:00", None, "a close more than a full day out"),
    ("Mo 11:00-11:00", None, "a zero-length window"),
    ("Mo-Su 11:00+", None, "open-ended: no closing time to honour"),
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

        # ONE DEAD TILE USED TO MEAN INCOMPLETE. It no longer does, because a
        # tile that lost is asked once more after the rest of the area — so the
        # tile here has to be dead for good, keyed on its box rather than on a
        # call count. A count-based failure would now recover on the retry,
        # which is exactly the behaviour that made this assertion stale.
        dead = cells[1]

        def flaky(bbox, delay=2.0, tries=4):
            n["i"] += 1
            if tuple(bbox) == tuple(dead):
                raise RuntimeError("504")
            return [{"type": "node", "id": n["i"], "tags": {}}]
        m._overpass_one = flaky
        els, complete = m.overpass({"name": "T", "center": [47.6062, -122.3321], "radius_miles": 50}, delay=0)
        checks.append((complete is False, "a tile dead on BOTH passes marks the area INCOMPLETE"))
        checks.append((len(els) == len(cells) - 1, "the surviving tiles are still returned and used"))

        # And the case that changed: dead once, alive on the second pass.
        seen_once = {"hit": False}

        def recovers(bbox, delay=2.0, tries=4):
            if tuple(bbox) == tuple(dead) and not seen_once["hit"]:
                seen_once["hit"] = True
                raise RuntimeError("504")
            return [{"type": "node", "id": str(bbox), "tags": {}}]
        m._overpass_one = recovers
        els3, complete3 = m.overpass({"name": "T", "center": [47.6062, -122.3321], "radius_miles": 50}, delay=0)
        checks.append((complete3 is True,
                       "a tile that answers on the retry makes the area COMPLETE — so it caches"))
        checks.append((len(els3) == len(cells),
                       "and every tile's elements are present, including the one that lost"))

        m._overpass_one = lambda bbox, delay=2.0, tries=4: [{"type": "node", "id": 7, "tags": {}}]
        els2, complete2 = m.overpass({"name": "T", "center": [47.6062, -122.3321], "radius_miles": 50}, delay=0)
        checks.append((complete2 is True, "all tiles answering marks it complete"))
        checks.append((len(els2) == 1, "the same place seen in several tiles is deduped"))
    finally:
        m._overpass_one, m.time.sleep = real_one, real_sleep

    # ---- one row per venue, not one per open day -------------------------
    # A business is open every Tuesday; it is not holding 52 Tuesday events.
    # Measured across Seattle, Chicago, Portland and London: 1,547 rows for 254
    # places, 6.1x. The row now carries the weekly pattern and ../mapsee 0156's
    # roller moves its window forward.
    print("\n-- one row per venue --")
    EL = {"type": "node", "id": 123456, "lat": 47.6, "lon": -122.3,
          "tags": {"name": "Joe's", "amenity": "restaurant"}}
    AREA = {"name": "Seattle", "city": "Seattle", "region": "WA", "country": "US"}
    daily = m.parse_opening_hours("Mo-Su 11:00-22:00")
    rows = m.to_events(EL, AREA, "https://order.toasttab.com/online/joes", daily, 7)
    sat = m.to_events(EL, AREA, "u", m.parse_opening_hours("Sa 09:00-14:00"), 7)
    twin = m.to_events({**EL, "id": 999999}, AREA, "u", daily, 7)
    rowchecks = [
        (len(rows) == 1, "open seven days a week is ONE row, not seven"),
        (bool(rows and rows[0].recurring_days and len(rows[0].recurring_days) == 7),
         "the weekly pattern rides along on that row"),
        # Assert the WEEKDAY, not "later than the seven-day place".
        #
        # That comparison was a proxy for "not today", and it is false one day
        # in seven: on a Saturday the Mo-Su venue and the Sa-only venue both
        # resolve to today, and `>` fails on equal dates. It went red every
        # Saturday and green again on Sunday, which reads as a flaky test rather
        # than a dated one. The property actually under test is that a single
        # open day resolves to the next occurrence OF THAT DAY — true whenever
        # it is run, including on a Saturday, which is the interesting case.
        (len(sat) == 1
         and date.fromisoformat(sat[0].start_local[:10]).weekday() == SA
         and sat[0].start_local[:10] >= rows[0].start_local[:10],
         "a Saturday-only place still resolves, to its next Saturday"),
        (bool(rows and twin and rows[0].fingerprint != twin[0].fingerprint),
         "two venues with the SAME NAME get different rows"),
        # The fingerprint must not contain the date, or a re-run adds a row
        # instead of updating one — the whole point.
        (m.to_events(EL, AREA, "u", daily, 7)[0].fingerprint == rows[0].fingerprint,
         "the fingerprint is stable across runs (re-run updates, never adds)"),
        (bool(rows and rows[0].recurring_days.get("0") == [["11:00", "22:00"]]),
         "0=Monday, and a day is a LIST of windows even when there is one (0188)"),
    ]
    # A SPLIT-SERVICE ROW, end to end. This is the shape the old parser refused
    # outright, so nothing downstream had ever seen it: the row must carry BOTH
    # windows for the roller to choose between, and its own starts_at/ends_at
    # must be the FIRST of them — a row that opened at the dinner sitting would
    # advertise a restaurant as shut through lunch.
    split = m.parse_opening_hours("Mo-Su 11:00-14:00,17:00-22:00")
    srows = m.to_events(EL, AREA, "u", split, 7)
    rec = srows[0].recurring_days if srows else {}
    checks.extend([
        (len(srows) == 1, "a split-service place is ONE row, not one per sitting"),
        (rec.get("0") == [["11:00", "14:00"], ["17:00", "22:00"]],
         "both windows reach recurring_days, in order"),
        (bool(srows) and srows[0].start_local.endswith("T11:00:00")
         and srows[0].end_local.endswith("T14:00:00"),
         "and the row opens on the FIRST window, not the last"),
    ])
    detail_tags = {
        "phone": "+1 206 555 0123", "cuisine": "ethiopian;coffee_shop",
        "wheelchair": "limited", "takeaway": "yes", "delivery": "yes",
        "outdoor_seating": "yes", "internet_access": "wlan",
        "diet:vegetarian": "yes", "diet:vegan": "only",
    }
    details = m.business_detail_lines(detail_tags, "(206) 000-0000")
    jsonld_phone = m.phone_from_official_html(
        '<script type="application/ld+json">'
        '{"@type":"Restaurant","telephone":"(206) 555-0199"}</script>')
    tel_phone = m.phone_from_official_html('<a href="tel:%2B12065550188">Call</a>')
    enriched = m.to_events(
        {**EL, "tags": {**EL["tags"], **detail_tags}}, AREA, "u", daily, 7,
        website_phone="(206) 000-0000")[0]
    rowchecks.extend([
        (details[0] == "☎ Phone: +1 206 555 0123",
         "OSM phone beats a website fallback and remains human-readable"),
        (any("Cuisine: ethiopian · coffee shop" in x for x in details),
         "cuisine values become readable business details"),
        (any("Limited wheelchair access" in x for x in details),
         "wheelchair access is explicit, including limited access"),
        (any("Takeaway" in x and "Delivery" in x and "Wi-Fi" in x for x in details),
         "positive customer services share one compact marker"),
        (jsonld_phone == "(206) 555-0199", "official JSON-LD telephone is read"),
        (tel_phone == "+12065550188", "official tel links are the bounded fallback"),
        ("Phone: +1 206 555 0123" in enriched.description
         and "Public business details from OpenStreetMap" in enriched.description,
         "enriched markers and attribution ride on the stable venue row"),
    ])
    checks.extend(rowchecks)

    # ---- the hub's name is not the venue's town ---------------------------
    # Reported live 2026-08-17: Sisters Restaurant, 2804 Grand Avenue, EVERETT,
    # was on the map as a "restaurant in Seattle", pinned twenty-seven miles
    # south of itself. The Seattle hub is a 50-mile radius and covers Everett,
    # Renton, Bellevue and Kent, and the row took its town from the HUB.
    #
    # The structured `city` field was fixed in "A restaurant in Renton is not in
    # Seattle" — and the DESCRIPTION was still built from area["name"], so the
    # prose went on naming the wrong town three hours after the field stopped.
    # Both are pinned here, because fixing one and not the other is precisely
    # what happened. The node id and coordinates below are the real ones.
    import hashlib as _h
    HUB = {"name": "Seattle", "city": "Seattle", "region": "WA", "country": "US"}
    everett = {"type": "node", "id": 13145544801, "lat": 47.9803582, "lon": -122.2130915,
               "tags": {"name": "Sisters Restaurant", "amenity": "restaurant",
                        "addr:housenumber": "2804", "addr:street": "Grand Avenue",
                        "opening_hours": "Mo-Su 07:00-20:00"}}
    hrs = m.parse_opening_hours("Mo-Su 07:00-20:00")
    ev = (m.to_events(everett, HUB, "https://order.toasttab.com/online/x", hrs, 7) or [None])[0]
    with_city = {**everett, "tags": {**everett["tags"], "addr:city": "Everett"}}
    ev2 = (m.to_events(with_city, HUB, "https://order.toasttab.com/online/x", hrs, 7) or [None])[0]
    checks.extend([
        (ev is not None, "the Everett restaurant is still ingested"),
        (ev is not None and ev.city is None,
         "no addr:city means NO city, never the hub's"),
        (ev is not None and "in Seattle" not in (ev.description or ""),
         "and the DESCRIPTION does not claim the hub's town either"),
        (ev is not None and abs(ev.latitude - 47.9803582) < 1e-6,
         "the pin is OSM's surveyed point, in Everett"),
        (ev is not None and ev.coords_exact is True,
         "coords_exact keeps the sync's Census pass off that point"),
        (ev is not None and ev.fingerprint == _h.sha1(b"osm-food|node/13145544801").hexdigest(),
         "identity is the OSM ref, so a re-ingest UPDATES rather than duplicating"),
        (ev2 is not None and ev2.city == "Everett", "OSM's own addr:city is used when present"),
        (ev2 is not None and "in Everett" in (ev2.description or ""),
         "and then the description names the real town"),
    ])

    # THE ROTATION WINDOW. --max-places walks a cursor through the candidates,
    # and the wrap is the part that goes wrong: slicing then topping up from the
    # front examines everything TWICE whenever there are fewer candidates than
    # the cap. EventStore dedupes on fingerprint, so the duplicates collapse and
    # the only symptom is a summary that contradicts its own detail — which is
    # why this ran unnoticed until osm-secondhand, whose cap is 400, reported
    # 104 rows over 52 Edinburgh shops on its first live run.
    pool = list(range(52))
    checks.extend([
        (len(m.window_at(pool, 0, 60)) == 52,
         "fewer candidates than the cap: the window is the POOL, not the cap"),
        (len(set(map(id, m.window_at(pool, 0, 60)))) == 52,
         "and every one of them appears exactly once"),
        (m.window_at(pool, 0, 20) == list(range(20)), "a full window starts at the cursor"),
        (m.window_at(pool, 45, 10) == [45, 46, 47, 48, 49, 50, 51, 0, 1, 2],
         "a window that runs off the end wraps to the front"),
        (len(set(m.window_at(pool, 45, 52))) == 52,
         "a full-length wrapped window still visits each candidate once"),
        (m.window_at([], 0, 60) == [], "an area with no candidates yields no window"),
        (m.window_at(pool, 99, 3) == [47, 48, 49],
         "a cursor past the end is taken modulo the pool, not clamped"),
        # The cursor must advance by what was EXAMINED. Asking for 60 of 52 and
        # advancing by 60 left it at 8, so the next run re-walked the first eight
        # it had just finished rather than starting cleanly again.
        ((0 + len(m.window_at(pool, 0, 60))) % 52 == 0,
         "after a short area the cursor lands back at 0, not at cap-minus-pool"),
    ])

    # ---- the tile sweep, and the second pass ----------------------------
    # Two production reparses on 2026-08-20 lost tiles on most hubs — seven of
    # thirty-two second-hand, six of eight food, New York alone losing NINE of
    # thirty. _overpass_one already retries a tile four times over ~26 seconds,
    # which is right for a rate-limit and useless for a busy service. The second
    # pass runs after the rest of the area, minutes later.
    #
    # None of this was testable while the fetch was inline in overpass(); it is
    # now a function taking a fetcher and a sleep, so failure is scriptable.
    calls = []

    def flaky(cell):
        """Tile 'b' fails the first time it is asked and works the second."""
        calls.append(cell)
        if cell == "b" and calls.count("b") == 1:
            raise RuntimeError("504")
        return [{"type": "node", "id": f"{cell}1"}, {"type": "node", "id": f"{cell}2"}]

    els, complete = m.sweep_tiles(["a", "b", "c"], flaky, "[t]", sleep=lambda _: None)
    checks.extend([
        (complete is True, "a tile that answers on the SECOND pass makes the area complete"),
        (len(els) == 6, "and its elements are in the result (3 tiles x 2)"),
        (calls.count("b") == 2 and calls.count("a") == 1,
         "only the tile that LOST is re-asked, not the whole area"),
    ])

    def always(cell):
        raise RuntimeError("504")

    els2, complete2 = m.sweep_tiles(["a"], always, "[t]", sleep=lambda _: None)
    checks.extend([
        (complete2 is False, "a tile that loses twice leaves the area INCOMPLETE"),
        (els2 == [], "and yields nothing rather than a partial pretending to be whole"),
    ])

    def dupes(cell):
        return [{"type": "node", "id": 1}, {"type": "node", "id": 2}]

    els3, _ = m.sweep_tiles(["a", "b"], dupes, "[t]", sleep=lambda _: None)
    checks.append((len(els3) == 2,
                   "overlapping tiles still de-duplicate on (type, id)"))

    slept = []
    m.sweep_tiles(["a"], lambda c: [], "[t]", sleep=lambda s: slept.append(s))
    checks.append((slept == [], "no failures means no waiting"))

    # ------------------------------------------------------------------
    # THE WALL-CLOCK BUDGET, AND IT IS TESTED THROUGH main() ON PURPOSE.
    #
    # On 2026-08-25 the Paris job ran 07:33 -> 13:03 and GitHub's
    # timeout-minutes CANCELLED it, which skips every step after — so the sync,
    # the cursor slice and the hand-up were all skipped, the whole sweep was
    # discarded, and the cursor never moved. Next week it would start in the
    # same place and do it again. Seven other metros finished in 1-3 hours, so
    # the cost is not predictable from the candidate count.
    #
    # `--max-minutes` ends the sweep ourselves with time left to save it, and
    # the load-bearing half is that the cursor then advances by what was
    # ACTUALLY examined rather than by the window's length — otherwise a run
    # that stopped early would march past venues nobody looked at.
    #
    # This runs the REAL main() against a stubbed Overpass because that is where
    # the change lives, and because the amenities adapter shipped two production
    # failures this month that no unit case could reach: both were in main(),
    # and nothing ran main().
    import json as _json, os as _os, shutil as _shutil, tempfile as _tmp
    _real_load, _real_links = m.load_places, m.links_for
    _real_save, _real_loadcur, _real_time = m.save_cursor, m.load_cursor, m.time.time
    _tags = {"amenity": "fast_food", "name": "X", "opening_hours": "Mo-Su 10:00-20:00",
             "website": "https://example.org", "addr:city": "Paris"}
    _els = [{"type": "node", "id": i, "lat": 48.85, "lon": 2.35, "tags": dict(_tags, name=f"X{i}")}
            for i in range(20)]
    _tmpdir = _tmp.mkdtemp()
    try:
        # links_for is the expensive call this budget exists to bound, so it is
        # also the honest counter for "how many did we actually examine" — far
        # steadier than a faked clock, which has to guess how many other things
        # read time.time() between two candidates.
        # ...and it doubles as the CLOCK. Advancing time only here — once per
        # candidate actually looked at — makes "stops after N" exact, where a
        # free-running clock has to guess how many other things read time.time()
        # between two candidates (an earlier draft of this case guessed wrong).
        _looked, _now = [], [1000.0]
        m.load_places = lambda *a, **k: (_els, True)
        m.time.time = lambda: _now[0]
        def _links(tags, *a, **k):
            _looked.append(tags.get("name"))
            _now[0] += 1.0
            return ("https://order.example/order", None, None, None)
        m.links_for = _links
        # BOTH halves, and patching m.CURSOR_PATH is NOT one of them: load_cursor
        # takes `path=CURSOR_PATH` as a DEFAULT ARGUMENT, bound once at def time,
        # so reassigning the module global does nothing and the run reads the
        # repo's real osm_food_cursor.json. Caught by this very case, which
        # reported Paris=178 — the committed value — for a run that examined
        # nothing. Same family as the signatures osm_amenities guessed rather
        # than read.
        _curfile = _os.path.join(_tmpdir, "cur.json")
        m.save_cursor = lambda cur, path=None: _json.dump(cur, open(_curfile, "w"))
        m.load_cursor = lambda path=None: (
            _json.load(open(_curfile)) if _os.path.exists(_curfile) else {})
        cfg = _os.path.join(_tmpdir, "cfg.json")
        _json.dump({"areas": [{"name": "Paris", "center": [48.85, 2.35], "radius_miles": 5,
                               "city": "Paris", "country": "FR"}]}, open(cfg, "w"))
        store = _os.path.join(_tmpdir, "store.json")

        # STOPPING MID-WINDOW is the case that matters: 5 seconds of budget,
        # one second per candidate, twenty candidates.
        rc = m.main(["--config", cfg, "--store", store, "--max-places", "20",
                     "--max-minutes", str(5 / 60)])
        checks.append((rc == 0,
                       "out of time is a clean exit, not a crash — the steps after "
                       "it are what save the run"))
        checks.append((len(_looked) == 5,
                       f"...having stopped on the budget, not on the window "
                       f"({len(_looked)} of 20 examined)"))
        cur = _json.load(open(_curfile))
        checks.append((cur.get("Paris") == 5,
                       f"...with the cursor on what was EXAMINED, never on the "
                       f"window's length — or the next run marches past venues "
                       f"nobody looked at (Paris={cur.get('Paris')})"))

        # And with no budget: the next run resumes THERE and walks the rest.
        before = len(_looked)
        rc2 = m.main(["--config", cfg, "--store", store, "--max-places", "20"])
        cur2 = _json.load(open(_curfile))
        checks.append((rc2 == 0 and len(_looked) - before == 20,
                       f"with no budget it examines the whole window "
                       f"({len(_looked) - before} of 20)"))
        checks.append((cur2.get("Paris") == 5,
                       f"...advancing from where it resumed and wrapping, not "
                       f"restarting at zero (Paris={cur2.get('Paris')})"))

        # AND THE AREA NOT REACHED AT ALL leaves its cursor alone, so the next
        # run begins exactly here rather than skipping a window nobody read.
        # The REAL clock for this one: the fake advances only inside links_for,
        # so it cannot express "time passed before the first candidate" — which
        # is the whole of this case. A budget of 60 microseconds is gone by the
        # time the config is read.
        _looked.clear()
        m.time.time = _real_time
        rc3 = m.main(["--config", cfg, "--store", store, "--max-places", "20",
                      "--max-minutes", "0.000001"])
        cur3 = _json.load(open(_curfile))
        checks.append((rc3 == 0 and not _looked and cur3.get("Paris") == 5,
                       f"an area the budget never reached is skipped with its "
                       f"cursor untouched (Paris={cur3.get('Paris')}, "
                       f"{len(_looked)} looked at)"))
        checks.append((_os.path.exists(store) and _json.load(open(store)),
                       "...having actually written the store, which is the whole "
                       "point of stopping ourselves instead of being cancelled"))
    except Exception as exc:                                    # noqa: BLE001
        checks.append((False, f"main() raised {type(exc).__name__}: {exc}"))
    finally:
        m.load_places, m.links_for = _real_load, _real_links
        m.save_cursor, m.load_cursor, m.time.time = _real_save, _real_loadcur, _real_time
        _shutil.rmtree(_tmpdir, ignore_errors=True)


    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")

    print(f"\n{len(CASES) + len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
