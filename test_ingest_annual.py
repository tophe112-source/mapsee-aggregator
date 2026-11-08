#!/usr/bin/env python3
"""
test_ingest_annual.py - an annual event whose own site states its date in prose.

The Short Run fixtures are short quotes off shortrun.org's own pages as read on
2026-11-07 (the homepage hero, three of its post bylines, two lines of its
programming page), trimmed to the sentences that decide something. The other
strings are adversarial: Short Run's phrasing, with a deadline, a fee, a recap
or a load-in where the festival's date would be. Nothing touches the network;
robots.txt, Crawl-delay and refusals are proved against a fake session and a
fake clock.

    python test_ingest_annual.py
"""
import json
import os
import re
import sys
import tempfile
import types
from datetime import date

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

import mapsee_ingest_annual as A
import mapsee_ingest_fairs as F
import robots_txt
from mapsee_ingest import EventStore

checks = []


def check(cond, label):
    checks.append((bool(cond), label))


# The MECHANISM cases below read with the organiser's name, so a short string
# can stand for a whole page; SITE and section 2b read with the config's own
# name_rx, which names the festival and not the organiser.
NAME = re.compile(r"short\s+run", re.I)
CONFIG_RX = r"short\s+run\s+comix"
CONFIG_NAME = re.compile(CONFIG_RX, re.I)

# The homepage hero, as the raw HTML reads (2026-11-07).
HERO = ('<h1 class="page-title">Short Run Comix Festival</h1>\n'
        '<div class="taxonomy-description"><p>Our 14th annual festival is coming up '
        '<strong>Saturday, November 7th, 2026</strong> at Seattle Design Center. '
        '11am &#8211; 6 pm. FREE!</p>\n<p>Add yourself to our mailing list for deadline '
        'reminders and updates on upcoming local programming.</p>\n</div>')
# Three of the homepage's twenty WordPress bylines, each in its own entry-meta
# block, with the post excerpts that sit above them.
BYLINES = (
    '<h2>Table Map!</h2><p><a class="more-link">Read more&#8230;</a></p>'
    '<div class="entry-meta"><span class="posted-on">Posted on <time>October 28, 2026</time>'
    '<time>October 29, 2026</time></span><span class="byline"> by kelly</span></div>'
    '<span class="cat-links">Categories: <a>Short Run Comix Festival</a></span>'
    '<p>it&#8217;s more than worth it to visit Short Run on <strong>Saturday, Nov. 7th.</strong> '
    'Come join the DIY takeover of the Seattle Design Center in Georgetown! Read more&#8230;</p>'
    '<div class="entry-meta"><span class="posted-on">Posted on <time>September 27, 2026</time>'
    '<time>November 6, 2026</time></span><span class="byline"> by kelly</span></div>'
    '<span class="cat-links">Categories: <a>Short Run Comix Festival</a></span>'
    '<h2>Special Guests !!</h2><p>We can&#8217;t wait to welcome these incredible artists to '
    'Short Run on Nov. 7th! Read more&#8230;</p>'
    '<div class="entry-meta"><span class="posted-on">Posted on <time>July 10, 2026</time>'
    '<time>August 3, 2026</time></span><span class="byline"> by kelly</span></div>')
HOME = HERO + BYLINES

SITE = {"key": "short-run", "name": "Short Run Comix Festival", "name_rx": CONFIG_RX,
        "url": "https://shortrun.org/", "shape": "day", "category": "arts",
        "venue": "Seattle Design Center", "address": "5701 6th Ave S, Seattle, WA 98108",
        "city": "Seattle", "region": "Washington", "country": "United States",
        "lat": 47.551473, "lon": -122.328325, "timezone": "America/Los_Angeles",
        "months": [10, 11]}


def read(html, today, **kw):
    kw.setdefault("name_rx", NAME)
    return A.read_page(html, today, kw.pop("shape", "day"), **kw)


def text_case(s, today, **kw):
    return read("<p>" + s + "</p>", today, **kw)


# ---------------------------------------------------- 1. the hero, as published
r = read(HERO, date(2026, 11, 1))
check((r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 7)),
      "Short Run's hero: Saturday, November 7th, 2026")
check(r.clock == (11 * 60, 18 * 60), "...11am &#8211; 6 pm decoded and read as 11:00-18:00")
check(r.free == "FREE!" and r.free_where == "after the date", "...FREE! right after the date")
check(r.description == ("Our 14th annual festival is coming up Saturday, November 7th, 2026 "
                        "at Seattle Design Center. 11am – 6 pm. FREE!"),
      "the description is the hero paragraph word for word, and nothing after it")
ev = A.to_event(SITE, r)
check(ev.start_local == "2026-11-07T11:00:00" and ev.end_local == "2026-11-07T18:00:00",
      "the row carries the publisher's clock as local time")
check(ev.start_utc == "2026-11-07T19:00:00Z" and ev.end_utc == "2026-11-08T02:00:00Z"
      and ev.timezone == "America/Los_Angeles",
      "...stamped with the entry's zone (PST, after the November change), not left naive")
check(ev.source == "annual" and ev.source_id == "short-run-2026"
      and ev.name == "Short Run Comix Festival 2026",
      "source 'annual', identity '<key>-<year>', name '<name> <year>'")
check(A.to_event(SITE, read(HERO.replace("November 7th, 2026", "November 6th, 2027"),
                            date(2027, 11, 1))).source_id == "short-run-2027",
      "...so next year's edition is a new row, not this one moved")
check("free" not in ev.name.lower() and "FREE!" in (ev.description or ""),
      "FREE is the publisher's word in the description, never ours in the title")
