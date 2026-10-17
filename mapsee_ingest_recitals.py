#!/usr/bin/env python3
"""Read Epiphany Seattle's published concert series into the Mapsee event store.

This deliberately supports the concert accordion on one publisher page only.
Dates and admission are read from each concert's own block; page-wide copy is
never used as evidence that a particular concert is free.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from html.parser import HTMLParser
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from mapsee_admission import admission_description, normalize_admission_facts
from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
from mapsee_ingest_festivals import Fetcher, instant

SOURCE = "venue:recital"
DEFAULT_URL = "https://epiphanyseattle.org/music-arts/concerts/"
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
DATE_LINE = re.compile(
    r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*)?"
    r"(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s*(\d{4})\s*\|\s*"
    r"(\d{1,2}):(\d{2})\s*(am|pm)\b", re.I)
DATE_HINT = re.compile(
    r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*)?"
    r"(" + "|".join(MONTHS) + r")\s+\d{1,2}\b", re.I)
PRICE = re.compile(r"\bTickets?\s*:\s*\$\s*([0-9]+(?:\.[0-9]{1,2})?)\b", re.I)
FREE = re.compile(r"\bFREE\b", re.I)
NOT_FREE = re.compile(r"\b(?:not|isn't|is not|never)\s+free\b", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class _Node:
    def __init__(self, tag="", attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or ())
        self.parent = parent
        self.children = []


class _Tree(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag.lower(), attrs, self.stack[-1]))

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _classes(node):
    return set((node.attrs.get("class") or "").split())


def _descendants(node, tag=None):
    for child in node.children:
        if isinstance(child, _Node):
            if tag is None or child.tag == tag:
                yield child
            yield from _descendants(child, tag)


def _text(node, preserve_breaks=False):
    parts = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag == "br" and preserve_breaks:
            parts.append("\n")
        else:
            parts.append(_text(child, preserve_breaks))
    value = "".join(parts).replace("\xa0", " ")
    if preserve_breaks:
        return "\n".join(re.sub(r"\s+", " ", line).strip() for line in value.splitlines())
    return re.sub(r"\s+", " ", value).strip()


def _series_container(root):
    headings = [n for n in _descendants(root, "h2")
                if "concert series" in _text(n).casefold()]
    if len(headings) != 1:
        raise ValueError("concert series heading missing or ambiguous")
    parent = headings[0].parent
    while parent is not None and not (parent.tag == "div" and
                                      {"block", "accordion"}.issubset(_classes(parent))):
        parent = parent.parent
    if parent is None:
        raise ValueError("concert series container missing")
    return parent


def _concert_blocks(container):
    blocks = []
    for node in _descendants(container, "div"):
        if "accordion" not in _classes(node) or "block" in _classes(node):
            continue
        questions = [n for n in _descendants(node, "a") if "question" in _classes(n)]
        answers = [n for n in _descendants(node, "div") if "answer" in _classes(n)]
        if questions and answers:
            blocks.append((answers[0],))
    return [row[0] for row in blocks]


def _date_occurrences(paragraph, timezone_name):
    text = _text(paragraph, preserve_breaks=True)
    matches = list(DATE_LINE.finditer(text))
    for line in text.splitlines():
        if DATE_HINT.search(line) and not DATE_LINE.search(line):
            raise ValueError("concert date/time row is incomplete or missing its year")
    if not matches:
        return []
    zone = ZoneInfo(timezone_name)
    output = []
    for match in matches:
        month, day, year = match.group(1).title(), int(match.group(2)), int(match.group(3))
        hour, minute = int(match.group(4)), int(match.group(5))
        ampm = match.group(6).lower()
        if hour < 1 or hour > 12 or minute > 59:
            raise ValueError("invalid concert time")
        hour = (hour % 12) + (12 if ampm == "pm" else 0)
        local = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").replace(
            hour=hour, minute=minute)
        start_utc = instant(local.isoformat(timespec="minutes"), timezone_name)
        # The source sometimes names a room after a pipe on the same date line.
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        tail = text[match.end():line_end]
        room_match = re.search(r"\|\s*([^|]+?)\s*$", tail)
        room = room_match.group(1).strip() if room_match else None
        output.append((local, start_utc, room))
    return output


def _ticket_url(answer, page_url):
    for anchor in _descendants(answer, "a"):
        label = _text(anchor).casefold()
        href = (anchor.attrs.get("href") or "").strip()
        if "ticket" not in label and "eventbrite.com/e/" not in href.casefold():
            continue
        absolute = urljoin(page_url, href)
        if urlsplit(absolute).scheme in ("http", "https") and urlsplit(absolute).hostname:
            return absolute
    return None


def _admission(paragraph, ticket_url, currency):
    """Only the date/admission paragraph can establish a free or paid offer."""
    evidence = _text(paragraph, preserve_breaks=True)
    match = PRICE.search(evidence)
    if match:
        price = match.group(1)
        facts = normalize_admission_facts(price, url=ticket_url,
                                          currency_hint=currency, context=evidence)
        if facts and facts.get("restricted"):
            # Preserve a public paid price while retaining the normalizer's
            # veto for a conditional perk such as "free for children".
            priced = normalize_admission_facts(price, url=ticket_url,
                                               currency_hint=currency)
            if priced:
                priced["restricted"] = True
                return priced
        return facts
    if NOT_FREE.search(evidence):
        return None
    if FREE.search(evidence):
        return normalize_admission_facts("FREE", url=ticket_url,
                                         currency_hint=currency, context=evidence)
    return None


def parse_concerts(markup, source, today=None):
    """Parse future performance occurrences from the official concert accordion."""
    today = today or date.today()
    page_url = source.get("url") or source.get("official_url") or DEFAULT_URL
    if urlsplit(page_url).hostname != "epiphanyseattle.org":
        raise ValueError("Epiphany concert parser requires the official publisher URL")
    timezone_name = source.get("timezone") or "America/Los_Angeles"
    ZoneInfo(timezone_name)  # reject an unusable configured timezone before parsing
    venue = source.get("venue")
    if not isinstance(venue, dict) or not venue.get("name"):
        raise ValueError("configured Epiphany venue is missing")
    try:
        latitude, longitude = float(venue["lat"]), float(venue["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("configured Epiphany venue coordinates are missing") from None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("configured Epiphany venue coordinates are invalid")

    root = _Tree(markup).root
    container = _series_container(root)
    blocks = _concert_blocks(container)
    if not blocks:
        raise ValueError("concert series contains no concert accordion entries")

    source_id = str(source.get("id") or "epiphany-concerts")
    currency = source.get("currency") or "USD"
    events = []
    for answer in blocks:
        headings = [node for node in _descendants(answer, "h4")]
        if len(headings) != 1:
            raise ValueError("concert entry has a missing or ambiguous h4 title")
        title = _text(headings[0])
        paragraphs = list(_descendants(answer, "p"))
        dated = [(paragraph, _date_occurrences(paragraph, timezone_name))
                 for paragraph in paragraphs]
        dated = [(paragraph, occurrences) for paragraph, occurrences in dated if occurrences]
        if len(dated) != 1:
            raise ValueError(f"concert entry has missing or ambiguous date paragraph: {title}")
        admission_paragraph, occurrences = dated[0]
        ticket = _ticket_url(answer, page_url)
        admission = _admission(admission_paragraph, ticket, currency)
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "concert"
        for local, start_utc, room in occurrences:
            if local.date() < today:
                continue
            venue_name = f"{venue['name']} — {room}" if room else venue["name"]
            source_event_id = f"{source_id}:{slug}:{local.date().isoformat()}"
            fingerprint = make_fingerprint(title, local.date().isoformat(), venue_name,
                                           venue.get("city"))
            base_description = f"Concert at {venue_name}.\nOfficial source: {page_url}"
            description = admission_description(base_description, admission)
            events.append(NormalizedEvent(
                source=SOURCE,
                source_id=source_event_id,
                fingerprint=fingerprint,
                name=title,
                description=description,
                start_local=local.isoformat(timespec="minutes"),
                start_utc=start_utc,
                timezone=timezone_name,
                venue_name=venue_name,
                latitude=latitude,
                longitude=longitude,
                coords_exact=True,
                address=venue.get("address"),
                city=venue.get("city"),
                region=venue.get("region"),
                country=venue.get("country"),
                postal_code=venue.get("postal_code"),
                category="music",
                ticket_url=ticket,
                source_details=admission,
                admission_checked=admission is not None,
            ))
    duplicate_counts = {}
    for event in events:
        key = (event.name, event.start_local[:10], event.venue_name)
        duplicate_counts[key] = duplicate_counts.get(key, 0) + 1
    for event in events:
        key = (event.name, event.start_local[:10], event.venue_name)
        if duplicate_counts[key] > 1:
            # Keep the usual title/date/venue identity for the common case.
            # Only same-title, same-date, same-room performances need the clock
            # in their identity so EventStore and sync retain both rows.
            clock = datetime.fromisoformat(event.start_local).strftime("%H%M")
            event.source_id = f"{event.source_id}:{clock}"
            event.fingerprint = make_fingerprint(
                f"{event.name} {clock}", event.start_local[:10], event.venue_name,
                event.city)
    return events


def fetch_page(fetcher, url, crawl_delay=10):
    """Use Fetcher's robots rules and bounds while honoring the publisher delay."""
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robot = fetcher._robots_for(origin)
    from mapsee_ingest_festivals import UA
    if not robot.can_fetch(UA, url):
        raise ValueError("robots disallows this URL")
    last = fetcher.last_fetch.get(parsed.netloc)
    if last is not None:
        wait = last + crawl_delay - time.monotonic()
        if wait > 0:
            if time.monotonic() + wait >= fetcher.deadline:
                raise TimeoutError("publisher crawl delay exceeds fetch budget")
            time.sleep(wait)
    return fetcher.get_text(url)


