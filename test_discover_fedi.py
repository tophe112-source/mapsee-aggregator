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
  * FROM THE SECOND REVIEW OF 2026-11-08: a candidate's robots.txt is read
    before its first request, and an operator refusing every crawler
    (`Disallow: /` for `*` or for us; gancio.antroposofiachile.net was
    proposed) is parked `refused` and never probed, an unreadable file is
    `fail`, a 403/429 on it a refusal, while a path rule (`Disallow: /admin`,
    `/api/`) does not bind the documented API; the host a redirect lands on
    answers for itself; verify's _robots_gate refuses the same for a
    hand-written gancio or mobilizon entry. A Mobilizon candidate only
    FediDB lists is held for a human (a `  FLAG mobilizon: held` line); its
    sampled titles meet ADULT_EVENT_SHARE; a follower of any one host is a
    mirror (mobilizon.com.br, 52 of 52). No two requests to one operator are
    in flight at once, in any phase. gancio.org/instances is read through
    mapsee_ingest_fairs.Polite.
  * FROM THE THIRD REVIEW OF 2026-11-08: a redirect is a request to another
    host, so no probe lets requests follow one. Each hop is asked with
    allow_redirects=False, and a host it leads to answers robots.txt first
    (or, declined or parked, is not asked at all); refusing, it is parked
    with the name that led there, in either alphabetical order, so no later
    run knocks through the redirect. The fake now makes a followed redirect
    a real second request, which the earlier fixtures never did. A Mobilizon
    follower of one host must also have fewer than MOBILIZON_SERVED_MIN
    events of its own: the share alone refused 20 of the 66 configured
    instances' live samples.
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
from urllib.parse import parse_qs, urljoin, urlparse

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
    def __init__(self, code=200, body=None, url="", text=None, headers=None):
        self.status_code, self._body, self.url = code, body, url
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode("utf-8")
        self.headers = dict(headers or {})

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
        return self._follow(ans, allow_redirects, None)

    def post(self, url, json=None, timeout=None, headers=None, allow_redirects=True, **_kw):
        self.calls.append((url, dict(json or {})))
        ans = self.route(url, dict(json or {}))
        if isinstance(ans, Exception):
            raise ans
        return self._follow(ans, allow_redirects, json)

    def _follow(self, ans, allow, body):
        """A redirect is answered as a server answers one (`moved`), and
        followed only when the caller lets requests follow it: then the next
        hop is a REAL request to the other host, recorded like any other (the
        review of 2026-11-08 found the earlier fixtures swapped the URL and
        never made it). A 307/308 keeps a POST, anything else becomes a GET,
        as requests does."""
        loc = ans.headers.get("Location")
        if not allow or ans.status_code not in REDIRECTS or not loc:
            return ans
        to = urljoin(ans.url, loc)
        if body is not None and ans.status_code in (307, 308):
            return self.post(to, json=body)
        return self.get(to)

    def asked(self, host, path=""):
        return [u for u, _p in self.calls if urlparse(u).hostname == host and urlparse(u).path.startswith(path)]


REDIRECTS = (301, 302, 303, 307, 308)


