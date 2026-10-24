"""Natuurwerkdag: the finder decides which jobs exist, a job page only adds to
one, the cache is the cursor, and nobody's phone number reaches the map.

WHY THIS EXISTS. Each case is a way the 2026-10-24 build could come out
well-formed and wrong:

  * THE RAIL. 21 sitemap URLs are hidden shells whose only date belongs to
    another job in "Meer in de buurt". A shell must parse as no job at all,
    and a page is read only for a job the listing shows.
  * THE YEAR AND THE WEEKDAY are the page's own. A weekday naming another day
    refuses the date; nothing is re-dated. Last year's card is past.
  * THE POINT is the organiser's own pin from the listing's map. No pin, a pin
    outside the Netherlands, one far from its province's other jobs, or one
    shared by different jobs is refused - and the refused job is still
    `seen`, so the rec sync's absence check cannot cancel it.
  * PEOPLE. A sentence with an e-mail address, a phone number or a named
    contact goes (1 of 22 real pages had "Contact: <name>, 06-..."), and the
    organiser's data-email attribute is never read.
  * FULL is not cancelled: refused and seen, never a tombstone.
  * IDENTITY. "<title> HH:MM" | day | place, the recipe EventStore's notice
    rule can prove, so "GEANNULEERD: <title>" tombstones the job.
  * THE BUDGET. A deadline cut leaves every listed job written (from the
    listing), the read still complete, and the cache holding what was read;
    the next run reads only what it had not.
  * main() on the REAL config, because a config that parses is not a config
    that loads (docs/agents/adapters-and-sources.md).

No network: the fixtures are the real markup with invented jobs and people.
"""
import os
import sys

os.environ["MAPSEE_TODAY"] = "20261024"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import re
import shutil
import tempfile
from datetime import date, timedelta

import mapsee_ingest_natuurwerkdag as N
from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "natuurwerkdag")
CFG = json.load(open(os.path.join(HERE, "natuurwerkdag_sources.json"), encoding="utf-8"))
SITE = CFG["sites"][0]
BASE = "https://www.natuurwerkdag.nl/klussen/"
TODAY = date(2026, 10, 24)


def fx(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


LISTING = fx("listing.html")
# The same finder without card 109, whose weekday names another day: a card
# that cannot be dated cannot be identified, so with it the read is never whole.
CLEAN = re.sub(r'(?s)<article class="section-grid__col loop-jobs post-109 .*?</article>\n?', "", LISTING) \
    .replace('"total_rows": 11', '"total_rows": 10')
SITEMAP = fx("jobs-sitemap.xml")
PAGES = {
    BASE + "wilgen-knotten-voorbeeldplas/": fx("job-101.html"),
    BASE + "heide-schonen-proefveld/": fx("job-102.html"),
    BASE + "bosrand-opschonen/": fx("job-105.html"),
    BASE + "bosrand-opschonen-2/": fx("job-106.html"),
    BASE + "hoogstamboomgaard-snoeien/": fx("job-110.html"),
    BASE + "junior-bollen-planten/": fx("job-111-shell.html"),
}


# --------------------------------------------------------------------------- #
# A fake web

class Resp:
    def __init__(self, code, text="", headers=None):
        self.status_code = code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}


class Web:
    """Routes URL -> Resp; every request advances the fake clock by 1 s."""

    def __init__(self, routes=None, overrides=None):
        self.routes = dict(routes if routes is not None else default_routes())
        self.routes.update(overrides or {})
        self.log = []
        self.now = 0.0
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=False):
        self.log.append(url)
        self.now += 1.0
        r = self.routes.get(url)
        if r is None:
            return Resp(404, "not found")
        if isinstance(r, Exception):
            raise r
        return r


def default_routes():
    r = {SITE["listing"]: Resp(200, LISTING), SITE["sitemap"]: Resp(200, SITEMAP),
         "https://www.natuurwerkdag.nl/robots.txt": Resp(200, "User-agent: *\nAllow: /\n\nSitemap:https://www.natuurwerkdag.nl/sitemap.xml\n")}
    r.update({u: Resp(200, t) for u, t in PAGES.items()})
    return r


