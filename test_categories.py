"""Does the ingest actually route movement events to wegosie, without stealing
anything from the other lenses? Real-shaped titles, including the collisions the
regexes were written to survive."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mapsee_supabase_sync import derive_categories

WEGOSIE = {"running", "sports", "fitness"}   # matches lens.js: outdoors deliberately excluded

# (name, source category, description, expect_primary, expect_in_wegosie)
CASES = [
    # --- "workout" is a live metaphor, and the classifier kept believing it.
    # A glass-fusing craft class opens "Your weekly creative workout starts
    # here!" and was promoted to fitness on that phrase alone. The guard is a
    # short list of MODIFIERS, not an attempt to understand the sentence:
    # "creative/mental workout" is figurative in every listing that uses it,
    # "morning workout" is not.
    ("Scrap to Sparkle: Glass Shards",   "learning",
     "Your weekly creative workout starts here! Turn scraps into fused glass.", "learning", False),
    ("Book Club",                        "community",
     "A workout for the mind, every Tuesday.", "community", False),
    ("Morning Session",                  "community",
     "Join us for a morning workout in the park.", "fitness", True),

    # A Meetup GROUP slug is the name of a group, not a claim about this event.
    ("Community Potluck",                "community",
     "Tickets / info: https://www.meetup.com/seattle-volunteer-crew/events/1", "community", False),
    ("Beach Cleanup Day",                "community",
     "Join our volunteer crew for a beach clean.", "volunteer", False),

    # --- FOOD is a polluted bucket, not a deliberate classification (2026-08-12)
    # A user screenshot showed "Gentle Morning Hatha Yoga" rendered as Food &
    # Drink. Of 1,000 upcoming food-classified events only 16% had a food word in
    # the title; yoga, pilates, tai chi, zumba and karate were all sitting there,
    # because Meetup tags an event with whichever sweep found it.
    ("Gentle Morning Hatha Yoga",         "food",      "", "fitness", True),
    ("Restorative Yoga",                  "food",      "", "fitness", True),
    ("Aktiv! Pilates",                    "food",      "", "fitness", True),
    ("Traditional Shorin Ryu Karate",     "food",      "", "fitness", True),
    ("Zumba Dance Fitness",               "food",      "", "fitness", True),
    # …but a real food event with no movement word in the TITLE stays food.
    ("Taco Tuesday at the Brewery",       "food",      "", "food",    False),
    ("Sunday Farmers Market Brunch",      "food",      "", "food",    False),

    # --- URLs are not evidence. The strong rule may read the description, and
    # descriptions carry links this pipeline WRITES — the Tickets / info line and
    # our own "More on this show" Google search. Both put arbitrary words in
    # front of the classifier. Live examples, both previously misrouted:
    ("Breathing Ecstasy: Tantric Breathing", "community",
     "🔎 More on this show: https://www.google.com/search?q=Yoga%20Society%20Of%20San%20Francisco",
     "community", False),
    ("Glass Fusing Workshop",             "learning",
     "Tickets / info: https://www.meetup.com/san-francisco-yoga-karate-writing-meetup-group/events/1",
     "learning", False),
    # a REAL yoga description still promotes — the words just have to be prose
    ("Stretch & Recharge in the Park",    "community",
     "Refresh your body and mind with a gentle lunchtime yoga session.", "fitness", True),

    # --- the whole point: these used to be invisible to a movement lens
    ("Community Yoga in the Park",        "community", "", "fitness", True),
    ("Sunrise Vinyasa Flow",              "other",     "", "fitness", True),
    ("Pilates for Beginners",             "learning",  "", "fitness", True),
    ("Saturday Morning Bootcamp",         "learning",  "", "fitness", True),
    ("Beginners Climbing Night",          "community", "", "fitness", True),
    ("Kickboxing Class",                  "other",     "", "fitness", True),
    ("Tai Chi in the Square",             "community", "", "fitness", True),
    ("Free HIIT Session",                 "other",     "", "fitness", True),

    # --- outdoors KEEPS its pin, reaches wegosie as a secondary
    ("Group Hike to Rattlesnake Ledge",   "outdoors",  "", "outdoors", True),
    ("Ski Trip to Crystal Mountain",      "outdoors",  "", "outdoors", True),
    ("Kayak Paddle on Lake Union",        "outdoors",  "", "outdoors", True),
    ("Snowshoe Day Out",                  "outdoors",  "", "outdoors", True),

    # --- already-good categories are untouched
    ("Saturday Parkrun 5k",               "running",   "", "running",  True),
    ("Sunday League Volleyball",          "sports",    "", "sports",   True),

    # --- COLLISIONS: none of these may become fitness
    ("Boxing Day Ceilidh",                "music",     "", "music",    False),
    ("Boxing Day Sale",                   "market",    "", "market",   False),
    ("Capitol Hill Art Walk",             "arts",      "", "arts",     False),
    ("Gallery Walk & Wine",               "arts",      "", "arts",     False),
    ("Intro to Watercolour Workshop",     "learning",  "", "learning", False),
    ("Spin the Bottle Comedy Night",      "other",     "", "theater",  False),
    ("The Rowing Club (live band)",       "music",     "", "music",    False),
    ("Trail Work Party — Volunteers",     "outdoors",  "", "volunteer",False),
    ("Kids Karate Storytime",             "community", "", "kids",     True),   # karate is movement
    ("Farmers Market",                    "market",    "", "market",   False),
    ("Taco Crawl & Happy Hour",           "food",      "", "party",    False),

    # --- FOOD BANKS are civic, not hospitality. The 14 food-bank calendars in
    # ics_sources.json used to carry category "food", and 'food' is deliberately
    # NOT in _PROMOTABLE_TO_VOLUNTEER (a restaurant listing is not a shift) — so
    # a repack-room shift stayed 'food', showed up on bar.ventures and
    # oneday.cafe, and never reached the volunteer layer or awaresie.com. They
    # are tagged "community" now; these three pin that convention down.
    ("Volunteer Shift: Repack Room",      "community", "", "volunteer", False),
    ("Mobile Food Bank Distribution",     "community", "", "volunteer", False),
    ("Empty Bowls Fundraiser Dinner",     "community", "", "community", False),
]

fails = []
for name, cat, desc, want_primary, want_wegosie in CASES:
    rec = {"name": name, "category": cat, "description": desc}
    primary, extras = derive_categories(rec)
    allc = {primary} | set(extras or [])
    in_wegosie = bool(allc & WEGOSIE)
    ok = (primary == want_primary) and (in_wegosie == want_wegosie)
    if not ok:
        fails.append((name, primary, extras, want_primary, want_wegosie, in_wegosie))
    print(f"{'ok ' if ok else 'FAIL'} {name[:38]:<40} -> {primary:<10} + {extras or []}"
          f"{'' if ok else f'   (wanted {want_primary}, wegosie={want_wegosie} got {in_wegosie})'}")

print()
print(f"{len(CASES)-len(fails)}/{len(CASES)} passed")

# ---------------------------------------------------------------------------
# Keyword-derived keys vs. content. Meetup tags an event with whichever search
# term found it, so its key can be plain wrong — a park workout arrived as
# 'party' because the "dance" sweep matched it. These check that strong exercise
# evidence overrides that, WITHOUT letting it steal genuine nightlife.
# (name, source category, description, want_primary, must_include, must_exclude)
PUMP = ("Let's workout together and get strong! This is a group with no formal exercise "
        "expert, but I will demo a few different exercises that I like to do. Lets l skill "
        "share and learn from one another! To start, I'll have stations set up in Jimmy "
        "Hendrix Park, and we will rotate through them, giving around 2 minutes for each station.")

CONTENT_CASES = [
    # the reported listing: meetup.com/pump-up-a-jam/events/315847839
    ("Pump Up... A Jam", "party", PUMP, "fitness", {"community"}, {"party"}),
    # a title that says nothing, with the evidence in the blurb
    ("Tuesday Morning Meetup", "community", "Bootcamp in the park, all levels welcome.",
     "fitness", {"community"}, set()),
    # genuine nightlife found by the same sweep must stay put
    ("Friday Night Dance Party", "party", "DJs till late, cocktails at the bar.",
     "party", set(), {"fitness"}),
    ("Silent Disco Warehouse Party", "party", "Three channels, one dancefloor.",
     "party", set(), {"fitness"}),
    # a DELIBERATE key is real classification and is never overridden
    ("Yoga-Themed Album Launch", "music", "Live set, then a workout playlist.",
     "music", set(), {"fitness"}),
    ("Sunday League Football", "sports", "Weekly workout for the squad.", "sports", set(), set()),
    # the general demoted-key rule must survive the exception above
    ("Taco Crawl & Happy Hour", "food", "Food trucks all evening.", "party", {"food"}, set()),
    # "community" is inclusive LANGUAGE, not a word that appears everywhere
    ("Farmers Market", "market", "Local produce and a coffee cart.", "market", set(), {"community"}),
    ("Jazz Night", "music", "Our house trio plays two sets.", "music", set(), {"community"}),

    # --- SECOND-HAND reaches fleabop. The market secondary asked only for
    # English and only for the shopping words, so of ~230 upcoming events whose
    # titles name second-hand retail, 15 reached the lens. The lens is called
    # "Flea Markets, Clothing Swaps and Vintage Near You" and had 2 flea markets
    # and 0 thrift, vintage, swap or antique in 3,469 events.
    #
    # These are all real live titles. The loanwords matter as much as the
    # English: Flohmarkt COMPOUNDS, so it has to match as a suffix.
    ("Flohmarkt am Arkonaplatz", "community", "Jeden Sonntag.", "community", {"market"}, set()),
    ("Garagenflohmarkt fur den guten Zweck", "volunteer", "", "volunteer", {"market"}, set()),
    ("Vide-grenier de la Croix-Rousse", "community", "", "community", {"market"}, set()),
    ("Brocante de Printemps", "community", "", "community", {"market"}, set()),
    ("Fashion Thrift Society Sydney", "arts", "Pre-loved fashion.", "arts", {"market"}, set()),
    ("Bermondsey Car Boot Sale", "community", "", "community", {"market"}, set()),
    ("Repair Cafe Islington", "community", "Bring something broken.", "community", {"market"}, set()),
    ("Community Clothing Swap", "community", "", "community", {"market"}, set()),
    ("Dallas Thrift & Vintage Shopping Tour", "outdoors", "", "outdoors", {"market"}, set()),

    # ...and the words it must NOT take. "Vintage" is a band name and a tour
    # name far more often than it is a market, and "thrifty" is alliteration.
    ("Vintage Vinyl Live", "music", "A night of soul 45s.", "music", set(), {"market"}),
    ("The Vintage Explosion", "music", "Glasgow's finest.", "music", set(), {"market"}),
    ("Postmodern Jukebox: The Future Is Vintage", "music", "World tour.",
     "music", set(), {"market"}),
    ("Tuesdays Thrifty Theater Night", "theater", "Cheap seats.", "theater", set(), {"market"}),
    # A pub quiz named after a song about a thrift shop. The one false positive
    # in 138 second-hand matches, and the shape recurs whenever a venue names a
    # night after a lyric.
    ("Monday Night Trivia: Thrift Shop Bull", "party", "Quiz from 8.",
     "party", set(), {"market"}),

    # --- BRUNCH reaches oneday.cafe. It was the commonest food word on the map
    # and the food secondary did not have it: 615 upcoming events, 158 reaching
    # the lens, 457 sitting on community, theater and music. oneday is the
    # second-thinnest lens and food is its ONLY category.
    #
    # The point of these three is that the PRIMARY must not move. A drag brunch
    # is theatre AND a meal, and the secondaries column exists precisely so it
    # can be both — a secondary that re-keyed the event would change its pin.
    ("Golden Girls Drag Brunch", "theater", "", "theater", {"food"}, set()),
    ("Gospel Brunch: The Moriah Sisters", "music", "", "music", {"food"}, set()),
    ("SF Brunch Social: Make New Friends", "community", "", "community", {"food"}, set()),
    # A tap takeover really is nightlife, so _PARTY_RX re-keys it and that is
    # correct — the food SECONDARY is what carries it to oneday as well, which
    # is the whole point: one row, bar.ventures and oneday.cafe both.
    ("Tap Takeover at Mt. Airy Taproom", "community", "", "party", {"food"}, set()),
    ("Distillery Tour and Tasting", "community", "", "community", {"food"}, set()),
]

cfails = []


# ---------------------------------------------------------------------------
# A fuzzy search's keyword is not a classification
# ---------------------------------------------------------------------------
# Meetup's eventSearch is not a phrase match. The adapter sweeps "farmers
# market" / "night market" and files everything it gets back under `market`,
# because normally the keyword that found an event is a decent guess at its
# layer. For `market` it is not: "market" is a business word first. Measured
# 2026-08-16, every `market` event in Berlin was a Meetup row and NONE was a
# market — three stand-up nights, a Magic: the Gathering league, a run club, an
# e-commerce breakfast, a homebuyers' meetup and a meditation. That was
# fleabop's entire supply in the city.
#
# The demotion is gated on PROVENANCE, not just text, and that is the whole
# design: "Randolph Street Market" does not match a market regex either, so a
# text-only rule would throw away the real supply to fix the fake.
from mapsee_supabase_sync import map_category   # noqa: E402


def _rec(name, desc="", cat="market", src="meetup"):
    return {"name": name, "title": name, "description": desc, "category": cat,
            "sources": [{"source": src, "source_id": "1", "url": "https://example.org/e"}]}


# ---------------------------------------------------------------------------
# The library is where `kids` supply comes from
# ---------------------------------------------------------------------------
# All 58 library feeds in ics_sources.json are filed `learning`, correctly — so
# _KIDS_RX is the only thing that gives the kids layer any supply, and it was
# missing a third of it. Measured over 1,347 distinct live titles from eight
# public library feeds: 124 promoted, 132 more were plainly children's or teen
# events that did not. Every case below is a real title from that set.
KIDS_CASES = [
    # (title, want_primary)
    ("Teen Book Club", "kids"),                 # teen/tween was absent entirely
    ("Tween Crafternoon", "kids"),
    ("Dungeons & Dragons for Tweens/Teens", "kids"),
    ("LEGO in the Library", "kids"),            # the programme is named for the brick
    ("Lego Free Play", "kids"),
    ("Baby Lap Sit", "kids"),                   # baby+(time|rhyme|song) was too narrow
    ("Bouncing Babies", "kids"),
    ("Read to the Dog", "kids"),                # a staple that matched nothing
    ("Read to a Therapy Dog", "kids"),
    ("Pokémon Club", "kids"),
    ("Homeschool Robotics", "kids"),
    ("Dino Days- Family STEAM", "kids"),
    ("LEGO in the Library (ages 4-18)", "kids"),   # an age range IS the signal
    ("Homeschool Exploratorium K-2", "kids"),
    # …and what it must NOT take. Libraries run the SAME programme for adults
    # and say so in the title; "Adult LEGO® Club" is a real listing.
    ("Adult LEGO® Club", "learning"),
    ("Adult Craft Hour: Decorate Bags", "learning"),
    ("Seniors Tech Help", "learning"),
    # the volunteer rule runs first and must keep winning
    ("Teen Volunteer Corps at Central Library", "volunteer"),
    # THE TWO AGE GATES IN THE GUARD COULD NEVER FIRE. `18\+` and `21\+` sat
    # inside a trailing `\b`, and `+` is not a word character — so the boundary
    # demanded a letter or digit straight after the plus sign, which an age gate
    # never has. Every one of these returned no match before 2026-09-13.
    ("Teen Night 18+", "learning"),
    ("Anime Club 21+", "learning"),
    ("Pokémon Tournament (18+)", "learning"),
    # Dating is the adults' version of a listing whose vocabulary reads young.
    ("Speed Dating for Anime Fans", "learning"),
    ("Singles Night: Board Games", "learning"),
    # …but SCOPED, because "dating" alone is a real teen-services topic.
    ("Teen Dating Violence Awareness Workshop", "kids"),
]

kfails = []
for title, want in KIDS_CASES:
    got = map_category({"name": title, "title": title, "description": "",
                        "category": "learning",
                        "sources": [{"source": "ics", "source_id": "1"}]})
    ok = got == want
    if not ok:
        kfails.append(title)
    print(f"{'ok ' if ok else 'FAIL'} {title[:46]:<48} -> {got}"
          f"{'' if ok else f'   (wanted {want})'}")
print()
print(f"{len(KIDS_CASES)-len(kfails)}/{len(KIDS_CASES)} passed")
cfails += kfails


# ---------------------------------------------------------------------------
# An adult age bracket is not a children's age range
# ---------------------------------------------------------------------------
# Reported from production: "Seattle Gay Online Speed Dating" was on plansie's
# kids layer. Nothing in its TITLE goes near _KIDS_RX — it arrived as a
# SECONDARY, off the age brackets its blurb enumerates ("Ages 18-32", "Ages
# 30-46"), because the age-range alternative was written for a library title
# ("ages 4-18", "grades K-2") and the secondary path also reads descriptions.
#
# Measured 2026-09-13 over 2,000 live event pages sampled from mapsee.me's own
# sitemaps: _KIDS_RX fired on 42, the age range was what fired on 18, and 18 of
# those 18 had a low end of 18 or more — every one a dating listing, none a
# children's programme. Bounding the low end below 18 removed all 18 and cost
# nothing, because a library writes "ages 4-18" and never "ages 30-46".
#
# The guard is the backstop for the next one, and it is DELIBERATELY not the
# title guard. Applying that to descriptions would have withheld the layer from
# "Homeschool Days", whose blurb prices "Students (4 – 18): $15" beside "Adults
# (19 & older): $22.50" — a price table is not an age policy.
from mapsee_supabase_sync import derive_categories as _derive   # noqa: E402

SPEED_DATING = ("**💕 Seattle Gay Virtual Speed Dating on Zoom** Join from home and meet real "
                "Seattle gay singles in guided one-on-one Zoom rounds. Matched by age and "
                "personality. **Register below for your age group:** - **Ages 18-32** - "
                "**Ages 30-46** - **Ages 40-58** - **Ages 55+**")

AGE_CASES = [
    # (title, description, base, must_NOT_be_kids)
    ("Seattle Gay Online Speed Dating", SPEED_DATING, "community", True),
    ("San Gabriel Valley Speed Dating (Ages 28-40)", "", "community", True),
    ("Houston Fun and Casual Character Matched Dating", "Ages 18-32 · Ages 30-46", "community", True),
    ("Trivia Night", "Open to everyone, ages 21-65.", "community", True),
    # …and the ranges a library actually writes must still reach the layer.
    ("Craft Hour", "A drop-in session for ages 4-18.", "learning", False),
    ("Robotics Drop-In", "Open to ages 10-14.", "learning", False),
    ("Story Explorers", "For ages 0-5 and their grown-ups.", "learning", False),
    ("After School Club", "Grades K-5 welcome.", "learning", False),
    ("Teen Zone", "Ages 12-18.", "learning", False),
    # The age range is not the only way in, so the guard has to hold on its own.
    ("Pokémon TCG League", "Adults only — 21+. Bring your deck.", "community", True),
    ("LEGO Night", "An 18+ evening of building and beer.", "community", True),
    # …and must NOT fire on a kids' event that merely mentions adults.
    ("Homeschool Days", "Special Homeschool Days Cave Walk Pricing Students (4 – 18): $15 "
                        "Adults (19 & older): $22.50", "community", False),
    ("Family Fun Swim", "Children under 8 must be accompanied by an adult.", "community", False),
    # "infant" is a word people use ABOUT a person, not a statement about who an
    # event is for. Bare, it matched 3 times across the same 2,000 live pages
    # and all 3 were wrong; both survivors are real rows from that sample.
    ("Red Cross CPR and First Aid (Blended Learning)",
     "Learn to recognize and respond to adult, infant and child victims in various "
     "emergency situations.", "community", True),
    ("Beloved by Toni Morrison (1987)",
     "The true story of an enslaved woman who killed her infant daughter to spare her "
     "a future of enslavement.", "community", True),
    # …while the sessions a library actually runs for babies still reach kids.
    ("Infant Massage", "", "learning", False),
    ("Newborn Group", "", "learning", False),
    ("Infants & Toddlers Drop-In", "", "learning", False),
]

afails = []
for title, desc, base, must_not in AGE_CASES:
    p, e = _derive({"name": title, "title": title, "description": desc, "category": base,
                    "sources": [{"source": "meetup", "source_id": "1"}]})
    cats = {p} | set(e or [])
    ok = ("kids" not in cats) if must_not else ("kids" in cats)
    if not ok:
        afails.append(title)
    print(f"{'ok ' if ok else 'FAIL'} {title[:44]:<46} -> {p} + {sorted(e or [])}"
          f"{'' if ok else ('   (kids must NOT be here)' if must_not else '   (kids is missing)')}")
print()
print(f"{len(AGE_CASES)-len(afails)}/{len(AGE_CASES)} passed")
cfails += afails


# ---------------------------------------------------------------------------
# Local live music reaches vivosie
# ---------------------------------------------------------------------------
# vivosie is "Live Music Near You Tonight" and the free outdoor town concert is
# the most characteristic thing it could open onto. The music SECONDARY wanted
# the exact phrase "concert series", so "Concert in the Park - Radio Replay"
# reached no music layer and "Summer Concert Series" did. Measured 2026-09-13
# over 3,160 DISTINCT live titles from 335 civic community calendars: 63 titles
# are about music and the rule caught 12; after widening, 105 match and every
# title below is a real one from that corpus.
#
# The NEGATIVES are the reason the rule is qualified rather than a list of bare
# words: "Jr. Jazz" is youth BASKETBALL (6 of the 10 bare `jazz` hits), "Trolls
# Band Together" is a film, and "Music Together" is a toddler class.
MUSIC_CASES = [
    # (title, base category, must music be among the categories?)
    ("Concert in the Park - Radio Replay", "community", True),
    ("Concerts in The Park", "community", True),
    ("Concert in the Park: MARIACHI COACHELLA", "community", True),
    ("Oktoberfest Concert: GB Leighton", "community", True),
    ("Bartlett Community Concert Band @ BPACC", "community", True),
    ("Performance in the Park - Capri Big Band", "community", True),
    ("Rumours: The Ultimate Fleetwood Mac Tribute Band", "community", True),
    ("St. Joseph Symphony \"Remembering Beethoven\"", "community", True),
    ("Symphonic Sinatra", "community", True),
    ("Orlando Jazz Orchestra", "community", True),
    ("Live Jazz Series with the Jesse Taitt Trio", "community", True),
    ("Jazz at MOCA", "community", True),
    ("Levitt AMP Dothan Music Series - Johnny Mullenax", "community", True),
    ("Music on the Trail", "community", True),
    ("ARRIVAL From Sweden: The Music of ABBA", "community", True),
    # ...and what it must NOT take.
    ("Girls Jr. Jazz Basketball Registration", "community", False),
    ("Jr. Jazz High School Basketball Registration Ends", "community", False),
    ("Hip Hop Jazz Camp Begins", "community", False),
    ("Movie in the Park: TROLLS BAND TOGETHER (PG)", "community", False),
    ("Music Together | Fall", "community", False),
    ("Music and Movement", "community", False),
    ("Family Music Bingo", "community", False),
    ("1 p.m. Monday Matinee \"Bill & Ted Face the Music\" PG-13", "community", False),
    ("Choir Practice", "community", False),
    ("USSSA Music City Fall Nationals - Softball", "community", False),
]

mfails = []
for title, base, want in MUSIC_CASES:
    pr, ex = _derive({"name": title, "title": title, "description": "", "category": base,
                      "sources": [{"source": "ics", "source_id": "1"}]})
    cats = {pr} | set(ex or [])
    ok = ("music" in cats) == want
    if not ok:
        mfails.append(title)
    print(f"{'ok ' if ok else 'FAIL'} {title[:46]:<48} -> {pr} + {sorted(ex or [])}"
          f"{'' if ok else ('   (music must NOT be here)' if not want else '   (music is missing)')}")
print()
print(f"{len(MUSIC_CASES)-len(mfails)}/{len(MUSIC_CASES)} passed")
cfails += mfails


# ---------------------------------------------------------------------------
# Free outdoor activity reaches wegosie
# ---------------------------------------------------------------------------
# wegosie is running+sports+fitness and `outdoors` is deliberately NOT one of
# its keys — a hike keeps its outdoors PIN and reaches the movement lens through
# the fitness SECONDARY. That path had two holes.
#
# `kayak` and `canoe` sat inside the secondary group's trailing \b, so the
# boundary demanded a non-word character straight after "canoe" and neither
# could ever match "Canoeing" or "Kayaking" — the only spelling a real listing
# uses. `hik(?:e|ing)` one line above shows the suffix was meant to be there.
# Same family as the `18+` age gate whose `+` could never end a \b, and the
# first patch for this reintroduced it (`swim\s+lesson` cannot match "Swim
# Lessons"), which is why the plurals are spelled out.
#
# And a parks department runs racquet, ice and target sports constantly, none of
# which the rule had heard of. Counted over 6,000 DISTINCT live titles from 490
# civic and park feeds, 2026-09-13, each count being titles that reached NEITHER
# layer: pickleball 25, skating 17, birding/bird walk 16, swim lessons 3,
# archery 3, disc golf 2, kayaking/canoeing 2. Net: 181 of the 6,000 reached
# wegosie from a `community` base before, 239 after.
WEGOSIE_CASES = [
    # (title, base category, must it reach wegosie?)
    ("Drop In Pickleball", "community", True),
    ("Fall 2026 Beginners Pickleball Clinics", "community", True),
    ("Developmental Figure Skating", "community", True),
    ("Family Canoeing", "outdoors", True),
    ("Kayaking 101 (7Y+)", "outdoors", True),
    ("Adaptive Swim Lessons Session Begins", "community", True),
    ("Intro to Archery", "community", True),
    ("Intro to Disc Golf", "community", True),
    ("Backyard Birding Series: Bird Walk at Brust Park", "community", True),
    # ...the ones that already worked, so a rewrite cannot quietly drop them
    ("Guided Bird Hike: Fall Migration", "outdoors", True),
    ("Full Moon Night Hike", "outdoors", True),
    ("Back to Basics: Cold Weather Hiking", "outdoors", True),
    # --- and what must NOT reach it.
    # A bare `walks?` scores 77 hits in the corpus and 74 correctly reach
    # neither layer: on a town calendar a "walk" is an art crawl or a fundraiser
    # far more often than it is exercise.
    ("Art & Wine Walk", "community", False),
    ("13th Annual Historic Cemetery Walk", "community", False),
    ("A Walk in Their Shoes - Dementia Simulation Workshop", "community", False),
    # Bare `paddle` is the same trap: 5 hits and 4 are not paddling at all.
    ("2026 Paddle Battle", "community", False),
    ("Doggie Paddle Day at Oasis", "community", False),
    ("Winter Springs Police Foundation Battle of the Paddle", "community", False),
    # CLOSURE NOTICES. `swim meet` would have taken both; a closed pool on a
    # movement lens is worse than missing the one real meet in the corpus.
    ("Swim Meet - Swim Center Closed", "community", False),
    ("Pool Closed on Saturday, October 31 for Halloween & Swim Meets", "community", False),
    # A birding LECTURE is not a walk — outdoors yes, movement no.
    ("Birding Day: A Day in Tillamook County", "community", False),
]

wfails = []
for title, base, want in WEGOSIE_CASES:
    pr, ex = _derive({"name": title, "title": title, "description": "", "category": base,
                      "sources": [{"source": "ics", "source_id": "1"}]})
    cats = {pr} | set(ex or [])
    got = bool(cats & WEGOSIE)
    ok = got == want
    if not ok:
        wfails.append(title)
    print(f"{'ok ' if ok else 'FAIL'} {title[:46]:<48} {base:<9} -> {pr} + {sorted(ex or [])}"
          f"{'' if ok else ('   (must NOT reach wegosie)' if not want else '   (wegosie is missing)')}")
print()
print(f"{len(WEGOSIE_CASES)-len(wfails)}/{len(WEGOSIE_CASES)} passed")
cfails += wfails


SWEEP_CASES = [
    # (name, description, source, want)
    ("On Fire! Scorching Stand Up Comedy!", "A night of side-splitting comedy.",
     "meetup", "community"),
    ("COMMON GROUND - Magic: the Gathering - Pauper Tuesdays", "Come play Magic.",
     "meetup", "community"),
    ("E-Commerce over Breakfast Berlin", "A curated meetup for e-commerce operators.",
     "meetup", "community"),
    ("Weekly Thursday Meditation", "Every Thursday, donation based.", "meetup", "community"),
    # a Meetup row that really IS a market keeps the key
    ("Ballard Farmers Market meetup", "", "meetup", "market"),
    ("Vintage Flea Market Crawl", "", "meetup", "market"),
    # and a source that stated `market` DELIBERATELY is never second-guessed,
    # including the two that would fail a text test
    ("Randolph Street Market September 26+27", "", "tribe", "market"),
    ("Wochenmarkt Wutzkyallee", "", "market:osm-berlin-de", "market"),
    ("Flohmarkt am Arkonaplatz", "", "market:osm-berlin-de", "market"),
]

sfails = []
for name, desc, src, want in SWEEP_CASES:
    got = map_category(_rec(name, desc, src=src))
    ok = got == want
    if not ok:
        sfails.append(name)
    print(f"{'ok ' if ok else 'FAIL'} [{src[:22]:<22}] {name[:40]:<42} -> {got}"
          f"{'' if ok else f'   (wanted {want})'}")
print()
print(f"{len(SWEEP_CASES)-len(sfails)}/{len(SWEEP_CASES)} passed")
cfails += sfails


for name, cat, desc, want_primary, must_have, must_not in CONTENT_CASES:
    primary, extras = derive_categories({"name": name, "category": cat, "description": desc})
    got = set(extras or [])
    ok = primary == want_primary and must_have <= got and not (must_not & (got | {primary}))
    if not ok:
        cfails.append(name)
    print(f"{'ok ' if ok else 'FAIL'} {name[:38]:<40} -> {primary:<10} + {sorted(got)}"
          f"{'' if ok else f'   (wanted {want_primary} +{sorted(must_have)} -{sorted(must_not)})'}")

print()
print(f"{len(CONTENT_CASES)-len(cfails)}/{len(CONTENT_CASES)} passed")


# ---------------------------------------------------------------------------
# The BACKFILL must be able to take a category away
# ---------------------------------------------------------------------------
# mapsee_reclassify.recompute() re-runs the classifier over a row already in the
# table. It used to hand the row's own stored `categories` back to
# derive_categories, whose rule 2 ("anything a source already told us
# explicitly") re-adds every key it is given, unjudged. That made the sweep a
# fixed point: it could add a secondary and never remove one, and it reported
# zero changes while doing it.
#
# Live consequence, 2026-08-12: "Gentle Morning Hatha Yoga" sat on the food map
# as fitness+[food] — a secondary is enough to reach a lens (../mapsee 0108
# matches `e.categories && p_categories`) — and the backfill was re-run "until
# it returned 0" against it. Zero was the laundering, not the answer.
print()
print("-- backfill can remove a wrong secondary --")
import mapsee_reclassify as _R

YOGA_DESC = ("Wednesday August 12 is the first of a 4-session meet-up to practice "
             "Gentle Morning Hatha Yoga with your Capitol Hill Community. You can pay "
             "a drop-in rate ($15.00) or sign-up for all four sessions using this link.")
BACKFILL_CASES = [
    # (title, desc, stored primary, stored secondaries, key that must NOT survive)
    ("Gentle Morning Hatha Yoga", YOGA_DESC, "fitness", ["food"], "food"),
    # the same shape, the other direction round: a stored secondary that the
    # rules do not re-derive must go, whatever it is.
    ("Sunday League Football", "Weekly workout for the squad.", "sports", ["food"], "food"),
]
bfails = []
for title, desc, prim, stored, gone in BACKFILL_CASES:
    p, e = _R.recompute({"title": title, "description": desc,
                         "category": prim, "categories": list(stored)})
    got = set(e or [])
    ok = p == prim and gone not in got
    if not ok:
        bfails.append(title)
    print(f"{'ok ' if ok else 'FAIL'} {title[:38]:<40} {prim}+{stored} -> {p} + {sorted(got)}"
          f"{'' if ok else f'   (wanted {gone!r} dropped)'}")

# And the guard against over-correcting: recompute must still KEEP a secondary
# the text genuinely supports, or the sweep just strips every row bare.
p, e = _R.recompute({"title": "Taco Crawl & Happy Hour", "description": "Food trucks all evening.",
                     "category": "party", "categories": []})
keeps = "food" in set(e or [])
if not keeps:
    bfails.append("Taco Crawl & Happy Hour")
print(f"{'ok ' if keeps else 'FAIL'} {'keeps a secondary the text supports':<40} party+[] -> {p} + {sorted(e or [])}")

print()
print(f"{len(BACKFILL_CASES)+1-len(bfails)}/{len(BACKFILL_CASES)+1} passed")
sys.exit(1 if (fails or cfails or bfails) else 0)
