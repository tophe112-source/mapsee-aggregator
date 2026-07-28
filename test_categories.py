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
sys.exit(1 if (fails or cfails) else 0)