def moved(u, to, code=301):
    """A server's redirect from `u` to `to`."""
    return Resp(code, None, u, "", headers={"Location": to})


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
              {"domain": "importer.example.nl", "stats": {"status_count": 790}},
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
        # imports them under its OWN urls: only title and start give it away
        "importer.example.nl": [dict(e, url=e["url"].replace("mob.configured.example", "importer.example.nl"))
                                for e in CONF_M[:5]] + [mev("Importer own", 9, on="importer.example.nl")],
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

    def mob_route(jm_ok=True, fd_ok=True, slow=None, fd_pages=1, jm=None, fd=None, search=None,
                  robots=None):
        """`slow`: {host: seconds} a probe of that host takes. `fd_pages`: how
        many pages FediDB's listing is cut into. `jm`, `fd`, `search` replace
        JM, FD_MOB and SEARCH; `robots`: {host: its robots.txt answer}, by
        default a 404 (no file, allow all)."""
        jm = JM if jm is None else jm
        fd_rows = FD_MOB if fd is None else fd
        search = SEARCH if search is None else search

        def r(u, p):
            if u == C.MOBILIZON_DIRECTORY:
                return Resp(200, jm, u) if jm_ok else ConnectionError("502 Bad Gateway")
            if u == C.FEDIDB_SERVERS:
                if not fd_ok:
                    return ConnectionError("Max retries exceeded")
                return fedidb_route(fd_rows, page=-(-len(fd_rows) // fd_pages))(p)
            if urlparse(u).path == "/robots.txt":
                return (robots or {}).get(urlparse(u).hostname) or Resp(404, None, u, "not found")
            if urlparse(u).path == "/api":
                assert p["variables"] == {"b": "2026-11-08T00:00:00Z", "l": C.MOBILIZON_PROBE_LIMIT}, p
                assert " url " in p["query"], "the probe asks each event's url"
                host = urlparse(u).hostname
                if slow and host in slow:
                    time.sleep(slow[host])
                ans = search[host]
                if isinstance(ans, tuple):    # ("->", code, host): a redirect of the API
                    return moved(u, u.replace(host, ans[2], 1), ans[1])
                if isinstance(ans, int):
                    return Resp(ans, None, u, "forbidden")
                return Resp(200, {"data": {"searchEvents": {"total": TOTALS.get(host, len(ans) * 10),
                                                            "elements": ans}}}, u)
            return ConnectionError("unexpected " + u)
        return r

    HELD_RX = re.compile(r"^  FLAG mobilizon: held for a human, only FediDB lists it: (\S+) ")

    def held_of(text):
        """The hosts a run held for a human, in the order it printed them."""
        return ["https://" + m.group(1) for m in map(HELD_RX.match, text.splitlines()) if m]

    s = Fake(mob_route())
    mled = {}
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), mled, 50)
    proposed, held = [e["base_url"] for e in found.values()], held_of(out)
    # What cleared every rule, proposed or held: the rules below are about this.
    hosts = proposed + held
    check(hosts == ["https://busy.example.fr", "https://big.example.it", "https://edge.example.de",
                    "https://once.example.pt", "https://zero.example.de"],
          f"union by host: joinmobilizon's busiest first, then FediDB-only by status_count ({hosts})")
    check(proposed == ["https://busy.example.fr"] and skipped["held"] == 4,
          f"only the host joinmobilizon lists is proposed; the four only FediDB lists are held ({proposed})")
    check(any(l == '  FLAG mobilizon: held for a human, only FediDB lists it: zero.example.de '
                   '(10 upcoming; e.g. "Zero 1")' for l in out.splitlines()),
          "a held host prints one `  FLAG mobilizon: held` line with its total and sampled titles")
    check(skipped["too_quiet"] == 1 and "https://quiet.example" not in hosts and not s.asked("quiet.example"),
          "a host joinmobilizon gates as too quiet stays out though FediDB also lists it, unasked")
    check("https://zero.example.de" in hosts,
          "a FediDB-only host with status_count 0 is not gated on it (63 upcoming at 0, measured)")
    check(skipped["configured"] == 1 and skipped["declined"] == 1 and skipped["test_or_demo"] == 1,
          f"configured, declined, and a test copy of a declined one are skipped ({skipped})")
    check(hosts.count("https://busy.example.fr") == 1 and len(s.asked("busy.example.fr", "/api")) == 1,
          "www.busy.example.fr in FediDB and busy.example.fr in joinmobilizon are one host, asked once")
    check("https://follower.example.nl" not in hosts
          and any(l.startswith("  mirror: follower.example.nl 5 of its 6 sampled events were published on "
                               "mob.configured.example (by their url)") for l in out.splitlines()),
          "an instance serving the configured instance's events back, their url on it, is a mirror "
          "(f.termine.di.day)")
    check("https://importer.example.nl" not in hosts
          and any(l.startswith("  mirror: importer.example.nl 83%") for l in out.splitlines()),
          "...and one re-publishing them under its own urls is a mirror by title and start")
    check("https://big.example.it" in hosts,
          "one configured event among a candidate's own is not a mirror")
    check("https://past.example" not in hosts and skipped["quiet"] == 1,
          "events that all began before today are not upcoming (events.doesliverpool.com)")
    check(skipped["refused"] == 1 and len(s.asked("refuser.example", "/api")) == 1,
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
    check(skipped["mirror"] == 3, f"three mirrors: by url, by title, and the converse ({skipped['mirror']})")
    check("https://huge.example.de" not in hosts and skipped["too_big"] == 1
          and any(l.startswith("  too big: huge.example.de answers 1,001 upcoming; held")
                  for l in out.splitlines()),
          "a candidate answering more than MOBILIZON_MAX_TOTAL upcoming is held for a human "
          "(politikvonunten.org, 5,943)")
    check("https://edge.example.de" in hosts and "huge.example.de" not in mled,
          "...exactly MOBILIZON_MAX_TOTAL is proposed, and a held one is not parked in the ledger")
    e = found.get(C._canon("https://busy.example.fr"))
    check(e == {"type": "mobilizon", "name": "Busy", "base_url": "https://busy.example.fr",
                "category": "community", "limit": 100, "max_pages": 5, "default_country": "France"},
          f"a proposed entry has the Mobilizon shape, country a NAME from the TLD ({e})")
    check(not [l for l in out.splitlines() if "FLAG" in l and not HELD_RX.match(l)]
          and "requests: searchEvents 11 candidate(s) + 1 configured, robots.txt 11" in out,
          "both directories answering prints no FLAG but the held lines, and the request count")
    check(len(s.asked("busy.example.fr", "/robots.txt")) == 1 and not s.asked("mob.configured.example", "/robots.txt"),
          "each candidate's robots.txt is read once; a configured instance is not re-judged")

    s = Fake(mob_route())
    (_f, sk2), _o = run(C._discover_mobilizon, s, C._config_keys(), mled, 50)
    check(not s.asked("refuser.example") and sk2["ledger"] == 1 and sk2["refused"] == 0,
          "the next run skips the parked refusal without asking it")

    s = Fake(mob_route(jm_ok=False))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50)
    check(found == {} and len(held_of(out)) == 6 and "https://quiet.example" in held_of(out)
          and "FediDB alone" in out and skipped["held"] == 6,
          f"joinmobilizon down: every candidate came from FediDB alone, and all 6 are held ({len(found)})")
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
    check([e["base_url"] for e in lim.values()] == ["https://busy.example.fr"] and len(held_of(_o)) == 4,
          "the limit cuts what is proposed, not what is held for a human")

    # The wall clock. A deadline already gone: FediDB's first page is read
    # (paging stops after it), no instance is asked, nothing is proposed.
    s = Fake(mob_route(fd_pages=3))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50, deadline=1.0)
    flag = [l for l in out.splitlines() if l.startswith("  FLAG mobilizon:")]
    check(found == {} and not [u for u, _p in s.calls if urlparse(u).path in ("/api", "/robots.txt")]
          and len(s.asked("api.fedidb.org")) == 1 and len(flag) == 1 and SUMMARY_RX.match(flag[0])
          and any("FLAG fedidb mobilizon: the deadline passed before page 2" in l for l in out.splitlines()),
          f"past the deadline: one FediDB page, no probe, nothing proposed, a FLAG the summary keeps ({flag})")
    # One that passes while the configured instance is read: it went first, so
    # the yardstick is whole and the candidates are what go unread.
    s = Fake(mob_route(slow={"mob.configured.example": 1.0}))
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), {}, 50, workers=1,
                                deadline=time.time() + 0.5)
    check(found == {} and len(s.asked("mob.configured.example", "/api")) == 1
          and not s.asked("busy.example.fr") and skipped["unread"] == 11
          and any("FLAG mobilizon: 11 host(s) UNREAD past the deadline (0 of them configured)" in l
                  for l in out.splitlines()),
          f"a deadline mid-run: configured read first, every candidate unread, nothing proposed ({skipped['unread']})")

    # ------------------------------------------ 2b. robots.txt, adult titles, a follower of anyone
    # The operator's robots.txt is read before the first request to a
    # candidate (review of 2026-11-08: gancio.antroposofiachile.net answers
    # `User-agent: *` / `Disallow: /` and was proposed). All listed by
    # joinmobilizon, so what clears every rule is proposed, not held.
    DENY = "User-agent: *\nDisallow: /\n"
    TOKEN_DENY = "User-agent: MapseeAggregator\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
    CHALLENGE = "<html><head><title>Just a moment...</title></head><body>checking your browser</body></html>"
    ROB_M = {"nocrawl.example.cl": Resp(200, None, "", DENY),
             "tokenno.example.cl": Resp(200, None, "", TOKEN_DENY),
             "pathonly.example.cl": Resp(200, None, "", "User-agent: *\nDisallow: /admin\n"),
             "down.example.cl": ConnectionError("timed out"),
             "waf.example.cl": Resp(403, None, "", "Forbidden"),
             "challenged.example.cl": Resp(200, None, "", CHALLENGE)}
    M2 = ("nocrawl.example.cl", "tokenno.example.cl", "pathonly.example.cl", "down.example.cl", "waf.example.cl",
          "challenged.example.cl", "adultmob.example.de", "someadult.example.de", "copier.example.br",
          "halfmirror.example.pt", "halfcopy.example.pt", "oneoff.example.pt", "onemine.example.pt",
          "split.example.pt")
    JM2 = {"data": [{"host": h, "name": h.split(".")[0].title(), "totalLocalEvents": 50, "health": 90} for h in M2]}

    def own(h, titles, d0=1):
        return [mev(tt, d0 + i, on=h) for i, tt in enumerate(titles)]
    ELSEWHERE, OTHER = "eventos.elsewhere.example", "agenda.other.example"
    SEARCH2 = dict({h: own(h, ["never asked"]) for h in M2}, **{
        "mob.configured.example": CONF_M,
        "pathonly.example.cl": own("pathonly.example.cl", ["Path 1", "Path 2"]),
        # 3 of 4 sampled titles adult: refused, as Gancio's event share is
        "adultmob.example.de": own("adultmob.example.de", ["Fetish night", "BDSM workshop", "Kink social", "Quiz"]),
        # 3 of 10: under ADULT_EVENT_SHARE (1/3), kept
        "someadult.example.de": own("someadult.example.de", ["Fetish fair", "BDSM talk", "Kink picnic"]
                                    + [f"Talk {i}" for i in range(7)], d0=20),
        # mobilizon.com.br's shape: every event published on one host no config names
        "copier.example.br": [mev(f"Copied {i}", i, on=ELSEWHERE) for i in range(1, 6)],
        # A follower has (almost) nothing of its own (the third review: the
        # share alone refused 20 of the 66 configured instances, among them
        # mobilize.adminforge.de with 43 of its own). 2 of 4 elsewhere (0.5,
        # GANCIO_MIRROR_SHARE) with 2 of its own (MOBILIZON_SERVED_MIN): kept;
        # with 1 of its own, at the same 0.5 share: a mirror.
        "halfmirror.example.pt": [mev("HM a", 1, on=ELSEWHERE), mev("HM b", 2, on=ELSEWHERE)]
        + own("halfmirror.example.pt", ["HM own 1", "HM own 2"], d0=3),
        "onemine.example.pt": [mev("OM a", 1, on=ELSEWHERE), mev("OM b", 2, on=ELSEWHERE),
                               mev("OM c", 3, on=OTHER)] + own("onemine.example.pt", ["OM own"], d0=4),
        # 2 of 5 (0.4) from the top host, 1 of its own: under the share, kept
        "split.example.pt": [mev("SP a", 1, on=ELSEWHERE), mev("SP b", 2, on=ELSEWHERE),
                             mev("SP c", 3, on=OTHER), mev("SP d", 4, on=OTHER)]
        + own("split.example.pt", ["SP own"], d0=5),
        "halfcopy.example.pt": [mev("HC a", 1, on=ELSEWHERE), mev("HC b", 2, on=ELSEWHERE)]
        + own("halfcopy.example.pt", ["HC 1", "HC 2", "HC 3"], d0=3),
        # one event of one published elsewhere is under MOBILIZON_SERVED_MIN
        "oneoff.example.pt": [mev("Shared 1", 1, on=ELSEWHERE)],
    })
    s = Fake(mob_route(jm=JM2, fd=[], search=SEARCH2, robots=ROB_M))
    rled = {}
    (found, skipped), out = run(C._discover_mobilizon, s, C._config_keys(), rled, 50)
    proposed = [e["base_url"] for e in found.values()]
    lines = out.splitlines()
    check(proposed == ["https://pathonly.example.cl", "https://someadult.example.de", "https://halfmirror.example.pt",
                       "https://halfcopy.example.pt", "https://oneoff.example.pt", "https://split.example.pt"],
          f"robots.txt run: what is proposed ({proposed})")
    for h in ("nocrawl.example.cl", "tokenno.example.cl", "down.example.cl", "waf.example.cl", "challenged.example.cl"):
        check(len(s.asked(h, "/robots.txt")) == 1 and not s.asked(h, "/api"),
              f"mobilizon {h}: robots.txt read once, and nothing else asked")
    check(s.asked("pathonly.example.cl", "/api") and "https://pathonly.example.cl" in proposed,
          "mobilizon: `Disallow: /admin` is a path rule, which the documented API is not bound by: probed")
    for h, status, why in (("nocrawl.example.cl", "refused", "Disallow: /"), ("tokenno.example.cl", "refused", "Disallow: /"),
                           ("challenged.example.cl", "fail", "challenged"),
                           ("waf.example.cl", "fail", "http 403")):
        row = rled.get(h) or {}
        check(row.get("status") == status and row.get("type") == "mobilizon" and why in row.get("reason", "")
              and row.get("checked") == 20261108 and C._dead_recently(rled, h),
              f"mobilizon {h}: ledger `{status}` ({why}), parked for DEAD_TTL ({row})")
    check("down.example.cl" not in rled and not C._dead_recently(rled, "down.example.cl"),
          f"mobilizon: an UNREACHABLE robots.txt writes no ledger row; the host is asked again next run "
          f"({rled.get('down.example.cl')})")
    check(skipped["robots"] == 2 and skipped["robots_unread"] == 2 and skipped["refused"] == 1,
          f"robots 2 (`*` and our token), robots_unread 2, a 403 on robots.txt a refusal ({skipped})")
    check("https://adultmob.example.de" not in proposed
          and any(l == "  adult: adultmob.example.de (3 of 4 sampled events by their own words)" for l in lines),
          "mobilizon: 3 of 4 sampled titles adult refuses the calendar (ADULT_EVENT_SHARE)")
    check("https://someadult.example.de" in proposed and skipped["adult"] == 1,
          "...and 3 of 10 does not")
    check("https://copier.example.br" not in proposed
          and any(l == f"  mirror: copier.example.br 5 of its 5 sampled events were published on {ELSEWHERE} "
                       f"(by their url)" for l in lines),
          "mobilizon: a follower of a host no config names is a mirror (mobilizon.com.br, 52 of 52)")
    check("https://onemine.example.pt" not in proposed
          and any(l == f"  mirror: onemine.example.pt 2 of its 4 sampled events were published on {ELSEWHERE} "
                       f"(by their url)" for l in lines) and skipped["mirror"] == 2,
          "...2 of 4 from one host with 1 of its own is one (0.5, under MOBILIZON_SERVED_MIN own)")
    check("https://halfmirror.example.pt" in proposed,
          "...the same 2 of 4 with 2 of its own is a calendar that follows a hub, kept "
          "(mobilize.adminforge.de: 43 own, 55 from mobilize.berlin)")
    check("https://split.example.pt" in proposed and "https://halfcopy.example.pt" in proposed
          and "https://oneoff.example.pt" in proposed,
          "...2 of 5 from the top host is under the share, and 1 of 1 under MOBILIZON_SERVED_MIN")
    s2 = Fake(mob_route(jm=JM2, fd=[], search=SEARCH2, robots=ROB_M))
    (_f, sk2), _o = run(C._discover_mobilizon, s2, C._config_keys(), rled, 50)
    check(not any(s2.asked(h) for h in ("nocrawl.example.cl", "tokenno.example.cl",
                                         "waf.example.cl", "challenged.example.cl"))
          and sk2["ledger"] == 4 and sk2["robots"] == 0,
          "the next run asks none of the four robots.txt verdicts again")
    check(len(s2.asked("down.example.cl", "/robots.txt")) == 1 and not s2.asked("down.example.cl", "/api")
          and sk2["robots_unread"] == 1,
          f"...and asks the unreachable one's robots.txt again, and nothing else of it ({sk2['robots_unread']})")

    # ------------------------------------------ 2c. a redirect is a request to another host
    # The third review of 2026-11-08: a candidate serving its own robots.txt
    # but sending /api elsewhere put a POST on a host refusing every crawler,
    # whose robots.txt was never read. Every hop is now asked first. The
    # fake makes a followed redirect a real request, as requests would.
    def mob_hop(listing, search, robots, led_, workers=1):
        jm = {"data": [{"host": h, "name": h.split(".")[0], "totalLocalEvents": 60 - i, "health": 90}
                       for i, h in enumerate(listing)]}
        sess = Fake(mob_route(jm=jm, fd=[], search=search, robots=robots))
        return sess, run(C._discover_mobilizon, sess, C._config_keys(), led_, 50, workers=workers)
    MH, MF = "mopen.example.com", "mclosed.example.org"
    HOPS_M = dict(SEARCH2, **{MH: ("->", 308, MF), MF: own(MF, ["Closed 1", "Closed 2"]),
                              "mtorefused.example.com": ("->", 307, "mrefused.example.org"),
                              "mrefused.example.org": own("mrefused.example.org", ["never asked"]),
                              "mtodeclined.example.com": ("->", 308, "keskonfai.example"),
                              "keskonfai.example": own("keskonfai.example", ["never asked"]),
                              "mold.example.com": ("->", 308, "mnew.example.com"),
                              "mtoconf.example.com": ("->", 308, "mob.configured.example"),
                              "mnew.example.com": [mev("New 1", 1), mev("New 2", 2)]})
    ROB_H = {MF: Resp(200, None, "", DENY)}
    for listing in ([MH], [MF, MH], [MH, MF]):
        for w in (1, 3):
            hled = {}
            s, ((found, skipped), out) = mob_hop(listing, HOPS_M, ROB_H, hled, w)
            s2, ((f2, sk2), _o) = mob_hop(listing, HOPS_M, ROB_H, hled, w)
            check(found == {} and not s.asked(MF, "/api") and len(s.asked(MF, "/robots.txt")) == 1
                  and (hled.get(MH) or {}).get("status") == "refused"
                  and f"on {MF}, where it redirects" in (hled.get(MH) or {}).get("reason", "")
                  and (hled.get(MF) or {}).get("status") == "refused"
                  and any(l.startswith(f"  robots.txt: {MH} -> {MF}: robots.txt: Disallow: /") for l in out.splitlines())
                  and not s2.asked(MH) and not s2.asked(MF),
                  f"mobilizon {listing} (workers {w}): a 308 onto a host refusing every crawler: that host "
                  f"gets robots.txt only, both names parked `refused`, the next run asks neither "
                  f"({[u for u, _p in s.calls if urlparse(u).hostname == MF]})")
    hled = {"mrefused.example.org": {"type": "mobilizon", "status": "refused", "checked": 20261101,
                                     "reason": "robots.txt: Disallow: /"}}
    s, ((found, skipped), out) = mob_hop(["mtorefused.example.com", "mtodeclined.example.com", "mold.example.com"],
                                         HOPS_M, ROB_H, hled)
    check(not s.asked("mrefused.example.org") and (hled.get("mtorefused.example.com") or {}).get("status") == "refused"
          and any(l == "  alias: mtorefused.example.com -> mrefused.example.org, which the ledger has parked"
                  for l in out.splitlines()),
          "mobilizon: a redirect onto a host the ledger parked for robots.txt is not followed (not even its "
          "robots.txt), and the name redirecting there is parked beside it")
    check(not s.asked("keskonfai.example") and skipped["declined"] == 1
          and any(l == "  alias: mtodeclined.example.com -> keskonfai.example, which is declined on the record"
                  for l in out.splitlines()),
          "mobilizon: a redirect onto a declined host is not followed")
    order = [urlparse(u).path for u, _p in s.calls if urlparse(u).hostname == "mnew.example.com"]
    check(list(found) == [C._canon("https://mold.example.com")] and order == ["/robots.txt", "/api"]
          and skipped["ledger"] == 1,
          f"mobilizon: a 308 onto a host that allows us: its robots.txt first, then the POST, kept ({order})")
    hled = {}
    s, ((found, skipped), out) = mob_hop(["mtoconf.example.com"], HOPS_M,
                                         dict(ROB_H, **{"mob.configured.example": Resp(200, None, "", DENY)}), hled)
    check(found == {} and skipped["mirror"] == 1 and not s.asked("mob.configured.example", "/robots.txt")
          and hled == {},
          f"mobilizon: a redirect onto the CONFIGURED instance is not re-judged by its robots.txt nor parked; "
          f"its events make the candidate a mirror ({hled}, {skipped['mirror']})")

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
        "alias-old.example": ("alias-new.example", [], {}),     # a 301: the host it names answers
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
        "toparked.example": ("parked.example", [], {}),
        "todeclined.example": ("declined.example", [], {}),
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
    # Only the robots.txt run (below) lists these, so the main run's counts stand.
    EXTRA_G = {
        "nocrawl.example.cl": (None, [ev("never asked", 1)], {}),
        "tokenno.example.cl": (None, [ev("never asked", 1)], {}),
        "pathonly.example.cl": (None, [ev("Path rule 1", 1), ev("Path rule 2", 2)], {"nodeName": "Path Only"}),
        "down.example.cl": (None, [ev("never asked", 1)], {}),
        "waf.example.cl": (None, [ev("never asked", 1)], {}),
        "waf429.example.cl": (None, [ev("never asked", 1)], {}),
        "challenged.example.cl": (None, [ev("never asked", 1)], {}),
        "toclosed.example.cl": ("closed.example.org", [], {"nodeName": "C"}),
    }
    # Where a redirect lands that no directory lists: it answers if asked, so
    # a request the robots.txt gate should have stopped shows up as a proposal.
    LANDING = {"closed.example.org": (None, [ev("Closed 1", 1), ev("Closed 2", 2)], {"nodeName": "C"})}

    def gancio_route(fd_ok=True, org_ok=True, slow=None, listed=None, robots=None, org_html=None):
        """`slow`: {host: seconds} its /api/events takes to answer. `listed`
        replaces LISTED as FediDB's list; `robots`: {host: its robots.txt
        answer}, by default a 404 (no file, allow all); `org_html` replaces
        gancio.org/instances."""
        fd = fedidb_rows(*(LISTED if listed is None else listed))

        def r(u, p):
            pu = urlparse(u)
            if u == C.FEDIDB_SERVERS:
                return fedidb_route(fd)(p) if fd_ok else ConnectionError("timed out")
            if pu.path == "/robots.txt":
                return (robots or {}).get(pu.hostname) or Resp(404, None, u, "not found")
            if u == C.GANCIO_DIRECTORY_PAGE:
                return (Resp(200, None, u, org_html or GANCIO_ORG_HTML) if org_ok
                        else Resp(503, None, u, "down"))
            host = pu.hostname
            if pu.path == "/api/events":
                q = parse_qs(pu.query)
                assert q["start"] == [str(T0)] and q["max"] == ["500"], u
                if slow and host in slow:
                    time.sleep(slow[host])
                if host == "confa.example":
                    return Resp(200, CONF_SETS["confa.example"], u)
                if host == "confr.example":   # rheinmain.social's shape: a 301 to events.<host>
                    return moved(u, u.replace("confr.example", "events.confr.example", 1))
                final, evs, _ni = INSTANCES.get(host) or EXTRA_G.get(host) or LANDING[host]
                if final:                     # a 301 the probe follows by hand
                    return moved(u, u.replace(host, final, 1))
                if isinstance(evs, int):
                    return Resp(evs, None, u, "<html>no</html>")
                return Resp(200, evs, u)
            if pu.path == "/.well-known/nodeinfo/2.0":
                ni = (INSTANCES.get(host) or EXTRA_G.get(host) or (None, None, {}))[2]
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
    check(len(s.asked("refuses.example", "/api/events")) == 1,
          "a 403 is asked exactly once: a refusal is not retried")
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

    # robots.txt, before the first request to a candidate; the host a
    # candidate redirects to answers for itself before nodeinfo or a proposal.
    ROB_G = {"nocrawl.example.cl": Resp(200, None, "", DENY),
             "tokenno.example.cl": Resp(200, None, "", TOKEN_DENY),
             "pathonly.example.cl": Resp(200, None, "", "User-agent: *\nDisallow: /admin\nDisallow: /api/\n"),
             "down.example.cl": ConnectionError("timed out"),
             "waf.example.cl": Resp(403, None, "", "Forbidden"),
             "waf429.example.cl": Resp(429, None, "", "Too Many Requests"),
             "challenged.example.cl": Resp(200, None, "", CHALLENGE),
             "closed.example.org": Resp(200, None, "", DENY)}
    s = Fake(gancio_route(listed=list(EXTRA_G), org_ok=False, robots=ROB_G))
    rled = {}
    (fr, skr), out = run(C._discover_gancio, s, C._config_keys(), rled, 100, workers=3)
    check([e["base_url"] for e in fr.values()] == ["https://pathonly.example.cl"],
          f"gancio robots.txt run: only the path-rule host is proposed ({[e['base_url'] for e in fr.values()]})")
    check(s.asked("pathonly.example.cl", "/api/events") and s.asked("pathonly.example.cl", "/.well-known"),
          "gancio: `Disallow: /admin` and `/api/` are path rules the documented API is not bound by: probed")
    for h in ("nocrawl.example.cl", "tokenno.example.cl", "down.example.cl", "waf.example.cl", "waf429.example.cl",
              "challenged.example.cl"):
        check(len(s.asked(h, "/robots.txt")) == 1 and not s.asked(h, "/api") and not s.asked(h, "/.well-known"),
              f"gancio {h}: robots.txt read once, and nothing else asked")
    for h, status, why in (("nocrawl.example.cl", "refused", "Disallow: /"), ("tokenno.example.cl", "refused", "Disallow: /"),
                           ("challenged.example.cl", "fail", "challenged"),
                           ("waf.example.cl", "fail", "http 403"), ("waf429.example.cl", "fail", "http 429"),
                           ("closed.example.org", "refused", "Disallow: /"),
                           ("toclosed.example.cl", "refused", "on closed.example.org, where it redirects")):
        row = rled.get(h) or {}
        check(row.get("status") == status and row.get("type") == "gancio" and why in row.get("reason", "")
              and C._dead_recently(rled, h),
              f"gancio {h}: ledger `{status}` ({why}), parked for DEAD_TTL ({row})")
    check(len(s.asked("closed.example.org", "/robots.txt")) == 1 and not s.asked("closed.example.org", "/api")
          and not s.asked("closed.example.org", "/.well-known")
          and not s.asked("toclosed.example.cl", "/.well-known")
          and any(l.startswith("  robots.txt: toclosed.example.cl -> closed.example.org: robots.txt: Disallow: /")
                  for l in out.splitlines()),
          "gancio: a candidate that redirects to a host refusing every crawler: that host's robots.txt read, "
          "no nodeinfo, both parked")
    check(skr["robots"] == 3 and skr["robots_unread"] == 2 and skr["refused"] == 2
          and sum(skr.values()) + len(fr) == len(EXTRA_G),
          f"robots 3, robots_unread 2, refused 2 (403, 429), each host counted once ({skr})")
    check("down.example.cl" not in rled,
          f"gancio: an UNREACHABLE robots.txt writes no ledger row ({rled.get('down.example.cl')})")
    s2 = Fake(gancio_route(listed=list(EXTRA_G), org_ok=False, robots=ROB_G))
    (_f, sk2), _o = run(C._discover_gancio, s2, C._config_keys(), rled, 100, workers=3)
    check(not any(s2.asked(h) for h in EXTRA_G if h not in ("pathonly.example.cl", "down.example.cl"))
          and not s2.asked("closed.example.org") and sk2["ledger"] == 6,
          f"the next run asks none of the parked ones again, nor where one redirects ({sk2['ledger']})")
    check(len(s2.asked("down.example.cl", "/robots.txt")) == 1 and not s2.asked("down.example.cl", "/api")
          and sk2["robots_unread"] == 1,
          f"...and asks the unreachable one's robots.txt again, and nothing else of it ({sk2['robots_unread']})")

    # A redirect is a request to another host (the third review of 2026-11-08).
    # zz-open serves its own robots.txt and sends /api/events to aa-closed,
    # which refuses every crawler and, listed too, sorts FIRST: it was parked
    # and the alias skipped unparked, so each later run knocked through the
    # redirect. Both alphabetical orders, alone and together, 1 and 3 workers.
    LANDING.update({
        "aa-closed.example.org": (None, [ev("Closed A 1", 1), ev("Closed A 2", 2)], {}),
        "zz-open.example.com": ("aa-closed.example.org", [], {}),
        "aa-open.example.com": ("zz-closed.example.org", [], {}),
        "zz-closed.example.org": (None, [ev("Closed Z 1", 1), ev("Closed Z 2", 2)], {}),
        "torparked.example.com": ("rparked.example.org", [], {}),
        "rparked.example.org": (None, [ev("never asked", 1)], {}),
        "toconf.example.com": ("confa.example", [], {})})
    ROB_GH = {"aa-closed.example.org": Resp(200, None, "", DENY), "zz-closed.example.org": Resp(200, None, "", DENY)}
    # gancio.org/instances unread here (FediDB lists them): Polite's 1 s wait
    # per run is not what these cases are about.
    _org = C._gancio_org_hosts
    C._gancio_org_hosts = lambda _s, deadline=0.0: (None, "not read in this case")
    for opn, closed in (("zz-open.example.com", "aa-closed.example.org"),
                        ("aa-open.example.com", "zz-closed.example.org")):
        for listing in ([opn], [opn, closed]):
            for w in (1, 3):
                hled = {}
                s = Fake(gancio_route(listed=listing, org_ok=False, robots=ROB_GH))
                (fh, skh), out = run(C._discover_gancio, s, C._config_keys(), hled, 100, workers=w)
                s2 = Fake(gancio_route(listed=listing, org_ok=False, robots=ROB_GH))
                (_f, sk2), _o = run(C._discover_gancio, s2, C._config_keys(), hled, 100, workers=w)
                to_closed = [urlparse(u).path for u, _p in s.calls if urlparse(u).hostname == closed]
                check(fh == {} and to_closed == ["/robots.txt"]
                      and (hled.get(opn) or {}).get("status") == "refused"
                      and (hled.get(closed) or {}).get("status") == "refused"
                      and not s2.asked(opn) and not s2.asked(closed) and sk2["ledger"] == len(listing),
                      f"gancio {listing} (workers {w}): the refusing host a redirect leads to is asked its "
                      f"robots.txt and nothing else, both names are parked `refused`, the next run asks "
                      f"neither ({to_closed}, {sorted(hled)})")
    hled = {"rparked.example.org": {"type": "gancio", "status": "refused", "checked": 20261101,
                                    "reason": "robots.txt: Disallow: /"}}
    s = Fake(gancio_route(listed=["torparked.example.com"], org_ok=False))
    (fh, skh), out = run(C._discover_gancio, s, C._config_keys(), hled, 100, workers=1)
    row = hled.get("torparked.example.com") or {}
    check(fh == {} and not s.asked("rparked.example.org") and row.get("status") == "refused"
          and "rparked.example.org refuses every crawler" in row.get("reason", "") and skh["ledger"] == 1,
          f"gancio: a redirect onto a host the ledger parked for robots.txt is not followed, and the name "
          f"redirecting there is parked beside it ({row})")
    check("toparked.example" not in gled and "toparked.example" not in led,
          "...while one onto a host parked for an http 500 is skipped, not parked itself")
    hled = {}
    s = Fake(gancio_route(listed=["toconf.example.com"], org_ok=False,
                          robots={"confa.example": Resp(200, None, "", DENY)}))
    (fh, skh), out = run(C._discover_gancio, s, C._config_keys(), hled, 100, workers=1)
    check(fh == {} and skh["alias"] == 1 and hled == {} and not s.asked("confa.example", "/robots.txt")
          and any(l == "  alias: toconf.example.com -> confa.example, where a configured site lands"
                  for l in out.splitlines()),
          f"gancio: a redirect onto a CONFIGURED site is not re-judged by its robots.txt nor parked: an alias ({hled})")
    C._gancio_org_hosts = _org

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
    # 1.5 s: gancio.org/instances, which alone lists orgonly.example, is read
    # through Polite, 1 s after its robots.txt.
    s = Fake(gancio_route(slow={"orgonly.example": 2.0}))
    (fd_, sk_), out = run(C._discover_gancio, s, C._config_keys(), led, 100, workers=1,
                          deadline=time.time() + 1.5)
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

        def robots_discover(*a, **k):
            a[2]["nocrawl.example.cl"] = {"checked": 20261108, "name": "nocrawl.example.cl", "type": "mobilizon",
                                          "status": "refused", "reason": "discover mobilizon: robots.txt: Disallow: /"}
            return {}, {"robots": 1, "refused": 0}
        C._discover_mobilizon = robots_discover
        rc, out = run(C.main, ["catalog_curate.py", "discover", "mobilizon", "--out", cand])
        check(rc == 0 and (C._load_ledger().get("nocrawl.example.cl") or {}).get("status") == "refused",
              "a robots.txt verdict the backend parked is saved to the ledger file, with no 403 beside it")
    finally:
        C._discover_gancio, C.curated_categories, C._session = real, real_cats, real_sess
        C._discover_mobilizon = real_mob

    # ------------------------------------------ 5. robots: annual_sources.json
    check("annual_sources.json" in C._ROBOTS_EXTRA, "annual_sources.json is in _ROBOTS_EXTRA")
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

