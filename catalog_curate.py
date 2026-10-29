#!/usr/bin/env python3
"""
catalog_curate.py - verify + merge new event-feed sources for the mapsee catalog.

The mapsee aggregator ingests three kinds of curated public feeds, each backed
by a JSON config file in this directory:

  localist_sources.json   Localist campus/city calendars  (GET {base}/api/2/events)
  ics_sources.json        iCal .ics feeds (LibCal, Trumba, CivicPlus, TEC, ...)
  opendata_sources.json   Socrata open-data event datasets (SODA $where/$limit)

Curation is discovery + VERIFICATION: never add a URL that has not been proven
to return real, FUTURE events using the exact User-Agent the production
aggregator sends. Feeds that only answer a browser UA (HTTP 403/500 to a bot)
would silently ingest nothing, and feeds full of past events add noise. This
tool encodes those checks so every run stays honest.

It also keeps a LEDGER (curation_ledger.json) of every URL ever tried, with the
result and the date. Both discover() and verify() skip anything already in a
config and anything that failed within the last --ttl days (default 90), so
scheduled runs stop re-probing known-dead sources and spend their effort on
genuinely new ground. Dead sources are rechecked once the TTL lapses, in case
they came online.

And a CURSOR (curation_cursor.json) of how far each catalog query has been read.
Discovery used to take the first page and stop, which meant a weekly job saw the
same rows for ever — "events" matches 1,390 Socrata datasets and only the top 100
were ever probed. The cursor advances a page per run and wraps at the end, so the
catalog is walked through rather than glanced at. It is committed BY the workflow
for exactly that reason; throw it away and discovery converges again.

DISCOVERY BACKENDS
  socrata    the federated US open-data catalog (api.us.socrata.com)
  ckan       eleven national data.gov.* portals, i.e. everywhere Socrata is not
  mobilizon  the joinmobilizon.org directory of live federated instances - the
             one backend whose supply GROWS on its own, because new instances
             appear without anyone publishing a dataset
  osm        venue calendars, from the map. See catalog_discover_osm.py
  civic      a whole TOWN's calendar - the city's own and its tourism board's -
             from Wikidata. See catalog_discover_civic.py

Usage:
  # candidates.json is an array; each item is a config entry PLUS a "type":
  #  [{"type":"localist","name":"...","base_url":"https://...","days":90},
  #   {"type":"ics","name":"...","url":"https://....ics","category":"learning",
  #      "geocode_suffix":", City, ST","limit":300},
  #   {"type":"opendata","name":"...","url":"https://portal/resource/id.json",
  #      "app_token":null,"category":"community","where":"start > '{now}'",
  #      "order":"start","limit":500,"geocode_venue":true,
  #      "geocode_suffix":", City, ST",
  #      "map":{"id":"...","title":"...","start":"...","venue":"..."}}]
  # propose NEW sources. The backend is POSITIONAL and must come before the
  # flags (main() reads argv[2:3]); omitted means socrata.
  python catalog_curate.py discover [socrata|ckan|mobilizon|osm|civic] [--limit 400]
         [--out candidates.json] [--metros N]      # --metros: osm only
         [--kinds community_centre,library]        # osm only: pin the walk to these OSM kinds
         [--workers N]                             # osm only: venues probed at once (default OSM_PROBE_WORKERS)
  python catalog_curate.py verify candidates.json [--recheck] [--ttl 90]
  python catalog_curate.py merge  candidates.verified.json
  python catalog_curate.py audit                     # re-check EXISTING configs
         [--max-minutes N]   # stop starting probes after N minutes; the oldest-checked go first
         [--workers N]       # probes at once, never two on one host (default AUDIT_WORKERS)
  # which configured requests robots.txt refuses, and hosts fetched unpaced.
  # Report only: it edits nothing.
  python catalog_curate.py robots
  python catalog_curate.py ledger                     # summarize what's been tried
  # the venues whose events are on a platform we already ingest, with the link
  python catalog_curate.py ledger --offsite [--refresh]   # --refresh re-probes
         # the routable finds recorded before the link was kept
  # what is held for the owner: a personal address is the calendar's id, or a ruling is owed
  python catalog_curate.py ledger --held
  python catalog_curate.py coverage                   # where the catalog is thin (no network)
  # prove a civic country before it ships: rows, seconds and the largest city
  python catalog_curate.py cityclass [US CH DE ...]   # every CITY_CLASSES entry if none named
  # after `git reset --hard origin/main`: put this run's ledger, cursor and
  # coverage line back on top of whatever main says NOW. No network.
  python catalog_curate.py reapply .curate-snapshot
"""
import datetime
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time

from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests

import mapsee_gcal
import robots_txt

# Every [OK]/[XX] line quotes a REMOTE title back at the console, and remote
# titles contain whatever the publisher typed. On a cp1252 console one narrow
# no-break space (U+202F, in a French event title) raised UnicodeEncodeError out
# of print() itself, which unwound the whole verify loop — and _save_ledger()
# runs AFTER that loop, so a run that had already probed forty feeds threw away
# every result it had bought. Nothing else in this pipeline lets one bad row
# abort a sweep of forty; this is that rule applied to stdout.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass                       # older stream object, or already unicode-safe

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_FILE = os.path.join(HERE, "curation_ledger.json")
CONFIG = {
    "localist": ("localist_sources.json", "base_url"),
    "ics": ("ics_sources.json", "url"),
    "opendata": ("opendata_sources.json", "url"),
    # OpenDatasoft datasets (the EU workhorse). Entries carry domain+dataset;
    # a derived "url" (the records API) is the dedup/ledger key. The file nests
    # entries under "sources" - _entries()/cmd_merge handle both shapes.
    "ods": ("ods_sources.json", "url"),
    # CKAN DataStore resources. Entries carry the full datastore_search URL, so
    # the ledger/dedup key needs no special case the way ods does.
    "ckan": ("ckan_sources.json", "url"),
    # Mobilizon instances (federated AGPL event software). Keyed by base_url, and
    # the file nests its list under "sites" rather than "sources" - see _entries.
    "mobilizon": ("mobilizon_sources.json", "base_url"),
    # VENUE-SITE configs. These predate curation and were invisible to it, which
    # meant _config_keys() did not know they existed: discovery could propose a
    # venue that has been configured for months, verification would pass it (it
    # works — that is why it was added), and merge would write a duplicate.
    # Volunteer Park Trust is the live example. Listing them here is what makes
    # "already in config" true for the long tail as well as for the catalogs.
    "squarespace": ("squarespace_sources.json", "collection"),
    "tribe": ("tribe_sources.json", "base_url"),
    "jsonld": ("jsonld_sources.json", "listing"),
    "mylisting": ("mylisting_sources.json", "explore_url"),
    "gancio": ("gancio_sources.json", "base_url"),
    # VenuePilot is keyed on its ACCOUNT IDS, not a URL — the venue's own domain
    # never appears in the config, so a URL key would dedup nothing.
    "venuepilot": ("venuepilot_sources.json", "account_ids"),
}

def _ods_url(e):
    return f"https://{e.get('domain')}/api/explore/v2.1/catalog/datasets/{e.get('dataset')}/records"

# The nested configs disagree about what to call their list: ods/ckan say
# "sources", mobilizon/gancio/tribe say "sites". Returning the list OBJECT (not a
# copy) is load-bearing - cmd_merge appends to what it gets back and then dumps
# the wrapper, so a fresh [] for an unrecognised key would drop every merge on
# the floor without erroring.
_LIST_KEYS = ("sources", "sites")

def _entries(fname, data):
    if not isinstance(data, dict):
        return data
    for k in _LIST_KEYS:
        if isinstance(data.get(k), list):
            return data[k]
    return []


def _today_int():
    # pass a fixed date via MAPSEE_TODAY=YYYYMMDD for reproducible runs.
    env = os.environ.get("MAPSEE_TODAY")
    if env and re.fullmatch(r"\d{8}", env):
        return int(env)
    d = datetime.date.today()
    return d.year * 10000 + d.month * 100 + d.day


def _as_date(yyyymmdd):
    s = f"{int(yyyymmdd):08d}"
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _now_iso():
    return _as_date(_today_int()).isoformat() + "T00:00:00"


def _days_since(yyyymmdd):
    try:
        return (_as_date(_today_int()) - _as_date(yyyymmdd)).days
    except Exception:  # noqa: BLE001
        return 10 ** 6


# A verifier gets ONE shot at a server on a bad morning, and losing it is
# expensive: a `fail` row parks the feed for the 90-day ledger TTL, and if the
# feed is already configured it is reported as a FEED_DOWN regression on top.
#
# Measured 2026-08-09 against the 15 feeds the audit had marked dead-while-
# configured: SIX were answering normally on a plain re-probe. Four of those six
# had failed on a ReadTimeout and one on a 403 — the transient shapes exactly.
# A 40% false-dead rate is not a finding about the catalog, it is a finding
# about probing once.
VERIFY_RETRIES = 3
VERIFY_BACKOFF_S = 3


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


# "The feed parses and has nothing on" is not "the feed is broken", and the
# ledger recorded both as `fail`. That is why FEED_DOWN reported 15 configured
# regressions when six were actually broken: the other nine were working
# calendars for venues with a quiet fortnight, an arts centre between seasons, a
# library whose export window had drifted. Retiring those loses a live source
# for ever; reporting them daily as regressions trains you to ignore the report.
#
# So there is a third status. `empty` still keeps discovery from PROPOSING the
# feed (adding a source with nothing upcoming ingests zero), but it is not a
# regression and configured_dead() does not raise it.
_EMPTY_RX = re.compile(r"(?:^|\D)([1-9]\d*)\s*(?:vevents|events|rows|records)\b"
                       r"[^/]*/\s*0\s+future", re.I)


class Verdict(str):
    """A verifier's note that names its own ledger status.

    Most notes are classified by their shape (_EMPTY_RX). A verdict that comes
    from a second request or a platform's measured behaviour, not from the
    note's numbers, says which status it is instead of hoping the wording falls
    on the right side of a regex: an empty CivicPlus category (see
    _civicplus_empty), a valid empty calendar on a platform whose missing
    calendars answer an error (EMPTY_CALENDAR_RULES), The Events Calendar
    between seasons (tribe_ical_existence). It is still a str, so it
    prints, slices and lands in the ledger's `reason` like any other note.
    """
    status = None

    # `status` has a default because copy.copy and pickle rebuild the object
    # by calling __new__ with str's own arguments (the text alone) and then
    # restore `status` from the instance dict. Without it both raised
    # TypeError (review of 2026-10-29): harmless under threads, a trap for
    # any process pool.
    def __new__(cls, text="", status=None):
        self = super().__new__(cls, text)
        self.status = status
        return self


def _status_for(ok, note):
    if ok:
        return "ok"
    said = getattr(note, "status", None)
    # `refused` comes from a robots.txt rule, never from a feed's answer: the
    # audit's gate (_audit_robots) over the requests an entry starts with, and
    # verify_jsonld when robots.txt disallows every link page it sampled.
    if said in ("empty", "fail", "refused"):
        return said
    return "empty" if _EMPTY_RX.search(note or "") else "fail"


def _verify_with_retry(fn, s, e):
    """Run a verifier, retrying the failures that a second look can change.

    A timeout, a connection reset and a 5xx are all "ask again later". A clean
    verdict — parsed the feed, no future events — is not, and re-fetching it
    would just triple the cost of every genuinely-dormant calendar. 403 is
    retried because rate limiters return it and so do bot walls; if it is a real
    block, three tries cost three requests and the answer is the same.
    """
    last = (False, "not attempted")
    for attempt in range(VERIFY_RETRIES):
        try:
            ok, note = fn(s, e)
            if ok:
                return True, note
            last = (ok, note)
            # A parsed-but-empty feed is a settled answer; only transport-shaped
            # failures are worth asking again.
            if not re.search(r"http (?:403|429|5\d\d)", note or ""):
                return last
        except (requests.Timeout, requests.ConnectionError) as ex:
            last = (False, f"{type(ex).__name__}: {str(ex)[:70]}")
        except Exception as ex:  # noqa: BLE001
            return False, f"{type(ex).__name__}: {str(ex)[:70]}"
        if attempt < VERIFY_RETRIES - 1:
            time.sleep(VERIFY_BACKOFF_S * (attempt + 1))
    return last


# A URL THAT CARRIES AN ADDRESS (`@`, `%40`) is a personal Google calendar's
# id in the making: a site whose "Calendar" link redirects to Google's embed
# page lands its probe there, and that URL is what the walk prints and the
# ledger keeps as `cal_url`. Printed as its host only, and never written.
_ADDRESS_IN_URL_RX = re.compile(r"@|%40", re.I)


def _public_url(u):
    """`u` for a public log: itself, or only its host when it carries an address."""
    u = str(u or "")
    if _ADDRESS_IN_URL_RX.search(u):
        p = urlparse(u)
        return f"{p.scheme}://{p.hostname or '?'}/(an address)"
    return u


def _canon(u):
    """Canonical dedup key for a feed URL: drop scheme, lowercase + strip a
    leading www. on the host, and strip a trailing slash when there's no query.
    Collapses http/https, www/non-www, and trailing-slash variants of the same
    feed so the same source can't be added twice. Query strings (?ical=1, ?cid=)
    are significant and kept as-is."""
    if not u:
        return u
    s = re.sub(r"^https?://", "", u.strip(), flags=re.I)
    m = re.match(r"([^/]+)(.*)$", s)
    if not m:
        return s.lower()
    host = re.sub(r"^www\.", "", m.group(1).lower())
    rest = m.group(2)
    if "?" not in rest:
        rest = rest.rstrip("/")
    return host + rest


# --- ledger ----------------------------------------------------------------
def _load_ledger():
    raw = json.load(open(LEDGER_FILE, encoding="utf-8")) if os.path.exists(LEDGER_FILE) else {}
    # re-key through _canon so older raw-URL entries migrate to canonical keys.
    out = {}
    for k, v in raw.items():
        out[_canon(k)] = v
    return out


def _save_ledger(led):
    """Write the ledger, keeping any entry that landed while this run held it.

    A WHOLE-FILE WRITE FROM AN IN-MEMORY COPY SILENTLY LOSES A CONCURRENT
    WRITER, and this repo's working mode makes that reachable: more than one
    agent works the same checkout, and every sweep loads the ledger once at the
    start and saves it minutes or hours later. Measured 2026-09-20 — a
    `ledger --offsite --refresh` that had loaded the file dropped ten
    neighbourhood-association probes another session had written in between,
    all of them dated the same day. Nothing had errored and nothing would have
    noticed; the entries were simply gone, and the next sweep would re-probe
    ten sites it had already answered.

    Re-reading and merging costs one file read on a path that is already doing
    a write, and _merge_ledgers is the rule cmd_reapply has always used for the
    same collision — the more recent probe wins a shared URL, ours winning a
    tie because this run finished later. Nothing deletes from the ledger, so a
    union can never resurrect something deliberately removed.
    """
    try:
        on_disk = _load_ledger()
    except Exception:                                             # noqa: BLE001
        on_disk = {}                     # unreadable or absent: ours is the file
    merged = _merge_ledgers(on_disk, led)
    # Written aside and renamed into place, so a run cancelled mid-write (the
    # audit now saves every few hundred probes, against a 19.6 MB file that
    # takes 1.2-1.7 s to write) leaves the last whole ledger, never half of one.
    tmp = LEDGER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
    os.replace(tmp, LEDGER_FILE)


DEAD_TTL = 90

# AN OLDER PROBE'S VERDICT LAPSES AT 60 DAYS, NOT THE DAY THE PROBE IMPROVED.
# Lapsing every version-1 row at once (2026-10-12) sent both walks back over
# ground they had read: 86.5% of the community walk's 6,087 probes in five
# runs (115-124) re-asked a key the ledger already had, and re-asked v1 rows
# verified 1.0% (99 of 9,898) against 3.0-4.7% per probe on new ground
# (measured 2026-10-23). From 60 days a v1 row is re-asked; before that the
# new ground goes first, and DEAD_TTL still ends every row at 90. On the
# 2026-10-24 ledger this parks 26,174 of the 28,947 v1 no-calendar and
# offsite rows again; the other 2,773 are 60-89 days old.
PROBE_REASK_DAYS = 60


def _dead_recently(led, key, ttl=DEAD_TTL):
    """Has this URL been probed and rejected inside the TTL?

    ONE predicate for discovery and verification, because they disagreed and the
    disagreement was silent. Both loops meant to ask this; discovery asked
    `led[key].get("ok") is False`, and no ledger row has ever had an "ok" field —
    the schema writes "status": "ok"/"fail". So the discovery filter never fired
    once in 469 recorded probes, `skipped["ledger"]` printed 0 every week, and
    every known-dead dataset was re-proposed forever, eating the candidate budget
    that was supposed to buy new ground.

    The TTL is the same 90 days cmd_verify uses, and for the same reason: a feed
    that was down is worth another look eventually, just not weekly.
    """
    rec = led.get(key)
    # 'empty' counts here too. It is not a regression when a CONFIGURED feed
    # reports it (see _status_for), but proposing a NEW source with nothing
    # upcoming buys a feed that ingests zero — which is the same waste of the
    # candidate budget, so discovery treats the two alike.
    # 'refused' too: robots.txt said no. Asking again next week costs a fetch
    # and gets the same answer; after the TTL a site that changed its file is
    # worth the look.
    if not rec or rec.get("status") not in ("fail", "empty", "refused"):
        return False
    # A VERDICT THE PROBE REACHED IS ONLY AS GOOD AS THE PROBE. A no-calendar
    # or offsite row written by an older probe (catalog_discover_osm
    # .PROBE_VERSION; rows before 2026-10-12 carry no number and are version 1)
    # has lapsed, so the walk asks again with what the probe can see now. An
    # offsite verdict too: find_calendar says offsite only after the
    # fingerprint failed on the homepage and every page it followed, so a
    # probe that reads more forms can turn it into a find, and version 2 also
    # stopped reading share buttons, posts and page widgets as a calendar (17
    # of the ledger's 632 offsite osm-venue rows were those, 2026-10-12). Not
    # a robots.txt refusal, an unreachable site or a failed verify: a better
    # fingerprint changes none of them. Only from PROBE_REASK_DAYS on (see
    # there), and verify asks the same, so the two still agree.
    reason = rec.get("reason") or ""
    age = _days_since(rec.get("checked", 0))
    if (rec.get("type") == "osm-venue"
            and (reason == "osm probe: no-calendar" or reason.startswith("osm probe: offsite:"))
            and _probe_version(rec) < _osm_probe_version()
            and age >= PROBE_REASK_DAYS):
        return False
    return age < ttl


def _probe_version(rec):
    try:
        return int(rec.get("probe") or 1)
    except (TypeError, ValueError):
        return 1


def _osm_probe_version():
    import catalog_discover_osm as osm
    return osm.PROBE_VERSION


# --- discovery cursor ------------------------------------------------------
# Discovery used to read the first page of each catalog query and stop, so it
# saw the same rows every week: "events" alone matches 1,390 Socrata datasets and
# only the top 100 were ever probed. A full 17-query sweep proposed SIX
# candidates, and would have proposed the same six forever.
#
# The cursor is what makes the job continual rather than convergent. Each query
# remembers the offset it reached; the next run starts there and the run after
# that starts further on, so the catalog is walked a page at a time and the sweep
# wraps to the beginning once it has been all the way round. Wrapping matters as
# much as advancing — portals publish new datasets, and a cursor parked at the
# end would never look at them.
CURSOR_FILE = os.path.join(HERE, "curation_cursor.json")


def _load_cursor():
    if not os.path.exists(CURSOR_FILE):
        return {}
    try:
        return json.load(open(CURSOR_FILE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}                      # a corrupt cursor costs a repeat, not a run


def _save_cursor(cur):
    json.dump(cur, open(CURSOR_FILE, "w", encoding="utf-8"), indent=1, sort_keys=True)


def _config_keys():
    """Every URL already configured, canonicalised.

    jsonld's key is `listing`, which is a LIST — a site can be crawled from more
    than one page. Every one of them has to land in the set or the same venue
    gets re-proposed through whichever page was not indexed.
    """
    keys = set()
    for t, (fname, key) in CONFIG.items():
        p = os.path.join(HERE, fname)
        if os.path.exists(p):
            for e in _entries(fname, json.load(open(p, encoding="utf-8"))):
                v = e.get(key) or (_ods_url(e) if fname.startswith("ods") else None)
                # jsonld's key is a list of listing URLS, all of which must be
                # indexed. venuepilot's is a list of ACCOUNT IDS — ints, which
                # _canon cannot take and which mean nothing individually; _key_of
                # folds them into one "venuepilot:<ids>" string. Telling the two
                # lists apart by their CONTENTS is what keeps both correct.
                if isinstance(v, list) and not all(isinstance(x, str) for x in v):
                    v = _key_of(dict(e, type=t))
                elif not isinstance(v, (str, list)):
                    v = _key_of(dict(e, type=t))
                for one in (v if isinstance(v, list) else [v]):
                    if isinstance(one, str) and one:
                        keys.add(_canon(one))
    return keys


def _key_of(e):
    if e.get("type") in ("localist", "mobilizon", "tribe", "gancio"):
        return e.get("base_url")
    if e.get("type") == "ods":
        return e.get("url") or _ods_url(e)
    if e.get("type") == "squarespace":
        return e.get("collection")
    if e.get("type") == "jsonld":
        listing = e.get("listing") or []
        return listing[0] if isinstance(listing, list) else listing
    if e.get("type") == "mylisting":
        return e.get("explore_url")
    if e.get("type") == "venuepilot":
        ids = e.get("account_ids") or []
        return "venuepilot:" + ",".join(str(i) for i in sorted(ids)) if ids else None
    return e.get("url")


# --- verifiers -------------------------------------------------------------
def verify_localist(s, e):
    r = s.get(e["base_url"].rstrip("/") + "/api/2/events?days=30&pp=5", timeout=15)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    evs = r.json().get("events") or []
    if not evs:
        return False, "0 events"
    # `days=30` is the API's promise, not ours. Reading the instance starts back
    # makes the future-events guarantee local, so an installation that ignores
    # the parameter cannot pass on a backlog. (verify_ics learned this the hard
    # way off VTIMEZONE dates; verify_opendata off text date columns.)
    today = _as_date(_today_int()).isoformat()
    starts = [str((i.get("event_instance") or {}).get("start") or "")[:10]
              for item in evs
              for i in ((item.get("event") or {}).get("event_instances") or [])]
    starts = [v for v in starts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)]
    if not starts:
        return False, f"{len(evs)} events, none with a parseable start"
    fut = sum(1 for v in starts if v >= today)
    return (fut > 0), f"{len(evs)} events / {fut} future instance(s)"


# AN EMPTY CIVICPLUS CATEGORY AND A DELETED ONE ANSWER THE SAME FILE. Measured
# 2026-10-29 on www.urbandale.org: catID=34 (Library Events, still on the
# site) and catID=99987 (never existed) both returned HTTP 200, text/calendar,
# 474 bytes, 0 VEVENTs. issaquahwa.gov did the same with 486 bytes (see the
# note above CIVICPLUS_ICAL_RX in catalog_discover_osm). So "0 vevents" alone
# decides nothing, and the audit recorded every such feed `fail`: 82 of the
# 130 configured feeds FEED_DOWN reported on 2026-10-29 were CivicPlus
# categories answering exactly that, among 1,275 CivicPlus feeds configured.
#
# What does decide it is the site's own list. /iCalendar.aspx carries one link
# per live category (catID=<n>, the category's name as the link text), quiet
# ones included: Urbandale's lists 9, among them 34, 38 (Mobile Recreation)
# and 39 (Concerts), all three empty that day. Still listed is a calendar
# between seasons: `empty`, kept, not a regression. Not listed is a category
# the city deleted: `fail`. An unreadable list leaves it `fail`, as before.
# The list is read ONCE per origin per run (the 1,275 feeds are on 601 sites,
# up to six on one), and robots.txt is asked first, like any publisher request.
_CIVICPLUS_FEED_RX = re.compile(r"/iCalendar\.aspx\?(?:[^#]*?&)?catID=(\d+)", re.I)
CIVICPLUS_LIST_TIMEOUT = 30
_CIVICPLUS_LISTS = {}            # origin -> {"cats": {id: name}} | {"error": why}


def civicplus_cat_id(url):
    """The category id of a CivicPlus iCal feed URL, or None for anything else."""
    m = _CIVICPLUS_FEED_RX.search(str(url or ""))
    return m.group(1) if m else None


def _robots_ask(s, url):
    """robots.txt's answer for `url`: the audit's shared reader when `s` is the
    audit's paced session (one robots.txt per origin per run, its Crawl-delay
    kept for the pacing), else a fresh reader, as verify has always asked."""
    pace = getattr(s, "pace", None)
    if pace is not None:
        return pace.robots_check(s.raw, url)
    return robots_txt.Robots(s).check(url)


def _get_after(s, url, ans, **kw):
    """A SECOND request to a host this probe has just asked, Crawl-delay first.

    CivicPlus's stock robots.txt asks `Crawl-delay: 10` (35 of 35 sampled
    hosts' files that could be read, 2026-10-29), and the list read used to go
    out the moment the feed's answer came back. In the audit the paced session
    waits (it knows when the host was last asked); elsewhere (`verify`) this
    waits the whole delay, which is never less than the host asked. A delay
    over AUDIT_MAX_CRAWL_DELAY_S is not waited out and the request is not
    sent sooner either: it raises, and the caller records why.
    """
    if getattr(s, "pace", None) is None:
        delay = float(ans.get("crawl_delay") or 0)
        if delay > AUDIT_MAX_CRAWL_DELAY_S:
            raise AuditCrawlDelay(f"robots.txt asks Crawl-delay {delay:g} s, "
                                  f"over the {AUDIT_MAX_CRAWL_DELAY_S:g} s a probe waits")
        if delay > 0:
            time.sleep(delay)
    return s.get(url, **kw)


def civicplus_list(s, url):
    """The categories /iCalendar.aspx lists on the site serving `url`.

    {"cats": {"34": "Library Events", ...}} or {"error": why}. Read once per
    origin and kept for the rest of the run, the error too: the audit retries
    a feed on a transport failure, and asking a struggling host's list again
    three times over is not what "once" means. It is the second request to the
    host in a row (the feed came first), so the host's Crawl-delay is honoured
    before it (_get_after).
    """
    import catalog_discover_osm as osm
    from html import unescape
    origin = robots_txt.origin_of(url)
    got = _CIVICPLUS_LISTS.get(origin)
    if got is not None:
        return got
    page = origin + "/iCalendar.aspx"
    ans = _robots_ask(s, page)
    if ans["allowed"] is not True:
        why = ("challenged" if ans["allowed"] is None else "refuses it")
        got = {"error": f"robots.txt {why}: {ans.get('rule') or ans['status']}"}
    else:
        try:
            r = _get_after(s, page, ans, timeout=CIVICPLUS_LIST_TIMEOUT, allow_redirects=True)
        except AuditCrawlDelay as ex:
            # NOT KEPT FOR THE ORIGIN: no server answered, the request was
            # simply not sent. In the audit the probe is then "not decided"
            # (its session saw the refusal) and the entry keeps its old row;
            # the next feed on this host may find the host ready.
            return {"error": str(ex)}
        except Exception as ex:                                   # noqa: BLE001
            got = {"error": type(ex).__name__}
        else:
            if r.status_code != 200:
                got = {"error": f"answered {r.status_code}"}
            elif osm.CHALLENGE_RX.search(r.text[:6000]):
                got = {"error": "a bot challenge"}
            else:
                cats = {}
                for _href, cid, label in osm.CIVICPLUS_ICAL_RX.findall(r.text):
                    cats.setdefault(cid, re.sub(r"\s+", " ", unescape(label)).strip())
                got = {"cats": cats} if cats else {"error": "no catID links on it"}
    _CIVICPLUS_LISTS[origin] = got
    return got


def civicplus_verdict(cat_id, listing):
    """Verdict for a CivicPlus category whose feed is a VCALENDAR with no VEVENT.

    The whole classification, apart from the fetch, so it can be tested on its
    own: listed -> empty, not listed -> fail, list unreadable -> fail.
    """
    if "cats" not in listing:
        return Verdict(f"0 vevents / 0 future; /iCalendar.aspx unreadable "
                       f"({listing.get('error') or '?'})", "fail")
    if cat_id in listing["cats"]:
        name = listing["cats"][cat_id] or "(unnamed)"
        return Verdict(f"0 vevents / 0 future; catID {cat_id} still listed on "
                       f"/iCalendar.aspx as '{name}'", "empty")
    return Verdict(f"0 vevents; catID {cat_id} no longer listed on /iCalendar.aspx "
                   f"({len(listing['cats'])} categories are)", "fail")


def _civicplus_empty(s, url):
    return civicplus_verdict(civicplus_cat_id(url), civicplus_list(s, url))


# A VALID CALENDAR WITH NO VEVENT PROVES THE CALENDAR EXISTS ONLY WHERE A
# MISSING ONE ANSWERS SOMETHING ELSE. Measured 2026-10-29 with the production
# UA, robots.txt asked first, by asking each platform for a calendar id that
# does not exist (1-2 requests a platform):
#   CivicPlus       a missing catID answers a VALID EMPTY VCALENDAR (urbandale
#                   catID=99987: 200, text/calendar, 474 B) - the same file a
#                   quiet category answers. Empty proves nothing; the site's
#                   /iCalendar.aspx list decides (_civicplus_empty).
#   LibCal          cid=999999 on leeds.libcal.com and smu-sg.libcal.com: 200
#                   text/html, 19 B, "invalid calendar id".
#   OpenAgenda      agendas/99999999999/events.v2.ics: 404 text/html.
#   unibas.ch       .../news-veranstaltungen/99999999.ics on botgarten.unibas.ch:
#                   200 text/calendar with a 0-byte body - no VCALENDAR at all.
#   My Calendar     ?feed=my-calendar-ics on a WordPress without the plugin
#                   (forterra.org) and an unregistered ?feed= on theblackaltar.org:
#                   404, WordPress's wp_die XML.
#   Vortex (UiO)    ?vrtx=ical on a folder that does not exist
#                   (nhm.uio.no/hva-skjer/no-such-folder-mapsee/): 404 text/html.
#   Google Calendar through the Calendar API: not measured (no key here). Read
#                   in mapsee_gcal.fetch_events: any answer but 200 (a deleted
#                   calendar's is 404 notFound) raises GcalError, and only a 200
#                   is turned into VCALENDAR text. Its timeMin is today, so a
#                   quiet calendar reads "0 vevents" by construction and
#                   _EMPTY_RX (which needs N > 0 VEVENTs) could never call it
#                   empty: the 3 Google calendars round 1 kept were `fail` again
#                   on every audit.
# So on the six platforms below a valid empty calendar is `empty` and says
# why; CivicPlus keeps its list test; every platform not in the table keeps
# "0 vevents / 0 future" -> `fail`, as before. Round 1 (2026-10-29) kept 14
# non-CivicPlus feeds `empty` by hand, and the audit re-derived `fail` for all
# 14 of them from this verifier; 3 Google, 2 LibCal, 1 OpenAgenda, 1 unibas.ch
# and 1 My Calendar are this table's, the other 6 are The Events Calendar's
# (tribe_ical_existence). Configured on 2026-10-29: LibCal 307, Google 459,
# OpenAgenda 66, My Calendar 61, unibas.ch 6, Vortex 4.
def _is_libcal(url, via_api=False):
    p = urlparse(url)
    return ((p.hostname or "").lower().endswith(".libcal.com")
            and p.path.lower().endswith("/ical_subscribe.php")
            and "cid" in parse_qs(p.query))


def _is_openagenda(url, via_api=False):
    p = urlparse(url)
    return ((p.hostname or "").lower().endswith("openagenda.com")
            and re.fullmatch(r"/agendas/\d+/events(?:\.v2)?\.ics", p.path) is not None)


def _is_unibas(url, via_api=False):
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return ((host == "unibas.ch" or host.endswith(".unibas.ch"))
            and re.search(r"/\d+\.ics$", p.path) is not None)


def _is_my_calendar(url, via_api=False):
    return "my-calendar-ics" in parse_qs(urlparse(url).query).get("feed", [])


def _is_vortex(url, via_api=False):
    return "ical" in parse_qs(urlparse(url).query).get("vrtx", [])


def _is_google_api(url, via_api=False):
    return bool(via_api and mapsee_gcal.calendar_id(url))


def _is_civicplus(url, via_api=False):
    return civicplus_cat_id(url) is not None


EMPTY_CALENDAR_RULES = (
    # (platform, recognises(url, via_api), what a missing calendar answers,
    #  verdict for a valid calendar with no VEVENT: "empty" or a callable
    #  existence test (s, url) -> Verdict)
    ("CivicPlus", _is_civicplus, "the same valid empty VCALENDAR", _civicplus_empty),
    ("Google Calendar API", _is_google_api, "404 notFound, which raises GcalError", "empty"),
    ("LibCal", _is_libcal, "200 'invalid calendar id'", "empty"),
    ("OpenAgenda", _is_openagenda, "404", "empty"),
    ("unibas.ch", _is_unibas, "200 with a 0-byte body", "empty"),
    ("My Calendar", _is_my_calendar, "404 (wp_die)", "empty"),
    ("Vortex", _is_vortex, "404", "empty"),
)


