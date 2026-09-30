#!/usr/bin/env python3
"""
mapsee_gcal.py - a public Google Calendar, read the way robots.txt allows.

WHY. calendar.google.com/robots.txt is `Allow: /$` then `Disallow: /`
(2026-09-30), so the public iCal export every Google calendar offers,
`calendar/ical/<id>/public/basic.ics`, is off limits to a crawler - including
the 30 in ics_sources.json that were added before anybody checked. The Calendar
API v3 on www.googleapis.com serves no robots.txt (a 404) and is Google's own
machine route. It wants an identity and nothing more for a PUBLIC calendar.
Called without one, it answers 403 "Method doesn't allow unregistered callers
... Please use API Key or other form of API consumer identity". The key goes in
the `X-Goog-Api-Key` header, never the URL, so no error message or log line
can carry it: a fake key there turns that answer into "API key not valid",
which is how we know the header is read.

HOW IT FITS. Nothing downstream changes. A config entry keeps its basic.ics URL,
because that URL is the calendar's identity everywhere: the ledger key, config
dedupe, the `ics:` source label. When GOOGLE_CALENDAR_API_KEY is set, the ics
adapter and `catalog_curate verify` read the calendar through the API instead,
and get back VCALENDAR text built from the API's events. The ics adapter parses
that exactly as it parses a real feed. With no key set, nothing changes.

THE ROWS MUST NOT MOVE. A row's identity is its fingerprint - (title, date,
venue) - and the date is the one the ics parser reads off DTSTART: the UTC date
for a `...Z` time, the local date for a TZID time. So the text built here
writes a time the way Google's export does. Production shows how: of 227 rows
from 7 Google calendars on 2026-09-29, 221 were timed in UTC, 2 in a local
zone (a recurring series) and 4 were all-day. A one-off event is written in
UTC with its iCalUID, which is the export's UID, so it keeps its row, its UID
and its date. A recurring series is written in its own zone.

AND ONE THING IS BETTER THAN THE EXPORT. The ics parser ignores RRULE, so a
series read from the export yields only its FIRST occurrence (usually long
past) and every later one is lost. The API expands a series into instances
(`singleEvents=true`), and each instance gets a UID of its own:
`<iCalUID>/<originalStartTime>`. They share an iCalUID, and EventStore re-keys
a record whenever one (source, source_id) comes back with a new fingerprint,
so a shared UID would leave only the LAST instance standing.

WHAT IT LEAVES OUT: cancelled events (the API omits them unless asked), events
marked private on a public calendar (the API sends them without a title - on
the map they would be a pin called "Busy"), and anything that is not an
ordinary event (`eventType` birthday, outOfOffice, focusTime, workingLocation,
fromGmail).
"""
from __future__ import annotations

import base64
import html as _html
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

try:
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover - 3.9+ has it
    ZoneInfo = None                     # type: ignore

KEY_ENV = "GOOGLE_CALENDAR_API_KEY"
API = "https://www.googleapis.com/calendar/v3/calendars/{}/events"

# The export URL a config entry keeps as the calendar's identity. `private-<hex>`
# is a calendar's SECRET address, which the API cannot read with a key: that
# calendar is not public, and only its owner's OAuth could open it.
ICAL_RX = re.compile(
    r"^(?:https?|webcal)://calendar\.google\.com/calendar/ical/([^/?#]+)/(public|private-[^/]+)/basic\.ics",
    re.I)

# A calendar embedded in a page. Google Sites' own Calendar block renders on
# www.google.com (`<iframe aria-label="Calendar, <name>" src="https://www.
# google.com/calendar/embed?...&src=<id>">`); the embed code Google Calendar
# hands out is on calendar.google.com. One block can carry several `src=`.
EMBED_RX = re.compile(
    r"(?:www\.google\.com|calendar\.google\.com)/calendar/(?:u/\d+/)?embed\?([^\"'<>\s]+)", re.I)


class GcalError(Exception):
    """A calendar the API would not give us. The message never holds the key."""


def api_key() -> Optional[str]:
    return (os.environ.get(KEY_ENV) or "").strip() or None


def calendar_id(url: str) -> Optional[str]:
    m = ICAL_RX.match((url or "").strip())
    return unquote(m.group(1)) if m else None


def is_secret_address(url: str) -> bool:
    m = ICAL_RX.match((url or "").strip())
    return bool(m) and m.group(2).lower().startswith("private-")


def ical_url(cid: str) -> str:
    """The canonical export URL for a calendar id, spelled the way the 30
    configured ones are (`@` as %40, `#` as %23), so config dedupe sees it."""
    return f"https://calendar.google.com/calendar/ical/{quote(cid, safe='')}/public/basic.ics"