# ---------------------------------------------- 6b. verify's gate, for a hand-written candidate
def robots_of(files):
    """A robots_txt.Robots over a fake session: {host: robots.txt text,
    an int status, or an Exception}; anything else a 404."""
    def route(u, _p):
        ans = files.get(urlparse(u).hostname)
        if isinstance(ans, Exception):
            return ans
        if isinstance(ans, int):
            return Resp(ans, None, u, "no")
        return Resp(200, None, u, ans) if ans is not None else Resp(404, None, u, "not found")
    return C.robots_txt.Robots(Fake(route))


R = robots_of({"deny.example": "User-agent: *\nDisallow: /\n",
               "token.example": "User-agent: MapseeAggregator\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
               "apionly.example": "User-agent: *\nDisallow: /api\n",
               "down.example": ConnectionError("timed out"),
               "waf.example": 403,
               "cal.example": "User-agent: *\nDisallow: /cal\n"})
for t in ("gancio", "mobilizon"):
    g = C._robots_gate(R, t, {"type": t, "base_url": "https://deny.example"})
    check(g and g[0] == "refused" and "Disallow: /" in g[1],
          f"verify refuses a hand-written {t} candidate whose host says `User-agent: *` / `Disallow: /` ({g})")
    g = C._robots_gate(R, t, {"type": t, "base_url": "https://token.example/"})
    check(g and g[0] == "refused", f"...and one that refuses MapseeAggregator by name ({t}: {g})")
    check(C._robots_gate(R, t, {"type": t, "base_url": "https://apionly.example"}) is None,
          f"...but not one whose `Disallow: /api` is a path rule: the documented API stays exempt ({t})")
    g = C._robots_gate(R, t, {"type": t, "base_url": "https://down.example"})
    check(g and g[0] == "fail" and "unreachable" in g[1], f"...an unreachable robots.txt is `fail` ({t}: {g})")
    g = C._robots_gate(R, t, {"type": t, "base_url": "https://waf.example"})
    check(g and g[0] == "fail" and "403" in g[1], f"...a 403 on robots.txt is a refusal, `fail` ({t}: {g})")
