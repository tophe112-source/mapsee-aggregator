#!/usr/bin/env python3
"""
test_seeds.py - every miss the owner reported is either fixed for its KIND or
on the record as not fixed yet, with a date.

seeds.json is the owner's list of sources the map was missing. The first is
https://shortrun.org/ (reported 2026-11-07): a free one-day comics festival in
Seattle that no discovery channel could find, because its date is WordPress
prose on its own site and every backend starts from a catalog, a directory, a
city or a venue - never from a festival's organiser. Fixing that one site by
hand would fix that one site. So each seed names a CLASS, and `_classes` names
the generator that finds that class from now on; the test refuses a seed whose
class has none.

A resolution has to be BACKED by the repository, not asserted:

    annual      annual_sources.json has a site with that key whose url is on
                the seed's host (the ref is "annual_sources.json#<key>")
    configured  the seed's host is in a config: catalog_curate._config_keys(),
                or a URL in one of the _ROBOTS_EXTRA files
    declined    an editorial no: the host is in a `_not_included` map
                (catalog_curate._declined_keys()), or the seed carries a dated
                reason
    held        a dated reason
    pending     a dated reason, and no older than PENDING_DAYS: a miss nobody
                has resolved in a month is a failure, not a backlog. The age
                is read against MAPSEE_TODAY when it is set, and fails then.
                Unset, it is read against the wall clock, and an ageing seed
                is a WARN line that fails nothing (a `::warning::` annotation
                under GitHub Actions): tests.yml runs on push only, and a
                push gate must break only when the code changes (2026-11-08
                review). So the age rule FAILS only where a run sets
                MAPSEE_TODAY to today on a schedule; this file cannot do that
                for itself

and a seed stores a URL, never a contact address (Short Run's published
contact is a personal mailbox).

annual_sources.json is written by mapsee_ingest_annual.py's package. When it is
missing, the real seeds are checked against a fixture handed in through
check_seeds()'s `annual` parameter, and the real-file case runs whenever the
file exists. No network.

Run: python test_seeds.py        (MAPSEE_TODAY=YYYYMMDD fixes "today", and
                                  makes an ageing pending seed a failure)
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse

import catalog_curate as cc

HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS = os.path.join(HERE, "seeds.json")
ANNUAL = os.path.join(HERE, "annual_sources.json")
PENDING_DAYS = 30
STATES = {"configured", "annual", "held", "declined", "pending"}
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
_CONTACT = re.compile(r"(?i)mailto:|\btel:|[\w.+-]+@[\w-]+\.[\w.-]+")

fails = []


def check(label, cond, detail=""):
    if callable(cond):
        try:
            cond = cond()
        except Exception as exc:  # noqa: BLE001
            cond, detail = False, f"raised {type(exc).__name__}: {exc}"
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


def host_of(url):
    try:
        h = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return re.sub(r"^www\.", "", h.lower().rstrip("."))


def _pinned(env=os.environ):
    """MAPSEE_TODAY as a date, or None when it is unset (or not YYYYMMDD)."""
    v = env.get("MAPSEE_TODAY")
    if v and re.fullmatch(r"\d{8}", v):
        return dt.date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    return None


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def configured_hosts():
    """Every host a config already reads: _config_keys() (canonical
    "host/path" strings) and every URL in the _ROBOTS_EXTRA files, which are
    the fetching configs outside CONFIG (annual_sources.json among them)."""
    hosts = {k.split("/", 1)[0] for k in cc._config_keys() if isinstance(k, str) and k}
    for fname in cc._ROBOTS_EXTRA:
        p = os.path.join(HERE, fname)
        if not os.path.exists(p):
            continue
        try:
            doc = _load(p)
        except ValueError:
            continue
        for _name, url in cc._walk_urls(doc):
            if host_of(url):
                hosts.add(host_of(url))
    return hosts


def declined_hosts():
    return {k.split("/", 1)[0] for k in cc._declined_keys() if isinstance(k, str) and k}


def _dated_reason(res):
    return (isinstance(res.get("reason"), str) and res["reason"].strip()
            and isinstance(res.get("date"), str) and bool(_DATE.match(res["date"])))


def check_seeds(doc, annual, config_hosts, declined, today, aged=None):
    """Problems with a seeds document, as a list of strings ([] when clean).

    `annual` is the annual_sources.json document (or a fixture); the host sets
    come from configured_hosts() / declined_hosts() or a fixture. A pending
    seed older than PENDING_DAYS on `today` is a problem, unless an `aged`
    list is passed: then it goes there instead, and the caller decides."""
    out = []
    classes = doc.get("_classes") if isinstance(doc, dict) else None
    if not isinstance(classes, dict) or not classes:
        return ["no _classes map"]
    for cname, c in classes.items():
        if not isinstance(c, dict) or not str(c.get("generator") or "").strip():
            out.append(f"class {cname!r} names no generator")
        if not (isinstance(c, dict) and _DATE.match(str(c.get("since") or ""))):
            out.append(f"class {cname!r} has no `since` date")
    sites = {}
    for s in (annual or {}).get("sites") or []:
        if isinstance(s, dict) and s.get("key"):
            sites[s["key"]] = s
    seeds = doc.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        return out + ["no seeds list"]
    for i, seed in enumerate(seeds):
        url = seed.get("url") if isinstance(seed, dict) else None
        tag = url or f"seed #{i}"
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or not host_of(url):
            out.append(f"{tag}: url is not an http(s) URL")
            continue
        blob = json.dumps(seed, ensure_ascii=False)
        if _CONTACT.search(blob):
            out.append(f"{tag}: carries a contact address; seeds store URLs only")
        if not _DATE.match(str(seed.get("reported") or "")):
            out.append(f"{tag}: no `reported` date")
        if not str(seed.get("why") or "").strip():
            out.append(f"{tag}: no `why`")
        if seed.get("class") not in classes:
            out.append(f"{tag}: class {seed.get('class')!r} is not in _classes")
        res = seed.get("resolution")
        if not isinstance(res, dict) or res.get("state") not in STATES:
            out.append(f"{tag}: resolution.state is not one of {sorted(STATES)}")
            continue
        state, host = res["state"], host_of(url)
        if state == "annual":
            ref = str(res.get("ref") or "")
            fname, _, key = ref.partition("#")
            site = sites.get(key)
            if fname != "annual_sources.json" or not key:
                out.append(f"{tag}: annual ref {ref!r} is not annual_sources.json#<key>")
            elif site is None:
                out.append(f"{tag}: annual_sources.json has no site {key!r}")
            elif host_of(site.get("url") or "") != host:
                out.append(f"{tag}: annual site {key!r} is on {host_of(site.get('url') or '')!r}, "
                           f"not {host!r}")
        elif state == "configured":
            if host not in config_hosts:
                out.append(f"{tag}: configured, but no config reads {host!r}")
        elif state == "declined":
            if host not in declined and not _dated_reason(res):
                out.append(f"{tag}: declined with neither a _not_included entry nor a dated reason")
        elif state in ("held", "pending"):
            if not _dated_reason(res):
                out.append(f"{tag}: {state} needs a reason and a YYYY-MM-DD date")
            if state == "pending" and _DATE.match(str(seed.get("reported") or "")):
                age = (today - dt.date.fromisoformat(seed["reported"])).days
                if age > PENDING_DAYS:
                    (out if aged is None else aged).append(
                        f"{tag}: pending for {age} days (limit {PENDING_DAYS})")
    return out


def gate(doc, annual, config_hosts, declined, today, pinned, warned):
    """check_seeds() as this script runs it on the real seeds. `pinned` is
    MAPSEE_TODAY's date, or None: unpinned, `today` is the wall clock, so a
    pending seed that ages past PENDING_DAYS goes into `warned` (a set) and
    fails nothing. Pinned, it fails like every other problem."""
    aged = None if pinned else []
    out = check_seeds(doc, annual, config_hosts, declined, today, aged)
    warned.update(aged or [])
    return out


def warn_line(w, env=os.environ):
    """How an aged seed is printed when it fails nothing. Under GitHub Actions
    (GITHUB_ACTIONS=true) it is a `::warning::` annotation, which the run's
    summary page lists: a plain WARN line sits in a passing log nobody opens
    (2026-11-08 review)."""
    text = f"{w} (MAPSEE_TODAY is unset, so this is the wall clock and fails nothing)"
    return f"::warning::{text}" if env.get("GITHUB_ACTIONS") == "true" else f"WARN  {text}"


# ---------------------------------------------------------------------------
PINNED = _pinned()
TODAY = PINNED or dt.date.today()
WARNED = set()
SEEDS_DOC = _load(SEEDS)
CONFIG_HOSTS = configured_hosts()
DECLINED_HOSTS = declined_hosts()

# What annual_sources.json will hold for Short Run: the contract
# {"sites": [{"key", "name", "name_rx", "url", ...}]}.
FIXTURE_ANNUAL = {"sites": [{"key": "short-run", "name": "Short Run Comix Festival",
                             "name_rx": r"short\s+run", "url": "https://shortrun.org/"}]}

check("the real seeds against an annual_sources.json fixture",
      lambda: gate(SEEDS_DOC, FIXTURE_ANNUAL, CONFIG_HOSTS, DECLINED_HOSTS, TODAY, PINNED, WARNED) == [],
      gate(SEEDS_DOC, FIXTURE_ANNUAL, CONFIG_HOSTS, DECLINED_HOSTS, TODAY, PINNED, WARNED))
if os.path.exists(ANNUAL):
    REAL_ANNUAL = _load(ANNUAL)
    check("the real seeds against the real annual_sources.json",
          lambda: gate(SEEDS_DOC, REAL_ANNUAL, CONFIG_HOSTS, DECLINED_HOSTS, TODAY, PINNED, WARNED) == [],
          gate(SEEDS_DOC, REAL_ANNUAL, CONFIG_HOSTS, DECLINED_HOSTS, TODAY, PINNED, WARNED))
else:
    print("skip  annual_sources.json does not exist yet; the fixture case stands in for it")

check("the first seed is Short Run, resolved annual",
      lambda: SEEDS_DOC["seeds"][0]["url"] == "https://shortrun.org/"
      and SEEDS_DOC["seeds"][0]["resolution"]["state"] == "annual"
      and SEEDS_DOC["seeds"][0]["resolution"]["ref"] == "annual_sources.json#short-run")
check("every class's generator names the file that does the finding",
      lambda: all(re.search(r"\b\w+\.py\b", c["generator"])
                  for c in SEEDS_DOC["_classes"].values()))
check("configured_hosts() reads the configs (thousands of hosts, the SPL Trumba feed's among them)",
      lambda: len(CONFIG_HOSTS) > 1000 and "trumba.com" in CONFIG_HOSTS, len(CONFIG_HOSTS))
# The 48 state fairs' hosts are in fair_sources.json alone, a _ROBOTS_EXTRA
# file outside CONFIG (measured 2026-11-08): a fair reported as a miss and
# resolved "configured" must pass.
check("configured_hosts() also reads the _ROBOTS_EXTRA files (a state fair's host)",
      lambda: "alaskastatefair.org" in CONFIG_HOSTS)


# --- the gate refuses what it should --------------------------------------
CLASS = "prose-only annual festival on an organiser's own site"


def doc_with(**seed_over):
    seed = {"url": "https://shortrun.org/", "reported": "2026-11-07", "why": "w",
            "class": CLASS, "resolution": {"state": "annual", "ref": "annual_sources.json#short-run",
                                           "reason": "r", "date": "2026-11-08"}}
    res = seed_over.pop("resolution", None)
    seed.update(seed_over)
    if res is not None:
        seed["resolution"] = res
    return {"_classes": {CLASS: {"generator": "catalog_mentions.py", "since": "2026-11-08"}},
            "seeds": [seed]}


D = dt.date(2026, 11, 8)
HOSTS = {"trumba.com", "example.org"}
DECL = {"refused.example"}


def problems(doc, annual=FIXTURE_ANNUAL, today=D):
    return check_seeds(doc, annual, HOSTS, DECL, today)


check("baseline fixture is clean", lambda: problems(doc_with()) == [], problems(doc_with()))
check("a class that is not in _classes fails",
      lambda: any("not in _classes" in p for p in problems(doc_with(**{"class": "venue"}))))
check("a class with no generator fails",
      lambda: any("no generator" in p for p in check_seeds(
          {"_classes": {CLASS: {"since": "2026-11-08"}}, "seeds": doc_with()["seeds"]},
          FIXTURE_ANNUAL, HOSTS, DECL, D)))
check("annual: a key annual_sources.json lacks fails",
      lambda: any("no site" in p for p in problems(doc_with(resolution={
          "state": "annual", "ref": "annual_sources.json#nope"}))))
check("annual: a site on another host fails",
      lambda: any("is on" in p for p in problems(doc_with(url="https://other.example/"))))
check("annual: www. and a path on the same host still match",
      lambda: problems(doc_with(url="https://www.shortrun.org/festival/")) == [])
check("annual: a missing annual_sources.json (empty fixture) fails an annual seed",
      lambda: any("no site" in p for p in problems(doc_with(), annual={})))
check("configured: a host no config reads fails",
      lambda: any("no config reads" in p for p in problems(doc_with(
          url="https://unread.example/", resolution={"state": "configured"}))))
check("configured: a host a config reads passes",
      lambda: problems(doc_with(url="https://example.org/cal",
                                resolution={"state": "configured"})) == [])
check("declined: in _not_included passes without a reason",
      lambda: problems(doc_with(url="https://refused.example/",
                                resolution={"state": "declined"})) == [])
check("declined: neither _not_included nor a dated reason fails",
      lambda: any("declined" in p for p in problems(doc_with(
          url="https://nobody.example/", resolution={"state": "declined", "reason": "no"}))))
check("declined: a dated reason passes",
      lambda: problems(doc_with(url="https://nobody.example/", resolution={
          "state": "declined", "reason": "robots.txt refuses /", "date": "2026-11-08"})) == [])
check("held: no date fails",
      lambda: any("held needs" in p for p in problems(doc_with(resolution={
          "state": "held", "reason": "asking the organiser"}))))
check("pending: 30 days old with a dated reason passes",
      lambda: problems(doc_with(reported="2026-10-09", resolution={
          "state": "pending", "reason": "r", "date": "2026-10-09"})) == [])
check("pending: 31 days old fails",
      lambda: any("pending for 31 days" in p for p in problems(doc_with(reported="2026-10-08", resolution={
          "state": "pending", "reason": "r", "date": "2026-10-08"}))))
# The push gate reads the wall clock unless MAPSEE_TODAY pins it (2026-11-08
# review): unpinned, the same seed is a WARN line and no failure.
OLD = doc_with(reported="2026-10-08", resolution={"state": "pending", "reason": "r", "date": "2026-10-08"})
W_UNPINNED, W_PINNED = set(), set()
check("pending: 31 days old, MAPSEE_TODAY unset: no failure, one warning",
      lambda: gate(OLD, FIXTURE_ANNUAL, HOSTS, DECL, D, None, W_UNPINNED) == []
      and W_UNPINNED == {"https://shortrun.org/: pending for 31 days (limit 30)"}, W_UNPINNED)
check("pending: 31 days old, MAPSEE_TODAY set: it fails, and nothing is only a warning",
      lambda: any("pending for 31 days" in p for p in gate(OLD, FIXTURE_ANNUAL, HOSTS, DECL, D, D, W_PINNED))
      and W_PINNED == set())
check("unpinned, every other problem still fails (a held seed with no date)",
      lambda: any("held needs" in p for p in gate(doc_with(resolution={"state": "held", "reason": "r"}),
                                                  FIXTURE_ANNUAL, HOSTS, DECL, D, None, set())))
check("MAPSEE_TODAY=20261108 pins 2026-11-08; unset, or not YYYYMMDD, pins nothing",
      lambda: _pinned({"MAPSEE_TODAY": "20261108"}) == D and _pinned({}) is None
      and _pinned({"MAPSEE_TODAY": "2026-11-08"}) is None)
check("a contact address anywhere in a seed fails",
      lambda: any("contact address" in p for p in problems(doc_with(why="write to someone@example.com"))))
check("a mailto: url fails as not http(s)",
      lambda: any("not an http" in p for p in problems(doc_with(url="mailto:someone@example.com"))))
check("an unknown state fails",
      lambda: any("resolution.state" in p for p in problems(doc_with(resolution={"state": "done"}))))

check("under GitHub Actions an aged seed is a ::warning:: annotation; elsewhere a WARN line",
      lambda: warn_line("s: pending for 31 days", {"GITHUB_ACTIONS": "true"}).startswith(
          "::warning::s: pending for 31 days (")
      and warn_line("s: pending for 31 days", {}).startswith("WARN  s: pending for 31 days (")
      and warn_line("s", {"GITHUB_ACTIONS": "false"}).startswith("WARN  "))

print()
for w in sorted(WARNED):
    print(warn_line(w))
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