def _sources(config):
    data = json.loads(Path(config).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("recitals", "sources"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("recital config must be a source list or contain a recitals list")


def run(config, store_path, max_seconds=60, today=None, fetcher_factory=Fetcher):
    """Fetch configured concert pages, save all successful sources, and count failures."""
    if not 1 <= int(max_seconds) <= 300:
        raise ValueError("fetch time budget must be from 1 to 300 seconds")
    fetcher = fetcher_factory(max_seconds=int(max_seconds))
    store = EventStore(store_path)
    failures = 0
    for source in _sources(config):
        try:
            url = source.get("url") or source.get("official_url") or DEFAULT_URL
            markup = fetch_page(fetcher, url, int(source.get("crawl_delay", 10)))
            events = parse_concerts(markup, source, today=today)
            for event in events:
                store.upsert(event)
            print(f"[recitals] {source.get('name', source.get('id', 'Epiphany'))}: {len(events)} upcoming performances")
        except Exception as exc:
            failures += 1
            print(f"[recitals] {source.get('name', source.get('id', '?'))} FAILED: {type(exc).__name__}: {str(exc)[:240]}")
    store.save()
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="JSON source list or {recitals: [...]} file")
    parser.add_argument("--store", default="mapsee_events.json")
    parser.add_argument("--max-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if not 1 <= args.max_seconds <= 300:
        parser.error("--max-seconds must be between 1 and 300")
    return run(args.config, args.store, args.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