# `catalog_curate.py robots` (report only) agrees with _robots_gate: a
# configured gancio or mobilizon instance whose operator refuses every crawler
# is listed as DISALLOWED, not under the documented APIs "not bound by it";
# a path rule on its API still is.
_saved = (C._configured_requests, C._session, C.robots_txt.Robots.prefetch)
# prefetch opens its own requests.Session per worker: kept off the network
# here, so check() reads each file through the Fake below.
C.robots_txt.Robots.prefetch = lambda self, urls, workers=16, make_session=None: None
C._configured_requests = lambda: [
    ("gancio_sources.json", "gancio", "Closed", "GET", "https://deny.example/api/events?start=1"),
    ("mobilizon_sources.json", "mobilizon", "Token", "POST", "https://token.example/api"),
    ("gancio_sources.json", "gancio", "Path", "GET", "https://apionly.example/api/events?start=1")]
try:
    _route = {"deny.example": "User-agent: *\nDisallow: /\n",
              "token.example": "User-agent: MapseeAggregator\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
              "apionly.example": "User-agent: *\nDisallow: /api\n"}

    def _rob(u, _params):
        h = urlparse(u).hostname
        return Resp(200, None, u, _route[h]) if h in _route else Resp(404, None, u, "not found")
    C._session = lambda: Fake(_rob)
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        C.cmd_robots(workers=1)
    _out = _buf.getvalue()
    _dis = _out.split("DISALLOWED", 1)[1].split("DOCUMENTED APIs", 1)[0]
    _api = _out.split("DOCUMENTED APIs", 1)[1].split("UNPACED", 1)[0]
    check("deny.example" in _dis and "token.example" in _dis and "deny.example" not in _api
          and "token.example" not in _api,
          "robots report: a fedi instance refusing every crawler is DISALLOWED, as _robots_gate binds it")
    check("apionly.example" in _api and "apionly.example" not in _dis,
          "...while a path rule on its API is listed with the documented APIs, not bound")