def run(web, cache=None, today=TODAY, deadline=None, site=None, robots=None):
    rows, seen = [], []
    cache = cache if cache is not None else {"version": N.CACHE_VERSION, "pages": {}}
    client = N.Client(web, 0.0, deadline, robots=robots, clock=lambda: web.now, sleep=lambda s: None)
    rep = N.read_site(site or SITE, client, today, rows.append, seen.append, cache)
    return rep, rows, seen, cache


def by_id(rows):
    return {r.source_id: r for r in rows}


# --------------------------------------------------------------------------- #
# Dates and clocks

for raw, want in [("Zaterdag 7 november 2026", (date(2026, 11, 7), None)),
                  ("Vrijdag 6 november 2026", (date(2026, 11, 6), None)),
                  ("7 november 2026", (date(2026, 11, 7), None)),
                  ("Vrijdag 7 november 2026", (None, "weekday disagrees with the date")),
                  ("Zaterdag 7 brumaire 2026", (None, "unreadable date")),
                  ("Zaterdag 31 november 2026", (None, "unreadable date")),
                  ("", (None, "unreadable date"))]:
    check(f"date {raw!r}", N.parse_date(raw) == want, N.parse_date(raw))

for raw, want in [("09:00 - 12:00", ((9, 0), (12, 0))), ("9.30 - 13.00 uur", ((9, 30), (13, 0))),
                  ("10:00 tot 16:00", ((10, 0), (16, 0))), ("12:00 - 09:00", None),
                  ("10:00 - 10:00", None), ("hele dag", None), (None, None)]:
    check(f"clock {raw!r}", N.parse_clock(raw) == want, N.parse_clock(raw))

check("CET on the day: 10:00 local is 09:00Z",
      N.localize(date(2026, 11, 7), (10, 0)) == ("2026-11-07T10:00:00+01:00", "2026-11-07T09:00:00Z"),
      N.localize(date(2026, 11, 7), (10, 0)))
check("no clock is a bare date, never midnight", N.localize(date(2026, 11, 6), None) == ("2026-11-06", None))

# --------------------------------------------------------------------------- #
# The listing

L = N.parse_listing(LISTING)
cards = {c["post_id"]: c for c in L["cards"]}
check("listing: 11 cards, read from the HTML only (the FWP_JSON popups repeat them)", len(L["cards"]) == 11,
      len(L["cards"]))
check("listing: the pager's total_rows is read", L["total_rows"] == 11, L["total_rows"])
check("listing: 10 map points, keyed on post id", L["markers"] == 10 and cards["101"]["lat"] == 52.0123456
      and cards["101"]["lon"] == 5.9012345, (L["markers"], cards["101"]))
check("listing: a card with no marker has no point", cards["103"]["lat"] is None)
c = cards["101"]
check("listing: title, link, day, clock, place, tasks, province",
      (c["title"], c["url"], c["date_text"], c["time_text"], c["place"], c["tasks"], c["province"])
      == ("Wilgen knotten bij de Voorbeeldplas", BASE + "wilgen-knotten-voorbeeldplas/",
          "Zaterdag 7 november 2026", "09:00 - 12:00", "Voorbeeld", ["Wilgen knotten", "Zagen"], "gelderland"), c)
check("listing: a card with no clock", cards["102"]["time_text"] is None)
sm = N.parse_sitemap(SITEMAP)
check("sitemap: every <url> with a lastmod", len(sm) == 12 and sm[BASE + "wilgen-knotten-voorbeeldplas/"]
      .startswith("2026-10-10"), len(sm))

# --------------------------------------------------------------------------- #
# A job page

d = N.parse_detail(PAGES[BASE + "wilgen-knotten-voorbeeldplas/"])
check("detail: date, clock and point from the summary",
      (d["date"], d["clock"], d["lat"], d["lon"]) == ("2026-11-07", "09:00-12:00", 52.0123456, 5.9012345), d)
check("detail: the kids flag", d["kids"] is True)
check("detail: open sign-up is not full", d["full"] is False)
check("detail: the meeting point keeps its place and loses its phone sentence",
      d["meeting"] == "Verzamelen bij de parkeerplaats aan de Plasweg 1.", d["meeting"])
check("detail: headings dropped, list items kept", "Onze impact" not in d["text"]
      and "- Soep en koffie" in d["text"], d["text"])