check(ev.source_details == {"free": True, "offer": {"price": "0"}} and ev.admission_checked,
      "...and the admission fact the waterfront adapter writes for its '| Free'")
check(ev.address == "5701 6th Ave S" and ev.postal_code == "98108" and ev.city == "Seattle",
      "the config's postal address is split, so the sync's pin line names Seattle once")
check(ev.coords_exact and ev.latitude == 47.551473 and ev.category == "arts",
      "the surveyed point is kept and the category comes from the config")
check(A.read_page(HERO, date(2026, 11, 1), "day", NAME).clock is not None
      and F.pick_fair_dates(F.text_of(HERO), date(2026, 11, 1))[0] is None,
      "the fairs adapter reads no single day: 'no date range' on the same hero")

# ---------------------------------------------------- 2. the byline, rule on and off
# Every post on the homepage ends "Posted on <date> <last edit>". On the day of
# an edit that second date is TODAY, which every other rule here would keep.
for today in (date(2026, 10, 29), date(2026, 11, 6), date(2026, 11, 7)):
    for bylines in (True, False):
        got = read(HOME, today, bylines=bylines)
        check(got.start == date(2026, 11, 7) and got.clock == (660, 1080),
              f"homepage on {today}, byline rule {'on' if bylines else 'OFF'}: the hero still wins")
byline_only = read(BYLINES, date(2026, 11, 6))
check(not byline_only.start or byline_only.start == date(2026, 11, 7),
      "a byline dated today is never the festival with the rule on")
check(any(v == "byline" for c, v in read(BYLINES, date(2026, 11, 6)).log
          if c.start == date(2026, 11, 6)),
      "...and the reason it gives is 'byline'")
# Without the name rule and without the byline rule, the byline wins: this is
# the review's finding, and why shape 'day' REQUIRES name_rx.
nameless = A.read_page(HOME, date(2026, 11, 6), "day", None, bylines=False)
check(nameless.start == date(2026, 11, 6),
      "with neither rule, 'Posted on ... November 6, 2026' beats the hero (why both exist)")
# A NAME NEVER VOUCHES ACROSS ANOTHER DATE. "...to Short Run on Nov. 7th! Read
# more..." ends 35 characters before "July 10, 2026" in "Posted on July 10,
# 2026" (measured 2026-11-08 in page_text of HOME and of the saved homepage
# alike); before July 10, with the byline rule off, that byline was the
# nearest named date.
check(read(HOME, date(2026, 7, 1), bylines=False).start == date(2026, 11, 7),
      "the name above 'Nov. 7th' does not vouch for the byline after it")
check(text_case("Short Run 2027 call for exhibitors! Applications are due Friday, March 5th, "
                "2027. The festival returns Saturday, November 6th, 2027 at Seattle Design "
                "Center.", date(2026, 11, 8)).start == date(2027, 11, 6),
      "...but inside one paragraph a date between does not matter")

# ---------------------------------------------------- 2b. name_rx names the EVENT
# Every post on an organiser's homepage says the organiser's name, and the
# nearest named date wins. With `short\s+run` one dated post beat the hero
# (measured on the saved homepage with each of these inserted, 2026-11-08).
POSTS = [
    ("<p>Kick off Short Run at the Marathon Art Show, Friday, November 6th, 2026, 6 - 9 pm. FREE!</p>",
     date(2026, 11, 6)),
    ("<p>The Short Run Spring Market is Saturday, May 15th, 2027 at Vermillion, 12 - 5 pm. FREE!</p>",
     date(2027, 5, 15)),
    ("<p>The Public Life of Comics, presented with Short Run, Sunday, November 8th, 2026, 2 - 3:30 pm.</p>",
     date(2026, 11, 8)),
]
for post, theirs in POSTS:
    check(read(HERO + post, date(2026, 10, 15)).start == theirs,
          f"(the danger) with the organiser's name, a post's {theirs} beats the hero")
    got = read(HERO + post, date(2026, 10, 15), name_rx=CONFIG_NAME)
    check(got.start == date(2026, 11, 7) and got.clock == (660, 1080) and got.free == "FREE!",
          f"with the festival's own name the hero wins over that post ({theirs})")
RESIDENCY = ("<p>Short Run&#8217;s 11th annual Trailer Blaze comics residency runs "
             "April 4 &#8211; 18, 2027.</p>")
check(read(HERO + RESIDENCY, date(2026, 11, 8)).start == date(2027, 4, 4),
      "(the danger) the day after the festival, the residency became the festival with 'short\\s+run'")
check(read(HERO + RESIDENCY, date(2026, 11, 1), name_rx=CONFIG_NAME).start == date(2026, 11, 7),
      "the residency beside the hero: the hero, on 2026-11-01, with the config's name_rx")
got = read(HERO + RESIDENCY, date(2026, 11, 8), name_rx=CONFIG_NAME)
check(got.start is None and "past" in got.why,
      "...and on 2026-11-08 nothing: the residency is not the festival")
for today in (date(2026, 7, 1), date(2026, 10, 29), date(2026, 11, 6), date(2026, 11, 7)):
    for bylines in (True, False):
        got = read(HOME, today, bylines=bylines, name_rx=CONFIG_NAME)
        check(got.start == date(2026, 11, 7) and got.clock == (660, 1080),
              f"homepage with the config's name_rx on {today}, byline rule "
              f"{'on' if bylines else 'OFF'}: the hero")