def api_url(cid: str) -> str:
    return API.format(quote(cid, safe=""))


# ---- reading ----------------------------------------------------------------
def _explain(resp) -> str:
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:                                             # noqa: BLE001
        err = {}
    reason = ""
    if isinstance(err, dict):
        errs = err.get("errors") or [{}]
        reason = (errs[0] or {}).get("reason") or err.get("status") or ""
        msg = err.get("message") or ""
    else:
        msg = str(err)
    out = f"calendar api {resp.status_code} {reason}".strip()
    if msg:
        out += f": {msg[:120]}"
    return out


def fetch_events(session, cid: str, key: str, *, days: int = 400, max_items: int = 2500,
                 timeout: float = 25, now: Optional[datetime] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(calendar meta, event items) from today to `days` ahead, series expanded."""
    now = now or datetime.now(timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    params: Dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(2500, max_items)),
        "timeMin": day0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeMax": (day0 + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    headers = {"X-Goog-Api-Key": key}
    meta: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    for _page in range(20):                           # 20 x 2,500 is more than any calendar we keep
        resp = session.get(api_url(cid), params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            raise GcalError(_explain(resp))
        data = resp.json() or {}
        if not meta:
            meta = {"summary": data.get("summary"), "timeZone": data.get("timeZone")}
        items.extend(data.get("items") or [])
        token = data.get("nextPageToken")
        if not token or len(items) >= max_items:
            break
        params["pageToken"] = token
    return meta, items[:max_items]


# ---- writing the VCALENDAR the ics adapter reads ----------------------------
def _esc(s: str) -> str:
    """RFC 5545 TEXT escaping, which the ics parser's _unescape undoes."""
    s = (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def _parse_rfc3339(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else None


def _zone(name: Optional[str]):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:                                             # noqa: BLE001
        return None


def _when(prop: str, t: Dict[str, Any], local_zone) -> Optional[str]:
    """One DTSTART/DTEND line. A date is a DATE; a time is UTC unless the
    event belongs to a series, which the export writes in its own zone."""
    if not isinstance(t, dict):
        return None
    if t.get("date"):
        d = str(t["date"]).replace("-", "")
        return f"{prop};VALUE=DATE:{d}" if re.fullmatch(r"\d{8}", d) else None
    dt = _parse_rfc3339(str(t.get("dateTime") or ""))
    if dt is None:
        return None
    if local_zone is not None:
        tz = _zone(t.get("timeZone")) or local_zone
        name = getattr(tz, "key", None)             # a bare UTC fallback has no IANA name
        if name:
            return f"{prop};TZID={name}:{dt.astimezone(tz).strftime('%Y%m%dT%H%M%S')}"
    return f"{prop}:{dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _instance_uid(item: Dict[str, Any]) -> str:
    base = item.get("iCalUID") or item.get("id") or ""
    if not item.get("recurringEventId"):
        return base
    o = item.get("originalStartTime") or item.get("start") or {}
    stamp = o.get("dateTime") or o.get("date") or item.get("id") or ""
    dt = _parse_rfc3339(str(stamp))
    stamp = dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if dt else str(stamp).replace("-", "")
    return f"{base}/{stamp}"


def keep(item: Dict[str, Any]) -> bool:
    if (item.get("status") or "").lower() == "cancelled":
        return False
    if (item.get("visibility") or "").lower() == "private":
        return False
    if (item.get("eventType") or "default") != "default":
        return False
    return bool((item.get("summary") or "").strip())


def to_ics(meta: Dict[str, Any], items: List[Dict[str, Any]]) -> str:
    cal_zone = _zone(meta.get("timeZone")) or timezone.utc
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//mapsee-aggregator//calendar api//EN"]
    if meta.get("summary"):
        lines.append("X-WR-CALNAME:" + _esc(str(meta["summary"])))
    if meta.get("timeZone"):
        lines.append("X-WR-TIMEZONE:" + str(meta["timeZone"]))
    for item in items:
        if not keep(item):
            continue
        in_series = bool(item.get("recurringEventId"))
        zone = (_zone((item.get("start") or {}).get("timeZone")) or cal_zone) if in_series else None
        start = _when("DTSTART", item.get("start") or {}, zone)
        if not start:
            continue
        lines.append("BEGIN:VEVENT")
        lines.append(start)
        if not item.get("endTimeUnspecified"):
            end = _when("DTEND", item.get("end") or {}, zone)
            if end:
                lines.append(end)
        lines.append("UID:" + _instance_uid(item))
        lines.append("SUMMARY:" + _esc(item["summary"].strip()))
        if (item.get("location") or "").strip():
            lines.append("LOCATION:" + _esc(item["location"].strip()))
        if (item.get("description") or "").strip():
            lines.append("DESCRIPTION:" + _esc(item["description"].strip()))
        if item.get("status"):
            lines.append("STATUS:" + str(item["status"]).upper())
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def fetch_as_ics(session, url: str, key: str, **kw) -> str:
    """The calendar behind an export URL, as VCALENDAR text, through the API."""
    cid = calendar_id(url)
    if not cid:
        raise GcalError("not a Google Calendar export URL")
    if is_secret_address(url):
        # A secret address is a private calendar's; the API will not open it
        # with a key. Said here so the log line names the reason.
        raise GcalError("a secret-address calendar (private-...), which the API cannot read with a key")
    meta, items = fetch_events(session, cid, key, **kw)
    return to_ics(meta, items)


# ---- finding calendars in pages ---------------------------------------------
_B64_RX = re.compile(r"[A-Za-z0-9+/_-]{8,}={0,2}")


def _maybe_b64(s: str) -> str:
    """Newer embed codes carry the id base64-encoded: `src=c3NiY0Bzd29yZHNzYWlsaW5nLmll`
    is ssbc@swordssailing.ie (measured 2026-09-30 in 2 of 14 embeds). An id in
    the clear has an `@` or a dot; a base64 one has neither."""
    if "@" in s or "." in s or not _B64_RX.fullmatch(s):
        return s
    try:
        raw = base64.urlsafe_b64decode(s.replace("+", "-").replace("/", "_") + "=" * (-len(s) % 4))
        text = raw.decode("ascii")
    except Exception:                                             # noqa: BLE001
        return s
    return text if "@" in text and text.isprintable() else s


def embed_ids(html: str) -> List[str]:
    """Calendar ids embedded in a page, in page order, without Google's own
    calendars (holidays, contacts, phases of the moon all live on
    `group.v.calendar.google.com`), which are nobody's local programme."""
    out: List[str] = []
    for query in EMBED_RX.findall(html or ""):
        query = _html.unescape(query)
        for m in re.finditer(r"(?:^|&)src=([^&#]+)", query):
            cid = _maybe_b64(unquote(m.group(1)).strip())
            if not cid or cid.lower().endswith("group.v.calendar.google.com"):
                continue
            if cid not in out:
                out.append(cid)
    return out


# ---- the smoke test ----------------------------------------------------------
def smoke(config_path: str = "ics_sources.json") -> int:
    """Read every configured Google calendar through the API, as the ics
    adapter would, and print what came back. Writes nothing.

    Per calendar: VEVENTs, rows the adapter keeps, how many are instances of a
    series (the export could only ever give the first of those), and a
    10-character prefix of each fingerprint, so a run can be compared against
    the rows the export route already made. It checks identity, not placement,
    so nothing is geocoded: every LOCATION is taken as placeable.
    """
    import json
    import requests
    import mapsee_ingest_ics as ICS

    key = api_key()
    if not key:
        print(f"::error::{KEY_ENV} is not set. Add it as a repository secret.")
        return 1
    sources = [s for s in json.load(open(config_path, encoding="utf-8"))
               if isinstance(s, dict) and calendar_id(s.get("url", ""))]
    session = requests.Session()
    session.headers["User-Agent"] = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

    class _Collect:
        def __init__(self):
            self.events = []

        def upsert(self, ev):
            self.events.append(ev)
            return "new"

    real_geocoder = ICS.make_location_geocoder
    ICS.make_location_geocoder = lambda _session, _suffix: (lambda _loc: (0.0, 0.0001))
    read = refused = 0
    try:
        for src in sources:
            store = _Collect()
            try:
                ICS.ingest_ics(store, session, src)
            except Exception as exc:                              # noqa: BLE001
                refused += 1
                print(f"[gcal] {src.get('name', '?')}: NOT READ - {exc}")
                continue
            read += 1
            series = sum(1 for e in store.events if "/" in (e.source_id or ""))
            fps = ",".join(sorted(e.fingerprint[:10] for e in store.events))
            print(f"[gcal] {src.get('name', '?')}: kept {len(store.events)} ({series} from a series) fp={fps}")
    finally:
        ICS.make_location_geocoder = real_geocoder
    print(f"[gcal] {read} of {len(sources)} Google calendars read through the Calendar API; {refused} not.")
    return 0 if read else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--smoke":
        sys.exit(smoke(sys.argv[2] if len(sys.argv) > 2 else "ics_sources.json"))
    print("usage: python mapsee_gcal.py --smoke [ics_sources.json]")
    sys.exit(2)
