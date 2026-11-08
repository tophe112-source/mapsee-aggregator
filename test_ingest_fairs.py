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

# ---------------------------------------------------- 7. the page's own entities
# WordPress writes the dash between two days as &#8211;, which RANGE_RX's
# [-–—] never matched: idahofair.com and statefair.org (Nebraska) both read as
# "no date range on the page" on 2026-11-07 with their 2027 dates on the
# homepage. Through text_of itself, because that is where the bytes become text.
ID_HTML = '<div class="hero"><p>August 13 &#8211; 29, 2027</p></div>'
check("August 13 – 29, 2027" in F.text_of(ID_HTML), "text_of decodes &#8211; to an en dash")
check(dates(F.text_of(ID_HTML)) == (date(2027, 8, 13), date(2027, 8, 29)),
      "Idaho's 'August 13 &#8211; 29, 2027' is read through text_of")
check(dates(F.text_of('<h2>August 27 &#8211; September 6, 2027</h2>'))
      == (date(2027, 8, 27), date(2027, 9, 6)),
      "...and Nebraska's 'August 27 &#8211; September 6, 2027'")
check(dates(F.text_of('<p>Aug&nbsp;12&ndash;22,&nbsp;2027</p>')) == (date(2027, 8, 12), date(2027, 8, 22)),
      "&nbsp; collapses with the whitespace and &ndash; is a dash too")
check("<b>" in F.text_of("<p>a &lt;b&gt; tag in prose</p>") and "<p>" not in F.text_of("<p>x</p>"),
      "an escaped tag in prose stays text; real tags are still stripped")
check(dates(F.text_of('<script>{"d":"August 1 &#8211; 9, 2027"}</script><p>soon</p>')) is None,
      "a script body's dates are still not the page's")


# ---------------------------------------------------- 8. robots.txt and the host's pace
# Read live 2026-11-07: azstatefair.com and oregonstatefair.org answer
# /robots.txt with a bot challenge, castatefair.org's cannot be reached, and
# durhamfair.com and sdstatefair.com ask Crawl-delay: 30. Until then this
# adapter asked none of them and paced every fair at 1.0 s and 0.4 s.
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


def fair_run(routes, url="https://fair.test/", deadline=None):
    import os
    import tempfile
    from mapsee_ingest import EventStore
    s, clk = Session(routes), Clock()
    polite = F.Polite(s, min_interval=1.0, sleep=clk.sleep, clock=clk,
                      deadline=None if deadline is None else clk.t + deadline)
    store = EventStore(os.path.join(tempfile.mkdtemp(), "e.json"))
    fair = dict(site, url=url)
    kept = F.ingest_site(store, polite, fair, TODAY)
    return kept, s, clk, store, polite


R = "https://fair.test/robots.txt"
HOME_NO_DATES = ('<p>Thanks for a great fair!</p><a href="/dates">Fair Dates</a>'
                 '<a href="/general-info">General Information</a><a href="/tickets">Tickets</a>')
DATES = "<p>The fair returns August 26 &#8211; September 6, 2027</p>"

kept, s, clk, store, _ = fair_run({R: Resp(200, "User-agent: *\nCrawl-delay: 30\n"),
                                   "https://fair.test/": Resp(200, HOME_NO_DATES),
                                   "https://fair.test/dates": Resp(200, DATES)})
check(kept == 1 and s.calls == [R, "https://fair.test/", "https://fair.test/dates"],
      "robots.txt first, the homepage, then the best hinted page")
check(clk.sleeps == [30.0, 30.0], "Crawl-delay: 30 is waited before each page, robots.txt read included")
kept, s, *_ = fair_run({R: Resp(200, "User-agent: *\nDisallow: /dates\n"),
                        "https://fair.test/": Resp(200, HOME_NO_DATES),
                        "https://fair.test/dates": Resp(200, DATES),
                        "https://fair.test/general-info": Resp(200, "<p>no dates here</p>")})
check(kept == 0 and "https://fair.test/dates" not in s.calls
      and "https://fair.test/general-info" in s.calls,
      "a Disallowed hinted page is skipped and never fetched; the next one is read")