def empty_calendar_rule(url, via_api=False):
    """The EMPTY_CALENDAR_RULES row for `url`, or None (the old `fail`)."""
    for rule in EMPTY_CALENDAR_RULES:
        if rule[1](url, via_api):
            return rule
    return None


def empty_calendar_verdict(s, url, via_api=False):
    """Verdict for a valid VCALENDAR with no VEVENT, by the platform table."""
    rule = empty_calendar_rule(url, via_api)
    if rule is None:
        return Verdict("0 vevents / 0 future", "fail")
    platform, _match, missing, decide = rule
    if callable(decide):
        return decide(s, url)
    return Verdict(f"0 vevents / 0 future; a valid empty {platform} calendar, and a "
                   f"missing one answers {missing}, so this one exists", decide)


# THE EVENTS CALENDAR BETWEEN SEASONS. Its ?ical=1 export renders the upcoming
# list, so with nothing upcoming it answers something that is not a calendar:
# measured 2026-10-29, Aldeburgh Food and Drink, British Cider Week, Buffalo
# Niagara Waterkeeper and Fiddlehead Brewing answered 200 with a 0-byte body,
# Berlin Food Week and Dublin Festival of History 404 with a 0-byte body - and
# the same sites' REST route, /wp-json/tribe/events/v1/events, answered 200
# JSON with an `events` list. A dead plugin has no such route. So the route is
# the existence test, asked with robots.txt's permission for that exact URL:
#   200 JSON, `events` empty   -> `empty`: the plugin is alive, nothing is on.
#   `events` not empty         -> `fail`, saying the tribe adapter would read
#                                 it: the export is broken while the calendar is
#                                 not, which is a fix to propose, not a quiet
#                                 calendar.
#   anything else              -> `fail`, as before (refused, unreachable, 404,
#                                 not The Events Calendar's JSON).
# Only for a ?ical=1 feed answering 404, or 200 with no VEVENT (an empty body,
# an HTML page, an empty VCALENDAR); a 403, 429 or 5xx keeps its old note so the
# retry still sees it.
TRIBE_REST_PATH = "/wp-json/tribe/events/v1/events"


def _is_tribe_ical(url):
    return "1" in parse_qs(urlparse(url).query).get("ical", [])


# A SCOPED EXPORT IS NOT THE SITE'S CALENDAR, and the route answers for the
# site. 7 configured ?ical=1 feeds on 2026-10-29 read one category or tag
# (bartramsgarden `tribe_events_cat=volunteer`, casalcatalatolosa
# `/tag/concert/`, lunel `/category/...`, two extension.wsu.edu subsites'
# `/events/category/<slug>/`), and the route at the ORIGIN is the network
# root, not the /thurston/ subsite: a quiet category on a busy site read
# "the tribe adapter would read it" (which would import every category) and a
# deleted one on a quiet site `empty` (review of 2026-10-29). So the test is
# asked only where the feed is plainly the whole calendar at the origin: a
# query of nothing but ical, post_type=tribe_events and eventDisplay, and a
# path of at most the archive slug and a view (`/events/`, `/events/list/`).
# Anything else - 40 of the 415 configured ?ical=1 feeds, 16 of them an
# /event(s)/<slug>/ export, none with an `empty` row - keeps the old verdict,
# `fail`, with no advice: its scope is not mapped to REST parameters.
_TRIBE_SITEWIDE_QUERY = {"ical", "post_type", "eventDisplay"}
_TRIBE_VIEWS = {"list", "lista", "liste", "month", "mois", "monat", "mes", "maand", "week",
                "day", "photo", "map", "upcoming", "past", "today", "summary"}


def tribe_feed_is_sitewide(url):
    """Whether a ?ical=1 feed is the site's whole calendar at its origin."""
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    if set(q) - _TRIBE_SITEWIDE_QUERY:
        return False
    if q.get("post_type", ["tribe_events"]) != ["tribe_events"]:
        return False
    segs = [x for x in p.path.split("/") if x]
    return len(segs) <= 1 or (len(segs) == 2 and segs[1].lower() in _TRIBE_VIEWS)


def tribe_rest_url(url):
    """The REST request the existence test makes for a ?ical=1 feed."""
    day = _as_date(_today_int()).isoformat()
    return (robots_txt.origin_of(url) + TRIBE_REST_PATH
            + "?" + urlencode({"per_page": 10, "start_date": day}))


def tribe_ical_existence(s, url, note):
    """Verdict for a ?ical=1 export that did not give a calendar with events."""
    if not tribe_feed_is_sitewide(url):
        return Verdict(note, "fail")
    rest = tribe_rest_url(url)
    ans = _robots_ask(s, rest)
    if ans["allowed"] is not True:
        why = "challenged" if ans["allowed"] is None else "refuses it"
        return Verdict(f"{note}; The Events Calendar REST not asked: robots.txt {why} "
                       f"({ans.get('rule') or ans['status']})", "fail")
    try:
        r = _get_after(s, rest, ans, timeout=30)
    except Exception as ex:                                       # noqa: BLE001
        return Verdict(f"{note}; The Events Calendar REST unreachable "
                       f"({type(ex).__name__}: {str(ex)[:60]})", "fail")
    if r.status_code != 200:
        return Verdict(f"{note}; The Events Calendar REST answers {r.status_code}", "fail")
    try:
        body = r.json()
    except Exception:                                             # noqa: BLE001
        body = None
    evs = body.get("events") if isinstance(body, dict) else None
    if not isinstance(evs, list):
        return Verdict(f"{note}; the REST route answers 200 but not with "
                       f"The Events Calendar's JSON", "fail")
    if not evs:
        return Verdict(f"{note}; The Events Calendar REST answers 200 with 0 upcoming", "empty")
    try:
        total = int(body.get("total") or len(evs))
    except (TypeError, ValueError):
        total = len(evs)
    return Verdict(f"{note}; The Events Calendar REST lists {total} upcoming: the tribe "
                   f"adapter would read it (a fix to propose, not an empty calendar)", "fail")


# WHAT COUNTS AS A CALENDAR IS WHAT mapsee_ingest_ics READS. The adapter
# decodes the body (_decode_ics: RFC 5545's UTF-8 when the server names no
# charset), unfolds it, and takes every line that is exactly BEGIN:VEVENT; it
# never looks for BEGIN:VCALENDAR. This verifier looked for that marker in the
# first 400 characters only, so Forterra (Seattle) - whose WordPress prints
# 8,120 characters of Elementor CSS before the calendar, `</style>BEGIN:VCALENDAR`
# on one line - was "http 200 / no VCALENDAR" in the 2026-10-18 audit while
# production kept 4 of 5 VEVENTs from it on 10-25, 10-27 and 10-28. Now the
# VEVENTs are counted with the adapter's parse_ics, and a body with none is a
# calendar when BEGIN:VCALENDAR appears anywhere in it.
def verify_ics(s, e):
    import mapsee_ingest_ics as ics
    url = e["url"]
    key = mapsee_gcal.api_key()
    via_api = bool(key and mapsee_gcal.calendar_id(url))
    if via_api:
        # The same text the ics adapter will parse; see mapsee_gcal.
        try:
            txt = mapsee_gcal.fetch_as_ics(s, url, key, timeout=60)
        except mapsee_gcal.GcalError as ex:
            return False, str(ex)[:90]
    else:
        fetch = "https://" + url[9:] if url.lower().startswith("webcal://") else url
        r = s.get(fetch, timeout=60)
        if r.status_code != 200:
            note = f"http {r.status_code} / no VCALENDAR"
            if r.status_code == 404 and _is_tribe_ical(url):
                return False, tribe_ical_existence(s, url, note)
            return False, note
        txt = ics._decode_ics(r)[0]
    events = ics.parse_ics(txt)
    if not events:
        if "BEGIN:VCALENDAR" not in txt:
            note = "http 200 / no VCALENDAR"
            if _is_tribe_ical(url):
                return False, tribe_ical_existence(s, url, note)
            return False, note
        if _is_tribe_ical(url):
            return False, tribe_ical_existence(s, url, "0 vevents / 0 future")
        return False, empty_calendar_verdict(s, url, via_api)
    today = _today_int()
    # DTSTART of the VEVENTs only. Scanning the whole file also catches the
    # DTSTARTs inside VTIMEZONE STANDARD/DAYLIGHT sub-components, which carry
    # DST-transition dates - so a calendar whose real events all ended years ago
    # can report thousands of "future" dates and sail through this gate on
    # timezone metadata alone. (Seen in the wild: a dead market calendar
    # reporting "51 vevents / 15946 future" under two different URLs.)
    starts = [m.group(1) for ev in events
              for m in [re.match(r"(\d{8})", str((ev.get("DTSTART") or ("",))[0]).strip())] if m]
    fut = sum(1 for d in starts if int(d) >= today)
    return (fut > 0), f"{len(events)} vevents / {fut} future"


def verify_opendata(s, e):
    where = e.get("where", "").replace("{now}", _now_iso())
    params = {"$limit": 3}
    if where:
        params["$where"] = where
    if e.get("order"):
        params["$order"] = e["order"]
    r = s.get(e["url"], params=params, timeout=25)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    rows = r.json()
    if not rows:
        return False, "0 rows"
    m = e.get("map", {})
    for key in ("title", "start"):
        col = m.get(key)
        if col and col not in rows[0]:
            return False, f"missing column '{col}' for {key}"
    # SODA compares a TEXT column as text, so "$where date > '2026-08-02'" is
    # true for "April 01 2011" ('A' sorts after '2') and for "20-Jun". Datasets
    # whose dates are human-formatted strings were passing this check with
    # nothing but archive rows behind them, so the sampled values have to parse
    # as real dates and actually be in the future.
    #
    # PARSED BY THE ADAPTER'S OWN READER, NOT A SECOND ONE. This check used to
    # take the value's first ten characters as ISO, so a column the adapter
    # reads perfectly well failed here: City of Calgary's `next_date_times`
    # ("October 27 2026 1 - 4 p.m.") was "not a date column (sample: 'October
    # 27')" in the 2026-10-18 audit while production kept 12 of 13 rows from it
    # on 2026-10-28, and FEED_DOWN reported a working source. Now the date is
    # whatever mapsee_ingest_opendata._iso_parts makes of it, and a row is
    # future when the adapter's own past-event drop would keep it (its end
    # date, else its start).
    #
    # WHAT IS REFUSED IS A VALUE WHOSE DATE IS NOT A DATE, NOT ONE WHOSE START
    # PYTHON CANNOT READ. When neither fromisoformat nor the free-text reader
    # takes a value, _iso_parts hands it back whole as the start and its first
    # ten characters as the date key: "October 27" and "TBD" give a date key
    # that is not YYYY-MM-DD, and are refused here ("not a date column"); the
    # adapter emits them anyway and the sync's Postgres refuses the row
    # (PostgreSQL 16.15, 2026-10-29: 'October 27'::timestamptz and
    # 'TBD'::timestamptz both error). But "2026-11-27 7:00 PM" gives the date
    # key 2026-11-27 and the start "2026-11-27 7:00 PM", which both this check
    # and the adapter accept, and Postgres reads as 2026-11-27 19:00. So
    # requiring a start Python can parse, here or in the adapter, would refuse
    # a row the database keeps. The test here is an ISO date key; the adapter's
    # is weaker (any date key, then its past-event drop), and Postgres has the
    # last word on the start.
    # (The review of 2026-10-29 found the earlier wording here claimed the
    # opposite for this value without measuring it.)
    start_col = m.get("start")
    if start_col:
        import mapsee_ingest_opendata as od
        today = _as_date(_today_int()).isoformat()
        iso_rx = re.compile(r"\d{4}-\d{2}-\d{2}")
        raw = [row.get(start_col) for row in rows]
        last_days = []
        for row in rows:
            day = od._iso_parts(od._get(row, start_col))[2] or ""
            if not iso_rx.fullmatch(day):
                continue
            end = od._iso_parts(od._get(row, m.get("end")))[2] or ""
            last_days.append(end if iso_rx.fullmatch(end) else day)
        if not last_days:
            return False, (f"'{start_col}' is not a date column "
                           f"(sample: {str(raw[0] or '')[:40]!r})")
        if not any(v >= today for v in last_days):
            return False, f"no future rows (latest sampled {max(last_days)})"
    return True, f"{len(rows)} rows, sample: {rows[0].get(m.get('title',''))}"


def verify_ods(s, e):
    """Records exist AND at least one is in the future.

    This used to accept any dataset with total_count > 0, which is not the same
    claim: a finished 2019 festival archive passes that and then ingests nothing
    but past events. Same hole as verify_opendata had, one step weaker — it never
    looked at a date at all.
    """
    start = (e.get("map") or {}).get("start")
    today = _as_date(_today_int()).isoformat()
    params = {"limit": 3}
    if start:
        params["where"] = f"{start} >= date'{today}'"
        params["order_by"] = start
    r = s.get(_ods_url(e), params=params, timeout=25)
    if r.status_code >= 400 and start:
        # Not every dataset types its start column as a date, and the portal
        # answers 400 rather than coercing. Fall back rather than condemn a
        # source over query syntax — but say the dates went unchecked.
        r = s.get(_ods_url(e), params={"limit": 3}, timeout=25)
        if r.status_code != 200:
            return False, f"http {r.status_code}"
        n = (r.json() or {}).get("total_count", 0)
        return (n > 0), f"{n} records (start '{start}' not date-filterable — dates unchecked)"
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    payload = r.json() or {}
    n = payload.get("total_count", 0)
    if not start:
        return (n > 0), f"{n} records (no start column mapped — dates unchecked)"
    if not n:
        return False, "0 future records"
    return True, f"{n} future records"


def verify_ckan(s, e):
    """Rows exist AND at least one start parses to a future date.

    datastore_search has no range operator, so unlike Socrata there is no WHERE
    clause doing this server-side — the check has to read the dates back, which
    is where verify_opendata and verify_ods both ended up anyway.
    """
    m = e.get("map") or {}
    start = m.get("start")
    params = {"limit": 5}
    if start:
        params["sort"] = f"{start} desc"
    r = s.get(e["url"], params=params, timeout=45)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    payload = r.json() or {}
    if not payload.get("success"):
        return False, f"ckan error: {str(payload.get('error'))[:80]}"
    rows = (payload.get("result") or {}).get("records") or []
    if not rows:
        return False, "0 rows"
    if not start:
        return False, "no start column mapped"
    today = _as_date(_today_int()).isoformat()
    iso = [str(row.get(start) or "")[:10] for row in rows]
    parseable = [v for v in iso if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)]
    if not parseable:
        return False, f"'{start}' is not a date column (sample: {iso[0]!r})"
    if not any(v >= today for v in parseable):
        return False, f"no future rows (latest sampled {max(parseable)})"
    title = m.get("title")
    return True, f"{len(rows)} rows, sample: {str(rows[0].get(title))[:40]}"


# The probe is deliberately the SAME shape mapsee_ingest_mobilizon.py sends —
# searchEvents(beginsOn:) rather than events(), and every field inside
# `... on Event` because `elements` is a union and a bare field errors on every
# instance. A verifier that asks an easier question than the adapter does is a
# verifier that green-lights feeds the adapter cannot read.
MOBILIZON_PROBE = """
query($b:DateTime,$l:Int){
  searchEvents(beginsOn:$b, limit:$l, page:1){
    total
    elements{ ... on Event { uuid title beginsOn } }
  }
}"""


# A QUIET INSTANCE IS NOT A MISSING ONE, AND THE API SAYS WHICH. Measured
# 2026-10-29 with the production UA (robots.txt asked first, though the
# GraphQL API is documented): bosvreugdevents.nl and events.litera.tools
# answered searchEvents(beginsOn: today) with 200 JSON, total 0 - and asked
# from a year back, 41 and 5 events (206 and 98 since 2000; the latest
# 2026-06-27 and 2026-09-30). A former instance answers something else:
# ibb.info's /api is a 404 HTML page ("http 404", `fail` as before). So an
# instance that answers the probe normally with nothing upcoming is asked once
# more, from MOBILIZON_QUIET_DAYS back: events there make it `empty`, none
# leaves it `fail` ("0 future events", an instance nobody uses). Round 2 kept
# both `empty` by hand and the audit would have re-filed them.
MOBILIZON_QUIET_DAYS = 365


def _mobilizon_search(s, base, begins, limit):
    """(node, why): the searchEvents node, or None and why the answer is not one."""
    r = s.post(f"{base}/api", timeout=30,
               json={"query": MOBILIZON_PROBE, "variables": {"b": begins, "l": limit}},
               headers={"Content-Type": "application/json", "Accept": "application/json"})
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    try:
        body = r.json() or {}
    except Exception:  # noqa: BLE001
        return None, "not json (is this a Mobilizon instance?)"
    if not isinstance(body, dict):
        return None, "not json (is this a Mobilizon instance?)"
    if body.get("errors"):
        return None, f"graphql: {str(body['errors'])[:70]}"
    node = (body.get("data") or {}).get("searchEvents")
    if not isinstance(node, dict):
        return None, "no searchEvents in the answer"
    return node, None


def verify_mobilizon(s, e):
    base = (e.get("base_url") or "").rstrip("/")
    if not base:
        return False, "no base_url"
    today = _as_date(_today_int())
    node, why = _mobilizon_search(s, base, today.isoformat() + "T00:00:00Z", 5)
    if node is None:
        return False, why
    rows = [x for x in (node.get("elements") or []) if x]
    if rows:
        return True, f"{node.get('total', len(rows))} upcoming, sample: {str(rows[0].get('title'))[:40]}"
    since = today - datetime.timedelta(days=MOBILIZON_QUIET_DAYS)
    past, why = _mobilizon_search(s, base, since.isoformat() + "T00:00:00Z", 1)
    if past is None:
        return False, f"0 future events; the look back answered {why}"
    try:
        held = int(past.get("total") or 0)
    except (TypeError, ValueError):
        held = 0
    if held:
        return False, Verdict(f"0 future events; the instance answers, with {held} events "
                              f"since {since.isoformat()}", "empty")
    return False, f"0 future events; none since {since.isoformat()} either"


# --- venue-site verifiers ---------------------------------------------------
# These prove the same thing the others do — the feed answers, and what it
# answers with is in the FUTURE — but they do it by asking the page the way its
# own adapter will. A candidate that parses beautifully and is all last season
# is `empty`, not `fail`: wisconsinbikefed's iCal was configured on the strength
# of 50 well-formed VEVENTs and ingested 0, every one of them past.
def _future_note(total, future, unit="events"):
    """The note shape _EMPTY_RX reads, so a dormant calendar is not a dead one."""
    return f"{total} {unit} / {future} future"


def verify_tribe(s, e):
    """The Events Calendar's own REST route, with the adapter's date window."""
    base = (e.get("base_url") or "").rstrip("/")
    if not base:
        return False, "no base_url"
    today = _as_date(_today_int())
    end = today + datetime.timedelta(days=int(e.get("within_days", 120)))
    r = s.get(f"{base}/wp-json/tribe/events/v1/events",
              params={"per_page": 10, "start_date": today.isoformat(),
                      "end_date": end.isoformat()}, timeout=30)
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    try:
        body = r.json()
    except Exception:                                             # noqa: BLE001
        return False, "not json"
    evs = body.get("events") or []
    total = int(body.get("total") or len(evs))
    if not evs:
        # A QUIET CALENDAR IS NOT A REMOVED ONE, AND THE ROUTE SAYS WHICH. The
        # plugin answers its own JSON (`events`, `total`, `total_pages`) with an
        # empty list when nothing is on; a WordPress without it answers 404
        # `rest_no_route`, and a site that left WordPress a 404 page. "0 events
        # / 0 future" fell outside _EMPTY_RX (it needs N > 0), so every quiet
        # site was `fail`: 45 of the 69 configured tribe `fail` rows on
        # 2026-10-29. That day the 36 kept `empty` by hand all answered 200
        # JSON with an empty list (1 to 146 events in the past year), and the
        # removed plugins 404 (Sacré-Cœur, Pittsburg, 't Reghthuys
        # rest_no_route; Cirkel Ordenen, Lemoore, Lawrence a page).
        if isinstance(body.get("events"), list):
            return False, Verdict(f"{_future_note(total, 0)}; The Events Calendar REST "
                                  f"answers 200 with 0 upcoming", "empty")
        return False, _future_note(total, 0)
    fut = [x for x in evs if str(x.get("start_date") or "")[:10] >= today.isoformat()]
    if not fut:
        return False, _future_note(len(evs), 0)
    return True, f"{total} events, {len(fut)}/{len(evs)} on page 1 future"


def verify_gancio(s, e):
    """Every Gancio instance serves the same public /api/events."""
    base = (e.get("base_url") or "").rstrip("/")
    if not base:
        return False, "no base_url"
    import calendar as _cal
    start = _as_date(_today_int())
    end = start + datetime.timedelta(days=int(e.get("within_days", 180)))
    r = s.get(f"{base}/api/events",
              params={"start": _cal.timegm(start.timetuple()),
                      "end": _cal.timegm(end.timetuple()), "max": 20}, timeout=30)
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    try:
        evs = r.json()
    except Exception:                                             # noqa: BLE001
        return False, "not json"
    if not isinstance(evs, list) or not evs:
        return False, _future_note(0, 0)
    # Gancio's start_datetime is UNIX SECONDS, which is the one thing about this
    # API that catches people out (the adapter's own docstring says so).
    now = _cal.timegm(start.timetuple())
    fut = [x for x in evs if int(x.get("start_datetime") or 0) >= now]
    if not fut:
        return False, _future_note(len(evs), 0)
    return True, f"{len(fut)}/{len(evs)} future"


def verify_venuepilot(s, e):
    """VenuePilot's public GraphQL, asked exactly as the adapter asks it."""
    ids = e.get("account_ids") or []
    if not ids:
        return False, "no account_ids"
    import mapsee_ingest_venuepilot as vp
    today = _as_date(_today_int())
    end = today + datetime.timedelta(days=int(e.get("within_days", 120)))
    try:
        r = s.post(vp.API, json={"query": vp.QUERY,
                                 "variables": {"ids": ids, "sd": today.isoformat(),
                                               "ed": end.isoformat(), "limit": 20, "page": 1}},
                   timeout=30)
    except Exception as exc:                                      # noqa: BLE001
        return False, f"{type(exc).__name__}"
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    try:
        body = r.json()
    except Exception:                                             # noqa: BLE001
        return False, "not json"
    if body.get("errors"):
        return False, f"graphql: {str(body['errors'][:1])[:60]}"
    data = (body.get("data") or {}).get("paginatedEvents") or {}
    # `collection`, not `results` — read what the adapter reads or the verifier
    # answers a question about a key nobody serves.
    evs = data.get("collection") or []
    total = ((data.get("metadata") or {}).get("totalCount")) or len(evs)
    if not evs:
        return False, _future_note(0, 0)
    return True, f"{len(evs)} on page 1 of {total} future"


def verify_squarespace(s, e):
    """The BARE collection page — ?format=json is disallowed platform-wide."""
    url = e.get("collection")
    if not url:
        return False, "no collection"
    if "?" in url:
        return False, "collection carries a query string"
    r = s.get(url, timeout=30)
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    import mapsee_ingest_squarespace as sq
    every = sq.parse_articles(r.text, e.get("venue"), upcoming_only=False)
    fut = sq.parse_articles(r.text, e.get("venue"), upcoming_only=True)
    if not every:
        return False, "no event articles on the collection page"
    if not fut:
        return False, _future_note(len(every), 0)
    return True, f"{len(fut)}/{len(every)} upcoming"


# MODERN EVENTS CALENDAR WITH NOTHING ON prints its no-events text where the
# list would be: an events container holding that text and no markup. Measured
# 2026-10-29 on the two round-2 agendas the audit re-files `fail` "no
# schema.org Event blocks found": New Rock (newrockescalade.be,
# `<div class="mec-skin-list-events-container" id="mec_skin_events_6270">`
# holding 'Aucun événement trouvé !', no view beacon) and MEERGroen
# (stichtingmeergroen.nl/evenementen/, MEC lite, 'Geen evenementen gevonden!'
# and a `data-count="0"` view beacon), 0 `mec-event-article` on either. The
# text is translated, so the SHAPE is matched: every events container on the
# page holds text only (a populated list puts `<article
# class="mec-event-article">` inside it). A removed page answers 404 and a
# removed shortcode prints no container, so neither reads as quiet. The text
# must have a visible character: a container holding only whitespace says
# nothing (a script may fill it), and is not quiet (review of 2026-10-29).
_MEC_CONTAINER_RX = re.compile(r'<div class="mec-skin-[a-z-]+-events-container"', re.I)
_MEC_EMPTY_RX = re.compile(r'<div class="mec-skin-[a-z-]+-events-container"[^>]*>\s*(\S[^<>]{0,119}?)\s*</div>',
                           re.I)


def mec_no_events(html):
    """MEC's no-events text when every events container on the page holds
    nothing else, else None."""
    html = html or ""
    containers = len(_MEC_CONTAINER_RX.findall(html))
    texts = _MEC_EMPTY_RX.findall(html)
    if not containers or len(texts) != containers or "mec-event-article" in html:
        return None
    return texts[0].strip()


def verify_jsonld(s, e):
    """Follow a few of the listing's event links and read their Event blocks.

    Capped hard: verification must not cost the 150-page crawl the real run is
    allowed. Finding future events on the first handful is enough to prove the
    shape; whether the tail is worth having is what max_events decides later.

    A FAILING NOTE SAYS WHAT THE LINKS ANSWERED. A non-200 event page used to
    be dropped without a word, so the 2026-10-18 audit's "no schema.org Event
    blocks found" on 18 Wix listings production kept events from could not be
    explained afterwards (review of 2026-10-29). Now it carries how many link
    pages the listing had, how many were fetched, what each non-200 answered
    and how many robots.txt disallows (those are not fetched, and do not fail
    an entry on their own: when every page sampled is disallowed, the verdict
    is `refused`, with the rule). The wording never says "http 403", which
    _verify_with_retry would read as the LISTING's transport failure.
    """
    listing = e.get("listing") or []
    listing = listing if isinstance(listing, list) else [listing]
    if not listing:
        return False, "no listing"
    import mapsee_ingest_jsonld as jl
    r = s.get(listing[0], timeout=30)
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    today = _as_date(_today_int()).isoformat()
    seen_events = future = 0

    def _count(text):
        nonlocal seen_events, future
        for block in jl._LD_RX.findall(text):
            doc = jl._parse_ld(block)
            if doc is None:
                continue
            for item in jl._iter_items(doc):
                if not jl._is_event(item):
                    continue
                seen_events += 1
                if str(item.get("startDate") or "")[:10] >= today:
                    future += 1

    # robots.txt for a link page: the audit's shared reader (asked before the
    # listing already), else one reader for this call.
    pace = getattr(s, "pace", None)
    reader, rules = [], []

    def disallowed(u):
        if pace is not None:
            ans = pace.robots_check(s.raw, u)
        else:
            if not reader:
                reader.append(robots_txt.Robots(s))
            ans = reader[0].check(u)
        if ans["allowed"] is False and ans["status"] == "ok":
            rules.append(ans.get("rule") or "?")
            return True
        return False

    _count(r.text)
    pat = e.get("link_pattern")
    tmpl = e.get("url_template")
    detail = ""
    if pat and not future:
        urls = list(dict.fromkeys(re.findall(pat, r.text)))
        listed = len(urls)
        # Both ends of the list, still six fetches at most. A calendar listed
        # oldest-first keeps its future at the END: Centretown CHC's Wix page
        # (Ottawa, 76 upcoming drop-ins) lists 116 slugs and the first upcoming
        # one is the 41st, so the first six could only ever say "none future".
        urls = urls if len(urls) <= 6 else urls[:3] + urls[-3:]
        fetched = refused = 0
        codes, errors = {}, {}
        for u in urls:
            u = u if isinstance(u, str) else u[0]
            # url_template is not decoration: a Wix site's link_pattern captures a
            # SLUG, and the page URL is /event-info/<slug>. Ignoring the template
            # fetched the bare slug against the listing's directory and found
            # nothing, so every Wix candidate failed verification for a reason
            # that had nothing to do with the site. ingest_site applies it; so
            # must whatever decides whether ingest_site would work.
            page = urljoin(listing[0], tmpl.format(u) if tmpl else u)
            if disallowed(page):
                refused += 1
                continue
            try:
                rr = s.get(page, timeout=25)
            except Exception as ex:                               # noqa: BLE001
                errors[type(ex).__name__] = errors.get(type(ex).__name__, 0) + 1
                continue
            fetched += 1
            if rr.status_code == 200:
                _count(rr.text)
            else:
                codes[rr.status_code] = codes.get(rr.status_code, 0) + 1
            if future:
                break
        if listed:
            said = [f"{fetched} fetched"
                    + (" (" + ", ".join(f"{n} answered {c}" for c, n in sorted(codes.items())) + ")"
                       if codes else "")]
            said += [f"{n} not answered ({why})" for why, n in sorted(errors.items())]
            if refused:
                said.append(f"{refused} disallowed by robots.txt")
            detail = f"; {len(urls)} link page(s) of {listed}: " + ", ".join(said)
            # EVERY LINK PAGE DISALLOWED is robots.txt's answer, not the
            # feed's: nothing was read, and the adapter (which asks robots.txt
            # for each page too) would read nothing either. `refused`, with the
            # rule, never `fail`, which FEED_DOWN would report as a dead feed
            # (review of 2026-10-29). The listing itself was let through by
            # the robots.txt gate that runs before a verifier (the audit's,
            # or verify's _robots_gate).
            if urls and refused == len(urls):
                return False, Verdict(f"robots.txt disallows every link page sampled "
                                      f"({refused} of {listed}): {rules[0]}{detail}", "refused")
        else:
            detail = "; no link matches link_pattern"
    if not seen_events:
        quiet = mec_no_events(r.text)
        if quiet is not None:
            return False, Verdict(f"no schema.org Event blocks found; Modern Events Calendar "
                                  f"prints its no-events text ('{quiet[:40]}'){detail}", "empty")
        return False, "no schema.org Event blocks found" + detail
    if not future:
        return False, _future_note(seen_events, 0) + detail
    return True, f"{future}/{seen_events} sampled events future"


VERIFIERS = {"localist": verify_localist, "ics": verify_ics, "opendata": verify_opendata,
             "ods": verify_ods, "ckan": verify_ckan, "mobilizon": verify_mobilizon,
             "tribe": verify_tribe, "squarespace": verify_squarespace,
             "jsonld": verify_jsonld, "gancio": verify_gancio,
             "venuepilot": verify_venuepilot}


# --- discovery -------------------------------------------------------------
# Socrata publishes a federated CATALOG of every dataset on every Socrata
# domain, so event datasets can be FOUND rather than guessed at. 1,389 match
# "events" against the ten in opendata_sources.json, and coverage says the thin
# ground is real. This only proposes; cmd_verify still has to prove each one
# returns future events before cmd_merge will take it.
SOCRATA_CATALOG = "https://api.us.socrata.com/api/catalog/v1"
DISCOVER_QUERIES = [
    ("events", "community"), ("community events", "community"),
    ("festivals", "community"), ("special events", "community"),
    ("library events", "learning"), ("library programs", "learning"),
    ("parks events", "outdoors"), ("recreation programs", "outdoors"),
    ("farmers markets", "market"), ("volunteer", "volunteer"),
    ("art events", "arts"), ("youth programs", "kids"),
    # MOVEMENT. wegosie.com opens onto running/sports/fitness and neither
    # `fitness` nor `running` had a query here, so no amount of scheduled
    # curation could ever grow its supply — the one door whose categories the
    # ticketing APIs do not blanket either. Municipal portals publish these as
    # parks-and-rec class schedules and permitted road races.
    ("fitness classes", "fitness"), ("wellness programs", "fitness"),
    ("recreation classes", "fitness"),
    ("road races", "running"), ("races permits", "running"),
]
# Discovery must cover every category curation is responsible for, or a door can
# be committed and then quietly starve.
#
# This was written to be "checked at import so the failure is a loud one line
# into a run" and then never called from anywhere, which is how CKAN went five
# categories short without anyone seeing it. cmd_discover calls it now, for the
# backend it is about to sweep, and prints FLAG lines the workflow lifts into the
# job summary.
def _assert_queries_cover(cats, queries=None):
    return sorted(set(cats) - {c for _q, c in (queries or DISCOVER_QUERIES)})


