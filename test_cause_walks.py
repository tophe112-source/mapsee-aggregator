"""Does a walk, run or ride FOR A CAUSE reach the community doors, without the
rule handing `community` to every ordinary race?

RunSignup files every race, walking_only included, as ('running', ['fitness']),
which only wegosie opens onto; awaresie (community/volunteer/learning) and
plansie (community/outdoors/arts/theater) showed none of them. _CAUSE_WALK_RX
reads the TITLE and adds `community`. Every title below is a real one, from
the live map (events_near_public, five metros, 2026-10-11), RunSignup's race
list, the US live corpus of 2026-09-24, or the charities' own platforms; the
three marked SHAPE are the collision a real title of that form would hit.
The source category and secondaries are what the adapter sends: RunSignup's
walking_only and running races arrive as ('running', ['fitness']), a trail
race as ('running', ['outdoors', 'fitness']), a ride as ('fitness',
['outdoors']).

    python test_cause_walks.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mapsee_supabase_sync import derive_categories, MAX_EXTRA_CATEGORIES

RACE = ["fitness"]                 # RunSignup's running_race / walking_only
RIDE = ["outdoors"]                # RunSignup's bike_ride, BikeReg

# (title, source category, source secondaries, description, expect primary,
#  expect community among the secondaries)
CASES = [
    # --- the brands, as the charities title their own events
    ("Walk to End Alzheimer's - Kewaunee", "running", RACE, "", "running", True),
    ("Relay For Life of Black Hawk County 2026", "fitness", None, "", "fitness", True),
    ("Making Strides of Atlanta Presented By Havertys Furniture", "running", RACE, "", "running", True),
    ("2026 Charlotte, NC MORE THAN PINK Walk | Susan G. Komen", "running", RACE, "", "running", True),
    ("2026 Central Ohio Kidney Walk", "running", RACE, "", "running", True),
    ("Corpus Christi Great Strides 2026", "running", RACE, "", "running", True),
    ("2027 Walk to Cure Arthritis - Columbus, OH", "running", RACE, "", "running", True),
    ("2026 Greater Los Angeles Heart & Stroke Walk", "fitness", None, "", "fitness", True),
    ("DSNOCC 15th Anniversary Buddy Walk®", "running", RACE, "", "running", True),
    ("Walk to Defeat ALS Greater San Diego", "running", RACE, "", "running", True),
    ("Relevo Por La Vida de San Juan Estudiantil Metro 2027", "fitness", None, "", "fitness", True),
    ("NAMI Walks 2026 - Hikers with Heart", "outdoors", None, "", "outdoors", True),
    ("Toys for Tots 5K", "running", RACE, "", "running", True),

    # --- a move word with a cause: live running/fitness titles, 2026-10-11
    ("Philadelphia Walk to End Colon Cancer", "running", RACE, "", "running", True),
    ("Every Color Matters Cancer Awareness 5K Run/Walk", "running", RACE, "", "running", True),
    ("Knox County Suicide Prevention Coalition 5K/10K", "running", RACE, "", "running", True),
    ("Delaware Down Syndrome 5K Run/Walk", "running", RACE, "", "running", True),
    ("2026 Walk for Sickle Cell San Antonio", "running", RACE, "", "running", True),
    ("Total HealthWorks - Movin' It! 5K Run/Walk for Parkinson’s", "running", RACE, "", "running", True),
    ("2026 Atlanta Miles for Melanoma 5K", "running", RACE, "", "running", True),
    ("EMA Pink Ribbon 5K Run/Walk of Greater Philadelphia", "running", RACE, "", "running", True),
    ("Race Against Violence 5K", "running", RACE, "", "running", True),
    ("Cantigny 5K Run/Walk 2026 - Benefiting The Midwest Shelter for Homeless Veterans",
     "running", RACE, "", "running", True),
    ("12th Annual Veterans Haven 5K Run and 1 Mile Walk", "running", RACE, "", "running", True),
    ("Trot for Troops 5K or 1Mi Walk", "running", RACE, "", "running", True),
    ("Rescue Run/Walk 5K", "running", RACE, "", "running", True),
    ("CCD’s Holiday Race for Recovery 5K", "running", RACE, "", "running", True),
    ("Mary's Meals \"Run for Hope\" 5K Run/Walk", "running", RACE, "", "running", True),
    ("RoofMen Charity 5K & 1 Mile Walk", "running", RACE, "", "running", True),
    ("SIDS Awareness 5k Run/Walk", "running", RACE, "", "running", True),
    ("Newtown Turkey Trot 5k in Honor of Officer Brian Gregg", "running", RACE, "", "running", True),
    # a memorial run is its own fundraiser; a foundation's 5K is the foundation's
    ("26th Annual Shannon McNamara Memorial Run/Walk", "running", RACE, "", "running", True),
    ("The Ed. Foundation of South Brunswick - 5K and 1 Mile Walk", "running", RACE, "", "running", True),
    # rides and hikes, not only races
    ("Gears and Beers Ride for Hospice", "fitness", RIDE, "", "fitness", True),
    ("Greg Pellegrino Memorial Ride", "fitness", None, "", "fitness", True),
    ("Joe Hummel Memorial Philly Hike 8.5 2.5 mph 11 or 13 mi 3 mph", "outdoors", None, "", "outdoors", True),
    ("Doggo Costume Parade and Walkathon Fundraiser", "outdoors", None, "", "outdoors", True),
    ("NOMAD RUN for AFANOC Charity (5KM) + Rooftop Hangout", "learning", None, "", "learning", True),

    # --- ordinary races stay on wegosie alone
    ("Columbus Hungry Turkey Trot", "running", RACE, "", "running", False),
    ("Santa Shuffle 5k & Kids Fun Run", "running", RACE, "", "running", False),
    ("Reindeer Run 5k / Kids Fun Run", "running", RACE, "", "running", False),
    # "New Hope" is a town on the Delaware, not a hope
    ("11th Annual Lambertville - New Hope Two Town Turkey Trot! Thanksgiving 2026",
     "running", RACE, "", "running", False),
    # the Arthritis Foundation's Jingle Bell Run looks exactly like a town's
    ("2026 Jingle Bell Run - Columbus, OH", "running", RACE, "", "running", False),
    ("Navarre Jingle Bell Run", "running", RACE, "", "running", False),
    # bare `heart` is a name far more often than a cause
    ("I Heart Biddeford 5K", "running", RACE, "", "running", False),
    ("Heart of the Desert Wine Run 5k", "running", RACE, "", "running", False),
    ("Sacred Heart Church 5K Run/Walk", "running", RACE, "", "running", False),
    ("Run Your Heart Out – 5K, 10K, 10-Miler, Half Marathon", "running", RACE, "", "running", False),
    ("Heart Attack Run Club's First Annual 5K", "running", RACE, "", "running", False),
    # a holiday is not a cause
    ("Veterans Day 5K", "running", RACE, "", "running", False),
    ("Langhorne American Legion Veteran’s Day 5K", "running", RACE, "", "running", False),
    ("Veteran's Day Nature Hike at Ridley Creek State Park", "outdoors", None, "", "outdoors", False),
    ("Memorial Day 5K", "running", RACE, "", "running", False),                      # SHAPE
    # a park is not a memorial
    ("Causal Run/Walk at Memorial Park", "outdoors", None, "", "outdoors", False),
    # MS is Mississippi in a US title; only Walk MS / Bike MS / MS 150 are the society's
    ("Gulfport, MS Turkey Trot 5K", "running", RACE, "", "running", False),        # SHAPE
    # awareness of nothing in particular is mindfulness
    ("Chitta Walks - Awareness in Motion", "outdoors", None, "", "outdoors", False),
    # a talk or a workday about the cause is not the walk
    ("A Walk in Their Shoes - Dementia Simulation Workshop", "learning", None, "", "learning", False),
    ("Chicken Run Rescue Workday", "learning", None, "", "learning", False),
    ("Alzheimer's Caregiver Support Group", "learning", None, "", "learning", False),
    ("Learn about Hypertension, Heart Health, and Get Your Blood Pressure Checked",
     "fitness", None, "", "fitness", False),
    # a bill's title is a name
    ("Light the Night", "music", None, "", "music", False),                          # SHAPE
]

fails = []
for name, cat, cats, desc, want_p, want_c in CASES:
    rec = {"name": name, "category": cat, "description": desc}
    if cats is not None:
        rec["categories"] = list(cats)
    p, e = derive_categories(rec)
    got_c = "community" in (e or [])
    ok = p == want_p and got_c == want_c and len(e or []) <= MAX_EXTRA_CATEGORIES
    print(f"{'ok ' if ok else 'FAIL'} {name[:70]:70s} -> {p} + {e}"
          f"  (community {'wanted' if want_c else 'refused'})")
    if not ok:
        fails.append(name)

print()
print("-- the slot it takes, and the ones it must not --")

# AHEAD of the description's keyword signals: a 5K's blurb says "food trucks"
# and "live music" far more often than its title names the charity, and the
# keyword loop would take the last slot first.
p, e = derive_categories({"name": "Every Color Matters Cancer Awareness 5K Run/Walk",
                          "category": "running", "categories": ["fitness"],
                          "description": "Food trucks, live music and a beer garden after the race."})
ok = (p, e) == ("running", ["fitness", "community"])
print(f"{'ok ' if ok else 'FAIL'} the cause outranks a blurb's food trucks -> {p} + {e}")
if not ok:
    fails.append("ahead of keywords")

# BEHIND what the source said: a trail race for a cause arrives with both
# slots taken, and the source's keys stay. It already reaches plansie on
# `outdoors`.
p, e = derive_categories({"name": "Sickle Cell Disease Association of America, CT/ Michelle's House Walk. Run. Bike.",
                          "category": "running", "categories": ["outdoors", "fitness"], "description": ""})
ok = (p, e) == ("running", ["outdoors", "fitness"])
print(f"{'ok ' if ok else 'FAIL'} a source's own two keys are never displaced -> {p} + {e}")
if not ok:
    fails.append("behind the source")

# A walk already filed community gains nothing and loses nothing.
p, e = derive_categories({"name": "Susan G. Komen Walk for Breast Cancer", "category": "community",
                          "description": ""})
ok = p == "community" and "community" not in (e or [])
print(f"{'ok ' if ok else 'FAIL'} a community walk keeps community as its primary -> {p} + {e}")
if not ok:
    fails.append("community primary")

print()
print(f"{len(CASES) + 3 - len(fails)}/{len(CASES) + 3} passed")
sys.exit(1 if fails else 0)
