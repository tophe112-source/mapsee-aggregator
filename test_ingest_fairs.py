#!/usr/bin/env python3
"""
test_ingest_fairs.py - every rule here is a wrong answer a live sweep produced.

The fixtures are the real strings, taken verbatim off the fairs' own pages on
2026-09-04. Nothing is invented, because the whole difficulty of this adapter is
that a state fair's website is marketing copy: the dates are the headline, and
so are a ticket sale, a horse show and a table of every year since 1966.

    python test_ingest_fairs.py
"""
import sys
from datetime import date

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

import mapsee_ingest_fairs as F

TODAY = date(2026, 9, 4)
checks = []


def check(cond, label):
    checks.append((bool(cond), label))


def dates(text):
    got, _why = F.pick_fair_dates(text, TODAY)
    return (got[0], got[1]) if got else None


def why(text):
    return F.pick_fair_dates(text, TODAY)[1]


# ---------------------------------------------------- 1. the shapes that are real
# Every one of these is how one fair actually writes its dates. They differ more
# than they look: a full second month, no second month, an ordinal, a holiday
# name in the middle, a weekday in the middle, and a hyphen with no space.
check(dates("Ohio State Fair 2027 | July 28 - August 8, 2027 | Family Fun")
      == (date(2027, 7, 28), date(2027, 8, 8)), "July 28 - August 8, 2027")
check(dates("Missouri State Fair | Aug 12-22, 2027 Skip to content")
      == (date(2027, 8, 12), date(2027, 8, 22)), "Aug 12-22, 2027 (no second month)")
check(dates("Georgia National Fair, October 8th-18th, 2026 Fair Hours")
      == (date(2026, 10, 8), date(2026, 10, 18)), "October 8th-18th, 2026 (ordinals)")
check(dates("Join Us at The Big E! Sept. 18-Oct. 4, 2026 The Big E is the largest")
      == (date(2026, 9, 18), date(2026, 10, 4)), "Sept. 18-Oct. 4, 2026 (crosses a month)")
check(dates("AUGUST 12 -21, 2027 Days Hours Minutes BUY TICKETS")
      == (date(2027, 8, 12), date(2027, 8, 21)), "AUGUST 12 -21, 2027 (upper case, odd spacing)")
# Minnesota and New York both interject a holiday; Oregon interjects a weekday.
check(dates("Skip to content Aug. 27 - Labor Day, Sept. 7, 2026 Open mobile menu")
      == (date(2026, 8, 27), date(2026, 9, 7)), "Aug. 27 - Labor Day, Sept. 7, 2026")
check(dates("THE OREGON STATE FAIR Friday, August 28 - Monday, September 7, 2026 BUY")
      == (date(2026, 8, 28), date(2026, 9, 7)), "August 28 - Monday, September 7, 2026")

# THE COMMA IN THE INTERJECTION IS LOAD-BEARING. Without requiring it the group
# is greedy enough to eat the END MONTH: "July 28 - August 8" became July 28 to
# July 8, which reverses and is dropped, so Ohio and Wisconsin both reported "no
# date range on the page" while the range was in the title.
check(dates("Come on down to the Wisconsin State Fair from August 5-15, 2027!")
      == (date(2027, 8, 5), date(2027, 8, 15)),
      "an end month is not swallowed as an interjection")

# ---------------------------------------------------- 2. the four rules
# Rule 1 — a sale window is not a fair. statefairva.org, live.
check(dates("Advance Tickets on Sale Now from September 1 - 22, 2026! Hours & Directions")
      is None, "an advance-ticket window is refused")
check("too long" in why("Advance Tickets on Sale Now from September 1 - 22, 2026!"),
      "...and 22 days is over the three-week ceiling as well")
check(dates("Save on deals: September 5 - 12, 2026 discount tickets") is None,
      "a discount window is refused on its wording alone")
# Rule 2 — a different event with real dates. kystatefair.org, live.
check(dates("World's Championship Horse Show August 22-29, 2027 The World's") is None,
      "the horse show is not the fair")
check("horse show" in why("World's Championship Horse Show August 22-29, 2027"),
      "...and the reason says which word did it")
# Rule 3 — length.
check(dates("The season runs June 1 - August 30, 2027 at the fairgrounds") is None,
      "a 91-day season is not a fair")
check(dates("State Fair of Louisiana October 29 - November 15, 2026 Hours of Operation")
      == (date(2026, 10, 29), date(2026, 11, 15)),
      "...but 18 days is, and Louisiana's is the longest that matters")
# Rule 4 — over is over, and this is what makes the scrape self-correcting.
check(dates("Buy Tickets Aug 13 - 23, 2026 Fair Spirit August 13-23, 2026") is None,
      "a fair that finished last month contributes nothing")
check(dates("DELAWARE STATE FAIR JULY 23 - August 1, 2026 "
            "We Will See You in 2027 JULY 22-31, 2027 choose")
      == (date(2027, 7, 22), date(2027, 7, 31)),
      "a page showing last year's and next year's takes next year's")