def _configured_per_category():
    """How many sources each curated category already has. Used to spend the
    candidate budget on thin ground first — with a cursor in play the limit now
    actually binds, so the ORDER queries run in decides what gets curated."""
    counts = {}
    for _t, _n, _m, _country, cat in _coverage_rows():
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _filter_queries(queries, only):
    """Keep only the queries for `only`, or everything when `only` is empty.

    Returns the full list rather than nothing when the filter matches no query:
    a category with no query at all is a real gap, `_report_query_gaps` has
    already FLAGged it by the time this runs, and silently sweeping zero
    candidates would look identical to a run that found nothing.
    """
    if not only:
        return queries
    hit = [q for q in queries if q[1] in set(only)]
    if not hit:
        print(f"  FLAG no discovery query targets {', '.join(only)} — "
              f"sweeping everything instead")
        return queries
    return hit


def _order_queries(queries, cats):
    """Thinnest category first, then the original order within a category.

    Categories the live roster does NOT ask for sort last rather than being
    dropped: they are still worth sweeping with whatever budget is left over, and
    a door can be added back at any time."""
    counts = _configured_per_category()
    wanted = set(cats)
    order = sorted(
        enumerate(queries),
        key=lambda iq: (0 if iq[1][1] in wanted else 1,
                        counts.get(iq[1][1], 0),
                        iq[0]),
    )
    return [q for _i, q in order]
# Column-name inference. Datatypes in the catalog are unreliable — NYC ships a
# real date column typed 'Text' — so names propose and the live probe disposes.
_COL_RX = {
    "start": r"^(event_?)?(start|begin|from)?_?date(_?time)?$|^start|^event_date|^date_?time$|^begin",
    "end": r"^(event_?)?end_?date|^end_?time$|^to_date|^thru",
    "title": r"^(event_?)?(name|title)|^name_of_event|^program_?name|^title$",
    "venue": r"venue|facility|park_?name|site_?name|location_?name|place|building",
    "address": r"address|street",
    "description": r"descript|details|summary|abstract",
    "url": r"url|link|website|more_?info",
    "id": r"^id$|_id$|^uid$|permit|^event_?number|^objectid$",
    "start_time": r"^start_?time$|^begin_?time$|^time_?start",
    "end_time": r"^end_?time$|^time_?end",
}
_POINT_TYPES = {"point", "location", "multipoint"}
# Datasets that MATCH an event search but are not an event listing. A permit
# table's "title" is the applicant — the Cambridge parking feed proposed
# "Harvard University Law School move-in" as a community event — and a dataset
# stamped with a past year is an archive whatever its rows say.
#
_DISCOVER_REJECT = re.compile(
    r"permit|parking|licen[cs]e|application|violation|citation|inspection|"
    r"archive|historical", re.I)

_YEAR_RX = re.compile(r"\b((?:19|20)\d{2})\b")


def _looks_archived(name, today=None):
    """Is this dataset's NAME stamped with a year that has already gone?

    The year test used to live inside _DISCOVER_REJECT as the literal
    `(19|20)([01]\\d|2[0-4])`, which had two faults. It rotted — the ceiling was
    written down, so once the calendar passed 2024 a dataset called "Events 2025"
    read as current, and a filter that silently loosens every January is worse
    than none because the rejects it stops making are invisible. And a bare
    alternation cannot read a RANGE: "2025-2026 Program Schedule" is a live
    season, and matching \\b2025\\b inside it threw the season away.

    So: collect every year in the name and let the LATEST one decide. No years at
    all means no opinion, which is the common case and must stay cheap.
    """
    years = [int(y) for y in _YEAR_RX.findall(name or "")]
    return bool(years) and max(years) < _as_date(today or _today_int()).year


def _pick(fields, pattern):
    rx = re.compile(pattern, re.I)
    for f in fields:
        if rx.search(f):
            return f
    return None


def _infer_map(fields, datatypes, allow_geocode=False):
    """A candidate `map` from column names, or None when the shape can't work.

    allow_geocode relaxes the coordinate requirement for portals whose COUNTRY
    is known: a ", Ireland" suffix cannot relocate a feed the way a guessed city
    would, it only makes the Photon lookup less precise.
    """
    m = {}
    for key, rx in _COL_RX.items():
        col = _pick(fields, rx)
        if col:
            m[key] = col
    # A location the dataset carries ITSELF. Guessing a geocode_suffix from a
    # domain would silently place a whole feed in the wrong city, so a dataset
    # without coordinates is left for a human instead.
    geo = next((f for f, t in zip(fields, datatypes)
                if str(t).lower() in _POINT_TYPES), None)
    if geo:
        m["geo"] = geo
    else:
        lat = _pick(fields, r"^lat(itude)?$")
        lon = _pick(fields, r"^(lon|lng|long(itude)?)$")
        if lat and lon:
            m["lat"], m["lon"] = lat, lon
        elif not (allow_geocode and (m.get("venue") or m.get("address"))):
            return None
    if not (m.get("title") and m.get("start")):
        return None
    return m


# CKAN portals, with the country their data is in. CKAN is what the countries
# coverage kept reporting at zero actually publish through — Socrata is a US
# product and OpenDataSoft is largely France and Belgium.
CKAN_PORTALS = [
    ("data.gov.uk", "United Kingdom"), ("open.canada.ca", "Canada"),
    ("data.gov.ie", "Ireland"), ("www.avoindata.fi/data", "Finland"),
    ("dados.gov.pt", "Portugal"), ("data.overheid.nl/data", "Netherlands"),
    ("www.govdata.de/ckan", "Germany"), ("data.gov.au/data", "Australia"),
    ("opendata.swiss", "Switzerland"), ("www.dati.gov.it/opendata", "Italy"),
    ("admin.opendata.dk", "Denmark"),
    # Added 2026-08-22 after probing /api/3/action/package_search on each: these
    # three answered 200 with success:true. Slovenia and Greece were both on
    # coverage's thin-ground list. Probed and REJECTED at the same time, so the
    # next person does not repeat it: data.gov.cz 404 (DCAT/SPARQL, not CKAN),
    # datos.gob.es 403, data.norge.no 404, avaandmed.eesti.ee 404, and
    # dane.gov.pl / data.gov.sk answer non-JSON. data.gov.se, opendata.gov.hk
    # and data.gov.ro could not be reached from the probing host at all, which
    # is a fact about that host and not about them — re-probe from CI before
    # concluding anything (see catalog_probe.py).
    ("podatki.gov.si", "Slovenia"), ("data.gov.lv", "Latvia"),
    ("data.gov.gr", "Greece"),
]
# WHAT A CKAN SWEEP ACTUALLY COSTS AND RETURNS, measured 2026-08-22 across all
# 14 portals: 4,781 datasets examined, 4,332 skipped `no_datastore`, 411
# `not_events`, and ZERO candidates. Sampling the resources behind them says why
# — 1,634 of 1,653 are plain FILES and only 19 are datastore_active, and the
# formats are 1,335 XLSX against 42 CSV. These portals publish statistical
# downloads whose titles mention events (registers, attendance counts, funding
# lines), not live event calendars, and mapsee_ingest_ckan can only read the
# DataStore API. Teaching it to read CSV would not rescue much either: the bulk
# is spreadsheets, and a spreadsheet of last year's festival attendance is not a
# feed. Keep the backend — the 19 are real and the cursor is cheap — but do not
# expect it to carry a country, and do not spend a curation run here hoping.
#
# The other known limitation, unaddressed: CKAN_QUERIES is ENGLISH ONLY, so a
# Greek or Slovenian portal is searched for "events" and "festival" rather than
# for what its datasets are actually called. Same shape as _SECONDARY_RX asking
# only for English and missing Flohmarkt, brocante and vide-grenier.
#
# CKAN is the ONLY discovery path outside the United States — Socrata is a US
# product — so a category missing from this list is a category no non-US door can
# ever grow supply for. It covered four of the nine the roster asks for, which
# meant arts, fitness, kids, running and volunteer were curated in the US and
# nowhere else. _assert_queries_cover now checks both lists rather than one.
CKAN_QUERIES = [("events", "community"), ("festival", "community"),
                ("library events", "learning"), ("markets", "market"),
                ("parks events", "outdoors"), ("sport facilities", "fitness"),
                ("leisure centres", "fitness"), ("parkrun", "running"),
                ("running events", "running"), ("arts culture", "arts"),
                ("museums galleries", "arts"), ("youth services", "kids"),
                ("playgrounds activities", "kids"), ("volunteering", "volunteer"),
                ("community groups", "volunteer")]


def _ckan_text(value):
    """A CKAN title is a string on most portals and a {lang: text} map on the
    multilingual ones (opendata.swiss ships de/fr/it/en)."""
    if isinstance(value, dict):
        for lang in ("en", "de", "fr", "it", "nl", "da", "fi", "pt"):
            if value.get(lang):
                return str(value[lang])
        return next((str(v) for v in value.values() if v), "")
    return str(value or "")


def _discover_ckan(session, seen_keys, led, limit, cursor, queries,
                   per_query=20, probe_cap=6):
    """package_search each portal, then read the DataStore field list of every
    active resource to infer a map. The field list needs its own request per
    resource, so probe_cap bounds how many a single dataset can cost.

    Paged the same way Socrata is, with the cursor keyed per portal+query — the
    portals are independent catalogs and a shared offset would skip most of the
    small ones while barely moving through data.gov.uk."""
    found, skipped = {}, {"configured": 0, "ledger": 0, "no_shape": 0,
                          "not_events": 0, "no_datastore": 0}
    for domain, country in CKAN_PORTALS:
        base = f"https://{domain}/api/3/action"
        hits = 0
        for q, category in queries:
            ckey = f"{domain}|{q}"
            start = int(cursor.get(ckey, 0))
            try:
                r = session.get(f"{base}/package_search", timeout=45,
                                params={"q": q, "rows": per_query, "start": start})
                r.raise_for_status()
                result = (r.json() or {}).get("result") or {}
                pkgs = result.get("results") or []
                total = int(result.get("count") or 0)
            except Exception as exc:  # noqa: BLE001
                print(f"  {domain:26} {q:14} unreachable ({str(exc)[:40]})")
                break
            nxt = start + per_query
            cursor[ckey] = 0 if (total and nxt >= total) else nxt
            for pkg in pkgs:
                title = _ckan_text(pkg.get("title")) or _ckan_text(pkg.get("name"))
                if _DISCOVER_REJECT.search(title) or _looks_archived(title):
                    skipped["not_events"] += 1
                    continue
                probed = 0
                for res in (pkg.get("resources") or []):
                    if probed >= probe_cap:
                        break
                    if not res.get("datastore_active") or not res.get("id"):
                        skipped["no_datastore"] += 1
                        continue
                    probed += 1
                    url = f"{base}/datastore_search?resource_id={res['id']}"
                    key = _canon(url)
                    if key in found:
                        continue
                    if key in seen_keys:
                        skipped["configured"] += 1
                        continue
                    if _dead_recently(led, key):
                        skipped["ledger"] += 1
                        continue
                    try:
                        fr = session.get(f"{base}/datastore_search", timeout=30,
                                         params={"resource_id": res["id"], "limit": 0})
                        flds = (((fr.json() or {}).get("result") or {}).get("fields")) or []
                    except Exception:  # noqa: BLE001
                        continue
                    names = [f.get("id") for f in flds if f.get("id")]
                    types = [f.get("type") for f in flds]
                    m = _infer_map(names, types, allow_geocode=True)
                    if not m:
                        skipped["no_shape"] += 1
                        continue
                    entry = {
                        "type": "ckan",
                        "name": f"{title} ({country})"[:90],
                        "url": url,
                        "category": category,
                        "limit": 2000,
                        "map": m,
                    }
                    if not (m.get("geo") or m.get("lat")):
                        entry["geocode_venue"] = True
                        entry["geocode_suffix"] = f", {country}"
                    found[key] = entry
                    hits += 1
                    if len(found) >= limit:
                        return found, skipped
        print(f"  {domain:26} -> {hits} candidate(s)")
    return found, skipped


# How many catalog rows one query may read in a single run. The old code read
# `per_query` rows and stopped forever; this reads that many STARTING AT the
# cursor, so the same budget walks new ground every week.
PAGE = 100


def _report_query_gaps(cats, queries, label):
    missing = _assert_queries_cover(cats, queries)
    if missing:
        print(f"  FLAG {label} discovery has no query for: {', '.join(missing)}"
              f" - a door on those categories cannot grow supply here")
    return missing


def _discover_socrata(session, seen_keys, led, limit, cursor, queries):
    found = {}
    skipped = {"configured": 0, "no_shape": 0, "ledger": 0, "not_events": 0}
    for q, category in queries:
        start = int(cursor.get(q, 0))
        try:
            r = session.get(SOCRATA_CATALOG, timeout=45,
                            params={"q": q, "only": "dataset",
                                    "limit": PAGE, "offset": start})
            r.raise_for_status()
            body = r.json()
            results = body.get("results", [])
            total = int(body.get("resultSetSize") or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"  ({q}: {exc})")
            continue
        # Advance, and WRAP. A cursor that ran off the end would park there and
        # the query would return nothing for ever after, which is the convergence
        # this whole mechanism exists to break. Wrapping also re-reads page 0
        # eventually, where newly published datasets land.
        nxt = start + PAGE
        cursor[q] = 0 if (total and nxt >= total) else nxt
        for item in results:
            res, meta = item.get("resource", {}), item.get("metadata", {})
            domain, ds_id = meta.get("domain"), res.get("id")
            if not (domain and ds_id):
                continue
            url = f"https://{domain}/resource/{ds_id}.json"
            key = _canon(url)
            if key in found:
                continue
            if key in seen_keys:
                skipped["configured"] += 1
                continue
            if _dead_recently(led, key):
                skipped["ledger"] += 1
                continue
            nm = res.get("name") or ""
            if _DISCOVER_REJECT.search(nm) or _looks_archived(nm):
                skipped["not_events"] += 1
                continue
            m = _infer_map(res.get("columns_field_name") or [],
                           res.get("columns_datatype") or [])
            if not m:
                skipped["no_shape"] += 1
                continue
            found[key] = {
                "type": "opendata",
                "name": f"{res.get('name')} ({domain})"[:90],
                "url": url,
                "app_token": None,
                "category": category,
                "where": f"{m['start']} > '{{now}}'",
                "order": m["start"],
                "limit": 1000,
                "map": m,
            }
        span = f"{start}-{start + len(results)}" + (f"/{total}" if total else "")
        print(f"  {q:22} [{span:>12}] -> {len(found)} candidate(s) so far")
        if len(found) >= limit:
            break
    return found, skipped


# --- mobilizon: a directory that grows on its own --------------------------
# The other two backends search CATALOGS OF DATASETS, and the US open-data well
# is close to dry: a full 17-query Socrata sweep now yields ~0 genuinely new
# candidates, because 19 are configured, ~30 are known-dead and ~840 of ~1,370
# carry no location this tool is willing to guess at.
#
# Mobilizon is a different shape of supply. It is federated software, so the
# population of instances GROWS without anyone publishing a dataset, and
# joinmobilizon.org keeps a keyless directory of them. mobilizon_sources.json's
# own header has pointed at that endpoint since the adapter was written; nothing
# ever read it, so the catalog sat at 3 instances out of 95 live ones.
#
# This is the one backend where "continually discovers new sources" is literally
# true: next quarter's directory contains instances that do not exist today.
BACKENDS = ("socrata", "ckan", "mobilizon", "osm", "civic")

# How many metros one `discover osm` run walks. Each is an Overpass call plus a
# site probe per venue with a website (~600 in a big city, most of them one
# request), so this is the knob that decides what a scheduled run costs. The
# cursor advances by exactly this many, so the globe is walked through rather
# than glanced at — the same reason the Socrata cursor exists.
OSM_METROS_PER_RUN = 3

# HOW MANY VENUES ONE `discover osm` RUN PROBES AT ONCE. The walk is time-bound,
# not request-bound: over 13 community walks (2026-09-22..10-03) a 55-minute
# run made 950-1,450 live probes at 2.3-3.5 s each, almost all of it waiting on
# somebody else's server, and read 3-7 metros a run. On 2026-10-04 the cursor
# sat at Hannover with Washington DC 57 metros on and Seattle 63: 8-19 runs.
# Most venues are a different site, but not all: in Winnipeg 19 of the 30
# unsettled venues are branch pages on one library site, in Halifax 11 of 25.
# So _discover_osm never runs two probes of one host together, and _HostGate
# releases the requests one host sees, redirects and follow-ups included,
# OSM_HOST_GAP_S apart across all the workers. That gate is a COST as well as
# a courtesy: about a second per same-host follow-up, which the one-at-a-time
# code never paid. Measured live 2026-10-05 against the code this replaced
# (git 1ea55aa, one at a time, ungated), same venue lists, scratch ledger, every
# request inside robots.txt: Halifax 46.7 s -> 28.1-29.0 s with four workers
# (1.6x; one worker of this code took 67.3 s, the gate and robots.txt reads
# included), Hamilton 72.2 s -> 32.0 s (2.3x on one sample each; a review run
# a day earlier implied about 53 s for the old code there, which is 1.7x). Both
# found the same candidates with fewer requests: 58 -> 48 and 80 -> 61, because
# robots.txt refuses some (12 in Halifax) and the run's cache answers repeats
# (11 and 19), at the price of one robots.txt read per origin. What sets
# the gain is how much of a metro sits on one host: that host's chain is the
# critical path, and the other workers are idle while it drains.
OSM_PROBE_WORKERS = 4
OSM_HOST_GAP_S = 1.0

# AN UNREACHABLE VENUE IS PARKED FOR A WEEK, NOT THE 90 DAYS A DEAD END GETS.
# It is how the site answered one request, not a fact about the site, which is
# why it was never written down — but a metro the deadline cuts short is read
# again the next day, and it used to pay for every unreachable venue again:
# Mexico City was walked on 09-24 and 09-25, and on 09-25 155 of the 163 sites
# probed in three Mexican metros were unreachable at ~23 s each (62 minutes).
# Its own status, so _dead_recently (fail/empty/refused, which `verify` also
# reads) means what it did and a verify of the same URL is never skipped.
UNREACHABLE_TTL_DAYS = 7

# A metro where at least this many probes raised, and more than half of them,
# is not read; see where it is used.
OSM_PROBE_ERRORS_STOP = 3


class _HostGate:
    """No two requests to one host are RELEASED less than `gap` seconds apart
    (or the host's Crawl-delay, when that is longer), across every worker that
    holds this gate. Paced on the last request actually let go, not on a
    reserved slot: with slots, a worker that woke late from its sleep was
    followed by the next on-time slot less than `gap` later (0.974-0.999 s
    measured). A waiting worker never holds the lock while it sleeps."""

    def __init__(self, gap=OSM_HOST_GAP_S, clock=time.monotonic, sleep=time.sleep):
        import threading
        self.gap, self._clock, self._sleep = float(gap), clock, sleep
        self._lock = threading.Lock()
        self._last, self._slow = {}, {}
        self.waits, self.waited = 0, 0.0

    def slow(self, url, delay):
        """A robots.txt Crawl-delay: this host's gap is at least `delay`."""
        host = _probe_host(url)
        with self._lock:
            self._slow[host] = max(self._slow.get(host, 0.0), float(delay))

    def wait(self, url):
        host = _probe_host(url)
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                last = self._last.get(host)
                pause = 0.0 if last is None else (
                    last + max(self.gap, self._slow.get(host, 0.0)) - now)
                if pause <= 0:
                    self._last[host] = now
                    if waited:
                        self.waits += 1
                        self.waited += waited
                    return
            self._sleep(pause)
            waited += pause


def _probe_host(url):
    """The host a probe is serialised on: `www.` and case do not make a second
    site, and nor does the scheme (http:// redirects to https:// on the host)."""
    from urllib.parse import urlparse
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        h = ""
    return h[4:] if h.startswith("www.") else h


# ROBOTS.TXT BINDS THE WALK TOO. Until 2026-10-05 discovery fetched whatever a
# venue linked and only `verify` read robots.txt; a review of the pinned walk
# on Halifax found 10 of its 11 library-branch probes fetching
# halifaxpubliclibraries.ca/explore/?post-type=events..., which that host's
# robots.txt disallows (`Disallow: /explore/`), and a hop to facebook.com,
# which disallows everything. Every request a probe makes, redirect hops
# included, now asks first; the refusal is raised BEFORE anything is sent, so
# find_calendar's own handling decides what it costs: a refused follow-up page
# is skipped, a refused homepage (or a homepage that redirects somewhere
# refused) is parked `refused` for DEAD_TTL like a verify's, an unreachable
# robots.txt is `unreachable` (a week), and a challenge on it is a challenge.
class _RobotsRefusal(requests.exceptions.RequestException):
    """robots.txt said no to this request, so it was never sent."""


class RobotsDisallowed(_RobotsRefusal):
    """A Disallow rule in the host's own robots.txt matched."""


class RobotsUnreachable(_RobotsRefusal):
    """robots.txt answered 5xx or not at all: RFC 9309 says assume Disallow: /."""


class RobotsChallenged(_RobotsRefusal):
    """A bot challenge served on /robots.txt: permission cannot be established."""


class RobotsCrawlDelay(_RobotsRefusal):
    """A Crawl-delay longer than OSM_MAX_CRAWL_DELAY_S: the page is not asked,
    rather than asked faster than the file says, or slept on past the deadline."""


# The walk waits out a Crawl-delay up to this long per request. Beyond it the
# request is skipped, never sent sooner: a worker asleep for a 3,600 s delay
# would carry the run past its deadline into the job's timeout-minutes.
OSM_MAX_CRAWL_DELAY_S = 30.0


class _WalkRobots:
    """One robots.txt per origin for the whole walk, read once, by whichever
    worker asks first and through that worker's gated session, while any other
    worker asking about the same origin waits for the answer."""

    def __init__(self, timeout):
        import threading
        self.timeout = timeout
        self._lock = threading.Lock()
        self._origins = {}              # origin -> [Lock, robots_txt.Robots | None]
        self.fetched = 0

    def check(self, session, url):
        import threading
        origin = robots_txt.origin_of(url)
        with self._lock:
            slot = self._origins.setdefault(origin, [threading.Lock(), None])
        with slot[0]:
            if slot[1] is None:
                rb = robots_txt.Robots(session, timeout=self.timeout)
                session._fetching_robots = True
                try:
                    ans = rb.check(url)             # reads the file, once
                finally:
                    session._fetching_robots = False
                slot[1] = rb
                with self._lock:
                    self.fetched += 1
                return ans
            return slot[1].check(url)


# A LIBRARY SYSTEM'S BRANCHES LINK THE SAME PAGES, and the walk read them once
# per branch: in the review runs of 2026-10-04, 21 of Halifax's 70 requests,
# 19 of Hamilton's 80 and 35 of Winnipeg's 95 re-fetched a URL already fetched
# that run (one Winnipeg page 18 times). Those repeats sit on the one shared
# host that is each metro's critical path, a second of gate apiece. So a GET
# answered once is answered from memory for the rest of the run, keyed by the
# URL asked and by every redirect hop on the way, and a worker asking for a
# URL another is mid-fetch on waits for that answer rather than asking twice.
# Measured live 2026-10-05: repeats were 11 of 58 requests in Halifax and 19 of
# 80 in Hamilton under the old code; 1 and 0 under this one (the 1 is a
# robots.txt that redirects to the homepage, which robots.txt reads never
# take from the cache).
# Errors are not kept. Capped in bytes, least recently used out first.
OSM_CACHE_BYTES = 48_000_000
OSM_CACHE_MAX_BODY = 4_000_000


class _ResponseCache:
    def __init__(self, max_bytes=OSM_CACHE_BYTES, max_body=OSM_CACHE_MAX_BODY):
        import collections
        import threading
        self.max_bytes, self.max_body = max_bytes, max_body
        self._lock = threading.Lock()
        self._keys = {}
        self._data = collections.OrderedDict()
        self._bytes = 0
        self.hits = 0

    def fetch(self, key, do):
        import copy
        import threading
        with self._lock:
            lk = self._keys.setdefault(key, threading.Lock())
        with lk:
            with self._lock:
                got, r = key, self._data.get(key)
                if r is None:
                    # A page asked directly and the same page reached as a
                    # redirect hop are one answer, unless it was a redirect.
                    got = (key[0], not key[1])
                    r = self._data.get(got)
                    if r is not None and (r.history or r.is_redirect):
                        r = None
                if r is not None:
                    self._data.move_to_end(got)
                    self.hits += 1
                    return copy.copy(r)          # Session.send sets .history on it
            r = do()                             # raises: nothing is kept
            size = len(r.content or b"")
            if size <= self.max_body:
                kept = copy.copy(r)
                with self._lock:
                    self._data[key] = kept
                    self._bytes += size
                    while self._bytes > self.max_bytes and len(self._data) > 1:
                        _k, old = self._data.popitem(last=False)
                        self._bytes -= len(old.content or b"")
            return r


class _PacedSession(requests.Session):
    """A worker's own Session (requests does not promise one survives being
    shared across threads). Every request asks the run's robots.txt first,
    then the run's cache, then waits on the shared gate. `send` is where each
    redirect hop goes too, so a hop to another host is asked of THAT host's
    robots.txt and paced on that host's clock, not the one it left."""

    gate = None
    robots = None
    cache = None

    def __init__(self):
        super().__init__()
        self.refusals = []               # (url, why) a probe was refused; reset per probe
        self._fetching_robots = False

    def send(self, request, **kw):
        if self._fetching_robots:        # robots.txt itself: paced, never asked of itself
            if self.gate is not None:
                self.gate.wait(request.url)
            return super().send(request, **kw)
        if (self.cache is not None and request.method == "GET"
                and not kw.get("stream")):
            key = (request.url, bool(kw.get("allow_redirects", True)))
            return self.cache.fetch(key, lambda: self._send(request, **kw))
        return self._send(request, **kw)

    def _send(self, request, **kw):
        url = request.url
        if self.robots is not None:
            ans = self.robots.check(self, url)
            if ans["allowed"] is not True:
                exc = (RobotsUnreachable if ans["status"] == "unreachable" else
                       RobotsChallenged if ans["allowed"] is None else RobotsDisallowed)
                why = f"{ans.get('rule') or ans['status']} ({ans.get('robots')})"
                self.refusals.append((url, exc.__name__, why))
                raise exc(f"robots.txt refuses {url}: {why}")
            delay = ans.get("crawl_delay")
            if delay and self.gate is not None:
                if delay > OSM_MAX_CRAWL_DELAY_S:
                    why = f"Crawl-delay: {delay:g} ({ans.get('robots')})"
                    self.refusals.append((url, "RobotsCrawlDelay", why))
                    raise RobotsCrawlDelay(f"robots.txt asks {delay:g} s between requests")
                self.gate.slow(url, delay)
        if self.gate is not None:
            self.gate.wait(url)
        return super().send(request, **kw)


# What robots.txt cost a probe, by how find_calendar reports it: a refused
# HOMEPAGE (or a homepage that redirects somewhere refused) comes back
# `unreachable` with the exception's name as its note.
_ROBOTS_PROBE_STATUS = {"RobotsDisallowed": "robots-disallowed",
                        "RobotsChallenged": "bot-challenge",
                        "RobotsCrawlDelay": "robots-crawl-delay"}


def _robots_probe_session(headers):
    """A walk's own session: robots.txt asked before every request and every
    redirect hop, one host at a time, each page read once a run."""
    import catalog_discover_osm as osm
    s = _PacedSession()
    s.headers.update(dict(headers or {}))
    s.gate = _HostGate(OSM_HOST_GAP_S)
    s.robots = _WalkRobots(timeout=(osm.PROBE_CONNECT_S, 15))
    s.cache = _ResponseCache()
    return s


def _unreachable_recently(led, key, ttl=UNREACHABLE_TTL_DAYS):
    rec = led.get(key)
    return (bool(rec) and rec.get("status") == "unreachable"
            and _days_since(rec.get("checked", 0)) < ttl)


# A HOMEPAGE THE WALK HAS JUST ASKED IS NOT ASKED AGAIN THIS WEEK, whatever it
# answered. A metro the deadline cuts short is resumed the next run, and 28%
# of the daily runs' probes (2,002 of 7,044 in runs 118, 121 and 123) left no
# ledger row, so the resume paid for them again: Berlin's second visit (run
# 121) spent about 500 of its 2,443 probes re-asking run 118's homes, and its
# `configured` tally went from 46 to 158 (measured 2026-10-23). `found` (a
# candidate came of it) and `probed` (a 4xx, a Crawl-delay past the cap, or a
# find no candidate could be made of) are osm-venue rows only, so verify,
# coverage and the health check, which read ok/fail/empty, never see them.
def _probed_recently(led, key, ttl=UNREACHABLE_TTL_DAYS):
    rec = led.get(key)
    return (bool(rec) and rec.get("type") == "osm-venue"
            and rec.get("status") in ("found", "probed")
            and _days_since(rec.get("checked", 0)) < ttl)


# How many cities one `discover civic` run walks. A CivicPlus city costs one
# fetch for the homepage, one for /iCalendar.aspx and one PER SURVIVING CATEGORY
# to prove it — Gloucester publishes 60 — so this is the knob that decides what a
# scheduled run costs, exactly as OSM_METROS_PER_RUN is for the other one.
# Measured on the first sweep: about 7 seconds and 12 requests a city.
CIVIC_CITIES_PER_RUN = 40


# THE CIVIC WALK IS A ROTATION NOW, NOT A COUNTRY.
#
# `catalog_discover_civic` was built to work "the same way in every country that
# has the class" and then shipped with one country in CITY_CLASSES and a cursor
# that read `{"country": "US", "offset": 2140}` — so every civic run since has
# walked further down the same list of US cities, and the one backend that can
# find a whole TOWN's calendar anywhere on earth has never left one. It is also
# the RICHEST backend this repo has: two 40-city batches returned 64 verified
# sources carrying 44 local-music events, 57 festivals and parades, 40
# block-party-shaped events and 58 market days (`docs/agents/curation-and-
# discovery.md`), against a Socrata sweep that measured ZERO event-shaped
# datasets for "block party", "street fair" and "community festivals".
#
# So the country is chosen per run, thinnest catalog first — the same rule
# osm.metros() sorts by, for the same reason. The cursor NAMES the country it
# just swept rather than counting to one, again like the metro walk: the order
# is computed from live counts and shifts as the catalog grows, and a position
# would silently re-aim at a country that moved under it.
def _civic_offsets(cursor):
    """The per-country offsets, migrating the single-country cursor in place.

    The old shape is `{"country": "US", "offset": 2140}` and 2,140 US cities of
    walking is not something to throw away, so it is read as US's offset the
    first time and the flat key is dropped."""
    offsets = cursor.get("offsets")
    if offsets is None:
        offsets = {}
        if cursor.get("offset"):
            offsets[(cursor.get("country") or "US").upper()] = int(cursor["offset"])
        cursor["offsets"] = offsets
    cursor.pop("offset", None)
    return offsets


def _civic_country_order(civic):
    """Every country with a city class, thinnest catalog first.

    A country with no sources at all sorts FIRST, which is the whole point: it
    is the ground a door opens onto and finds nothing. Ties keep CITY_CLASSES'
    own order so the walk is deterministic on a fresh catalog."""
    counts = {}
    for _t, _n, _m, country, _cat in _coverage_rows():
        counts[country] = counts.get(country, 0) + 1
    ranked = sorted(
        enumerate(civic.CITY_CLASSES),
        key=lambda ic: (counts.get(_ISO_COUNTRY.get(ic[1], ic[1]), 0), ic[0]),
    )
    return [code for _i, code in ranked]


def _civic_next_country(cursor, civic):
    """The country this run sweeps: the one AFTER the last one swept.

    Named rather than counted, so editing CITY_CLASSES does not re-aim the walk
    at a country that happened to slide into the old position. A cursor naming a
    country that has since been removed restarts at the thinnest.
    """
    order = _civic_country_order(civic)
    if not order:
        return "US"
    prev = (cursor.get("country") or "").upper()
    start = (order.index(prev) + 1) % len(order) if prev in order else 0
    # A country NOBODY HAS SWEPT YET jumps the queue, once. Otherwise a small
    # country that has already been read to the end spends the run re-reading
    # its own top — the ledger's 90-day TTL makes that nearly free and nearly
    # pointless — while a country with no sources at all waits another rotation.
    # `visited` and not "offset is 0", because wrapping resets the offset to 0
    # and a wrapped country would look new for ever. Once everything has been
    # visited once this is plain round-robin, which is the steady state.
    _civic_offsets(cursor)                      # migrate the old cursor shape
    visited = cursor.setdefault("visited", [])
    ring = [order[(start + i) % len(order)] for i in range(len(order))]
    unswept = [c for c in ring if c not in visited]
    country = unswept[0] if unswept else ring[0]
    if country not in visited:
        visited.append(country)
    print(f"  civic rotation: {country} "
          f"(thinnest first: {', '.join(order[:6])}{'...' if len(order) > 6 else ''})")
    return country