# ---------------------------------------------------- 2c. a byline <time> by its markup
# The review's a8.py: a category link names the festival 12 characters before
# a theme's "updated" <time>, with no "Posted"/"updated" WORD in front of the
# date. On that day it was kept as the festival, all day.
POST_UPDATED = ('<article><h2>Thank you!</h2><p>See you next year.</p>'
                '<div class="entry-meta"><a rel="category">Short Run Comix Festival</a> &middot; '
                '<time class="updated">November 20, 2026</time> by kelly</div></article>')
for months in (None, [10, 11]):
    got = read(HERO + POST_UPDATED, date(2026, 11, 20), name_rx=CONFIG_NAME, months=months)
    check(got.start is None and any(v == "byline" for c, v in got.log if c.start == date(2026, 11, 20)),
          f"a8: <time class=\"updated\"> after a category link is a byline (months {months})")
for attr in ('class="entry-date published"', "itemprop='dateModified'", "itemprop=datePublished"):
    got = read(HERO + POST_UPDATED.replace('class="updated"', attr), date(2026, 11, 20),
               name_rx=CONFIG_NAME)
    check(got.start is None, f"...and so is <time {attr}>")
got = read(HERO + POST_UPDATED, date(2026, 11, 20), name_rx=CONFIG_NAME, bylines=False)
check(got.start == date(2026, 11, 20), "(the danger) with the byline rule off it is the festival")
got = read("<h1>Short Run Comix Festival</h1><p>Returns <time datetime=\"2027-11-06\">Saturday, "
           "November 6th, 2027</time> at Seattle Design Center.</p>", date(2026, 11, 20),
           name_rx=CONFIG_NAME)
check(got.start == date(2027, 11, 6), "a <time> with no byline class is the event's own date, kept")
marks = []
check(A.page_text(HOME, marks) == A.page_text(HOME) and marks == [],
      "page_text's text is the same with marks asked; the fixture's bare <time>s carry none")
for post in (POST_UPDATED, POST_UPDATED.replace('class="updated">November 20, 2026',
                                                 'class="updated">\n <span>November 20, 2026</span> ')):
    marks = []
    t = A.page_text(post, marks)
    check(t == A.page_text(post) and len(marks) == 1 and t[marks[0]:].startswith("November 20"),
          f"...and a byline <time>'s mark points at its date, the text unchanged ({len(post)} chars)")

# ---------------------------------------------------- 2d. the event's own months
# Once the edition is past, every other date near the name is a candidate for
# the next one. The review's a7.py strings, beside the real hero on
# 2026-11-20, each became the 2027 festival; the deny list cannot know every
# phrasing, the curated months (annual_sources.json: [10, 11]) can.
A7 = [
    ("<p>Exhibitor applications for Short Run Comix Festival 2027 are open until Friday, "
     "March 5th, 2027.</p>", date(2027, 3, 5)),
    ("<p>Submit your work to the Short Run Comix Festival anthology by Friday, March 5th, "
     "2027.</p>", date(2027, 3, 5)),
    ("<p>Want a table at Short Run Comix Festival 2027? Apply before Friday, March 5th, "
     "2027!</p>", date(2027, 3, 5)),
    ("<p>Short Run Comix Festival exhibitor sign-ups close Friday, March 5th, 2027.</p>",
     date(2027, 3, 5)),
    ("<p>Volunteer for Short Run Comix Festival! Orientation is Saturday, October 30th, "
     "2027.</p>", None),
    ("<p>Short Run Comix Festival Spring Market: Saturday, May 15th, 2027 at Washington "
     "Hall.</p>", date(2027, 5, 15)),
]
for post, danger in A7:
    got = read(HERO + post, date(2026, 11, 20), name_rx=CONFIG_NAME, months=SITE["months"])
    check(got.start is None and "past 1 (newest 2026-11-07)" in got.why,
          f"a7 refused with the festival's months: {post[3:60]}...")
    if danger:
        check(read(HERO + post, date(2026, 11, 20), name_rx=CONFIG_NAME).start == danger,
              f"(the danger) without months it was the {danger} festival")
check("not-this-event:orientation" in read(HERO + A7[4][0], date(2026, 11, 20), name_rx=CONFIG_NAME,
                                           months=SITE["months"]).why,
      "the orientation is inside the months (a Saturday, October 30th): refused by its word")
got = read(HERO + A7[5][0], date(2026, 11, 1), name_rx=CONFIG_NAME, months=SITE["months"])
check(got.start == date(2026, 11, 7) and got.clock == (660, 1080) and got.free == "FREE!",
      "inside the months the hero still reads, clock and FREE with it")
# THE DAILY SWEEP of the homepage fixture: the hero every day up to and
# including the festival, and nothing after it into 2027, with the config's
# name_rx and months (the saved homepage gives the same, measured 2026-11-08:
# 311 of 311 days, then 419 of 419 empty).
sweep, d = {}, date(2026, 1, 1)
while d <= date(2027, 12, 31):
    got = read(HOME + "".join(p for p, _ in A7), d, name_rx=CONFIG_NAME, months=SITE["months"])
    sweep.setdefault(d <= date(2026, 11, 7), set()).add((got.start, got.clock, got.free))
    d = date.fromordinal(d.toordinal() + 1)
check(sweep[True] == {(date(2026, 11, 7), (660, 1080), "FREE!")},
      "daily sweep 2026-01-01..11-07, the a7 posts on the page: the hero, its clock and FREE, every day")
check(sweep[False] == {(None, None, None)}, "...and from 2026-11-08 to 2027-12-31: nothing")

# ---------------------------------------------------- 3. the weekday
r = text_case("Short Run returns Saturday, November 7th, 2027!", date(2026, 11, 8))
check(r.start is None and "weekday disagrees" in r.why,
      "'Saturday, November 7th, 2027' is a Sunday: refused, not guessed")
