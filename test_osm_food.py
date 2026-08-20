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
    # OSM writes past midnight as hours >= 24, and the c <= o test above never
    # catches it because 27:00 sorts AFTER 11:00. Accepted, it became the literal
    # local timestamp "2026-08-14T27:00:00", which Postgres rejects with
    # "date/time field value out of range" — and the sync reported that as a
    # moderation block. Voodoo Doughnut, Los Tacos Mexicali, Happy Fortune and
    # Carribean Bokit Factory were all lost this way on the first full refresh:
    # late-night places, which are the ones a "hungry right now" map most wants.
    ("Mo-Su 11:00-27:00", None, "past midnight written as hour 27"),
    ("Mo-Su 11:00-24:00", None, "24:00 is the next day, not this one"),
    ("Mo-Su 10:00-24:45", None, "24:45"),
    ("Mo-Fr 09:00-25:00", None, "25:00"),
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
        (bool(rows and rows[0].recurring_days.get("0") == ["11:00", "22:00"]),
         "0=Monday, matching what the roller expects"),
    ]
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

    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")

    print(f"\n{len(CASES) + len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