# THE US TOPS UP A TURN THAT ENDS EARLY, and the rotation does not change. The
# US sorts last in the thinnest-first order, so it gets one turn in ~30, and it
# is where the yield is. Measured 2026-10-23: the non-US turns of runs 114-123
# (MX, NO, FI, JP, AT) used 2.6-7.1 of their 10 minutes and verified 0 from 344
# cities; the one US turn (run 108) read 55 cities in the 10 minutes and
# verified 31. So the minutes a turn leaves go to the US, from the US's own
# offset. The cursor still NAMES the country whose TURN it was: a cursor naming
# the US would restart the ring at the thinnest country every run.
#
# One batch and no more: 3-7 minutes at about 11 s a US city (run 108) is 16-40
# cities, inside one batch of CIVIC_CITIES_PER_RUN. And only with a deadline,
# since without one there is no "time left" to spend; CIVIC_TOPUP_MIN_S keeps it
# from asking Wikidata for a batch it has no time to read.
#
# AND ITS WIKIDATA ASK IS BOUNDED BY THE TIME IT HAS. A top-up can start with
# 60 s left and cities() waits up to 200 s a request, so an unbounded ask could
# run the 10-minute civic backstop ~8.7 min over, out of a daily job measured
# at 69.6-73.7 of its 90 minutes (2026-10-22, 10-23). The US answers in 1.9 s,
# so a bound of the time left less 30 s (20 at least) cuts no working answer.
CIVIC_TOPUP_COUNTRY = "US"
CIVIC_TOPUP_MIN_S = 60


def _discover_civic(session, seen_keys, led, limit, cursor, cities_per_run=None,
                    deadline=0.0):
    """Propose whole-town calendars. See catalog_discover_civic.py.

    THE CURSOR IS AN OFFSET INTO A STABLE ORDER, PER COUNTRY, which is the one
    thing that makes this walkable. Wikidata is asked for cities BY POPULATION
    DESCENDING, so position 400 means the same city next week as it does today
    unless a census lands — unlike the OSM backend, whose list is a hand-edited
    file and whose cursor therefore has to name a metro rather than count to
    one. The order also means the sweep spends its early runs where the people
    are.

    WHICH country is a rotation, not a setting: see _civic_next_country. The
    offsets are per country and the cursor NAMES the one it just swept, so
    adding a country to CITY_CLASSES never re-aims a walk already in progress.
    A turn that ends before its deadline is topped up from the US's own offset:
    see CIVIC_TOPUP_COUNTRY.

    It walks past the end rather than stopping: `discover civic` on a wrapped
    cursor starts again at the top, where the ledger's 90-day TTL means the
    biggest cities get re-probed roughly quarterly. A city that installed a
    calendar since we last looked is the whole reason to come back.
    """
    import catalog_discover_civic as civic

    country = _civic_next_country(cursor, civic)
    per_run = int(cities_per_run or CIVIC_CITIES_PER_RUN)
    found, skipped = {}, {}

    # ROBOTS.TXT BINDS THIS WALK TOO, as it does the venue walk (see
    # _WalkRobots). Until 2026-10-05 a city's pages were fetched with the plain
    # session and nothing asked first. Wikidata is a documented API and keeps
    # that session; every page of a city's or a board's own site goes through
    # this one.
    probe_s = _robots_probe_session(getattr(session, "headers", None))

    _civic_batch(civic, session, probe_s, cursor, country, per_run, deadline,
                 limit, seen_keys, led, found, skipped)
    # THE ROTATION ADVANCES WHETHER OR NOT THE BATCH WAS READ. The cursor's
    # `country` is "whose turn has been taken", not "whose batch was read":
    # leaving it behind means the next run picks the same country, and a
    # country WDQS is currently refusing — it answered 502/504 for hours on
    # 2026-09-19 — would hold every civic run in the repo for as long as it
    # kept refusing. Its offset is untouched, so nothing is skipped; it simply
    # comes round again.
    cursor["country"] = country

    top = CIVIC_TOPUP_COUNTRY
    left = (deadline - time.time()) if deadline else 0.0
    if (country != top and top in civic.CITY_CLASSES
            and left >= CIVIC_TOPUP_MIN_S and len(found) < limit):
        print(f"  {country}'s turn left {left / 60:.1f} min: {top} tops it up "
              f"from its own offset")
        _civic_batch(civic, session, probe_s, cursor, top, per_run, deadline,
                     limit, seen_keys, led, found, skipped,
                     wd_timeout=min(200, max(20, int(left) - 30)))
    _save_ledger(led)
    return found, skipped


def _civic_batch(civic, session, probe_s, cursor, country, per_run, deadline,
                 limit, seen_keys, led, found, skipped, wd_timeout=None):
    """Walk one batch of `country`'s cities from its own offset, into `found`.

    Moves that country's offset and nothing else in the cursor: whose TURN it
    was is the caller's to record, because a top-up walks a country whose turn
    it is not. `wd_timeout` bounds the Wikidata ask (a top-up's; a turn keeps
    cities()'s own 200 s, which Canada's 117 s answer needs)."""
    offset = int(_civic_offsets(cursor).get(country) or 0)

    def bump(k):
        skipped[k] = skipped.get(k, 0) + 1

    try:
        places, rows = civic.cities(session, country, limit=per_run, offset=offset,
                                    **({"timeout": wd_timeout} if wd_timeout else {}))
    except Exception as exc:                                      # noqa: BLE001
        # UNREAD, NOT EMPTY. Wikidata answers 504 on a query it decides is too
        # expensive, and advancing the OFFSET past a batch we never saw would
        # skip those cities until the walk wraps.
        print(f"  wikidata {country} offset {offset}: {type(exc).__name__} — "
              f"batch UNREAD, offset stays")
        return
    if not places:
        print(f"  {country}: offset {offset} is past the end — wrapping to the top")
        _civic_offsets(cursor)[country] = 0
        return
    print(f"  {country}: {len(places)} cities from offset {offset} "
          f"(pop {places[0]['population']} -> {places[-1]['population']})")

    # The DMO stream, batched before the walk: one request per fifty cities, so
    # asking is cheaper than deciding whether to ask. Not past the deadline,
    # where the walk below reads no city, and never waiting longer than the
    # time left (its own timeout is 45 s).
    left = (deadline - time.time()) if deadline else None
    try:
        noms = ({} if left is not None and left <= 0 else civic.dmo_nominations(
            session, places, **({"timeout": max(5, min(45, int(left)))} if left is not None else {})))
    except Exception:                                             # noqa: BLE001
        noms = {}
    if noms:
        print(f"  {len(noms)} of them name a tourism board on Wikipedia")

    declined = _declined_keys()
    read = 0
    for place in places:
        if deadline and time.time() >= deadline:
            print(f"  out of time after {read} cities — the rest are UNREAD and "
                  f"the cursor stays before them")
            break
        if len(found) >= limit:
            break
        # A city and its board are two sites, probed the same way. The city's own
        # is first because it is the one Wikidata actually promised us.
        targets = [(place, place["url"], "city")]
        targets += [(place, home, "dmo") for home in noms.get(place["qid"], [])]
        for who, home, kind in targets:
            key = _canon(home)
            if not key:
                continue
            if key in seen_keys:
                bump("configured"); continue
            if _dead_recently(led, key):
                bump("known-dead"); continue
            probe_s.refusals = []
            try:
                cands, f = (civic.probe(probe_s, who)
                            if kind == "city" else civic.probe_dmo(probe_s, who, home))
            except Exception as exc:                              # noqa: BLE001
                bump("exception:" + type(exc).__name__); continue
            status = f.get("status") or "?"
            for _u, why, _r in probe_s.refusals:
                bump(f"robots-refused-request:{why}")
            if status == "unreachable" and f.get("note") in _ROBOTS_PROBE_STATUS:
                status = _ROBOTS_PROBE_STATUS[f["note"]]
            new = 0
            for cand in cands:
                ckey = _canon(_key_of(cand))
                if ckey in seen_keys or _dead_recently(led, ckey):
                    bump("configured"); continue
                if ckey in declined:
                    bump("declined"); continue
                if (led.get(ckey) or {}).get("status") == "held":
                    bump("held"); continue
                # The country SPELLED OUT, like the venue backend's label.
                # `_metro` is written into the shipped config (1,247 ics entries
                # carry one), so "Bern, CH" is a line a curator reads and a
                # future parser has to guess at — the same trap the geocode
                # suffix was in.
                cand["_metro"] = (
                    f"{place.get('city')}, "
                    f"{place.get('region') or _ISO_COUNTRY.get(country, country)}")
                found[ckey] = cand
                new += 1
                print(f"    + {cand['type']:5} {cand['name'][:46]:46} "
                      f"{_public_url(cand.get('url') or cand.get('base_url'))[:44]}")
            if new:
                continue
            # Same rule the venue backend follows: park the STABLE findings only.
            # A bot challenge or a timeout is how the site felt about us for one
            # request, not a fact about the site, and parking it for 90 days
            # would retire a working calendar on the strength of a bad moment.
            if status == "no-calendar" or status.startswith("offsite"):
                led[key] = {"checked": _today_int(), "name": place["name"][:80],
                            "reason": f"civic probe: {status}", "status": "fail",
                            "type": f"civic-{kind}"}
                if f.get("offsite_url"):      # see the venue backend's note
                    led[key]["offsite_url"] = f["offsite_url"][:300]
            elif (status == "robots-disallowed"
                  and (led.get(key) or {}).get("type") in (None, f"civic-{kind}")):
                # A verify's status and so its DEAD_TTL; the rule in the reason.
                why = next((r for _u, w, r in probe_s.refusals if w == "RobotsDisallowed"), "")
                led[key] = {"checked": _today_int(), "name": place["name"][:80],
                            "reason": f"civic probe: robots.txt {why}"[:300],
                            "status": "refused", "type": f"civic-{kind}"}
            if status.startswith("ok"):
                bump("found-but-unusable: " + civic.why_no_candidate(f))
            else:
                bump(status.split(":")[0])
        read += 1

    # BY ROWS, NOT BY CITIES — see catalog_civic.cities. And only past the ones
    # actually read: a batch cut short by the deadline leaves the cursor before
    # its tail, exactly as the metro walk does.
    #
    # WHICH ROW, precisely: the first UNREAD city's own. `read` is a count of
    # CITIES and the offset is over ROWS, and cities() pages until it has enough
    # distinct cities, so the two diverge by however many duplicates the batch
    # held — a Danish batch is 109 cities in 120 rows and a Swiss one was 2 in
    # 40. Advancing by `read` there would leave the cursor in the middle of
    # ground already walked and the run would re-read its own head for ever.
    # cities() records `_row` for exactly this; a batch from an older cursor
    # that lacks it falls back to the old arithmetic rather than guessing.
    if read >= len(places):
        nxt = offset + rows
    else:
        nxt = offset + int(places[read].get("_row", read))
    _civic_offsets(cursor)[country] = nxt
    if read < len(places):
        print(f"  advanced {read}/{len(places)} cities; the rest come round again")


def _discover_osm(session, seen_keys, led, limit, cursor, metros_per_run=None,
                  deadline=0.0, kinds=(), workers=None):
    """Propose venue calendars found on the map. See catalog_discover_osm.py.

    `kinds` PINS the walk to named OSM kinds — `community_centre,library` —
    and the caller gives a pinned walk its own cursor slot, because it is a
    different walk: the full sweep covers three metros a day and had reached
    metro 42 (Milan) after a month, and "the schedule of every community
    centre" is a question about ALL 260 hubs that cannot wait for it. A pinned
    metro costs a fraction of a full one (DC: 198 community-centre sites
    against ~600 of every kind) and the ledger it writes into is shared, so
    nothing is probed twice.

    The ledger does more work here than in the other backends. A metro has
    hundreds of venues with a website and most of them have no calendar we can
    read — so the OUTCOME OF THE PROBE is recorded against the venue's homepage,
    not just the successes. Without that, every run re-fetches every dead end in
    the city it lands on, and the cost of a sweep never falls.

    `workers` venues are probed at once (OSM_PROBE_WORKERS), never two on one
    host; `session` is the Overpass session and lends its headers to theirs.
    """
    import catalog_discover_osm as osm

    per_run = int(metros_per_run or OSM_METROS_PER_RUN)
    all_metros = osm.metros()
    if not all_metros:
        print("  no metro list — metros_global.json / metros_us.txt missing")
        return {}, {}
    # THE CURSOR NAMES A METRO. IT DOES NOT COUNT TO ONE.
    #
    # It used to store a position, and a position means nothing the moment the
    # list is edited: metros_global.json has been broadened twice, and inserting
    # 14 German metros ahead of the walk silently re-aims the cursor at a country
    # it already read while the ones after it are never reached. The live cursor
    # is the evidence — it said 49, which is Paris, on a ledger holding 1,041
    # probes of .fr hosts. Those French metros were swept and the cursor was
    # pointed back at them by an edit, not by a wrap.
    #
    # Keyed by name, an edit costs nothing: unknown key -> start at the top,
    # which under the thinnest-first order in osm.metros() is the country the
    # catalog has least of, and re-probing is already cheap (see the ledger
    # note below). `metro` is still written, for a human reading the file.
    keys = [osm.metro_key(m) for m in all_metros]
    prev = cursor.get("metro_key")
    start = keys.index(prev) if prev in keys else 0
    declined = set()
    for fname in ("squarespace_sources.json", "tribe_sources.json",
                  "jsonld_sources.json", "ics_sources.json"):
        declined |= _not_included(fname)   # already canonicalised
    # What a homepage's `found` row may name and still be answered (see the
    # pre-filter below): a configured or declined key, or the HASH a held
    # calendar's row carries (_held_key) of a Google calendar the owner has
    # since configured by hand, whose URL key the row cannot name.
    answered = seen_keys | declined
    answered |= {_held_key({"url": "https://calendar.google.com/" + m.group(1)})
                 for m in (re.match(r"(?:calendar\.)?google\.com/(calendar/ical/.+)", k or "")
                           for k in answered) if m}

    found, skipped = {}, {}
    found_rank = {}           # key -> (metro step, venue index) of its proposer

    def bump(k):
        skipped[k] = skipped.get(k, 0) + 1

    robots_status = _ROBOTS_PROBE_STATUS

    def settle(v, home, f, rank):
        """What one probe's answer means for the ledger, `found` and the tally.
        Main thread only."""
        status = f.get("status") or "?"
        refusals = f.get("robots_refused") or []
        for _u, why, _r in refusals:
            bump(f"robots-refused-request:{why}")
        if status == "unreachable" and f.get("note") in robots_status:
            status = robots_status[f["note"]]
        cand = osm.to_candidate(v, f, label) if status in ("ok", "ok-homepage") else None
        # The walk writes the homepage's row only where it is the walk's: a
        # row of another type under the same key is a verify's (a tribe
        # base_url at the site root), and its fail or refused says more.
        prior = led.get(home) or {}
        ours = prior.get("type") in (None, "osm-venue")
        if cand:
            key = _canon(_key_of(cand))
            # A PERSONAL ADDRESS IS ITS ID (see _held): its ledger row is keyed
            # by a hash, never by the URL, and one the owner already has in the
            # held list is not proposed again.
            held = _held(cand)
            lkey = _held_key(cand) if held else key
            # THE HOMEPAGE IS PARKED `found` FOR A WEEK (_probed_recently),
            # whatever becomes of its candidate, and names the candidate's
            # key, so once that is configured the home is not asked again at
            # all. It also replaces an older probe's no-calendar or offsite
            # row, which the ledger would otherwise go on believing. Every
            # candidate that reaches `found` is therefore written out:
            # cmd_discover does not cut the walk's finds at `limit`.
            if ours:
                led[home] = {"checked": _today_int(), "name": v.get("name", "?")[:80],
                             "reason": f"osm probe: found {cand.get('type')}",
                             "status": "found", "type": "osm-venue", "cal_key": lkey,
                             "probe": osm.PROBE_VERSION}
            if key in seen_keys or key in declined:
                bump("configured")
                return
            if _dead_recently(led, lkey):
                bump("known-dead")
                return
            # Held for the owner, a personal address or a ruling still owed
            # (see cmd_verify): not proposed again until the owner decides.
            if (led.get(lkey) or {}).get("status") == "held":
                bump("held")
                return
            if held:
                cand["_home"] = v.get("url")      # for the owner's list; merge never writes it
            # TWO VENUES, ONE CALENDAR (a city's events page linked from each
            # of its centres): the FIRST in map order proposes it, whichever
            # probe finished first, so the candidate's name and venue block do
            # not change between two runs of the same metro.
            if key in found_rank:
                bump("same-calendar")
                if found_rank[key] <= rank:
                    return
            else:
                print(f"    + {cand['type']:11} {cand['name'][:36]:36} {_public_url(f.get('cal_url'))[:52]}")
            cand["_metro"] = label
            found[key] = cand
            found_rank[key] = rank
            return
        # A probe that found nothing is a RESULT, and recording it is what
        # keeps the next sweep of this metro cheap — 3,748 London venues
        # publish a website and most have no calendar we can read.
        #
        # But only the STABLE findings are parked for the 90-day TTL. "This
        # site has no events page" and "its events live on Eventbrite" are
        # facts about the site that will still be true in a month. A bot
        # challenge, a timeout or a 5xx is not a fact about the site at all, it
        # is how the site felt about us for one request — theblackaltar.org
        # served this probe and challenged the very next one, from the same IP,
        # seconds apart. Parking those for 90 days would retire a working
        # calendar on the strength of a bad moment, and the metro cursor means
        # nobody would look again for months. Challenges, 429s and 5xx are not
        # written down at all; `unreachable` is parked for UNREACHABLE_TTL_DAYS
        # under its own status (see there for why a week), and so is
        # `probed` (see _probed_recently): a 4xx, a Crawl-delay past the cap,
        # or a find no candidate could be made of.
        why = osm.why_no_candidate(f) if status.startswith("ok") else ""
        # A JSONLD LISTING THAT SHOWS NO EVENT (osm.events_evidence) is a
        # no-calendar verdict, not a candidate proposed to fail verify:
        # parked for DEAD_TTL under the reason a later PROBE_VERSION re-asks
        # once the row is PROBE_REASK_DAYS old (_dead_recently). Like the
        # `found` and `probed` rows, never over a verify's row.
        empty_jsonld = why.startswith("jsonld-without-events")
        # A LAPSED OFFSITE VERDICT KEEPS ITS LINK. A 4xx, a timeout or an
        # unusable find (a jsonld listing with no event among them) on the
        # re-probe says nothing about the Eventbrite organizer the row
        # names, and `ledger --offsite` lists a link only under an offsite
        # reason. So that row is left as it is, unparked: the home is asked
        # again on the next visit, which costs one request. A fresh
        # no-calendar or offsite verdict, or a find, still replaces it.
        links_out = bool(prior.get("offsite_url"))
        if (status in ("no-calendar",) or status.startswith("offsite")
                or (empty_jsonld and ours and not links_out)):
            led[home] = {"checked": _today_int(), "name": v.get("name", "?")[:80],
                         "reason": f"osm probe: {'no-calendar' if empty_jsonld else status}",
                         "status": "fail", "type": "osm-venue", "probe": osm.PROBE_VERSION}
            # The page the probe took for the calendar and found nothing on.
            # It is what a later probe needs to look at again, and it was
            # computed and thrown away (find_calendar records the first page
            # it followed). Never the homepage itself: that is the key.
            if (f.get("cal_url") and _canon(f["cal_url"]) != home
                    and not _ADDRESS_IN_URL_RX.search(f["cal_url"])):
                led[home]["cal_url"] = f["cal_url"][:300]
            # AND THE LINK, for the ones that name an adapter we have.
            # `offsite:eventbrite` is a finished discovery wearing a
            # failure's clothes: the venue's events exist, they are on a
            # platform this repo ingests, and the only thing missing is the
            # organizer id — which is in the link. Written beside the entry
            # so `catalog_curate.py ledger --offsite` can list them without
            # fetching 26,000 sites again.
            if f.get("offsite_url") and not _ADDRESS_IN_URL_RX.search(f["offsite_url"]):
                led[home]["offsite_url"] = f["offsite_url"][:300]
        elif status == "unreachable" and ours and not links_out:
            # Never over a row some other probe of this URL wrote (a verify's
            # ok, empty or refused says more than one failed connection).
            note = f" ({f['note']})" if f.get("note") else ""
            led[home] = {"checked": _today_int(), "name": v.get("name", "?")[:80],
                         "reason": f"osm probe: unreachable{note}",
                         "status": "unreachable", "type": "osm-venue",
                         "probe": osm.PROBE_VERSION}
        elif status == "robots-disallowed" and ours:
            # The same status, and so the same DEAD_TTL, a verify gives a
            # source robots.txt refuses; the rule is in the reason.
            rule = next((r for _u, w, r in refusals if w == "RobotsDisallowed"), "")
            led[home] = {"checked": _today_int(), "name": v.get("name", "?")[:80],
                         "reason": f"osm probe: robots.txt {rule}"[:300],
                         "status": "refused", "type": "osm-venue",
                         "probe": osm.PROBE_VERSION}
        elif ours and not links_out and (
                status.startswith("ok") or status == "robots-crawl-delay"
                or (re.fullmatch(r"http4\d\d", status) and status != "http429")):
            # Not dead (verify never reads `probed`), only not worth a second
            # request this week. 429 is a rate limit, so it is not one.
            if status == "robots-crawl-delay":
                rule = next((r for _u, w, r in refusals if w == "RobotsCrawlDelay"), "")
                said = f"robots.txt {rule}".strip()
            else:
                said = f"found-but-unusable: {why}" if why else status
            led[home] = {"checked": _today_int(), "name": v.get("name", "?")[:80],
                         "reason": f"osm probe: {said}"[:300],
                         "status": "probed", "type": "osm-venue",
                         "probe": osm.PROBE_VERSION}
        # A probe that SUCCEEDED and still yielded nothing is not the same
        # event as a probe that failed, and lumping them under the status
        # word reported 25 of them as "ok" in one sweep. Name the gap.
        if status.startswith("ok"):
            bump("found-but-unusable: " + why)
        elif status == "unreachable" and f.get("note"):
            # BY EXCEPTION, because the two timeouts are different costs and
            # the log never said which fired: a ConnectTimeout is spent inside
            # PROBE_CONNECT_S, a ReadTimeout waits out the read timeout.
            bump(f"unreachable:{f['note']}")
        else:
            bump(status.split(":")[0])

    # The probing pool, built once for the run. One Session per worker thread,
    # carrying the caller's headers (the User-Agent the walk is judged by), and
    # one gate across all of them. See OSM_PROBE_WORKERS.
    import concurrent.futures as cf
    import threading
    workers = max(1, int(workers or OSM_PROBE_WORKERS))
    gate = _HostGate(OSM_HOST_GAP_S)
    robots = _WalkRobots(timeout=(osm.PROBE_CONNECT_S, 15))
    cache = _ResponseCache()
    headers = dict(getattr(session, "headers", None) or {})
    local, sessions, live = threading.local(), [], [0]

    def _probe(url):
        s = getattr(local, "s", None)
        if s is None:
            s = local.s = _PacedSession()
            s.headers.update(headers)
            s.gate, s.robots, s.cache = gate, robots, cache
            sessions.append(s)
        s.refusals = []
        f = osm.find_calendar(s, url)
        if s.refusals:
            f = dict(f, robots_refused=list(s.refusals))
        return f

    def _submit(url):
        live[0] += 1
        return pool.submit(_probe, url)

    pool = cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="osm-probe")
    probed = set()            # every site this run has started a probe of
    broken = None

    # A metro Overpass never answered for is UNREAD, not swept. Advancing the
    # cursor past it costs the whole metro until the cursor wraps — 78 runs, or
    # about eleven weeks at three a day — and that is exactly what happened on
    # 2026-08-19: nine of ten metros hit connection resets and every one was
    # reported as "0 venues publish a website" before the cursor moved on.
    read = 0
    stuck = cursor.setdefault("stuck", {})
    for step in range(per_run):
        # OUT OF TIME IS A REASON TO STOP, NOT A REASON TO LOSE THE RUN.
        #
        # This backend is the whole budget: measured 2026-08-26, socrata took 6
        # seconds, ckan two minutes and mobilizon three seconds, and osm then ran
        # 88 minutes into the job's 90-minute timeout-minutes and was CANCELLED
        # — which skips every step after it, so "Commit new sources" never ran
        # and every source the run had verified went in the bin. That happened on
        # both of the day's two scheduled runs, and on the ones before them: it
        # is what this job does every time, not a bad day.
        #
        # Breaking BETWEEN metros is the honest place, because `read` is what the
        # cursor advances by — so an unreached metro is simply where the next run
        # starts, which is the same contract an Overpass refusal already has.
        if deadline and time.time() >= deadline:
            print(f"  out of time after {read}/{per_run} metros — stopping here so "
                  f"the candidates found so far can be verified and committed")
            break
        idx = (start + step) % len(all_metros)
        m = all_metros[idx]
        key = keys[idx]                       # position moves; the name does not
        # The country SPELLED OUT. This label is the geocode_suffix floor for
        # every ics candidate the metro proposes (osm._suffix falls back to it
        # when the venue has no addr:city), so an ISO code here ships in the
        # config file — ", Zurich, CH" — and is then read by the geocoder and by
        # the coverage report, neither of which knows what CH is. The cursor
        # still keys on the CODE via metro_key, so this does not move the walk.
        label = f"{m['name']}, {m.get('country_name') or m['country']}"
        # The keyword only when pinned, so an unpinned walk is the call it always
        # was (test_discover_osm stubs it with three positionals).
        venues = osm.overpass_venues(session, m["bbox"], label,
                                     **({"kinds": kinds} if kinds else {}))
        if venues is None:
            # ONE STRIKE A DAY. Since 2026-10-23 the 03:20 and 09:30 crons walk
            # this one slot, so a strike a run reached three in about 24 h
            # (day 1 14:30, day 2 08:40, day 2 14:30), and a skipped metro is
            # not read again until the 261-metro ring wraps: 45-130 days at
            # 1-3 metros a run. The day of the last strike (_today_int, the
            # runner's UTC date) is kept beside the count; a bare int is a
            # strike from before, on a day unknown.
            today, prev = _today_int(), stuck.get(key)
            n, last = ((int(prev.get("n", 0) or 0), int(prev.get("day", 0) or 0))
                       if isinstance(prev, dict) else (int(prev or 0), 0))
            if last != today:
                n += 1
            stuck[key] = {"n": n, "day": today}
            if n >= 3:
                # Never asked on three days running is no longer a bad afternoon.
                # Move past it rather than wedge the sweep on one bbox for ever,
                # and say so, because a silently skipped metro is what this whole
                # block exists to prevent.
                print(f"  {label}: overpass has refused on {n} days running — "
                      f"SKIPPING this metro and advancing past it")
                stuck.pop(key, None)
                read += 1
            else:
                again = "" if last != today else ", already counted today"
                print(f"  {label}: overpass did not answer — metro UNREAD, the "
                      f"cursor stays here (day {n}/3{again})")
            break
        stuck.pop(key, None)
        print(f"  {label}: {len(venues)} venue(s) publish a website")
        # A METRO IS ONLY `read` IF IT WAS READ TO THE END. Both ways out of the
        # venue loop below leave it half-probed, and the cursor must not march
        # past what nobody looked at — the same contract an Overpass refusal has
        # had since the nine-metros-for-78-runs bug. Re-probing costs little,
        # because the ledger already records every dead end found here and
        # `_dead_recently` skips them next time; that is what the ledger is for.
        whole = True
        # THE CHEAP DECISIONS FIRST, ON THIS THREAD: which venues need a live
        # probe at all. A venue already configured, declined, parked dead or
        # parked unreachable costs nothing, and neither does the second venue
        # that names a site this run has already probed (overlapping bboxes,
        # one website on several map objects).
        todo = []
        for vi, v in enumerate(venues):
            home = _canon(v["url"])
            if not home:
                continue
            if home in seen_keys:
                bump("configured")
                continue
            # A SITE DECLINED WHOLE (a bare host in `_not_included`: Lotto
            # Arena's, whose event pages answer through a third party's
            # sign-in) is declined under any page of it. 25,303 of the
            # 72,884 osm-venue homes in the ledger on 2026-10-29 carry a path,
            # so an OSM `website` can name a page and miss the bare host.
            # One home did then: gldf.org/ronchin-59.html, probed 10-28 to
            # find gldf.org's calendar, whose site tribe_sources.json declines.
            if home in declined or re.split(r"[/?]", home, maxsplit=1)[0] in declined:
                bump("declined")
                continue
            if _dead_recently(led, home):
                bump("known-dead")
                continue
            if _unreachable_recently(led, home):
                bump("unreachable-recently")
                continue
            # A home whose calendar is now configured (or declined) stays
            # answered for as long as it is: its probe would only say so
            # again, 91-170 times a run in the community walk.
            rec = led.get(home) or {}
            if (rec.get("type") == "osm-venue" and rec.get("status") == "found"
                    and rec.get("cal_key") and rec["cal_key"] in answered):
                bump("configured")
                continue
            if _probed_recently(led, home):
                bump("probed-recently")
                continue
            if home in probed:
                bump("same-site")
                continue
            probed.add(home)
            todo.append((v, home, _probe_host(v["url"]), (step, vi)))

        # THE LIVE PROBES, `workers` AT ONCE, NEVER TWO ON ONE HOST. Only the
        # fetching happens on a worker; every write — the ledger, `found`, the
        # tally — happens here, as each probe comes back, so nothing below the
        # loop is shared between threads.
        #
        # THE LONGEST SAME-HOST QUEUE GOES FIRST. A host's venues run one after
        # another, so a library system with 19 branch pages on one site is the
        # metro's critical path (Winnipeg: the others were done at 12.5 s, the
        # chain at 40 s), and starting it last adds the rest of the metro to it.
        inflight, busy = {}, set()
        attempted = errors = 0
        left = {}
        for t in todo:
            left[t[2]] = left.get(t[2], 0) + 1
        while todo or inflight:
            while todo and len(inflight) < workers:
                # THE BUDGET HAS TO BE CHECKED HERE, NOT ONLY BETWEEN METROS.
                #
                # Live on 2026-08-26: a --max-minutes 70 budget checked only at
                # the top of the metro loop never fired, and the step was
                # cancelled at its 90-minute cap having printed NOTHING from
                # this backend — no "out of time", no per-metro summary. One
                # metro is an Overpass call plus a LIVE FETCH PER VENUE
                # (find_calendar), and a dense metro is hundreds of them, so a
                # single iteration of the outer loop can outlast the whole
                # budget. Bounding the loop you can see is not the same as
                # bounding the work. Checked before every probe is STARTED;
                # the ones already running are let finish and are recorded.
                if (deadline and time.time() >= deadline) or len(found) >= limit:
                    whole = False
                    todo = []
                    break
                pick = max((i for i, t in enumerate(todo) if t[2] not in busy),
                           key=lambda i: (left[todo[i][2]], -i), default=None)
                if pick is None:
                    break                 # every waiting venue's host is mid-probe
                v, home, host, rank = todo.pop(pick)
                left[host] -= 1
                busy.add(host)
                inflight[_submit(v["url"])] = (v, home, host, rank)
            if not inflight:
                break
            done, _ = cf.wait(list(inflight), return_when=cf.FIRST_COMPLETED)
            for fut in [f for f in inflight if f in done]:   # submission order
                v, home, host, rank = inflight.pop(fut)
                busy.discard(host)
                attempted += 1
                try:
                    f = fut.result()
                except Exception as exc:                          # noqa: BLE001
                    # One venue's page breaking the parser must not end the
                    # walk; it is tallied, not parked, and asked again next time.
                    print(f"    ! {home[:60]}: {type(exc).__name__}: {str(exc)[:60]}")
                    bump(f"probe-error:{type(exc).__name__}")
                    errors += 1
                    continue
                settle(v, home, f, rank)
        # BUT A METRO WHERE MOST PROBES RAISED WAS NOT READ: that is the code
        # broken, not one page. Tallying it and moving on would march the
        # cursor past every metro the run visits while recording nothing
        # (measured in review: find_calendar raising on every venue moved a
        # 3-metro cursor three metros and wrote no ledger row), where the code
        # before the pool raised and left the cursor where it was. So the
        # metro is UNREAD and the run stops, loudly, once the ledger is saved.
        if errors >= OSM_PROBE_ERRORS_STOP and errors * 2 > attempted:
            whole = False
            broken = (label, errors, attempted)
            print(f"  {label}: {errors} of {attempted} probes RAISED — the probe "
                  f"code is broken, not the sites; metro UNREAD, stopping the walk")
            break
        read += 1 if whole else 0
        if not whole and deadline and time.time() >= deadline:
            print(f"  out of time inside {label} — stopping so the candidates "
                  f"found so far can be verified and committed; this metro is "
                  f"UNREAD and the cursor stays before it")
            break
        if len(found) >= limit:
            break

    pool.shutdown(wait=True)
    for s in sessions:
        s.close()
    if live[0]:
        print(f"  {live[0]} live probe(s), {workers} at a time; the host gate held "
              f"{gate.waits} request(s) back, {gate.waited:.0f}s in all; "
              f"{robots.fetched} robots.txt read, {cache.hits} repeat request(s) "
              f"answered from this run's cache")

    # Only past what was actually READ.
    nxt = (start + read) % len(all_metros)
    cursor["metro_key"] = keys[nxt]
    cursor["metro"] = nxt                     # informational: what the name is now at
    if read < per_run:
        print(f"  advanced {read}/{per_run} metros; the rest are unread and come "
              f"round again next run")
    _save_ledger(led)
    if broken:
        raise RuntimeError(f"osm walk: {broken[1]} of {broken[2]} probes raised in "
                           f"{broken[0]}; the ledger is saved and the metro left unread")
    # In map order, not the order the probes happened to finish in.
    found = dict(sorted(found.items(), key=lambda kv: found_rank.get(kv[0], (0, 0))))
    return found, skipped