finally:
    C._configured_requests, C._session, C.robots_txt.Robots.prefetch = _saved
check(C._robots_gate(R, "localist", {"base_url": "https://deny.example"}) is None
      and C._robots_gate(R, "ckan", {"url": "https://deny.example/api/3/action/datastore_search?x=1"}) is None,
      "the other documented APIs are still not asked")
g = C._robots_gate(R, "ics", {"url": "https://cal.example/cal/feed.ics"})
check(g and g[0] == "refused" and "Disallow: /cal" in g[1], f"a publisher's feed is still refused by a path rule ({g})")

# ---------------------------------------------- 6c. one request per operator at a time
for h, op in (("warszawa.askapunk.net", "askapunk.net"), ("WWW.Example.org", "example.org"),
              ("mobilizon.com.br", "mobilizon.com.br"), ("a.b.mobilizon.com.br", "mobilizon.com.br"),
              ("swindonguide.org.uk", "swindonguide.org.uk"), ("mlg.convoca.la", "convoca.la"),
              ("events.campus.ltu.se", "ltu.se"), ("gorzow.org.pl", "gorzow.org.pl")):
    check(C._operator_of(h) == op, f"the operator of {h} is {op} ({C._operator_of(h)})")


class Tracker(Fake):
    """A Fake whose every request takes `pause` s, counting what is in flight
    per operator (C._operator_of) and at all."""

    def __init__(self, route, pause=0.02):
        super().__init__(route)
        import threading
        self.pause, self.lock = pause, threading.Lock()
        self.now, self.peak, self.all, self.peak_all = {}, {}, 0, 0

    def _track(self, url, fn):
        op = C._operator_of(urlparse(url).hostname)
        with self.lock:
            self.now[op] = self.now.get(op, 0) + 1
            self.peak[op] = max(self.peak.get(op, 0), self.now[op])
            self.all += 1
            self.peak_all = max(self.peak_all, self.all)
        try:
            time.sleep(self.pause)
            return fn()
        finally:
            with self.lock:
                self.now[op] -= 1
                self.all -= 1

    def get(self, url, **kw):
        return self._track(url, lambda: Fake.get(self, url, **kw))

    def post(self, url, **kw):
        return self._track(url, lambda: Fake.post(self, url, **kw))


