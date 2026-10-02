#!/usr/bin/env python3
"""
mapsee_ingest_programs.py - curated RECURRING community programs into dated map
events (e.g. Seattle's Free Summer Meals: free kids' lunches at parks all summer).

Some high-value civic programs run daily or weekly at fixed locations for a
season but publish NO machine-readable feed - only a flyer or a live map widget.
This adapter turns a hand-curated JSON (locations + weekday schedule + season
window) into one dated event per site per operating day within a rolling
horizon, idempotently (fingerprint = title | date | address). The daily cleanup
prunes past ones and re-runs regenerate the window in place. Every event carries
the program's source URL, so the app's "More on this event" link points at the
official page.

A source with verified year-round weekly hours may opt into `"standing": true`.
That emits one stable venue row with `recurring_days` (weekday-name to
`[opening, closing]`), and Mapsee's existing recurring-hours roller advances its
window. It requires a valid IANA `timezone`, explicit hours and fixed coordinates
for every site; dated, seasonal, monthly and one-off closure rules are rejected.
The weekly roller cannot represent holiday exceptions, so source notes must tell
visitors to verify exceptional closures on the official page. Do not use this
mode when a weekly schedule would misstate whether the place is open.

Config (program_sources.json): a list of programs, each:
  { "name": "Seattle Free Summer Meals", "category": "community",
    "url": "https://www.hungerfreewa.org/freesummerfood",
    "timezone": "America/Los_Angeles",
    "season_start": "2026-06-29", "season_end": "2026-08-20",
    "days": "Monday Tuesday Wednesday Thursday Friday", "horizon_days": 42,
    "title_prefix": "Free Summer Meals",
    "blurb": "Free lunch, games and activities for kids and teens ages 0-18. No registration, just show up.",
    "sites": [ { "name": "North Acres Park",
                 "address": "12718 1st Ave NE, Seattle, WA 98125",
                 "start": "11:00", "end": "14:30",
                 "meals": "Lunch 11am-12pm, Snack 1pm-2pm" }, ... ] }

MONTHLY RULES, for the free museum days (Italy's Domenica al museo, France's
first Sundays at national monuments, Bank of America's Museums on Us). A museum
that is free on the first Sunday is not free on the other three, so a weekday
list alone would put a free day on the map that is not one:
  "nth": 1                     only the Nth of each listed weekday in its month
                               (1 = days 1-7, so "Sunday" + nth 1 = first Sunday)
  "months": [1, 2, 3, 11, 12]  only these months (the season, for a rule that
                               repeats every year and so has no season dates)
  "rule": "first_full_weekend" the first Saturday of the month and the Sunday
                               after it - which is NOT the first Sunday when the
                               1st is a Sunday (then it is the 7th and 8th)
A SITE can narrow its programme, never widen it - a partner that takes part on
Saturdays only, a monument free in fewer months than its network, a house closed
for a marathon on the one Sunday that counted:
  "days": ["Saturday"]            only these weekdays (a list or a string)
  "months": [1, 2, 3]             only these months
  "season_start"/"season_end"     only inside this window
  "exclude_dates": ["2026-11-01"] never on these dates
A site with no "start" is an all-day occurrence (the museum's own opening hours
decide), and a site carrying "lat"/"lon" is not geocoded by this adapter. Set
`"coords_exact": true` when those coordinates are authoritative to prevent the
sync's later Census refresh from replacing them. "city", "region", "country",
"url" and "notes" pass through, and "categories" adds secondaries.

Standing site example:
  { "name": "Year-round museum hours", "standing": true,
    "timezone": "America/New_York", "admission": 0,
    "sites": [{ "name": "Museum", "lat": 37.5, "lon": -77.4,
                "recurring_days": { "Monday": ["10:00", "17:00"],
                                    "Wednesday": ["10:00", "21:00"] } }] }
`admission` is optional. If supplied, its public zero-price fact is normalized
through `mapsee_admission`; an omitted/invalid value does not imply free entry.
Use `"listing_type": "visit_window"` for general museum/monument admission
during opening hours, including monthly admission offers. It preserves the
dated visitor window but lets the public page publish Place search metadata.
Organized tours, concerts and other occasions keep the default Event type.

The offer:free catalog tag is assigned by ../mapsee's migration 0227 from each
event's text, so a programme's blurb has to SAY what the
offer is, and say it honestly: "Free admission for everyone" is tagged free;
"cardholders get free general admission" is not, because it is free for some.

    python mapsee_ingest_programs.py --config program_sources.json --store mapsee_events.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from mapsee_geo_budget import geocode_allowed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint
from mapsee_admission import admission_description, normalize_admission_facts

# ---- persistent geocode cache (shared with the other adapters) --------------
GEO_CACHE_PATH = os.environ.get("GEOCODE_CACHE", "geocode_cache.json")


def _load_cache() -> Dict[str, Any]:
    try:
        return json.loads(open(GEO_CACHE_PATH, encoding="utf-8").read())
    except Exception:
        return {}


def _save_cache(c: Dict[str, Any]) -> None:
    try:
        open(GEO_CACHE_PATH, "w", encoding="utf-8").write(json.dumps(c))
    except Exception:
        pass


_CACHE = _load_cache()


def _geocode(session, query: str) -> Tuple[Optional[float], Optional[float]]:
    key = f"{query}|program".strip().lower()
    if key in _CACHE:
        return tuple(_CACHE[key])
    if not geocode_allowed():                        # over this run's budget → retry next run
        return (None, None)
    out: Tuple[Optional[float], Optional[float]] = (None, None)
    try:
        time.sleep(1.1)                              # Photon fair-use pacing
        r = session.get("https://photon.komoot.io/api/", params={"q": query, "limit": 1}, timeout=20)
        f = (r.json().get("features") or [None])[0]
        if f:
            c = f["geometry"]["coordinates"]
            out = (c[1], c[0])
    except Exception:
        pass
    _CACHE[key] = list(out)
    return out


_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
         "friday": 4, "saturday": 5, "sunday": 6}


def _weekdays(s) -> List[int]:
    s = (" ".join(s) if isinstance(s, (list, tuple)) else (s or "")).lower()
    return sorted({v for k, v in _DAYS.items() if k in s})


def _tz(name: Optional[str]):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _localize(ds: str, t: Optional[str], tz) -> Tuple[str, Optional[str]]:
    """(start_local, start_utc) for a date + 'HH:MM' in tz. Without tz -> naive
    local (start_utc None); with tz -> a real UTC instant so the app shows the
    program at its true local time."""
    if not t:
        return ds, None
    if tz is not None:
        dt = datetime.fromisoformat(f"{ds}T{t}:00").replace(tzinfo=tz)
        return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{ds}T{t}:00", None


def _as_date(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _today():
    """MAPSEE_TODAY=YYYYMMDD fixes "today" for the tests, as everywhere else."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    return datetime.now().date()