MOBILIZON_DIRECTORY = "https://instances.joinmobilizon.org/api/v1/instances"
# An instance with no local events is somebody's empty test server, and health is
# the directory's own reachability score. Both are cheap ways to not spend a
# verification request finding out.
MOBILIZON_MIN_EVENTS = 5
MOBILIZON_MIN_HEALTH = 50


def _not_included(fname):
    """URLs a human has looked at and rejected ON THE RECORD.

    mobilizon_sources.json carries a `_not_included` map — one instance is
    spam-ridden with scam listings geocoded to France, another has a licence that
    has not been cleared. Both would pass verification easily, which is the
    point: verification proves a feed WORKS, not that we want it. Automation that
    cannot read an editorial no would re-propose them every week for ever.
    """
    p = os.path.join(HERE, fname)
    if not os.path.exists(p):
        return set()
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    if not isinstance(doc, dict):
        return set()
    return {_canon(u) for u in (doc.get("_not_included") or {})}


# AN EDITORIAL NO HOLDS EVERYWHERE A SOURCE CAN COME BACK, not only in the walk
# that read it first. The 16 CivicPlus booking calendars the corrections
# retired on 2026-10-29 (renters' names, "Reserved", trailer rotas) were all
# found by the civic walk (`civic:city -> civicplus[...]`), and the civic walk,
# `verify` and `merge` read no `_not_included`: only the venue walk and
# Mobilizon discovery did. 10 of the 16 ledger rows were still `ok`, so when
# the walk reached those cities again it would have proposed them, verify
# passed them and the weekly sweep merged them back (review of 2026-10-29).
# Every CONFIG file's map counts, keyed as _not_included keys them; an iCal
# feed's record lives in jsonld_sources.json's, because ics_sources.json is a
# bare list.
def _declined_keys():
    """Every key in every CONFIG file's `_not_included` map, canonicalised."""
    out = set()
    for fname in sorted({f for f, _k in CONFIG.values()}):
        out |= _not_included(fname)
    return out


def _entry_keys(e):
    """Every canonical key an entry could be configured or declined under:
    its ledger key, and for jsonld every listing URL."""
    keys = {_canon(_key_of(e))}
    if e.get("type") == "jsonld":
        listing = e.get("listing") or []
        keys |= {_canon(u) for u in (listing if isinstance(listing, list) else [listing])
                 if isinstance(u, str) and u}
    return {k for k in keys if k}


def _discover_mobilizon(session, seen_keys, led, limit):
    found = {}
    skipped = {"configured": 0, "ledger": 0, "declined": 0, "too_quiet": 0, "unhealthy": 0}
    declined = _not_included("mobilizon_sources.json")
    try:
        r = session.get(MOBILIZON_DIRECTORY, params={"count": 500}, timeout=45)
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
    except Exception as exc:  # noqa: BLE001
        print(f"  (instance directory unreachable: {str(exc)[:60]})")
        return found, skipped
    print(f"  directory lists {len(rows)} instance(s)")
    # Busiest first: an instance with 9,000 local events is worth a probe before
    # one with 6, and the limit should bite on the tail rather than on the head.
    for inst in sorted(rows, key=lambda i: -(i.get("totalLocalEvents") or 0)):
        host = (inst.get("host") or "").strip().lower()
        if not host:
            continue
        base = f"https://{host}"
        key = _canon(base)
        if key in found or key in seen_keys:
            skipped["configured"] += 1
            continue
        if key in declined:
            skipped["declined"] += 1
            continue
        if _dead_recently(led, key):
            skipped["ledger"] += 1
            continue
        if (inst.get("totalLocalEvents") or 0) < MOBILIZON_MIN_EVENTS:
            skipped["too_quiet"] += 1
            continue
        if (inst.get("health") or 0) < MOBILIZON_MIN_HEALTH:
            skipped["unhealthy"] += 1
            continue
        # Country from the TLD, NOT from the directory's `country` — that field
        # is where the server is hosted, and it is wrong in the way that matters:
        # the directory files ticketzon.it under FI and mobilizon.fr under DE.
        # The hand-curated entries say Italy and France, and they are right.
        # It only ever fills a gap anyway (the adapter prefers the event's own
        # physicalAddress, and coordinates come from geom regardless), so an
        # unknown TLD is better left blank than filled in from hosting.
        entry = {
            "type": "mobilizon",
            "name": (inst.get("name") or host)[:90],
            "base_url": base,
            "category": "community",
            "limit": 100,
            "max_pages": 5,
        }
        country = _url_country(base)   # already a NAME, not an ISO code
        if country:
            entry["default_country"] = country
        found[key] = entry
        if len(found) >= limit:
            break
    return found, skipped


def _pinned_slot(all_cursors, kinds):
    """The cursor slot of a kind-pinned osm walk, carried over when it WIDENS.

    The slot is named by its kinds, so adding one (social_centre joined
    community_centre,library on 2026-10-12) names a slot nobody has written,
    and an empty slot starts at the top of the metro list: the community walk
    had reached US:Kansas City, metro 205 of 261, and would have gone back to
    re-read 204 metros before reaching new ground. A new slot therefore starts
    where the widest pinned walk whose kinds it contains had got to. The
    metros behind that point are read for the new kind when the cursor wraps.
    """
    slot = f"osm|{','.join(kinds)}"
    if slot in all_cursors:
        return slot
    want, best = set(kinds), None
    for k, v in all_cursors.items():
        have = set(k.split("|", 1)[1].split(",")) if k.startswith("osm|") else set()
        if have and have < want and isinstance(v, dict) and v.get("metro_key"):
            if best is None or len(have) > len(best[0]):
                best = (have, k, v)
    if best:
        all_cursors[slot] = {"metro": best[2].get("metro"), "metro_key": best[2]["metro_key"]}
        print(f"  a widened walk: {slot!r} starts where {best[1]!r} had got to "
              f"({best[2]['metro_key']})")
    return slot


