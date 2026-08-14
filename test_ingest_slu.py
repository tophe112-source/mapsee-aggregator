"""Does the South Lake Union adapter read the right date, and refuse the rows
it cannot place?

Three traps, all of which produce a well-formed, plausible, WRONG row rather
than an error - the failure mode this repo cares most about:

  THE SERIES START IS NOT THE OCCURRENCE. A six-month window returns 225 rows
  for 118 event_ids: the endpoint expands a recurrence into one row per date,
  and on those rows `date_time` stays the SERIES start while
  `occurrence_date_time` carries the row's own date. Reading `date_time` puts
  every Saturday of a June-to-November farmers market on the first Saturday in
  June - twenty-five rows, one date, all but one wrong, and every one of them
  parses, geocodes and syncs without complaint.

  `end_time` IS A CLOCK, `end_date` IS THE LAST DAY OF A RUN. Pairing them turns
  a three-week school supply drive into a single event twenty-one days long,
  which on a map is an event that is always happening.

  A VENUE NAME IS NOT A LOCATION. mapsee_supabase_sync geocodes street+city+
  region and drops what has no street, silently. The block party this adapter
  was written for is one of the rows with no address in the feed, so the config's
  venue book is not a nicety - without it the event this source exists to carry
  is the one event it loses.

Pure functions and literal payloads: no network, no store, no database.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_slu as SLU

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


SITE = {
    "name": "test", "category": "community",
    "default_city": "Seattle", "default_region": "WA", "default_country": "US",
    "venues": {"South Lake Union Discovery Center": {
        "address": "101 Westlake Ave N", "city": "Seattle",
        "region": "WA", "postal_code": "98109", "country": "US"}},
    "skip_venues": ["multiple locations"],
}


def row(**over):
    base = {
        "event_id": 18693, "title": "2026 South Lake Union Block Party",
        "description": "SLU&#8217;s largest &amp; longest running party returns.",
        "permalink": "https://www.discoverslu.com/events/south-lake-union-block-party-2026/",
        "image_src": "https://www.discoverslu.com/wp-content/uploads/SLUBP_Thumb-1.jpg",
        "date_time": "2026-08-14 11:00 am", "end_time": "10:00 pm",
        "start_date_ymd": "20260814", "end_date_ymd": "20260814", "end_date": "20260814",
        "location_name": "South Lake Union Discovery Center",
        "address1": "", "address2": "", "city": "", "state": "", "zip": "",
        "schedule": "single_day", "post_status": "publish", "categories": [],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# the occurrence, not the series start
# --------------------------------------------------------------------------- #
market = row(event_id=18000, title="2026 South Lake Union Farmers Market",
             date_time="2026-06-06 10:00 am", occurrence_date_time="2026-09-05 10:00 am",
             end_time="3:00 pm", schedule="recurring", address1="120 Westlake Ave N",
             city="Seattle", state="WA", zip="98109")
ev, _ = SLU.to_event(market, SITE)
check("a recurrence uses occurrence_date_time, not the series start",
      ev is not None and ev.start_local == "2026-09-05T10:00:00", ev and ev.start_local)
check("the series start is not what lands", ev is not None and not ev.start_local.startswith("2026-06-06"))
check("occurrences of one event get distinct source_ids",
      ev is not None and ev.source_id == "18000:2026-09-05", ev and ev.source_id)

# with no occurrence field at all, date_time IS the date
one, _ = SLU.to_event(row(), SITE)
check("a single-day row falls back to date_time",
      one is not None and one.start_local == "2026-08-14T11:00:00", one and one.start_local)

check("noon and midnight survive the 12-hour clock",
      SLU.parse_start({"date_time": "2026-08-14 12:00 pm"}) == "2026-08-14T12:00:00"
      and SLU.parse_start({"date_time": "2026-08-14 12:00 am"}) == "2026-08-14T00:00:00")

# --------------------------------------------------------------------------- #
# end_time belongs to the start DAY
# --------------------------------------------------------------------------- #
drive = row(event_id=18859, title="School Supply Drive", date_time="2026-08-10 12:00 am",
            occurrence_date_time="2026-08-14 12:00 am", end_time="6:00 pm",
            end_date="20260831", end_date_ymd="20260830", schedule="multiple_days",
            address1="400 Fairview Ave N", city="Seattle", state="WA", zip="98109")
ev, _ = SLU.to_event(drive, SITE)
check("a multi-day run ends on the row's OWN day, not on end_date",
      ev is not None and ev.end_local == "2026-08-14T18:00:00", ev and ev.end_local)

check("an end earlier than the start is dropped rather than run backwards",
      SLU.parse_end({"end_time": "1:00 am"}, "2026-08-14T22:00:00") is None)
check("a missing end_time is simply no end",
      SLU.parse_end({"end_time": None}, "2026-08-14T11:00:00") is None)

# --------------------------------------------------------------------------- #
# placing it
# --------------------------------------------------------------------------- #
ev, why = SLU.to_event(row(), SITE)
check("the venue book places a row the feed left address-less",
      ev is not None and ev.address == "101 Westlake Ave N" and ev.city == "Seattle"
      and ev.postal_code == "98109", (ev and ev.address, why))

ev, why = SLU.to_event(row(location_name="Some New Cafe"), SITE)
check("an unknown venue with no address is refused", ev is None, ev)
check("...and says so, because the log is how the book grows", "add it to `venues`" in why, why)

ev, why = SLU.to_event(row(location_name="Multiple Locations"), SITE)
check("'Multiple Locations' is skipped", ev is None, ev)
check("...quietly, because nobody can fix it", why == "", why)

ev, _ = SLU.to_event(row(address1="860 Terry Ave N", city="Seattle", state="WA", zip="98109",
                         location_name="Lake Union Park"), SITE)
check("a row WITH an address does not consult the book",
      ev is not None and ev.address == "860 Terry Ave N", ev and ev.address)

# --------------------------------------------------------------------------- #
# the rest of the row
# --------------------------------------------------------------------------- #
ev, _ = SLU.to_event(row(categories=[{"slug": "music", "name": "Music"},
                                     {"slug": "other-eat-drink", "name": "Other Eat &amp; Drink"}]), SITE)
check("the first mapped category becomes the primary", ev is not None and ev.category == "music", ev and ev.category)
check("the rest come along as extras", ev is not None and "food" in (ev.categories or []), ev and ev.categories)
ev, _ = SLU.to_event(row(categories=[{"slug": "salon-spa", "name": "Salon & Spa"}]), SITE)
check("a directory-only tag falls back to the site default rather than inventing a lens",
      ev is not None and ev.category == "community", ev and ev.category)

ev, _ = SLU.to_event(row(), SITE)
check("entities are decoded, not passed through",
      ev is not None and "&#8217;" not in ev.description and "&amp;" not in ev.description, ev and ev.description)
check("the permalink is the way back to the listing",
      ev is not None and ev.ticket_url and ev.ticket_url.endswith("/south-lake-union-block-party-2026/"))

ev, _ = SLU.to_event(row(post_status="draft"), SITE)
check("an unpublished row is not an event", ev is None, ev)

# --------------------------------------------------------------------------- #
# the config the adapter actually ships with
# --------------------------------------------------------------------------- #
here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "slu_sources.json"), encoding="utf-8"))
site = cfg["sites"][0]
check("the shipped config still books the block party's venue",
      any(k.lower() == "south lake union discovery center" for k in site.get("venues", {})), list(site.get("venues", {})))
check("the shipped config still skips Multiple Locations",
      "multiple locations" in [s.lower() for s in site.get("skip_venues", [])], site.get("skip_venues"))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
