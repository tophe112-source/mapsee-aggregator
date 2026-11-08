#!/usr/bin/env python3
"""
test_discover_fedi.py — Mobilizon's two directories and Gancio's discovery.

Prints one line per case and exits non-zero on failure, like the others. No
network: every directory and instance is a fake session answering short
inline snippets shaped like the live answers of 2026-11-07/08.

What is pinned is what the measurements found:

  * ONE DIRECTORY WAS A SINGLE POINT OF FAILURE. instances.joinmobilizon.org
    was unreachable from the runners on 27 daily runs in a row (2026-10-12 to
    11-07) and each said so in a line the step summary never shows. FediDB is
    read beside it and the two are unioned by host; when NEITHER answers the
    run prints `  FLAG ... UNREAD`, which the summary's discover grep shows.
  * FEDIDB IS CURSOR-PAGED, and the paging is bounded: a listing that
    outgrows FEDIDB_MAX_PAGES says so instead of reading its head quietly.
  * A HOST ONLY FEDIDB KNOWS IS NOT GATED ON A COUNT. Its status_count does
    not predict upcoming events (63 upcoming at status_count 0); joinmobilizon's
    own rows keep their totalLocalEvents gate. Every candidate gets one
    searchEvents probe instead, because 14 of the 32 live FediDB-only hosts on
    2026-11-08 served the configured instances' events back (federation), and
    verify passes a mirror like any other instance.
  * GANCIO: a redirect alias, a federated mirror or aggregator, a demo, an
    adult calendar and an online-only one are each refused before verify;
    configured, declined and ledger-dead hosts are never asked at all.
  * THE ENTRY IS SHAPED LIKE THE HAND-CURATED ONES: type, name, base_url,
    category community, within_days 180, max 500, default_country from the
    TLD (a NAME), unless the instance's own time zone is on another continent.
  * FROM THE REVIEW OF 2026-11-08: each threshold's KEPT side just inside it
    (2 of 8 shared, 3 of 6 online, 3 of 10 adult-tagged), so a mutant that
    moves one fails here; an alias landing on a declined or ledger-parked host;
    a 401/403/429 parked in the ledger for DEAD_TTL; a Mobilizon candidate whose
    own events (by `url`) a configured instance serves, and one over
    MOBILIZON_MAX_TOTAL held; a `--max-minutes` deadline that leaves the pool
    cut, and so proposes nothing; adult words that matched two French towns, a
    musical, an idiom and a swing club; and the two all-advertising Mobilizon
    instances declined in the REAL mobilizon_sources.json, which this code
    must not ship without.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

os.environ["MAPSEE_TODAY"] = "20261108"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog_curate as C  # noqa: E402

checks = []


def check(cond, label):
    checks.append((bool(cond), label))


# The workflow's step summary keeps these lines of discover's output and no
# others (curate-catalog.yml, "Discover, verify and merge").
SUMMARY_RX = re.compile(r"^(skipped:|  advanced |  .*UNREAD|  .*SKIPPING)")


class Resp:
    def __init__(self, code=200, body=None, url="", text=None):
        self.status_code, self._body, self.url = code, body, url
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode("utf-8")
        self.headers = {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class Fake:
    """A session whose answers come from `route(url, params)`; records every ask."""

    headers = {"User-Agent": C.UA}

    def __init__(self, route):
        self.route, self.calls = route, []

    def get(self, url, params=None, timeout=None, allow_redirects=True, **_kw):
        self.calls.append((url, dict(params or {})))
        ans = self.route(url, dict(params or {}))
        if isinstance(ans, Exception):
            raise ans
        return ans

    def post(self, url, json=None, timeout=None, headers=None, **_kw):
        self.calls.append((url, dict(json or {})))
        ans = self.route(url, dict(json or {}))
        if isinstance(ans, Exception):
            raise ans
        return ans

    def asked(self, host, path=""):
        return [u for u, _p in self.calls if urlparse(u).hostname == host and urlparse(u).path.startswith(path)]


def run(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        res = fn(*a, **kw)
    return res, out.getvalue()


# ---------------------------------------------- fixtures
T0 = 1794096000            # 2026-11-08T00:00:00Z, the MAPSEE_TODAY window's start
DAY = 86400


def ev(title, day=1, coords=True, place="Hall", tags=()):
    lat = 45.07 if coords else None
    return {"id": abs(hash((title, day))) % 10 ** 6, "title": title, "start_datetime": T0 + day * DAY,
            "end_datetime": T0 + day * DAY + 7200, "tags": list(tags),
            "place": {"name": place, "address": "Via Roma 1", "latitude": lat, "longitude": 7.68 if coords else None}}


def fedidb_rows(*domains):
    return [{"id": i, "domain": d, "description": None, "location": {"country": "Austria"},
             "software": {"slug": "gancio"}, "stats": {"status_count": 10, "user_count": 3}}
            for i, d in enumerate(domains)]


def fedidb_route(rows, page=40, fail_from=None, loop=False):
    """FediDB's /v1/servers, paged by an opaque cursor like the live one."""
    def answer(params):
        n = int(params.get("cursor", "c0")[1:]) if params.get("cursor") else 0
        if fail_from is not None and n >= fail_from:
            return Resp(502, None, C.FEDIDB_SERVERS, "bad gateway")
        chunk = rows[n * page:(n + 1) * page]
        nxt = f"c{n + 1}" if (n + 1) * page < len(rows) else None
        if loop:
            nxt = "c1"
        return Resp(200, {"data": chunk, "links": {}, "meta": {"per_page": page, "next_cursor": nxt}})
    return answer