check(text_case("Short Run is Saturday & Sunday, November 7 & 8, 2026.",
                date(2026, 11, 1)).end == date(2026, 11, 8),
      "a weekday pair before a day pair checks both days")
r = text_case("Please visit them at Short Run on Saturday, Nov. 1st.", date(2026, 10, 1))
check(r.start is None and "past" in r.why,
      "a yearless 'Saturday, Nov. 1st' resolves by its weekday to 2025, which is over")
r = text_case("We can't wait to welcome these artists to Short Run on Nov. 7th!", date(2026, 11, 1))
check(r.start is None and "no year and no weekday" in r.why,
      "a yearless date with no weekday is not believed")

# ---------------------------------------------------- 3b. two dated days are one event
# Read as two singles, each lost something (the review's a5.py/a6.py, today
# 2026-10-01): the shared trailing year sat on Sunday alone, so the festival
# started on its second day with the clock; two full dates lost the end.
r = text_case("Short Run Comix Festival returns Saturday, November 7 &amp; Sunday, November 8, "
              "2026 at Seattle Design Center, 11am &#8211; 6pm both days.", date(2026, 10, 1),
              name_rx=CONFIG_NAME)
check((r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 8)) and r.clock is None,
      "'Saturday, November 7 & Sunday, November 8, 2026': both days, all day, from the 7th")
r = text_case("Short Run Comix Festival: November 7, 2026 &#8211; November 8, 2026.", date(2026, 10, 1),
              name_rx=CONFIG_NAME)
check((r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 8)),
      "'November 7, 2026 – November 8, 2026': the end is kept")
r = text_case("Short Run Comix Festival: December 31, 2026 &#8211; January 1, 2027.", date(2026, 10, 1),
              name_rx=CONFIG_NAME)
check((r.start, r.end) == (date(2026, 12, 31), date(2027, 1, 1)),
      "'December 31, 2026 – January 1, 2027': across the year, not 'two years in play'")
r = text_case("Short Run Comix Festival: Thursday, December 31 through Friday, January 1, 2027.",
              date(2026, 10, 1), name_rx=CONFIG_NAME)
check((r.start, r.end) == (date(2026, 12, 31), date(2027, 1, 1)),
      "...and a yearless first day takes the trailing year less one when the month wraps")
r = text_case("Short Run Comix Festival is Saturday, November 7th, 2026. Sunday, November 8th, 2026 "
              "is for the exhibitors' own picnic.", date(2026, 10, 1), name_rx=CONFIG_NAME)
check((r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 7)) and len(r.log) == 2,
      "two dates in two sentences are NOT merged: the festival is one day")
r = text_case("Short Run Comix Festival returns Friday, November 7 &amp; Saturday, November 8, 2026.",
              date(2026, 10, 1), name_rx=CONFIG_NAME)
check(r.start is None and "weekday disagrees" in r.why,
      "a merged pair still checks each day's weekday (2026-11-07 is a Saturday)")

# ---------------------------------------------------- 4. dates that are not the event
DENY = [
    ("Short Run exhibitors: table fee payments open June 1 - 15, 2027 through our shop.",
     date(2027, 5, 1), "table fee"),
    ("Short Run exhibitors: table fees are due by Sunday, August 15th, 2027.",
     date(2027, 5, 1), "table fees"),
    ("Short Run 2026 recap: thank you for joining us Saturday, November 7th, 2026!",
     date(2026, 11, 7), "recap"),
    ("Exhibitor load-in and set-up is at 10 am on Saturday, November 7th, 2026 at Short Run.",
     date(2026, 11, 1), "load-in"),
    ("Tickets for the Short Run afterparty go on sale Monday, October 5th, 2026.",
     date(2026, 10, 1), "on sale"),
    ("Short Run submissions close Friday, July 10th, 2026 for the anthology.",
     date(2026, 7, 1), "submissions close"),
    ("Short Run booth fees due on Monday, September 14th, 2026.",
     date(2026, 9, 1), "booth fees"),
]
for s, today, word in DENY:
    r = text_case(s, today)
    check(r.start is None and f"not-this-event:{word}" in r.why, f"refused for {word!r}: {s[:48]}...")
check(F.pick_fair_dates("Short Run exhibitors: table fee payments open June 1 - 15, 2027",
                        date(2027, 5, 1))[0] is not None,
      "...a table-fee window the fairs' narrower list would have kept")
r = text_case("Short Run is Saturday, November 7th, 2026: exhibitor load-in 8am-10am, "
              "public 11am - 6pm.", date(2026, 11, 1))
check(r.start == date(2026, 11, 7) and r.clock == (660, 1080),
      "load-in AFTER the date is logistics, not a refusal; its clock is skipped for the public's")
# AFTER THE DATE, ONLY ITS OWN PARAGRAPH when that paragraph goes on. The real
# hero's next paragraph is "Add yourself to our mailing list for deadline
# reminders", its "deadline" 83 characters after the date (a 38-character margin).
H27 = "<h1>Short Run Comix Festival</h1><p>Our 15th annual festival is coming up "
r = read(H27 + "Saturday, November 6th, 2027. FREE!</p><p>Join our mailing list for deadline "
         "reminders.</p>", date(2026, 11, 8))
check(r.start == date(2027, 11, 6) and r.free == "FREE!",
      "a 'deadline' in the NEXT paragraph does not refuse the hero's date")