blob = json.dumps(d, ensure_ascii=False)
check("detail: no person, no number, no address anywhere in what is kept",
      not any(x in blob for x in ("Voorbeeld,", "Jan Voorbeeld", "06-00000001", "@example.org", "klusleider")), blob)
check("detail: 'Vragen?' goes with the address it introduced", "Vragen?" not in d["text"], d["text"])
check("detail: four contact sentences dropped and counted", d["dropped"] == 4, d["dropped"])
check("detail: the 'Wat ga je doen?' prose is kept", d["text"].startswith("We knotten de wilgen"), d["text"])
full = N.parse_detail(PAGES[BASE + "heide-schonen-proefveld/"])
check("detail: 'Het maximale aantal deelnemers is bereikt' is full", full["full"] is True and full["clock"] is None,
      full)
check("detail: a shell (hidden job, only the rail) is no job - the rail's date is never read",
      N.parse_detail(PAGES[BASE + "junior-bollen-planten/"]) is None)
d110 = N.parse_detail(PAGES[BASE + "hoogstamboomgaard-snoeien/"])
c110 = dict(cards["110"], date=date(2026, 11, 7), clock=((9, 0), (13, 0)))
check("agree: a page naming another day disagrees on the date", N.agrees(c110, d110) == "date", N.agrees(c110, d110))
c101 = dict(cards["101"], date=date(2026, 11, 7), clock=((9, 0), (12, 0)))
check("agree: the same day, clock and point agree", N.agrees(c101, d) is None, N.agrees(c101, d))
check("agree: a point 200 m off disagrees", N.agrees(dict(c101, lat=c101["lat"] + 0.0018), d) == "point")

# --------------------------------------------------------------------------- #
# People

for s, gone in [("Contact: Jan Voorbeeld, 06-00000001", True),
                ("Bel 0570 000001 voor info.", True),
                ("Bel +31 6 0000 0001 als je later komt.", True),
                ("Mail naar klusleider@example.org.", True),
                ("Neem contact op met Jan Voorbeeld via de site.", True),
                ("Klusleider: Jan Voorbeeld", True),
                ("Questions? Email Jane Example at info@example.org", True),
                ("Neem contact op met de boswachter als je later komt.", False),
                ("We werken van 09.00 tot 12.00 uur op 7-11-2026.", False),
                ("VINDplaats Zenit ligt achter Borneostraat 2-74.", False),
                ("Op 2026-11-07 van 10:00 - 12:00 aan de Plasweg 12.", False),
                ("Postcode 1234 AB, huisnummer 10.", False)]:
    check(f"contact {'dropped' if gone else 'kept'}: {s!r}", N.gives_contact(s) == gone, N.gives_contact(s))

# --------------------------------------------------------------------------- #
# Placement guards (pure)

peers = [{"post_id": str(i), "province": "groningen", "lat": 53.2 + i * 0.01, "lon": 6.6, "title": f"t{i}"}
         for i in range(6)]
far = {"post_id": "x", "province": "groningen", "lat": 51.5, "lon": 3.6, "title": "x"}
near = {"post_id": "y", "province": "groningen", "lat": 53.2 + 0.6, "lon": 6.6, "title": "y"}   # ~66 km
out = N.province_outliers(peers + [far, near], 110)
check("province: a Groningen job pinned in Zeeland is refused", "x" in out and out["x"] > 200, out)
check("province: 66 km from the median is not (measured max 71 km)", "y" not in out, out)
check("province: too few peers decides nothing", N.province_outliers(peers[:3] + [far], 110) == {})
same = [{"post_id": "a", "title": "Bosrand", "lat": 52.0, "lon": 5.0},
        {"post_id": "b", "title": "Bosrand", "lat": 52.0, "lon": 5.0}]
check("point: one title twice on one point (Fri+Sat, a double posting) is one site",
      N.crowded_points(same, 2) == set())
three = [{"post_id": k, "title": k, "lat": 52.0, "lon": 5.0} for k in "abc"]
check("point: three different jobs on one point is a default pin", N.crowded_points(three, 2) == {"a", "b", "c"})

# --------------------------------------------------------------------------- #
# A whole read, no cache

