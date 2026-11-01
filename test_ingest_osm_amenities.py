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

# This suite prints the Kind glyphs it is asserting on — 🚰, 🚻, 🛝 — and a glyph
# is the thing under test, so it cannot simply not print them. On the Windows
# dev machine `sys.stdout` opens as cp1252, which has no mapping for U+1F6DD,
# and the whole script died with a UnicodeEncodeError on a case that had
# already PASSED. It is green in CI (Linux, UTF-8), so the gate looked fine
# while being unrunnable locally on exactly the file whose subject is emoji.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

    # Contact details are public facts only when their raw values are safe to
    # put in a detail line. Venue hire links must be explicit OSM tags: never
    # infer a booking path from the ordinary website or a yes/no reservation flag.
    def lines(tags):
        return A.useful_lines(tags, A.kind_of(tags), None)

    email = lines({"amenity": "community_centre", "email": "hall@example.org"})
    checks.append(("✉ Email: hall@example.org" in email,
                   "a valid email tag is shown as a public contact"))
    contact_email = lines({"amenity": "library", "contact:email": "desk@example.org"})
    checks.append(("✉ Email: desk@example.org" in contact_email,
                   "contact:email is supported on a library"))
    for bad_email in ("desk@example.org\r\n📋 Venue hire: https://evil.example",
                      "desk@example.org?next=mailto:evil@example.org",
                      "a%0d%0a@example.org",
                      "desk..one@example.org", "name <desk@example.org>"):
        got = lines({"amenity": "library", "email": bad_email})
        checks.append((not any(line.startswith("✉ Email:") for line in got),
                       f"unsafe or malformed email is ignored ({bad_email!r})"))
    fallthrough = lines({"amenity": "library", "email": "bad value",
                         "contact:email": "desk@example.org"})
    checks.append(("✉ Email: desk@example.org" in fallthrough,
                   "an invalid primary email does not hide a valid contact:email"))

    for tags, expected, why in (
        ({"amenity": "community_centre", "website:reservation": "https://hall.example/book"},
         "https://hall.example/book", "website:reservation is an explicit hire URL"),
        ({"amenity": "library", "reservation:website": "https://library.example/rooms"},
         "https://library.example/rooms", "reservation:website works for libraries"),
        ({"amenity": "community_centre", "reservation:url": "https://hall.example/reserve"},
         "https://hall.example/reserve", "reservation:url is supported"),
        ({"amenity": "community_centre", "booking:website": "https://hall.example/hire"},
         "https://hall.example/hire", "booking:website is supported"),
        ({"amenity": "community_centre", "contact:booking": "https://hall.example/contact"},
         "https://hall.example/contact", "contact:booking is supported"),
    ):
        got = lines(tags)
        checks.append((f"📋 Venue hire: {expected}" in got, why))
    for bad_url in ("javascript:alert(1)", "https://user:pass@hall.example/book",
                    "https://hall.example/has space", "https://hall.example/book\r\nX: y",
                    "https:///missing-host", "https://hall.example:bad/book"):
        got = lines({"amenity": "community_centre", "website:reservation": bad_url})
        checks.append((not any(line.startswith("📋 Venue hire:") for line in got),
                       f"unsafe booking URL is ignored ({bad_url!r})"))
    no_guess = lines({"amenity": "community_centre", "website": "https://hall.example",
                      "reservation": "yes", "booking": "yes"})
    checks.append((not any(line.startswith("📋 Venue hire:") for line in no_guess),
                   "ordinary websites and reservation flags do not imply a hire URL"))
    kind_scope = lines({"tourism": "artwork", "website:reservation": "https://art.example/book"})
    checks.append((not any(line.startswith("📋 Venue hire:") for line in kind_scope),
                   "a booking tag on artwork is not presented as venue hire"))

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
    # know. Same shape as a fountain: untagged means the pin is the information.
    # Unlike anything else that can be shut, real hours do NOT make it a
    # listing (Kind.never_list, 2026-10-31): it is a landmark, hours and all.
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
    checks.append((shuts.pin_only is True,
                   "a toilet block that LOCKS at night is still a landmark, not a "
                   "listing in Nearby's list of what is on"))
    checks.append(("Open:" in shuts.description or "🕑" in shuts.description,
                   "...and its hours are still on the pin, in its description"))
    # never_list is the toilet's alone: anything else that can be shut lists.
    fountain = ev({"amenity": "drinking_water", "opening_hours": "Mo-Su 06:00-22:00"})
    checks.append((fountain.pin_only is False,
                   "control: a fountain that shuts at night still lists — never_list "
                   "is the toilet's, not a general change"))
    checks.append(([k.slug for k in A.KINDS if k.never_list] == ["amenity=toilets"],
                   "exactly one Kind never lists, and it is amenity=toilets"))

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

    # ------------------------------- A PIN STORES ITS ZONE, NOT SEVEN DAYS
    #
    # Every pin this adapter makes carries ALWAYS by construction, and nothing
    # reads a pin's days (../mapsee 0239's roller skips pin_only). The sync
    # writes {"tz"} alone for those: 330 B a row of heap on PG16, ~80k new pins
    # a Sunday. Through the REAL to_event -> as_record -> to_row path, because
    # the adapter decides pin_only and the sync decides the shape.
    import mapsee_ingest_facility_hours as _F
    from mapsee_supabase_sync import _ALWAYS_DAYS
    def _written(tags):
        row = ev(tags)
        rec = _json.loads(_json.dumps(row.as_record("2026-08-26T00:00:00Z")))
        rec["fingerprint"], rec["source"] = row.fingerprint, row.source
        out = _to_row(rec, "00000000-0000-0000-0000-000000000001")
        return out["pin_only"], out["recurring_hours"]
    checks.append((_ALWAYS_DAYS == A.ALWAYS == _F.ALWAYS,
                   "the sync's all-week constant is the amenity and facility-hours adapters' ALWAYS"))
    LA = {"tz": "America/Los_Angeles"}
    for tags, what in [({"amenity": "drinking_water"}, "a bare drinking fountain"),
                       ({"leisure": "playground", "opening_hours": "24/7"}, "a 24/7 playground"),
                       ({"leisure": "playground", "opening_hours": "Mo-Su 00:00-24:00"},
                        "a playground open Mo-Su 00:00-24:00"),
                       ({"leisure": "playground", "name": "Cal Anderson Playground",
                         "wheelchair": "yes"}, "a playground with something to say")]:
        got = _written(tags)
        checks.append((got == (True, LA), f"{what} is a pin stored with its zone alone ({got})"))
    for tags, what in [({"social_facility": "food_bank"}, "a bare food bank (always a listing)"),
                       ({"leisure": "playground", "opening_hours": "Mo-Su 08:00-20:00"},
                        "a playground that shuts")]:
        got = _written(tags)
        checks.append((got[0] is False and isinstance(got[1], dict) and got[1].get("days"),
                       f"{what} is a listing and keeps its days for the roller ({got})"))

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

    import re as _re_mod
    _re_claim = _re_mod.compile(r"(node|way|relation)/\d+")
    # ---- one writer per centre ---------------------------------------------
    #
    # mapsee_ingest_facility_hours writes 70 community centres' rows (2026-10-05)
    # under THIS adapter's fingerprint, with the city's hours. Writing them here
    # too would flip each row between two sets of hours every Sunday.
    _cc = [{"type": "way", "id": 53589225, "lat": 47.66, "lon": -122.38,
            "tags": {"amenity": "community_centre", "name": "Green Lake Community Center"}}]
    _real = (A.sweep_tiles, A.CURSOR_PATH, A.CLAIMED_BY_FACILITY_HOURS)
    A.sweep_tiles = lambda cells, fetch_one, label, delay=2.0, sleep=None: (_cc, True)
    _cursor_dir = _tempfile.mkdtemp()
    A.CURSOR_PATH = _os.path.join(_cursor_dir, "cursor.json")
    try:
        got = {}
        for claimed in (set(), {"way/53589225"}):
            A.CLAIMED_BY_FACILITY_HOURS = claimed
            with _tempfile.TemporaryDirectory() as tmp:
                store = _os.path.join(tmp, "feeds.json")
                A.main(["--config", "osm_amenity_sources.json", "--store", store,
                        "--only", "Seattle", "--max-places", "50",
                        "--places-cache", _os.path.join(tmp, "cache"), "--ignore-cursor"])
                got[bool(claimed)] = len(_json2.load(open(store, encoding="utf-8")).get("events", []))
        checks.append((got == {False: 1, True: 0},
                       f"a community centre facility_hours claims is not written here, and an "
                       f"unclaimed one still is ({got})"))
    except Exception as exc:                                    # noqa: BLE001
        checks.append((False, f"the claimed-centre run raised {type(exc).__name__}: {exc}"))
    finally:
        A.sweep_tiles, A.CURSOR_PATH, A.CLAIMED_BY_FACILITY_HOURS = _real
        _shutil.rmtree(_cursor_dir, ignore_errors=True)
    checks.append((len(A.CLAIMED_BY_FACILITY_HOURS) >= 1
                   and all(_re_claim.fullmatch(r) for r in A.CLAIMED_BY_FACILITY_HOURS),
                   f"the live facility_hours config claims refs in to_event's own 'type/id' "
                   f"shape ({len(A.CLAIMED_BY_FACILITY_HOURS)})"))

    # ---- the fill held at a frontier, through main() -----------------------
    #
    # 2026-10-27: the first full turn of all 260 hubs inserted 465,774 rows in
    # five Sundays, and 94 dense hubs still held 415,471 candidates past their
    # cursor. --frontier stops each at its first F. The bug class this adapter
    # has is "it was in main(), and nothing ran main()", so every case below
    # runs the real main() with Overpass stubbed: ten fountains, ids 1-10 in
    # candidate order, so a row's id says which index it came from.
    import contextlib as _ctx, io as _io
    _ten = [{"type": "node", "id": i, "lat": 47.6 + i / 1000, "lon": -122.3,
             "tags": {"amenity": "drinking_water"}} for i in range(1, 11)]

    def _run(args, cursor=None, frontier=None, raw_frontier=None, elements=_ten):
        """(rc, ids written, cursor after, the area lines printed, error)."""
        real = (A.sweep_tiles, A.CURSOR_PATH)
        A.sweep_tiles = lambda cells, fetch_one, label, delay=2.0, sleep=None: (elements, True)
        out, rc, err = _io.StringIO(), None, None
        with _tempfile.TemporaryDirectory() as tmp:
            A.CURSOR_PATH = _os.path.join(tmp, "cursor.json")
            if cursor is not None:
                _json2.dump(cursor, open(A.CURSOR_PATH, "w", encoding="utf-8"))
            extra = []
            if frontier is not None or raw_frontier is not None:
                fp = _os.path.join(tmp, "frontier.json")
                open(fp, "w", encoding="utf-8").write(
                    raw_frontier if raw_frontier is not None else _json2.dumps(frontier))
                extra = ["--frontier", fp]
            store = _os.path.join(tmp, "feeds.json")
            try:
                with _ctx.redirect_stdout(out):
                    rc = A.main(["--config", "osm_amenity_sources.json", "--store", store,
                                 "--places-cache", _os.path.join(tmp, "cache")] + args + extra)
            except (Exception, SystemExit) as exc:          # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            finally:
                A.sweep_tiles, A.CURSOR_PATH = real
            ids = []
            if _os.path.exists(store):
                ids = sorted(int(src["source_id"].rsplit("/", 1)[1])
                             for r in _json2.load(open(store, encoding="utf-8")).get("events", [])
                             for src in r.get("sources") or [])
            cur = A.load_cursor(_os.path.join(tmp, "cursor.json"))
        return rc, ids, cur, out.getvalue().splitlines(), err

    def _area_lines(lines, name="Seattle"):
        return [l for l in lines if l.startswith(f"[osm-amenity] {name}:")]

    seattle4 = ["--only", "Seattle", "--max-places", "4"]
    # THE CONTROL: without a frontier, the walk from cursor 4 reaches 5-8. If
    # it did not, nothing below could tell a hold from a fixture that never
    # reached past index 4.
    rc, ids, cur, lines, err = _run(seattle4, cursor={"Seattle": 4})
    checks.append((ids == [5, 6, 7, 8] and cur.get("Seattle") == 8,
                   f"control: unheld, cursor 4 examines ids 5-8 and saves 8 ({ids}, {cur}, {err})"))

    # (a) F < N. The production shape: F = --max-places = the cursor, so the
    # held walk wraps inside [0, F) and saves 0.
    rc, ids, cur, lines, err = _run(seattle4, cursor={"Seattle": 4}, frontier={"Seattle": 4})
    checks.append((rc == 0 and ids == [1, 2, 3, 4] and cur.get("Seattle") == 0,
                   f"(a) held at F=4 of 10, cursor 4: re-reads ids 1-4 (index < F) and saves "
                   f"cursor 0 < F ({ids}, {cur}, {err})"))
    checks.append(("[osm-amenity] Seattle: frontier held at 4 of 10 candidate(s)" in lines,
                   f"(a) ...and says so per area ({_area_lines(lines)})"))
    checks.append((any(l.startswith("[osm-amenity] Seattle: 10 candidate(s),") for l in lines),
                   "(a) ...while the summary still counts the FULL list, so the logs measure "
                   "what a lift would resume"))
    rc, ids, cur, lines, err = _run(["--only", "Seattle", "--max-places", "3"],
                                    cursor={"Seattle": 3}, frontier={"Seattle": 4})
    checks.append((ids == [1, 2, 4] and cur.get("Seattle") == 2,
                   f"(a) a smaller window walks modulo the HELD length: from 3 it takes 4,1,2 and "
                   f"saves 2, never id 5+ ({ids}, {cur}, {err})"))

    # (b) F >= N, or the area not in the file: today's run, byte for byte —
    # the same rows, the same cursor, the same per-area lines.
    today = _run(seattle4, cursor={"Seattle": 4})
    for label, front in (("F == N", {"Seattle": 10}), ("F > N", {"Seattle": 50}),
                         ("area not in the file", {"Portland": 4, "_readme": ["x"]})):
        got = _run(seattle4, cursor={"Seattle": 4}, frontier=front)
        checks.append((got[4] is None and got[1:3] == today[1:3]
                       and _area_lines(got[3]) == _area_lines(today[3]),
                       f"(b) {label}: identical to an unheld run ({got[1]}, {got[2]}, {got[4]})"))

    # (c) a missing or broken file is today's behaviour, SAID ALOUD — a hold
    # that silently stopped holding reads exactly like a week it worked.
    for label, kw in (("missing", {"frontier": None}), ("not JSON", {"raw_frontier": "{nope"}),
                      ("not an object", {"raw_frontier": "[4000]"})):
        if label == "missing":
            got = _run(seattle4 + ["--frontier", "/nonexistent/osm_amenity_frontier.json"],
                       cursor={"Seattle": 4})
        else:
            got = _run(seattle4, cursor={"Seattle": 4}, **kw)
        checks.append((got[0] == 0 and got[4] is None and got[1:3] == today[1:3]
                       and any("NO area is held" in l for l in got[3]),
                       f"(c) frontier file {label}: no crash, today's run, and it says no area "
                       f"is held ({got[1]}, {got[4]})"))
    got = _run(seattle4, cursor={"Seattle": 4}, frontier={"Seattle": "4000"})
    checks.append((got[1:3] == today[1:3]
                   and any("Seattle" in l and "NOT held" in l for l in got[3]),
                   f"(c) an F that is not a positive count holds nothing, out loud ({got[1]})"))

    # (d) --kinds: F is a position in the FULL list and means nothing in a
    # filtered one, so the frontier is not applied, and the log says so.
    kinds = seattle4 + ["--kinds", "amenity=drinking_water"]
    plain = _run(kinds, cursor={"Seattle": 4})
    got = _run(kinds, cursor={"Seattle": 4}, frontier={"Seattle": 4})
    checks.append((got[4] is None and got[1:3] == plain[1:3] == ([5, 6, 7, 8], {"Seattle": 8}),
                   f"(d) under --kinds the frontier is ignored: ids 5-8, cursor 8 ({got[1]}, {got[2]})"))
    checks.append((any("IGNORED" in l and "--kinds" in l for l in got[3])
                   and not any("frontier held" in l for l in got[3]),
                   "(d) ...and the log says it was ignored"))

    # (e) --only is EXACT. A substring sent the Reno job to Grenoble too
    # (2026-10-25, job 121138829093: 4,494 rows prepared for a metro of 718
    # candidates), and four more pairs in the live config did the same.
    for short, long_ in (("Reno", "Grenoble"), ("Portland", "Portland ME"),
                         ("Birmingham", "Birmingham AL"), ("Newcastle", "Newcastle upon Tyne"),
                         ("Porto", "Porto Alegre")):
        got = _run(["--only", short, "--max-places", "4"])
        pulled = sorted({l.split(":", 1)[0][len("[osm-amenity] "):] for l in got[3]
                         if _re_mod.match(r"\[osm-amenity\] [^:]+: \d+ candidate\(s\),", l)})
        checks.append((pulled == [short],
                       f"(e) --only {short} pulls {short} alone, not {long_} as well ({pulled}, {got[4]})"))
    got = _run(["--only", "reno", "--max-places", "4"])
    pulled = sorted(l.split(":", 1)[0][len("[osm-amenity] "):] for l in got[3]
                    if _re_mod.match(r"\[osm-amenity\] [^:]+: \d+ candidate\(s\),", l))
    checks.append((pulled == ["Reno"], f"(e) ...case-insensitively: --only reno is Reno ({pulled})"))
    got = _run(["--only", "Gren", "--max-places", "4"])
    checks.append((got[4] is not None and got[4].startswith("SystemExit") and got[1] == [],
                   f"(e) a name that matches no area is refused, not a silent empty run ({got[4]})"))

    # THE COMMITTED FILE. Only a hub whose cursor has ONLY EVER ADVANCED may be
    # held: one that wrapped has stored rows past any F, which a frontier
    # would freeze (no OSM edit reaching them again). These nine are the ones
    # the history disqualifies — seven wrapped in osm_amenity_cursor.json,
    # Washington DC was read whole by the 2026-09-22 dispatches, and Grenoble
    # by the Reno double pull — and the file's `_left_out` names each.
    _cfg_names = {a["name"] for a in _json2.load(open("osm_amenity_sources.json",
                                                      encoding="utf-8"))["areas"]}
    try:
        _blob = _json2.load(open("osm_amenity_frontier.json", encoding="utf-8"))
        with _ctx.redirect_stdout(_io.StringIO()):
            _held = A.load_frontier("osm_amenity_frontier.json")
    except Exception as exc:                                    # noqa: BLE001
        _blob, _held = {}, {}
        checks.append((False, f"osm_amenity_frontier.json could not be read by the ingest: "
                              f"{type(exc).__name__}: {exc}"))
    _never = {"Paris", "London", "Berlin", "New York", "Seattle", "Chicago", "Portland",
              "Washington DC", "Grenoble"}
    checks.append((len(_held) >= 1 and set(_held) <= _cfg_names
                   and len(_held) == len([k for k in _blob if not k.startswith("_")]),
                   f"osm_amenity_frontier.json: {len(_held)} held hub(s), every one a config "
                   f"area name and a positive F"))
    checks.append((not (set(_held) & _never) and _never <= set(_blob.get("_left_out") or {}),
                   f"...and no hub whose walk ever came back round is held "
                   f"({sorted(set(_held) & _never)})"))

    # ---- a private pool is somebody's garden ------------------------------
    #
    # `leisure=swimming_pool` is the only selector here that is not civic by
    # definition: most pools on earth are behind a house or a hotel. The
    # Overpass query narrows on `access`, and these check the SECOND guard, the
    # one in Python, because a query filter is a request and the answer is
    # somebody else's. Getting it wrong puts a walking route to a stranger's
    # garden on a public map.
    pool = [k for k in A.KINDS if k.value == "swimming_pool"][0]
    checks.append((bool(pool.extra) and "access" in pool.extra,
                   "the pool query narrows on access before Overpass answers"))
    for acc in ("yes", "public", "permissive"):
        checks.append((ev({"leisure": "swimming_pool", "access": acc, "name": "Lido"}) is not None,
                       f"a pool with access={acc} is a public pool"))
    for acc in ("private", "customers", "permit", "no"):
        checks.append((ev({"leisure": "swimming_pool", "access": acc, "name": "Lido"}) is None,
                       f"a pool with access={acc} is refused"))
    checks.append((ev({"leisure": "swimming_pool", "name": "Lido"}) is None,
                   "...and a pool with NO access tag is refused rather than assumed public"))

    # The two properties the Kind exists for, restated where they can regress.
    open_pool = ev({"leisure": "swimming_pool", "access": "yes", "name": "Ballard Pool"})
    checks.append((open_pool is not None and open_pool.pin_only is False,
                   "a public pool is a LISTING even with no hours - where one is IS the fact"))
    checks.append((open_pool is not None and open_pool.recurring_days == A.ALWAYS,
                   "an untagged pool claims no hours of its own; the all-week window is the "
                   "roller's and the body says the hours are unknown"))
    timed = ev({"leisure": "swimming_pool", "access": "yes", "name": "Ballard Pool",
                "opening_hours": "Mo-Fr 06:00-21:00"})
    checks.append((timed is not None and timed.recurring_days not in (None, A.ALWAYS),
                   "...and real hours become a real weekly pattern"))

    # ---- four buildings: a place somebody GOES, not furniture ---------------
    #
    # Added 2026-09-21 (community centre, library, marketplace, dog park). The
    # first three list without hours for the pool's reason — where one is IS
    # the fact — and shut for the food bank's; the dog park is a playground
    # for dogs and stays furniture. Measured in the 35-mile DC box the day
    # they were added: 576 community centres, 295 libraries, 110 markets,
    # 334 dog parks, and 43 / 180 / 61 / 11 of those carrying opening_hours.
    for tags, cat in (({"amenity": "community_centre", "name": "Hillcrest Community Centre"}, "community"),
                      ({"amenity": "library", "name": "Southwest Library"}, "learning"),
                      ({"amenity": "marketplace", "name": "Eastern Market"}, "market")):
        e = ev(tags)
        checks.append((e is not None and e.pin_only is False and e.category == cat,
                       f"a bare named {tags['amenity']} is a LISTING under `{cat}`"))
        checks.append((e is not None and e.recurring_days == A.ALWAYS
                       and "Opening times are not listed" in e.description,
                       f"...claims no hours of its own and says so in the body"))
    timed_cc = ev({"amenity": "community_centre", "name": "Hillcrest Community Centre",
                   "opening_hours": "Mo-Fr 09:00-16:00"})
    checks.append((timed_cc is not None and timed_cc.recurring_days not in (None, A.ALWAYS)
                   and "🕑 Open: Mon-Fri 09:00-16:00" in timed_cc.description,
                   "a community centre's real hours become a real weekly pattern"))
    youth = ev({"amenity": "community_centre", "name": "The Hub", "community_centre": "youth_centre"})
    checks.append((youth is not None and "🏘 Youth centre" in youth.description,
                   "what SORT of centre prints — a youth centre is not a village hall"))
    checks.append((ev({"amenity": "community_centre", "name": "FC Sportheim",
                       "community_centre": "club_home"}) is None,
                   "a club house (community_centre=club_home) is a members' building and is refused"))
    for tags in ({"amenity": "community_centre", "name": "Staff Hall"},
                 {"amenity": "library", "name": "Firm Library"},
                 {"amenity": "marketplace", "name": "Trade Hall"},
                 {"leisure": "dog_park", "name": "Residents' Dog Run"}):
        k = A.kind_of(tags)
        checks.append((bool(k.extra) and "access" in k.extra and k.refuse is not None,
                       f"{k.slug}'s query refuses private access, and Python re-checks it"))
        checks.append((ev(dict(tags, access="private")) is None
                       and ev(dict(tags, access="customers")) is None,
                       f"...so a private {k.noun} is refused"))
        checks.append((ev(tags) is not None,
                       f"...and one with NO access tag is allowed — unlike a pool, a bare {k.noun} is public"))
    dog = ev({"leisure": "dog_park", "name": "Glencarlyn Dog Park"})
    checks.append((dog is not None and dog.pin_only is True and dog.recurring_days == A.ALWAYS,
                   "a bare dog park is FURNITURE, open all hours, like a playground"))
    fenced = ev({"leisure": "dog_park", "name": "Glencarlyn Dog Park", "fenced": "yes"})
    checks.append((fenced is not None and fenced.pin_only is True and "🐕 Fenced" in fenced.description,
                   "...a fenced one has something to say when tapped and is still furniture"))

    # ---- the glyph must actually REACH the row ----------------------------
    #
    # Kind.glyph was assigned when this adapter was written and read by nothing
    # for its whole life: NormalizedEvent had no icon field, and the sync
    # hard-coded `"icon": None` with the note "let the app render the
    # CATEGORY's emoji". That is right for every other adapter — one music
    # event should look like another — and wrong for this one, because three
    # selectors share `outdoors`. Reported from the map: drinking fountains,
    # public toilets and bike repair stands were all 🚰, so nothing could tell
    # you which corner had a lavatory on it.
    #
    # Nothing could have caught it here either. Every case above reads a
    # description or a pin_only; none had ever looked at what the pin is DRAWN
    # with.
    glyphs = {}
    for k in A.KINDS:
        # A Kind with `public_only` needs the tag that makes it eligible, or the
        # probe builds the very row the guard exists to refuse and this loop
        # reports a missing glyph for a row that correctly does not exist.
        probe = {k.key: k.value, "name": f"probe {k.value}"}
        if getattr(k, "public_only", False):
            probe["access"] = "yes"
        e = ev(probe)
        got = getattr(e, "icon", None) if e is not None else None
        checks.append((got == k.glyph,
                       f"{k.key}={k.value} is drawn with {k.glyph} (icon={got!r})"))
        glyphs.setdefault(k.glyph, []).append(f"{k.key}={k.value}")
    clashes = {g: ks for g, ks in glyphs.items() if len(ks) > 1}
    checks.append((not clashes,
                   f"no two kinds share a glyph{'' if not clashes else f' — {clashes}'}"))

    # THE REPORTED BUG, named. These three are one CATEGORY and three THINGS,
    # which is the whole reason a category emoji is not enough here.
    outdoors = [k for k in A.KINDS if k.category == "outdoors"]
    checks.append((len(outdoors) >= 3,
                   f"`outdoors` still holds several kinds ({len(outdoors)}) — "
                   "the condition that made a category glyph ambiguous"))
    seen = [getattr(ev({k.key: k.value}), "icon", None) for k in outdoors]
    checks.append((len(set(seen)) == len(seen),
                   f"...and each draws its own glyph: {' '.join(str(x) for x in seen)}"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