r = read(H27 + "Saturday, November 6th, 2027, 11am - 6pm</p><p>Table fees and the exhibitor FAQ</p>",
         date(2026, 11, 8))
check(r.start == date(2027, 11, 6) and r.clock == (660, 1080),
      "...nor 'Table fees' in the next paragraph")
r = read("<h3>Short Run application deadline</h3><p>Friday, March 5th, 2027</p>", date(2026, 11, 8))
check(r.start is None and "not-this-event:deadline" in r.why,
      "a heading BEFORE the date still refuses it")
r = read("<h2>Short Run 2027</h2><h3>Saturday, March 6th, 2027</h3><p>Application deadline for "
         "exhibitors</p>", date(2026, 11, 8))
check(r.start is None and "not-this-event:" in r.why,
      "a date that ENDS its block is labelled by the next one: still refused")

# THE BYLINE, ANCHORED: the word, then the date (or a first date) and nothing
# else. A 26-character gap refused these three announcements as 'byline'.
for s, want in (("Schedule published! Short Run is Saturday, November 6th, 2027 at Seattle Design "
                 "Center. 11am - 6pm. FREE!", date(2027, 11, 6)),
                ("UPDATED: Short Run moves to Saturday, November 13th, 2027. FREE!", date(2027, 11, 13)),
                ("Map updated for Short Run, Saturday, November 6th, 2027!", date(2027, 11, 6))):
    r = text_case(s, date(2026, 11, 8))
    check(r.start == want, f"an announcement is not a byline: {s[:44]}...")
for s in ("Short Run news. Last updated: November 6, 2026",
          "Short Run news. Posted on October 28, 2026 November 6, 2026",
          "Short Run news. Posted by kelly on November 6, 2026",
          "Short Run news. Published November 6, 2026"):
    r = text_case(s, date(2026, 11, 6))
    check(r.start is None and "byline" in r.why, f"...while a real byline still is: {s[16:]}")

# ---------------------------------------------------- 5. FREE only where it belongs
r = text_case("Short Run is Saturday, November 7th, 2026, 11am - 6pm. Parking is free. Tickets $10.",
              date(2026, 11, 1))
check(r.start and r.free is None, "'Parking is free.' is a perk, not free admission")
r = text_case("Short Run is Saturday, November 7th, 2026, 11am - 6pm. Members get in free! "
              "$10 otherwise.", date(2026, 11, 1))
check(r.start and r.free is None, "'Members get in free!' is not free")
r = text_case("Short Run is Saturday, November 7th, 2026, 11am - 6pm. Free parking. Free admission.",
              date(2026, 11, 1))
check(r.free == "Free admission", "...but a perk beside free admission does not hide it")
r = text_case("Short Run is Saturday, November 7th, 2026, $5. Friday, Nov. 6th 6 - 9 pm: pre-party. "
              "FREE!", date(2026, 11, 1))
check(r.start == date(2026, 11, 7) and r.free is None and r.clock is None,
      "the pre-party's clock and FREE! are not read across its date")
r = read("<title>Short Run - Free admission!</title>" + "<p>Short Run is Saturday, November 7th, "
         "2026.</p>", date(2026, 11, 1))
check(r.free and r.free_where == "headline" and "Free admission" in r.description,
      "FREE in the page's own headline counts, and is quoted into the description")
# NOT FREE ADMISSION (the review's a1.py/a2.py/a4.py): each of these set
# {'free': True, 'offer': {'price': '0'}} beside a ticket price.
SR_DAY = ("<h1>Short Run Comix Festival</h1><p>Saturday, November 7th, 2026 at Seattle Design "
          "Center. 11am &#8211; 6pm. ")
NOT_FREE = ["Tickets $10. A scent-free event.", "Step-free entrance on 6th Ave S. Tickets $10.",
            "Tickets $10; barrier-free entry.", "Tickets $10 at the door. Alcohol-free.",
            "Tickets $10; children 12 and under free."]
for tail in NOT_FREE:
    r = read(SR_DAY + tail + "</p>", date(2026, 10, 1), name_rx=CONFIG_NAME)
    ev = A.to_event(SITE, r)
    check(r.start == date(2026, 11, 7) and r.free is None and ev.source_details == {},
          f"not free beside a price: {tail!r}")
# ...and each rule alone, with no price in the paragraph to veto it.
for tail in ("A scent-free event.", "Step-free entrance on 6th Ave S.", "Barrier-free entry.",
             "Alcohol-free.", "Children 12 and under free.", "Kids under 12 get in free.",
             "Ages 5 and under free."):
    r = read(SR_DAY + tail + "</p>", date(2026, 10, 1), name_rx=CONFIG_NAME)
    check(r.start and r.free is None and r.free_where == "",
          f"a hyphenated compound or a child's free is not free admission: {tail!r}")
for tail, price in (("Free entry to the hall. Workshop tickets &pound;5.", "£5"),
                    ("FREE! Tickets: 12 at the door for the party.", "Tickets: 12"),
                    ("Free admission; a 10 € suggested donation.", "10 €")):
    r = read(SR_DAY + tail + "</p>", date(2026, 10, 1), name_rx=CONFIG_NAME)
    check(r.start and r.free is None and price in r.free_where,
          f"a price in the date's paragraph vetoes FREE: {tail!r}")
r = read("<title>Short Run Comix Festival - Free admission!</title><p>Short Run Comix Festival is "
         "Saturday, November 7th, 2026. Tickets $10.</p>", date(2026, 11, 1), name_rx=CONFIG_NAME)
check(r.start and r.free is None, "...and a headline's FREE beside a priced date paragraph")
r = read(SR_DAY + "FREE! Tickets for the 2 pm panel are at the door.</p>", date(2026, 10, 1),
         name_rx=CONFIG_NAME)