# Four subdomains of one operator, three of another, two under a ccTLD's
# second level, and two loners, as askapunk.net's and convoca.la's are.
OPS = ["w.op1.example", "a.op1.example", "m.op1.example", "b.op1.example", "x.op2.example", "y.op2.example",
       "z.op2.example", "p.op3.co.uk", "q.op3.co.uk", "solo.example.de", "lone.example.it"]


def conc_route(u, p):
    pu = urlparse(u)
    if u == C.MOBILIZON_DIRECTORY:
        return Resp(200, {"data": [{"host": h, "name": h, "totalLocalEvents": 50, "health": 90} for h in OPS]}, u)
    if u == C.FEDIDB_SERVERS:
        return Resp(200, {"data": [{"domain": h} for h in OPS], "meta": {}}, u)
    if pu.path == "/robots.txt":
        return Resp(404, None, u, "not found")
    if u == C.GANCIO_DIRECTORY_PAGE:
        return Resp(503, None, u, "down")
    if pu.path == "/api":
        return Resp(200, {"data": {"searchEvents": {"total": 2, "elements": [
            {"title": f"{pu.hostname} {i}", "beginsOn": f"2026-11-1{i}T19:00:00Z", "url": f"https://{pu.hostname}/e/{i}",
             "physicalAddress": {"geom": "1;2"}} for i in (1, 2)]}}}, u)
    if pu.path == "/api/events":
        return Resp(200, [ev(f"{pu.hostname} {i}", i) for i in (1, 2)], u)
    if pu.path == "/.well-known/nodeinfo/2.0":
        return Resp(200, {"metadata": {"nodeName": pu.hostname}}, u)
    return ConnectionError("unexpected " + u)


