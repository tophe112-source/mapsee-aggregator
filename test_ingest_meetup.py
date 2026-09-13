#!/usr/bin/env python3
"""
test_ingest_meetup.py — a row can have real coordinates and still not be a place.

Prints one line per case and exits non-zero, like the other gate scripts.

WHAT THIS IS ABOUT. Every adapter that can refuse an online event refuses it by
asking whether it has coordinates — mapsee_ingest_meetup says so in as many
words ("online / no venue -> can't map it"). That is only true while the venue
box is empty. Meetup lets a Zoom event be filed as eventType PHYSICAL with
anything at all typed into the venue, and then it HAS coordinates and sails
through every check we had.

The reported row did exactly that: "Seattle Gay Virtual Speed Dating on Zoom …
Join from home", status ACTIVE, venue and address both the Plus Code
"JP7Q+33 Mercer Island", city "Seattle" — so the map showed a street corner for
a video call, and the sync's Census pass then moved the pin several km by
geocoding the fabricated address.

Measured 2026-09-13 over 2,000 live event pages sampled from mapsee.me's own
sitemaps: 40 rows say in their own words that they happen on Zoom, 35 of them
online-only and ALL 35 from this adapter — one commercial speed-dating network
reposting the same template city by city, into groups that have nothing to do
with it (a taekwondo group, a calligraphy club, a vegan cookery crew). The other
5 are a sangha, a church and a meditation group that really do run a room as
well as a stream.

MOST OF WHAT IS PINNED HERE IS THE HYBRID SIDE, because that is the expensive
direction to get wrong: refusing a congregation that streams its service is a
working venue removed from the map, and nothing downstream would ever say so.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mapsee_ingest import (looks_online_only, looks_like_plus_code,
                           venue_is_only_a_plus_code)
from mapsee_ingest_meetup import to_event

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


SOON = (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%dT19:00:00Z")


def node(**kw):
    base = {"id": "1", "title": "Sunday Social", "status": "ACTIVE", "dateTime": SOON,
            "description": "Come along.",
            "venue": {"name": "The Royal Room", "address": "5000 Rainier Ave S",
                      "lat": 47.5589, "lon": -122.2839}}
    v = kw.pop("venue", None)
    if v:
        base["venue"] = dict(base["venue"], **v)
    base.update(kw)
    return base


print("the reported row, exactly as Meetup serves it")
REPORTED = node(
    title="Seattle Gay Online Speed Dating",
    description=("💕 Seattle Gay Virtual Speed Dating on Zoom Join from home and meet real "
                 "Seattle gay singles in guided one-on-one Zoom rounds. Matched by age."),
    venue={"name": "JP7Q+33 Mercer Island", "address": "JP7Q+33 Mercer Island",
           "city": "Seattle", "state": "WA", "lat": 47.612686, "lon": -122.262314})
check("it is refused", to_event(REPORTED), None)
# Each half has to stand on its own, or a listing that fixes one of them walks
# back in through the other.
check("  ...on the Zoom blurb alone, with a real venue",
      to_event(node(description="Virtual Speed Dating on Zoom. Join from home.")), None)
check("  ...and on the Plus Code alone, with an innocent blurb",
      to_event(node(venue={"name": "JP7Q+33 Mercer Island",
                           "address": "JP7Q+33 Mercer Island"})), None)
check("status ACTIVE and real coordinates are NOT enough on their own — this is "
      "the row as it passed every previous check", to_event(node()) is None, False)

print()
print("a hybrid event has somewhere to turn up, and must stay on the map")
HYBRID = [
    ("Sunday Sangha Night & Community Gathering: Online and In-Person",
     "Join us in the hall or on Zoom."),
    ("SUNDAY EVENING SERVICES", "Services are held in person and on Zoom each week."),
    ("Meditation + \"Teacher's Corner\" Dharma Learning (online & in-person)",
     "A Zoom link is sent to everyone registered."),
    ("Peach Blossom Sangha Meetup", "We sit together in-person, with others joining on Zoom."),
    ("Monthly Talk", "Hybrid: come to the venue or join the Zoom call."),
]
for title, desc in HYBRID:
    check(f"  {title[:52]}", to_event(node(title=title, description=desc)) is None, False)

print()
print("...and silence is never evidence. Most listings never say how you attend")
for title, desc in [("Sunday Social", "Come along, all welcome."),
                    ("Board Game Night", ""),
                    ("Book Club", "We meet at the back of the cafe.")]:
    check(f"  {title}", to_event(node(title=title, description=desc)) is None, False)

print()
print("the two phrases kept OUT of the rule, and why")
# A Les Mills VIRTUAL class is held in a real studio with the instructor on a
# screen, and OpenActive leisure centres are the biggest block of rows on the
# map — 393 of the 2,000 sampled are book.everyoneactive.com alone.
check("a 'Virtual' gym class is a real bookable session in a real studio",
      looks_online_only("R P M Virtual", "45 minutes on the bike."), False)
check("...including when the title spells it out",
      looks_online_only("Virtual Class: Body Balance", "Studio 2, level 1."), False)
# Measured 0 for 2: both rows carrying "Zoom link" are hybrid.
check("a published Zoom link is usually a room that also streams",
      looks_online_only("Weekly Meeting", "A Zoom link goes out on Friday."), False)
check("...but an explicit 'on Zoom' still counts",
      looks_online_only("Weekly Meeting", "We meet on Zoom every Friday."), True)

print()
print("a Plus Code is a dropped pin, not the name of a place")
for s in ("JP7Q+33 Mercer Island", "87G8+QF New York", "6W8X+GQ Frayser"):
    check(f"  {s!r} is a Plus Code", looks_like_plus_code(s), True)
for s in ("2800 Torrey Pines Scenic Dr", "The Royal Room", "Cafe 8+8", "Studio A+",
          "Seattle Center", ""):
    check(f"  {s!r} is not", looks_like_plus_code(s), False)

print()
print("  BOTH boxes, and that is the load-bearing part")
check("name and address are the same machine string -> no venue at all",
      venue_is_only_a_plus_code("JP7Q+33 Mercer Island", "JP7Q+33 Mercer Island"), True)
# Large parts of the world have no street addressing, and a Plus Code is the
# honest ADDRESS there. The venue is still called something.
check("a real venue with a Plus Code address is untouched",
      venue_is_only_a_plus_code("Kigali Convention Centre", "FW5M+2R Kigali"), False)
check("a Plus Code name with a real street address is untouched too",
      venue_is_only_a_plus_code("JP7Q+33", "2800 Torrey Pines Scenic Dr"), False)
check("a venue with no address at all is not condemned by the missing half",
      venue_is_only_a_plus_code("The Royal Room", None), False)

print()
print("the retire pass and the ingest must never disagree")
# An upsert cannot delete, so refusing these at the boundary leaves every row
# already written exactly where it is — mapsee_retire_online_events is the other
# half, and it decides with the SAME predicate over the stored row. If these two
# ever drift, the backfill either misses rows the ingest refuses or hides rows it
# would happily write.
from mapsee_retire_online_events import should_retire   # noqa: E402

STORED = [
    # (title, stored description as the sync assembled it, want_hidden)
    ("Seattle Gay Online Speed Dating",
     "💕 Seattle Gay Virtual Speed Dating on Zoom Join from home and meet real singles.\n\n"
     "📍 JP7Q+33 Mercer Island, Seattle, WA 98101\n\n"
     "Tickets / info: https://www.meetup.com/rainbow-singles-alliance/events/316277698/", True),
    ("Sunday Sangha Night & Community Gathering: Online and In-Person",
     "Join us in the hall or on Zoom.\n\n📍 1 Main St\n\nTickets / info: https://x.test/e", False),
    ("Farmers Market", "Every Saturday, rain or shine.\n\n📍 1 Main St", False),
]
for title, desc, want in STORED:
    check(f"  {title[:52]}", should_retire({"title": title, "description": desc}), want)
# The generated lines the sync appends carry no Zoom vocabulary, which is why
# should_retire does not have to strip them. Pin that, or a future line that
# does would silently start hiding rows.
check("  the sync's own appended lines are not evidence of anything",
      should_retire({"title": "Book Club",
                     "description": "📍 1 Main St\n\nTickets / info: https://www.meetup.com/"
                                    "zoom-virtual-online-meetup-group/events/1\n\n"
                                    "🔎 More on this show: https://www.google.com/search?q=zoom"}),
      False)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all meetup placement checks passed")