web = Web()
rep, rows, seen, cache = run(web)
ids = by_id(rows)
check("run: 101, 105, 106, 110? no - 110 disagrees; 111 from the listing",
      sorted(ids) == ["101", "105", "106", "111"], sorted(ids))
check("run: refused and counted", rep["refused"] == {
    "no point on the site's map": 1, "title carries a URL": 1, "point outside the Netherlands": 1,
    "weekday disagrees with the date": 1, "full: sign-up closed": 1,
    "page and listing disagree on the date": 1}, rep["refused"])
check("run: last year's card is past, not refused", rep["notes"].get("past") == 1, rep["notes"])
check("run: NOT complete - a listed card that cannot be dated (109) may be a job already written",
      rep["complete"] is False)
check("run: requests = listing + sitemap + 6 pages", rep["requests"] == 8 and web.log[:2] ==
      [SITE["listing"], SITE["sitemap"]], (rep["requests"], web.log))
check("run: no page is read for a refused card, a past one or a sitemap-only shell",
      not any(u in web.log for u in (BASE + "poel-uitbaggeren/", BASE + "natuur-in-parijs/",
                                      BASE + "knotwilgen-vorig-jaar/", BASE + "test-pagina/",
                                      BASE + "ringpark-voorbeeld/", BASE + "verkeerde-weekdag/")), web.log)
fp = lambda c, clock: N.fingerprint_for(c["title"], N.parse_date(c["date_text"])[0], clock, c.get("place"))
check("run: full, no point, URL title and disagreement are seen; the weekday card cannot be",
      set(seen) == {fp(cards["102"], None), fp(cards["103"], ((10, 0), (15, 0))),
                    fp(cards["104"], ((10, 0), (14, 30))), fp(cards["108"], ((10, 0), (12, 0))),
                    fp(cards["110"], ((9, 0), (13, 0)))}, len(seen))
check("run: the shell is written from the listing and not cached",
      ids["111"].description.endswith(SITE["signup_line"]) and BASE + "junior-bollen-planten/" not in cache["pages"],
      ids["111"].description)
check("run: the disagreeing page is not cached (read again next run)",
      BASE + "hoogstamboomgaard-snoeien/" not in cache["pages"])
check("run: what was read is cached with the sitemap's lastmod",
      cache["pages"][BASE + "wilgen-knotten-voorbeeldplas/"]["lastmod"] == sm[BASE + "wilgen-knotten-voorbeeldplas/"]
      and cache["pages"][BASE + "wilgen-knotten-voorbeeldplas/"]["read"] == "2026-10-24")
check("run: a full job's page is cached too (so it is not re-read daily)",
      cache["pages"][BASE + "heide-schonen-proefveld/"]["detail"]["full"] is True)
check("run: the double posting is printed and counted",
      rep["notes"].get("same title, place, day and start as another post (a double posting?)") == 1, rep["notes"])
check("run: four contact sentences counted", rep["notes"].get("contact sentences dropped") == 4, rep["notes"])

# --------------------------------------------------------------------------- #
# The row

ev = ids["101"]
check("row: source, post id, the job's own page as the link",
      (ev.source, ev.source_id, ev.ticket_url) == ("natuurwerkdag", "101", BASE + "wilgen-knotten-voorbeeldplas/"))
check("row: wall clock in Europe/Amsterdam",
      (ev.start_local, ev.start_utc, ev.end_local, ev.end_utc, ev.timezone) ==
      ("2026-11-07T09:00:00+01:00", "2026-11-07T08:00:00Z", "2026-11-07T12:00:00+01:00", "2026-11-07T11:00:00Z",
       "Europe/Amsterdam"), (ev.start_local, ev.start_utc, ev.end_local, ev.end_utc))
check("row: the organiser's pin, never geocoded over",
      (ev.latitude, ev.longitude, ev.coords_exact) == (52.0123456, 5.9012345, True))
check("row: place, province, country", (ev.venue_name, ev.city, ev.region, ev.country, ev.address)
      == ("Voorbeeld", "Voorbeeld", "Gelderland", "NL", None))
check("row: volunteer, with outdoors and (from the page) kids",
      (ev.category, ev.categories) == ("volunteer", ["outdoors", "kids"]), (ev.category, ev.categories))