def _occurrences(weekdays: List[int], horizon_days: int, s_start, s_end,
                 nth: Optional[int] = None, months: Optional[List[int]] = None,
                 rule: Optional[str] = None) -> List:
    """Every matching weekday from today through the horizon, clamped to the
    program's season window, and narrowed by the monthly rules in the header."""
    today = _today()
    out = []
    for i in range(horizon_days):
        d = today + timedelta(days=i)
        if rule == "first_full_weekend":
            # The first Saturday is day 1-7 and its Sunday is day 2-8. The Sunday
            # is tested by looking back one day, so a month that STARTS on a
            # Sunday does not count that Sunday: its Saturday was last month.
            sat = d - timedelta(days=1)
            if not ((d.weekday() == 5 and d.day <= 7)
                    or (d.weekday() == 6 and sat.month == d.month and sat.day <= 7)):
                continue
        elif d.weekday() not in weekdays:
            continue
        if nth and (d.day - 1) // 7 + 1 != int(nth):
            continue
        if months and d.month not in months:
            continue
        if s_start and d < s_start:
            continue
        if s_end and d > s_end:
            continue
        out.append(d)
    return out


def _site_dates(site: Dict[str, Any], dates: List) -> List:
    """The programme's dates this one site keeps (its narrowing rules, header)."""
    only_days = _weekdays(site["days"]) if site.get("days") else None
    only_months = site.get("months")
    first, last = _as_date(site.get("season_start")), _as_date(site.get("season_end"))
    closed = {str(x)[:10] for x in (site.get("exclude_dates") or ())}
    return [d for d in dates
            if (only_days is None or d.weekday() in only_days)
            and (not only_months or d.month in only_months)
            and (not first or d >= first) and (not last or d <= last)
            and d.isoformat() not in closed]