GANCIO_ORG_HTML = """<html><body>
<a href="https://fedidb.org/software/gancio">FediDB</a>
<a href="https://framagit.org/les/gancio/-/edit/main/docs/instances.md">edit</a>
<a href="https://www.orgonly.example">Org-only instance</a>
<a href="https://aggr.example">aggr</a>
</body></html>"""


# ---------------------------------------------- 1. FediDB paging
rows = fedidb_rows(*[f"h{i}.example" for i in range(95)])
s = Fake(lambda u, p: fedidb_route(rows)(p))
stats = {}
(got, why), out = run(C._fedidb_servers, s, "gancio", stats=stats)
check(why is None and len(got) == 95 and stats["pages"] == 3,
      f"fedidb pages by cursor to the end: 95 rows in 3 pages ({len(got or [])}, {stats})")
check([p.get("cursor") for _u, p in s.calls] == [None, "c1", "c2"]
      and all(p.get("software") == "gancio" and p.get("limit") == C.FEDIDB_PAGE for _u, p in s.calls),
      f"...each page asks software=gancio, limit {C.FEDIDB_PAGE}, and the cursor it was handed")
s = Fake(lambda u, p: fedidb_route(rows)(p))
(got, why), out = run(C._fedidb_servers, s, "gancio", max_pages=2)
check(len(got) == 80 and len(s.calls) == 2 and any(SUMMARY_RX.match(l) and "FLAG" in l for l in out.splitlines()),
      f"a listing past max_pages stops there and prints a FLAG the summary shows ({out.strip()})")
s = Fake(lambda u, p: fedidb_route(rows, loop=True)(p))
(got, why), out = run(C._fedidb_servers, s, "gancio")
check(len(s.calls) == 2 and why is None,
      f"a cursor handed back twice ends the paging instead of looping ({len(s.calls)} asks)")
s = Fake(lambda u, p: Resp(403, None, u, "forbidden"))
(got, why), out = run(C._fedidb_servers, s, "mobilizon")
check(got is None and "403" in why and len(s.calls) == 1,
      f"a 403 on the first page is the answer: None, why, and not asked again ({why})")
s = Fake(lambda u, p: fedidb_route(rows, fail_from=1)(p))
(got, why), out = run(C._fedidb_servers, s, "gancio")
check(why is None and len(got) == 40 and "kept the 40" in out,
      f"a later page that fails keeps what was read ({len(got or [])})")

