"""Does the South Lake Union adapter read the right date, and refuse the rows
it cannot place?

Four traps, all of which produce a well-formed, plausible, WRONG row rather
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

  A CLOCK THE SITE WILL NOT SHOW IS NOT A CLOCK. 230 of 327 rows (2026-11-21)
  carry `dont_display_time: 1`, and the site prints a date and no time for
  them. 93 are the form's "12:00 am"; read as a clock, a musical with a 2pm
  matinee and an 8pm show was on the map from midnight to 3 AM and off it
  before either. The other 137 are an hour nobody chose to publish (a gallery
  show at its opening reception's hour, every weekday it hangs).

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
# a clock the site will not show is not a clock
# --------------------------------------------------------------------------- #
# The reported row, as the endpoint sent it on 2026-11-21 (prose shortened to
# the paragraphs that matter; the markup and entities are the feed's own).
BRAIN_PROSE = (
    "<p>In June 2015, the musical was presented as part of the Encores! Off-Center stage "
    "concert series.</p>\n<p><strong>Showtimes:</strong><br />\nNovember 13th &#8211; 8pm<br />\n"
    "November 14th &#8211; 2pm and 8pm<br />\nNovember 19th &#8211; 8pm<br />\n"
    "November 20th &#8211; 8pm<br />\nNovember 21st &#8211; 2pm and 8pm</p>\n"
    "<p>Music and Lyrics by William Finn<br />\nDirected by Richard Gray</p>\n")


def brain(day, **over):
    base = dict(event_id=19311, title="A New Brain", location_name="Raisbeck Performance Hall",
                address1="2015 Boren Ave", city="Seattle", state="WA", zip="98121",
                date_time="2026-11-19 12:00 am", occurrence_date_time=f"{day} 12:00 am",
                end_time=None, end_date="20261121", schedule="multiple_days",
                dont_display_time=1, custom_date_display_text="Nov 13 - 14 & 19 - 21",
                long_description=BRAIN_PROSE, categories=[{"slug": "film-theater"}])
    base.update(over)
    return row(**base)


evs, _ = SLU.to_events(brain("2026-11-20"), SITE)
check("the reported row: one show that day, at the hour its listing states",
      [(e.start_local, e.end_local) for e in evs] == [("2026-11-20T20:00:00", None)],
      [(e.start_local, e.end_local) for e in evs])
check("...and never at the feed's 12:00 am", all("T00:00" not in e.start_local for e in evs))
check("...under the id and fingerprint the stored row already has, so it is corrected, not orphaned",
      evs[0].source_id == "19311:2026-11-20"
      and evs[0].fingerprint == SLU.make_fingerprint("A New Brain", "2026-11-20", "Raisbeck Performance Hall"),
      evs[0].source_id)

evs, _ = SLU.to_events(brain("2026-11-21"), SITE)
check("a matinee and an evening show are two events",
      [e.start_local for e in evs] == ["2026-11-21T14:00:00", "2026-11-21T20:00:00"],
      [e.start_local for e in evs])
check("...with two fingerprints, or the store folds the 8pm into the 2pm",
      len({e.fingerprint for e in evs}) == 2 and len({e.source_id for e in evs}) == 2,
      [e.source_id for e in evs])
check("...the first keeping the day's old key, the second widened by its clock",
      len(evs) == 2
      and evs[0].fingerprint == SLU.make_fingerprint("A New Brain", "2026-11-21", "Raisbeck Performance Hall")
      and evs[1].source_id == "19311:2026-11-21:2000", [e.source_id for e in evs])
check("to_event still answers with the day's first", SLU.to_event(brain("2026-11-21"), SITE)[0].start_local
      == "2026-11-21T14:00:00")

# No prose to read: the row names a DAY. A bare date is what the sync brackets
# as all day where the venue is; "T00:00:00" is what it used to write.
drive_day = row(event_id=19423, title="Food Drive", location_name="Fairview Market Hall",
                address1="400 Fairview Ave N", city="Seattle", state="WA", zip="98109",
                date_time="2026-11-09 12:00 am", occurrence_date_time="2026-11-24 12:00 am",
                end_time=None, schedule="multiple_days", dont_display_time=1,
                long_description="<p>Drop off non-perishable food in the lobby.</p>")
ev, _ = SLU.to_event(drive_day, SITE)
check("a row the site shows no time for, with none stated, is a bare date",
      ev is not None and ev.start_local == "2026-11-24" and ev.end_local is None,
      ev and (ev.start_local, ev.end_local))
check("...keyed as it always was", ev is not None and ev.source_id == "19423:2026-11-24", ev and ev.source_id)

# The 137: a hidden hour that is not midnight is still not published. This one
# is real - "Body Language" hung Mon-Fri at its opening reception's 6-8pm.
show = row(event_id=19350, title='"Body Language" Exhibition', location_name="Gage Academy of Art",
           address1="2107 Westlake Ave", city="Seattle", state="WA", zip="98121",
           date_time="2026-11-13 6:00 pm", occurrence_date_time="2026-11-16 6:00 pm",
           end_time="8:00 pm", schedule="recurring", dont_display_time=1,
           custom_date_display_text="Mon - Fri, Nov 13, 2026 - Jan 8, 2027",
           long_description="<p>Opening Reception: Nov 12 from 6-8pm</p>")
ev, _ = SLU.to_event(show, SITE)
check("a hidden hour is not published either: the reception's 6-8pm is not the exhibition's",
      ev is not None and ev.start_local == "2026-11-16" and ev.end_local is None,
      ev and (ev.start_local, ev.end_local))
check("a line that opens with a label is another occasion's hour, even on its own date",
      SLU.showtimes("<p>Opening Reception: Nov 12 from 6-8pm</p>", "2026-11-12") == [])

# ...unless the site PRINTS the hours where its time label would be.
ship = dict(show, title='"The Mothership" Exhibition', date_time="2026-11-14 12:00 pm",
            occurrence_date_time="2026-11-18 12:00 pm", end_time="5:00 pm",
            custom_date_display_text="Wed - Sat, Nov 13 - Dec 18, 2026, 12 - 5 pm",
            long_description="<p>Opening Reception: Nov 13 from 4:30-7pm</p>")
ev, _ = SLU.to_event(ship, SITE)
check("hours printed in the date text are published hours",
      ev is not None and (ev.start_local, ev.end_local) == ("2026-11-18T12:00:00", "2026-11-18T17:00:00"),
      ev and (ev.start_local, ev.end_local))
check("a date range in that text is not an hour range",
      SLU.shown_hours({"custom_date_display_text": "Nov 13 - 14 & 19 - 21"}) == []
      and SLU.shown_hours({"custom_date_display_text": "Tue - Sat, Oct 29 - Dec 19, 2026"}) == [])

# A range on a dated line is a start and an end, per day (Bloodworks, live).
BLOOD = "<p>Thursday, Dec 3: 10:00 am to 4:00 pm<br />Friday, Dec 4: 9:00 am to 3:00 pm</p>"
check("a dated range gives that day's own hours",
      SLU.showtimes(BLOOD, "2026-12-03") == [(600, 960)] and SLU.showtimes(BLOOD, "2026-12-04") == [(540, 900)],
      (SLU.showtimes(BLOOD, "2026-12-03"), SLU.showtimes(BLOOD, "2026-12-04")))
check("'p.m.' with its dots, and 'at' (Four Churchill Short Plays, live)",
      SLU.showtimes("Saturday, November 7th at 2 p.m. and 8 p.m.<br>Sunday, November 8th at 2 p.m.",
                    "2026-11-07") == [(840, None), (1200, None)])

# What the reader must refuse: every one of these is a guess if taken.
check("a line for another day says nothing about this one",
      SLU.showtimes(BRAIN_PROSE, "2026-11-15") == [])
check("a weekday that is not this date's is another year's listing",
      SLU.showtimes("Friday, Dec 3: 10:00 am to 4:00 pm", "2026-12-03") == [])
check("a stated year that is not the row's refuses the line",
      SLU.showtimes("November 21st, 2025 &#8211; 8pm", "2026-11-21") == []
      and SLU.showtimes("November 21st, 2026 &#8211; 8pm", "2026-11-21") == [(1200, None)])
check("words after the clock are not clocks, so the line is not read",
      SLU.showtimes("November 21st &#8211; 8pm (sold out) doors 7pm", "2026-11-21") == []
      and SLU.showtimes("November 21st &#8211; 5:30-6:30pm or 6:30-7:30pm", "2026-11-21") == [])
check("two lines for one day that disagree are no answer",
      SLU.showtimes("November 21st &#8211; 8pm<br>November 21 &#8211; 7pm", "2026-11-21") == []
      and SLU.showtimes("November 21st &#8211; 8pm<br>Nov 21: 8 pm", "2026-11-21") == [(1200, None)])
check("clocks out of order, or an hour that is not one, are not a programme",
      SLU.showtimes("November 21st &#8211; 8pm and 2pm", "2026-11-21") == []
      and SLU.showtimes("November 21st &#8211; 13pm", "2026-11-21") == [])
check("'11 - 1 pm' opens in the morning; '4-6pm' in the afternoon",
      SLU._times("11 - 1 pm") == [(660, 780)] and SLU._times("4-6pm") == [(960, 1080)],
      (SLU._times("11 - 1 pm"), SLU._times("4-6pm")))
check("'May' is a month, 'Maybe' is not",
      SLU.showtimes("May 5 &#8211; 8pm", "2027-05-05") == [(1200, None)]
      and SLU.showtimes("Maybe 5 &#8211; 8pm", "2027-05-05") == [])

# And a row the site DOES show a time for is read exactly as before - the
# stated prose is not consulted, whatever it says.
timed = brain("2026-11-21", dont_display_time=0, occurrence_date_time="2026-11-21 7:30 pm")
evs, _ = SLU.to_events(timed, SITE)
check("a shown time wins over the prose", [e.start_local for e in evs] == ["2026-11-21T19:30:00"],
      [e.start_local for e in evs])
check("the flag arrives as 0/1 and is read as either spelling",
      SLU.has_clock({"dont_display_time": 0}) and SLU.has_clock({}) and SLU.has_clock({"dont_display_time": "0"})
      and not SLU.has_clock({"dont_display_time": 1}) and not SLU.has_clock({"dont_display_time": "1"})
      and not SLU.has_clock({"dont_display_time": True}))

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
