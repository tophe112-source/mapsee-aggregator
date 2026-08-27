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

    # ------------------------------------- A FACT BUYS A SHEET, NOT A LISTING
    #
    # These used to assert `pin_only is False` — that one fact promoted a row
    # into the Nearby list. That was wrong, and measurably: 752 rows from this
    # adapter were in one Seattle box's Nearby list and 745 of them were open
    # 24 hours a day, which is 58% of everything under `kids` and 67% under
    # `arts`. Nearby is a list of WHAT IS ON, and a well-described playground
    # is not on — it is there, at 3am, exactly as it is now.
    #
    # So what a fact buys is what the PIN does: ../mapsee's amenityHasContent
    # reads the description written here and gives a pin with something to say
    # a hover label and a tap that opens its sheet. `has_content` is this
    # repo's half of that, and it is what these cases assert now.
    for tags, why in [
        ({"amenity": "public_bookcase", "operator": "Ballard Rotary Club"},
         "an OPERATOR is a fact — who to thank, and who to ask"),
        ({"amenity": "drinking_water", "fee": "yes", "charge": "50c"},
         "a CHARGE is a fact"),
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
        ({"amenity": "give_box", "access": "private"},
         "a RESTRICTIVE access rule is a fact — it saves a wasted walk"),
    ]:
        row = ev(tags)
        kind = A.kind_of(tags)
        checks.append((row is not None and A.has_content(
            tags, kind, A.read_hours(tags, kind)[1]), why))
        # ...and unless it can be SHUT, it is still scenery rather than a row
        # in the list of what is on.
        if not tags.get("opening_hours"):
            checks.append((row.pin_only is True,
                           f"...and stays off the Nearby list, being always open "
                           f"({why.split(' — ')[0].split(' is ')[0]})"))

    # ---------------------------------------------- A PICTURE IS CONTENT TOO
    #
    # For artwork and playgrounds the photograph is the best content there is:
    # a sculpture you can see before walking to it. So an image ALONE promotes
    # a row out of furniture, with no other fact required.
    art = ev({"tourism": "artwork", "name": "The Wall",
              "wikimedia_commons": "File:Seattle Wall.jpg"})
    checks.append((A.has_content({"tourism": "artwork", "name": "Untitled",
                                  "wikimedia_commons": "File:Seattle Art.jpg"},
                                 A.BY_SLUG["tourism=artwork"], None),
                   "an image alone earns a sheet"))
    checks.append((art.pin_only is True,
                   "...on a pin, not a Nearby row — a sculpture is always there"))
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

    # ------------------- A VALUE THAT MATCHES THE ASSUMPTION IS NOT A FACT
    #
    # "a name is not a fact", one level down, and missed on the first pass.
    # `access=yes` is the commonest tag on a playground and `fee=no` on a
    # drinking fountain, so together they were promoting a large share of the
    # two densest selectors — to sheets whose entire content was
    # "🚪 Access: Open to everyone". That is precisely the tap-for-nothing this
    # whole split exists to prevent. Free and public is what a civic amenity
    # IS; only the deviation is worth a sheet.
    for tags, why in [
        ({"leisure": "playground", "name": "Cal Anderson", "access": "yes"},
         "access=yes says nothing a public playground did not already say"),
        ({"leisure": "playground", "access": "public"}, "...nor does access=public"),
        ({"leisure": "playground", "access": "permissive"}, "...nor permissive"),
        ({"amenity": "drinking_water", "fee": "no"},
         "fee=no says nothing a public fountain did not already say"),
    ]:
        row = ev(tags)
        kind = A.kind_of(tags)
        checks.append((not A.has_content(tags, kind, None), why))
        checks.append(("🚪" not in row.description and "🎟" not in row.description,
                       f"...and the line is not printed at all ({why[:28]}…)"))
    # ...but the deviation still counts, and accessibility is never assumed.
    # It buys a hover and a sheet on the PIN; it does not buy a Nearby row,
    # because none of these things ever shuts.
    for tags, why in [
        ({"leisure": "playground", "access": "private"}, "access=private IS a fact"),
        ({"amenity": "drinking_water", "fee": "yes"}, "a charge IS a fact"),
        ({"leisure": "playground", "wheelchair": "no"},
         "wheelchair is a fact in EVERY value — nobody may assume it either way"),
        ({"leisure": "playground", "wheelchair": "yes"}, "...including yes"),
    ]:
        checks.append((A.has_content(tags, A.kind_of(tags), None), why))
        checks.append((ev(tags).pin_only is True,
                       f"...and it is still a pin, not a listing ({why[:26]}…)"))

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

    # A FOOD BANK IS THE EXCEPTION TWICE OVER, and the two exceptions are
    # different rules that happen to live on the same selector.
    #
    # `always_open` is about the HOURS: no hours on a playground is true, and
    # no hours on a food bank is a person at a locked door holding an empty
    # bag, so it never gets the open-all-week window the other kinds get.
    #
    # `always_list` is about the SHEET: every other selector has to earn its
    # tap, because a fountain's pin already carries the whole of what its row
    # knows. WHERE A FOOD BANK IS does not work like that — the location is
    # itself the thing somebody came looking for, and whether they can open it,
    # read its name, route to it or send it to someone must not depend on
    # whether a mapper filled in a phone number.
    fb_bare = ev({"social_facility": "food_bank", "name": "ACRS Food Bank"})
    checks.append((fb_bare is not None and fb_bare.pin_only is False,
                   "a food bank with nothing else tagged is still a LISTING — where it "
                   "is, is the fact"))
    checks.append(("not listed" in fb_bare.description,
                   "...and says out loud that the hours are unknown, rather than "
                   "leaving a silence a reader fills in with \"open, presumably\""))
    checks.append((fb_bare.recurring_days == A.ALWAYS,
                   "...while the all-week window it carries is the roller's, not a "
                   "claim: parkrun's all-day event, not an invented time"))
    checks.append((ev({"leisure": "playground"}).pin_only is True,
                   "and the exemption is ONE selector — a bare playground is still "
                   "furniture, or 0194 unravels by degrees"))

    fb = ev({"social_facility": "food_bank", "name": "Rainier Food Bank",
             "opening_hours": "Tu,Th 10:00-14:00", "operator": "Northwest Harvest"})
    checks.append((fb.pin_only is False and "Tue 10:00-14:00" in fb.description,
                   "a food bank that publishes its hours is a listing that states them"))
    checks.append((fb.category == "volunteer", "and lands on the volunteer layer"))

    # AN UNREADABLE STRING IS NOT AN OPEN SIGN — AND IT IS NOT AN ABSENT ONE
    # EITHER. Our parser refuses "Mo-Fr 09:00-17:00; PH off" because it cannot
    # be ACTED on; the person standing outside reads it fine. Quote it,
    # attribute it, claim nothing.
    fb_junk = ev({"social_facility": "food_bank", "name": "St Mary's Pantry",
                  "opening_hours": "Mo-Fr 09:00-17:00; PH off"})
    checks.append((fb_junk.recurring_days == A.ALWAYS,
                   "an unreadable food-bank string still produces NO weekly pattern"))
    checks.append(("as tagged in OpenStreetMap: Mo-Fr 09:00-17:00; PH off" in fb_junk.description,
                   "...and is quoted verbatim and attributed, because unparseable "
                   "is not unreadable"))
    checks.append(("Open:" not in fb_junk.description,
                   "...and never as our own \"Open:\" claim"))

    # 24/7 IS DELIBERATELY SILENT ON A FOUNTAIN AND IS THE BEST NEWS ON A FOOD
    # BANK. "Never closes" is what a fountain's pin already implies.
    fb_247 = ev({"social_facility": "food_bank", "name": "Little Free Pantry",
                 "opening_hours": "24/7"})
    checks.append(("Open at all hours" in fb_247.description,
                   "a 24/7 food bank says so"))
    checks.append(("🕑" not in ev({"amenity": "drinking_water",
                                           "opening_hours": "24/7"}).description,
                   "...where a 24/7 fountain still says nothing: the pin already did"))

    # NAMING A THING AFTER ITS OWN KIND. Unnamed rows were all furniture until
    # food banks started listing, so "Food bank — food bank." was never read.
    unnamed = ev({"social_facility": "food_bank", "addr:city": "Seattle"})
    checks.append((unnamed.description.startswith("Food bank in Seattle."),
                   "an unnamed row does not introduce itself twice"))

    # ------------------------------- AN OPERATOR THAT RESTATES THE THING
    #
    # "A NAME IS NOT A FACT", one tag over. `operator=Little Free Library` on a
    # little free library says exactly what the title and the book glyph
    # already said — and it was PROMOTING rows: live in Seattle, 47 openable
    # pins carried that line and 35 of them had nothing else, so the whole
    # content of their sheet was the row's own kind read back at them.
    for tags, want, why in [
        ({"amenity": "public_bookcase", "name": "Little Free Library",
          "operator": "Little Free Library"}, False,
         "an operator that repeats the row's NAME prints nothing"),
        ({"amenity": "public_bookcase", "operator": "Little Free Library"}, False,
         "...nor one that is just the kind's own noun, on an unnamed row"),
        ({"leisure": "playground", "name": "Cal Anderson Play Area",
          "operator": "Seattle Parks"}, True,
         "a REAL operator still prints — who to thank, and who to ask"),
        ({"amenity": "public_bookcase", "name": "LFL #123",
          "operator": "Little Free Library Ltd"}, True,
         "...and a superstring is a different name, so it prints too"),
    ]:
        kind = A.kind_of(tags)
        got = any(l.startswith("🏛") for l in A.useful_lines(tags, kind, None))
        checks.append((got is want, why))
    tauto = ev({"amenity": "public_bookcase", "name": "Little Free Library",
                "operator": "Little Free Library"})
    checks.append((tauto.pin_only is True and "🏛" not in tauto.description,
                   "...so a row whose ONLY fact was that tautology is scenery again"))

    # ------------------------------------------- QUOTES, AND PUNCTUATION-ONLY
    #
    # OSM wraps some description values in quotes, and the hover label is where
    # that shows: live in Seattle, 34 of 1,000 civic pins read "Catfish — 'The
    # ceramic tiles…" or "…band type head saw.'" once the description reached a
    # tooltip. And one sculpture's ENTIRE description is a single apostrophe —
    # the WP Event Manager "-" trap in another costume, because punctuation is
    # truthy and would make a pin openable on nothing.
    for raw, want, why in [
        ("'The ceramic tiles in place of pavers.'", "The ceramic tiles in place of pavers.",
         "a matching pair of quotes is OSM's, not the author's"),
        ("Cast iron abstraction of band type head saw.'", "Cast iron abstraction of band type head saw.",
         "...and so is an unbalanced straggler, when it is the only quote there"),
        ('He said "hello" loudly', 'He said "hello" loudly',
         "a quote WITH a partner is somebody's punctuation — left alone"),
        ("It's a mural", "It's a mural", "an apostrophe inside a word is not a wrapper"),
        ("'", None, "a lone apostrophe is not a description"),
        ("...", None, "nor is punctuation on its own"),
        ("-", None, "the original placeholder still goes"),
    ]:
        checks.append((A._clean(raw) == want, f"{why} ({A._clean(raw)!r})"))
    quoted = ev({"tourism": "artwork", "name": "Catfish",
                 "description": "'The ceramic tiles create an illusion.'"})
    checks.append(("'The ceramic" not in quoted.description
                   and "The ceramic tiles create an illusion." in quoted.description,
                   "...and the stored description carries neither wrapper"))
    bare_quote = ev({"tourism": "artwork", "name": "Coelacanths", "description": "'"})
    checks.append((bare_quote.pin_only is True
                   and not A.has_content({"tourism": "artwork", "name": "Coelacanths",
                                          "description": "'"},
                                         A.BY_SLUG["tourism=artwork"], None),
                   "a sculpture described as \"'\" does not become openable on it"))

    # --------------------------- A BARE ONE OF THESE IS NOT WORTH A DOT
    #
    # Seven of the nine selectors are here BECAUSE their bare existence is the
    # answer: "there is a drinking fountain on that corner" needs no words.
    # `tourism=artwork` is not like that — OSM's definition takes in every
    # tagged wall, and "there is art here" tells nobody anything.
    #
    # Measured in one Seattle box: 404 artworks, 146 unnamed, and 55 tagged
    # `artwork_type=graffiti` of which ZERO carried a name. Along Eastlake they
    # draw a solid line of 🎨 down the side of I-5, burying ten drinking
    # fountains and nine playgrounds in the same view.
    #
    # The type is NOT what earns it. `🗿 Type: graffiti` is a restatement of the
    # category — "a name is not a fact", one tag over — and it was the entire
    # content of the row in the screenshot that prompted this.
    for tags, want, why in [
        ({"tourism": "artwork", "artwork_type": "graffiti"}, None,
         "an unnamed wall tag is not imported at all"),
        ({"tourism": "artwork"}, None, "nor is an artwork with nothing on it"),
        ({"tourism": "artwork", "artwork_type": "graffiti", "access": "yes"}, None,
         "...and an assumed access value does not rescue it"),
        ({"tourism": "artwork", "name": "The Wall"}, "The Wall",
         "a NAME makes it findable, so it is worth a dot"),
        ({"tourism": "artwork", "artist_name": "D. Crabtree"}, "Public artwork",
         "so does an artist, on an untitled piece"),
        ({"tourism": "artwork", "description": "A ceramic catfish."}, "Public artwork",
         "so does a real description"),
        ({"tourism": "artwork", "inscription": "1898-1902"}, "Public artwork",
         "so does an inscription — it is what you would go to read"),
        ({"tourism": "artwork", "wikimedia_commons": "File:X.jpg"}, "Public artwork",
         "and a photograph is the best reason of all"),
        ({"tourism": "artwork", "artwork_type": "graffiti", "name": "Wall of Fame"},
         "Wall of Fame", "a NAMED mural is a destination, whatever its type"),
    ]:
        row = ev(tags)
        checks.append(((row.name if row else None) == want, why))
    keep = ev({"tourism": "artwork", "name": "Catfish", "artwork_type": "sculpture"})
    checks.append(("Type: sculpture" in keep.description,
                   "...and a kept piece still PRINTS its type — worth reading, "
                   "just not worth a pin on its own"))
    # Every other selector is unchanged: a bare one is still drawn.
    for tags, why in [({"amenity": "drinking_water"}, "a bare fountain"),
                      ({"amenity": "toilets"}, "a bare public toilet"),
                      ({"leisure": "playground"}, "a bare playground")]:
        checks.append((ev(tags) is not None, f"{why} is still imported — the pin IS the answer"))

    # ------------------------------------- PUBLIC TOILETS, the one people ask for
    #
    # 519,045 worldwide (taginfo 2026-08-27) — denser than drinking water, and
    # the thing a map of "what this neighbourhood already has" is expected to
    # know. Same shape as a fountain: untagged means the pin is the information,
    # real hours make it a listing that can be shut.
    loo = ev({"amenity": "toilets", "changing_table": "yes", "wheelchair": "yes",
              "fee": "yes", "charge": "50p"})
    checks.append((loo.category == "outdoors" and loo.pin_only is True,
                   "a public toilet lands on outdoors, as a pin"))
    for want, why in [("🚼 Baby changing table.", "a baby changing table is a fact worth the walk"),
                      ("🎟 Charge: 50p", "and so is a charge"),
                      ("♿ Accessibility", "and accessibility, as everywhere")]:
        checks.append((want in loo.description, why))
    shuts = ev({"amenity": "toilets", "name": "Pier 62 Restroom",
                "opening_hours": "Mo-Su 06:00-22:00"})
    checks.append((shuts.pin_only is False,
                   "a toilet block that LOCKS at night is a listing, like anything "
                   "else that can be shut"))

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
    import os as _os, shutil as _shutil, tempfile as _tempfile, json as _json2
    _fake = [
        {"type": "node", "id": 1, "lat": 47.61, "lon": -122.33,
         "tags": {"amenity": "drinking_water"}},                       # furniture
        {"type": "node", "id": 2, "lat": 47.62, "lon": -122.34,
         "tags": {"amenity": "drinking_water", "bottle": "yes"}},      # pin, opens
        {"type": "node", "id": 3, "lat": 47.63, "lon": -122.35,
         "tags": {"leisure": "playground", "name": "Cal Anderson"}},   # pin, inert
        {"type": "node", "id": 4, "lat": 47.64, "lon": -122.36,
         "tags": {"tourism": "artwork", "name": "The Wall",
                  "artist_name": "Ellen Sollod"}},                     # pin, opens
        # THE ONLY LISTING IN THE SET, and the only one that can be SHUT.
        {"type": "node", "id": 6, "lat": 47.66, "lon": -122.38,
         "tags": {"leisure": "playground", "name": "Gated Play Area",
                  "opening_hours": "Mo-Su 08:00-20:00"}},              # LISTING
        {"type": "node", "id": 5, "lat": 47.65, "lon": -122.37,
         "tags": {"amenity": "bench"}},                                # not ours
    ]
    _real_sweep, _real_cursor = A.sweep_tiles, A.CURSOR_PATH
    A.sweep_tiles = lambda cells, fetch_one, label, delay=2.0, sleep=None: (_fake, True)
    # THE CURSOR FILE IN THE REPO IS PRODUCTION STATE — it holds every metro's
    # position in its candidate list, committed by the workflow's cursor job.
    # An earlier version of this test removed it in its `finally`, which was
    # invisible while the file held `{}` and would have silently reset every
    # metro to zero the moment the sweep was actually running. A test does not
    # get to write there at all.
    _cursor_dir = _tempfile.mkdtemp()
    A.CURSOR_PATH = _os.path.join(_cursor_dir, "cursor.json")
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
            checks.append((len(rows) == 5,
                           f"...and writes one row per SELECTED element, skipping the bench "
                           f"({len(rows)})"))
            pin_only = [r for r in rows if r.get("pin_only")]
            checks.append((len(pin_only) == 4 and len(rows) - len(pin_only) == 1,
                           f"...with the split intact through the whole path "
                           f"({len(pin_only)} pins, {len(rows)-len(pin_only)} listing — only "
                           f"the one that shuts is a listing)"))
            # The cursor arithmetic is what the ValueError was hiding in.
            A.main(["--config", "osm_amenity_sources.json", "--store", store,
                    "--only", "Seattle", "--max-places", "2",
                    "--places-cache", _os.path.join(tmp, "cache2")])
            cur = A.load_cursor(A.CURSOR_PATH)
            checks.append((cur.get("Seattle") == 2,
                           f"...and a bounded window advances the cursor by what it examined "
                           f"({cur.get('Seattle')})"))
    except Exception as exc:                                    # noqa: BLE001
        checks.append((False, f"main() raised {type(exc).__name__}: {exc}"))
    finally:
        A.sweep_tiles, A.CURSOR_PATH = _real_sweep, _real_cursor
        _shutil.rmtree(_cursor_dir, ignore_errors=True)

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