check("row: the description leads with meeting point, tasks, kids and sign-up",
      ev.description.split("\n")[:4] == ["Meeting point: Verzamelen bij de parkeerplaats aan de Plasweg 1.",
                                         "Tasks: Wilgen knotten, Zagen.", "Suitable for children.",
                                         SITE["signup_line"]], ev.description)
check("row: no person reaches the description", not any(x in ev.description for x in
                                                         ("Jan Voorbeeld", "06-00000001", "example.org")))
check("row: the description fits the sync's 800", len(ev.description) <= 800, len(ev.description))
check("row: fingerprint is make_fingerprint('<title> HH:MM', day, place)",
      ev.fingerprint == make_fingerprint("Wilgen knotten bij de Voorbeeldplas 09:00", "2026-11-07", "Voorbeeld"))
check("row: 105 and 106 (same title, place, day, start) share one fingerprint",
      ids["105"].fingerprint == ids["106"].fingerprint)
date_only = N.build_event(dict(cards["102"], date=date(2026, 11, 6), clock=None), None, SITE)
check("row: a job with no clock is a bare-date row, plain recipe",
      (date_only.start_local, date_only.start_utc, date_only.end_local) == ("2026-11-06", None, None)
      and date_only.fingerprint == make_fingerprint("Heide schonen op het Proefveld", "2026-11-06", "Proefdorp"))
check("row: listing-only rows carry no kids flag they were not told",
      ids["111"].categories == ["outdoors"], ids["111"].categories)

