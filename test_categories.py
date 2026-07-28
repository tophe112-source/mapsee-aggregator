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
sys.exit(1 if fails else 0)
