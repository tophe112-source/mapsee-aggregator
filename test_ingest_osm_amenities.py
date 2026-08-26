#!/usr/bin/env python3
"""
test_ingest_osm_amenities.py — which civic pins earn a sheet, and which are
just the map doing its job.

The one judgement in mapsee_ingest_osm_amenities is `pin_only`, and both ways
of getting it wrong are silent. Mark a real listing as furniture and its
opening hours become unreachable; mark furniture as a listing and every bench,
bin and bollard in the metro takes a turn at the bottom of somebody's screen
and a place in the Nearby list, burying the concert three streets away.

The second half of these cases is the hours rule, which is the food adapter's
and is here because it means something DIFFERENT for a food bank than for a
playground: no hours on a playground is true, and no hours on a food bank is
somebody standing at a locked door holding an empty bag.

Run: python test_ingest_osm_amenities.py
"""
import sys

import mapsee_ingest_osm_amenities as A

AREA = {"name": "Seattle", "region": "WA", "country": "US"}


def node(tags, ident=1):
    return {"type": "node", "id": ident, "lat": 47.61, "lon": -122.33, "tags": tags}


def ev(tags, ident=1):
    return A.to_event(node(tags, ident), AREA)


def main():
    checks = []

    # ------------------------------------------------- FURNITURE: the default
    #
    # A drinking fountain is a drinking fountain. The pin already says so, and a
    # sheet repeating the pin's own icon back at you costs a tap, the bottom of
    # the screen and a history entry to answer a question nobody asked.
    bare = ev({"amenity": "drinking_water"})
    checks.append((bare is not None, "a bare drinking fountain is still WORTH DRAWING"))
    checks.append((bare.pin_only is True, "...and carries nothing worth reading — furniture"))
    checks.append((bare.category == "outdoors", "on a key a lens already opens onto"))

    # A NAME IS NOT A FACT. "Sarah's Book Box" tells you nothing a book icon on
    # the corner did not. This is the case most likely to be argued with, and
    # the argument is that a sheet has to earn its tap with something ACTIONABLE.
    named = ev({"amenity": "public_bookcase", "name": "Sarah's Book Box"})
    checks.append((named.pin_only is True, "a NAME alone does not earn a sheet"))
    checks.append((named.name == "Sarah's Book Box", "...though the row still carries it"))

    # ------------------------------------------------- LISTINGS: one fact is enough
    for tags, why in [
        ({"amenity": "public_bookcase", "operator": "Little Free Library"},
         "an OPERATOR is a fact — who to thank, and who to ask"),
        ({"amenity": "drinking_water", "fee": "no"},
         "a FEE rule is a fact"),
        ({"amenity": "drinking_water", "wheelchair": "yes"},
         "an ACCESSIBILITY note is a fact — it decides whether to go at all"),
        ({"leisure": "playground", "opening_hours": "Mo-Fr 09:00-17:00"},
         "readable HOURS are a fact"),
        ({"tourism": "artwork", "name": "The Wall", "artist_name": "Ellen Sollod"},
         "an ARTIST is what makes a sculpture worth walking to"),
        ({"amenity": "bicycle_repair_station", "service:bicycle:pump": "yes"},
         "a bike stand that HAS a pump is a different errand from one that does not"),
        ({"leisure": "playground", "description": "Fully fenced, shaded, with a splash pad."},
         "a real DESCRIPTION is a fact"),
        ({"amenity": "give_box", "access": "yes"},
         "an ACCESS rule is a fact"),
    ]:
        row = ev(tags)
        checks.append((row is not None and row.pin_only is False, why))

    # ---------------------------------------------- A PICTURE IS CONTENT TOO
    #
    # For artwork and playgrounds the photograph is the best content there is:
    # a sculpture you can see before walking to it. So an image ALONE promotes
    # a row out of furniture, with no other fact required.
    art = ev({"tourism": "artwork", "name": "The Wall",
              "wikimedia_commons": "File:Seattle Wall.jpg"})
    checks.append((art.pin_only is False, "an image alone earns a sheet"))
    checks.append((art.poster_image_url ==
                   "https://commons.wikimedia.org/wiki/Special:FilePath/Seattle_Wall.jpg",
                   "...and a Commons FILE NAME is turned into its documented redirect"))
    checks.append((A.own_image({"image": "https://example.org/a.jpg"}) is not None,
                   "a plain https image tag is taken as-is"))
    # http would be blocked as mixed content on the way into the app, which
    # renders as a BROKEN image rather than as no image.
    checks.append((A.own_image({"image": "http://example.org/a.jpg"}) is None,
                   "...and an http one is refused, because it would render broken"))
    checks.append((A.own_image({"wikimedia_commons": "Category:Statues"}) is None,
                   "a Commons CATEGORY is not a file and does not become an image URL"))
    bare_art = ev({"tourism": "artwork", "name": "Untitled"})
    checks.append((bare_art.pin_only is True and bare_art.poster_image_url is None,
                   "a named sculpture with no artist and no photo is still furniture"))

    # ------------------------------------------------------------ the hours rule
    #
    # PARSEABLE hours become both a claim and a rolling window.
    hours = ev({"leisure": "playground", "opening_hours": "Mo-Fr 09:00-17:00"})
    checks.append(("🕑 Open: Mon-Fri 09:00-17:00" in hours.description,
                   "a parsed week is summarised as a range, not as five identical lines"))
    checks.append((hours.recurring_days.get("5") is None,
                   "...and a day the source did not open is not invented"))

    # 24/7 IS TRUE AND NOT WORTH A LINE. It is what the pin already implies.
    always = ev({"leisure": "playground", "opening_hours": "24/7"})
    checks.append((always.pin_only is True and "🕑" not in always.description,
                   "'24/7' on a playground says nothing new — still furniture"))
    checks.append((len(always.recurring_days) == 7,
                   "...but the row still rolls every day, so it never expires"))

    # AN UNREADABLE STRING IS NOT AN OPEN SIGN. The food adapter's rule: the
    # refusal matters most of all, because ignoring "shut" advertises a place
    # as open.
    junk = ev({"leisure": "playground", "opening_hours": "dawn till dusk-ish"})
    checks.append((junk is not None and "🕑" not in junk.description,
                   "hours we cannot parse produce NO hours claim"))
    checks.append((junk.pin_only is True,
                   "...and an unreadable string does not promote furniture to a listing"))

    # A FOOD BANK IS THE EXCEPTION, and it is the whole reason `always_open`
    # exists. No hours on a playground is true. No hours on a food bank is a
    # person at a locked door.
    fb_bare = ev({"social_facility": "food_bank"})
    checks.append((fb_bare is not None and fb_bare.pin_only is True,
                   "a food bank with no readable hours is still DRAWN — knowing it is there matters"))
    checks.append(("🕑" not in fb_bare.description,
                   "...and makes no claim whatsoever about being open"))
    fb = ev({"social_facility": "food_bank", "name": "Rainier Food Bank",
             "opening_hours": "Tu,Th 10:00-14:00", "operator": "Northwest Harvest"})
    checks.append((fb.pin_only is False and "Tue 10:00-14:00" in fb.description,
                   "a food bank that publishes its hours is a listing that states them"))
    checks.append((fb.category == "volunteer", "and lands on the volunteer layer"))

    # ------------------------------------------------------------- placement
    row = ev({"leisure": "playground", "name": "Cal Anderson Play Area",
              "operator": "Seattle Parks", "addr:city": "Seattle",
              "addr:housenumber": "1635", "addr:street": "11th Ave"})
    checks.append((row.coords_exact is True,
                   "OSM's point is surveyed — the geocoder must never move it"))
    checks.append((row.address == "1635 11th Ave" and row.city == "Seattle",
                   "the street is the street and the city is the city — never glued"))
    # THE HUB'S NAME IS NOT THE PLACE'S TOWN. The food adapter shipped that in
    # both the field and the prose, and moved a Sequim diner sixty miles.
    no_town = ev({"leisure": "playground", "operator": "Parks"})
    checks.append((no_town.city is None,
                   "no addr:city means NO city — never the hub's name"))

    # ------------------------------------------------------ the selector itself
    checks.append((A.kind_of({"amenity": "bench"}) is None,
                   "an element outside the eight selectors is not ours"))
    checks.append((A.kind_of({"social_facility": "food_bank"}) is not None
                   and A.kind_of({"amenity": "food_bank"}) is None,
                   "food banks are social_facility=food_bank (4,938) not amenity=food_bank (16)"))
    sel = A.selector(0, 0, 1, 1)
    checks.append((all(f'["{k.key}"="{k.value}"]' in sel for k in A.KINDS),
                   "every KIND appears in the Overpass union — the query and the "
                   "Python filter are built from one table"))
    checks.append(('["name"]' not in sel,
                   "and nothing requires a name: that would delete the fountains"))

    # ---------------------------------------- A PLACE IS NOT A "SHOW"
    #
    # The sync appends "🔎 More on this show: <google search>" as a web-search
    # fallback. Its own comment says that is for the big-venue aggregators, but
    # the implementation is a DENY-list, so every adapter added since inherited
    # it — including all three OpenStreetMap PLACE adapters. A drinking
    # fountain has no support acts.
    #
    # It matters more than it reads, and this is how it was found: ../mapsee
    # 0195 decides whether a pin opens by asking whether anything survives
    # stripping the row's boilerplate, and a Google link is not a fact about a
    # fountain — so every furniture pin on earth would have become clickable.
    # Caught only by generating the REAL stored description and reading it.
    from mapsee_supabase_sync import to_row as _to_row
    import json as _json
    def _stored(tags):
        row = ev(tags)
        rec = _json.loads(_json.dumps(row.as_record("2026-08-26T00:00:00Z")))
        rec["fingerprint"], rec["source"] = row.fingerprint, row.source
        return _to_row(rec, "00000000-0000-0000-0000-000000000001")["description"]
    checks.append(("More on this show" not in _stored({"amenity": "drinking_water"}),
                   "a stored furniture row carries NO 'more on this show' search link"))
    checks.append(("More on this show" not in _stored(
                       {"social_facility": "food_bank", "name": "Rainier Food Bank",
                        "opening_hours": "Tu,Th 10:00-14:00"}),
                   "...and neither does a listing"))
    checks.append(("OpenStreetMap contributors (ODbL)" in _stored({"amenity": "give_box"}),
                   "...while the ODbL line the client strips is still there"))

    # ------------------------------------------------------------ attribution
    checks.append(("OpenStreetMap contributors (ODbL)" in bare.description,
                   "ODbL attribution is on furniture too — retire_perday keys on that line"))

    # A PLACEHOLDER IS TRUTHY. The WP Event Manager lesson: "-" survives every
    # `if not x` gate and reaches the reader as content.
    dash = ev({"amenity": "give_box", "operator": "-"})
    checks.append((dash.pin_only is True, "'-' is a placeholder, not an operator"))

    # ------------------------------- THE CONFIG IS PART OF THE ADAPTER
    #
    # Every case above builds a NormalizedEvent directly, so all of them passed
    # while osm_amenity_sources.json was unloadable: it wrote lat/lon as
    # separate keys where area_bbox reads a `center` PAIR. Valid JSON, every
    # test green, and `KeyError: 'center'` on the first line of the first real
    # run — after the Overpass fetch had been queued and a runner spent.
    #
    # Same family as the parkrun config that was never committed while the job
    # printed a friendly skip: a config a job needs is part of the job, and
    # nothing that only tests the pure functions can see it.
    import json as _json
    from mapsee_ingest_osm_food import area_bbox as _bbox, tiles as _tiles
    cfg = _json.load(open("osm_amenity_sources.json", encoding="utf-8"))
    areas = cfg.get("areas") or []
    checks.append((len(areas) > 0, "osm_amenity_sources.json declares areas"))
    for area in areas:
        name = area.get("name", "?")
        try:
            south, west, north, east = _bbox(area)
            ok = south < north and west < east
            cells = len(_tiles((south, west, north, east), max_deg=A.TILE_DEG))
        except Exception as exc:                            # noqa: BLE001
            ok, cells, south = False, 0, f"{type(exc).__name__}: {exc}"
        checks.append((ok, f"{name}: area_bbox reads it, and the box is the right way up"))
        # A hub that tiles into nothing would fetch nothing and report success.
        checks.append((cells >= 1, f"{name}: covers at least one Overpass tile"))
    checks.append((all(a.get("country") for a in areas),
                   "every area names its country — the sync has no other source for it"))

    # ---------------------------- MAIN() ITSELF, RUN END TO END
    #
    # BOTH production failures of this adapter were in main(), and NEITHER was
    # reachable from any case above, because every one of them calls to_event
    # or a pure helper directly:
    #
    #   KeyError: 'center'                  the config's own shape
    #   ValueError: too many values         window_at returns a LIST, and the
    #                                       caller owns the next cursor
    #
    # Both are contracts of code IMPORTED from mapsee_ingest_osm_food, guessed
    # rather than read, and both cost a runner and a full Overpass fetch to
    # discover — the second after pulling 7,210 elements for Seattle.
    #
    # So this runs the real main() with Overpass stubbed and the store in a
    # temp dir: the config is loaded by the code that loads it, every imported
    # helper is called with the arguments it will really get, and the cursor
    # arithmetic actually executes.
    import os as _os, tempfile as _tempfile, json as _json2
    _fake = [
        {"type": "node", "id": 1, "lat": 47.61, "lon": -122.33,
         "tags": {"amenity": "drinking_water"}},                       # furniture
        {"type": "node", "id": 2, "lat": 47.62, "lon": -122.34,
         "tags": {"amenity": "drinking_water", "fee": "no"}},          # listing
        {"type": "node", "id": 3, "lat": 47.63, "lon": -122.35,
         "tags": {"leisure": "playground", "name": "Cal Anderson"}},   # furniture
        {"type": "node", "id": 4, "lat": 47.64, "lon": -122.36,
         "tags": {"tourism": "artwork", "name": "The Wall",
                  "artist_name": "Ellen Sollod"}},                     # listing
        {"type": "node", "id": 5, "lat": 47.65, "lon": -122.37,
         "tags": {"amenity": "bench"}},                                # not ours
    ]
    _real_sweep = A.sweep_tiles
    A.sweep_tiles = lambda cells, fetch_one, label, delay=2.0, sleep=None: (_fake, True)
    # CAUGHT, NOT RAISED. Both real failures were exceptions, and letting one
    # abort the run buries the sixty-two cases that come before it under a
    # traceback. The exception IS the failure message.
    try:
        with _tempfile.TemporaryDirectory() as tmp:
            store = _os.path.join(tmp, "feeds.json")
            rc = A.main(["--config", "osm_amenity_sources.json", "--store", store,
                         "--only", "Seattle", "--max-places", "50",
                         "--places-cache", _os.path.join(tmp, "cache"),
                         "--ignore-cursor"])
            checks.append((rc == 0, "main() runs to completion against a stubbed Overpass"))
            rows = _json2.load(open(store, encoding="utf-8")).get("events", [])
            checks.append((len(rows) == 4,
                           f"...and writes one row per SELECTED element, skipping the bench "
                           f"({len(rows)})"))
            pin_only = [r for r in rows if r.get("pin_only")]
            checks.append((len(pin_only) == 2 and len(rows) - len(pin_only) == 2,
                           f"...with the split intact through the whole path "
                           f"({len(pin_only)} furniture, {len(rows)-len(pin_only)} listing)"))
            # The cursor arithmetic is what the ValueError was hiding in.
            A.main(["--config", "osm_amenity_sources.json", "--store", store,
                    "--only", "Seattle", "--max-places", "2",
                    "--places-cache", _os.path.join(tmp, "cache2")])
            cur = A.load_cursor(A.CURSOR_PATH) if _os.path.exists(A.CURSOR_PATH) else {}
            checks.append((cur.get("Seattle") == 2,
                           f"...and a bounded window advances the cursor by what it examined "
                           f"({cur.get('Seattle')})"))
    except Exception as exc:                                    # noqa: BLE001
        checks.append((False, f"main() raised {type(exc).__name__}: {exc}"))
    finally:
        A.sweep_tiles = _real_sweep
        if _os.path.exists(A.CURSOR_PATH):
            _os.remove(A.CURSOR_PATH)

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
