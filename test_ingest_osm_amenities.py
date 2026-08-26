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

    # ------------------------------------------------------------ attribution
    checks.append(("OpenStreetMap contributors (ODbL)" in bare.description,
                   "ODbL attribution is on furniture too — retire_perday keys on that line"))

    # A PLACEHOLDER IS TRUTHY. The WP Event Manager lesson: "-" survives every
    # `if not x` gate and reaches the reader as content.
    dash = ev({"amenity": "give_box", "operator": "-"})
    checks.append((dash.pin_only is True, "'-' is a placeholder, not an operator"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