tmp = tempfile.mkdtemp(prefix="nwd-test-")
try:
    st = EventStore(os.path.join(tmp, "s.json"))
    for r in rows:
        st.upsert(r)
    check("store: the double posting is ONE record with both posts as sources",
          len(st.records) == 3 and len(st.records[ids["105"].fingerprint]["sources"]) == 2, len(st.records))
    notice = N.build_event(dict(c101, title="GEANNULEERD: Wilgen knotten bij de Voorbeeldplas"), None, SITE)
    got = st.upsert(notice)
    check("store: 'GEANNULEERD: <title>' tombstones the job it names (notice_fingerprint proves the recipe)",
          got == "notice" and ev.fingerprint in st.tombstones and ev.fingerprint not in st.records,
          (got, list(st.tombstones)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

try:
    from mapsee_supabase_sync import derive_categories
    rec = ev.as_record("2026-10-24T00:00:00Z")
    rec["source"] = ev.source
    check("sync: primary volunteer, secondaries outdoors and kids",
          derive_categories(rec) == ("volunteer", ["outdoors", "kids"]), derive_categories(rec))
    rec111 = ids["111"].as_record("2026-10-24T00:00:00Z")
    check("sync: a listing-only row is volunteer too", derive_categories(rec111)[0] == "volunteer",
          derive_categories(rec111))
except ImportError as exc:                     # pragma: no cover
    check("sync importable", False, exc)

# --------------------------------------------------------------------------- #
# Past skip

web = Web()
rep, rows, seen, _ = run(web, today=date(2026, 11, 7))
check("past: on Saturday the Friday jobs are past - not read, not written, not seen",
      sorted(by_id(rows)) == ["101"] and rep["notes"].get("past") == 5
      and BASE + "heide-schonen-proefveld/" not in web.log and BASE + "bosrand-opschonen/" not in web.log,
      (sorted(by_id(rows)), rep["notes"], web.log))
web = Web()
rep, rows, seen, _ = run(web, today=date(2026, 11, 8))
check("past: after the weekend nothing is read but the listing (no sitemap, no page)",
      rows == [] and web.log == [SITE["listing"]], web.log)

# --------------------------------------------------------------------------- #
# Budget and cursor

web = Web(overrides={SITE["listing"]: Resp(200, CLEAN)})
rep, rows, seen, cache = run(web)
check("run: without it the read is complete (10 of the pager's 10)", rep["complete"] is True
      and rep["listed"] == 10, (rep["complete"], rep["listed"]))

web = Web(overrides={SITE["listing"]: Resp(200, CLEAN)})
rep, rows, seen, cache = run(web, deadline=3.5)
ids = by_id(rows)
check("budget: listing, sitemap and 2 pages, then the deadline",
      rep["requests"] == 4 and rep["stopped"] == "run deadline reached", (rep["requests"], rep["stopped"]))
check("budget: every listed job is still written - unread pages from the listing",
      sorted(ids) == ["101", "105", "106", "110", "111"] and rep["details"] == {"read": 2, "cached": 0,
                                                                                "listing only": 4},
      (sorted(ids), rep["details"]))
check("budget: the read is still complete (the listing decides which jobs exist)", rep["complete"] is True)
check("budget: the cache holds the two pages read", sorted(cache["pages"]) ==
      [BASE + "heide-schonen-proefveld/", BASE + "wilgen-knotten-voorbeeldplas/"], sorted(cache["pages"]))
check("budget: the pages left are counted", rep["notes"].get("pages left for the next run") == 4, rep["notes"])
check("budget: a row written from the listing has the same fingerprint as a read one",
      ids["105"].fingerprint == N.fingerprint_for("Bosrand opschonen", date(2026, 11, 6), ((9, 30), (16, 0)),
                                                  "Maarnveld"))

web2 = Web()
rep2, rows2, _, cache2 = run(web2, cache=cache)
check("resume: the next run reads only the 4 pages it had not; the 2 cached (one full) cost nothing",
      rep2["requests"] == 6 and BASE + "wilgen-knotten-voorbeeldplas/" not in web2.log
      and BASE + "heide-schonen-proefveld/" not in web2.log
      and rep2["details"] == {"read": 2, "cached": 2, "listing only": 1},
      (rep2["requests"], rep2["details"], web2.log))
check("resume: the cached page still gives its row its description",
      by_id(rows2)["101"].description.startswith("Meeting point:"))

moved = SITEMAP.replace("2026-10-10T08:27:26", "2026-10-23T08:27:26")
web3 = Web(overrides={SITE["sitemap"]: Resp(200, moved)})
rep3, rows3, _, cache3 = run(web3, cache=json.loads(json.dumps(cache2)))
check("lastmod: a page whose lastmod moved is read again; unchanged ones are not",
      BASE + "wilgen-knotten-voorbeeldplas/" in web3.log and BASE + "bosrand-opschonen/" not in web3.log,
      web3.log)

aged = json.loads(json.dumps(cache2))
for u in aged["pages"]:
    aged["pages"][u]["read"] = (TODAY - timedelta(days=8)).isoformat()
aged["pages"][BASE + "bosrand-opschonen/"]["read"] = (TODAY - timedelta(days=9)).isoformat()
web4 = Web()
rep4, _, _, _ = run(web4, cache=aged, site=dict(SITE, max_refresh_per_run=1))
check("age: past refresh_days, the OLDEST read is refreshed first, one per run here",
      [u for u in web4.log if u.startswith(BASE)].count(BASE + "bosrand-opschonen/") == 1
      and BASE + "wilgen-knotten-voorbeeldplas/" not in web4.log, web4.log)

wrong = json.loads(json.dumps(cache2))
wrong["pages"][BASE + "bosrand-opschonen-2/"]["detail"]["clock"] = "08:00-12:00"
web5 = Web()
run(web5, cache=wrong)
check("cache: a cached page that disagrees with the listing is read again",
      BASE + "bosrand-opschonen-2/" in web5.log and BASE + "bosrand-opschonen/" not in web5.log, web5.log)

check("cache: jobs gone from the finder leave the cache", N.load_cache(None)["pages"] == {})
gone = json.loads(json.dumps(cache2))
gone["pages"][BASE + "oude-klus/"] = {"lastmod": None, "read": "2026-10-20", "detail": {}}
run(Web(), cache=gone)
check("cache: a URL the listing no longer shows is dropped", BASE + "oude-klus/" not in gone["pages"])

# --------------------------------------------------------------------------- #
# Refusals and failures

web = Web(overrides={BASE + "bosrand-opschonen/": Resp(403, "Forbidden")})
rep, rows, _, cache = run(web)
check("403: a refusal on a page stops the reading; no further page is requested",
      rep["stopped"].startswith("REFUSED - HTTP 403") and web.log[-1] == BASE + "bosrand-opschonen/",
      (rep["stopped"], web.log))
check("403: the jobs already decided are still written (unread ones from the listing)",
      sorted(by_id(rows)) == ["101", "105", "106", "110", "111"], sorted(by_id(rows)))


class NoRobots:
    def check(self, url):
        return {"allowed": False, "status": "ok", "rule": "Disallow: /klussen/", "crawl_delay": None}


web = Web()
rep, rows, seen, _ = run(web, robots=NoRobots())
check("robots: a refused listing writes nothing and is not complete",
      rows == [] and web.log == [] and rep["complete"] is False and rep["stopped"].startswith("REFUSED - robots"),
      (rep["stopped"], web.log))

web = Web(overrides={SITE["listing"]: Resp(200, LISTING.replace('"total_rows": 11', '"total_rows": 12'))})
rep, rows, _, _ = run(web)
check("pager: fewer cards than total_rows is NOT complete (no absence may be read from it)",
      rep["complete"] is False and len(rows) == 4, (rep["complete"], len(rows)))
web = Web(overrides={SITE["listing"]: Resp(200, "<html><body>nieuw thema</body></html>")})
rep, rows, _, _ = run(web)
check("markup: a listing with no cards is not complete and writes nothing",
      rep["complete"] is False and rows == [] and rep["listed"] == 0)
web = Web(overrides={SITE["sitemap"]: Resp(500, "oops")})
rep, rows, _, _ = run(web)
check("sitemap: a failed sitemap costs the lastmods, not the run",
      len(rows) == 4 and rep["notes"].get("sitemap not read (pages re-read by age only)") == 1, rep["notes"])
web = Web(overrides={BASE + "bosrand-opschonen/": OSError("timed out")})
rep, rows, _, _ = run(web)
check("network: one timeout costs one page (written from the listing)",
      sorted(by_id(rows)) == ["101", "105", "106", "111"] and rep["notes"].get("network error, page skipped") == 1,
      (sorted(by_id(rows)), rep["notes"]))

# --------------------------------------------------------------------------- #
# main() on the real config

check("config: one site, on natuurwerkdag.nl, volunteer, with _found and _measured",
      len(CFG["sites"]) == 1 and SITE["listing"].startswith("https://www.natuurwerkdag.nl/")
      and SITE["sitemap"].startswith("https://www.natuurwerkdag.nl/") and SITE["category"] == "volunteer"
      and SITE["_found"].startswith("manual:2026-10-24") and "_measured" in SITE)

tmp = tempfile.mkdtemp(prefix="nwd-main-")
try:
    store_p, cache_p = os.path.join(tmp, "store.json"), os.path.join(tmp, "cache.json")
    webs = []

    def factory():
        w = Web(overrides={SITE["listing"]: Resp(200, CLEAN)})
        webs.append(w)
        return w

    code = N.main(["--config", os.path.join(HERE, "natuurwerkdag_sources.json"), "--store", store_p,
                   "--cache", cache_p, "--max-minutes", "0"], session_factory=factory, sleep=lambda s: None)
    data = json.load(open(store_p, encoding="utf-8"))
    check("main: exits 0 and writes the store (3 records: the double posting merged)",
          code == 0 and len(data["events"]) == 3, (code, len(data.get("events", []))))
    check("main: robots.txt is read first, with our User-Agent",
          webs[0].log[0] == "https://www.natuurwerkdag.nl/robots.txt"
          and webs[0].headers.get("User-Agent") == N.USER_AGENT, (webs[0].log[:2], webs[0].headers))
    check("main: a complete read is marked for the absence check",
          "natuurwerkdag" in (data.get("complete_reads") or {}), data.get("complete_reads"))
    check("main: full / unplaceable / disagreeing jobs are 'seen'", len(data.get("seen") or []) == 5,
          data.get("seen"))
    saved = json.load(open(cache_p, encoding="utf-8"))
    check("main: the cache file is written", saved["version"] == N.CACHE_VERSION and len(saved["pages"]) == 4,
          len(saved["pages"]))
    dry = os.path.join(tmp, "dry")
    os.mkdir(dry)
    N.main(["--config", os.path.join(HERE, "natuurwerkdag_sources.json"), "--store", os.path.join(dry, "s.json"),
            "--cache", os.path.join(dry, "c.json"), "--dry-run"], session_factory=Web, sleep=lambda s: None)
    check("main: --dry-run writes neither store nor cache", os.listdir(dry) == [], os.listdir(dry))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"{'FAILED' if fails else 'passed'}: {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