# ---------------------------------------------- isolation: a temp HERE
_keep = (C.HERE, C.LEDGER_FILE, C.CURSOR_FILE)
tmp = tempfile.mkdtemp(prefix="fedi-test-")
C.HERE = tmp
C.LEDGER_FILE = os.path.join(tmp, "curation_ledger.json")
C.CURSOR_FILE = os.path.join(tmp, "curation_cursor.json")
try:
    json.dump({"sites": [{"name": "Configured M", "base_url": "https://mob.configured.example"}],
               "_not_included": {"https://keskonfai.example": "declined"}},
              open(os.path.join(tmp, "mobilizon_sources.json"), "w"))

    # ------------------------------------------ 2. Mobilizon: the union
    JM = {"data": [
        {"host": "busy.example.fr", "name": "Busy", "totalLocalEvents": 900, "health": 90},
        {"host": "quiet.example", "name": "Quiet", "totalLocalEvents": 2, "health": 90},
        {"host": "mob.configured.example", "name": "Configured", "totalLocalEvents": 50, "health": 90},
    ]}
    FD_MOB = [{"domain": "quiet.example", "stats": {"status_count": 5000}},
              {"domain": "www.busy.example.fr", "stats": {"status_count": 1}},
              {"domain": "zero.example.de", "stats": {"status_count": 0}},
              {"domain": "big.example.it", "stats": {"status_count": 9000}},
              {"domain": "follower.example.nl", "stats": {"status_count": 800}},
              {"domain": "past.example", "stats": {"status_count": 700}},
              {"domain": "refuser.example", "stats": {"status_count": 600}},
              {"domain": "edge.example.de", "stats": {"status_count": 500}},
              {"domain": "once.example.pt", "stats": {"status_count": 400}},
              {"domain": "followed.example.es", "stats": {"status_count": 300}},
              {"domain": "huge.example.de", "stats": {"status_count": 200}},
              {"domain": "test2.keskonfai.example", "stats": {"status_count": 1700}},
              {"domain": "keskonfai.example", "stats": {"status_count": 1700}}]

    def mev(title, day, geom=True, on=None):
        """`on`: the instance the event was published on, as its `url` says."""
        e = {"title": title, "beginsOn": f"2026-{11 + (8 + day) // 31:02d}-{(8 + day) % 31 or 1:02d}T19:00:00Z",
             "physicalAddress": {"geom": "7.6;45.0"} if geom else None}
        if on:
            e["url"] = f"https://{on}/events/{abs(hash(title)) % 10 ** 6}"
        return e

    # The configured instance's sample is its FAR future (searchEvents' default
    # order), so it never meets the near-term events of followed.example.es by
    # title; their `url` says whose they are. once.example.pt has one there.
    CONF_M = ([mev(f"Configured talk {i}", i, on="mob.configured.example") for i in range(1, 7)]
              + [mev("Followed far 1", 80, on="followed.example.es"),
                 mev("Followed far 2", 81, on="followed.example.es"),
                 mev("Once far", 82, on="once.example.pt")])
    SEARCH = {
        "busy.example.fr": [mev("Busy 1", 1), mev("Busy 2", 2)],
        "quiet.example": [mev("Quiet 1", 3)],
        "zero.example.de": [mev("Zero 1", 2)],
        "big.example.it": [mev("Big 1", 4), mev("Configured talk 1", 1)],
        # follows the configured instance and serves its events back
        "follower.example.nl": CONF_M[:5] + [mev("Own one", 9)],
        # only events that began long ago: searchEvents still answers them
        "past.example": [{"title": "IPTV deals", "beginsOn": "2025-01-01T10:00:00Z", "physicalAddress": None}],
        "refuser.example": 403,
        "edge.example.de": [mev("Edge 1", 1, on="edge.example.de")],
        "once.example.pt": [mev("Once near", 2, on="once.example.pt"), mev("Once near 2", 3, on="once.example.pt")],
        "followed.example.es": [mev("Followed near 1", 1, on="followed.example.es"),
                                mev("Followed near 2", 2, on="followed.example.es")],
        "huge.example.de": [mev("Funding call", 5, on="huge.example.de"), mev("Trade fair", 6, on="huge.example.de")],
        "mob.configured.example": CONF_M,
    }
    # searchEvents' `total`; by default ten times what the sample shows.
    TOTALS = {"huge.example.de": C.MOBILIZON_MAX_TOTAL + 1, "edge.example.de": C.MOBILIZON_MAX_TOTAL}

    def mob_route(jm_ok=True, fd_ok=True, slow=None, fd_pages=1):
        """`slow`: {host: seconds} a probe of that host takes. `fd_pages`: how
        many pages FediDB's listing is cut into."""
        def r(u, p):
            if u == C.MOBILIZON_DIRECTORY:
                return Resp(200, JM, u) if jm_ok else ConnectionError("502 Bad Gateway")
            if u == C.FEDIDB_SERVERS:
                if not fd_ok:
                    return ConnectionError("Max retries exceeded")
                return fedidb_route(FD_MOB, page=-(-len(FD_MOB) // fd_pages))(p)
            if urlparse(u).path == "/api":
                assert p["variables"] == {"b": "2026-11-08T00:00:00Z", "l": C.MOBILIZON_PROBE_LIMIT}, p
                assert " url " in p["query"], "the probe asks each event's url"
                host = urlparse(u).hostname
                if slow and host in slow:
                    time.sleep(slow[host])
                ans = SEARCH[host]
                if isinstance(ans, int):
                    return Resp(ans, None, u, "forbidden")
                return Resp(200, {"data": {"searchEvents": {"total": TOTALS.get(host, len(ans) * 10),
                                                            "elements": ans}}}, u)
            return ConnectionError("unexpected " + u)
        return r

    s = Fake(mob_route())
    mled = {}
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), mled, 50)
    hosts = [e["base_url"] for e in found.values()]
    check(hosts == ["https://busy.example.fr", "https://big.example.it", "https://edge.example.de",
                    "https://once.example.pt", "https://zero.example.de"],
          f"union by host: joinmobilizon's busiest first, then FediDB-only by status_count ({hosts})")
    check(skipped["too_quiet"] == 1 and "https://quiet.example" not in hosts and not s.asked("quiet.example"),
          "a host joinmobilizon gates as too quiet stays out though FediDB also lists it, unasked")
    check("https://zero.example.de" in hosts,
          "a FediDB-only host with status_count 0 is not gated on it (63 upcoming at 0, measured)")
    check(skipped["configured"] == 1 and skipped["declined"] == 1 and skipped["test_or_demo"] == 1,
          f"configured, declined, and a test copy of a declined one are skipped ({skipped})")
    check(hosts.count("https://busy.example.fr") == 1 and len(s.asked("busy.example.fr", "/api")) == 1,
          "www.busy.example.fr in FediDB and busy.example.fr in joinmobilizon are one host, asked once")
    check("https://follower.example.nl" not in hosts
          and any(l.startswith("  mirror: follower.example.nl 83%") for l in out.splitlines()),
          "an instance serving the configured instance's events back is a mirror (f.termine.di.day)")
    check("https://big.example.it" in hosts,
          "one configured event among a candidate's own is not a mirror")
    check("https://past.example" not in hosts and skipped["quiet"] == 1,
          "events that all began before today are not upcoming (events.doesliverpool.com)")
    check(skipped["refused"] == 1 and len(s.asked("refuser.example")) == 1,
          "a 403 is a refusal, asked once")
    row = mled.get("refuser.example") or {}
    check(row.get("status") == "fail" and row.get("type") == "mobilizon" and "http 403" in row.get("reason", "")
          and row.get("checked") == 20261108 and C._dead_recently(mled, "refuser.example"),
          f"...and parked in the ledger for DEAD_TTL, as a verify parks a 403 ({row})")
    check(len(s.asked("mob.configured.example", "/api")) == 1,
          "each configured instance is probed once, for the mirror test")
    check("https://followed.example.es" not in hosts
          and any(l.startswith("  mirror: followed.example.es 2 of the configured instances' sampled events "
                               "are its own (url on followed.example.es), served by mob.configured.example")
                  for l in out.splitlines()),
          "a candidate whose own events (by url) a configured instance serves is a mirror, though no title "
          "meets: the configured sample is its far future")
    check("https://once.example.pt" in hosts,
          f"one of a candidate's events served by a configured instance is not a mirror "
          f"(MOBILIZON_SERVED_MIN {C.MOBILIZON_SERVED_MIN})")
    check(skipped["mirror"] == 2, f"two mirrors, one each way ({skipped['mirror']})")
    check("https://huge.example.de" not in hosts and skipped["too_big"] == 1
          and any(l.startswith("  too big: huge.example.de answers 1,001 upcoming; held")
                  for l in out.splitlines()),
          "a candidate answering more than MOBILIZON_MAX_TOTAL upcoming is held for a human "
          "(politikvonunten.org, 5,943)")
    check("https://edge.example.de" in hosts and "huge.example.de" not in mled,
          "...exactly MOBILIZON_MAX_TOTAL is proposed, and a held one is not parked in the ledger")
    e = found.get(C._canon("https://zero.example.de"))
    check(e == {"type": "mobilizon", "name": "zero.example.de", "base_url": "https://zero.example.de",
                "category": "community", "limit": 100, "max_pages": 5, "default_country": "Germany"},
          f"a FediDB-only entry has the Mobilizon shape, named for its host ({e})")
    check("FLAG" not in out and "requests: searchEvents 10 candidate(s) + 1 configured" in out,
          "both directories answering prints no FLAG, and the request count")

    s = Fake(mob_route())
    (_f, sk2), _o = run(C._discover_mobilizon, s, C._config_keys(), mled, 50)
    check(not s.asked("refuser.example") and sk2["ledger"] == 1 and sk2["refused"] == 0,
          "the next run skips the parked refusal without asking it")

    s = Fake(mob_route(jm_ok=False))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50)
    check(len(found) == 6 and C._canon("https://quiet.example") in found and "FLAG" not in out
          and "FediDB alone" in out,
          f"joinmobilizon down: FediDB alone proposes, with no FLAG ({len(found)})")
    s = Fake(mob_route(fd_ok=False))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50)
    check([e["base_url"] for e in found.values()] == ["https://busy.example.fr"] and "FLAG" not in out,
          "FediDB down: joinmobilizon alone, as before")
    s = Fake(mob_route(jm_ok=False, fd_ok=False))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50)
    flag = [l for l in out.splitlines() if l.startswith("  FLAG ")]
    check(found == {} and len(flag) == 1 and SUMMARY_RX.match(flag[0])
          and "joinmobilizon" in flag[0] and "fedidb" in flag[0],
          f"EVERY directory down: one `  FLAG` line the step summary keeps, naming both ({flag})")
    (lim, _sk), _o = run(C._discover_mobilizon, Fake(mob_route()), C._config_keys(), {}, 1)
    check([e["base_url"] for e in lim.values()] == ["https://busy.example.fr"], "the limit cuts what is proposed")

    # The wall clock. A deadline already gone: FediDB's first page is read
    # (paging stops after it), no instance is asked, nothing is proposed.
    s = Fake(mob_route(fd_pages=3))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50, deadline=1.0)
    flag = [l for l in out.splitlines() if l.startswith("  FLAG mobilizon:")]
    check(found == {} and not [u for u, _p in s.calls if urlparse(u).path == "/api"]
          and len(s.asked("api.fedidb.org")) == 1 and len(flag) == 1 and SUMMARY_RX.match(flag[0])
          and any("FLAG fedidb mobilizon: the deadline passed before page 2" in l for l in out.splitlines()),
          f"past the deadline: one FediDB page, no probe, nothing proposed, a FLAG the summary keeps ({flag})")
    # One that passes while the configured instance is read: it went first, so
    # the yardstick is whole and the candidates are what go unread.
    s = Fake(mob_route(slow={"mob.configured.example": 1.0}))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50, workers=1,
                                deadline=time.time() + 0.5)
    check(found == {} and len(s.asked("mob.configured.example", "/api")) == 1
          and not s.asked("busy.example.fr", "/api") and skipped["unread"] == 10
          and any("FLAG mobilizon: 10 host(s) UNREAD past the deadline (0 of them configured)" in l
                  for l in out.splitlines()),
          f"a deadline mid-run: configured read first, every candidate unread, nothing proposed ({skipped['unread']})")

    # ------------------------------------------ 3. Gancio
    json.dump({"crawl_delay": 2, "sites": [
        {"name": "Conf A", "base_url": "https://confa.example", "category": "community"},
        {"name": "Conf R", "base_url": "https://confr.example", "category": "community"},
    ], "_not_included": {"https://declined.example": "no", "https://kinkcalendar.example": "adult"}},
        open(os.path.join(tmp, "gancio_sources.json"), "w"))
    led = {"deadledger.example": {"type": "gancio", "status": "fail", "reason": "http 500",
                                  "checked": 20261101},
           "parked.example": {"type": "gancio", "status": "fail", "reason": "http 500",
                              "checked": 20261105}}
    CONF_A = [ev("Assemblea", 1), ev("Concerto", 2), ev("Cena", 3)]
    AGG_OWN = [ev(f"Own {i}", i) for i in range(1, 2)]
    CITY1 = [ev(f"City1 show {i}", i) for i in range(1, 6)]
    CITY2 = [ev(f"City2 show {i}", i) for i in range(1, 5)]
    INSTANCES = {
        # host: (final url host or None, events or status, nodeinfo {name, desc, tz})
        "fine.example.it": (None, [ev("Mercato", 1), ev("Film", 2, coords=False)],
                            {"nodeName": "Fine Agenda", "nodeDescription": "Agenda condivisa",
                             "nodeTimezone": "Europe/Rome"}),
        "plain.example.org": (None, [ev("Talk", 4)], {"nodeName": "Gancio", "nodeDescription": "x"}),
        "pun.example.in": (None, [ev("Sagra", 4)], {"nodeName": "Sa Pratza", "nodeDescription": "",
                                                    "nodeTimezone": "Europe/Rome"}),
        "alias-old.example": ("alias-new.example", [ev("Same", 1), ev("Same 2", 2)], {}),
        "alias-new.example": (None, [ev("Same", 1), ev("Same 2", 2)], {"nodeName": "New"}),
        "events.confr.example": (None, [ev("R1", 1), ev("R2", 2)], {}),
        "mirror.example": (None, list(CONF_A), {}),
        "twin.example.net": (None, [ev("Twin 1", 1), ev("Twin 2", 2), ev("Twin 3", 3)], {}),
        "twin-copy.deploy.example.net": (None, [ev("Twin 1", 1), ev("Twin 2", 2), ev("Twin 3", 3)], {}),
        "city1.example": (None, CITY1, {}),
        "city2.example": (None, CITY2, {}),
        "aggr.example": (None, CITY1 + CITY2 + CONF_A + AGG_OWN, {}),
        "origin.example": (None, [ev("Coord 1", 1, coords=True), ev("Coord 2", 2, coords=True)], {}),
        "onething.example": (None, [ev("Cena", 3), ev("Something else", 9)], {}),
        "club.example": (None, [ev("Night", 1)], {"nodeName": "Club", "nodeDescription": "For the BDSM community"}),
        "tagged.example": (None, [ev("A", 1, tags=["fetish"]), ev("B", 2, tags=["kink"]),
                                  ev("C", 3, tags=["bdsm"]), ev("D", 4)], {}),
        "general.example": (None, [ev("Sex Pistols tribute", 1)] + [ev(f"Gig {i}", i) for i in range(2, 31)]
                            + [ev("Kink talk", 31, tags=["kink"])], {}),
        "demo.example.org": (None, [ev("x", 1)], {}),
        "pruebas.example": (None, [ev("x", 1)], {"nodeName": "Gancio para pruebas",
                                                  "nodeDescription": "Publica aquí eventos de prueba"}),
        "demo-protest.example.de": (None, [ev("Demo am Samstag", 1)],
                                    {"nodeName": "Demo-Kalender", "nodeDescription": "Demo und Kundgebung"}),
        "zoom.example": (None, [ev(f"Call {i}", i, coords=False, place="online") for i in range(1, 6)], {}),
        "empty.example": (None, [], {}),
        "refuses.example": (None, 403, {}),
        "notgancio.example": (None, 404, {}),
        "orgonly.example": (None, [ev("Org only", 2)], {"nodeName": "Org Only"}),
        "confa.example": (None, CONF_A, {}),
        "declined.example": (None, [ev("never asked", 1)], {}),
        "deadledger.example": (None, [ev("never asked", 1)], {}),
        "kinkcalendar.example": (None, [ev("never asked", 1)], {}),
        "kinkyparty.example": (None, [ev("never asked", 1)], {}),
        # Listed hosts that redirect to a host the ledger has parked, and to one
        # declined on the record (old.example -> new.example, review 2026-11-08).
        "parked.example": (None, [ev("never asked", 1)], {}),
        "toparked.example": ("parked.example", [ev("P1", 1), ev("P2", 2)], {}),
        "todeclined.example": ("declined.example", [ev("D1", 1), ev("D2", 2)], {}),
        # Each rule's KEPT side, just inside its threshold: 2 of 8 a configured
        # site's (0.25; gancio.pl's 18 of 72), 3 of 6 online (0.5;
        # organize.frontrange.co), 3 of 10 adult-tagged (0.3 < 1/3).
        "partial.example": (None, [ev("Assemblea", 1), ev("Concerto", 2)]
                            + [ev(f"Partial own {i}", 10 + i) for i in range(6)], {}),
        "halfonline.example": (None, [ev(f"Webinar {i}", i, coords=False, place="online") for i in range(1, 4)]
                               + [ev(f"Walk {i}", 3 + i) for i in range(1, 4)], {}),
        "someadult.example": (None, [ev(f"Party {i}", i, tags=["fetish"]) for i in range(1, 4)]
                              + [ev(f"Talk {i}", 3 + i) for i in range(1, 8)], {}),
    }
    # The configured devent-style hub carries origin.example's events WITHOUT
    # coordinates, so the origin is still worth reading.
    CONF_SETS = {"confa.example": CONF_A + [ev("Coord 1", 1, coords=False), ev("Coord 2", 2, coords=False)],
                 "confr.example": None}
    LISTED = [h for h in INSTANCES if h not in ("orgonly.example",)] + ["www.fine.example.it"]

    def gancio_route(fd_ok=True, org_ok=True, slow=None):
        """`slow`: {host: seconds} its /api/events takes to answer."""
        fd = fedidb_rows(*LISTED)

        def r(u, p):
            pu = urlparse(u)
            if u == C.FEDIDB_SERVERS:
                return fedidb_route(fd)(p) if fd_ok else ConnectionError("timed out")
            if u == "https://gancio.org/robots.txt":
                return Resp(404, None, u, "not found")
            if u == C.GANCIO_DIRECTORY_PAGE:
                return Resp(200, None, u, GANCIO_ORG_HTML) if org_ok else Resp(503, None, u, "down")
            host = pu.hostname
            if pu.path == "/api/events":
                q = parse_qs(pu.query)
                assert q["start"] == [str(T0)] and q["max"] == ["500"], u
                if slow and host in slow:
                    time.sleep(slow[host])
                if host == "confa.example":
                    return Resp(200, CONF_SETS["confa.example"], u)
                if host == "confr.example":   # rheinmain.social's shape: a 301 to events.<host>
                    return Resp(200, INSTANCES["events.confr.example"][1],
                                u.replace("confr.example", "events.confr.example"))
                final, evs, _ni = INSTANCES[host]
                if isinstance(evs, int):
                    return Resp(evs, None, u, "<html>no</html>")
                return Resp(200, evs, u.replace(host, final) if final else u)
            if pu.path == "/.well-known/nodeinfo/2.0":
                ni = INSTANCES.get(host, (None, None, {}))[2]
                return Resp(200, {"metadata": ni, "software": {"name": "gancio"}}, u)
            return ConnectionError("unexpected " + u)
        return r

    s = Fake(gancio_route())
    gled = dict(led)
    (found, skipped), out = run(C._discover_gancio, s, C._config_keys(), gled, 100, workers=3)
    got = {e["base_url"]: e for e in found.values()}
    lines = out.splitlines()

    check(not s.asked("declined.example") and not s.asked("deadledger.example")
          and not s.asked("kinkcalendar.example") and not s.asked("parked.example"),
          "declined, ledger-dead and declined-adult hosts are never asked at all")
    check(skipped["configured"] == 1 and len(s.asked("confa.example", "/api/events")) == 1,
          f"a configured host is skipped as a candidate and probed once, as configured ({skipped['configured']})")
    check(skipped["declined"] == 3 and skipped["ledger"] == 3, f"declined 3, ledger 3 ({skipped})")
    check(any("alias: toparked.example -> parked.example, which the ledger has parked" in l for l in lines)
          and "https://parked.example" not in got and "https://toparked.example" not in got
          and not s.asked("toparked.example", "/.well-known"),
          "a listed host that redirects to a ledger-parked host is skipped as `ledger`, no nodeinfo asked")
    check(any("alias: todeclined.example -> declined.example, which is declined on the record" in l for l in lines)
          and "https://declined.example" not in got and "https://todeclined.example" not in got
          and not s.asked("todeclined.example", "/.well-known"),
          "a listed host that redirects to a declined host is skipped as `declined`, no nodeinfo asked")
    check(not s.asked("kinkyparty.example") and any("adult: kinkyparty.example (by its name)" in l for l in lines),
          "an adult host name is refused before any request")
    check(any("alias: alias-old.example -> alias-new.example, already a candidate" in l for l in lines)
          and "https://alias-new.example" in got and "https://alias-old.example" not in got,
          "a host that redirects to another candidate is an alias; the host it lands on is kept")
    check(any("alias: events.confr.example, where a configured site lands" in l for l in lines)
          and "https://events.confr.example" not in got,
          "a listed host that a CONFIGURED site redirects to is an alias, though it never redirects itself")
    check(skipped["alias"] == 2, f"two aliases ({skipped['alias']})")
    check("https://mirror.example" not in got and any(l.startswith("  mirror: mirror.example 100%") for l in lines),
          "a candidate whose events are all a configured site's is a mirror")
    check("https://twin.example.net" in got and "https://twin-copy.deploy.example.net" not in got,
          "two hosts with one calendar: the one with fewer labels is kept, the other is a mirror")
    check("https://city1.example" in got and "https://city2.example" in got and "https://aggr.example" not in got,
          "an aggregator loses to its smaller sources (smallest first), not the other way round")
    check("https://origin.example" in got,
          "an origin whose only other copy has no coordinates is kept (devent.cz / akce.nolog.cz)")
    check("https://onething.example" in got,
          f"one shared event of two is not a mirror (GANCIO_MIRROR_MIN {C.GANCIO_MIRROR_MIN})")
    check(skipped["mirror"] == 3, f"three mirrors ({skipped['mirror']})")
    check("https://partial.example" in got,
          f"2 of 8 events a configured site's (0.25, gancio.pl's 18 of 72) is not a mirror "
          f"(GANCIO_MIRROR_SHARE {C.GANCIO_MIRROR_SHARE})")
    check("https://halfonline.example" in got,
          f"3 of 6 events online (organize.frontrange.co) is not online-only "
          f"(GANCIO_ONLINE_SHARE {C.GANCIO_ONLINE_SHARE})")
    check("https://someadult.example" in got,
          "3 of 10 events adult-tagged is under ADULT_EVENT_SHARE (1/3), and not refused")
    check("https://club.example" not in got and any("adult: club.example ('BDSM'" in l for l in lines),
          "an adult calendar is refused by its nodeinfo description")
    check("https://tagged.example" not in got and any("adult: tagged.example (3 of 4 events" in l for l in lines),
          "...and by the share of its events whose title or tags say so")
    check("https://general.example" in got,
          "a general calendar with one kink-tagged event in 31 and a band called Sex Pistols is not refused")
    check(skipped["adult"] == 3, f"three adult refusals ({skipped['adult']})")
    check("https://demo.example.org" not in got and not s.asked("demo.example.org"),
          "a demo host label is skipped before any request")
    check("https://pruebas.example" not in got and any("test or demo: pruebas.example" in l for l in lines),
          "a test instance known only by its description is skipped (des.convoca.la)")
    check("https://demo-protest.example.de" in got,
          "a German calendar of Demos (protest marches) is not a demo server")
    check(skipped["test_or_demo"] == 2, f"two test/demo ({skipped['test_or_demo']})")
    check("https://zoom.example" not in got and skipped["online_only"] == 1,
          "an online-only calendar places nothing and is skipped")
    check(skipped["quiet"] == 1 and skipped["refused"] == 1 and skipped["not_gancio"] == 1,
          f"empty, 403 and 404 each counted under their own name ({skipped})")
    check(len(s.asked("refuses.example")) == 1, "a 403 is asked exactly once: a refusal is not retried")
    row = gled.get("refuses.example") or {}
    check(row.get("status") == "fail" and row.get("type") == "gancio" and "http 403" in row.get("reason", "")
          and C._dead_recently(gled, "refuses.example") and "refuses.example" not in led,
          f"...and parked in the ledger for DEAD_TTL ({row})")
    s2 = Fake(gancio_route())
    (_f, sk2), _o = run(C._discover_gancio, s2, C._config_keys(), gled, 100, workers=3)
    check(not s2.asked("refuses.example") and sk2["ledger"] == 4 and sk2["refused"] == 0,
          "the next run skips the parked refusal without asking it")
    check(not s.asked("zoom.example", "/.well-known") and not s.asked("empty.example", "/.well-known")
          and s.asked("fine.example.it", "/.well-known/nodeinfo/2.0"),
          "nodeinfo is read only for what is still standing after the events probe")
    check("https://orgonly.example" in got
          and got["https://orgonly.example"]["_found"] == "discover gancio:gancio.org/instances 2026-11-08",
          "a host only gancio.org/instances links is read too, and says where it was found")
    check(len(s.asked("fine.example.it", "/api/events")) == 1,
          "www.fine.example.it and fine.example.it are one host, asked once")

    e = got.get("https://fine.example.it")
    check(e == {"type": "gancio", "name": "Fine Agenda", "base_url": "https://fine.example.it",
                "category": "community", "within_days": 180, "max": 500, "default_country": "Italy",
                "_found": "discover gancio:fedidb 2026-11-08"},
          f"the entry has the gancio_sources.json shape, country a NAME from the TLD ({e})")
    check(got.get("https://plain.example.org", {}).get("name") == "plain.example.org"
          and "default_country" not in got.get("https://plain.example.org", {}),
          "a nodeName of plain 'Gancio' is replaced by the host; .org names no country")
    check("default_country" not in got.get("https://pun.example.in", {"default_country": 1}),
          "a .in instance whose time zone is Europe/Rome gets no 'India' (sapratza.in)")
    total = sum(skipped.values()) + len(found)
    check(total == len({C._fold_host(h) for h in LISTED} | {"orgonly.example", "aggr.example"}),
          f"every listed host is accounted for exactly once ({total})")
    check(any(l.startswith("  requests: fedidb 1 page(s)") for l in lines),
          f"the run reports what it asked ({[l for l in lines if 'requests:' in l]})")
    order = [e["base_url"] for e in found.values()]
    check("https://city1.example" in order and "https://plain.example.org" in order
          and order.index("https://city1.example") < order.index("https://plain.example.org"),
          "most placeable first, so a small limit takes what lands on the map")
    (lim, _sk), _o = run(C._discover_gancio, Fake(gancio_route()), C._config_keys(), led, 2, workers=2)
    check(len(lim) == 2, f"the limit cuts what is proposed ({len(lim)})")

    # Directories failing.
    s = Fake(gancio_route(fd_ok=False))
    (found2, _sk), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=2)
    check("FLAG" not in out and "https://orgonly.example" in {e["base_url"] for e in found2.values()},
          "FediDB down: gancio.org/instances alone, no FLAG")
    s = Fake(gancio_route(fd_ok=False, org_ok=False))
    (found3, _sk), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=2)
    flag = [l for l in out.splitlines() if l.startswith("  FLAG ")]
    check(found3 == {} and len(flag) == 1 and SUMMARY_RX.match(flag[0]),
          f"EVERY Gancio directory down: one FLAG the summary keeps ({flag})")

    # The wall clock and the bound: a pool that is cut proposes nothing.
    def api_asked(sess):
        return [u for u, _p in sess.calls if urlparse(u).path == "/api/events"]
    s = Fake(gancio_route())
    (fd_, sk_), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=2, deadline=1.0)
    flag = [l for l in out.splitlines() if l.startswith("  FLAG gancio:")]
    check(fd_ == {} and not api_asked(s) and len(flag) == 1 and SUMMARY_RX.match(flag[0]),
          f"past the deadline: no instance asked, nothing proposed, a FLAG the summary keeps ({flag})")
    s = Fake(gancio_route(slow={"confa.example": 1.0}))
    (fd_, sk_), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=1,
                          deadline=time.time() + 0.5)
    check(fd_ == {} and api_asked(s) == [u for u in api_asked(s) if urlparse(u).hostname == "confa.example"]
          and len(api_asked(s)) == 1
          and any("FLAG gancio:" in l and "(1 of them configured)" in l for l in out.splitlines()),
          "a deadline mid-probe: the configured sites go first, the candidates go unread, nothing proposed")
    s = Fake(gancio_route(slow={"orgonly.example": 1.0}))
    (fd_, sk_), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=1,
                          deadline=time.time() + 0.5)
    check(fd_ == {} and s.asked("orgonly.example", "/api/events") and not s.asked("fine.example.it", "/.well-known")
          and any(l.startswith("  FLAG gancio: nodeinfo of ") for l in out.splitlines()),
          "a deadline before nodeinfo: no description read, so nothing proposed")
    keep_max = C.GANCIO_MAX_PROBES
    C.GANCIO_MAX_PROBES = 5
    try:
        s = Fake(gancio_route())
        (fd_, sk_), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=2)
    finally:
        C.GANCIO_MAX_PROBES = keep_max
    check(fd_ == {} and not api_asked(s) and any(l.startswith("  FLAG gancio:") and "UNREAD, past GANCIO_MAX_PROBES"
                                                  in l for l in out.splitlines()),
          "a pool past GANCIO_MAX_PROBES is not cut and read: nothing is asked or proposed")

    # ------------------------------------------ 4. the dispatch and the CLI
    check("gancio" in C.BACKENDS, "gancio is a discovery backend")
    real = C._discover_gancio
    seen = []
    real_mob = C._discover_mobilizon

    def fake_discover(*a, **k):
        seen.append((a, k))
        a[2]["refusing.example"] = {"checked": 20261108, "name": "refusing.example", "type": "gancio",
                                    "status": "fail", "reason": "discover gancio: http 403, a refusal"}
        return {"x": {"type": "gancio", "name": "n", "base_url": "https://n.example"}}, {"mirror": 1, "refused": 1}
    C._discover_gancio = C._discover_mobilizon = fake_discover
    real_cats, real_sess = C.curated_categories, C._session
    C.curated_categories = lambda session: ["community", "music"]
    C._session = lambda: Fake(lambda u, p: ConnectionError("no network in tests"))
    cand = os.path.join(tmp, "cand.json")
    try:
        t0 = time.time()
        rc, out = run(C.main, ["catalog_curate.py", "discover", "gancio", "--limit", "5", "--max-minutes", "5",
                               "--out", cand])
        check(rc == 0 and len(seen) == 1 and json.load(open(cand))[0]["base_url"] == "https://n.example"
              and "skipped: 1 mirror" in out,
              "`discover gancio` (positional, as the workflow passes it) runs the gancio backend")
        check(t0 + 290 < seen[0][1].get("deadline", 0) < time.time() + 310,
              f"--max-minutes 5 reaches gancio as a deadline five minutes on ({seen[0][1]})")
        check("refusing.example" in C._load_ledger(),
              "a refusal the backend parked is saved to the ledger file")
        rc, out = run(C.main, ["catalog_curate.py", "discover", "mobilizon", "--max-minutes", "5", "--out", cand])
        check(rc == 0 and len(seen) == 2 and t0 + 290 < seen[1][1].get("deadline", 0) < time.time() + 310,
              "...and mobilizon, the same way")
        rc, out = run(C.main, ["catalog_curate.py", "discover", "mobilizon", "--out", cand])
        check(rc == 0 and len(seen) == 3 and seen[2][1].get("deadline") == 0.0,
              "without --max-minutes there is no deadline (0)")
        rc, out = run(C.main, ["catalog_curate.py", "discover", "gancio", "--category", "music", "--out", cand])
        check(rc == 0 and len(seen) == 3 and "skipped for a category-pinned run" in out
              and json.load(open(cand)) == [],
              "a category-pinned run skips gancio, like mobilizon")
    finally:
        C._discover_gancio, C.curated_categories, C._session = real, real_cats, real_sess
        C._discover_mobilizon = real_mob

    # ------------------------------------------ 5. robots: annual_sources.json
    check(C._ROBOTS_EXTRA[-1] == "annual_sources.json", "annual_sources.json is in _ROBOTS_EXTRA")
    json.dump({"sites": [{"key": "fair", "name": "A Fair", "url": "https://fair.example/"}]},
              open(os.path.join(tmp, "annual_sources.json"), "w"))
    reqs = [r for r in C._configured_requests() if r[0] == "annual_sources.json"]
    check(reqs == [("annual_sources.json", "annual", "A Fair", "GET", "https://fair.example/")],
          f"the robots audit reads an annual site's url ({reqs})")