check(r.free == "FREE!", "a ticket word with a clock after it is not a price")

# ---------------------------------------------------- 6. the name rule on a programming page
SCHED = ("<p>Friday, Nov. 6th</p><p>6:00 &#8211; 9:00 pm FREE</p>"
         "<p>Marathon Art Show and Pre-Fest Reception</p>"
         "<p>Sunday, Nov. 8th</p><p>2:00 &#8211; 3:30 pm FREE</p>"
         "<p>The Public Life of Comics presentation &amp; panel discussion</p>")
r = read(SCHED, date(2026, 10, 31))
check(r.start is None and "event not named near the date" in r.why,
      "the pre-party and Sunday's panel carry dates, clocks and FREE, but not the name")
check(A.read_page(SCHED, date(2026, 10, 31), "day", None).start == date(2026, 11, 6),
      "...which, without the name rule, would have been the festival")

# ---------------------------------------------------- 7. shape 'range'
DE = ("DELAWARE STATE FAIR JULY 23 - August 1, 2026 We Will See You in 2027 "
      "JULY 22-31, 2027 choose")
r = A.read_page("<p>" + DE + "</p>", date(2026, 9, 4), "range")
check((r.start, r.end) == (date(2027, 7, 22), date(2027, 7, 31))
      and (r.start, r.end) == F.pick_fair_dates(DE, date(2026, 9, 4))[0][:2],
      "shape 'range' answers what the fairs adapter answers")
check(A.to_event(dict(SITE, shape="range"), r).start_local == "2027-07-22"
      and A.to_event(dict(SITE, shape="range"), r).end_local == "2027-07-31",
      "...as an all-day span, no clock")
r = A.read_page("<p>Upcoming: Sun, Nov 08 Gem and Mineral Show</p>", date(2026, 11, 7), "range")
check(r.start is None, "a single day on a range-shape page is not a candidate (Maryland's 'Sun, Nov 08')")
r = A.read_page("<p>Bumbershoot returns August 29 - 31, 2027 to Seattle Center, three days of "
                "music and arts. Passes go on sale Saturday, March 6th, 2027.</p>",
                date(2026, 11, 8), "range", re.compile("bumbershoot", re.I))
check((r.start, r.end) == (date(2027, 8, 29), date(2027, 8, 31)) and r.clock is None,
      "a range with a name rule: the span, all day, the sale day not a candidate")
r = A.read_page("<p>Bumbershoot returns August 29 - 31, 2027. Bumbershoot passes on sale soon.</p>",
                date(2026, 11, 8), "range", re.compile("bumbershoot", re.I))
check(r.start is None and "not-this-event:on sale" in r.why,
      "...and the fairs' 45-character sale rule still holds on a range")
r = text_case("Short Run is November 7-8, 2026 at Seattle Design Center.", date(2026, 11, 1))
check((r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 8)) and r.clock is None,
      "shape 'day' still reads a range, as all-day")
check(A.SINGLE_RX.search("Short Run, Nov 7-8") is None
      and A.SINGLE_RX.search("Short Run, November 7th - 11am to 6pm").group("d1") == "7",
      "a yearless 'Nov 7-8' is not the single day Nov 7; 'November 7th - 11am' still is")
r = A.read_page("<p>Short Run Nov 7-8 2026</p>", date(2026, 11, 1), "day", NAME)
check((r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 8)),
      "...and with a year it is RANGE_RX's two days")

# ---------------------------------------------------- 8. over is over
r = read(HOME, date(2026, 11, 8))
check(r.start is None and "past" in r.why and "newest 2026-11-07" in r.why,
      "the day after, the 2026 edition is refused as past and the verdict says which date")
r = read(HERO, date(2026, 11, 7), now_minute=18 * 60)
check(r.start is None and r.why.startswith("past") and "18:00" in r.why,
      "on the day, once its stated hours have ended, it is over")
check(read(HERO, date(2026, 11, 7), now_minute=17 * 60 + 44).start == date(2026, 11, 7),
      "...and at 17:44 it is still on")
check(read(HERO.replace("2026", "2029"), date(2026, 11, 8)).start is None,
      "an edition more than fifteen months out is noise")
os.environ["MAPSEE_TODAY"] = "20261108"
check(A.now_for(SITE) == (date(2026, 11, 8), None), "MAPSEE_TODAY fixes the day and leaves no clock")
del os.environ["MAPSEE_TODAY"]


# ---------------------------------------------------- 9. robots.txt, Crawl-delay, refusals
class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self.content = text.encode("utf-8")


class Session:
    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append(url)
        got = self.routes.get(url, Resp(404, "not found"))
        if isinstance(got, Exception):
            raise got
        return got


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


ROBOTS = "https://shortrun.org/robots.txt"
SR_ROBOTS = ("User-agent: *\nCrawl-delay: 10\nDisallow: /admin/\nDisallow: /wp-admin/\n"
             "Disallow: /private/\n")


def run(routes, site=SITE, today=date(2026, 11, 1), deadline=None, now_minute=None):
    s, clk = Session(routes), Clock()
    polite = F.Polite(s, min_interval=1.0, sleep=clk.sleep, clock=clk,
                      deadline=None if deadline is None else clk.t + deadline)
    store = EventStore(os.path.join(tempfile.mkdtemp(), "e.json"))
    out = A.ingest_site(store, polite, site, today=today, now_minute=now_minute)
    return out, s, clk, store