_STANDING_SCHEDULE_KEYS = frozenset(("season_start", "season_end", "nth", "months", "rule", "days", "exclude_dates"))
_STANDING_SITE_DATE_KEYS = _STANDING_SCHEDULE_KEYS | {"exclude_dates"}
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _standing_timezone(name: Any):
    """Require a declared IANA zone for standing rows; never fall back to UTC."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("standing program requires a valid timezone")
    zone_name = name.strip()
    if zone_name != "UTC" and "/" not in zone_name:
        raise ValueError(f"standing program timezone must be an IANA name: {name!r}")
    zone = _tz(zone_name)
    if zone is None:
        raise ValueError(f"standing program has an unknown timezone: {name!r}")
    return zone


def _standing_weekly_hours(raw: Any) -> Dict[str, List[str]]:
    """Validate weekday-name -> [opening, closing] and emit sync's 0=Mon keys."""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("standing site requires recurring_days with at least one weekday")
    out: Dict[str, List[str]] = {}
    for day, times in raw.items():
        if not isinstance(day, str) or day.strip().lower() not in _DAYS:
            raise ValueError(f"standing site has an invalid weekday: {day!r}")
        key = str(_DAYS[day.strip().lower()])
        if key in out:
            raise ValueError(f"standing site repeats weekday: {day!r}")
        if (not isinstance(times, (list, tuple)) or len(times) != 2
                or any(not isinstance(value, str) or not _HHMM.fullmatch(value) for value in times)):
            raise ValueError(f"standing site needs two HH:MM times for {day}")
        start = datetime.strptime(times[0], "%H:%M").time()
        end = datetime.strptime(times[1], "%H:%M").time()
        if start >= end:
            raise ValueError(f"standing site hours must close after opening on {day}")
        out[key] = [times[0], times[1]]
    return out


def _standing_events(prog: Dict[str, Any], session) -> List[NormalizedEvent]:
    """A standing venue is one stable row; the DB roller advances its window."""
    if _STANDING_SCHEDULE_KEYS.intersection(prog):
        raise ValueError("standing program cannot use seasonal, monthly, or dated weekday rules")
    tz = _standing_timezone(prog.get("timezone"))
    sites = prog.get("sites")
    if not isinstance(sites, list) or not sites:
        raise ValueError("standing program requires at least one site")
    category = prog.get("category", "community")
    extra_cats = [c for c in (prog.get("categories") or []) if c and c != category]
    prefix = (prog.get("title_prefix") or prog.get("name") or "Community program").strip()
    blurb = (prog.get("blurb") or "").strip()
    url = prog.get("url")
    label = "program:" + str(prog.get("name", "program")).lower().replace(" ", "-")
    out: List[NormalizedEvent] = []
    for site in sites:
        if _STANDING_SITE_DATE_KEYS.intersection(site):
            raise ValueError(f"standing site {site.get('name')!r} cannot use seasonal/monthly/date rules")
        weekly = _standing_weekly_hours(site.get("recurring_days"))
        sname = (site.get("name") or "").strip()
        if not sname:
            raise ValueError("standing site requires a name")
        addr = site.get("address")
        lat, lon = site.get("lat"), site.get("lon")
        if (not isinstance(lat, (int, float)) or isinstance(lat, bool)
                or not isinstance(lon, (int, float)) or isinstance(lon, bool)
                or not math.isfinite(lat) or not math.isfinite(lon)
                or not -90 <= lat <= 90 or not -180 <= lon <= 180):
            raise ValueError(f"standing site {sname!r} requires fixed source coordinates")

        # The sync's rolling-hours timezone is resolved from these coordinates.
        # Fail if it would disagree with the source-declared zone used below.
        from mapsee_supabase_sync import _tz_for
        resolved = _tz_for(lat, lon)
        if getattr(resolved, "key", None) != getattr(tz, "key", None):
            raise ValueError(f"standing site {sname!r} coordinates resolve to "
                             f"{getattr(resolved, 'key', None)!r}, not {tz.key!r}")

        fixed_today = os.environ.get("MAPSEE_TODAY")
        if fixed_today:
            now = datetime.combine(datetime.strptime(fixed_today, "%Y%m%d").date(),
                                   datetime.min.time(), tzinfo=tz)
        else:
            now = datetime.now(tz)
        earliest = None
        first_hours = None
        for offset in range(8):
            day = now.date() + timedelta(days=offset)
            hours = weekly.get(str(day.weekday()))
            closing = (datetime.fromisoformat(f"{day.isoformat()}T{hours[1]}:00")
                       .replace(tzinfo=tz) if hours else None)
            if hours and closing > now:
                earliest, first_hours = day, hours
                break
        if earliest is None or first_hours is None:
            # Weekly schedules must include a real day; the validator above
            # already ensures one, but retain this guard at the use site.
            raise ValueError(f"standing site {sname!r} has no upcoming open day")

        title = f"{prefix} - {sname}"
        desc_parts = [blurb, (site.get("notes") or "").strip()]
        desc = " · ".join(part for part in desc_parts if part) or None
        admission = None
        if "admission" in prog:
            admission = normalize_admission_facts(
                prog.get("admission"), url=site.get("url") or url, context=desc or "")
            desc = admission_description(desc, admission)
        fp = make_fingerprint(title, "standing", addr or sname)
        sl, su = _localize(earliest.isoformat(), first_hours[0], tz)
        el, eu = _localize(earliest.isoformat(), first_hours[1], tz)
        ev = NormalizedEvent(
            source=label, source_id=fp, name=title, description=desc,
            start_local=sl, start_utc=su, end_local=el, end_utc=eu,
            timezone=tz.key, venue_name=sname, latitude=lat, longitude=lon, address=addr,
            city=site.get("city"), region=site.get("region"), country=site.get("country"),
            category=category, categories=list(extra_cats),
            ticket_url=site.get("url") or url, recurring_days=weekly,
            coords_exact=True, source_details=admission,
        )
        ev.fingerprint = fp
        out.append(ev)
    return out


