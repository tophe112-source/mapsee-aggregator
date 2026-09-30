#!/usr/bin/env python3
"""
test_robots.py - robots.txt is read the way RFC 9309 says, and verify obeys it.

WHY THIS EXISTS. "Respect robots.txt" was a rule nothing enforced: verify proved
a feed parsed and had something upcoming, and the weekly sweep committed
whatever passed. On 2026-09-30 an audit of every configured request found 127
of 3,468 disallowed by the host's own file - 30 of them Google calendars,
because calendar.google.com/robots.txt is `Allow: /$` then `Disallow: /`.

The matcher is the part that is easy to get subtly wrong, and every way it can
be wrong is silent: urllib.robotparser ignores `*` inside a path, so it reads
Houston Food Bank's `Disallow: *ical=*` and Minneapolis Parks'
`Disallow: /*?*ical=` as allowing the `?ical=1` exports both files refuse. The
fixtures below are real files from that audit, trimmed to the lines that decide
the case.

No network. The verify test runs the REAL cmd_verify against a temp tree with a
stub session, and asserts on what was FETCHED as well as what was written: a
refused feed must not be requested at all, not merely left out of the output.

    python test_robots.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["MAPSEE_TODAY"] = "20260930"

import robots_txt as R
import catalog_curate as cc

fails = []


def check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(label)


def decide(body, path, token=R.PRODUCT_TOKEN):
    rules, which = R.rules_for(R.parse(body), token)
    ok, rule = R.verdict(rules, path)
    return ok, rule, which, R.crawl_delay(rules)


# --- 1. the matcher, on files it met ----------------------------------------
GOOGLE_CAL = "User-agent: *\nAllow: /$\nDisallow: /\n"
ok, rule, _w, _d = decide(GOOGLE_CAL, "/calendar/ical/wprtwatch%40gmail.com/public/basic.ics")
check("calendar.google.com: a public basic.ics export is disallowed", ok is False and rule == "Disallow: /", rule)
check("calendar.google.com: `Allow: /$` allows the root and only the root",
      decide(GOOGLE_CAL, "/")[0] is True and decide(GOOGLE_CAL, "/?hl=en")[0] is False)

# Yoast appends a second `User-agent: *` group with an empty Disallow. RFC 9309
# merges the groups, so the first group's Disallow still binds.
TORONTO = ("User-agent: *\nDisallow: /events/\nAllow: /events/$\nDisallow: /*?tribe-bar-date=\n"
           "# START YOAST BLOCK\nUser-agent: *\nDisallow:\n\nSitemap: http://torontobotanicalgarden.ca/sitemap_index.xml\n")
check("two `*` groups are merged: `Disallow: /events/` refuses `/events/?ical=1`",
      decide(TORONTO, "/events/?ical=1")[0] is False)
check("...while `Allow: /events/$` (longer) still allows the bare listing",
      decide(TORONTO, "/events/")[0] is True)
check("...and a single event page outside /events/ is allowed",
      decide(TORONTO, "/event/fall-plant-sale/")[0] is True)

# A second `*` group that disallows everything, then an allow-list of named
# crawlers. Unnamed is disallowed; Googlebot's own group decides for Googlebot.
SANTA_FE = ("User-agent: *\nAllow: /core/*.css$\nDisallow: /core/\n\nUser-agent: *\nDisallow: /\n\n"
            "User-agent: Applebot\nUser-agent: Googlebot\nUser-agent: Bingbot\nDisallow: /admin/\n")
check("santafelibrary.org: our UA falls to `*`, which is `Disallow: /`",
      decide(SANTA_FE, "/events/feed/ical/")[0] is False)
ok, _r, which, _d = decide(SANTA_FE, "/events/feed/ical/", token="googlebot")
check("...and a crawler the file names gets its own group instead", ok is True and which == "named")

# A pattern that does not start with `/`: Google's parser matches it, so do we.
HOUSTON = "User-agent: *\nDisallow: */comments\nDisallow: *ical=*\nDisallow: *outlook-ical=*\n"
check("houstonfoodbank.org: `Disallow: *ical=*` refuses `/events/?ical=1`",
      decide(HOUSTON, "/events/?ical=1")[0] is False)
check("...and leaves the events page itself alone", decide(HOUSTON, "/events/")[0] is True)

MINNEAPOLIS = "User-agent: *\nDisallow: /*?*ical=\nDisallow: /*?*format=ical\n"
check("minneapolisparks.org: `Disallow: /*?*ical=` refuses `/events/?ical=1`",
      decide(MINNEAPOLIS, "/events/?ical=1")[0] is False)

# The LibCal tenant file: every named crawler refused, then `*` with a delay AND
# a Disallow. Seven tenants in the catalog carry it (FIU, Denver, UMN, ...).
LIBCAL = ("User-agent: crawl\nDisallow: /\nUser-agent: GPTBot\nDisallow: /\n"
          "User-agent: Twitterbot\nDisallow:\nUser-agent: *\nCrawl-delay: 10\nDisallow: /\n")
ok, rule, _w, delay = decide(LIBCAL, "/ical_subscribe.php?cid=15594")
check("a LibCal tenant whose `*` group is `Disallow: /` refuses ical_subscribe.php",
      ok is False and delay == 10.0, f"{ok} {rule} {delay}")

# The stock Squarespace file, which is the precedent: its AI crawlers share ONE
# group with `*` (consecutive user-agent lines), and `Disallow:/` has no space.
SQUARESPACE = ("User-agent: GPTBot\nUser-agent: ClaudeBot\nUser-agent: *\nDisallow: /config\n"
               "Disallow: /api/\nAllow: /api/ui-extensions/\nDisallow:/*?format=json\n"
               "Disallow:/*&format=json\nDisallow:/*?format=ical\nDisallow:/*?month=*\n")
check("stock Squarespace: `?format=json` is refused (the reason the adapter moved off it)",
      decide(SQUARESPACE, "/events?format=json")[0] is False)
check("stock Squarespace: `?format=ical` is refused too", decide(SQUARESPACE, "/events?format=ical")[0] is False)
check("stock Squarespace: the bare collection page the adapter reads is allowed",
      decide(SQUARESPACE, "/events")[0] is True)
check("stock Squarespace: `Allow: /api/ui-extensions/` beats `Disallow: /api/` by length",
      decide(SQUARESPACE, "/api/ui-extensions/x")[0] is True and decide(SQUARESPACE, "/api/x")[0] is False)

# Group selection is by PRODUCT TOKEN, whole and case-insensitive.
NAMED = "User-agent: *\nDisallow: /\n\nUser-agent: MapseeAggregator/1.0\nAllow: /\n"
ok, _r, which, _d = decide(NAMED, "/calendar.ics")
check("a group that names MapseeAggregator replaces `*`", ok is True and which == "named")
check("`User-agent: Mapsee` is not our product token",
      decide("User-agent: *\nDisallow: /\n\nUser-agent: Mapsee\nAllow: /\n", "/x")[0] is False)

# Longest match, a tie to Allow, `$` as an end anchor.
check("the longest pattern wins: `Allow: /p` over `Disallow: /`",
      decide("User-agent: *\nDisallow: /\nAllow: /p\n", "/page")[0] is True)
check("a tie goes to Allow", decide("User-agent: *\nDisallow: /folder\nAllow: /folder\n", "/folder/a")[0] is True)
DOLLAR = "User-agent: *\nDisallow: /*.ics$\n"
check("`$` anchors the end: `/a/b.ics` refused, `/a/b.ics?x=1` not",
      decide(DOLLAR, "/a/b.ics")[0] is False and decide(DOLLAR, "/a/b.ics?x=1")[0] is True)

# Percent-encoding: an escaped UNRESERVED character is its character; a
# reserved one stays escaped, so `%2F` is not a path separator.
check("`%7E` in a rule is `~` in a path", decide("User-agent: *\nDisallow: /%7Ejoe/\n", "/~joe/x")[0] is False)
check("`%2F` in a rule does not match a real `/`",
      decide("User-agent: *\nDisallow: /a%2Fb\n", "/a/b")[0] is True)
check("...and the PATH is normalised too: `/%7Ejoe/` is refused by `Disallow: /~joe/`",
      decide("User-agent: *\nDisallow: /~joe/\n", "/%7Ejoe/x")[0] is False)
check("a raw UTF-8 rule matches the same path escaped in lower-case hex",
      decide("User-agent: *\nDisallow: /évènements/\n", "/%c3%a9v%c3%a8nements/?ical=1")[0] is False)

# Group boundaries: a Crawl-delay is a group member, so the user-agent line
# after it opens a NEW group rather than joining this one.
SPLIT = "User-agent: *\nCrawl-delay: 10\nUser-agent: Googlebot\nDisallow: /x\n"
ok, _r, _w, delay = decide(SPLIT, "/x")
check("a user-agent line after a Crawl-delay starts a new group", ok is True and delay == 10.0, f"{ok} {delay}")
check("an empty Disallow is no rule at all", decide("User-agent: *\nDisallow:\n", "/anything")[0] is True)
ok, _r, which, _d = decide("User-agent: Googlebot\nDisallow: /\n", "/x")
check("no `*` group and no group naming us: nothing applies", ok is True and which == "none")


# --- 2. reading the file: what each answer means -----------------------------
class Resp:
    def __init__(self, code=200, body="", url=""):
        self.status_code = code
        self.content = body.encode("utf-8")
        self.text = body
        self.url = url
        self.headers = {"content-type": "text/plain"}


class Session:
    """robots.txt and feed bodies by URL; records every URL asked for."""

    def __init__(self, pages):
        self.pages = pages
        self.headers = {"User-Agent": cc.UA}
        self.asked = []

    def get(self, url, timeout=None, allow_redirects=True, params=None):
        self.asked.append(url)
        got = self.pages.get(url)
        if isinstance(got, Exception):
            raise got
        if got is None:
            return Resp(404, "not found", url)
        code, body = got
        return Resp(code, body, url)


def answer(pages, url):
    return R.Robots(Session(pages)).check(url)


a = answer({}, "https://nofile.example/cal.ics")
check("404 on robots.txt: nothing is disallowed", a["allowed"] is True and a["status"] == "http404", a)
a = answer({"https://err.example/robots.txt": (503, "busy")}, "https://err.example/cal.ics")
check("5xx on robots.txt: RFC 9309 says assume Disallow", a["allowed"] is False and a["status"] == "unreachable", a)
import requests
a = answer({"https://down.example/robots.txt": requests.ConnectionError("refused")}, "https://down.example/cal.ics")
check("a refused connection is unreachable, not allowed", a["allowed"] is False and a["status"] == "unreachable", a)
WAF = '<!DOCTYPE html>\n<html lang="en">\n<head><title>One moment, please...</title></head></html>'
a = answer({"https://waf.example/robots.txt": (200, WAF)}, "https://waf.example/cal.ics")
check("a 200 'One moment, please' page on robots.txt is a challenge, not a file",
      a["allowed"] is None and a["status"] == "challenge", a)
a = answer({"https://sg.example/robots.txt": (202, '<html><head><meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/"></head></html>')},
           "https://sg.example/cal.ics")
check("SiteGround's 202 sgcaptcha is a challenge", a["allowed"] is None and a["status"] == "challenge", a)
a = answer({"https://home.example/robots.txt": (200, "<!doctype html><html><body>Welcome</body></html>")},
           "https://home.example/cal.ics")
check("a homepage served at /robots.txt has no rules: allowed", a["allowed"] is True and a["status"] == "html", a)
a = answer({"https://cap.example/robots.txt": (200, "User-agent: *\nDisallow: /captcha-delivery/\n")},
           "https://cap.example/cal.ics")
check("a real file that merely MENTIONS a challenge path is read as a file", a["allowed"] is True and a["status"] == "ok", a)

s = Session({"https://once.example/robots.txt": (200, "User-agent: *\nDisallow: /private/\n")})
rb = R.Robots(s)
first, second = rb.check("https://once.example/a.ics"), rb.check("https://once.example/private/b.ics")
check("robots.txt is fetched once per origin", s.asked == ["https://once.example/robots.txt"], s.asked)
check("...and both answers come from it", first["allowed"] is True and second["allowed"] is False)
check("a webcal:// feed is judged by its https origin's file",
      R.origin_of("webcal://cal.example/x.ics") == "https://cal.example")


# --- 3. the parallel pass does not get the last word -------------------------
class Flaky:
    """Challenges an origin the first time it is asked, answers the second."""
    calls = {}

    def __init__(self):
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=True, params=None):
        n = Flaky.calls[url] = Flaky.calls.get(url, 0) + 1
        if n == 1:
            return Resp(200, WAF, url)
        return Resp(200, "User-agent: *\nDisallow: /nope/\n", url)


rb = R.Robots(Session({}))
rb.prefetch(["https://burst.example/cal.ics"], workers=4, make_session=Flaky)
a = rb.check("https://burst.example/cal.ics")
check("a challenge from the parallel pass is re-asked alone, and the quiet answer stands",
      a["status"] == "ok" and a["allowed"] is True and rb.retried == 1 and rb.recovered == 1,
      f"{a} retried={rb.retried} recovered={rb.recovered}")


# --- 4. verify: the request the ADAPTER makes, and a refusal is not fetched ---
reqs = dict((t, cc._ingest_requests(t, e)) for t, e in [
    ("ics", {"url": "webcal://cal.example/feed.ics"}),
    ("tribe", {"base_url": "https://club.example/"}),
    ("localist", {"base_url": "https://events.example.edu"}),
    ("gancio", {"base_url": "https://gancio.example"}),
    ("mobilizon", {"base_url": "https://mobilizon.example"}),
    ("squarespace", {"collection": "https://sq.example/events"}),
    ("jsonld", {"listing": ["https://venue.example/whats-on", "https://venue.example/kids"]}),
    ("ods", {"domain": "data.example.fr", "dataset": "agenda"}),
    ("opendata", {"url": "https://data.example.gov/resource/abcd-1234.json"}),
])
check("ics: webcal is fetched as https", reqs["ics"] == [("GET", "https://cal.example/feed.ics")], reqs["ics"])
check("tribe: the REST route, with the query string the adapter sends",
      reqs["tribe"][0][1].startswith("https://club.example/wp-json/tribe/events/v1/events?per_page=50&start_date=2026-09-30"),
      reqs["tribe"])
check("localist: /api/2/events", reqs["localist"][0][1].startswith("https://events.example.edu/api/2/events?"))
check("gancio: /api/events", reqs["gancio"][0][1].startswith("https://gancio.example/api/events?"))
check("mobilizon: a POST to /api", reqs["mobilizon"] == [("POST", "https://mobilizon.example/api")])
check("squarespace: the bare collection page", reqs["squarespace"] == [("GET", "https://sq.example/events")])
check("jsonld: every listing page", [u for _m, u in reqs["jsonld"]] == ["https://venue.example/whats-on", "https://venue.example/kids"])
check("ods: the records API with a query",
      reqs["ods"][0][1].startswith("https://data.example.fr/api/explore/v2.1/catalog/datasets/agenda/records?"))
check("opendata: a query appended with `?` when the URL has none",
      reqs["opendata"][0][1] == "https://data.example.gov/resource/abcd-1234.json?limit=1")


class StubRobots:
    def __init__(self, answers):
        self.answers = answers

    def check(self, url):
        return self.answers[R.origin_of(url)]


ok_ans = {"allowed": True, "status": "ok", "rule": None, "crawl_delay": None, "robots": "x"}
stub = StubRobots({
    "https://no.example": {"allowed": False, "status": "ok", "rule": "Disallow: /", "crawl_delay": None, "robots": "x"},
    "https://down.example": {"allowed": False, "status": "unreachable", "rule": "(unreachable)", "crawl_delay": None, "robots": "x"},
    "https://waf.example": {"allowed": None, "status": "challenge", "rule": None, "crawl_delay": None, "robots": "x"},
    "https://slow.example": dict(ok_ans, crawl_delay=10.0),
    "https://fine.example": ok_ans,
})
g = cc._robots_gate(stub, "ics", {"url": "https://no.example/a.ics"})
check("a Disallow is `refused`, and the reason names the rule",
      g is not None and g[0] == "refused" and "Disallow: /" in g[1], g)
check("an unreachable robots.txt is `fail`, not `refused`",
      (cc._robots_gate(stub, "ics", {"url": "https://down.example/a.ics"}) or ("",))[0] == "fail")
check("a challenged robots.txt is `fail`",
      (cc._robots_gate(stub, "ics", {"url": "https://waf.example/a.ics"}) or ("",))[0] == "fail")
e = {"url": "https://slow.example/a.ics"}
check("an allowed ics feed on a host asking 10 s gets crawl_delay 10",
      cc._robots_gate(stub, "ics", e) is None and e.get("crawl_delay") == 10.0, e)
e = {"url": "https://slow.example/a.ics", "crawl_delay": 30}
cc._robots_gate(stub, "ics", e)
check("...but a delay already in the entry is kept", e["crawl_delay"] == 30)
e = {"base_url": "https://slow.example"}
cc._robots_gate(stub, "localist", e)
check("...and an adapter that paces nothing is not given one", "crawl_delay" not in e)

tmp = tempfile.mkdtemp(prefix="robots-verify-")
keep = (cc.HERE, cc.LEDGER_FILE, cc._session)
cc.HERE = tmp
cc.LEDGER_FILE = os.path.join(tmp, "curation_ledger.json")
FEED = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261010T180000Z\nSUMMARY:Fall BBQ\nEND:VEVENT\nEND:VCALENDAR\n"
pages = {
    "https://calendar.google.com/robots.txt": (200, GOOGLE_CAL),
    "https://calendar.google.com/calendar/ical/x%40gmail.com/public/basic.ics": (200, FEED),
    "https://club.example/robots.txt": (200, "User-agent: *\nDisallow: /wp-admin/\n"),
    "https://club.example/?ical=1": (200, FEED),
    "https://lib.example/robots.txt": (200, "User-agent: *\nCrawl-delay: 10\nDisallow: /admin/\n"),
    "https://lib.example/ical_subscribe.php?cid=1": (200, FEED),
}
sess = Session(pages)
cc._session = lambda: sess
cands = [
    {"type": "ics", "name": "A Google calendar", "category": "community",
     "url": "https://calendar.google.com/calendar/ical/x%40gmail.com/public/basic.ics"},
    {"type": "ics", "name": "A club", "category": "community", "url": "https://club.example/?ical=1"},
    {"type": "ics", "name": "A library", "category": "learning", "url": "https://lib.example/ical_subscribe.php?cid=1"},
]
path = os.path.join(tmp, "cand.json")
json.dump(cands, open(path, "w", encoding="utf-8"))
try:
    cc.cmd_verify(path)
    verified = json.load(open(os.path.join(tmp, "cand.verified.json"), encoding="utf-8"))
    led = json.load(open(cc.LEDGER_FILE, encoding="utf-8"))
finally:
    cc.HERE, cc.LEDGER_FILE, cc._session = keep
names = [v["name"] for v in verified]
check("verify passes the allowed feeds and not the refused one", names == ["A club", "A library"], names)
check("the refused feed was never requested - only its robots.txt was",
      not any("public/basic.ics" in u for u in sess.asked), sess.asked)
row = led.get(cc._canon(cands[0]["url"])) or {}
check("the ledger records the refusal as `refused`, with the rule",
      row.get("status") == "refused" and "Disallow: /" in row.get("reason", ""), row)
lib = next(v for v in verified if v["name"] == "A library")
check("the library's config carries the Crawl-delay its robots.txt asks for", lib.get("crawl_delay") == 10.0, lib)
club = next(v for v in verified if v["name"] == "A club")
check("...and a host that asks for none gets none", "crawl_delay" not in club, club)
check("a recent refusal keeps discovery from proposing it again",
      cc._dead_recently({"k": {"status": "refused", "checked": 20260930}}, "k") is True)

if fails:
    print(f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