out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS), "https://shortrun.org/": Resp(200, HOME)})
check(out["kept"] == 1 and len(store.records) == 1, "robots.txt allows it: one row")
check(s.calls == [ROBOTS, "https://shortrun.org/"], "robots.txt first, then the homepage, nothing else")
check(clk.sleeps == [10.0], "shortrun.org's Crawl-delay: 10 is waited out after the robots.txt read")

site2 = dict(SITE, pages=["https://shortrun.org/festival-programming-schedule/",
                          "https://shortrun.org/private/dates/"])
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS),
                          "https://shortrun.org/": Resp(200, '<p>Add yourself to our mailing list.</p>'
                                                             '<a href="/dates/">Dates</a>'),
                          "https://shortrun.org/festival-programming-schedule/": Resp(200, SCHED)},
                         site=site2)
check(out["kept"] == 0 and "https://shortrun.org/private/dates/" not in s.calls,
      "a configured page robots.txt disallows is never asked for")
check("https://shortrun.org/dates/" not in s.calls, "a link the config does not name is never followed")
check(clk.sleeps == [10.0, 10.0], "...and every page waits the Crawl-delay after the one before")
check("Disallow: /private/" in out["verdict"], "the verdict names the rule that refused")

out, s, clk, store = run({ROBOTS: Resp(200, "User-agent: *\nDisallow: /\n")})
check(out["kept"] == 0 and s.calls == [ROBOTS], "Disallow: / - the homepage is never fetched")
out, s, clk, store = run({ROBOTS: Resp(200, "<html><title>Just a moment...</title>"
                                           "/cdn-cgi/challenge-platform/</html>")})
check(out["kept"] == 0 and s.calls == [ROBOTS] and "challenge" in out["verdict"],
      "a bot challenge on robots.txt is a refusal, not a pass")
out, s, clk, store = run({ROBOTS: TimeoutError("read timed out")})
check(out["kept"] == 0 and s.calls == [ROBOTS] and "unreachable" in out["verdict"],
      "an unreachable robots.txt is RFC 9309's 'assume Disallow: /'")
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS), "https://shortrun.org/": Resp(403, "Forbidden")},
                         site=site2)
check(out["kept"] == 0 and s.calls == [ROBOTS, "https://shortrun.org/"],
      "a 403 is a refusal: not retried, and no configured page is asked either")
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS), "https://shortrun.org/": Resp(429, "slow down")})
check(s.calls.count("https://shortrun.org/") == 1 and "429" in out["verdict"], "a 429 is not retried")
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS),
                          "https://shortrun.org/": Resp(200, "<html><head><title>Just a moment...</title>"
                                                             "</head></html>")})
check(out["kept"] == 0 and "challenge" in out["verdict"], "a challenge page is a refusal")
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS),
                          "https://shortrun.org/": Resp(301, "", {"Location": "/private/home"})})
check(out["kept"] == 0 and "https://shortrun.org/private/home" not in s.calls,
      "a redirect hop is asked of robots.txt like any URL")
out, s, clk, store = run({ROBOTS: Resp(200, "User-agent: *\nCrawl-delay: 600\n"),
                          "https://shortrun.org/": Resp(200, HOME)}, deadline=180)
check(out["kept"] == 0 and s.calls == [ROBOTS] and "deadline" in out["verdict"] and not clk.sleeps,
      "a Crawl-delay longer than the run's budget asks nothing rather than overrun")
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS), "https://shortrun.org/": Resp(200, HOME)},
                         today=date(2026, 11, 8))
check(out["kept"] == 0 and len(store.records) == 0 and "past" in out["verdict"],
      "a live run the day after keeps 0 rows and says 'past'")
# The entry's own months reach the page reader: a Spring Market post after the
# festival is over writes nothing with them, and a 2027 "festival" without.
for site, rows in ((SITE, 0), ({k: v for k, v in SITE.items() if k != "months"}, 1)):
    out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS),
                              "https://shortrun.org/": Resp(200, HOME + A7[5][0])},
                             site=site, today=date(2026, 11, 20))
    check(out["kept"] == rows and len(store.records) == rows,
          f"ingest_site with{'' if 'months' in site else 'out'} the entry's months: {rows} row(s)"
          + ("" if rows == 0 else " (the danger: the Spring Market as the 2027 festival)"))
# ITS OWN HOURS ARE OVER: no later page may bring it back. The programming page
# says "Saturday, Nov. 7th" with no clock; read after the homepage's 18:00 it
# wrote the finished festival as an all-day row with admission {}.
PROG = "https://shortrun.org/festival-programming-schedule/"
SCHED_SAT = ("<h1>Short Run Comix Festival programming</h1><p>Short Run Comix Festival, "
             "Saturday, Nov. 7th</p><p>Panels all day in the main hall.</p>")
site3 = dict(SITE, pages=[PROG])
for minute, rows, label in ((17 * 60 + 44, 1, "17:44: the hero's row, 11:00-18:00"),
                            (18 * 60 + 1, 0, "18:01: nothing, and the programming page is not asked"),
                            (22 * 60 + 17, 0, "22:17: nothing")):
    out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS), "https://shortrun.org/": Resp(200, HOME),
                              PROG: Resp(200, SCHED_SAT)},
                             site=site3, today=date(2026, 11, 7), now_minute=minute)
    recs = list(store.records.values())
    check(len(recs) == rows and PROG not in s.calls
          and all(x["start_local"] == "2026-11-07T11:00:00" for x in recs),
          f"pages after an edition's own hours on its day - {label}")
check(A.read_page(SCHED_SAT, date(2026, 11, 7), "day", CONFIG_NAME).start == date(2026, 11, 7),
      "(the danger) the programming page alone would have read 2026-11-07, all day")