# THE DISQUALIFYING WORDS HAVE TO BE CLOSE. At 90 characters a fair's own hero
# sat within reach of its navigation and two correct answers were thrown away.
check(dates("Deals &amp; Games Skip to content Days Hours August 28 - September 7, 2026 "
            "Plan Your Visit America 250") == (date(2026, 8, 28), date(2026, 9, 7)),
      "a Deals menu item does not disqualify Colorado's dates")
check(dates("Special Events Where to Stay Building Hours - Sept. 11 - 20, 2026 "
            "Group Tickets PARTICIPATE Commercial Vendor application")
      == (date(2026, 9, 11), date(2026, 9, 20)),
      "...nor a vendor application Kansas's")

# ---------------------------------------------------- 3. the year in the wrong place
# iowastatefair.org publishes a table of future dates with the YEAR LEADING each
# row, so read left to right "2027 Aug 12-22 2028" is the 2027 fair wearing
# 2028's label. The rule is not to prefer the leading year — a page can say
# "Thank you for 2026! August 5-15, 2027" and mean the trailing one — it is to
# refuse when two years are in play.
check(dates("the latest are August 13-23. YEAR FAIR DATES 2027 Aug 12-22 2028 "
            "Aug 10-20 2029 Aug 9-19 Fair Dates") is None,
      "a table of years does not yield a fair a year out")
check(dates("Thank you for 2026! August 5-15, 2027 See you there") is None,
      "...and a disagreeing leading year is refused rather than guessed")
check(dates("Ohio State Fair 2027 | July 28 - August 8, 2027") is not None,
      "an AGREEING leading year is not ambiguous and passes")
check(dates("We Will See You in 2027 JULY 22-31, 2027 choose") is not None,
      "...as does Delaware's, which reads the same way")

# ---------------------------------------------------- 4. arithmetic
check(F.parse_ranges("December 30 - January 4, 2027")[0][0] == date(2026, 12, 30),
      "a range crossing new year starts in the year before")
check(F.parse_ranges("February 30 - March 2, 2027") == [],
      "February 30 is somebody's typo, not a date")
check(F.parse_ranges("August 22 - August 2, 2027") == [],
      "a range that ends before it starts is not a range")
check(dates("The fair returns August 5-15, 2029") is None
      and "too far out" in why("The fair returns August 5-15, 2029"),
      "a date three years out is noise, not a plan")

# ---------------------------------------------------- 5. the row it writes
site = {"state": "MN", "name": "Minnesota State Fair",
        "url": "https://www.mnstatefair.org/", "city": "Falcon Heights",
        "region": "Minnesota", "country": "United States",
        "lat": 44.985775, "lon": -93.168436}
ev = F.to_event(site, date(2026, 8, 27), date(2026, 9, 7))
check(ev.start_local == "2026-08-27" and ev.end_local == "2026-09-07",
      "the row spans the whole fair, not its first day")
check("T" not in (ev.start_local or ""),
      "...and carries no clock, because a fair's gates open at a different hour daily")
check(ev.name == "Minnesota State Fair 2026", "the year is in the name")
check(ev.source_id == "mn-2026",
      "...and in the identity, so next year is a new row and not a moved one")
check(F.to_event(site, date(2027, 8, 26), date(2027, 9, 6)).source_id == "mn-2027",
      "...which is what stops one year overwriting the other")
check(ev.coords_exact is True and abs(ev.latitude - 44.9858) < 0.01,
      "the fairground's surveyed point is used rather than geocoding a fair's name")
check(ev.ticket_url == site["url"], "the row links to the fair's own site")
check(ev.category == "community" and "market" in ev.categories,
      "community first, and it reaches the market lens too")

# ---------------------------------------------------- 6. which page gets read
# A fair site's navigation is thirty links wide and the dates are on exactly one
# of them. Unranked, the first three matches were Buy Tickets, Concerts and
# Vendors, and General Information — where wistatefair.com states its dates in a
# sentence — was never reached.
links = F._hinted_links(
    '<a href="/tickets">Buy Tickets</a><a href="/concerts">Concerts</a>'
    '<a href="/fair/general-information">General Information</a>'
    '<a href="/dates">Fair Dates</a><a href="https://other.test/dates">Elsewhere</a>',
    "https://x.test/")
check(links and links[0].endswith("/dates"), "a Dates link is read first")
check(len(links) > 1 and "general-information" in links[1],
      "...then General Information")
check(all("other.test" not in u for u in links),
      "and never another host, however well its link is named")

# ---------------------------------------------------- report
bad = [l for ok, l in checks if not ok]
for ok, l in checks:
    print(("ok    " if ok else "FAIL  ") + l)
print(f"\n{len(checks)} cases, {len(bad)} failed")
sys.exit(1 if bad else 0)