def cmd_discover(limit=400, out="candidates.json", backend="socrata", only=(),
                 metros=None, max_minutes=0.0, kinds=(), workers=None):
    """`only` pins the sweep to specific lens categories.

    Without it the budget is spent thinnest-category-first across every query,
    which is the right default and still lets a fat category crowd a starving
    one out: `market` has 481 sources and `fitness` has none, and one run of 300
    candidates does not reliably reach the bottom of the list. A gap sweep passes
    the categories with zero supply here and spends the whole budget on them.
    """
    seen_keys = _config_keys()
    led = _load_ledger()
    session = _session()
    cats = curated_categories(session)
    all_cursors = _load_cursor()
    # A kind-pinned osm walk is a different walk and keeps its own place —
    # see _discover_osm. `osm|community_centre,library`, so the slot names
    # what it is walking for.
    if backend == "osm" and kinds:
        slot = _pinned_slot(all_cursors, kinds)
    else:
        slot = backend
    cursor = all_cursors.setdefault(slot, {})
    src = "mapsee.me/api/lenses" if CURATED_FROM_LIVE else "committed fallback"
    print(f"targets via {src}: {', '.join(cats)}")
    if only:
        unknown = [c for c in only if c not in cats]
        if unknown:
            # Loud, not fatal: a lens can be retired between the gap being
            # measured and this run starting, and sweeping for it is harmless.
            print(f"  FLAG --category names {', '.join(unknown)}, which the live "
                  f"roster does not ask for")
        print(f"  pinned to: {', '.join(only)}")

    if backend == "mobilizon":
        # No query list and no cursor: the directory is one short document that
        # is read whole every time, so "what is new" is simply what is not
        # already configured, declined or known-dead. A Mobilizon instance is a
        # general-purpose community calendar with no category to pin, so --only
        # does not apply here; skip the backend entirely rather than sweep it
        # and pretend the result was targeted.
        if only:
            print("  (mobilizon carries no per-source category — skipped for a "
                  "category-pinned run)")
            found, skipped = {}, {}
        else:
            found, skipped = _discover_mobilizon(session, seen_keys, led, limit)
    elif backend == "osm":
        # Geographic, not textual: there is no query list to gap-report and no
        # category to pin, because what a venue programmes is not knowable from
        # its OSM tags alone — the config category is a default the classifier
        # then refines.
        if only:
            print("  (osm proposes by PLACE, not by category — skipped for a "
                  "category-pinned run)")
            found, skipped = {}, {}
        else:
            if kinds:
                print(f"  pinned to OSM kinds: {', '.join(kinds)} (cursor slot {slot!r})")
            found, skipped = _discover_osm(
                session, seen_keys, led, limit, cursor, metros_per_run=metros,
                deadline=(time.time() + max_minutes * 60) if max_minutes > 0 else 0.0,
                kinds=kinds, workers=workers)
    elif backend == "civic":
        # Geographic like osm, and for the same reason category-pinning does not
        # apply: what a town programmes is not knowable from its population.
        if only:
            print("  (civic proposes by TOWN, not by category — skipped for a "
                  "category-pinned run)")
            found, skipped = {}, {}
        else:
            found, skipped = _discover_civic(
                session, seen_keys, led, limit, cursor, cities_per_run=metros,
                deadline=(time.time() + max_minutes * 60) if max_minutes > 0 else 0.0)
    elif backend == "ckan":
        _report_query_gaps(cats, CKAN_QUERIES, "ckan")
        queries = _order_queries(_filter_queries(CKAN_QUERIES, only), cats)
        found, skipped = _discover_ckan(session, seen_keys, led, limit, cursor, queries)
    else:
        _report_query_gaps(cats, DISCOVER_QUERIES, "socrata")
        queries = _order_queries(_filter_queries(DISCOVER_QUERIES, only), cats)
        found, skipped = _discover_socrata(session, seen_keys, led, limit, cursor, queries)

    all_cursors[slot] = cursor
    _save_cursor(all_cursors)
    # THE OSM WALK'S FINDS ARE NOT CUT. It stops STARTING probes at `limit`
    # and lets the running ones settle, so it can return up to
    # OSM_PROBE_WORKERS - 1 more, and settle has already parked each one's
    # homepage `found` for a week. Cut here, those calendars were never
    # verified and the resumed metro skipped their homes: 3 of 5 finds with
    # --limit 2 and 4 workers (measured 2026-10-24), lost until the next lap.
    out_list = list(found.values()) if backend == "osm" else list(found.values())[:limit]
    json.dump(out_list, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{len(out_list)} candidate(s) -> {out}")
    print("skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(skipped.items())))
    print(f"Next: python catalog_curate.py verify {out}")
    return 0


# --- robots.txt --------------------------------------------------------------
# Verification proved a feed WORKS and never asked whether we were allowed to
# read it, and the weekly sweep commits whatever verifies. Measured 2026-09-30
# over every configured request: 127 of 3,468 disallowed by their own host's
# robots.txt, 30 of them Google calendars. See robots_txt.py for the matching
# rules and the four files that decided them.
#
# What is checked is the request the INGEST adapter makes, built here the way
# each adapter builds it, and not the page the feed was found on or the
# verifier's smaller probe: `Disallow: /*?` and `Disallow: /*ical=` are exactly
# the rules a query string trips, and the probe and the ingest can differ by one.
_TRIBE_Q = "?per_page=50&start_date={d}&end_date={d}"


def _ingest_requests(t, e):
    """[(method, url)] the ingest adapter for type `t` fetches for entry `e`."""
    base = (e.get("base_url") or "").rstrip("/")
    day = _as_date(_today_int()).isoformat()
    if t == "ics":
        u = e.get("url") or ""
        # A Google calendar with the key set is read through the Calendar API,
        # so that is the request robots.txt is asked about (mapsee_gcal).
        cid = mapsee_gcal.calendar_id(u)
        if cid and mapsee_gcal.api_key():
            return [("GET", mapsee_gcal.api_url(cid))]
        return [("GET", "https://" + u[9:] if u.lower().startswith("webcal://") else u)] if u else []
    if t == "localist":
        return [("GET", f"{base}/api/2/events?days=90&pp=100&page=1")] if base else []
    if t == "tribe":
        return [("GET", f"{base}/wp-json/tribe/events/v1/events" + _TRIBE_Q.format(d=day))] if base else []
    if t == "gancio":
        return [("GET", f"{base}/api/events?start=0&end=0&max=500")] if base else []
    if t == "mobilizon":
        return [("POST", f"{base}/api")] if base else []
    if t == "squarespace":
        return [("GET", e["collection"])] if e.get("collection") else []
    if t == "jsonld":
        listing = e.get("listing") or []
        return [("GET", u) for u in (listing if isinstance(listing, list) else [listing]) if u]
    if t == "mylisting":
        return [("GET", e["explore_url"])] if e.get("explore_url") else []
    if t == "ods":
        return [("GET", (e.get("url") or _ods_url(e)) + "?where=x&limit=100&offset=0")]
    if t in ("opendata", "ckan", "market"):
        u = e.get("url") or ""
        return [("GET", u + ("&" if "?" in u else "?") + "limit=1")] if u else []
    if t == "venuepilot":
        return [("POST", "https://www.venuepilot.co/graphql")]
    u = e.get("url") or ""
    return [("GET", u)] if u.startswith(("http://", "https://")) else []


# The adapters that pace a host by a config's `crawl_delay`: ics per host across
# its feeds, tribe between pages.
_PACED = ("ics", "tribe")


# WHERE ROBOTS.TXT BINDS, AND WHERE IT DOES NOT. The owner's decision,
# 2026-09-30. robots.txt is a PUBLISHER's consent for its own pages and feeds:
# an iCal export, a WordPress site's REST route, a Squarespace collection, a
# listing page, a fair's own site. Those are what `verify` refuses.
#
# A DOCUMENTED PUBLIC API used within its published usage policy is not
# crawling. robots.txt on an API host keeps search engines out of query URLs.
# Photon (`Disallow: /`) and Overpass (`Disallow: /api/`), which the pipeline
# runs on, carry theirs for the same reason. These adapters read such APIs:
#   - Socrata's SODA (opendata, market)
#   - OpenDatasoft's Explore API (ods)
#   - CKAN's datastore
#   - Localist's /api/2
#   - Gancio's and Mobilizon's APIs
#   - Mapas Culturais
#   - OpenActive's RPDE feeds
# The API's own terms still bind: rate limits, licences, keys.
#
# VenuePilot's GraphQL is NOT on the list. It is the back end of a ticket
# widget with no published usage policy, which is DICE's case
# (platforms-probed.md), and its host says `Disallow: /`.
DOCUMENTED_API_TYPES = frozenset({"opendata", "market", "ods", "ckan", "localist",
                                  "gancio", "mobilizon", "mapasculturais", "openactive"})


def _robots_gate(robots, t, e):
    """None when every request is allowed, else the ledger (status, reason).

    A Disallow is `refused` - the operator's answer, parked for the TTL and
    never counted as a dead feed. An unreachable robots.txt and a challenge on
    it are `fail`: RFC 9309 says assume Disallow for the first, permission
    cannot be established for the second, and neither is a verdict about the
    feed that a later look could not change.

    Side effect, deliberately: when the host asks for a Crawl-delay and the
    entry carries none, the entry gets one, so merge writes it into the config
    and the adapter paces the host by it.

    A documented public API is not asked at all (DOCUMENTED_API_TYPES).
    """
    if t in DOCUMENTED_API_TYPES:
        return None
    for method, url in _ingest_requests(t, e):
        ans = robots.check(url)
        if ans["allowed"] is False and ans["status"] == "unreachable":
            return "fail", f"robots.txt unreachable ({ans['robots']})"
        if ans["allowed"] is None:
            return "fail", f"robots.txt challenged ({ans['robots']})"
        if ans["allowed"] is False:
            return "refused", f"robots.txt: {ans['rule']} ({method} {robots_txt.request_path(url)[:60]})"
        if ans.get("crawl_delay") and t in _PACED and not e.get("crawl_delay"):
            e["crawl_delay"] = ans["crawl_delay"]
    return None


# --- commands --------------------------------------------------------------
_CPL_OPENDATA_URL = "https://data.cityofchicago.org/resource/vsdy-d8k7.json"


def _chipublib_configured():
    """Whether BiblioCommons is the configured primary Chicago library feed."""
    path = os.path.join(HERE, "bibliocommons_sources.json")
    try:
        with open(path, encoding="utf-8") as fh:
            sites = json.load(fh).get("sites", [])
        return any(site.get("slug") == "chipublib" for site in sites if isinstance(site, dict))
    except Exception:  # noqa: BLE001 - a missing primary config permits the fallback
        return False


def _blocked_cpl_opendata(e, chipublib_configured):
    if not chipublib_configured or e.get("type") != "opendata":
        return False
    candidate, primary = urlparse(str(_key_of(e) or "")), urlparse(_CPL_OPENDATA_URL)
    return ((candidate.hostname or "").lower() == (primary.hostname or "").lower()
            and candidate.path.rstrip("/").lower() == primary.path.rstrip("/").lower())


def cmd_verify(path, recheck=False, ttl=90):
    cands = json.load(open(path, encoding="utf-8"))
    s = _session()
    robots = robots_txt.Robots(s)
    led = _load_ledger()
    in_config = _config_keys()
    declined = _declined_keys()
    chipublib_configured = _chipublib_configured()
    passed, skipped = [], 0
    for e in cands:
        t = e.get("type")
        key = _canon(_key_of(e))
        # A held entry's row is never keyed by its URL: the URL carries the
        # personal address, and the ledger is committed (see _held).
        held = _held(e)
        lkey = _held_key(e) if held else key
        if t not in VERIFIERS:
            print(f"[SKIP] unknown type={t!r} {e.get('name')}")
            continue
        if not recheck and key in in_config:
            print(f"[SKIP] {t:8} already in config: {e.get('name','?')[:44]}")
            skipped += 1
            continue
        # Not even with --recheck: an editorial no is not a stale verdict.
        if _entry_keys(e) & declined:
            print(f"[SKIP] {t:8} declined on the record (_not_included): {e.get('name','?')[:40]}")
            skipped += 1
            continue
        if _blocked_cpl_opendata(e, chipublib_configured):
            print(f"[SKIP] opendata Chicago Public Library is read through configured BiblioCommons chipublib")
            skipped += 1
            continue
        rec = led.get(lkey)
        # THE SAME PREDICATE THE WALK ASKS (_dead_recently), so a row the walk
        # treats as lapsed is not skipped here: a centre whose old no-calendar
        # row sits under the very key its candidate has (a tribe base_url at
        # the site root) was proposed by the walk and then dropped here as
        # known-dead until the row's 90 days ran out (reviewed 2026-10-12).
        if not recheck and _dead_recently(led, lkey, ttl):
            age = _days_since(rec.get("checked", 0))
            print(f"[SKIP] {t:8} known-dead {age}d ago ({rec.get('reason','')[:30]}): "
                  f"{e.get('name','?')[:36]}")
            skipped += 1
            continue
        # Held for the owner: a personal-address calendar (its row is under
        # _held_key), or any verified feed the owner was asked to rule on
        # first (Los Ranchos de Albuquerque's CivicPlus catIDs 24 and 23,
        # 2026-10-29: the lowercase `Disallow: /common/modules/icalendar/`).
        # --recheck re-verifies a personal-address one (it is held again
        # below); a ruling still owed is not a stale verdict, so never.
        if (rec or {}).get("status") == "held" and not (recheck and held):
            print(f"[SKIP] {t:8} held for the owner since {rec.get('checked')}: "
                  f"{e.get('name','?')[:36]}")
            skipped += 1
            continue
        # A Google calendar can only be read with the Calendar API key, because
        # robots.txt refuses its iCal export. Without the key it is skipped and
        # NOT recorded: a `refused` row would park it for 90 days, and the key
        # is a setting, not a verdict about the calendar.
        if t == "ics" and mapsee_gcal.calendar_id(e.get("url") or "") and not mapsee_gcal.api_key():
            print(f"[SKIP] {t:8} {e.get('name','?')[:46]:46} a Google calendar: needs "
                  f"{mapsee_gcal.KEY_ENV} (its iCal export is refused by robots.txt)")
            skipped += 1
            continue
        # The owner's list (`ledger --held`) needs where a held one was found,
        # never its id; a failed one is parked under the same hashed key.
        where = ({k: e[f] for k, f in (("home", "_home"), ("metro", "_metro"), ("found", "_found"))
                  if e.get(f)} if held else {})
        gate = _robots_gate(robots, t, e)
        if gate:
            status, note = gate
            led[lkey] = {"type": t, "name": e.get("name"), "status": status,
                         "reason": note, "checked": _today_int(), **where}
            print(f"[XX ] {t:8} {e.get('name','?')[:46]:46} {note}")
            continue
        try:
            ok, note = VERIFIERS[t](s, e)
        except Exception as ex:  # noqa: BLE001
            ok, note = False, f"{type(ex).__name__}: {str(ex)[:70]}"
        led[lkey] = {"type": t, "name": e.get("name"), "status": _status_for(ok, note),
                     "reason": note, "checked": _today_int(), **where}
        if held:
            if ok:
                led[lkey].update(status="held", reason=f"{note}; {held}")
                print(f"[HELD] {t:8} {e.get('name','?')[:46]:46} {note}: {held} "
                      f"(catalog_curate.py ledger --held)")
                continue
        print(f"[{'OK ' if ok else 'XX '}] {t:8} {e.get('name','?')[:46]:46} {note}")
        if ok:
            passed.append(e)
    _save_ledger(led)
    out = os.path.splitext(path)[0] + ".verified.json"
    json.dump(passed, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n{len(passed)} verified, {skipped} skipped (known) of {len(cands)} -> {out}")
    return 0


# Files outside CONFIG that an adapter still FETCHES from. program, affiliate
# and restaurant configs carry links for people, not requests, and are left out.
_ROBOTS_EXTRA = ("fair_sources.json", "market_sources.json", "openactive_sources.json",
                 "mapasculturais_sources.json", "festival_sources.json",
                 "luma_sources.json", "dice_venue_sources.json", "slu_sources.json",
                 "pioneersquare_sources.json", "seattlecenter_sources.json",
                 "rolodex_sources.json", "parkrun_sources.json",
                 # The community-centre adapters that read a publisher's own
                 # pages or CMS endpoint (each also asks robots.txt itself).
                 "glenecho_sources.json", "drupal_fullcalendar_sources.json",
                 "revize_sources.json", "perfectmind_sources.json", "lcsd_sources.json",
                 # The Natuurwerkdag's finder and job pages (2026-10-24): an HTML
                 # read of a publisher's own pages, which also asks robots.txt itself.
                 "natuurwerkdag_sources.json",
                 # Galaxy Digital volunteer portals (2026-10-25): HTML reads of
                 # each portal's own pages; it asks robots.txt itself too.
                 "galaxydigital_sources.json",
                 # The walks (2026-10-11). Each also asks robots.txt itself.
                 "ava_sources.json", "rallybound_sources.json", "luminate_sources.json",
                 # KČT's hikes (2026-10-24): an HTML read of kct.cz's own
                 # calendar and event pages; it asks robots.txt itself too.
                 "kct_sources.json",
                 # Swiss repair cafés' session pages (2026-10-24), read from
                 # the publisher's own sitemaps; it asks robots.txt itself too.
                 "repaircafe_ch_sources.json")


def _walk_urls(o):
    """Every http(s) value in a config, skipping `_`-prefixed keys (the
    `_not_included` lists and the notes)."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k.startswith("_"):
                continue
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                yield o.get("name") or "?", v
            else:
                yield from _walk_urls(v)
    elif isinstance(o, list):
        for x in o:
            yield from _walk_urls(x)


def _configured_requests():
    """[(file, type, name, method, url)] for every configured source."""
    out = []
    for t, (fname, _key) in CONFIG.items():
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            continue
        for e in _entries(fname, json.load(open(path, encoding="utf-8"))):
            if isinstance(e, dict):
                for method, url in _ingest_requests(t, e):
                    out.append((fname, t, e.get("name") or "?", method, url))
    for fname in _ROBOTS_EXTRA:
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            continue
        t = fname.replace("_sources.json", "")
        for name, url in _walk_urls(json.load(open(path, encoding="utf-8"))):
            out.append((fname, t, name, "GET", url))
    return out


def cmd_robots(workers=16):
    """REPORT ONLY: which configured requests their host's robots.txt refuses.

    A publisher's pages and feeds are listed as DISALLOWED. A documented
    public API is listed apart: robots.txt does not bind it
    (DOCUMENTED_API_TYPES), but the line should stay visible.

    Changes nothing - no config, no ledger. What to do about a refused source is
    the owner's call (see the parkrun `_decision` for what that looks like), and
    this is the evidence for it. Crawl-delay is reported too: a host that asks
    for one and serves several paced feeds with none configured is fetched
    back to back.
    """
    reqs = _configured_requests()
    robots = robots_txt.Robots(_session())
    robots.prefetch([u for *_x, u in reqs], workers=workers)
    by_status, refused, paced, api_would = {}, {}, {}, {}
    for fname, t, name, method, url in reqs:
        ans = robots.check(url)
        kind = ("disallowed" if ans["allowed"] is False and ans["status"] not in ("unreachable",)
                else "unreachable" if ans["status"] == "unreachable"
                else "challenged" if ans["allowed"] is None
                else "allowed")
        origin = robots_txt.origin_of(url)
        if t in DOCUMENTED_API_TYPES:
            # Not bound by robots.txt (see DOCUMENTED_API_TYPES), and reported
            # apart so that line stays visible rather than silently applied.
            if kind == "disallowed":
                api_would.setdefault((origin, ans["rule"]), []).append((fname, name, method, url))
            kind = "documented-api"
        by_status[kind] = by_status.get(kind, 0) + 1
        if kind == "disallowed":
            refused.setdefault((origin, ans["rule"]), []).append((fname, name, method, url))
        elif kind == "allowed" and ans.get("crawl_delay") and t in _PACED:
            paced.setdefault(origin, {"asks": ans["crawl_delay"], "n": 0, "set": 0})
            paced[origin]["n"] += 1
    # which paced requests already carry a crawl_delay in their config
    for t in _PACED:
        fname = CONFIG[t][0]
        for e in _entries(fname, json.load(open(os.path.join(HERE, fname), encoding="utf-8"))):
            if isinstance(e, dict) and e.get("crawl_delay"):
                for _m, url in _ingest_requests(t, e):
                    o = robots_txt.origin_of(url)
                    if o in paced:
                        paced[o]["set"] += 1
    print(f"robots.txt audit: {len(reqs)} configured requests across "
          f"{len({robots_txt.origin_of(u) for *_x, u in reqs})} origins "
          f"({robots.retried} re-asked one at a time after the parallel pass, "
          f"{robots.recovered} of them answered)")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))
    n = sum(len(v) for v in refused.values())
    print(f"\nDISALLOWED for our User-Agent, a publisher's own pages and feeds: {n} request(s) "
          f"on {len({o for o, _r in refused})} origin(s)")
    for (origin, rule), rows in sorted(refused.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        files = ", ".join(sorted({f for f, *_x in rows}))
        print(f"  {len(rows):3}  {origin}  [{rule}]  {files}")
        for _f, name, method, url in rows[:3]:
            print(f"         {name[:48]}  {method} {robots_txt.request_path(url)[:70]}")
        if len(rows) > 3:
            print(f"         ... and {len(rows) - 3} more")
    n_api = sum(len(v) for v in api_would.values())
    print(f"\nDOCUMENTED APIs whose host's robots.txt disallows the path: {n_api} request(s) "
          f"on {len({o for o, _r in api_would})} origin(s). Not bound by it; their usage policy is.")
    for (origin, rule), rows in sorted(api_would.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  {len(rows):3}  {origin}  [{rule}]  {', '.join(sorted({f for f, *_x in rows}))}")
    unpaced = {o: v for o, v in paced.items() if v["n"] > 1 and not v["set"]}
    print(f"\nUNPACED: {len(unpaced)} host(s) ask for a Crawl-delay, serve several ics/tribe "
          f"requests, and no config on them carries `crawl_delay`")
    for o, v in sorted(unpaced.items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {o}  asks {v['asks']:g}s  {v['n']} requests")
    return 0


def _file_indent(text, default=1):
    """The indent width a JSON file is already stored with."""
    m = re.search(r"^\{?\n( +)\S", text) or re.search(r"^\[\n( +)\S", text)
    return len(m.group(1)) if m else default


def _held(e):
    """Why `merge` may not write this verified entry by itself, or None.

    A DISCOVERED GOOGLE CALENDAR WHOSE ID IS A PERSONAL ADDRESS. A personal
    calendar's id is its owner's email address (`<name>@gmail.com`); the repo
    is public and its rule is that a personal address is never committed, so
    a discovery may find, propose and verify one but not write it into a
    config: the owner decides. 51 of the 172 configured Google calendars are
    @gmail.com (2026-10-12), 40 of them found through a community centre, and
    page_ids now also reads the id a Simple Calendar page's event links
    carry: the one such page measured (2026-10-12) named a @gmail.com
    calendar. Only a DISCOVERED entry (it carries `_found`) is held; one a
    person curated and merges by hand passes.

    HELD MEANS THE ADDRESS IS NOWHERE PUBLIC. Not in a config, and not in the
    ledger either, which is committed (verify writes a held entry's row under
    _held_key, a hash, with status `held` and the venue's homepage), nor in
    the step summary, which is public on this repo (merge and verify print the
    name, never the URL). The owner's list is `catalog_curate.py ledger
    --held`; the walk does not propose a calendar already on it. In today's
    configs 40 of the 110 Google calendars found through a community centre
    and 4 of the 15 found through a library are @gmail.com (2026-10-12), so
    about a third of the centre Google calendars discovery finds will wait for
    the owner.
    """
    if not e.get("_found") or e.get("type") != "ics":
        return None
    cid = mapsee_gcal.calendar_id(e.get("url") or "")
    if cid and mapsee_gcal.personal_address(cid):
        return "its calendar id is a personal address; the owner decides"
    return None


def _held_key(e):
    """The ledger key of a held entry: a hash of its calendar id, so the row
    can be found again without the address being written down."""
    cid = (mapsee_gcal.calendar_id(e.get("url") or "") or "").strip().lower()
    return "held:" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:16]


def cmd_merge(path):
    verified = json.load(open(path, encoding="utf-8"))
    chipublib_configured = _chipublib_configured()
    declined = _declined_keys()
    by_type = {}
    for e in verified:
        if _entry_keys(e) & declined:
            # The name only, like [HOLD]: this line goes to the step summary.
            print(f"[SKIP] {e.get('type')} {e.get('name', '?')[:50]}: declined on the record "
                  f"(_not_included)")
            continue
        why = _held(e)
        if why:
            # The name only: this line goes to the public step summary.
            print(f"[HOLD] {e.get('type')} {e.get('name', '?')[:50]}: {why} "
                  f"(catalog_curate.py ledger --held)")
            continue
        by_type.setdefault(e.get("type"), []).append(e)
    for t, adds in by_type.items():
        if t not in CONFIG:
            print(f"[SKIP] unknown type {t!r}")
            continue
        fname, key = CONFIG[t]
        fpath = os.path.join(HERE, fname)
        fpath_text = open(fpath, encoding="utf-8").read()
        doc = json.loads(fpath_text)
        cur = _entries(fname, doc)
        seen = set()
        for c in cur:
            v = c.get(key) or (_ods_url(c) if t == "ods" else None)
            for one in (v if isinstance(v, list) else [v]):   # jsonld's `listing`
                if one:
                    seen.add(_canon(one))
        added = 0
        for e in adds:
            if _blocked_cpl_opendata(e, chipublib_configured):
                print("[SKIP] Chicago Public Library OpenData duplicate; BiblioCommons chipublib is configured")
                continue
            entry = {k: v for k, v in e.items() if k != "type"}
            if t == "ods":
                entry["url"] = _ods_url(entry)     # derived key, kept for dedup
            kv = entry.get(key)
            ck = _canon(kv[0] if isinstance(kv, list) and kv else kv)
            if not ck or ck in seen:
                continue
            cur.append(entry)
            seen.add(ck)
            added += 1
        out_doc = doc if isinstance(doc, dict) else cur   # nested files keep their wrapper
        # Write it back the way it was written. These files are stored at
        # indent=1 and this dumped them at 2, so adding ONE source to
        # ics_sources.json produced a 3,653-line diff — every line of a 260-entry
        # file re-indented around it. A merge nobody can read is a merge nobody
        # checks, and the point of this tool is that each addition is inspectable.
        json.dump(out_doc, open(fpath, "w", encoding="utf-8"),
                  indent=_file_indent(fpath_text), ensure_ascii=False)
        json.load(open(fpath, encoding="utf-8"))  # validate it still parses
        print(f"{fname}: +{added} -> {len(cur)} total")
    return 0


# --- re-applying a finished run onto a main that moved ---------------------
# A curation run holds its checkout for 30-90 minutes and then pushes four
# files, so ANYTHING that lands on main in that window makes the push a
# non-fast-forward and the run dies at its very last step with every source it
# verified in the bin. Three runs have died that way (2026-08-20, 2026-08-27,
# 2026-09-02) and the two causes are both structural rather than unlucky:
#
#   * source-health.yml's audit writes curation_ledger.json, and the three
#     osm-*.yml cursor jobs write to main all day.
#   * a run QUEUED behind another one of these (Wednesday's gap sweep behind
#     the daily sweep) is stale before it starts. `concurrency` serialises the
#     two runs but actions/checkout still checks out github.sha — the tip as it
#     was when the run was CREATED — so on 2026-09-02 the second run checked
#     out at 08:40:14 a commit that the first had superseded at 08:40:05.
#
# osm-food.yml already solved this shape and its comment says why re-reading
# beats rebasing: "recomputing the merge against whatever main says NOW is
# always correct and can never conflict. Each attempt starts from the newest
# base." Same argument here; the arithmetic is per FILE rather than per area.
#
#   curation_ledger.json   url -> probe record. Union, and the more recent
#                          `checked` wins a shared URL. Not hypothetical: the
#                          weekly audit writes this file too.
#   curation_cursor.json   {backend: {query: offset}}. OURS wins per key —
#                          only a curation run writes it and `concurrency`
#                          means no other one can be in flight, so whatever is
#                          on main is the base this run advanced from.
#   coverage_history.jsonl one line per run, append-only. Append ours.
#   *_sources.json         NOT here. `merge` already dedups by canonical URL
#                          against whatever the config holds now, so it is
#                          idempotent and the workflow just re-runs it.
#
# This lives here rather than as a heredoc in the workflow for the reason
# cmd_coverage_delta already records — a `python - <<'PY'` body sits at column
# 0 and terminates the YAML block scalar — and for a second one: a merge rule
# that silently drops half a ledger is exactly the kind of thing that needs a
# test, and test_curate_reapply.py can only import what is in a module.
COVERAGE_HISTORY_FILE = os.path.join(HERE, "coverage_history.jsonl")


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_ledger_at(path):
    """A ledger from somewhere other than the repo, through the SAME loader.

    _load_ledger re-keys every row through _canon; a second reader that did not
    would merge a raw-URL key beside its canonical twin and quietly double the
    file. One canonicaliser, borrowed by pointing the module global at the
    snapshot for the length of the call.
    """
    global LEDGER_FILE
    keep, LEDGER_FILE = LEDGER_FILE, path
    try:
        return _load_ledger()
    finally:
        LEDGER_FILE = keep


def _merge_ledgers(base, ours):
    """Union of two ledgers; the more recent probe wins a shared URL.

    `>=` rather than `>` on the date: two probes of one URL on the same day are
    this run and something that landed while it ran, and this run finished
    later, so its answer is the newer observation. A missing `checked` reads as
    0 — the same default _dead_recently uses — so a malformed row can never
    outrank a dated one.
    """
    out = dict(base)
    for k, v in ours.items():
        cur = out.get(k)
        if cur is None or int(v.get("checked", 0) or 0) >= int(cur.get("checked", 0) or 0):
            out[k] = v
    return out


def _merge_cursors(base, ours):
    """{backend: {query: offset}} merged ONE level down, ours winning.

    A top-level update would replace a whole backend's dict, so a run that swept
    socrata alone would drop every ckan and osm position on main. The nesting is
    the whole point of the merge.
    """
    out = {b: (dict(q) if isinstance(q, dict) else q) for b, q in base.items()}
    for b, q in ours.items():
        if isinstance(q, dict) and isinstance(out.get(b), dict):
            out[b].update(q)
        else:
            out[b] = q
    return out


def cmd_reapply(outdir):
    """Re-apply this run's ledger, cursor and coverage line onto the tree as it is.

    Called after `git reset --hard origin/main`, so the three files on disk are
    whatever main holds now and the snapshot in `outdir` is what this run
    computed. Every part is optional: a run that swept nothing still has a
    coverage line worth appending, and a step that died before writing a
    snapshot must not take the rest of the commit with it.
    """
    if not os.path.isdir(outdir):
        # LOUD, and not fatal. This runs inside the commit loop under `bash -e`,
        # so returning non-zero here would abandon the verified sources too —
        # which is the exact failure the loop exists to stop. A sweep cancelled
        # before it wrote a snapshot has nothing to re-apply and everything to
        # merge.
        print(f"::warning::no snapshot directory {outdir!r} - "
              "nothing to re-apply; merging verified sources only")
        return 0

    led_p = os.path.join(outdir, "curation_ledger.json")
    if os.path.exists(led_p):
        # _save_ledger writes a temp file and renames it (since 2026-10-29), so
        # the ledger it leaves is always whole; the SNAPSHOT is a `cp` of it,
        # and a step cancelled mid-copy can still leave half a file here.
        # Losing this run's probes then costs a repeat, where raising costs the
        # cursor, the coverage line and every verified source in the same step.
        try:
            base, ours = _load_ledger(), _load_ledger_at(led_p)
        except Exception as exc:  # noqa: BLE001
            print(f"ledger:  {led_p} unreadable ({exc}) - left as main has it")
        else:
            merged = _merge_ledgers(base, ours)
            _save_ledger(merged)
            print(f"ledger:  {len(base)} on main + {len(ours)} ours "
                  f"-> {len(merged)} (+{len(merged) - len(base)} new)")
    else:
        print(f"ledger:  no {led_p} - left as main has it")

    cur_p = os.path.join(outdir, "curation_cursor.json")
    if os.path.exists(cur_p):
        base = _load_cursor()
        try:
            ours = _read_json(cur_p)
        except Exception as exc:  # noqa: BLE001
            print(f"cursor:  {cur_p} unreadable ({exc}) - left as main has it")
        else:
            merged = _merge_cursors(base, ours)
            _save_cursor(merged)
            moved = sum(1 for b, q in ours.items() if isinstance(q, dict)
                        for k, v in q.items() if base.get(b, {}).get(k) != v)
            print(f"cursor:  {len(merged)} backend(s), {moved} position(s) advanced")
    else:
        print(f"cursor:  no {cur_p} - left as main has it")

    cov_p = os.path.join(outdir, "coverage-after.json")
    if os.path.exists(cov_p):
        # Re-serialised the way cmd_coverage_json prints it, so the appended
        # line is byte-identical to the one `cat coverage-after.json >>` wrote
        # before this and the file stays one snapshot per line.
        line = json.dumps(_read_json(cov_p), sort_keys=True)
        raw = ""
        if os.path.exists(COVERAGE_HISTORY_FILE):
            raw = open(COVERAGE_HISTORY_FILE, encoding="utf-8").read()
        if raw.splitlines()[-1:] == [line]:
            # Only reachable if a push half-succeeded; cheaper to check than to
            # explain a duplicated snapshot to whoever reads the trend later.
            print("history: this run's line is already the last one")
        else:
            with open(COVERAGE_HISTORY_FILE, "a", encoding="utf-8") as fh:
                if raw and not raw.endswith("\n"):
                    fh.write("\n")
                fh.write(line + "\n")
            print(f"history: appended 1 line -> {len(raw.splitlines()) + 1} total")
    else:
        print(f"history: no {cov_p} - nothing to append")
    return 0


# THE WEEKLY AUDIT IS A ROTATION, BECAUSE IT NO LONGER FITS ITS JOB. On
# 2026-10-04 it probed 3,240 configured feeds in 94 minutes. On 2026-10-25
# (run 40179210416) the configs held 5,568 entries in the files it reached and
# the probe step ran 09:05:35-13:06:03Z: localist 57 in 1.3 min, ics 2,931 in
# 108.3, tribe 1,501 in 110.2, and 311 of jsonld's 785 when the job's
# 240-minute cap cancelled it. 5,094 probes, and nothing kept: the snapshot was
# copied inside the cancelled step and the commit step was skipped. Where the
# time went is not where the probes are: the median probe took 0.69 s, but 67
# probes over 60 s (1.3%) took 133.4 of the 240 minutes, each a connect or read
# timeout asked three times (VERIFY_RETRIES) at 60 s apiece.
#
# So a run is a BUDGET and an ORDER. `--max-minutes` starts no probe after the
# deadline (the ledger is saved at once, and the ones running get AUDIT_GRACE_S
# to finish and be recorded); every entry goes in the
# order of its ledger row's `checked`, never-checked first, across all the
# config files, so a run the budget cuts short is where the next one starts
# instead of the head of ics_sources.json again; and the ledger is saved every
# AUDIT_SAVE_EVERY probes and on the way out, however the way out happens. An
# entry no probe reached keeps its old row: a run never writes a verdict about
# a feed it did not ask.
AUDIT_SAVE_EVERY = 200

# Probes at once. Never two on one host (the ingest adapters never ask one host
# twice at once either, and a CivicPlus city or a Google calendar API host is a
# long same-host queue), so the stalls above wait side by side instead of end
# to end. 1 is the old one-at-a-time loop. See the workflow for what it runs.
AUDIT_WORKERS = 1


def _audit_plan(led):
    """Every configured entry, the one the ledger checked longest ago first.

    [(checked, seq, type, fname, key, entry)]; a row never checked sorts as 0,
    and a tie keeps CONFIG and file order. A config file that is missing or
    does not parse is reported and left out, not a reason to probe nothing.

    A TYPE WITH NO VERIFIER IS LEFT OUT, SAID ONCE, ITS ROWS UNTOUCHED.
    `mylisting` is in CONFIG (so discovery knows what is configured) and in no
    VERIFIERS entry. Planned anyway, every probe raised KeyError inside the
    worker and was recorded `fail` "KeyError: 'mylisting'" (review of
    2026-10-29), and FEED_DOWN, which reads mylisting_sources.json, then
    reported Bainbridge Island daily while production kept 556 of 592
    occurrences from it on 10-28. A missing verifier is not a verdict.
    """
    plan = []
    for t, (fname, key) in CONFIG.items():
        if t not in VERIFIERS:
            print(f"audit: no verifier for `{t}` ({fname}): its entries are not probed "
                  f"and keep their ledger rows", flush=True)
            continue
        fpath = os.path.join(HERE, fname)
        try:
            # _entries(), not a bare json.load: ods_sources.json nests its list
            # under "sources", so iterating the raw object walks the dict KEYS.
            cur = _entries(fname, json.load(open(fpath, encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"::warning::audit: {fname} not read ({type(exc).__name__}: "
                  f"{str(exc)[:60]}) - its entries keep their old ledger rows")
            continue
        for e in cur:
            if not isinstance(e, dict):
                continue
            rec = led.get(_canon(_key_of(dict(e, type=t)))) or {}
            try:
                checked = int(rec.get("checked") or 0)
            except (TypeError, ValueError):
                checked = 0
            plan.append((checked, len(plan), t, fname, key, e))
    plan.sort(key=lambda p: (p[0], p[1]))
    return plan


# CRAWL-DELAY IN THE AUDIT. The audit asked a host as fast as its probes
# came back: a host's next probe started the moment the last one ended, and
# the CivicPlus list read went out right after the feed. CivicPlus's stock
# robots.txt asks `Crawl-delay: 10` (35 of the 35 sampled hosts' files that
# could be read, 2026-10-29), LibCal's 10 (leeds, smu-sg), and only 15 of the
# 1,270 configured CivicPlus feeds carry `crawl_delay`. Now every request the
# audit makes to a host waits out that host's delay since its last request:
# the larger of what its robots.txt asks and what its entries' `crawl_delay`
# asks (_AuditPace). Between probes nobody sleeps: the scheduler starts a
# host's next probe only once the host is ready, and works on other hosts
# meanwhile. Inside a probe (a retry, the CivicPlus list, The Events Calendar's
# REST test, a jsonld follow-up page) the worker waits, but never longer than
# AUDIT_MAX_CRAWL_DELAY_S: a request that would need longer is not sent, rather
# than asking sooner than the host said, and the probe decides nothing: the
# entry keeps its row (_audit_each). 30 s
# is the walk's cap too (OSM_MAX_CRAWL_DELAY_S) and three times the stock
# CivicPlus and LibCal value; a worker asleep for a 3,600 s delay would carry
# the run past its deadline. A documented public API (DOCUMENTED_API_TYPES)
# is not asked for robots.txt, so only an entry's own `crawl_delay` paces it.
AUDIT_MAX_CRAWL_DELAY_S = 30.0

# WHEN THE BUDGET RUNS OUT, THE LEDGER IS SAVED FIRST AND THE STRAGGLERS GET
# A BOUNDED GRACE. The wait on probes still running had no timeout, and a
# server that trickles bytes holds a probe past `timeout=60` (it bounds each
# read, not the response): on 2026-10-25 the runner killed python 0.25 s after
# "The operation was canceled", taking up to 199 recorded probes and both
# summary lines with it. Now the ledger is written the moment the deadline
# cuts, then the running probes get AUDIT_GRACE_S to finish (three tries of a
# 60 s timeout plus a list read is under 5 minutes), and any still running are
# abandoned unrecorded: their entries keep their old rows and go first next
# run. 200 + 5 minutes sits inside the probe step's 215. The workers are
# daemon threads, so an abandoned one cannot hold the interpreter open.
AUDIT_GRACE_S = 300.0

# How long the scheduler blocks at most before it looks at the clock again.
AUDIT_POLL_S = 1.0


class AuditCrawlDelay(requests.exceptions.RequestException):
    """A request that would wait out more than AUDIT_MAX_CRAWL_DELAY_S: not sent."""


class _AuditPace:
    """When each host may next be asked, for the audit: its last request plus
    its Crawl-delay (robots.txt or the entries', the larger). Shared by every
    worker; robots.txt is read once per origin per run, by whichever worker
    needs it first, and robots.txt reads are not themselves paced."""

    def __init__(self, entry_delays=None, cap=AUDIT_MAX_CRAWL_DELAY_S, robots_timeout=15.0):
        import threading
        self.entry = dict(entry_delays or {})        # host -> the configs' crawl_delay
        self.cap = float(cap)
        self.robots_timeout = robots_timeout
        self._lock = threading.Lock()
        self._last = {}                               # host -> time.time() of its last request
        self._asked = {}                              # host -> robots.txt Crawl-delay (0 if none)
        self._robots = {}                             # origin -> [Lock, Robots | None]
        self.robots_read = self.waits = 0
        self.waited = 0.0

    def robots_check(self, raw, url):
        """robots.txt's answer for `url`, the file read once per origin."""
        import threading
        origin = robots_txt.origin_of(url)
        with self._lock:
            slot = self._robots.setdefault(origin, [threading.Lock(), None])
        with slot[0]:
            if slot[1] is None:
                slot[1] = robots_txt.Robots(raw, timeout=self.robots_timeout)
                with self._lock:
                    self.robots_read += 1
            ans = slot[1].check(url)
        try:
            asked = float(ans.get("crawl_delay") or 0)
        except (TypeError, ValueError):
            asked = 0.0
        host = _probe_host(url)
        with self._lock:
            self._asked[host] = max(self._asked.get(host, 0.0), asked)
        return ans

    def knows(self, host):
        with self._lock:
            return host in self._asked

    def delay(self, host):
        with self._lock:
            return max(self.entry.get(host, 0.0), self._asked.get(host, 0.0))

    def ready_at(self, host):
        """The earliest time.time() at which `host` may be asked again."""
        with self._lock:
            last = self._last.get(host)
        return 0.0 if last is None else last + self.delay(host)

    def before(self, raw, url, exempt=False):
        """Called before every request: wait out the host's delay, then mark it."""
        host = _probe_host(url)
        with self._lock:
            seen = host in self._last
        if seen and not exempt and not self.knows(host):
            self.robots_check(raw, url)               # a second request: now it matters
        wait = self.ready_at(host) - time.time()
        if wait > self.cap:
            raise AuditCrawlDelay(f"{host} asks Crawl-delay {self.delay(host):g} s; "
                                  f"over the {self.cap:g} s a probe waits")
        if wait > 0:
            time.sleep(wait)
            with self._lock:
                self.waits += 1
                self.waited += wait
        with self._lock:
            self._last[host] = time.time()


class _AuditSession:
    """A worker's session as the verifiers see it: every GET and POST goes
    through the run's _AuditPace first. `raw` is the session underneath, which
    robots.txt is read with. One per probe: `undecided` is set when the pacing
    refused to send one of its requests (AuditCrawlDelay), so a probe whose
    verifier swallowed that refusal into a note is still known undecided."""

    def __init__(self, raw, pace, exempt=False):
        self.raw, self.pace, self.exempt = raw, pace, exempt
        self.undecided = None

    @property
    def headers(self):
        return getattr(self.raw, "headers", {})

    def _before(self, url):
        try:
            self.pace.before(self.raw, url, self.exempt)
        except AuditCrawlDelay as ex:
            self.undecided = str(ex)
            raise

    def get(self, url, **kw):
        self._before(url)
        return self.raw.get(url, **kw)

    def post(self, url, **kw):
        self._before(url)
        return self.raw.post(url, **kw)


def _audit_host(t, e):
    """The host a probe of `e` asks: what the probes are serialised on."""
    reqs = _ingest_requests(t, e)
    return _probe_host(reqs[0][1]) if reqs else str(_key_of(e))


def _entry_delays(plan):
    """host -> the largest `crawl_delay` any configured entry on it asks."""
    out = {}
    for item in plan:
        t, e = item[2], item[5]
        try:
            d = float(e.get("crawl_delay") or 0)
        except (TypeError, ValueError):
            d = 0.0
        if d > 0:
            h = _audit_host(t, dict(e, type=t))
            out[h] = max(out.get(h, 0.0), d)
    return out


def _audit_probe(s, t, e):
    # Retried, unlike verification of a fresh candidate: a candidate that times
    # out costs nothing to skip and can be proposed again next week, but a
    # CONFIGURED feed marked dead here is parked for 90 days and reported as a
    # regression. The two are not the same bet.
    return _verify_with_retry(VERIFIERS[t], s, e)


# THE AUDIT ASKS ROBOTS.TXT BEFORE AN ENTRY'S FIRST REQUEST, AND OBEYS IT.
# It read the file for the Crawl-delay alone, then fetched a feed the file
# disallows and filed it ok, empty or fail, never `refused` (review of
# 2026-10-29; round 2 found two such refusals by hand: OEC Korfbal's
# `Disallow: /`, Mathallen's `/*?*ical=`). Now an explicit Disallow of a
# request the ingest adapter makes (_ingest_requests, as verify's
# _robots_gate asks) is recorded `refused` with the rule and nothing is
# fetched, so a host that starts refusing us is found the next Sunday. A
# robots.txt that cannot be read (unreachable, a challenge) is probed as
# before: not being able to read the file is not a verdict about the feed. A
# documented public API is not asked (DOCUMENTED_API_TYPES). A Google calendar
# with no Calendar API key is not probed at all and keeps its row, as verify
# skips it: robots.txt refuses its export, and the key is a setting, not a
# verdict about the calendar.
def _audit_robots(pace, raw, t, e):
    """None to probe; (False, Verdict 'refused') on an explicit Disallow;
    (None, why) when the entry is not decided this run."""
    if t == "ics" and mapsee_gcal.calendar_id(e.get("url") or "") and not mapsee_gcal.api_key():
        return None, (f"not decided: a Google calendar needs {mapsee_gcal.KEY_ENV} "
                      f"(its iCal export is refused by robots.txt)")
    for method, url in _ingest_requests(t, e):
        ans = pace.robots_check(raw, url)
        if ans["allowed"] is False and ans["status"] == "ok":
            return False, Verdict(f"robots.txt: {ans['rule']} "
                                  f"({method} {robots_txt.request_path(url)[:60]})", "refused")
    return None


def _daemon_pool(n, fn):
    """`n` daemon worker threads running fn(*args): (submit, close).

    Daemon, unlike ThreadPoolExecutor's, whose workers the interpreter joins
    at exit: a probe abandoned after the grace would otherwise hold python
    open until the step's timeout killed it, which is the 2026-10-25 failure.
    """
    import concurrent.futures as cf
    import queue
    import threading
    jobs = queue.Queue()

    def run():
        while True:
            got = jobs.get()
            if got is None:
                return
            fut, args = got
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as exc:                          # noqa: BLE001
                fut.set_exception(exc)

    for i in range(n):
        threading.Thread(target=run, name=f"audit-{i}", daemon=True).start()

    def submit(*args):
        fut = cf.Future()
        jobs.put((fut, args))
        return fut

    def close():
        for _ in range(n):
            jobs.put(None)

    return submit, close


def _audit_each(plan, deadline, workers, record, pace=None, on_cut=None, grace=None,
                undecided=None):
    """Probe `plan` in order, `workers` at once, never two on one host and
    never a host before its Crawl-delay is out (`pace`); record(item, ok,
    note) on THIS thread as each one comes back.

    Returns (cut, abandoned): cut is True when the deadline stopped it before
    the end; abandoned the items still running `grace` seconds after it,
    left unrecorded. The deadline is checked before every probe is started;
    when it cuts, on_cut() runs first (the caller saves the ledger), then the
    probes already running get the grace. A worker's exception is the probe's
    note, never the run's end; an interrupt is re-raised here.

    A PROBE THE PACING STOPPED IS NOT DECIDED. When a request inside it would
    have to wait out more than AUDIT_MAX_CRAWL_DELAY_S it is not sent
    (AuditCrawlDelay), and whatever the verifier then said (a CivicPlus list
    "unreadable", "REST unreachable", a retry's "AuditCrawlDelay: ..." in place
    of the 503 before it) was decided by no server. Such a probe, and one
    _audit_robots will not start, goes to undecided(item, note), never to
    record: the entry keeps its old row, like an abandoned probe.
    """
    import concurrent.futures as cf
    import threading
    grace = AUDIT_GRACE_S if grace is None else float(grace)
    local = threading.local()
    todo = []
    for item in plan:
        t = item[2]
        todo.append((item, _audit_host(t, dict(item[5], type=t))))

    def probe(t, e, host):
        raw = getattr(local, "s", None)
        if raw is None:
            raw = local.s = _session()
        if pace is None:
            return _audit_probe(raw, t, e)
        exempt = t in DOCUMENTED_API_TYPES
        if not exempt:
            # Read before the entry's first request, every host: its Disallow
            # decides whether to ask at all, and its Crawl-delay when the
            # host's next probe may start.
            gate = _audit_robots(pace, raw, t, e)
            if gate is not None:
                return gate
        s = _AuditSession(raw, pace, exempt)
        ok, note = _audit_probe(s, t, e)
        if not ok and s.undecided:
            return None, f"not decided: {s.undecided}"
        return ok, note

    def ready(host, now):
        return pace is None or pace.ready_at(host) <= now

    submit, close = _daemon_pool(max(1, int(workers)), probe)
    inflight, busy = {}, set()
    cut, grace_end = False, None
    try:
        while todo or inflight:
            now = time.time()
            if not cut and deadline and now >= deadline:
                cut = True
                if on_cut:
                    on_cut(len(inflight))
                grace_end = time.time() + grace
            while not cut and todo and len(inflight) < workers:
                pick = next((i for i, (_it, h) in enumerate(todo)
                             if h not in busy and ready(h, now)), None)
                if pick is None:
                    break                 # every waiting host is mid-probe or in its delay
                item, host = todo.pop(pick)
                busy.add(host)
                t = item[2]
                inflight[submit(t, dict(item[5], type=t), host)] = (item, host)
            if cut and not inflight:
                break
            if cut:
                timeout = grace_end - time.time()
                if timeout <= 0:
                    break                 # the grace is spent: abandon what is left
            else:
                timeout = AUDIT_POLL_S
                if deadline:
                    timeout = min(timeout, max(0.0, deadline - now))
                if pace is not None and todo and len(inflight) < workers:
                    waiting = [pace.ready_at(h) for _it, h in todo if h not in busy]
                    if waiting:
                        timeout = min(timeout, max(0.0, min(waiting) - now))
            if not inflight:
                time.sleep(timeout)
                continue
            done, _ = cf.wait(list(inflight), timeout=min(timeout, AUDIT_POLL_S),
                              return_when=cf.FIRST_COMPLETED)
            for fut in [f for f in inflight if f in done]:
                item, host = inflight.pop(fut)
                busy.discard(host)
                try:
                    ok, note = fut.result()
                except Exception as exc:                          # noqa: BLE001
                    ok, note = False, f"{type(exc).__name__}: {str(exc)[:70]}"
                if ok is None:
                    if undecided:
                        undecided(item, note)
                    continue
                record(item, ok, note)
    finally:
        close()
    return cut, [item for item, _h in inflight.values()]


def cmd_audit(max_minutes=0.0, workers=None, save_every=AUDIT_SAVE_EVERY):
    led = _load_ledger()
    plan = _audit_plan(led)
    total = len(plan)
    workers = max(1, int(workers or AUDIT_WORKERS))
    deadline = time.time() + float(max_minutes) * 60 if max_minutes else None
    never = sum(1 for p in plan if not p[0])
    print(f"audit: {total} configured entries, the oldest ledger check first "
          f"({never} never checked; oldest {(plan[0][0] or 'never') if plan else '-'}), "
          f"{workers} at once"
          + (f", a {float(max_minutes):g}-minute budget" if deadline else ""), flush=True)
    n = {"probed": 0, "fail": 0, "empty": 0, "refused": 0, "undecided": 0}

    def shown_of(item):
        # jsonld's key, `listing`, is a LIST. Slicing it and formatting the
        # slice with :60 raised TypeError on the first jsonld entry, and the one
        # save sat after the loop, so every Sunday since mid-September threw the
        # whole audit away: 2026-10-04 probed 3,240 feeds in 94 minutes (211
        # broken) and the commit step found "ledger unchanged".
        shown = item[5].get(item[4], "?")
        return (shown[0] if shown else "?") if isinstance(shown, list) else shown

    def record(item, ok, note):
        t, e = item[2], item[5]
        status = _status_for(ok, note)
        if status in ("fail", "empty", "refused"):
            n[status] += 1
        lkey = _canon(_key_of(dict(e, type=t)))
        if lkey:                   # an entry with no key has no row to write
            led[lkey] = {"type": t, "name": e.get("name"), "status": status,
                         "reason": str(note), "checked": _today_int()}
        mark = {"ok": "OK ", "empty": "-- ", "fail": "XX ", "refused": "NO "}[status]
        print(f"[{mark}] {str(shown_of(item))[:60]:60} {note}", flush=True)
        n["probed"] += 1
        if n["probed"] % max(1, int(save_every)) == 0:
            _save_ledger(led)

    def undecided(item, note):
        # No row is written: the entry keeps the one it had (see _audit_each).
        n["undecided"] += 1
        print(f"[.. ] {str(shown_of(item))[:60]:60} {note}", flush=True)

    def on_cut(running):
        # The ledger first, then the wait: whatever the stragglers do, every
        # probe that came back is on disk before anything else can go wrong.
        _save_ledger(led)
        print(f"audit: the {float(max_minutes):g}-minute budget is spent; ledger saved "
              f"({n['probed']} probed), waiting up to {AUDIT_GRACE_S:g} s for the "
              f"{running} still running", flush=True)

    pace = _AuditPace(_entry_delays(plan))
    cut, abandoned, how = False, [], "interrupted"
    try:
        cut, abandoned = _audit_each(plan, deadline, workers, record, pace=pace, on_cut=on_cut,
                                     undecided=undecided)
        how = "budget" if cut else "done"
    finally:
        _save_ledger(led)          # whatever ended the loop, what it probed is kept
        # In the finally too: the workflow reads these two lines, and an
        # interrupted run is the one whose log most needs them.
        print(f"\n{n['fail']} entries are BROKEN (unreachable, or no longer a feed).")
        print(f"{n['empty']} entries parse fine but have nothing upcoming — kept, not a regression.")
        if n["refused"]:
            print(f"{n['refused']} entries robots.txt disallows: recorded `refused`, "
                  f"nothing it disallows fetched.")
        if n["undecided"]:
            print(f"{n['undecided']} probe(s) not decided (a Crawl-delay over "
                  f"{AUDIT_MAX_CRAWL_DELAY_S:g} s inside the probe, or a Google calendar "
                  f"without {mapsee_gcal.KEY_ENV}): their rows are unchanged.")
        if abandoned:
            print(f"{len(abandoned)} probe(s) still running {AUDIT_GRACE_S:g} s after the budget "
                  f"were abandoned unrecorded: "
                  + ", ".join(str(_canon(_key_of(dict(i[5], type=i[2]))))[:60] for i in abandoned[:5]))
        if pace.waits or pace.robots_read:
            print(f"crawl-delay: {pace.waits} wait(s) inside probes, {pace.waited:.0f} s in all; "
                  f"robots.txt read for {pace.robots_read} origin(s)")
        reached = n["probed"] + n["undecided"]
        left = total - reached
        tail = {"budget": f"; stopped at the {float(max_minutes):g}-minute budget, and the next run "
                          f"starts with the {left} the ledger checked longest ago",
                "interrupted": f"; interrupted, and the next run starts with the {left} the "
                               f"ledger checked longest ago",
                "done": ""}[how]
        print(f"audit reached {reached} of {total} configured entries{tail}", flush=True)
    return 0


# WHAT AN `offsite:` FIND CAN AND CANNOT BECOME, and almost all of it is
# cannot — which is the useful half, because "we already ingest that platform"
# is the intuition this table exists to correct. Every line traces to an
# adapter's own measurement rather than to this file's guess:
#
#   eventbrite (46, the largest routable-LOOKING pile) is REFERENCE ONLY. The
#     `/o/<slug>-<id>` profile ids are a different id space from the
#     organization ids the API serves, so organizer-scoped fetching 404s across
#     the board — mapsee_ingest_eventbrite's header says so and its `organizers`
#     list is documented as "NOT fetched". Four `/o/` ids were harvested here
#     and NOT added for that reason.
#   ticketmaster (24) and meetup (2) are already swept nationally, by metro and
#     country, so a venue found this way is covered before it is configured.
#   axs (1) is behind a partner agreement this project does not hold.
#   facebook (158) and instagram (77) are structural dead ends: no API we may
#     read. Together they are two thirds of the tally.
#   humanitix (21) was ASSESSED, not assumed — see the OFFSITE_HOSTS note in
#     catalog_discover_osm for why a platform that looks perfectly ingestable is
#     not (no coordinates anywhere, and the only geocoder here is US Census).
#
# Which leaves ONE that routes, and only in one of its two shapes:
ROUTABLE_OFFSITE = {
    "dice.fm": ("dice_venue_sources.json — but only a /venue/<slug> link. "
                "An /event/ link names a night, not a room"),
    # A community centre's drop-ins on its city's booking platform (2026-10-03).
    "perfectmind.com": ("perfectmind_sources.json — a /BookMe4?widgetId=<uuid> link "
                        "names the widget; the tenant's org path is in the same URL"),
}
# The rest are kept in the report because the RANKED LIST is the point: it is
# the only measurement this repo has of what venues worldwide actually use, and
# it is what says which adapter would be worth writing next.
REFERENCE_OFFSITE = {
    "eventbrite": "reference only: /o/ ids are a different id space (see the adapter)",
    "ticketmaster": "already swept nationally by metro",
    "meetup.com": "already swept nationally by metro",
    "axs.com": "partner credentials this project does not hold",
    "facebook.com": "no API we may read",
    "fb.me": "no API we may read",
    "instagram.com": "no API we may read",
    "humanitix.com": "assessed 2026-08-30: no coordinates anywhere",
    # Recreation-booking platforms, each measured 2026-10-03 over 3-9 tenants
    # (docs/agents/platforms-probed.md). Kept for the tally of which platform
    # holds which centre's timetable - the letters go to whoever owns it.
    "activecommunities.com": ("ACTIVE's Terms of Use refuse automated retrieval "
                              "without written consent; letters drafted in "
                              "../mapsee/outreach/partners/activenet-permission.md"),
    "amilia.com": "a course catalogue: 8,358 of 10,906 rows (77%) paid courses",
    "recdesk.com": "a facility diary: 2.6% drop-in, 16% private bookings, no address",
    "myrec.com": "a facility diary: 7% drop-in, 29%+ bookings, no address",
    "rec1.com": "a course catalogue: 153 of 176 sessions (87%) paid",
    "myvscloud.com": "robots.txt refuses (WebTrac)",
    "capturepoint.com": "robots.txt refuses (CommunityPass)",
}


def _refresh_offsite(led, session, cap=60):
    """Re-probe the routable offsite entries that predate the link being kept.

    The link is only written by a probe, and the ledger's 90-day TTL means these
    venues are skipped until then — so 46 Eventbrite finds would sit unusable
    for three months over a one-line change made after they were recorded. This
    is the cheap half of a sweep: one GET per KNOWN venue, no Overpass, no
    discovery, and only for the platforms something can be done with.
    """
    import catalog_discover_osm as osm
    stale = [(k, v) for k, v in led.items()
             if isinstance(v, dict) and not v.get("offsite_url")
             and any(f"offsite:{h}" in (v.get("reason") or "")
                     for h in set(ROUTABLE_OFFSITE) | set(REFERENCE_OFFSITE))]
    if not stale:
        print("  every routable offsite find already has its link")
        return 0
    print(f"  re-probing {min(len(stale), cap)} of {len(stale)} routable finds "
          f"that predate the link being kept")
    got = 0
    for key, v in stale[:cap]:
        url = key if key.startswith("http") else f"https://{key}"
        try:
            f = osm.find_calendar(session, url)
        except Exception as exc:                                  # noqa: BLE001
            print(f"    {v.get('name','?')[:30]:30} {type(exc).__name__}")
            continue
        # A site that has since GROWN a calendar of its own is not an offsite
        # find any more, and saying so is the point of re-probing rather than
        # scraping the old page for a link.
        if (f.get("status") or "").startswith("ok"):
            print(f"    {v.get('name','?')[:30]:30} now has its OWN calendar "
                  f"({f.get('adapter')}) — left for the next sweep to propose")
            continue
        if f.get("offsite_url"):
            v["offsite_url"] = f["offsite_url"][:300]
            v["checked"] = _today_int()
            got += 1
            print(f"    {v.get('name','?')[:30]:30} {f['offsite_url'][:72]}")
    if got:
        _save_ledger(led)
    print(f"  {got} link(s) recorded")
    return got


def cmd_ledger_offsite(refresh=False):
    """The `offsite:` finds, grouped by platform, with the link that names them.

    An `offsite:` probe is a finished discovery wearing a failure's clothes: the
    venue HAS a calendar, it is on a platform, and for five of those platforms
    this repo already has the adapter. What was missing was never the venue, it
    was the id — and the id is in the link, which the probe used to throw away.
    This reads what it now keeps.
    """
    led = _load_ledger()
    if refresh:
        _refresh_offsite(led, _session())
    by_host = {}
    for key, v in led.items():
        if not isinstance(v, dict):
            continue
        reason = v.get("reason") or ""
        if "offsite:" not in reason:
            continue
        host = reason.split("offsite:", 1)[1].strip()
        by_host.setdefault(host, []).append((key, v))
    if not by_host:
        print("no offsite finds in the ledger")
        return 0
    total = sum(len(x) for x in by_host.values())
    print(f"{total} venues publish their events somewhere else, on "
          f"{len(by_host)} platforms")
    print()
    for host, hits in sorted(by_host.items(), key=lambda kv: -len(kv[1])):
        routable = ROUTABLE_OFFSITE.get(host) or REFERENCE_OFFSITE.get(host)
        with_url = [h for h in hits if h[1].get("offsite_url")]
        head = f"{len(hits):5}  {host}"
        if routable:
            head += f"   -> {routable}"
        print(head)
        print(f"       {len(with_url)} of them recorded the link; the rest were "
              f"probed before the link was kept and come round again on the "
              f"ledger's 90-day TTL")
        for key, v in with_url[:6]:
            print(f"         {v.get('name', '?')[:34]:34} {v['offsite_url'][:74]}")
    return 0


def cmd_ledger_held():
    """The feeds held for the owner. Mostly discovered calendars whose id is a
    personal address (see _held): the address is in none of these rows, each
    names the venue and its homepage, where the calendar can be read again.
    Also a verified feed held for a ruling the owner owes (its row is keyed by
    its URL, which carries no address, and its reason says what is owed)."""
    led = _load_ledger()
    rows = sorted(((k, v) for k, v in led.items()
                   if isinstance(v, dict) and v.get("status") == "held"),
                  key=lambda kv: (kv[1].get("metro") or "", kv[1].get("name") or ""))
    if not rows:
        print("nothing is held")
        return 0
    personal = [(k, v) for k, v in rows if str(k).startswith("held:")]
    ruling = [(k, v) for k, v in rows if not str(k).startswith("held:")]
    if personal:
        print(f"{len(personal)} discovered calendar(s) held for the owner: each one's id is "
              f"a personal address, which this public repo does not commit by itself")
        for _k, v in personal:
            print(f"  {str(v.get('name') or '?')[:40]:40} {str(v.get('metro') or '?')[:24]:24} "
                  f"{v.get('home') or '?'}  ({v.get('checked')}: {str(v.get('reason') or '')[:50]})")
        print("To keep one: read the calendar on the venue's site, decide that its address may "
              "be published, and add the entry by hand without `_found`; merge writes those.")
    if ruling:
        print(f"{len(ruling)} verified feed(s) held for a ruling the owner owes:")
        for k, v in ruling:
            print(f"  {str(v.get('name') or '?')[:60]:60} {_public_url(k)}\n      ({v.get('checked')}: "
                  f"{str(v.get('reason') or '')[:240]})")
    return 0


def cmd_ledger():
    led = _load_ledger()
    good = {k: v for k, v in led.items() if v.get("status") == "ok"}
    dead = {k: v for k, v in led.items() if v.get("status") == "fail"}
    quiet = {k: v for k, v in led.items() if v.get("status") == "empty"}
    # Every status the ledger holds, so the header adds up: `refused` is
    # robots.txt (verify, and the osm walk's homepages), `unreachable` the osm
    # walk's week-long park, `reopened` a hand-cleared row.
    other = {}
    for v in led.values():
        st = v.get("status")
        if st not in ("ok", "fail", "empty"):
            other[st or "?"] = other.get(st or "?", 0) + 1
    rest = "".join(f"  |  {n} {st}" for st, n in sorted(other.items()))
    print(f"ledger: {len(led)} tried  |  {len(good)} ok  |  {len(dead)} broken  "
          f"|  {len(quiet)} parse fine but nothing upcoming{rest}\n")
    print("broken (skipped until TTL lapses):")
    for k, v in sorted(dead.items()):
        print(f"  [{v.get('type','?'):8}] {k}  <- {v.get('reason','')[:40]}  ({v.get('checked')})")
    return 0


# --- coverage --------------------------------------------------------------
# Where does the catalog cover ground, and where is it thin? Parses the three
# source configs (no network) and prints per-country / per-metro / per-category
# tables plus a ranked "thin ground" list an autonomous curation run can pick
# targets from. Location comes from ics/opendata geocode_suffix (", City, ST"
# or ", City, Country") and from the "(City...)" suffix in source names.

_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
# THE SAME STATES SPELLED OUT, because that is what the CATALOG actually says.
#
# _US_STATES is codes, and a hand-written suffix uses one (", Seattle, WA").
# Everything the civic backend has ever written uses the full name instead —
# `geocode_suffix` is ", City, Region" and Wikidata's region label is "Wisconsin"
# — so _parse_place fell through to its last-token rule and read the STATE as
# the metro. Measured 2026-09-19: 894 of the catalog's 3,131 rows, which is the
# whole top of the metro table ("*Texas 173", "*Florida 125", "*California 76",
# "*Minnesota 71"). Those are not metros; the city was sitting in the part the
# parser threw away. The country came out right only because a bare token
# defaults to the US, which is also why "(RDA)" and "(CFI)" — two acronyms in a
# source's name — were US metros.
_US_STATE_NAMES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia",
    # NOT "Puerto Rico": it already reads as its own country in the report, it
    # is flagged as thin ground there, and folding it into the US would hide
    # that. NOT a special case for "Georgia" either — the country is absent from
    # _ISO_COUNTRY and the catalog has never had a source in it, so the state
    # wins by default. Add the country there and this set needs a note.
}
_COUNTRY_ALIASES = {
    "UK": "United Kingdom", "United Kingdom": "United Kingdom",
    "US": "United States", "USA": "United States", "United States": "United States",
    "Canada": "Canada", "Australia": "Australia", "Ireland": "Ireland",
    "New Zealand": "New Zealand", "Singapore": "Singapore", "India": "India",
    "South Africa": "South Africa", "France": "France", "Germany": "Germany",
    "Netherlands": "Netherlands", "Mexico": "Mexico", "Japan": "Japan",
}
# A NAME OR AN ISO CODE, BOTH SPELLINGS OF ONE COUNTRY.
#
# `_COUNTRY_ALIASES` above is the hand-written half: the spellings that are not
# derivable ("UK", "USA") and the names that were curated before _ISO_COUNTRY
# existed. The derivable half is _ISO_COUNTRY itself, which already knows every
# country the catalog touches by code AND by canonical name — and _parse_place
# was reading neither, so both halves of the world were read as the United
# States. Measured 2026-09-19 over the live catalog: 103 sources whose suffix
# ends in a foreign ISO code (", Zurich, CH" — 20 of them, then Stockholm,
# Brussels, Sydney, Paris, Oslo, Copenhagen, Warsaw, Madrid, Brno, Auckland,
# Vienna, London, Winnipeg, Dublin) and 12 more whose suffix ends in a country
# NAME that _COUNTRY_ALIASES happened not to list (Sweden, Italy, Hong Kong,
# Norway, Spain, Finland, Denmark). 115 in total, every one of them filed under
# "United States" with the ISO code itself standing in as the METRO — which is
# where the "*CH 20", "*SE 10", "*BE 10" and "*Wien 9" rows in the metro table
# came from. The report is what aims the next curation run, so this did not just
# misprint: it inflated the one country the ticketing APIs already blanket and
# hid real supply in nine countries that read as thin ground.
#
# The US-state branch in _parse_place runs FIRST and keeps running first. "CA"
# in a geocode suffix is California, not Canada; "DE" is Delaware, "IN" Indiana,
# "LA" Louisiana. That collision is the reason this is a fallback and not a
# lookup at the top.
def _country_named(token):
    """The country a suffix's last part names, by alias, name or ISO code."""
    if not token:
        return None
    hit = _COUNTRY_ALIASES.get(token)
    if hit:
        return hit
    if len(token) == 2 and token.isalpha():
        # Only an UPPERCASE code is a code. "Us" and "in" are words.
        if token.isupper():
            return _ISO_COUNTRY.get(token)
        return None
    # A canonical name spelled in full: "Switzerland", "Czechia", "Hong Kong".
    return _ISO_COUNTRY_NAMES.get(token.casefold())


_METRO_ALIASES = {
    "NYC": "New York", "New York City": "New York", "Brooklyn": "New York",
    "DC": "Washington DC", "DC metro": "Washington DC", "Washington": "Washington DC",
    "SF Bay": "San Francisco", "SF": "San Francisco", "LA": "Los Angeles",
    "Portland OR": "Portland",
}
# App category keys that curated feeds are responsible for.
#
# DERIVED FROM THE PRODUCT, not hand-maintained. Every mapsee front door opens
# onto a set of categories (`categories` in mapsee/src/lens.js), and a door whose
# categories have no supply is an empty map with a brand on it. That list used to
# be copied here by hand, so adding a door silently failed to widen curation:
# awaresie.com shipped opening onto community/volunteer/learning and wegosie.com
# onto running/sports/fitness, and `fitness` was never a curation target at all —
# the coverage report could not raise a gap it did not know existed.
#
# mapsee serves the roster at /api/lenses precisely so this repo does not need a
# copy of lens.js. Two groups are then subtracted, for reasons that have nothing
# to do with which doors exist:
_TICKETED = {"music", "theater", "sports", "food"}
# ...blanketed nationally by the ticketing APIs (Ticketmaster, SeatGeek, DICE,
# AXS, Moshtix). Curating civic feeds for them is duplicated effort.
_PROMOTED = {"party"}
# ...reached by keyword promotion at sync time (_PARTY_RX in
# mapsee_supabase_sync — crawls, happy hours and karaoke arrive tagged music or
# community and get moved). No feed declares it, so listing it raised a gap in
# every country that no source could ever close.
MAPSEE_LENSES_URL = os.environ.get("MAPSEE_LENSES_URL", "https://mapsee.me/api/lenses")
# What the roster resolved to on 2026-08-03, and what is used when the lookup
# fails. Offline behaviour must stay useful: `coverage` is a reporting command
# people run on a laptop.
_CURATED_FALLBACK = ["arts", "community", "fitness", "kids", "learning",
                     "market", "outdoors", "running", "volunteer"]
_curated_cache = None
# Whether the last resolve actually reached the roster. Tracked explicitly
# rather than inferred by comparing the result to _CURATED_FALLBACK — the two
# are identical whenever the fallback is up to date, which is exactly when you
# most want to know the live lookup worked.
CURATED_FROM_LIVE = False


def curated_categories(session=None):
    """Every category a committed lens opens onto, minus the two groups above."""
    global _curated_cache, CURATED_FROM_LIVE
    if _curated_cache is not None:
        return _curated_cache
    try:
        r = (session or requests).get(MAPSEE_LENSES_URL, timeout=15,
                                      headers={"User-Agent": UA, "Accept": "application/json"})
        r.raise_for_status()
        lenses = (r.json() or {}).get("lenses") or {}
        keys = set()
        for l in lenses.values():
            for c in (l.get("categories") or []):
                keys.add(c)
        got = sorted(keys - _TICKETED - _PROMOTED)
        if got:
            _curated_cache = got
            CURATED_FROM_LIVE = True
            return got
    except Exception:      # noqa: BLE001 - offline, DNS, a 500: fall back, don't fail
        pass
    _curated_cache = list(_CURATED_FALLBACK)
    return _curated_cache


def _parse_place(text):
    """(metro, country) from 'City, ST' / 'City, Country' / 'X, City, Country'.
    Returns (metro, None) when the country can't be inferred."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return None, None
    last = parts[-1]
    # A state, either spelling. The NAME is checked first because it is what the
    # civic backend writes and it cannot collide with a two-letter code.
    if (last in _US_STATE_NAMES) or (last.upper() in _US_STATES and len(last) == 2):
        # '(DC)' alone is a metro name, not a bare state
        metro = parts[-2] if len(parts) >= 2 else _METRO_ALIASES.get(last)
        return _METRO_ALIASES.get(metro, metro), "United States"
    country = _country_named(last)
    if country:
        rest = parts[:-1]
        while rest and re.fullmatch(r"[A-Z]{2,3}", rest[-1]):
            rest.pop()                     # drop province/state codes (BC, NSW)
        metro = rest[-1] if rest else country
        return _METRO_ALIASES.get(metro, metro), country
    # single unrecognized token: 'Chicago', 'SF Bay', 'Portland OR', 'DC metro'
    if len(parts) == 1:
        token = _METRO_ALIASES.get(last, last)
        m = re.fullmatch(r"(.+?)\s+([A-Z]{2})", token)
        if m and m.group(2) in _US_STATES:
            token = m.group(1)
        return _METRO_ALIASES.get(token, token), None
    return _METRO_ALIASES.get(last, last), None


# WHAT DISCOVERY WROTE DOWN, and three ways to read it when the suffix cannot.
#
# `_locate` reads a source's NAME and its `geocode_suffix`, and that is all the
# ics-shaped configs carry. But 840 entries place themselves with a `venue`
# block instead — a surveyed point from OSM, which is BETTER data and was
# invisible here. Measured 2026-09-20: 824 of those 840 reported metro "?", and
# the country only survived when a ccTLD happened to rescue it. That is 26% of
# the catalog, and it includes every source the venue walk has ever proposed:
# "Nectar Lounge, Seattle WA" read as metro ?, country ?, which is the bucket
# the thin-ground ranker cannot tell apart from a country with no feeds at all.
#
# Three fallbacks, in order of how directly they were OBSERVED:
#
#  1. `_metro` — what the sweep recorded about itself, present on 868 of them.
#     Parsed COUNTRY-FIRST, never by _parse_place: this field is written as
#     "<metro>, <country>" by _discover_osm and _discover_civic, so its tail is
#     always a country and never a US state. _parse_place's state branch runs
#     first by design and reads "Winnipeg, CA" as Winnipeg, California — which
#     is right for a geocode suffix and wrong for this.
#  2. The venue's own city/region/country. Present on 399 of 840; a config that
#     states where it is outranks anything inferred from geometry.
#  3. The surveyed POINT, matched to a metro this repo sweeps. Nearly complete
#     — lat/lon is on 836 of 840 — and principled rather than a guess: these
#     venues were found BY a metro sweep, so the point lies inside that metro's
#     own bbox. Nearest centre among overlapping boxes, because Washington DC's
#     100-mile radius reaches into Baltimore's.
def _parse_metro_field(text):
    """(metro, country) from a `_metro` provenance string, country-first."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return None, None
    country = _country_named(parts[-1]) if len(parts) > 1 else None
    if not country and len(parts) > 1 and re.fullmatch(r"[A-Z]{2}", parts[-1]):
        country = _ISO_COUNTRY.get(parts[-1])
    metro = parts[0] if len(parts) > 1 else parts[0]
    return _METRO_ALIASES.get(metro, metro), country


_swept_boxes = None
_swept_boxes_by_latitude = None


def _index_swept_boxes(boxes):
    """Index each swept bbox into the integer latitude bands it touches.

    Coverage reads thousands of venue points. A linear scan of all 261 current
    metro boxes checked about 397k boxes to place 1,520 surveyed points, though
    only 3.9 boxes per point span its latitude. The index narrows that candidate
    set without changing the exact bbox or nearest-centre checks below.
    """
    by_latitude = {}
    for box in boxes:
        try:
            south, west, north, east = (float(value) for value in box[:4])
        except (IndexError, TypeError, ValueError, OverflowError):
            continue
        if (not all(math.isfinite(value) for value in (south, west, north, east))
                or not (-90 <= south <= north <= 90)
                or not (-180 <= west <= east <= 180)):
            continue
        for band in range(math.floor(south), math.floor(north) + 1):
            by_latitude.setdefault(band, []).append(box)
    return by_latitude


def _swept_metro_for(lat, lon):
    """The metro this repo sweeps that a surveyed point falls inside."""
    global _swept_boxes, _swept_boxes_by_latitude
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        return None, None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None, None
    if _swept_boxes is None:
        try:
            import catalog_discover_osm as osm
            _swept_boxes = []
            for m in osm.metros():
                try:
                    s, w, n, e = (float(x) for x in m["bbox"].split(","))
                    name = m["name"]
                    country = m.get("country_name") or m["country"]
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if (not all(math.isfinite(value) for value in (s, w, n, e))
                        or not (-90 <= s <= n <= 90)
                        or not (-180 <= w <= e <= 180)):
                    continue
                _swept_boxes.append((s, w, n, e, (s + n) / 2, (w + e) / 2,
                                     name, country))
            _swept_boxes_by_latitude = _index_swept_boxes(_swept_boxes)
        except Exception:                                         # noqa: BLE001
            _swept_boxes = []                # no metro list: this fallback is off
            _swept_boxes_by_latitude = {}
    best = None
    boxes = (_swept_boxes_by_latitude.get(math.floor(lat), ())
             if _swept_boxes_by_latitude is not None else _swept_boxes)
    for s, w, n, e, clat, clon, name, country in boxes:
        if s <= lat <= n and w <= lon <= e:
            d = (lat - clat) ** 2 + (lon - clon) ** 2
            if best is None or d < best[0]:
                best = (d, name, country)
    return (best[1], best[2]) if best else (None, None)


def _observed_metro(e):
    """The metro a config entry OBSERVED, in order of how directly: what the
    sweep recorded about itself, what the venue block states, then which swept
    metro the surveyed point falls in. Metro only — see _locate_entry."""
    lat, lon = _venue_point(e.get("venue"))
    for cand in (_parse_metro_field(e.get("_metro"))[0],
                 _venue_place(e.get("venue"))[0],
                 _swept_metro_for(lat, lon)[0] if lat is not None else None):
        if cand:
            return cand
    return None


def _locate_entry(e, name, suffix):
    """(metro, country) for one config entry: the suffix first, then what the
    sweep observed. Never overrides a value the suffix or the name produced.

    A METRO AND A COUNTRY COME FROM DIFFERENT EVIDENCE HERE, and conflating them
    is a mistake that ships wrong countries. `_metro` and a bbox match say which
    SWEEP found the venue, and a sweep area is not a border: Basel's 25km box
    covers Alsace and Baden, Liege's reaches Maastricht, Salzburg's reaches
    Bavaria, Geneva's covers Haute-Savoie. Measured 2026-09-20 — letting
    geometry name the country moved 24 rows to the wrong one, every one of them
    a border case the ccTLD had right: Musee de l'Impression sur Etoffes
    (Mulhouse, .fr) to Switzerland, De Muziekgieterij (Maastricht, .nl) to
    Belgium, Hans-Peter Porsche TraumWerk (Bavaria, .de) to Austria.
    So: geometry may name the METRO, and only a statement ABOUT THE VENUE — its
    own `venue.country`, or its city and region together — may name the country.
    Everything else still falls through to the ccTLD rescue in _coverage_rows,
    which is where it belongs.
    """
    metro, country = _locate(name, suffix)
    if metro != "?" and country != "?":
        return metro, country
    v_metro, v_country = _venue_place(e.get("venue"))
    if country == "?" and v_country:
        country = v_country
    if metro == "?":
        metro = _observed_metro(e) or "?"
    return metro, country


def _venue_point(venue):
    v = venue or {}
    try:
        return float(v["lat"]), float(v["lon"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _venue_place(venue):
    """(metro, country) a `venue` block STATES, as opposed to implies."""
    v = venue or {}
    if not v:
        return None, None
    country = None
    if v.get("country"):
        iso = str(v["country"]).upper()
        country = _ISO_COUNTRY.get(iso) if len(iso) == 2 else None
        country = country or _country_named(str(v["country"])) or None
    metro = v.get("city") or None
    if metro and not country and v.get("region"):
        # "City, Region" is the shape _parse_place already reads, and a US state
        # there is a state — this is a venue's own address, not a country code.
        m2, c2 = _parse_place(f"{v['city']}, {v['region']}")
        metro, country = m2 or metro, c2
    return (_METRO_ALIASES.get(metro, metro) if metro else None), country


def _name_paren(name):
    """The '(City...)' suffix curators put in source names, if any."""
    m = re.search(r"\(([^()]+)\)\s*$", name or "")
    return m.group(1).strip() if m else None


def _locate(name, suffix):
    """Best-effort (metro, country) for one source entry. The name's '(City...)'
    suffix carries metro intent ('DC metro'); the geocode_suffix pins country."""
    s_metro, s_country = _parse_place((suffix or "").strip(" ,"))
    p_metro, p_country = _parse_place(_name_paren(name)) if _name_paren(name) else (None, None)
    # The name's parens win, EXCEPT when what they hold is a code rather than a
    # place. Curators put acronyms there too — "Eau Claire — Redevelopment
    # Authority (RDA)" and "Centre Franco-Iranien (CFI)" — and a gancio entry
    # spells its parens as "(US, IL)", which names a country and a state and no
    # city at all. Each of those became a METRO with one source in it, which is
    # exactly the shape the thin-ground ranker chases.
    if _looks_like_a_code(p_metro):
        p_metro = None
    metro = p_metro or s_metro
    country = s_country or p_country
    if not country and metro and not _looks_like_a_code(metro):
        country = "United States"          # config convention: bare '(City)' = US
    return metro or "?", country or "?"


def _looks_like_a_code(metro):
    """Whether a would-be metro is really a region/country code, not a place.

    The bare-'(City)'-is-US convention above is about a CITY NAME: a curator who
    writes "(Issaquah)" means Issaquah, Washington. It was also swallowing every
    two-letter token the country tables do not know — ", Winnipeg, MB" (Manitoba)
    and ", SA" (South Australia) were filed as US metros named MB and SA. A code
    is not a city, so it does not get the city convention; leaving the country
    unknown lets the ccTLD rescue in _coverage_rows read wcccc.ca and cmfc.asn.au
    and get both right.
    """
    return bool(re.fullmatch(r"[A-Z]{2,3}", metro or ""))


_TZ_COUNTRY = {
    "Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Paris": "FR",
    "Europe/Berlin": "DE", "Europe/Amsterdam": "NL", "Europe/Brussels": "BE",
    "Europe/Zurich": "CH", "Europe/Madrid": "ES", "Europe/Rome": "IT",
    "Europe/Stockholm": "SE", "Europe/Oslo": "NO", "Europe/Copenhagen": "DK",
    "Europe/Helsinki": "FI", "Europe/Vienna": "AT", "Europe/Warsaw": "PL",
    "Europe/Lisbon": "PT", "Europe/Prague": "CZ", "Asia/Tokyo": "JP",
    "Asia/Seoul": "KR", "Asia/Singapore": "SG", "Asia/Hong_Kong": "HK",
    "Asia/Dubai": "AE", "Asia/Kolkata": "IN", "Africa/Johannesburg": "ZA",
    "Pacific/Auckland": "NZ", "America/Mexico_City": "MX", "America/Toronto": "CA",
    "America/Vancouver": "CA", "America/Edmonton": "CA", "America/Winnipeg": "CA",
    "America/Halifax": "CA", "America/Montreal": "CA",
}
_TLD_COUNTRY = {
    # ccTLDs the Mobilizon instance directory turned up with no mapping. Only
    # COUNTRY codes are added - .org/.social/.net/.eu and friends say nothing
    # about where the events are, and a blank default_country is strictly better
    # than a guessed one (the adapter prefers the event's own address anyway).
    "gr": "GR", "hu": "HU", "lt": "LT", "si": "SI", "cr": "CR", "br": "BR",
    "us": "US",
    "uk": "GB", "ie": "IE", "ca": "CA", "au": "AU", "nz": "NZ", "de": "DE",
    "fr": "FR", "nl": "NL", "be": "BE", "ch": "CH", "es": "ES", "it": "IT",
    "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "at": "AT", "pl": "PL",
    "pt": "PT", "cz": "CZ", "mx": "MX", "in": "IN", "jp": "JP", "kr": "KR",
    "sg": "SG", "hk": "HK", "ae": "AE", "za": "ZA",
    # THREE THAT ARE NOT ccTLDs AND STILL NAME ONE COUNTRY. The rule above is
    # right that .org and .eu say nothing; these three are not that. `.cat` is
    # Catalonia's sponsored TLD and every holder is in Spain — 14 rows, and it
    # is why the metro table read "Barcelona ?" beside "Barcelona Spain".
    # `.gov` and `.edu` are US-restricted registries: everywhere else is
    # gov.uk, gov.au, edu.au, ac.uk, which this table already resolves through
    # their real ccTLD. 50 rows between them, measured 2026-09-20.
    "cat": "ES", "gov": "US", "edu": "US",
}

_ISO_COUNTRY = {
    "CR": "Costa Rica", "GR": "Greece", "HU": "Hungary", "SI": "Slovenia",
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "NZ": "New Zealand", "IE": "Ireland", "FR": "France", "DE": "Germany",
    "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland", "ES": "Spain",
    "IT": "Italy", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "AT": "Austria", "PL": "Poland", "PT": "Portugal", "CZ": "Czechia",
    "MX": "Mexico", "BR": "Brazil", "IN": "India", "JP": "Japan", "KR": "South Korea",
    "LT": "Lithuania", "MY": "Malaysia",
    "SG": "Singapore", "HK": "Hong Kong", "AE": "United Arab Emirates",
    "ZA": "South Africa",
    # Declared by bikereg_sources.json's `countries` and by nothing else. Absent
    # from this table they stayed as "IS" and "JM", which is not a country name
    # a gap report can act on.
    "IS": "Iceland", "JM": "Jamaica",
    # The 2026-10-05 community-feeds batch declared these and nothing had before.
    "AR": "Argentina", "AW": "Aruba", "CL": "Chile", "DO": "Dominican Republic",
    "IL": "Israel", "LV": "Latvia", "MQ": "Martinique", "PR": "Puerto Rico",
    "RO": "Romania",
    # The 2026-10-23 targeted batch: Kuressaare kultuurikeskus (Saaremaa).
    "EE": "Estonia",
}
# The same table read the other way, for a suffix that spells the country out.
# Built rather than written, so the two can never disagree.
_ISO_COUNTRY_NAMES = {name.casefold(): name for name in _ISO_COUNTRY.values()}


def _iso_countries(value):
    """The ISO codes a config's `countries` declares, whatever shape it is in.

    Every config but one states `countries` as a LIST of codes. parkrun's states
    a MAP — parkrun's own numeric country id to the ISO code — because the
    adapter looks a feed row's `countrycode` up in it (`(cfg.get("countries") or
    {}).get(str(code))`). Iterating a dict yields its KEYS, so the coverage
    report read parkrun's ids as country names and filed 20 sources under "97",
    "3", "85" and friends. That is not a cosmetic misprint: those 20 fakes each
    held exactly one source, `thin_countries` is "<= 2 sources", and the ranked
    thin-ground list is capped at 20 — so they filled **all twenty slots**, and
    the report an autonomous curation run reads to pick its targets contained
    nothing but parse artefacts. The real thin ground (volunteer at 10 sources,
    running at 22, kids at 25) never appeared at all.

    The same shape would bite the generic `countries` branch in _coverage_rows,
    so both go through here rather than one being taught about the other.
    """
    if isinstance(value, dict):
        value = list(value.values())
    return [str(c).upper() for c in (value or []) if c]


try:
    # Only for expanding a market source's national grid spec into the places it
    # covers. Guarded: curation must still run if the pipeline module is absent.
    from mapsee_ingest_markets import usda_grid as _usda_grid
except Exception:  # noqa: BLE001
    _usda_grid = None


def _tz_country(tz):
    """Country from an IANA timezone — the only location signal some configs carry."""
    if not tz:
        return None
    if tz in _TZ_COUNTRY:
        return _ISO_COUNTRY[_TZ_COUNTRY[tz]]
    if tz.startswith("Australia/"):
        return "Australia"
    if tz.startswith(("America/", "US/")) or tz == "Pacific/Honolulu":
        return "United States"
    return None


def _url_country(url):
    """Country from a ccTLD. Coarse, but a .co.uk venue calendar is not in Ohio."""
    m = re.match(r"https?://([^/]+)", url or "")
    if not m:
        return None
    code = _TLD_COUNTRY.get(m.group(1).rsplit(".", 1)[-1].lower())
    return _ISO_COUNTRY.get(code) if code else None


def _scan_name_metro(name):
    """'NYC Parks Events', 'Seattle University' carry the metro in the bare name."""
    scan = dict(_METRO_ALIASES)
    scan.update({c: c for c in ("Seattle", "New York", "Chicago", "Boston",
                                "Austin", "Denver", "Miami", "Atlanta")})
    for alias, canon in scan.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", name or ""):
            return canon
    return None


def _bbox_place(text):
    """'London, GB' -> (London, United Kingdom); 'Portland OR' -> (Portland, US).

    The bbox and USDA area lists spell a foreign metro with an ISO suffix and a
    domestic one bare, so a two-letter tail is read as a COUNTRY first — ', CA'
    in that list is Canada, never California.
    """
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].upper() in _ISO_COUNTRY:
        metro = ", ".join(parts[:-1])
        return _METRO_ALIASES.get(metro, metro), _ISO_COUNTRY[parts[-1].upper()]
    metro = parts[0] if parts else "?"
    m = re.fullmatch(r"(.+?)\s+([A-Z]{2})", metro)
    if m and m.group(2) in _US_STATES:
        metro = m.group(1)                     # 'Portland OR' -> Portland
    return _METRO_ALIASES.get(metro, metro), "United States"


def _rows_market(data):
    """One row per PLACE a market source covers — a bounding box, a query area,
    or the source's own city. Breadth, not depth: ten hand-curated Seattle
    markets are one covered metro, while OSM's box list is 245 of them."""
    out = []
    for e in data:
        name = e.get("name") or "?"
        hint = _tz_country(e.get("timezone"))
        if e.get("grid"):
            # A national grid covers every metro AND everything between them, so
            # it is expanded for the country totals but filed under one synthetic
            # metro — 200-odd "conus 41.2,-95.3" rows would bury the metro table.
            for area in (_usda_grid(e["grid"]) if _usda_grid else []):
                region = str(area.get("name", "")).split(" ")[0].split("-")[0]
                country = "Puerto Rico" if region == "pr" else "United States"
                out.append((name, f"(national grid: {region})", country, "market"))
            continue
        places = e.get("bboxes") or e.get("areas")
        if places:
            for p in places:
                metro, country = _bbox_place(p.get("name") or p.get("city") or "")
                out.append((name, metro, country, "market"))
            continue
        metro, country = _parse_place(e.get("city") or "")
        if not metro or metro == "?":
            metro = _scan_name_metro(name) or "?"
        out.append((name, metro, country or hint or "?", "market"))
    return out


def _rows_jsonld(data):
    """jsonld sites place themselves with a `venue` block, not a suffix — 235 of
    the 239 carry one and this expander could read none of it, so "Songbyrd
    Music House" (Washington DC) and "Chiswick House" (London) both arrived as
    metro "?".

    THE COUNTRY RULE IS UNCHANGED ON PURPOSE. It is the ccTLD, then the US only
    when the NAME held a US metro — and that last clause has to stay keyed to
    the name scan rather than to the metro below it, because the metro can now
    come from a Swiss venue block and "metro is known, therefore American" would
    then be false. `_locate` is not used here at all: it reads a name's
    parenthetical as a city and defaults it to the US, which turns
    "i45 (industrie 45)" into a US metro called "industrie 45".
    """
    out = []
    for e in data.get("sites", []):
        name = e.get("name") or "?"
        us_metro = _scan_name_metro(name)
        metro = us_metro or _observed_metro(e) or "?"
        country = _url_country((e.get("listing") or [None])[0])
        if not country:
            country = _venue_place(e.get("venue"))[1]
        if not country:
            country = "United States" if us_metro else "?"
        out.append((name, metro, country, e.get("category") or "?"))
    return out


def _rows_program(data):
    out = []
    for e in data:
        name = e.get("name") or "?"
        # The sites carry full street addresses; the first one locates the program.
        # A nationwide one (the free museum days) has coordinates and a "country"
        # per site instead, and no one metro; Museums on Us has no timezone either.
        site = (e.get("sites") or [{}])[0]
        addr = site.get("address") or ""
        metro, country = _parse_place(re.sub(r"\s+\d{5}(?:-\d{4})?\s*$", "", addr))
        if not metro or metro == "?":
            metro = _scan_name_metro(name) or "?"
        out.append((name, metro, country or _country_named(site.get("country"))
                    or _tz_country(e.get("timezone")) or "?",
                    e.get("category") or "?"))
    return out


def _rows_venuepilot(data):
    out = []
    for e in data.get("sites", []):
        v = e.get("venue") or {}
        metro, country = _parse_place(f"{v.get('city', '')}, {v.get('region', '')}".strip(" ,"))
        out.append((e.get("name") or "?", metro or "?", country or "?",
                    e.get("category") or "?"))
    return out


def _rows_mylisting(data):
    """MyListing directory sites. One entry is one TOWN's whole calendar, so its
    metro comes from the config's own default_city/default_region rather than
    from the site name — "Bainbridge Island (bainbridgeisland.com)" is a domain
    in parentheses, not a place, and _locate would read it as one."""
    out = []
    for e in data.get("sites", []):
        metro, country = _parse_place(
            f"{e.get('default_city', '')}, {e.get('default_region', '')}".strip(" ,"))
        out.append((e.get("name") or "?", metro or "?", country or "?",
                    e.get("category") or "?"))
    return out


def _rows_restaurant(data):
    out = []
    for e in data.get("restaurants", []):
        metro, country = _parse_place(f"{e.get('city', '')}, {e.get('region', '')}".strip(" ,"))
        out.append((e.get("name") or "?", metro or "?", country or "?",
                    e.get("category") or "food"))
    return out


def _rows_parkrun(data):
    """One feed, ~2,900 events, 21 countries — so it is filed per country it
    declares. Metro is meaningless here: parkrun is denser than a metro list."""
    if not data.get("url"):
        return []
    cat = data.get("category") or "running"
    # _iso_countries, not a bare iteration: this config states `countries` as a
    # {parkrun id: ISO} MAP, and the keys are the ids. See its docstring.
    codes = _iso_countries(data.get("countries")) or ["?"]
    return [("parkrun", "(worldwide)", _ISO_COUNTRY.get(c, c), cat) for c in codes]


def _rows_runsignup(data):
    """One API covering every race it hosts, so there is no per-source list to
    walk — the config tunes how much of it to read.

    Filed as TWO rows, running and fitness, because the adapter genuinely
    supplies both: a road race is emitted with `running` primary and `fitness`
    secondary, and multisport the other way round. Reporting it under one would
    leave the other reading zero and send the next gap sweep after ground that is
    already covered — which is the failure the ODS per-country rows were added to
    fix.

    Partitioned by US state, and RunSignup is a US platform (measured: 247 of 250
    races on a page), so the country is not a guess.
    """
    if not data:
        return []
    states = data.get("states")
    metro = "(50 states)" if states is None else f"({len(states)} states)"
    return [("runsignup", "RunSignup races", metro, "United States", cat)[1:]
            for cat in ("running", "fitness")]


def _rows_bikereg(data):
    """One GraphQL calendar covering every AthleteReg event, so — like RunSignup
    — there is no per-source list to walk; the config tunes how much to read.

    Filed as ONE row per country the feed actually reaches, and as `fitness`
    only. Unlike RunSignup this adapter has a single primary: every cycling
    listing is emitted `fitness`, with sports/outdoors as secondaries. Claiming
    a `sports` row here would report a door as covered that this source does not
    supply, which is precisely the over-reporting the per-country ODS rows exist
    to avoid.
    """
    if not data:
        return []
    apps = ", ".join(data.get("app_types") or ["BIKEREG"])
    return [(f"AthleteReg ({apps})", "(national)",
             _ISO_COUNTRY.get(c.upper(), c.upper()), "fitness")
            for c in (data.get("countries") or ["US"])]


def _rows_affiliate(data):
    out = []
    for e in data.get("feeds", []):
        name = e.get("source") or e.get("name") or "?"
        for code in (e.get("countries") or ["?"]):
            out.append((name, "?", _ISO_COUNTRY.get(code.upper(), code.upper()),
                        e.get("category") or "food"))
    return out


# Configs the coverage report READS but curation never probes. Their reach lives
# in a nested list — bounding boxes, query areas, venues — rather than one URL
# per source, so they cannot join CONFIG: audit/verify would try to HTTP-probe a
# bounding box. Leaving them out entirely was worse, though; it meant the report
# ranked market coverage as thin in exactly the countries a national source had
# just covered, and every gap it printed was computed from 4 of the 10 configs.
# THREE CURATED FILES THE REPORT COULD NOT SEE, AND ALL THREE ARE ABOUT THE
# WORLD. `coverage` reads CONFIG and EXTRA_CONFIG and nothing else, and its
# thin-ground ranker is what aims the next curation run — so a curated file
# missing from both tables does not read as supply that exists, it reads as
# ground nobody has reached. Measured 2026-09-19 against the live report:
#
#   • BRAZIL was FLAGged "no arts, community, fitness, kids, learning feeds
#     yet" while `mapasculturais_sources.json` is, in AGENTS.md's own words,
#     "the only source that puts anything on the map in Brazil" — two state
#     registers, both `community`, one of them measured at 329 future
#     occurrences. The report was sending sweeps at a country to find what the
#     repo had already built an adapter for.
#   • THE UNITED KINGDOM was FLAGged for `volunteer`, and GoodGym — a national
#     volunteering network — is entry seven of `openactive_sources.json`. That
#     is one of the nine curated volunteer sources on earth, invisible to the
#     one report that counts them.
#   • `learning` counted 244 without the six BiblioCommons library systems
#     behind 28,314 upcoming programmes, two of which are Canadian.
#
# None of the three can go in CONFIG: that table is what `verify` and `merge`
# probe, and these are adapters with their own shapes and their own hand
# curation, not candidates a sweep can propose. An expander is exactly the
# mechanism for that, which is what market, parkrun and bikereg already use.
def _rows_openactive(data):
    """One row per operator. OpenActive is a UK data standard and every entry
    declares `country`, so nothing here is inferred.

    The metro is a placeholder because these are NATIONAL operators — Better
    (GLL) runs over 250 leisure centres — and giving one of them a city would
    invent a metro with a hundred venues hiding behind it.
    """
    rows = []
    for e in (data.get("sources") or []):
        iso = str(e.get("country") or "").upper()
        rows.append((e.get("name") or "?", "(national)",
                     _ISO_COUNTRY.get(iso, iso or "?"),
                     e.get("category") or "fitness"))
    return rows


def _rows_bibliocommons(data):
    """One row per library SYSTEM, in the country it declares.

    `country` was added to the config for this: the file is six systems and two
    of them are Canadian (Edmonton, Vancouver), so deriving it from the name
    would be a guess about exactly the thing the report is for. The adapter
    reads only the keys it knows, so the field costs it nothing.
    """
    rows = []
    for e in (data.get("sites") or []):
        iso = str(e.get("country") or "").upper()
        rows.append((e.get("name") or "?", e.get("city") or "?",
                     _ISO_COUNTRY.get(iso, iso or "?"),
                     e.get("category") or "learning"))
    return rows


def _rows_recitals(data):
    """One row per public concert series, at its configured physical venue."""
    rows = []
    for entry in data.get("recitals", []):
        venue = entry.get("venue") or {}
        rows.append((entry.get("name") or "?", venue.get("city") or "?",
                     _country_named(venue.get("country")) or "?",
                     entry.get("category") or "music"))
    return rows


def _rows_waterfront(data):
    """One public calendar, with explicitly declared city and country."""
    return [(entry.get("name") or "?", entry.get("city") or "?",
             _country_named(entry.get("country")) or "?",
             entry.get("category") or "community")
            for entry in data.get("sites", [])]


def _rows_mapasculturais(data):
    """One row per state register. Brazil is not declared per entry and does not
    need to be: the adapter exists for Mapas Culturais, which is a Brazilian
    federal software project, and every instance in the file is a state
    secretariat of culture. The METRO is the state, because that is the extent
    of what one register covers."""
    rows = []
    for e in (data.get("sites") or []):
        name = e.get("name") or "?"
        # "Mapa Cultural do Ceará" -> "Ceará". The slug is the state's own code
        # and is what the adapter keys on; the name is what a person reads.
        state = re.sub(r"^\s*Mapa\s+Cultural\s+d[eoa]s?\s+", "", name).strip() or "?"
        rows.append((name, state, "Brazil", e.get("category") or "community"))
    return rows


EXTRA_CONFIG = {
    "market": ("market_sources.json", _rows_market),
    "mylisting": ("mylisting_sources.json", _rows_mylisting),
    "parkrun": ("parkrun_sources.json", _rows_parkrun),
    "runsignup": ("runsignup_sources.json", _rows_runsignup),
    "bikereg": ("bikereg_sources.json", _rows_bikereg),
    "jsonld": ("jsonld_sources.json", _rows_jsonld),
    "program": ("program_sources.json", _rows_program),
    "venuepilot": ("venuepilot_sources.json", _rows_venuepilot),
    "restaurant": ("restaurant_sources.json", _rows_restaurant),
    "affiliate": ("affiliate_sources.json", _rows_affiliate),
    "openactive": ("openactive_sources.json", _rows_openactive),
    "bibliocommons": ("bibliocommons_sources.json", _rows_bibliocommons),
    "recitals": ("recital_sources.json", _rows_recitals),
    "waterfront": ("waterfront_sources.json", _rows_waterfront),
    "mapasculturais": ("mapasculturais_sources.json", _rows_mapasculturais),
}
# jsonld, mylisting and venuepilot are declared in BOTH tables, over the SAME
# file: CONFIG so verify/merge can probe them, EXTRA_CONFIG for an expander that
# reads the file's real shape. Concatenating the two key sets listed each of them
# TWICE — a duplicate column in the report, and, because _coverage_rows walked
# both paths, 107 sources (jsonld 105, mylisting 1, venuepilot 1) counted twice
# in `total`. Every `total_sources` in coverage_history.jsonl is inflated 6.7%
# by it (1702 reported, 1595 real, measured 2026-09-06), which is the number the
# curation run reports as its growth.
ALL_TYPES = sorted(set(CONFIG) | set(EXTRA_CONFIG))

# ...and the expander is the one that wins. It knows the file: _rows_jsonld
# reads the country off `listing[0]`'s ccTLD and _rows_venuepilot/_rows_mylisting
# off the config's declared city/region, where the generic path sees only a name
# and files all 105 jsonld sites under "?".
EXPANDER_OWNED = {t for t in CONFIG if t in EXTRA_CONFIG
                  and CONFIG[t][0] == EXTRA_CONFIG[t][0]}


def _extra_coverage_rows():
    rows = []
    for t, (fname, expand) in sorted(EXTRA_CONFIG.items()):
        p = os.path.join(HERE, fname)
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  (skipped {fname}: {exc})")
            continue
        for name, metro, country, cat in expand(data):
            rows.append((t, name, metro, country, cat))
    return rows


def _first_url(e, key):
    """The first http(s) URL an entry carries, under its own key or a common one.

    Half the configs spell the key differently and jsonld's `listing` is a LIST
    of them, so a naive e.get(key) hands _url_country a list and gets None back
    for all 105 sites.
    """
    for k in (key, "url", "base_url", "listing", "explore_url", "collection", "home"):
        v = e.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, str) and x.startswith("http"):
                    return x
    return ""


def _coverage_rows():
    """One row per configured source: (type, name, metro, country, category)."""
    rows = []
    for t, (fname, key) in sorted(CONFIG.items()):
        # A type with its own expander over the same file is counted THERE, once.
        if t in EXPANDER_OWNED:
            continue
        p = os.path.join(HERE, fname)
        if not os.path.exists(p):
            continue
        for e in _entries(fname, json.load(open(p, encoding="utf-8"))):
            name = e.get("name") or "?"
            suffix = e.get("geocode_suffix") or ""
            metro, country = _locate_entry(e, name, suffix)
            if metro == "?":
                hit = _scan_name_metro(name)
                if hit:
                    metro, country = hit, "United States"
            # _scan_name_metro looks for a US metro NAME anywhere in the source's
            # name and, on a hit, hardcodes the country to the United States. It
            # is a decent guess for a hand-written ics label and a bad one for a
            # Mobilizon instance: "Mobilisons (CH)", "Mobilizon.fr (FR)" and
            # "Ticketzon (IT)" — Switzerland, France and Italy — all filed as US.
            # An entry that DECLARES its country is not guessing, so it wins.
            if e.get("default_country"):
                # It is an ISO CODE — the adapters read it as one — so it is
                # expanded here or the country is counted twice under two names.
                # Measured: gancio declares DE, ES, NL, CZ, CA and US, and the
                # report showed "Germany 27" beside "DE 4", "Spain 8" beside
                # "ES 2", and "CA 1" as a country with one source while Canada
                # had 36. Every one of those halves then read as thin ground.
                # No US-state ambiguity to worry about here ("CA" is California
                # in a geocode suffix): this field names a COUNTRY.
                iso = str(e["default_country"]).upper()
                country = _ISO_COUNTRY.get(iso, e["default_country"])
            cat = e.get("category") or ("(per-event)" if t == "localist" else "?")
            # ODS entries carry an ISO `countries` array instead of a
            # geocode_suffix, so _locate() cannot see them and they landed under
            # "?" - which made a covered country still read as thin and sent the
            # next curation run back over ground it had already won (Belgium
            # showed 0 with 1363 events configured; Brisbane 11 datasets showed
            # as nothing). Emit one row per declared country instead.
            iso = _iso_countries(e.get("countries"))
            if iso:
                for code in iso:
                    rows.append((t, name, metro, _ISO_COUNTRY.get(code, code), cat))
                continue
            # Last resort before "?": the source's own ccTLD. A .co.nz venue
            # calendar is not in Ohio, and _url_country has been in this file
            # all along — the coverage report just never asked it. 32% of the
            # catalog (539 of 1702 rows) sat under "?", which reads to the
            # thin-ground ranker exactly like a country with no feeds. Recovers
            # 226 of them, measured 2026-09-06: Sweden 14 -> 50, Denmark 22 ->
            # 47, New Zealand 16 -> 52, Australia 46 -> 83.
            if country == "?":
                country = _url_country(_first_url(e, key)) or "?"
            rows.append((t, name, metro, country, cat))
    return rows + _extra_coverage_rows()


def _sweep_countries():
    """Countries the Ticketmaster/Meetup API sweep already visits, with their
    metros - places where curated feeds would land on active ground."""
    p = os.path.join(HERE, "metros_global.json")
    out = {"United States": ["(top ~50 US metros)"]}
    if os.path.exists(p):
        for c in json.load(open(p, encoding="utf-8")).get("countries", []):
            out[c.get("name")] = [m.get("name") for m in c.get("metros", [])]
    return out


def cmd_coverage():
    rows = _coverage_rows()
    counts = {t: sum(1 for r in rows if r[0] == t) for t in ALL_TYPES}
    # Ten source types will not fit a terminal line beside a metro and a country,
    # and an all-zero column is noise, so only configured types get one.
    types = [t for t in ALL_TYPES if counts[t]]
    W = 8
    print("=== mapsee catalog coverage ===")
    print("  " + "  ".join(f"{t}={counts[t]}" for t in types) + f"  total={len(rows)}\n")

    # -- per country --
    by_country = {}
    for t, _n, _m, country, _c in rows:
        by_country.setdefault(country, {}).setdefault(t, 0)
        by_country[country][t] += 1
    print("-- sources per country --")
    print(f"{'country':<22}" + "".join(f"{t[:W - 1]:>{W}}" for t in types) + f"{'total':>8}")
    for country, tc in sorted(by_country.items(), key=lambda kv: -sum(kv[1].values())):
        tot = sum(tc.values())
        print(f"{country:<22}" + "".join(f"{tc.get(t, 0):>{W}}" for t in types) + f"{tot:>8}")
    thin_countries = [c for c, tc in by_country.items()
                      if sum(tc.values()) <= 2 and c != "?"]
    if thin_countries:
        print("  FLAG countries with 0-2 sources: " + ", ".join(sorted(thin_countries)))
    sweep = _sweep_countries()
    uncovered = [c for c in sweep if c not in by_country]
    if uncovered:
        print("  FLAG API-sweep countries with NO curated feeds: " + ", ".join(uncovered))

    # -- per metro --
    by_metro = {}
    for t, _n, metro, country, _c in rows:
        by_metro.setdefault((metro, country), {}).setdefault(t, 0)
        by_metro[(metro, country)][t] += 1
    print("\n-- sources per metro (* = only one source type) --")
    print(f"{'metro':<26}{'country':<16}"
          + "".join(f"{t[:W - 1]:>{W}}" for t in types) + f"{'total':>8}")
    single_type = []
    for (metro, country), tc in sorted(by_metro.items(),
                                       key=lambda kv: (-sum(kv[1].values()), kv[0])):
        tot = sum(tc.values())
        star = "*" if len(tc) == 1 else " "
        # A NATIONAL ADAPTER'S PLACEHOLDER IS NOT A METRO, so "only one source
        # type here" says nothing about it. parkrun files itself under
        # "(worldwide)", bikereg under "(national)" and a market grid under
        # "(national grid: conus)" — labels that exist BECAUSE the source has no
        # metro to name, and which are therefore single-type by construction and
        # for ever. They were 11 of the 20 ranked targets, recommending "add
        # another feed type" for Germany-the-parkrun-row while Germany itself had
        # 32 sources across six types. Measured: 7 such labels over 250 rows, and
        # no real metro name in the catalog's 421 begins with "(".
        if len(tc) == 1 and not str(metro).startswith("("):
            single_type.append((tot, metro, country, next(iter(tc))))
        print(f"{star}{str(metro)[:24]:<25}{str(country)[:15]:<16}"
              + "".join(f"{tc.get(t, 0):>{W}}" for t in types) + f"{tot:>8}")

    # -- categories per country (localist is per-event, counted separately) --
    cat_by_country = {}
    for t, _n, _m, country, cat in rows:
        cat_by_country.setdefault(country, {}).setdefault(cat, 0)
        cat_by_country[country][cat] += 1
    # Resolved once per run: one lookup against /api/lenses, or the committed
    # fallback when offline. Printed, so the report always says which it used —
    # a gap list is only meaningful if you know what it was measured against.
    cats = curated_categories()
    src = "mapsee.me/api/lenses" if CURATED_FROM_LIVE else "committed fallback (roster unreachable)"
    print("\n-- curated categories per country (every config; '.' = none) --")
    print(f"   lens targets via {src}: {', '.join(cats)}")
    print(f"{'country':<22}" + "".join(f"{c[:6]:>8}" for c in cats))
    cat_gaps = {}
    for country in sorted(cat_by_country, key=lambda c: -sum(cat_by_country[c].values())):
        cc = cat_by_country[country]
        cells = "".join(f"{cc.get(c) or '.':>8}" for c in cats)
        print(f"{country:<22}{cells}")
        if country != "?":
            missing = [c for c in cats if not cc.get(c)]
            if missing:
                cat_gaps[country] = missing
    for country, missing in sorted(cat_gaps.items()):
        print(f"  FLAG {country}: no curated feeds for " + ", ".join(missing))

    # -- ranked thin ground --
    print("\n-- thin ground (ranked targets for the next curation run) --")
    targets = []
    per_cat = {c: 0 for c in cats}
    for _t, _n, _m, _country, cat in rows:
        if cat in per_cat:
            per_cat[cat] += 1
    for country in uncovered:
        metros = ", ".join(sweep[country][:5])
        targets.append((0, 0, f"{country}: 0 curated feeds; API sweep already covers "
                              f"{metros} - seed community/learning feeds there"))
    # CATEGORIES, second only to a country with nothing at all — a lens opens
    # onto categories, so a starved one is a door onto an empty map. They used to
    # score behind the three geographic tiers, which between them always filled
    # the 20-line cap, so no category has ever been printed here. `--category`
    # is the flag that acts on this, hence naming it in the line.
    for cat in thin_categories(per_cat):
        targets.append((1, per_cat[cat],
                        f"{cat}: only {per_cat[cat]} curated source(s) catalog-wide "
                        f"- run `discover --category {cat}`"))
    for country in sorted(thin_countries):
        n = sum(by_country[country].values())
        targets.append((2, n, f"{country}: only {n} curated source(s) - broaden "
                              f"metros and categories"))
    # Capped at 5, not 12: every entry here scores tot=1, so `sorted()` ranks
    # them ALPHABETICALLY by metro and a wider slice just prints more of the
    # letter A (Aarhus, Abu Dhabi, Ahmedabad, Albany, Alberta...). Five is enough
    # to show the shape without crowding out the tiers below, which is what the
    # old twelve did to every category gap.
    for tot, metro, country, only in sorted(single_type)[:5]:
        targets.append((3, tot, f"{metro} ({country}): {tot} source(s), all "
                                f"'{only}' - add another feed type"))
    for country, missing in sorted(cat_gaps.items(),
                                   key=lambda kv: -len(kv[1])):
        if country in thin_countries:
            continue                       # already listed above with more urgency
        targets.append((4, -len(missing),
                        f"{country}: no {', '.join(missing[:5])} feeds yet"))
    for i, (_p, _s, msg) in enumerate(sorted(targets)[:20], 1):
        print(f"{i:>3}. {msg}")
    if not targets:
        print("  (no gaps detected)")
    return 0


def thin_categories(per_cat):
    """The curated categories that are STARVED relative to the rest, thinnest first.

    The loop has always been able to see a thin COUNTRY and a thin METRO and has
    never once been able to see a thin CATEGORY, which is the axis the front
    doors are actually built on — a lens opens onto categories, not onto
    countries. Two things hid it:

      • `cmd_coverage`'s ranked list scores category gaps at priority 3, behind
        uncovered countries, thin countries and single-type metros. Those three
        produced 9 + 12 = 21 entries against a 20-line cap, so a category has
        never appeared in the ranked targets at all.
      • `curate-catalog.yml`'s Wednesday gap sweep pins to `zero_categories`,
        and nothing has been at zero since fitness and running were seeded. An
        empty pin means "sweep everything", so the category-pinned run has been
        a second copy of the daily run for weeks.

    Meanwhile the spread is 100:1 — measured 2026-09-13: community 1010, market
    525, arts 170, learning 123, fitness 81, outdoors 26, kids 25, running 22,
    volunteer 10.

    BELOW HALF THE MEDIAN, rather than "the bottom four" or a hardcoded ceiling.
    A fixed count would always name four categories even once they had evened
    out, and a hardcoded number would rot the way `_looks_archived`'s year
    ceiling did. Half the median is scale-free: it flags a genuinely lopsided
    catalog (today: volunteer, running, kids, outdoors, against a median of 81)
    and goes quiet on an even one, with no constant to maintain.
    """
    counts = {c: n for c, n in (per_cat or {}).items()}
    if not counts:
        return []
    median = statistics.median(counts.values())
    floor = median / 2.0
    return [c for c, n in sorted(counts.items(), key=lambda kv: (kv[1], kv[0]))
            if n < floor]


def coverage_snapshot():
    """The counts a human reads off cmd_coverage, as data.

    "Are we improving the catalog?" was only answerable by diffing two 500-line
    terminal dumps from different weeks, which is to say it was not answerable.
    curate-catalog.yml appends one of these per run to coverage_history.jsonl,
    so the trend is a file rather than an archaeology exercise — and a category
    that has been at zero for a month is visible as such.
    """
    rows = _coverage_rows()
    cats = curated_categories()
    per_cat = {c: 0 for c in cats}
    for _t, _n, _m, _country, cat in rows:
        if cat in per_cat:
            per_cat[cat] += 1
    per_type = {}
    countries = set()
    for t, _n, _m, country, _c in rows:
        per_type[t] = per_type.get(t, 0) + 1
        if country and country != "?":
            countries.add(country)
    dead, total = 0, 0
    led = _load_ledger()
    if led:
        total = len(led)
        dead = sum(1 for v in led.values()
                   if isinstance(v, dict) and v.get("status") == "fail")
    return {
        "date": _today_int(),
        "total_sources": len(rows),
        "countries": len(countries),
        "per_category": per_cat,
        "per_type": per_type,
        "zero_categories": sorted(c for c, n in per_cat.items() if not n),
        # What the gap sweep should pin to once nothing is at zero. See
        # thin_categories(): without this the Wednesday run silently became a
        # duplicate of the daily one.
        "thin_categories": thin_categories(per_cat),
        "ledger": {"dead": dead, "total": total},
        "roster_live": CURATED_FROM_LIVE,
    }


def cmd_coverage_json():
    print(json.dumps(coverage_snapshot(), sort_keys=True))
    return 0


def cmd_coverage_delta(before_path, after_path):
    """What one curation run actually changed, as markdown for the job summary.

    Lives here rather than as a heredoc in the workflow on purpose: a `python -
    <<'PY'` block inside a `run: |` puts its body at column 0, which terminates
    the YAML block scalar and makes the whole workflow file unparseable — the
    same trap the commit-message comment in curate-catalog.yml already records.
    """
    before = json.load(open(before_path, encoding="utf-8"))
    after = json.load(open(after_path, encoding="utf-8"))
    delta = after["total_sources"] - before["total_sources"]
    out = ["## Catalog delta", "",
           f"**{after['total_sources']} sources** ({delta:+d} this run) across "
           f"{after['countries']} countries.", "",
           "| category | before | after | delta |", "|---|---:|---:|---:|"]
    for c in sorted(after["per_category"]):
        b = before["per_category"].get(c, 0)
        a = after["per_category"][c]
        mark = " **(zero)**" if a == 0 else ""
        out.append(f"| {c}{mark} | {b} | {a} | {a - b:+d} |")
    if after["zero_categories"]:
        out += ["", "> Categories with NO curated supply anywhere: "
                    + ", ".join(f"`{c}`" for c in after["zero_categories"])
                    + ". A lens opening onto one of these shows an empty map."]
    print("\n".join(out))
    return 0


def cmd_cityclass(codes=()):
    """Run the REAL cities() query for each country and report what it returns.

    AGENTS.md's rule for CITY_CLASSES is that a country goes in "verified to
    return rows before it goes in, rather than listed hopefully", and this is
    what makes that rule followable rather than aspirational. It prints the
    three numbers an entry's comment should carry — cities in the first page,
    seconds, the largest city — so re-measuring a country is one command.

    FINDING the class in the first place is the other half and is not here,
    because it needs no SPARQL at all: ask the Wikidata Action API for three or
    four mid-sized towns by name (`wbsearchentities`), read their `P31` claims
    (`wbgetentities`), keep the ones whose `P17` is the country, and drop the
    labels that are not a kind of settlement — a town is also a weather station
    and a federal electoral district, and an electoral district has a population
    AND a website, so the query cannot tell them apart afterwards. Mid-sized on
    purpose: a capital is often its own class that no other town shares. That
    was measured on 2026-09-19 against the sampling query it replaced, which
    asked WDQS to group a whole country's settlements by class: the API route
    answers in a second where the sampling query took 40-120 and spent an
    afternoon answering 502 and 504.
    """
    import catalog_discover_civic as civic
    session = _session()
    codes = [c.upper() for c in codes] or sorted(civic.CITY_CLASSES)
    bad = [c for c in codes if c not in civic.CITY_CLASSES]
    if bad:
        print(f"  FLAG no city class known for {', '.join(bad)} — see CITY_CLASSES")
    rc = 0
    for code in [c for c in codes if c in civic.CITY_CLASSES]:
        classes, scope, label = civic.CITY_CLASSES[code]
        t = time.time()
        try:
            places, rows = civic.cities(session, code, limit=40, offset=0)
        except Exception as exc:                                  # noqa: BLE001
            print(f"  {code}: {type(exc).__name__} — {exc}")
            rc = 1
            continue
        secs = round(time.time() - t, 1)
        if not places:
            print(f"  {code}: NO ROWS in {secs}s over {len(classes)} class(es) "
                  f"({label}) — do not ship this entry")
            rc = 1
            continue
        top = places[0]
        print(f"  {code}: {len(places)} cities from {rows} rows in {secs}s "
              f"over {len(classes)} class(es); largest {top['name']} "
              f"({top['population']:,}) {top['url'][:44]}")
        print(f"       suffix: {civic._suffix(top)!r}")
    return rc


def main(argv):
    cmds = {"verify", "merge", "audit", "ledger", "coverage", "discover",
            "reapply", "cityclass", "robots"}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "reapply":
        if len(argv) < 3:
            print("error: reapply needs the snapshot directory")
            return 2
        return cmd_reapply(argv[2])
    if cmd == "discover":
        lim = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 400
        out = argv[argv.index("--out") + 1] if "--out" in argv else "candidates.json"
        only = ()
        if "--category" in argv:
            only = tuple(c.strip() for c in argv[argv.index("--category") + 1].split(",")
                         if c.strip())
        # POSITIONAL, and it must sit at argv[2] before any flag — the workflow
        # spells the two forms out rather than interpolating for this reason.
        want = argv[2:3]
        backend = want[0] if want and want[0] in BACKENDS else "socrata"
        # osm only: how many metros this run walks. The cursor makes the metros
        # it does not reach simply where the next run starts, so this is a budget
        # knob and never a coverage decision.
        metros = int(argv[argv.index("--metros") + 1]) if "--metros" in argv else None
        # osm only: a WALL-CLOCK budget, because the metro count does not predict
        # the time — the cost is an Overpass call plus a fetch per venue with a
        # website, against servers we do not control. 0 = no limit.
        mm = (float(argv[argv.index("--max-minutes") + 1])
              if "--max-minutes" in argv else 0.0)
        # osm only: pin the walk to these OSM kinds (`community_centre,library`)
        # — its own cursor, its own pace. See _discover_osm.
        kinds = ()
        if "--kinds" in argv:
            kinds = tuple(k.strip() for k in argv[argv.index("--kinds") + 1].split(",")
                          if k.strip())
        # osm only: venues probed at once. Absent keeps OSM_PROBE_WORKERS (4);
        # the flag is for the owner's A/B of 8 against 4 on one metro
        # (2026-10-23: the host gate slept 37-53% of worker time in runs 108,
        # 121 and 123), not a new default.
        workers = (int(argv[argv.index("--workers") + 1])
                   if "--workers" in argv else None)
        return cmd_discover(limit=lim, out=out, backend=backend, only=only,
                            metros=metros, max_minutes=mm, kinds=kinds,
                            workers=workers)
    if cmd == "cityclass":
        # Every configured country, or the ones named: `cityclass CH DE`.
        return cmd_cityclass(tuple(a for a in argv[2:] if not a.startswith("-")))
    if cmd == "audit":
        # A WALL-CLOCK budget, for the reason the osm walk has one: the cost is
        # other people's servers, and 1.3% of the probes took 55% of 2026-10-25's
        # four hours. 0 = no limit (a manual run that should read everything).
        mm = (float(argv[argv.index("--max-minutes") + 1])
              if "--max-minutes" in argv else 0.0)
        workers = (int(argv[argv.index("--workers") + 1])
                   if "--workers" in argv else None)
        return cmd_audit(max_minutes=mm, workers=workers)
    if cmd == "robots":
        return cmd_robots()
    if cmd == "ledger":
        if "--offsite" in argv:
            return cmd_ledger_offsite(refresh="--refresh" in argv)
        if "--held" in argv:
            return cmd_ledger_held()
        return cmd_ledger()
    if cmd == "coverage":
        if "--delta" in argv:
            i = argv.index("--delta")
            if len(argv) < i + 3:
                print("error: --delta needs a BEFORE and an AFTER json path")
                return 2
            return cmd_coverage_delta(argv[i + 1], argv[i + 2])
        return cmd_coverage_json() if "--json" in argv else cmd_coverage()
    if len(argv) < 3:
        print("error: need a candidates file path")
        return 2
    if cmd == "verify":
        recheck = "--recheck" in argv
        ttl = 90
        if "--ttl" in argv:
            ttl = int(argv[argv.index("--ttl") + 1])
        return cmd_verify(argv[2], recheck=recheck, ttl=ttl)
    return cmd_merge(argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