# A refused page whose URL says "deadline" is one refused page, not the end of
# the run: out of time is Polite's own "run deadline" reason, not a substring.
site4 = dict(SITE, pages=["https://shortrun.org/private/entry-deadlines/", PROG])
out, s, clk, store = run({ROBOTS: Resp(200, SR_ROBOTS),
                          "https://shortrun.org/": Resp(200, '<p>Add yourself to our mailing list.</p>'),
                          PROG: Resp(200, SCHED_SAT)},
                         site=site4, today=date(2026, 11, 1))
check(PROG in s.calls and "https://shortrun.org/private/entry-deadlines/" not in s.calls
      and out["kept"] == 1,
      "a disallowed page at /entry-deadlines/ is skipped, and the next configured page is still read")
out, s, clk, store = run({ROBOTS: Resp(429, ""), "https://shortrun.org/": Resp(200, HOME)})
check(out["kept"] == 0 and s.calls == [ROBOTS] and "429" in out["verdict"],
      "a 429 on robots.txt itself is a refusal: the homepage is never asked")
cd = [c for c in (robots_txt.parse(SR_ROBOTS)[0][1]) if c[0] == "crawl-delay"]
check(cd == [("crawl-delay", "10")], "(fixture) shortrun.org's own robots.txt shape: Crawl-delay 10")

# ---------------------------------------------------- 10. the config
sites = A.load_sites(os.path.join(os.path.dirname(os.path.abspath(__file__)), "annual_sources.json"))
sr = next(x for x in sites if x["key"] == "short-run")
check(sr["url"] == "https://shortrun.org/" and sr["shape"] == "day" and sr["category"] == "arts",
      "annual_sources.json#short-run keeps its key and url (seeds.json points at them)")
check(47.54 < sr["lat"] < 47.56 and -122.34 < sr["lon"] < -122.32 and "231286803" in sr["_coords"],
      "...its point is the Design Center's OSM outline, and says how it was measured")
check(re.search(sr["name_rx"], "Short Run Comix Festival", re.I), "...and its name_rx matches its own name")
check(sr["name_rx"] == CONFIG_RX and not re.search(sr["name_rx"], "Short Run Spring Market", re.I),
      "...the festival's name, not the organiser's (SITE here mirrors it)")
check(sr.get("months") == SITE["months"] == [10, 11]
      and all(d in sr.get("_months", "") for d in ("2026-11-07", "2025-11-01", "2024-11-02",
                                                    "2023-11-04", "2022-11-05")),
      "...its months are [10, 11], and '_months' names the five editions they come from")
for months in ([11], [1, 12], None):
    try:
        A.validate_site(dict(SITE, months=months))
        check(True, f"months {months} is a valid entry")
    except ValueError as exc:
        check(False, f"months {months} is a valid entry: {exc}")
for bad, why in ((dict(SITE, months=[]), "months"), (dict(SITE, months=[0]), "months"),
                 (dict(SITE, months=[13]), "months"), (dict(SITE, months=[11, 11]), "months"),
                 (dict(SITE, months="11"), "months"), (dict(SITE, months=[True]), "months"),
                 (dict(SITE, months=[11.0]), "months"),
                 (dict(SITE, name_rx=None), "name_rx"),
                 (dict(SITE, pages=["https://elsewhere.example/dates"]), "every page"),
                 (dict(SITE, shape="week"), "shape"),
                 (dict(SITE, timezone="Seattle"), "timezone"),
                 (dict(SITE, category="comics"), "category"),
                 (dict(SITE, lat=147.0), "lat/lon"),
                 (dict(SITE, key="Short Run"), "key")):
    try:
        A.validate_site(bad)
        check(False, f"a bad {why} is refused")
    except ValueError as exc:
        check(why in str(exc), f"a bad {why} is refused: {exc}")


# ---------------------------------------------------- 11. main(): the User-Agent and the exit
class MainSession(Session):
    pass


tmp = tempfile.mkdtemp()
cfg = os.path.join(tmp, "annual.json")
json.dump({"sites": [SITE]}, open(cfg, "w", encoding="utf-8"))
ms = MainSession({ROBOTS: Resp(200, "User-agent: *\nAllow: /\n"),
                  "https://shortrun.org/": Resp(200, HOME)})
real_requests = A.requests
A.requests = types.SimpleNamespace(Session=lambda: ms)
os.environ["MAPSEE_TODAY"] = "20261101"
try:
    rc = A.main(["--config", cfg, "--store", os.path.join(tmp, "store.json"), "--max-seconds", "30"])
finally:
    A.requests = real_requests
    del os.environ["MAPSEE_TODAY"]
check(rc == 0 and ms.headers.get("User-Agent") == "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
      "main() asks with exactly the production User-Agent")
saved = json.load(open(os.path.join(tmp, "store.json"), encoding="utf-8"))["events"]
check(len(saved) == 1 and saved[0]["name"] == "Short Run Comix Festival 2026", "...and saves the row it read")
json.dump({"sites": [dict(SITE, name_rx=None)]}, open(cfg, "w", encoding="utf-8"))
check(A.main(["--config", cfg, "--store", os.path.join(tmp, "s2.json")]) == 2,
      "a bad config exits 2 before any request")

# ---------------------------------------------------- report
bad = [l for ok, l in checks if not ok]
for ok, l in checks:
    print(("ok    " if ok else "FAIL  ") + l)
print(f"\n{len(checks)} cases, {len(bad)} failed")
sys.exit(1 if bad else 0)
