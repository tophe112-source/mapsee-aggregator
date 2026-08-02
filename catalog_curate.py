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
result and the date. verify() skips anything already in a config and anything
that failed within the last --ttl days (default 90), so scheduled runs stop
re-probing known-dead sources and spend their effort on genuinely new ground.
Dead sources are rechecked once the TTL lapses, in case they came online.

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
  # find NEW Socrata event datasets across every Socrata domain, as candidates
  python catalog_curate.py discover [--limit 400] [--out candidates.json]
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
}

def _ods_url(e):
    return f"https://{e.get('domain')}/api/explore/v2.1/catalog/datasets/{e.get('dataset')}/records"

def _entries(fname, data):
    return data.get("sources", []) if isinstance(data, dict) else data


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


def _config_keys():
    keys = set()
    for fname, key in CONFIG.values():
        p = os.path.join(HERE, fname)
        if os.path.exists(p):
            for e in _entries(fname, json.load(open(p, encoding="utf-8"))):
                keys.add(_canon(e.get(key) or (_ods_url(e) if fname.startswith("ods") else None)))
    return keys


def _key_of(e):
    if e.get("type") == "localist":
        return e.get("base_url")
    if e.get("type") == "ods":
        return e.get("url") or _ods_url(e)
    return e.get("url")


# --- verifiers -------------------------------------------------------------
def verify_localist(s, e):
    r = s.get(e["base_url"].rstrip("/") + "/api/2/events?days=30&pp=5", timeout=15)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    n = len(r.json().get("events") or [])
    return (n > 0), f"{n} events"


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
    r = s.get(_ods_url(e), params={"limit": 3}, timeout=25)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    n = (r.json() or {}).get("total_count", 0)
    return (n > 0), f"{n} records"


VERIFIERS = {"localist": verify_localist, "ics": verify_ics, "opendata": verify_opendata, "ods": verify_ods}


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
]
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
_DISCOVER_REJECT = re.compile(
    r"permit|parking|licen[cs]e|application|violation|citation|inspection|"
    r"archive|historical|\b(19|20)([01]\d|2[0-4])\b", re.I)


def _pick(fields, pattern):
    rx = re.compile(pattern, re.I)
    for f in fields:
        if rx.search(f):
            return f
    return None


def _infer_map(fields, datatypes):
    """A candidate `map` from column names, or None when the shape can't work."""
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
        else:
            return None
    if not (m.get("title") and m.get("start")):
        return None
    return m


def cmd_discover(limit=400, out="candidates.json", per_query=100):
    seen_keys = _config_keys()
    led = _load_ledger()
    session = _session()
    found, skipped = {}, {"configured": 0, "no_shape": 0, "ledger": 0, "not_events": 0}
    for q, category in DISCOVER_QUERIES:
        try:
            r = session.get(SOCRATA_CATALOG, timeout=45,
                            params={"q": q, "only": "dataset", "limit": per_query})
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as exc:  # noqa: BLE001
            print(f"  ({q}: {exc})")
            continue
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
            if key in led and led[key].get("ok") is False:
                skipped["ledger"] += 1
                continue
            if _DISCOVER_REJECT.search(res.get("name") or ""):
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
        print(f"  {q:22} -> {len(found)} candidate(s) so far")
        if len(found) >= limit:
            break
    out_list = list(found.values())[:limit]
    json.dump(out_list, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{len(out_list)} candidate(s) -> {out}")
    print(f"skipped: {skipped['configured']} already configured, "
          f"{skipped['ledger']} known dead, {skipped['not_events']} permit/archive "
          f"tables, {skipped['no_shape']} without a usable title/date/location shape")
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
# App category keys that curated feeds are responsible for (ticketing APIs
# already blanket music/theater/sports/food nationally - see the skill doc).
_CURATED_CATEGORIES = ["community", "learning", "arts", "outdoors", "volunteer",
                       "kids", "market", "running", "party"]


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
    "uk": "GB", "ie": "IE", "ca": "CA", "au": "AU", "nz": "NZ", "de": "DE",
    "fr": "FR", "nl": "NL", "be": "BE", "ch": "CH", "es": "ES", "it": "IT",
    "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "at": "AT", "pl": "PL",
    "pt": "PT", "cz": "CZ", "mx": "MX", "in": "IN", "jp": "JP", "kr": "KR",
    "sg": "SG", "hk": "HK", "ae": "AE", "za": "ZA",
}

_ISO_COUNTRY = {
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
    print("\n-- curated categories per country (every config; '.' = none) --")
    print(f"{'country':<22}" + "".join(f"{c[:6]:>8}" for c in _CURATED_CATEGORIES))
    cat_gaps = {}
    for country in sorted(cat_by_country, key=lambda c: -sum(cat_by_country[c].values())):
        cc = cat_by_country[country]
        cells = "".join(f"{cc.get(c) or '.':>8}" for c in _CURATED_CATEGORIES)
        print(f"{country:<22}{cells}")
        if country != "?":
            missing = [c for c in _CURATED_CATEGORIES if not cc.get(c)]
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


def main(argv):
    cmds = {"verify", "merge", "audit", "ledger", "coverage", "discover"}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "discover":
        lim = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 400
        out = argv[argv.index("--out") + 1] if "--out" in argv else "candidates.json"
        return cmd_discover(limit=lim, out=out)
    if cmd == "audit":
        return cmd_audit()
    if cmd == "ledger":
        return cmd_ledger()
    if cmd == "coverage":
        return cmd_coverage()
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