def _with_listing_type(events: List[NormalizedEvent], listing_type: Optional[str]) -> List[NormalizedEvent]:
    if listing_type:
        for event in events:
            event.source_details = {**(event.source_details or {}), "listing_type": listing_type}
    return events


def program_events(prog: Dict[str, Any], session) -> List[NormalizedEvent]:
    listing_type = prog.get("listing_type")
    if "listing_type" in prog and listing_type != "visit_window":
        raise ValueError("listing_type must be 'visit_window' when supplied")
    if "standing" in prog and not isinstance(prog["standing"], bool):
        raise ValueError("standing must be a boolean")
    if prog.get("standing") is True:
        return _with_listing_type(_standing_events(prog, session), listing_type)
    weekdays = _weekdays(prog.get("days")) or [0, 1, 2, 3, 4]   # Mon-Fri default
    tz = _tz(prog.get("timezone"))
    s_start, s_end = _as_date(prog.get("season_start")), _as_date(prog.get("season_end"))
    horizon = int(prog.get("horizon_days", 42))
    nth, months, rule = prog.get("nth"), prog.get("months"), prog.get("rule")
    category = prog.get("category", "community")
    extra_cats = [c for c in (prog.get("categories") or []) if c and c != category]
    prefix = prog.get("title_prefix") or prog.get("name") or "Community program"
    blurb = (prog.get("blurb") or "").strip()
    url = prog.get("url")
    label = "program:" + str(prog.get("name", "program")).lower().replace(" ", "-")
    dates = _occurrences(weekdays, horizon, s_start, s_end, nth, months, rule)
    out: List[NormalizedEvent] = []
    for site in prog.get("sites", []):
        sname = (site.get("name") or "").strip()
        if not sname:
            continue
        addr = site.get("address")
        lat, lon = site.get("lat"), site.get("lon")
        if lat is None or lon is None:
            # The site's own place when it names one: the Seattle fallback predates
            # any programme outside Seattle, and would pin an Italian museum there.
            where = ", ".join(x for x in (site.get("city"), site.get("region"), site.get("country")) if x)
            lat, lon = _geocode(session, addr or (f"{sname}, {where}" if where else f"{sname}, Seattle, WA"))
        if lat is None or lon is None:
            continue                                 # nowhere to pin it (yet - retried next run)
        title = f"{prefix} - {sname}"
        meals = (site.get("meals") or "").strip()
        notes = (site.get("notes") or "").strip()
        desc = " · ".join(x for x in (blurb, meals, notes) if x) or None
        admission = None
        if "admission" in prog:
            admission = normalize_admission_facts(
                prog.get("admission"), url=site.get("url") or url, context=desc or "")
            desc = admission_description(desc, admission)
        start_t, end_t = site.get("start"), site.get("end")
        for d in _site_dates(site, dates):
            ds = d.isoformat()
            fp = make_fingerprint(title, ds, addr or sname)
            sl, su = _localize(ds, start_t, tz)
            el, eu = _localize(ds, end_t, tz)
            ev = NormalizedEvent(
                source=label, source_id=fp, name=title, description=desc,
                start_local=sl, start_utc=su, end_local=el, end_utc=eu,
                venue_name=sname, latitude=lat, longitude=lon, address=addr,
                city=site.get("city"), region=site.get("region"), country=site.get("country"),
                category=category, categories=list(extra_cats),
                ticket_url=site.get("url") or url,
                coords_exact=site.get("coords_exact") is True,
                source_details=admission,
            )
            ev.fingerprint = fp
            out.append(ev)
    return _with_listing_type(out, listing_type)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import curated recurring community programs into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)
    programs = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"})
    store = EventStore(a.store)
    total = 0
    for prog in programs:
        try:
            evs = program_events(prog, session)
            for ev in evs:
                store.upsert(ev)
            total += len(evs)
            print(f"[programs] {prog.get('name', '?')}: +{len(evs)} events across {len(prog.get('sites', []))} sites")
        except Exception as exc:
            print(f"[programs] {prog.get('name', '?')} FAILED: {exc}")
    store.save()
    _save_cache(_CACHE)
    print(f"[programs] done: +{total}; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
