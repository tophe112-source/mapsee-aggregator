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
    return True, f"{len(rows)} rows, sample: {rows[0].get(m.get('title',''))}"


def verify_ods(s, e):
    r = s.get(_ods_url(e), params={"limit": 3}, timeout=25)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return False, f"http {r.status_code}"
    n = (r.json() or {}).get("total_count", 0)
    return (n > 0), f"{n} records"


VERIFIERS = {"localist": verify_localist, "ics": verify_ics, "opendata": verify_opendata, "ods": verify_ods}


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


_ISO_COUNTRY = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "NZ": "New Zealand", "IE": "Ireland", "FR": "France", "DE": "Germany",
    "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland", "ES": "Spain",
    "IT": "Italy", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "AT": "Austria", "PL": "Poland", "PT": "Portugal", "CZ": "Czechia",
    "MX": "Mexico", "BR": "Brazil", "IN": "India", "JP": "Japan", "KR": "South Korea",
    "SG": "Singapore", "HK": "Hong Kong", "AE": "United Arab Emirates",
    "ZA": "South Africa",
}


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
                # 'NYC Parks Events', 'Seattle University' carry the metro in
                # the bare name; scan for aliases and big-city names
                scan = dict(_METRO_ALIASES)
                scan.update({c: c for c in ("Seattle", "New York", "Chicago",
                                            "Boston", "Austin", "Denver",
                                            "Miami", "Atlanta")})
                for alias, canon in scan.items():
                    if re.search(r"\b" + re.escape(alias) + r"\b", name):
                        metro, country = canon, "United States"
                        break
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
    return rows


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
    types = sorted(CONFIG)
    print("=== mapsee catalog coverage ===")
    counts = {t: sum(1 for r in rows if r[0] == t) for t in types}
    print("  " + "  ".join(f"{t}={counts[t]}" for t in types) + f"  total={len(rows)}\n")

    # -- per country --
    by_country = {}
    for t, _n, _m, country, _c in rows:
        by_country.setdefault(country, {}).setdefault(t, 0)
        by_country[country][t] += 1
    print("-- sources per country --")
    print(f"{'country':<22}" + "".join(f"{t:>10}" for t in types) + f"{'total':>8}")
    for country, tc in sorted(by_country.items(), key=lambda kv: -sum(kv[1].values())):
        tot = sum(tc.values())
        print(f"{country:<22}" + "".join(f"{tc.get(t, 0):>10}" for t in types) + f"{tot:>8}")
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
    print(f"{'metro':<28}{'country':<20}" + "".join(f"{t:>10}" for t in types) + f"{'total':>8}")
    single_type = []
    for (metro, country), tc in sorted(by_metro.items(),
                                       key=lambda kv: (-sum(kv[1].values()), kv[0])):
        tot = sum(tc.values())
        star = "*" if len(tc) == 1 else " "
        if len(tc) == 1:
            single_type.append((tot, metro, country, next(iter(tc))))
        print(f"{star}{metro:<27}{country:<20}"
              + "".join(f"{tc.get(t, 0):>10}" for t in types) + f"{tot:>8}")

    # -- categories per country (localist is per-event, counted separately) --
    cat_by_country = {}
    for t, _n, _m, country, cat in rows:
        cat_by_country.setdefault(country, {}).setdefault(cat, 0)
        cat_by_country[country][cat] += 1
    print("\n-- curated categories per country (ics + opendata; '.' = none) --")
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
    cmds = {"verify", "merge", "audit", "ledger", "coverage"}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    cmd = argv[1]
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
