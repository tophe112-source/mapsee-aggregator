"""Do adapter-supplied secondary categories survive all the way to the sync?

A lens is only as good as the supply behind its categories, and a source that can
only ever emit ONE key forces every event to pick a single lens. NormalizedEvent
carries up to MAX_EXTRA_CATEGORIES extras; this checks the adapters populate them
and that mapsee_supabase_sync.derive_categories folds them in rather than
dropping them on the floor.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mapsee_ingest import NormalizedEvent, norm_categories, MAX_EXTRA_CATEGORIES, VALID_CATEGORIES
from mapsee_supabase_sync import derive_categories, MAPSEE_CATEGORY_KEYS
import mapsee_ingest_eventbrite as EB
import mapsee_ingest_localist as LO
import mapsee_ingest_axs as AXS

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


# --- the two vocabularies must not drift apart -------------------------------
check("ingest and sync share one category vocabulary",
      VALID_CATEGORIES == MAPSEE_CATEGORY_KEYS,
      f"only in ingest={VALID_CATEGORIES - MAPSEE_CATEGORY_KEYS}, "
      f"only in sync={MAPSEE_CATEGORY_KEYS - VALID_CATEGORIES}")

# --- norm_categories hygiene --------------------------------------------------
check("drops the primary from extras", norm_categories("music", ["music", "party"]) == ["party"])
check("drops unknown keys", norm_categories("music", ["nightlife", "party"]) == ["party"])
check("de-duplicates", norm_categories("music", ["party", "party", "food"]) == ["party", "food"])
check("caps at the DB limit", len(norm_categories("music", ["party", "food", "arts", "kids"])) == MAX_EXTRA_CATEGORIES)
check("tolerates None / nesting", norm_categories("music", None, ["food"], "party") == ["food", "party"])

# --- Eventbrite: the buckets that are genuinely two things --------------------
def eb(cat):
    return EB._categories({"category": {"name": cat}}, None)

check("EB sports & fitness -> sports + fitness", eb("Sports & Fitness") == ("sports", ["fitness"]), eb("Sports & Fitness"))
check("EB health & wellness -> fitness (was community)", eb("Health & Wellness")[0] == "fitness", eb("Health & Wellness"))
check("EB travel & outdoor reaches movement", "fitness" in eb("Travel & Outdoor")[1], eb("Travel & Outdoor"))
check("EB music stays single", eb("Music") == ("music", []), eb("Music"))
check("EB override still wins the primary", EB._categories({"category": {"name": "Music"}}, "food")[0] == "food")

# --- Localist: every event_type kept, not just the first ----------------------
def lo(*types):
    return LO._categories({"filters": {"event_types": [{"name": t} for t in types]}}, None)

check("Localist keeps a second type", lo("Recreation", "Wellness") == ("outdoors", ["fitness"]), lo("Recreation", "Wellness"))
check("Localist caps extras", len(lo("Music", "Food", "Market", "Kids")[1]) <= MAX_EXTRA_CATEGORIES)
check("Localist falls back to the source default",
      LO._categories({"filters": {"event_types": []}}, "learning") == ("learning", []))
check("Localist maps campus fitness vocabulary", lo("Fitness")[0] == "fitness", lo("Fitness"))
# A campus "Theatre" event_type used to land on 'arts', and nothing downstream
# corrected it — _PROMOTABLE_TO_THEATER is {"music", "other"}, so 'arts' is a
# terminal answer. Both spellings, because Localist calendars use both.
check("Localist theatre -> theater", lo("Theatre")[0] == "theater", lo("Theatre"))
check("Localist theater -> theater", lo("Theater")[0] == "theater", lo("Theater"))
check("Localist performance stays arts", lo("Performance")[0] == "arts", lo("Performance"))

# --- AXS: was emitting raw genre strings, so everything became 'other' --------
check("AXS concert -> music", AXS._categories("Concerts", "") == ("music", []), AXS._categories("Concerts", ""))
check("AXS comedy -> theater", AXS._categories("Comedy", "")[0] == "theater")
check("AXS club night reaches nightlife", "party" in AXS._categories("Electronic/DJ", "")[1])
check("AXS falls back to the title", AXS._categories(None, "Summer Jazz Festival")[0] == "music")
check("AXS unknown stays other", AXS._categories("Zzzz", "Zzzz") == ("other", []))

# --- END TO END: adapter extras must reach the sync's output ------------------
ev = NormalizedEvent(source="eventbrite", source_id="1", name="Sunrise Flow",
                     category="sports", categories=["fitness"])
rec = ev.as_record("2026-07-28T00:00:00Z")
primary, extras = derive_categories(rec)
check("adapter extras survive into the sync", primary == "sports" and "fitness" in (extras or []),
      (primary, extras))

# a source that emits nothing extra must still behave
ev2 = NormalizedEvent(source="x", source_id="2", name="Quiet Thing", category="music")
p2, x2 = derive_categories(ev2.as_record("2026-07-28T00:00:00Z"))
check("no extras is still valid (None, not [])", p2 == "music" and x2 is None, (p2, x2))

# the sync must never emit a key the app cannot render
ev3 = NormalizedEvent(source="x", source_id="3", name="Yoga in the Park",
                      category="community", categories=["fitness", "outdoors"])
p3, x3 = derive_categories(ev3.as_record("2026-07-28T00:00:00Z"))
check("every emitted key is renderable", {p3} | set(x3 or []) <= MAPSEE_CATEGORY_KEYS, (p3, x3))
check("never exceeds the DB limit", len(x3 or []) <= MAX_EXTRA_CATEGORIES, x3)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