finally:
    C.HERE, C.LEDGER_FILE, C.CURSOR_FILE = _keep
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------- 6. the rules alone
for h in ("test2.keskonfai.fr", "tests.keskonfai.fr", "mobilizon.demo-dev2.der-smarte-gemeinschaftskalender.de",
          "klimatkalendern-dev.kompismoln.se", "demo.gancio.org", "legacy.gancio.org"):
    check(C._test_instance(h), f"test/demo host: {h}")
for h in ("flypost-gancio.fly.dev", "devent.cz", "dodenver.diy", "mobilizon.fr", "events.techwerkers.nl",
          "dev-meetup.example", "demo-kalender.example.de", "devent.example"):
    check(not C._test_instance(h), f"not a test host: {h}")
for h, word in (("kinkcalendar.site", "kinkcalendar"), ("bootblack.events", "bootblack")):
    check(C._adult_words(C._host_words(h)) == word, f"adult host words: {h}")
for text in ("Your fetish and puppy events calendar", "Shared agenda for the kink, BDSM and fetish community."):
    check(C._adult_words(text), f"adult description: {text!r}")
for text in ("Swingerclub Nacht", "Soirée fétichiste", "Kinky party", "pornographic"):
    check(C._adult_words(text), f"adult: {text!r}")
# Each matched the first version of the rule (review of 2026-11-08): two French
# towns, a musical, a French idiom for a favourite haunt, a swing-dance club.
for text in ("Milwaukee's centralized music calendar.", "Veranstaltungen rund um Dein Haustier",
             "Sex Pistols tribute night", "Leather crafts workshop", "Demo instance",
             C._host_words("agenda.pornic.fr"), "Fête de la mer à Pornichet", "Kinky Boots, the musical",
             "Le lieu fétiche des Rennais", "Lindy Hop Swingers social"):
    check(not C._adult_words(text), f"not adult: {text!r}")
