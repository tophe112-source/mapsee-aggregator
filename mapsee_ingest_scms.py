#!/usr/bin/env python3
"""Read Seattle Chamber Music Society's event calendar into the Mapsee event store.

seattlechambermusic.org publishes its season as one paged WordPress archive
(/events/, /events/page/2/, ...). Each card carries the post id, the month and
day, a clock, a venue name and the publisher's own category and tags. No card
prints a year: an embedded calendar block (<div class='events_info_s'>) gives
every upcoming post id its full MM/DD/YYYY date, and the two are joined by post
id, so no card is ever handed a year by guessing. A card whose month and day
disagree with its calendar entry is reported and skipped.

SCMS plays in Seattle, Bellevue, Vashon and Bainbridge Island. Every venue is
pinned from scms_sources.json, where each point is a checked OpenStreetMap
feature. A card at a venue that is not configured is reported and skipped, and
the two "venues" nobody can travel to (a private home, the online Virtual
Concert Hall) are skipped by name.

A row's identity is the publisher's post id, so a performance moved to another
day or retitled is the same row, not a second one beside a stale first.

Admission: the publisher tags its free events `free` (lectures, masterclasses,
Azure concerts). That per-event tag is read the way Localist's `free` flag is;
a card without it makes no admission claim and clears an earlier one.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from mapsee_admission import admission_description, normalize_admission_facts
from mapsee_ingest import EventStore, NormalizedEvent, notice_reason, strip_notice
from mapsee_ingest_festivals import Fetcher, instant
# Imported, not copied: the same stdlib tree and the same publisher crawl-delay
# wait as the Epiphany reader, so test_ingest_recitals.py guards them too.
from mapsee_ingest_recitals import _Tree, _classes, _descendants, _text, fetch_page

SOURCE = "venue:seattlechambermusic.org"
HOST = "www.seattlechambermusic.org"
DEFAULT_URL = "https://www.seattlechambermusic.org/events/"
PUBLISHER = "Seattle Chamber Music Society"
MONTHS = {name.casefold(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), start=1)}
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
MONTHS["sept"] = 9
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AaPp])\.?\s*[Mm]\b\.?")
CALENDAR_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
MAX_PAGES = 10


def _norm(value):
    """Venue labels compare without case, spacing or dash style."""
    value = (value or "").replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _first(node, tag, cls):
    for child in _descendants(node, tag):
        if cls in _classes(child):
            return child
    return None


def _publisher_url(value, base):
    """An absolute http(s) URL on the publisher's own host, or None."""
    absolute = urljoin(base, (value or "").strip())
    parts = urlsplit(absolute)
    if parts.scheme in ("http", "https") and parts.hostname == HOST:
        return absolute
    return None


def parse_calendar(root):
    """{post id: date} from every embedded calendar block on the page."""
    blocks = [n for n in _descendants(root, "div") if "events_info_s" in _classes(n)]
    if not blocks:
        raise ValueError("SCMS calendar block (events_info_s) missing")
    dates = {}
    for block in blocks:
        try:
            data = json.loads(_text(block))
        except ValueError:
            raise ValueError("SCMS calendar block is not JSON") from None
        calendars = data.get("calendars") if isinstance(data, dict) else None
        if not isinstance(calendars, list):
            raise ValueError("SCMS calendar block has no calendars list")
        for calendar in calendars:
            for entry in (calendar or {}).get("entries") or []:
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue                      # the list ends with an empty {}
                post_id = str(entry["id"]).strip()
                match = CALENDAR_DATE.fullmatch(str(entry.get("date") or "").strip())
                if not post_id.isdecimal() or not match:
                    raise ValueError(f"SCMS calendar entry is malformed: {entry!r:.120}")
                month, day, year = (int(g) for g in match.groups())
                when = date(year, month, day)     # rejects an impossible date
                if dates.get(post_id, when) != when:
                    raise ValueError(f"SCMS calendar gives post {post_id} two dates")
                dates[post_id] = when
    return dates


