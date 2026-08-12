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
]

cfails = []
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