check(C._fold_host("https://WWW.Example.org:443/path") == "example.org", "a host is folded: scheme, www., port, path")
_get, _close = C._probe_sessions(C._session())
_s1 = _get()
check(_s1.headers.get("User-Agent") == C.UA == "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
      and _get() is _s1, "each probe thread's session carries the production User-Agent, one per thread")
_close()

# ---------------------------------------------- 7. the declines this code depends on
# Discovery proposed both on 2026-11-08 and verify passed both (18 of 18):
# meetup.furryfederation.com's 42 upcoming were all betting, drug-sale and
# predatory-conference listings, and mobilizon.public.cat's were IPTV and
# e-bike adverts; mapsee_spam.is_spam refused none. Mobilizon discovery runs on
# the daily schedule and merge needs no human, so the editorial no must be in
# the REAL mobilizon_sources.json before this code ships.
_declined = C._declined_keys()
for h in ("meetup.furryfederation.com", "mobilizon.public.cat"):
    check(C._canon("https://" + h) in _declined,
          f"{h} is declined on the record in mobilizon_sources.json (all advertising, 2026-11-08)")

# ---------------------------------------------- report
bad = [l for ok, l in checks if not ok]
for ok, l in checks:
    print(("ok    " if ok else "FAIL  ") + l)
print(f"\n{len(checks)} cases, {len(bad)} failed")
sys.exit(1 if bad else 0)
