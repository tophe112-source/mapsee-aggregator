#!/usr/bin/env python3
"""Import dated event cards from Friends of Waterfront Park's public calendar.

The publisher's `/events/` page is server-rendered HTML. It has no Event
JSON-LD, its public WordPress post API omits occurrence dates, and neither the
Tribe REST route nor `?ical=1` is a feed. One bounded listing read carries the
complete upcoming cards, including a year-bearing local datetime and a link
whose `date` query names that exact occurrence.

    python mapsee_ingest_waterfront.py --config waterfront_sources.json \
        --store feeds_events.json --max-seconds 60

Config: `{ "sites": [{ "name": "Friends of Waterfront Park",
  "url": "https://waterfrontparkseattle.org/events/",
  "timezone": "America/Los_Angeles",
  "venues": { "Pier 62": {"lat": ..., "lon": ..., "address": ...} } }] }`.
Only exact publisher venue labels in `venues` are placed; an unknown venue is
skipped so a map pin is never guessed. No cancellation is inferred from a card
disappearing from this rolling calendar.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import robots_txt

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with: pip install requests")

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
from mapsee_admission import normalize_admission_facts

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
HOST = "waterfrontparkseattle.org"
SOURCE = "waterfront"
MIN_INTERVAL_S = 1.0
MAX_RUN_SECONDS = 300
MAX_PAGE_BYTES = 2 * 1024 * 1024
_DATETIME = re.compile(r"^\d{12}$")
_RANGE = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\s*[-\u2010-\u2015]\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\s*$", re.I)
_FREE = re.compile(r"\|\s*free\b", re.I)
_SPACE = re.compile(r"\s+")


@dataclass
class Card:
    datetime: str = ""
    href: str = ""
    filters: str = ""
    poster_image_url: str = ""
    headings: List[Tuple[str, str]] = field(default_factory=list)
    title: str = ""
    description: str = ""

    @property
    def time_text(self) -> str:
        for kind, text in self.headings:
            if kind == "time":
                return text
        return ""

    @property
    def venue_line(self) -> str:
        places = [text for kind, text in self.headings if kind == "place"]
        return places[-1] if places else ""


class _CalendarParser(HTMLParser):
    """Read every publisher `div.grid-item`, including cards before page 1."""

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: List[Card] = []
        self.card: Optional[Card] = None
        self.div_depth = 0
        self.capture: Optional[Tuple[str, List[str]]] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): v for k, v in attrs if k and v is not None}
        classes = set((a.get("class") or "").split())
        if tag == "div":
            if self.card is None and "grid-item" in classes:
                self.card = Card(datetime=a.get("data-datetime", ""),
                                 href="", filters=a.get("data-filters", ""))
                self.div_depth = 1
            elif self.card is not None:
                self.div_depth += 1
        if self.card is None:
            return
        if tag == "a" and not self.card.href and a.get("href"):
            self.card.href = a["href"]
        if tag == "img" and not self.card.poster_image_url:
            self.card.poster_image_url = a.get("data-src") or a.get("src") or ""
        if tag in ("h3", "h4", "p"):
            if tag == "h4":
                kind = "title"
            elif tag == "p" and "brief" in classes:
                kind = "description"
            elif tag == "h3" and "date-time" in classes:
                kind = "time"
            elif tag == "h3":
                kind = "place"
            else:
                kind = "other"
            self.capture = (kind, [])
        if tag in self._VOID:
            return

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.card is not None and self.capture is not None:
            self.capture[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.card is None:
            return
        if tag in ("h3", "h4", "p") and self.capture is not None:
            kind, parts = self.capture
            value = _SPACE.sub(" ", " ".join(parts)).strip()
            if kind == "title" and value and not self.card.title:
                self.card.title = value
            elif kind == "description" and value and not self.card.description:
                self.card.description = value
            elif kind in ("time", "place") and value:
                self.card.headings.append((kind, value))
            self.capture = None
        if tag == "div":
            self.div_depth -= 1
            if self.div_depth <= 0:
                self.cards.append(self.card)
                self.card = None
                self.div_depth = 0
                self.capture = None


def parse_cards(page: str) -> List[Card]:
    parser = _CalendarParser()
    parser.feed(page or "")
    parser.close()
    return parser.cards


def _as_datetime(value: str, zone: ZoneInfo) -> Optional[datetime]:
    if not _DATETIME.fullmatch(value or ""):
        return None
    try:
        wall = datetime.strptime(value, "%Y%m%d%H%M")
    except ValueError:
        return None
    candidates = [wall.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [candidate for candidate in candidates
             if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall]
    if not valid:
        # A nonexistent wall time in the spring-forward gap.
        return None
    if len({candidate.utcoffset() for candidate in valid}) > 1:
        # data-date is a calendar-day sort key, not an occurrence instant.
        # Neither it nor a wall clock distinguishes the fall-back hour.
        return None
    return valid[0]


def _clock(hour: str, minute: Optional[str], meridiem: str) -> Optional[Tuple[int, int]]:
    h, m = int(hour), int(minute or "0")
    ap = (meridiem or "").lower()
    if h < 1 or h > 12 or m > 59:
        return None
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    return h, m


def _explicit_end(card: Card, start: datetime) -> Optional[datetime]:
    raw = card.time_text.replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    m = _RANGE.fullmatch(raw)
    if not m:
        return None
    end_ap = m.group(6).lower()
    start_clock = _clock(m.group(1), m.group(2), (m.group(3) or end_ap).lower())
    end_clock = _clock(m.group(4), m.group(5), end_ap)
    if start_clock != (start.hour, start.minute) or end_clock is None:
        return None
    end = start.replace(hour=end_clock[0], minute=end_clock[1], second=0, microsecond=0)
    return end if end > start else None


def _url_matches(href: str, stamp: str) -> bool:
    try:
        u = urlparse(href)
        return (u.scheme == "https" and u.netloc.lower() == HOST
                and u.path.startswith("/blog/event/")
                and parse_qs(u.query).get("date") == [stamp])
    except (TypeError, ValueError):
        return False


def _poster_url(value: str) -> Optional[str]:
    try:
        u = urlparse(value)
        if (u.scheme == "https" and u.netloc.lower() == HOST
                and u.path.startswith("/wp-content/uploads/")
                and u.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
            return value
    except (TypeError, ValueError):
        pass
    return None


def _location(card: Card) -> Tuple[str, str, bool]:
    line = _SPACE.sub(" ", card.venue_line).strip()
    parts = line.split("|", 1)
    label = parts[0].strip()
    admission = parts[1].strip() if len(parts) == 2 else ""
    return label, admission, bool(_FREE.search(line))


def _admission(admission: str, is_free: bool) -> Optional[Dict[str, Any]]:
    if not is_free:
        return None
    return normalize_admission_facts("Free", context=admission)


def to_event(card: Card, site: Dict[str, Any], today: Optional[datetime] = None) -> Tuple[Optional[NormalizedEvent], str]:
    """Return (event, reason). Unknown locations and malformed cards are refused."""
    zone_name = site.get("timezone", "America/Los_Angeles")
    try:
        zone = ZoneInfo(zone_name)
    except (TypeError, ZoneInfoNotFoundError):
        return None, "invalid timezone"
    start = _as_datetime(card.datetime, zone)
    if start is None:
        return None, "invalid or missing publisher datetime"
    if not _url_matches(card.href, card.datetime):
        return None, "missing, mismatched, or off-host occurrence link"
    if not card.title.strip():
        return None, "missing title"
    now = today or datetime.now(zone)
    if start < now:
        return None, "past event"
    label, admission, is_free = _location(card)
    venue = (site.get("venues") or {}).get(label)
    if not isinstance(venue, dict):
        return None, f"unknown venue: {label or '(blank)'}"
    lat = venue.get("lat", venue.get("latitude"))
    lon = venue.get("lon", venue.get("longitude"))
    if venue.get("address") is None and (lat is None or lon is None):
        return None, f"venue has no place data: {label}"
    if lat is not None or lon is not None:
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None, f"venue has invalid coordinates: {label}"
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None, f"venue has invalid coordinates: {label}"
    end = _explicit_end(card, start)
    description_parts = [card.description.strip()] if card.description.strip() else []
    # Preserve precisely what the publisher displayed, even when it is not a
    # safe interval for a structured end time or cost field.
    if card.time_text:
        description_parts.append(f"Time as listed: {card.time_text.strip()}.")
    if admission:
        description_parts.append(f"Admission: {admission}.")
    description = " ".join(description_parts) or None
    venue_name = venue.get("name", label)
    fingerprint = make_fingerprint(card.title.strip(), start.isoformat(), venue_name)
    # The dated URL's HHmm changes when the publisher corrects a clock.
    # Keep the occurrence's day in its identity and its current URL as the link.
    source_id = card.href.split("?", 1)[0] + "#" + start.date().isoformat()
    return NormalizedEvent(
        source=SOURCE, source_id=source_id, ticket_url=card.href, name=card.title.strip(),
        fingerprint=fingerprint, description=description,
        start_local=start.isoformat(),
        start_utc=start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_local=end.isoformat() if end else None,
        end_utc=end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if end else None,
        timezone=zone_name, venue_name=venue_name,
        latitude=lat,
        longitude=lon,
        address=venue.get("address"), city=venue.get("city", site.get("city", "Seattle")),
        region=venue.get("region", site.get("region", "WA")),
        country=venue.get("country", site.get("country", "US")),
        postal_code=venue.get("postal_code"),
        category=venue.get("category", site.get("category", "community")),
        source_details=_admission(admission, is_free) or {}, admission_checked=True,
        poster_image_url=_poster_url(card.poster_image_url),
        coords_exact=bool(venue.get("coords_exact", lat is not None and lon is not None)),
    ), ""


def store_event(store: EventStore, event: NormalizedEvent) -> str:
    """Refresh this publisher's stored clocks without replacing other feeds' facts."""
    previous = store.records.get(event.fingerprint)
    owned = previous is not None and any(
        ref.get("source") == SOURCE and ref.get("source_id") == event.source_id
        for ref in previous.get("sources", []) if isinstance(ref, dict))
    status = store.upsert(event)
    if owned and status in {"updated", "merged"}:
        record = store.records[event.fingerprint]
        # EventStore fills missing fields across feeds. An owned schedule read
        # also replaces old clocks, including an end withdrawn by the publisher.
        for key in ("start_local", "start_utc", "end_local", "end_utc", "timezone"):
            record[key] = getattr(event, key)
    return status


def _validate_site(site: Dict[str, Any]) -> None:
    if not isinstance(site, dict):
        raise ValueError("site config must be an object")
    url = site.get("url")
    u = urlparse(url) if isinstance(url, str) else None
    if not u or u.scheme != "https" or u.netloc.lower() != HOST or u.path != "/events/":
        raise ValueError(f"{site.get('name', '?')}: url must be the official Waterfront Park /events/ page")
    try:
        ZoneInfo(site.get("timezone", "America/Los_Angeles"))
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"{site.get('name', '?')}: invalid timezone") from exc
    if not isinstance(site.get("venues"), dict):
        raise ValueError(f"{site.get('name', '?')}: venues must map exact publisher labels to place data")


def ingest_site(store: EventStore, session, robots: robots_txt.Robots,
                site: Dict[str, Any], deadline: float,
                today: Optional[datetime] = None) -> Dict[str, int]:
    _validate_site(site)
    name, url = site.get("name", "Waterfront Park"), site["url"]
    counts = {"kept": 0, "past": 0, "unknown_venue": 0, "invalid": 0,
              "duplicates": 0, "notices": 0, "rejected": 0}
    answer = robots.check(url)
    if answer.get("allowed") is not True:
        raise RuntimeError(f"robots.txt ({answer.get('status')}) refuses {url}: {answer.get('rule') or ''}")
    delay = max(MIN_INTERVAL_S, float(answer.get("crawl_delay") or 0),
                float(site.get("crawl_delay", 0) or 0))
    remaining = deadline - time.monotonic()
    if remaining <= 0 or delay >= remaining:
        raise RuntimeError("run deadline leaves no time for the required host delay and listing request")
    time.sleep(delay)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("run deadline reached before listing request")
    timeout = (max(0.1, min(5.0, remaining / 3)), max(0.1, min(15.0, remaining * 2 / 3)))
    response = session.get(url, timeout=timeout, allow_redirects=False, stream=True)
    try:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_PAGE_BYTES:
            raise RuntimeError("listing exceeds the 2 MiB response limit; stopped")
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("run deadline reached while reading the listing")
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                raise RuntimeError("listing exceeds the 2 MiB response limit; stopped")
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        response.close()
    body = payload[:8192].decode("utf-8", "replace")
    if robots_txt.CHALLENGE_RX.search(body) and not re.search(r"(?im)^\s*user-agent\s*:", body):
        raise RuntimeError("publisher returned a challenge page; stopped without retrying")
    if response.status_code == 429:
        raise RuntimeError("publisher returned HTTP 429; stopped without retrying")
    if response.status_code != 200:
        raise RuntimeError(f"listing returned HTTP {response.status_code}; stopped")
    if urlparse(response.url).netloc.lower() != HOST:
        raise RuntimeError("listing redirected off the publisher host; stopped")
    cards = parse_cards(payload.decode("utf-8", "replace"))
    if not any(card.title and card.datetime and card.href for card in cards):
        raise RuntimeError("listing matched no usable dated event cards; markup may have changed")
    today = today or datetime.now(ZoneInfo(site.get("timezone", "America/Los_Angeles")))
    seen = set()
    for card in cards:
        if card.href and card.datetime:
            key = (card.href, card.datetime)
            if key in seen:
                counts["duplicates"] += 1
                continue
            seen.add(key)
        event, reason = to_event(card, site, today)
        if event is None:
            if reason == "past event":
                counts["past"] += 1
            elif reason.startswith("unknown venue") or reason.startswith("venue has no"):
                counts["unknown_venue"] += 1
            else:
                counts["invalid"] += 1
            continue
        status = store_event(store, event)
        if status in {"added", "updated", "merged"}:
            counts["kept"] += 1
        elif status == "notice":
            counts["notices"] += 1
        elif status == "rejected":
            counts["rejected"] += 1
    print(f"[waterfront] {name}: parsed {len(cards)} cards; kept {counts['kept']}; "
          f"past {counts['past']}; unknown venue {counts['unknown_venue']}; "
          f"invalid {counts['invalid']}; duplicate {counts['duplicates']}; "
          f"notices {counts['notices']}; rejected {counts['rejected']}")
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Import Friends of Waterfront Park's upcoming event cards.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--max-seconds", type=int, default=60)
    args = ap.parse_args(argv)
    if not 1 <= args.max_seconds <= MAX_RUN_SECONDS:
        ap.error(f"--max-seconds must be between 1 and {MAX_RUN_SECONDS}")
    try:
        config = json.loads(open(args.config, encoding="utf-8").read())
        sites = config.get("sites") if isinstance(config, dict) else None
        if not isinstance(sites, list) or not sites:
            raise ValueError("config must contain a non-empty sites list")
        for site in sites:
            _validate_site(site)
    except (OSError, ValueError, TypeError) as exc:
        print(f"[waterfront] configuration failed: {exc}", file=sys.stderr)
        return 2
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                            "Accept-Language": "en-US,en;q=0.8"})
    robots = robots_txt.Robots(session, timeout=min(15.0, float(args.max_seconds)))
    store = EventStore(args.store)
    deadline = time.monotonic() + args.max_seconds
    failed = False
    total = 0
    for site in sites:
        if time.monotonic() >= deadline:
            print("[waterfront] run deadline reached before next site", file=sys.stderr)
            failed = True
            break
        try:
            result = ingest_site(store, session, robots, site, deadline)
            total += result["kept"]
        except Exception as exc:
            print(f"[waterfront] {site.get('name', '?')} FAILED: {exc}", file=sys.stderr)
            failed = True
    if total or store.records:
        store.save()
    print(f"[waterfront] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
