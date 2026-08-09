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
  python catalog_curate.py discover [socrata|ckan|mobilizon] [--limit 400] [--out candidates.json]
  python catalog_curate.py verify candidates.json [--recheck] [--ttl 90]
  python catalog_curate.py merge  candidates.verified.json
  python catalog_curate.py audit                     # re-check EXISTING configs
  python catalog_curate.py ledger                     # summarize what's been tried
  python catalog_curate.py coverage                   # where the catalog is thin (no network)
"""
import datetime
import json
import os
import re
import sys

import requests

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


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


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
    json.dump(led, open(LEDGER_FILE, "w", encoding="utf-8"), indent=2, sort_keys=True)


DEAD_TTL = 90


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
    if not rec or rec.get("status") != "fail":
        return False
    return _days_since(rec.get("checked", 0)) < ttl


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
    keys = set()
    for fname, key in CONFIG.values():
        p = os.path.join(HERE, fname)
        if os.path.exists(p):
            for e in _entries(fname, json.load(open(p, encoding="utf-8"))):
                keys.add(_canon(e.get(key) or (_ods_url(e) if fname.startswith("ods") else None)))
    return keys


def _key_of(e):
    if e.get("type") in ("localist", "mobilizon"):
        return e.get("base_url")
    if e.get("type") == "ods":
        return e.get("url") or _ods_url(e)
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


def verify_ics(s, e):
    r = s.get(e["url"], timeout=60)
    txt = r.text
    if r.status_code != 200 or "BEGIN:VCALENDAR" not in txt[:400]:
        return False, f"http {r.status_code} / no VCALENDAR"
    today = _today_int()
    # Scope the scan to VEVENT BODIES. Scanning the whole file also catches the
    # DTSTARTs inside VTIMEZONE STANDARD/DAYLIGHT sub-components, which carry
    # DST-transition dates - so a calendar whose real events all ended years ago
    # can report thousands of "future" dates and sail through this gate on
    # timezone metadata alone. (Seen in the wild: a dead market calendar
    # reporting "51 vevents / 15946 future" under two different URLs.)
    bodies = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", txt, re.S)
    starts = [d for b in bodies for d in re.findall(r"^DTSTART[^:\n]*:(\d{8})", b, re.M)]
    fut = sum(1 for d in starts if int(d) >= today)
    return (fut > 0), f"{len(bodies)} vevents / {fut} future"


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
    start_col = m.get("start")
    if start_col:
        today = _as_date(_today_int()).isoformat()
        iso = [str(row.get(start_col) or "")[:10] for row in rows]
        parseable = [v for v in iso if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)]
        if not parseable:
            return False, f"'{start_col}' is not a date column (sample: {iso[0]!r})"
        if not any(v >= today for v in parseable):
            return False, f"no future rows (latest sampled {max(parseable)})"
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


def verify_mobilizon(s, e):
    base = (e.get("base_url") or "").rstrip("/")
    if not base:
        return False, "no base_url"
    begins = _as_date(_today_int()).isoformat() + "T00:00:00Z"
    r = s.post(f"{base}/api", timeout=30,
               json={"query": MOBILIZON_PROBE, "variables": {"b": begins, "l": 5}},
               headers={"Content-Type": "application/json", "Accept": "application/json"})
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    try:
        body = r.json() or {}
    except Exception:  # noqa: BLE001
        return False, "not json (is this a Mobilizon instance?)"
    if body.get("errors"):
        return False, f"graphql: {str(body['errors'])[:70]}"
    node = (body.get("data") or {}).get("searchEvents") or {}
    rows = [x for x in (node.get("elements") or []) if x]
    if not rows:
        return False, "0 future events"
    return True, f"{node.get('total', len(rows))} upcoming, sample: {str(rows[0].get('title'))[:40]}"


VERIFIERS = {"localist": verify_localist, "ics": verify_ics, "opendata": verify_opendata,
             "ods": verify_ods, "ckan": verify_ckan, "mobilizon": verify_mobilizon}


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
]
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
BACKENDS = ("socrata", "ckan", "mobilizon")
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


def cmd_discover(limit=400, out="candidates.json", backend="socrata", only=()):
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
    cursor = all_cursors.setdefault(backend, {})
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
    elif backend == "ckan":
        _report_query_gaps(cats, CKAN_QUERIES, "ckan")
        queries = _order_queries(_filter_queries(CKAN_QUERIES, only), cats)
        found, skipped = _discover_ckan(session, seen_keys, led, limit, cursor, queries)
    else:
        _report_query_gaps(cats, DISCOVER_QUERIES, "socrata")
        queries = _order_queries(_filter_queries(DISCOVER_QUERIES, only), cats)
        found, skipped = _discover_socrata(session, seen_keys, led, limit, cursor, queries)

    all_cursors[backend] = cursor
    _save_cursor(all_cursors)
    out_list = list(found.values())[:limit]
    json.dump(out_list, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{len(out_list)} candidate(s) -> {out}")
    print("skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(skipped.items())))
    print(f"Next: python catalog_curate.py verify {out}")
    return 0


# --- commands --------------------------------------------------------------
def cmd_verify(path, recheck=False, ttl=90):
    cands = json.load(open(path, encoding="utf-8"))
    s = _session()
    led = _load_ledger()
    in_config = _config_keys()
    passed, skipped = [], 0
    for e in cands:
        t = e.get("type")
        key = _canon(_key_of(e))
        if t not in VERIFIERS:
            print(f"[SKIP] unknown type={t!r} {e.get('name')}")
            continue
        if not recheck and key in in_config:
            print(f"[SKIP] {t:8} already in config: {e.get('name','?')[:44]}")
            skipped += 1
            continue
        rec = led.get(key)
        if not recheck and rec and rec.get("status") == "fail":
            age = _days_since(rec.get("checked", 0))
            if age < ttl:
                print(f"[SKIP] {t:8} known-dead {age}d ago ({rec.get('reason','')[:30]}): "
                      f"{e.get('name','?')[:36]}")
                skipped += 1
                continue
        try:
            ok, note = VERIFIERS[t](s, e)
        except Exception as ex:  # noqa: BLE001
            ok, note = False, f"{type(ex).__name__}: {str(ex)[:70]}"
        led[key] = {"type": t, "name": e.get("name"), "status": "ok" if ok else "fail",
                    "reason": note, "checked": _today_int()}
        print(f"[{'OK ' if ok else 'XX '}] {t:8} {e.get('name','?')[:46]:46} {note}")
        if ok:
            passed.append(e)
    _save_ledger(led)
    out = os.path.splitext(path)[0] + ".verified.json"
    json.dump(passed, open(out, "w", encoding="utf-8"), indent=2)
    print(f"\n{len(passed)} verified, {skipped} skipped (known) of {len(cands)} -> {out}")
    return 0


def cmd_merge(path):
    verified = json.load(open(path, encoding="utf-8"))
    by_type = {}
    for e in verified:
        by_type.setdefault(e.get("type"), []).append(e)
    for t, adds in by_type.items():
        if t not in CONFIG:
            print(f"[SKIP] unknown type {t!r}")
            continue
        fname, key = CONFIG[t]
        fpath = os.path.join(HERE, fname)
        doc = json.load(open(fpath, encoding="utf-8"))
        cur = _entries(fname, doc)
        seen = {_canon(c.get(key) or (_ods_url(c) if t == "ods" else None)) for c in cur}
        added = 0
        for e in adds:
            entry = {k: v for k, v in e.items() if k != "type"}
            if t == "ods":
                entry["url"] = _ods_url(entry)     # derived key, kept for dedup
            ck = _canon(entry.get(key))
            if ck in seen:
                continue
            cur.append(entry)
            seen.add(ck)
            added += 1
        out_doc = doc if isinstance(doc, dict) else cur   # nested files keep their wrapper
        json.dump(out_doc, open(fpath, "w", encoding="utf-8"), indent=2)
        json.load(open(fpath, encoding="utf-8"))  # validate it still parses
        print(f"{fname}: +{added} -> {len(cur)} total")
    return 0


def cmd_audit():
    s = _session()
    led = _load_ledger()
    stale = 0
    for t, (fname, key) in CONFIG.items():
        fpath = os.path.join(HERE, fname)
        # _entries(), not a bare json.load: ods_sources.json nests its list under
        # "sources", so iterating the raw object walks the dict KEYS ("_comment",
        # "sources") and blows up on dict(e, type=t). Same trap that hit
        # _coverage_rows(); every reader of these configs must go through here.
        cur = _entries(fname, json.load(open(fpath, encoding="utf-8")))
        print(f"\n=== {fname} ({len(cur)}) ===")
        for e in cur:
            e2 = dict(e, type=t)
            try:
                ok, note = VERIFIERS[t](s, e2)
            except Exception as ex:  # noqa: BLE001
                ok, note = False, f"{type(ex).__name__}: {str(ex)[:60]}"
            if not ok:
                stale += 1
            led[_canon(_key_of(e2))] = {"type": t, "name": e.get("name"),
                                        "status": "ok" if ok else "fail", "reason": note,
                                        "checked": _today_int()}
            print(f"[{'OK ' if ok else 'XX '}] {e.get(key,'?')[:60]:60} {note}")
    _save_ledger(led)
    print(f"\n{stale} entries need attention (dead or no future events).")
    return 0


def cmd_ledger():
    led = _load_ledger()
    good = {k: v for k, v in led.items() if v.get("status") == "ok"}
    dead = {k: v for k, v in led.items() if v.get("status") == "fail"}
    print(f"ledger: {len(led)} tried  |  {len(good)} ok  |  {len(dead)} dead\n")
    print("known-dead (skipped until TTL lapses):")
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
_COUNTRY_ALIASES = {
    "UK": "United Kingdom", "United Kingdom": "United Kingdom",
    "US": "United States", "USA": "United States", "United States": "United States",
    "Canada": "Canada", "Australia": "Australia", "Ireland": "Ireland",
    "New Zealand": "New Zealand", "Singapore": "Singapore", "India": "India",
    "South Africa": "South Africa", "France": "France", "Germany": "Germany",
    "Netherlands": "Netherlands", "Mexico": "Mexico", "Japan": "Japan",
}
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
    if last.upper() in _US_STATES and len(last) == 2:
        # '(DC)' alone is a metro name, not a bare state
        metro = parts[-2] if len(parts) >= 2 else _METRO_ALIASES.get(last)
        return _METRO_ALIASES.get(metro, metro), "United States"
    country = _COUNTRY_ALIASES.get(last)
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


def _name_paren(name):
    """The '(City...)' suffix curators put in source names, if any."""
    m = re.search(r"\(([^()]+)\)\s*$", name or "")
    return m.group(1).strip() if m else None


def _locate(name, suffix):
    """Best-effort (metro, country) for one source entry. The name's '(City...)'
    suffix carries metro intent ('DC metro'); the geocode_suffix pins country."""
    s_metro, s_country = _parse_place((suffix or "").strip(" ,"))
    p_metro, p_country = _parse_place(_name_paren(name)) if _name_paren(name) else (None, None)
    metro = p_metro or s_metro
    country = s_country or p_country
    if not country and metro:
        country = "United States"          # config convention: bare '(City)' = US
    return metro or "?", country or "?"


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
}


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
    out = []
    for e in data.get("sites", []):
        name = e.get("name") or "?"
        metro = _scan_name_metro(name) or "?"
        country = _url_country((e.get("listing") or [None])[0])
        if not country:
            country = "United States" if metro != "?" else "?"
        out.append((name, metro, country, e.get("category") or "?"))
    return out


def _rows_program(data):
    out = []
    for e in data:
        name = e.get("name") or "?"
        # The sites carry full street addresses; the first one locates the program.
        addr = ((e.get("sites") or [{}])[0].get("address") or "")
        metro, country = _parse_place(re.sub(r"\s+\d{5}(?:-\d{4})?\s*$", "", addr))
        if not metro or metro == "?":
            metro = _scan_name_metro(name) or "?"
        out.append((name, metro, country or _tz_country(e.get("timezone")) or "?",
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
    return [("parkrun", "(worldwide)", _ISO_COUNTRY.get(c.upper(), c.upper()), cat)
            for c in (data.get("countries") or ["?"])]


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
EXTRA_CONFIG = {
    "market": ("market_sources.json", _rows_market),
    "parkrun": ("parkrun_sources.json", _rows_parkrun),
    "jsonld": ("jsonld_sources.json", _rows_jsonld),
    "program": ("program_sources.json", _rows_program),
    "venuepilot": ("venuepilot_sources.json", _rows_venuepilot),
    "restaurant": ("restaurant_sources.json", _rows_restaurant),
    "affiliate": ("affiliate_sources.json", _rows_affiliate),
}
ALL_TYPES = sorted(CONFIG) + sorted(EXTRA_CONFIG)


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


def _coverage_rows():
    """One row per configured source: (type, name, metro, country, category)."""
    rows = []
    for t, (fname, _key) in sorted(CONFIG.items()):
        p = os.path.join(HERE, fname)
        if not os.path.exists(p):
            continue
        for e in _entries(fname, json.load(open(p, encoding="utf-8"))):
            name = e.get("name") or "?"
            suffix = e.get("geocode_suffix") or ""
            metro, country = _locate(name, suffix)
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
                country = e["default_country"]
            cat = e.get("category") or ("(per-event)" if t == "localist" else "?")
            # ODS entries carry an ISO `countries` array instead of a
            # geocode_suffix, so _locate() cannot see them and they landed under
            # "?" - which made a covered country still read as thin and sent the
            # next curation run back over ground it had already won (Belgium
            # showed 0 with 1363 events configured; Brisbane 11 datasets showed
            # as nothing). Emit one row per declared country instead.
            iso = [c for c in (e.get("countries") or []) if c]
            if iso:
                for code in iso:
                    rows.append((t, name, metro, _ISO_COUNTRY.get(code.upper(), code.upper()), cat))
                continue
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
        if len(tc) == 1:
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
    for country in uncovered:
        metros = ", ".join(sweep[country][:5])
        targets.append((0, 0, f"{country}: 0 curated feeds; API sweep already covers "
                              f"{metros} - seed community/learning feeds there"))
    for country in sorted(thin_countries):
        n = sum(by_country[country].values())
        targets.append((1, n, f"{country}: only {n} curated source(s) - broaden "
                              f"metros and categories"))
    for tot, metro, country, only in sorted(single_type)[:12]:
        targets.append((2, tot, f"{metro} ({country}): {tot} source(s), all "
                                f"'{only}' - add another feed type"))
    for country, missing in sorted(cat_gaps.items(),
                                   key=lambda kv: -len(kv[1])):
        if country in thin_countries:
            continue                       # already listed above with more urgency
        targets.append((3, -len(missing),
                        f"{country}: no {', '.join(missing[:5])} feeds yet"))
    for i, (_p, _s, msg) in enumerate(sorted(targets)[:20], 1):
        print(f"{i:>3}. {msg}")
    if not targets:
        print("  (no gaps detected)")
    return 0


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


def main(argv):
    cmds = {"verify", "merge", "audit", "ledger", "coverage", "discover"}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    cmd = argv[1]
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
        return cmd_discover(limit=lim, out=out, backend=backend, only=only)
    if cmd == "audit":
        return cmd_audit()
    if cmd == "ledger":
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