_keep2 = (C.HERE, C.LEDGER_FILE)
tmp2 = tempfile.mkdtemp(prefix="fedi-conc-")
C.HERE, C.LEDGER_FILE = tmp2, os.path.join(tmp2, "curation_ledger.json")
try:
    json.dump({"sites": [{"name": "Conf", "base_url": "https://conf.op1.example"}]},
              open(os.path.join(tmp2, "mobilizon_sources.json"), "w"))
    json.dump({"sites": [{"name": "Conf", "base_url": "https://conf.op2.example"}]},
              open(os.path.join(tmp2, "gancio_sources.json"), "w"))
    s = Tracker(conc_route)
    (fm, _sk), _o = run(C._discover_mobilizon, s, C._config_keys(), {}, 50, workers=6)
    probes = [u for u, _p in s.calls if urlparse(u).path in ("/api", "/robots.txt")]
    check(len(fm) == len(OPS) and len(probes) == 2 * len(OPS) + 1
          and max(s.peak[C._operator_of(h)] for h in OPS) == 1 and s.peak_all >= 2,
          f"mobilizon, 6 workers: never two requests to one operator at once, several operators at once "
          f"({ {o: n for o, n in s.peak.items()} }, all {s.peak_all})")
    s = Tracker(conc_route)
    (fg, _sk), _o = run(C._discover_gancio, s, C._config_keys(), {}, 50, workers=6)
    infos = [u for u, _p in s.calls if urlparse(u).path == "/.well-known/nodeinfo/2.0"]
    check(len(fg) == len(OPS) and len(infos) == len(OPS)
          and max(s.peak[C._operator_of(h)] for h in OPS) == 1 and s.peak_all >= 2,
          f"gancio, 6 workers, the probe and nodeinfo phases: never two requests to one operator at once "
          f"({ {o: n for o, n in s.peak.items()} }, all {s.peak_all})")
    # A candidate of one operator that redirects onto another's host: that
    # host's robots.txt is read under ITS operator's lock, while that
    # operator's own candidates are being read in another worker.
    OPS2 = ["r.opa.example"] + [f"c{i}.opb.example" for i in range(5)]

    def redirect_route(u, p):
        pu = urlparse(u)
        if u == C.FEDIDB_SERVERS:
            return Resp(200, {"data": [{"domain": h} for h in OPS2], "meta": {}}, u)
        if pu.hostname == "r.opa.example" and pu.path == "/api/events":
            return moved(u, u.replace("r.opa.example", "landing.opb.example", 1))
        return conc_route(u, p)
    s = Tracker(redirect_route)
    (fg2, _sk), _o = run(C._discover_gancio, s, C._config_keys(), {}, 50, workers=6)
    landed = [urlparse(u).path for u, _p in s.calls if urlparse(u).hostname == "landing.opb.example"]
    check(landed[:2] == ["/robots.txt", "/api/events"] and landed.count("/robots.txt") == 1
          and s.peak.get("opb.example") == 1
          and "https://landing.opb.example" in {e["base_url"] for e in fg2.values()},
          f"gancio: the host a redirect lands on is read under its own operator's lock ({s.peak})")
finally:
    C.HERE, C.LEDGER_FILE = _keep2
    shutil.rmtree(tmp2, ignore_errors=True)

# ---------------------------------------------- 6c'. one hop at a time (_fedi_follow)
def hop_session(chain):
    """chain: {url: Resp}; anything else is a 404. Records (method, url)."""
    calls = []

    class S:
        def get(self, u, **kw):
            assert kw.get("allow_redirects") is False, "every hop is asked with allow_redirects=False"
            calls.append(("GET", u))
            return chain.get(u) or Resp(404, None, u, "")

        def post(self, u, **kw):
            assert kw.get("allow_redirects") is False, "every hop is asked with allow_redirects=False"
            calls.append(("POST", u))
            return chain.get(u) or Resp(404, None, u, "")
    sess = S()

    def send(u, post):
        return sess.post(u, allow_redirects=False) if post else sess.get(u, allow_redirects=False)
    return send, calls