kept, s, *_ = fair_run({R: Resp(200, "<title>Just a moment...</title><div>challenge-platform</div>")})
check(kept == 0 and s.calls == [R], "a bot challenge on robots.txt (Arizona, Oregon) asks nothing more")
kept, s, *_ = fair_run({R: ConnectionError("reset")})
check(kept == 0 and s.calls == [R], "an unreachable robots.txt (California) is assume-Disallow")
kept, s, *_ = fair_run({R: Resp(503, "busy")})
check(kept == 0 and s.calls == [R], "...as is a 5xx")
kept, s, *_ = fair_run({R: Resp(404, "none"), "https://fair.test/": Resp(200, DATES)})
check(kept == 1, "a missing robots.txt (4xx) allows everything, RFC 9309 2.3.1")
kept, s, *_ = fair_run({R: Resp(200, ""), "https://fair.test/": Resp(200, HOME_NO_DATES),
                        "https://fair.test/dates": Resp(403, "Forbidden"),
                        "https://fair.test/general-info": Resp(200, DATES)})
check(kept == 0 and "https://fair.test/general-info" not in s.calls,
      "a 403 on one page ends that fair for the run: no retry, no next page")
kept, s, *_ = fair_run({R: Resp(200, ""), "https://fair.test/": Resp(429, "slow down")})
check(kept == 0 and s.calls.count("https://fair.test/") == 1, "a 429 is not retried")
CF_TAIL = ("<html><body><p>The fair returns August 26 - September 6, 2027</p>" + "x" * 7000
           + '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></body></html>')
kept, *_ = fair_run({R: Resp(200, ""), "https://fair.test/": Resp(200, CF_TAIL)})
check(kept == 1, "Cloudflare's script at the END of a normal page (Florida, NJ, Texas, Wyoming) is not a challenge")
kept, s, *_ = fair_run({R: Resp(200, ""),
                        "https://fair.test/": Resp(200, "<title>Just a moment...</title>" + DATES)})
check(kept == 0, "...but a challenge in the page's head is")
kept, s, *_ = fair_run({R: Resp(200, ""),
                        "https://fair.test/": Resp(301, "", {"Location": "https://www.fair.test/"}),
                        "https://www.fair.test/robots.txt": Resp(200, "User-agent: *\nDisallow: /\n"),
                        "https://www.fair.test/": Resp(200, DATES)})
check(kept == 0 and "https://www.fair.test/robots.txt" in s.calls and "https://www.fair.test/" not in s.calls,
      "a redirect to another host asks THAT host's robots.txt before following")
kept, s, *_ = fair_run({R: Resp(200, ""),
                        "https://fair.test/": Resp(301, "", {"Location": "https://www.fair.test/"}),
                        "https://www.fair.test/": Resp(200, DATES)})
check(kept == 1, "...and follows it when allowed")
kept, s, clk, store, polite = fair_run({R: Resp(200, "User-agent: *\nCrawl-delay: 30\n"),
                                        "https://fair.test/": Resp(200, DATES)}, deadline=20)
check(kept == 0 and s.calls == [R] and not clk.sleeps,
      "a Crawl-delay the run's deadline cannot hold asks nothing rather than overrun it")
# robots_txt reads every 4xx as RFC 9309's "unavailable, allow all"; a 403 or a
# 429 on /robots.txt is nonetheless the host saying no (the owner's rule), and
# until 2026-11-08 this door went on to ask for the homepage.
for code in (429, 403):
    kept, s, clk, store, polite = fair_run({R: Resp(code, ""), "https://fair.test/": Resp(200, DATES)})
    check(kept == 0 and s.calls == [R] and "fair.test" in polite.refused
          and f"HTTP {code} on {R}" in polite.refused["fair.test"],
          f"a {code} on robots.txt refuses the host: the homepage is never asked")
kept, s, *_ = fair_run({R: Resp(404, "not found"), "https://fair.test/": Resp(200, DATES)})
check(kept == 1 and s.calls == [R, "https://fair.test/"],
      "...while a 404 is still 'no robots.txt', and the homepage is read (5 of the 48 fairs)")

# ---------------------------------------------------- report
bad = [l for ok, l in checks if not ok]
for ok, l in checks:
    print(("ok    " if ok else "FAIL  ") + l)
print(f"\n{len(checks)} cases, {len(bad)} failed")
sys.exit(1 if bad else 0)
