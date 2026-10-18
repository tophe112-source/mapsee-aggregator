#!/usr/bin/env python3
"""The 📺 Watch line: a league streamed in one known place, and nowhere else.

A Sounders match arrives from Ticketmaster with sub-genre "MLS", and every MLS
match is on Apple TV. That is a fact about the LEAGUE, so the sync writes it as
a rule keyed on what the source said - never a broadcaster guessed from a title.
What this protects, each with the case that would break it:
  * the adapter keeps the sub-genre as a field (genre), and a refresh fills it
  * MLS + sports -> the line, with the verified short alias
  * a league with no single home (NBA) gets nothing, however big
  * the same word on a concert (category music) gets nothing
  * the line is LAST, so a first-marker tail cut drops it with the rest
  * the app-side shape: "📺 Watch — <Service>: <url>" and nothing else on the line

  python test_sync_watch.py
"""
import re
import sys

from mapsee_ingest import NormalizedEvent, _FILLABLE, parse_ticketmaster_event
import mapsee_supabase_sync as SY

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("ok    " if cond else "FAIL  ") + label + ("" if cond else "   " + repr(detail)))
    if not cond:
        fails += 1


TENANT = "00000000-0000-0000-0000-000000000000"


def ev(**kw):
    base = dict(source="ticketmaster", source_id="tm-1", name="Seattle Sounders FC vs. CF Montreal", fingerprint="fp-1",
                start_utc="2026-10-25T02:30:00Z", end_utc="2026-10-25T04:30:00Z", latitude=47.595, longitude=-122.332,
                venue_name="Lumen Field", category="sports", description="Matchday.", ticket_url="https://www.ticketmaster.com/x")
    base.update(kw)
    return NormalizedEvent(**base)


def desc(**kw):
    return SY.to_row(ev(**kw).as_record("now"), TENANT)["description"] or ""


check("genre is a field a refresh fills", "genre" in _FILLABLE)
raw = {"id": "tm-1", "name": "Seattle Sounders FC vs. CF Montreal", "dates": {"start": {"localDate": "2026-10-24", "localTime": "19:30:00"}},
       "classifications": [{"primary": True, "segment": {"name": "Sports"}, "genre": {"name": "Soccer"}, "subGenre": {"name": "MLS"}}],
       "_embedded": {"venues": [{"name": "Lumen Field", "location": {"latitude": "47.595", "longitude": "-122.332"}}]}}
check("Ticketmaster's sub-genre is kept on the record as the league", parse_ticketmaster_event(raw).genre == "MLS", parse_ticketmaster_event(raw).genre)

d = desc(genre="MLS")
check("MLS + sports -> the Apple TV line, on the short alias that 301s to the channel",
      "\n\n📺 Watch — Apple TV: https://tv.apple.com/mls" in d, d)
check("...and it is the LAST line of the tail", d.rstrip().endswith("https://tv.apple.com/mls"), d[-80:])
check("...so the first-marker cut the offer measurers and 0227 make drops it with the rest",
      "📺" not in re.split(r"\n\nTickets / info:|\n\n🔎 More on this|\n\n🎵 Listen|\n\n📺 Watch", d)[0])
# The alternation added to the three cutters exists for exactly one case: a
# body whose ONLY marker is the Watch line. Run the measurer's own cutter on
# it, not a copy of its regex - a mutant that dropped the alternation survived
# a check that only compared strings.
import offers_0227 as OFF  # noqa: E402
only = {"title": "Seattle Sounders FC vs. CF Montreal", "description": "Matchday.\n\n📺 Watch — Apple TV: https://tv.apple.com/mls"}
check("the offer measurer cuts a body whose only marker is the Watch line (its own cutter, run)",
      "📺" not in OFF.body_text(only) and "Matchday." in OFF.body_text(only), OFF.body_text(only))
check("a league with no single home gets nothing, however big", "📺" not in desc(genre="NBA"))
check("no sub-genre, nothing", "📺" not in desc(genre=None))
check("the same word on a concert is not a league", "📺" not in desc(genre="MLS", category="music"))
check("the rule table says exactly what it can stand behind - and Apple TV is a subscription, not free",
      SY.BROADCAST_RULES == {"MLS": (("Apple TV", "https://tv.apple.com/mls", False),)})
check("...so the line carries no (free) on it", "(free)" not in d)
# FREE GOES FIRST, whatever order the table lists them in. Run on a rule table
# of our own, so the ordering is graded and not the one entry's accident.
with_free = {"XYZ": (("PayWall+", "https://paywall.example/x", False), ("Open TV", "https://open.example/x", True))}
orig = SY.BROADCAST_RULES
try:
    SY.BROADCAST_RULES = with_free
    got = SY.broadcast_for({"genre": "XYZ"}, "sports")
    check("a free service is listed ahead of a subscription one, however the table orders them",
          [r[0] for r in got] == ["Open TV", "PayWall+"], got)
    line = desc(genre="XYZ").split("\n\n")[-1]
    check("...and the line says which is free, first, with the flag written once after the name",
          line == "📺 Watch — Open TV (free): https://open.example/x  ·  PayWall+: https://paywall.example/x", line)
finally:
    SY.BROADCAST_RULES = orig
check("the line's shape is the Listen line's: one marker, service-colon-url, dot-separated",
      re.fullmatch(r"📺 Watch — (?:[^:\n]+: https?://\S+)(?:  ·  [^:\n]+: https?://\S+)*", d.split("\n\n")[-1]) is not None, d.split("\n\n")[-1])

print("\n%s" % ("%d failed" % fails if fails else "all ok"))
sys.exit(1 if fails else 0)