def parse_cards(root, page_url):
    """One dict per event card in the listing grid, in page order."""
    cards = []
    for item in _descendants(root, "div"):
        if item.attrs.get("data-elementor-type") != "loop-item":
            continue
        classes = _classes(item)
        if "type-events" not in classes:
            continue
        post_ids = [c[5:] for c in classes if c.startswith("post-") and c[5:].isdecimal()]
        body = _first(item, "div", "slider_event_item")
        if len(post_ids) != 1 or body is None:
            raise ValueError("SCMS event card has no post id or no event body")
        title_node = next(_descendants(body, "h4"), None)
        month = _first(body, "span", "event_item_info_date_month")
        day = _first(body, "span", "event_item_info_date_day")
        if title_node is None or month is None or day is None:
            raise ValueError(f"SCMS event card {post_ids[0]} is missing its title or date")
        link = _first(body, "a", "event_item_link")
        label = _first(body, "span", "event_item_cat_name")
        clock = _first(body, "span", "event_item_info_date_time")
        place = _first(body, "span", "event_item_info_date_location")
        image = next(_descendants(body, "img"), None)
        cards.append({
            "post_id": post_ids[0],
            "categories": [c[len("events-categories-"):] for c in sorted(classes)
                           if c.startswith("events-categories-")],
            "tags": {c[len("events-tags-"):] for c in classes if c.startswith("events-tags-")},
            "month": _text(month),
            "day": _text(day),
            "title": _text(title_node),
            "time": _text(clock) if clock is not None else "",
            "venue": _text(place) if place is not None else "",
            "label": _text(label) if label is not None else "",
            "url": _publisher_url(link.attrs.get("href") if link is not None else "", page_url),
            "link_text": _text(link) if link is not None else "",
            "image": _publisher_url(image.attrs.get("src") if image is not None else "", page_url),
        })
    return cards


def next_page(root, page_url, listing_url):
    """The pager's "next" link when it stays inside the listing, else None."""
    listing = urlsplit(listing_url)
    for anchor in _descendants(root, "a"):
        if not {"page-numbers", "next"}.issubset(_classes(anchor)):
            continue
        target = _publisher_url(anchor.attrs.get("href"), page_url)
        if target and urlsplit(target).path.startswith(listing.path):
            return target
    return None


def _venue_table(source):
    table = {}
    for venue in source.get("venues") or []:
        try:
            lat, lon = float(venue["lat"]), float(venue["lon"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"configured SCMS venue lacks coordinates: {venue.get('name')!r}") from None
        if not venue.get("name") or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"configured SCMS venue is invalid: {venue.get('name')!r}")
        for label in [venue["name"], *(venue.get("aliases") or [])]:
            table[_norm(label)] = venue
    if not table:
        raise ValueError("no SCMS venues are configured")
    return table