def follow_in_thread(*a, **kw):
    """_fedi_follow run where a deadlock would show as a timeout, not a hang."""
    import threading
    box = {}
    t = threading.Thread(target=lambda: box.update(out=C._fedi_follow(*a, **kw)), daemon=True)
    t.start()
    t.join(5)
    return box.get("out")


gated = []
send, calls = hop_session({"https://a.op.example/api": moved("https://a.op.example/api", "https://www.a.op.example/api"),
                           "https://www.a.op.example/api": moved("https://www.a.op.example/api",
                                                                 "https://b.op.example/api", 308),
                           "https://b.op.example/api": Resp(200, {"ok": 1}, "https://b.op.example/api")})
out = follow_in_thread(send, "https://a.op.example/api", C._OperatorLocks(), post=True,
                       gate=lambda o: gated.append(o), stop=lambda h: gated.append("stop " + h))
check(out and out.get("url") == "https://b.op.example/api" and out["r"].status_code == 200
      and calls == [("POST", "https://a.op.example/api"), ("GET", "https://www.a.op.example/api"),
                    ("GET", "https://b.op.example/api")]
      and gated == ["https://a.op.example", "https://www.a.op.example", "stop b.op.example", "https://b.op.example"],
      f"_fedi_follow: three hosts of one operator, no lock held across hops (no deadlock); robots.txt asked per "
      f"origin (www. too), stop per new host; a 301 turns the POST into a GET, a 308 keeps the method "
      f"({calls}, {gated})")
send, calls = hop_session({"https://x.example/api": moved("https://x.example/api", "https://y.example/api", 307)})
out = C._fedi_follow(send, "https://x.example/api", C._OperatorLocks(), post=True,
                     gate=lambda o: ("robots", "refused", "no") if "y." in o else None)
check(out == {"why": "robots", "gate": ("robots", "refused", "no"), "at": "y.example", "url": "https://y.example/api"}
      and calls == [("POST", "https://x.example/api")],
      f"_fedi_follow: a refusing gate on the second host ends the chain before it is asked ({out}, {calls})")
loop = {f"https://l.example/{i}": moved(f"https://l.example/{i}", f"https://l.example/{i + 1}") for i in range(20)}
send, calls = hop_session(loop)
out = C._fedi_follow(send, "https://l.example/0", C._OperatorLocks())
check(len(calls) == C.FEDI_MAX_HOPS + 1 and out["r"].status_code == 301,
      f"_fedi_follow: at most FEDI_MAX_HOPS redirects followed, the last 3xx returned ({len(calls)})")
send, calls = hop_session({"https://p.example/api": moved("https://p.example/api", "https://p.example/v2", 303),
                           "https://p.example/v2": Resp(200, {}, "https://p.example/v2")})
out = C._fedi_follow(send, "https://p.example/api", C._OperatorLocks(), post=True, gate=lambda o: None,
                     deadline=time.time() + 60)
check(calls == [("POST", "https://p.example/api"), ("GET", "https://p.example/v2")],
      f"_fedi_follow: a 303 continues as a GET ({calls})")
send, calls = hop_session({})
out = C._fedi_follow(send, "https://p.example/api", C._OperatorLocks(), deadline=1.0)
check(out.get("why") == "unread" and not calls, f"_fedi_follow: past the deadline nothing is asked ({out})")


# ---------------------------------------------- 6d. gancio.org/instances through Polite
def org_session(robots, page=None, more=None):
    """robots: gancio.org's robots.txt (text or a status); page: the page's
    Resp; more: {url: Resp} for anywhere a redirect leads."""
    def route(u, _p):
        if u == "https://gancio.org/robots.txt":
            return Resp(robots, None, u, "no") if isinstance(robots, int) else Resp(200, None, u, robots)
        if u == C.GANCIO_DIRECTORY_PAGE:
            return page or Resp(200, None, u, GANCIO_ORG_HTML)
        if more and u in more:
            return more[u]
        return ConnectionError("unexpected " + u)
    return Fake(route)


def fake_clock():
    box, waits = [1000.0], []

    def sleep(w):
        waits.append(w)
        box[0] += w
    return (lambda: box[0]), sleep, waits


for code in (403, 429):
    s = org_session(code)
    clk, slp, waits = fake_clock()
    hosts, why = C._gancio_org_hosts(s, sleep=slp, clock=clk)
    check(hosts is None and str(code) in (why or "") and not s.asked("gancio.org", "/instances"),
          f"gancio.org: a {code} on robots.txt is a refusal, and the page is not asked ({why})")
s = org_session("User-agent: *\nCrawl-delay: 30\nAllow: /\n")
clk, slp, waits = fake_clock()
hosts, why = C._gancio_org_hosts(s, sleep=slp, clock=clk)
check(hosts == ["orgonly.example", "aggr.example"] and waits and abs(sum(waits) - 30) < 0.01
      and [u for u, _p in s.calls] == ["https://gancio.org/robots.txt", C.GANCIO_DIRECTORY_PAGE],
      f"gancio.org: its Crawl-delay of 30 s is waited out between robots.txt and the page ({waits})")
s = org_session(404)
clk, slp, waits = fake_clock()
hosts, why = C._gancio_org_hosts(s, sleep=slp, clock=clk)
check(hosts == ["orgonly.example", "aggr.example"] and abs(sum(waits) - 1.0) < 0.01,
      f"gancio.org: no file is allow-all (RFC 9309), the page read 1 s after it ({waits})")
moved = Resp(301, None, C.GANCIO_DIRECTORY_PAGE, "")
moved.headers = {"Location": "https://docs.gancio.example/instances"}
s = org_session(404, page=moved, more={
    "https://docs.gancio.example/robots.txt": Resp(200, None, "", "User-agent: *\nDisallow: /\n"),
    "https://docs.gancio.example/instances": Resp(200, None, "", GANCIO_ORG_HTML)})
clk, slp, waits = fake_clock()
hosts, why = C._gancio_org_hosts(s, sleep=slp, clock=clk)
check(hosts is None and s.asked("docs.gancio.example", "/robots.txt") and not s.asked("docs.gancio.example", "/instances"),
      f"gancio.org: a redirect is asked of the new host's robots.txt, which refuses it ({why})")
s = org_session(404, page=Resp(200, None, C.GANCIO_DIRECTORY_PAGE,
                               "<html><title>Just a moment...</title>checking your browser</html>"))
clk, slp, waits = fake_clock()
hosts, why = C._gancio_org_hosts(s, sleep=slp, clock=clk)
check(hosts is None and "challenge" in (why or ""), f"gancio.org: a bot challenge on the page is a refusal ({why})")
s = org_session(404)
hosts, why = C._gancio_org_hosts(s, deadline=1.0)
check(hosts is None and not s.calls, f"gancio.org: past the deadline nothing is asked ({why})")

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