def _clock(text):
    """(hour, minute) of the card's first clock, or None for a day with no time."""
    match = CLOCK.search(text or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 1 <= hour <= 12 or minute > 59:
        raise ValueError(f"invalid clock {match.group(0)!r}")
    return (hour % 12) + (12 if match.group(3).lower() == "p" else 0), minute


def fingerprint(post_id):
    """The publisher's post id, not title|date|venue: an edit keeps the row."""
    return hashlib.sha1(f"{HOST}\n{post_id}".encode("utf-8")).hexdigest()


def build_events(cards, calendar, source, today=None):
    """(events, cancellations, problems) for the cards of one complete read."""
    today = today or date.today()
    zone_name = source.get("timezone") or "America/Los_Angeles"
    ZoneInfo(zone_name)                           # reject an unusable configured zone
    venues = _venue_table(source)
    skipped = {_norm(name) for name in source.get("skip_venues") or {}}
    rules = source.get("categories") or {}
    currency = source.get("currency") or "USD"
    events, cancellations, problems = [], [], []
    seen = set()
    for card in cards:
        post_id, title = card["post_id"], card["title"]
        if post_id in seen:
            continue                              # a card repeated across pages
        seen.add(post_id)
        when = calendar.get(post_id)
        if when is None:
            problems.append(f"post {post_id} ({title!r}) has no dated calendar entry")
            continue
        month = MONTHS.get(card["month"].strip(" .").casefold())
        day = int(card["day"]) if card["day"].isdecimal() else None
        if (month, day) != (when.month, when.day):
            problems.append(f"post {post_id} ({title!r}): card reads {card['month']} {card['day']}, "
                            f"calendar reads {when.isoformat()}")
            continue
        if when < today:
            continue
        if _norm(card["venue"]) in skipped:
            continue
        venue = venues.get(_norm(card["venue"]))
        if venue is None:
            problems.append(f"post {post_id} ({title!r}): venue {card['venue']!r} is not configured")
            continue
        if not card["url"]:
            problems.append(f"post {post_id} ({title!r}) links to no page on {HOST}")
            continue
        reason = notice_reason(title)
        cancelled = False
        if reason:
            own = strip_notice(title)
            if reason == "closure notice" or not own:
                problems.append(f"post {post_id} ({title!r}) is a notice, not an event")
                continue
            title, cancelled = own, True
        try:
            clock = _clock(card["time"])
            if clock:
                local = datetime(when.year, when.month, when.day, *clock).isoformat(timespec="minutes")
                start_utc = instant(local, zone_name)
            else:
                local, start_utc = when.isoformat(), None   # the card claims a day, not a minute
        except ValueError as exc:
            problems.append(f"post {post_id} ({title!r}): {exc}")
            continue

        category, extras = None, []
        for program in card["categories"]:
            rule = rules.get(program) or {}
            category = category or rule.get("category")
            extras += [c for c in rule.get("categories") or [] if c not in extras]
        category = category or source.get("category") or "music"
        extras = [c for c in extras if c != category]

        if "free" in card["tags"]:
            facts = normalize_admission_facts("FREE", url=card["url"], currency_hint=currency,
                                              context=title)
        else:
            facts = {}
        # The sync appends the address ("📍") and the page ("Tickets / info"),
        # so the description says only what the card adds to those.
        lines = [f"{PUBLISHER}" + (f" · {card['label']}" if card["label"] else "")]
        if "register" in card["link_text"].casefold():
            lines.append("Register on the event page.")
        event = NormalizedEvent(
            source=SOURCE,
            source_id=post_id,
            fingerprint=fingerprint(post_id),
            name=title,
            description=admission_description("\n".join(lines), facts),
            start_local=local,
            start_utc=start_utc,
            timezone=zone_name,
            venue_name=card["venue"],
            latitude=float(venue["lat"]),
            longitude=float(venue["lon"]),
            coords_exact=True,
            address=venue.get("address"),
            city=venue.get("city"),
            region=venue.get("region"),
            country=venue.get("country"),
            postal_code=venue.get("postal_code"),
            category=category,
            categories=extras,
            promoter=PUBLISHER,
            poster_image_url=card["image"],
            ticket_url=card["url"],
            source_details=facts,
            admission_checked=True,
        )
        (cancellations if cancelled else events).append(event)
    return events, cancellations, problems


def read_listing(fetcher, source, max_pages=MAX_PAGES):
    """(cards, calendar) over every page of the listing, following its pager."""
    listing_url = source.get("url") or DEFAULT_URL
    if urlsplit(listing_url).hostname != HOST:
        raise ValueError("SCMS reader requires the publisher's own listing URL")
    delay = int(source.get("crawl_delay", 10))
    cards, calendar, visited = [], {}, set()
    url = listing_url
    while url:
        if url in visited:
            break
        if len(visited) >= max_pages:
            raise ValueError(f"SCMS listing has more than {max_pages} pages")
        visited.add(url)
        root = _Tree(fetch_page(fetcher, url, delay)).root
        page_cards = parse_cards(root, url)
        if not page_cards:
            raise ValueError(f"SCMS listing page has no event cards: {url}")
        cards += page_cards
        for post_id, when in parse_calendar(root).items():
            if calendar.get(post_id, when) != when:
                raise ValueError(f"SCMS pages disagree about post {post_id}'s date")
            calendar[post_id] = when
        url = next_page(root, url, listing_url)
    return cards, calendar


def _sources(config):
    data = json.loads(Path(config).read_text(encoding="utf-8"))
    sources = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(sources, list):
        raise ValueError("SCMS config must be a source list or contain a sources list")
    return sources


def run(config, store_path, max_seconds=120, today=None, fetcher_factory=Fetcher):
    """Fetch each configured listing, save what parsed, and return the failure count."""
    if not 1 <= int(max_seconds) <= 300:
        raise ValueError("fetch time budget must be from 1 to 300 seconds")
    warning = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    fetcher = fetcher_factory(max_seconds=int(max_seconds))
    store = EventStore(store_path)
    failures = 0
    for source in _sources(config):
        name = source.get("name") or source.get("id") or PUBLISHER
        try:
            cards, calendar = read_listing(fetcher, source)
            events, cancellations, problems = build_events(cards, calendar, source, today=today)
            if problems and not events and not cancellations:
                # Every upcoming card refused is a layout change, not a quiet
                # season: say so as a failure rather than as warnings alone.
                raise ValueError(f"no card could be read ({len(problems)} problems): {problems[0]}")
            for event in events:
                store.upsert(event)
            for event in cancellations:
                store.cancel(event, "cancelled in the title")
            carded = {card["post_id"] for card in cards}
            uncarded = sorted(set(calendar) - carded)
            if uncarded:
                problems.append(f"{len(uncarded)} calendar entr(y/ies) with no card: {', '.join(uncarded[:8])}")
            for problem in problems:
                print(f"{warning}[scms] {name}: {problem}", flush=True)
            print(f"[scms] {name}: {len(cards)} card(s), {len(events)} upcoming event(s), "
                  f"{len(cancellations)} cancelled, {len(problems)} problem(s)", flush=True)
        except Exception as exc:
            failures += 1
            print(f"{warning}[scms] {name} FAILED: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
    store.save()
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default="scms_sources.json")
    parser.add_argument("--store", default="scms_events.json")
    parser.add_argument("--max-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if not 1 <= args.max_seconds <= 300:
        parser.error("--max-seconds must be between 1 and 300")
    return run(args.config, args.store, args.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
