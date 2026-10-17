#!/usr/bin/env python3
"""Festival parents with internal agendas; bounded official-site verification.

Configured PDF parsers and verified JSON-LD subEvent schedules share the normal
EventStore/sync path. Discovery nominations are NEVER treated as timetables.
An unread or unsupported source is reported, not replaced with an empty agenda.
"""
from __future__ import annotations

import argparse
import codecs
from email.message import Message
import hashlib
import html
import ipaddress
import json
import re
import socket
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from mapsee_ingest import EventStore, NormalizedEvent

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
MAX_BYTES = 8 * 1024 * 1024


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('unsupported public URL')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('non-public destination')
    return url


class Fetcher:
    """Identified, byte/time bounded reads; robots checked across redirects."""
    def __init__(self, max_seconds=240):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = UA
        self.robots = {}
        self.deadline = time.monotonic() + max_seconds
        self.requests = 0
        self.last_fetch = {}
        self.last_encoding = ()

    def _raw(self, url):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('festival fetch budget exhausted')
        public_url(url)
        host = urlsplit(url).netloc
        delay = self.last_fetch.get(host, 0) + 1.1 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_fetch[host] = time.monotonic()
        self.requests += 1
        with self.session.get(url, timeout=(8, 20), stream=True, allow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                return response.status_code, b'', urljoin(url, response.headers.get('Location', ''))
            if response.status_code != 200:
                return response.status_code, b'', None
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES or time.monotonic() >= self.deadline:
                    raise ValueError('response exceeds festival byte/time budget')
            # Keep the server declaration; get_text also sniffs HTML's own
            # ASCII-compatible <meta charset>.  Do not use apparent_encoding
            # here: requests cannot calculate it after a streamed body has
            # already been consumed.
            # Several European festival sites serve ISO-8859-1/Windows-1252;
            # forcing their HTML through UTF-8 drops the whole source.
            content_type = Message()
            content_type['content-type'] = response.headers.get('Content-Type', '')
            declared = content_type.get_content_charset()
            self.last_encoding = (declared,) if declared else ()
            return 200, bytes(data), None

    def _robots_for(self, origin):
        if origin in self.robots:
            return self.robots[origin]
        url = origin + '/robots.txt'
        redirects = 0
        while True:
            code, body, redirect = self._raw(url)
            if redirect:
                if redirects >= 5:                 # RFC 9309 section 2.3.1.2
                    raise ValueError('robots unavailable: too many redirects')
                url = redirect
                redirects += 1
                continue
            if code not in (200, 404):
                raise ValueError(f'robots unavailable: HTTP {code}')
            robot = RobotFileParser()
            robot.parse(body.decode('utf-8', 'replace').splitlines() if code == 200 else [])
            if code == 404:
                robot.allow_all = True
            self.robots[origin] = robot
            return robot

    def get(self, url, check_robots=True):
        self.last_encoding = ()
        redirects = 0
        while True:
            p = urlsplit(url)
            origin = f'{p.scheme}://{p.netloc}'
            if check_robots:
                if not self._robots_for(origin).can_fetch(UA, url):
                    raise ValueError('robots disallows this URL')
            code, body, redirect = self._raw(url)
            if redirect:
                if redirects >= 5:
                    raise ValueError('too many redirects')
                url = redirect
                redirects += 1
                continue
            if code != 200:
                raise ValueError(f'source HTTP {code}')
            return body

    def get_text(self, url, check_robots=True):
        body = self.get(url, check_robots=check_robots)
        head = body[:4096]
        meta = re.search(br'<meta\b[^>]*\bcharset\s*=\s*["\']?\s*([^\s"\'/>;]+)',
                         head, re.I)
        meta_encoding = meta.group(1).decode('ascii', 'ignore') if meta else None
        tried = set()
        for encoding in (*self.last_encoding, meta_encoding, 'utf-8'):
            try:
                canonical = codecs.lookup(encoding).name
            except (LookupError, TypeError):
                continue
            if canonical in tried:
                continue
            tried.add(canonical)
            try:
                return body.decode(canonical)
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError('utf-8', body, 0, min(1, len(body)),
                                 'HTML does not match its declared or detected encoding')


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.scripts, self.links, self.calendars, self.jsonld = [], [], [], []
        self._ld = None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            if attrs.get('src'):
                self.scripts.append(attrs['src'])
            if attrs.get('type', '').lower() == 'application/ld+json':
                self._ld = ''
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'add-to-calendar-button':
            self.calendars.append(attrs)

    def handle_data(self, value):
        if self._ld is not None:
            self._ld += value

    def handle_endtag(self, tag):
        if tag == 'script' and self._ld is not None:
            try:
                self.jsonld.append(json.loads(self._ld))
            except ValueError:
                pass
            self._ld = None


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def instant(value, zone=None):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('schedule needs a full date and time')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        if not zone:
            raise ValueError('schedule timezone is missing')
        z = ZoneInfo(zone)
        # Refuse both ambiguous and nonexistent wall clocks at DST transitions.
        a, b = stamp.replace(tzinfo=z, fold=0), stamp.replace(tzinfo=z, fold=1)
        if a.utcoffset() != b.utcoffset() or a.astimezone(timezone.utc).astimezone(z).replace(tzinfo=None) != stamp:
            raise ValueError('ambiguous or nonexistent local schedule time')
        stamp = a
    return stamp.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def upcoming(start, end, today):
    if datetime.fromisoformat(end.replace('Z', '+00:00')).date() < today:
        return False
    return datetime.fromisoformat(start.replace('Z', '+00:00')).date() <= today + timedelta(days=370)


def identity(source_id, edition):
    return hashlib.sha1(f'festival:{source_id}:{edition}'.encode()).hexdigest()


def jackson_pdf_url(page_body, fetcher, page_url='https://jacksonstreetjazz.org/schedule'):
    """Follow public Homestead page assets, never execute their JavaScript."""
    page = Page(page_body)
    direct = [u for u in page.links if urlsplit(u).path.lower().endswith('.pdf') and 'schedule' in u.lower()]
    if len(set(direct)) == 1:
        return urljoin(page_url, direct[0])
    urls = []
    for asset in page.scripts:
        # Site content explicitly referenced by the page; do not fetch analytics,
        # Google Maps, or third-party APIs/keys embedded in the builder settings.
        if not asset.startswith('https://storage.googleapis.com/wzukusers/'):
            continue
        if len(urls) >= 4:
            raise ValueError('too many Homestead content assets')
        urls.append(asset)
    matches = set()
    for asset in urls:
        body = fetcher.get_text(asset)
        if not body.startswith('PagesStructures['):
            continue
        payload, _ = json.JSONDecoder().raw_decode(body[body.index('=') + 1:].lstrip())
        for obj in walk(payload):
            if obj.get('fileType') == 'pdf' and 'schedule' in str(obj.get('title', '')).lower():
                if obj.get('storageServer') != 6 or not re.fullmatch(r'[0-9]+', str(obj.get('ownerID'))):
                    raise ValueError('unknown PDF storage mapping')
                path = str(obj.get('fileName', ''))
                if '..' in path or not path.lower().endswith('.pdf'):
                    raise ValueError('invalid schedule asset path')
                matches.add(f'https://storage.googleapis.com/wzukusers/user-{obj["ownerID"]}/documents/{quote(path, safe="/")}')
    if len(matches) != 1:
        raise ValueError(f'expected one current schedule PDF, found {len(matches)}')
    return matches.pop()


def jackson_event(source, fetcher, today):
    from festival_pdf import parse_jackson_pdf
    schedule_html = fetcher.get_text(source['schedule_url'])
    pdf_url = jackson_pdf_url(schedule_html, fetcher)
    parsed = parse_jackson_pdf(fetcher.get(pdf_url), timezone=source['timezone'])
    buttons = Page(fetcher.get_text(source['ticket_url'])).calendars
    buttons = [b for b in buttons if 'jackson street jazz' in b.get('name', '').lower()]
    if len(buttons) != 1:
        raise ValueError('ticket page calendar is missing or ambiguous')
    b = buttons[0]
    if b['startdate'] != parsed['date'] or b.get('timezone') != source['timezone']:
        raise ValueError('ticket page and schedule date/timezone disagree')
    start = instant(b['startdate'] + 'T' + b['starttime'], source['timezone'])
    end = instant(b['enddate'] + 'T' + b['endtime'], source['timezone'])
    if not upcoming(start, end, today):
        return None
    venue = source['venue']
    return NormalizedEvent(
        source='venue:festival', source_id=source['id'] + ':' + parsed['date'][:4],
        fingerprint=identity(source['id'], parsed['date'][:4]), name=source['name'],
        description=f"Jazz performances across Seattle's Central District. Wristbands required for admission.\n"
                    f"Official schedule: {source['schedule_url']}\nSchedule PDF: {pdf_url}",
        start_utc=start, end_utc=end, timezone=source['timezone'], category='music',
        venue_name=venue['name'], address=venue['address'], city=venue['city'],
        region=venue['region'], country=venue['country'], latitude=venue['lat'], longitude=venue['lon'],
        coords_exact=True, ticket_url=source['ticket_url'], promoter='Music For A Cause',
        agenda=parsed['agenda'], agenda_tz=source['timezone'])


class BenaroyaSchedule(HTMLParser):
    """Read the official Open House's timed schedule block, not its ticket-sale cards."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.stopped = False
        self.room = None
        self.heading = None
        self.heading_text = []
        self.in_room = False
        self.in_h1 = False
        self.in_p = False
        self.in_strong = False
        self.strong_text = []
        self.p_lines = []
        self.rows = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('id') == 'schedule':
            self.active = True
        if not self.active or self.stopped:
            return
        if tag == 'h1':
            self.in_h1 = True
            self.heading_text = []
        if tag == 'h3' and 'rte-subtitle' in attrs.get('class', '').split():
            self.in_room = True
            self.heading = []
        if tag == 'p':
            self.in_p = True
            self.strong_text = []
            self.p_lines = [[]]
        elif tag == 'br' and self.in_p:
            self.p_lines.append([])
        if tag == 'strong' and self.in_p:
            self.in_strong = True

    def handle_data(self, value):
        if self.in_h1:
            self.heading_text.append(value)
        if self.in_room and self.heading is not None:
            self.heading.append(value)
        if self.in_p and self.p_lines:
            self.p_lines[-1].append(value)
            if self.in_strong:
                self.strong_text.append(value)

    def handle_endtag(self, tag):
        if tag == 'strong':
            self.in_strong = False
        if tag == 'h3' and self.in_room:
            room = ' '.join(''.join(self.heading or []).split())
            if room:
                self.room = room
            self.heading = None
            self.in_room = False
        if tag == 'h1' and self.in_h1:
            heading = ' '.join(''.join(self.heading_text).split()).casefold()
            self.in_h1 = False
            if 'open house ticket sale' in heading or '$19 tickets' in heading:
                self.stopped = True
                self.active = False
        if tag == 'p' and self.in_p:
            label = ' '.join(''.join(self.strong_text).split())
            lines = [' '.join(''.join(line).split()) for line in self.p_lines]
            body = ' — '.join(line for line in lines[1:] if line)
            if any(lines) or label:
                self.rows.append((self.room, label, body))
            self.in_p = False
            self.in_strong = False
            self.strong_text = []
            self.p_lines = []


_BENAROYA_CLOCK = re.compile(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM|NOON)?$', re.I)


def _benaroya_clock(value, inherited=None):
    match = _BENAROYA_CLOCK.fullmatch(value.strip())
    if not match:
        raise ValueError('unsupported Benaroya schedule clock')
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or inherited or '').upper()
    if minute > 59 or hour < 1 or hour > 12 or meridiem not in ('AM', 'PM', 'NOON'):
        raise ValueError('incomplete Benaroya schedule clock')
    if meridiem == 'NOON':
        if hour != 12 or minute:
            raise ValueError('invalid noon clock')
        hour = 12
    elif meridiem == 'AM':
        hour = 0 if hour == 12 else hour
    elif meridiem == 'PM':
        hour = 12 if hour == 12 else hour + 12
    return f'{hour:02d}:{minute:02d}:00'


def _benaroya_ranges(label):
    label = label.strip().rstrip('*').strip()
    label = re.sub(r'^STARTING\s+AT\s+', '', label, flags=re.I)
    if label.upper() == 'ALL DAY':
        return []
    segments = re.split(r'\s*&\s*', label)
    ranges = []
    for segment in segments:
        parts = re.split(r'\s*[–—-]\s*', segment.strip())
        if len(parts) == 1:
            ranges.append((_benaroya_clock(parts[0]), None))
            continue
        if len(parts) != 2:
            raise ValueError('unsupported Benaroya schedule range')
        end_match = _BENAROYA_CLOCK.fullmatch(parts[1].strip())
        if not end_match or not end_match.group(3):
            raise ValueError('schedule range has no explicit end meridiem')
        end = _benaroya_clock(parts[1])
        start = _benaroya_clock(parts[0], end_match.group(3))
        if end <= start:
            raise ValueError('Benaroya schedule range ends before it starts')
        ranges.append((start, end))
    return ranges


def parse_benaroya_open_house(body, source, today):
    """Parse one verified Benaroya Hall Open House HTML snapshot.

    Parent hours come from the official public-hours article, not the earliest
    timed activity (9:00 AM) nor the 4:00 PM headliner. Activities before the
    published 10:00 AM opening stay in the parent's description; child agenda
    entries remain inside the parent window.
    """
    schedule = BenaroyaSchedule(body)
    if not schedule.stopped or not schedule.rows:
        raise ValueError('Benaroya schedule section or sale boundary is missing')
    zone = source.get('timezone')
    event_date = source.get('event_date')
    start_time = source.get('parent_start_time')
    end_time = source.get('parent_end_time')
    if not zone or not event_date or not start_time or not end_time:
        raise ValueError('Benaroya parent date/window/timezone is not configured')
    try:
        day = date.fromisoformat(event_date)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid Benaroya event date') from exc
    start = instant(f'{event_date}T{_benaroya_clock(start_time)}', zone)
    end = instant(f'{event_date}T{_benaroya_clock(end_time)}', zone)
    if end <= start:
        raise ValueError('Benaroya parent window ends before it starts')
    if not upcoming(start, end, today):
        return None

    agenda = []
    for room, label, body_text in schedule.rows:
        if label.strip().upper().rstrip('*').strip() == 'ALL DAY':
            continue
        details = [part for part in body_text.split(' — ') if part]
        if not room or not details:
            raise ValueError('Benaroya schedule item is missing its room or title')
        title = details[0]
        for detail in details[1:]:
            candidate = title + ' — ' + detail
            if len(candidate) > 120:
                break
            title = candidate
        for segment_index, (at_clock, until_clock) in enumerate(_benaroya_ranges(label)):
            at = instant(f'{event_date}T{at_clock}', zone)
            until = instant(f'{event_date}T{until_clock}', zone) if until_clock else None
            # The Hall's official article defines the public Open House window
            # as 10:00 AM–6:30 PM. Keep earlier scheduled activities in the
            # parent description; agenda validation correctly keeps them out
            # of a child timetable whose parent has not opened yet.
            if datetime.fromisoformat(at.replace('Z', '+00:00')) < datetime.fromisoformat(start.replace('Z', '+00:00')):
                continue
            stable = f"{source['id']}|{event_date}|{room}|{details[0]}|{segment_index}"
            agenda.append({'id': hashlib.sha1(stable.encode('utf-8')).hexdigest(),
                           'title': title, 'place': room, 'at': at, 'until': until,
                           'url': source['schedule_url']})
    if not agenda:
        raise ValueError('Benaroya schedule has no timed items')

    venue = source.get('venue')
    if not isinstance(venue, dict):
        raise ValueError('Benaroya venue is missing')
    lat, lon = float(venue['lat']), float(venue['lon'])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        raise ValueError('invalid Benaroya venue coordinates')
    official_url = source['official_url']
    schedule_url = source['schedule_url']
    hours_url = source['hours_url']
    retrieved_at = source['snapshot_retrieved_at']
    try:
        retrieved = datetime.fromisoformat(retrieved_at.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('invalid Benaroya snapshot retrieval time') from exc
    if retrieved.tzinfo is None:
        raise ValueError('Benaroya snapshot retrieval time needs a timezone')
    description = (
        f"Free admission to the Benaroya Hall Open House. Official public hours are "
        f"10:00 AM–6:30 PM. The schedule lists advance activities at 9:00 AM and 9:30 AM before the public "
        f"hours; separate free tickets for the 4:00 PM Seattle Symphony with The Head and the "
        f"Heart performance are distributed in person beginning at 9:00 AM, limit two per person while supplies last. "
        f"Tickets are not required for the other Open House activities. Cafe purchases are "
        f"separate. The ticket sale on the schedule page applies to other eligible concerts, "
        f"not Open House admission.\nOfficial schedule: {schedule_url}\n"
        f"Public hours: {hours_url}\nOfficial event page: {official_url}\n"
        f"Schedule snapshot retrieved: {retrieved_at}"
    )
    return NormalizedEvent(source='venue:festival', source_id=source['id'] + ':' + str(day.year),
        fingerprint=identity(source['id'], str(day.year)), name=source['name'],
        description=description, start_utc=start, end_utc=end, timezone=zone,
        category='music', venue_name=venue['name'], address=venue['address'],
        city=venue['city'], region=venue['region'], country=venue['country'],
        latitude=lat, longitude=lon, coords_exact=True, ticket_url=official_url,
        promoter='Seattle Symphony', agenda=agenda, agenda_tz=zone)


def benaroya_event(source, fetcher, today):
    """Fetch and parse the configured Benaroya event using normal robots gates."""
    body = fetcher.get_text(source['schedule_url'])
    return parse_benaroya_open_house(body, source, today)


# schema.org/eventStatus values that take a festival, or one set in it, off the
# map: Postponed means the dates in the markup are the ones NOT happening, and
# MovedOnline that the grounds are empty (the same three mapsee_ingest_jsonld reads).
DEAD_STATUSES = ('EventCancelled', 'EventPostponed', 'EventMovedOnline')


def dead_status(item):
    return next((s for s in DEAD_STATUSES if s in str(item.get('eventStatus', ''))), None)


def _when_where(item, source):
    """(loc, lat, lon, zone, start, end) of a festival parent, or ValueError.
    One function for the live row and its tombstone, because the edition in the
    fingerprint is the start's year IN THIS ZONE and a second copy could drift."""
    loc = item.get('location')
    if not isinstance(loc, dict) or not isinstance(loc.get('geo'), dict):
        raise ValueError('festival has no precise location')
    lat, lon = float(loc['geo']['latitude']), float(loc['geo']['longitude'])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        raise ValueError('invalid festival location')
    zone = source.get('timezone') or item.get('eventTimeZone')
    if not zone:
        from mapsee_supabase_sync import _tz_for
        zone = _tz_for(lat, lon)
    if not zone:
        raise ValueError('festival needs a verified local timezone')
    start, end = instant(item.get('startDate'), zone), instant(item.get('endDate'), zone)
    if end <= start:
        raise ValueError('festival end precedes start')
    return loc, lat, lon, zone, start, end


def _event_id(item, source):
    return str(item.get('@id') or item.get('url') or source['official_url'])


def festival_tombstone(item, source, today):
    """The record a festival the organizer marks dead was stored as, for
    EventStore.cancel: same source_id and identity(event_id, edition). None when
    that key cannot be proved (no place or zone to date the edition by) or the
    edition is over. The schedule items are not needed: they key nothing."""
    try:
        loc, lat, lon, zone, start, end = _when_where(item, source)
    except (ValueError, TypeError, KeyError):
        return None
    if not upcoming(start, end, today):
        return None
    event_id, edition = _event_id(item, source), start[:4]
    return NormalizedEvent(source='venue:festival', source_id=event_id + ':' + edition,
                           fingerprint=identity(event_id, edition), name=item.get('name'),
                           start_utc=start, end_utc=end, timezone=zone, category='music',
                           venue_name=loc.get('name'), latitude=lat, longitude=lon)


def structured_events(body, source, today, dead=None):
    """Only explicit festival parents with complete timed subEvents qualify.

    No inference from a lineup, ticket-sale date, neighboring event, or city
    centroid. Unsupported sites stay in the review queue for another parser.
    `dead`, a list, collects (record, status) for the parents the organizer marks
    cancelled, postponed or moved online: skipping them kept a stored festival
    on the map, because an upsert cannot delete.
    """
    out = []
    for doc in Page(body).jsonld:
        for item in walk(doc):
            kinds = item.get('@type', [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if not any(k.rsplit('/', 1)[-1] in ('Festival', 'MusicFestival') for k in kinds if isinstance(k, str)):
                continue
            status = dead_status(item)
            if status:
                tomb = festival_tombstone(item, source, today) if dead is not None else None
                if tomb is not None:
                    dead.append((tomb, status))
                continue
            children = item.get('subEvent') or item.get('subEvents')
            if isinstance(children, dict):
                children = [children]
            if not isinstance(children, list) or not children or len(children) > 60:
                raise ValueError('festival needs 1–60 explicit schedule items')
            loc, lat, lon, zone, start, end = _when_where(item, source)
            if not upcoming(start, end, today):
                continue
            agenda = []
            for child in children:
                if not isinstance(child, dict) or not child.get('name'):
                    raise ValueError('incomplete schedule item')
                # A set that is off leaves the agenda the parent row is rewritten
                # with; postponed too, since its time in the markup is the old one.
                if dead_status(child):
                    continue
                stage = child.get('location')
                place = stage.get('name') if isinstance(stage, dict) else stage
                if not isinstance(place, str) or not place.strip():
                    raise ValueError('schedule item needs its stage')
                at = instant(child.get('startDate'), zone)
                until = instant(child.get('endDate'), zone) if child.get('endDate') else None
                stable = child.get('@id') or child.get('url') or f'{child["name"]}|{place}|{at[:10]}'
                agenda.append({'id': hashlib.sha1(str(stable).encode()).hexdigest(),
                               'title': child['name'], 'place': place, 'at': at, 'until': until,
                               'url': child.get('url')})
            if not agenda:
                raise ValueError('no active schedule items')
            address = loc.get('address') if isinstance(loc.get('address'), dict) else {}
            event_id = _event_id(item, source)
            edition = start[:4]
            offers = item.get('offers')
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            ticket = (offers.get('url') if isinstance(offers, dict) else None) or source['official_url']
            # Public text only; do not copy long promotional prose into the catalog.
            out.append(NormalizedEvent(source='venue:festival', source_id=event_id + ':' + edition,
                fingerprint=identity(event_id, edition), name=item.get('name'),
                description=f"Festival schedule published by the organizer: {source.get('schedule_url') or source['official_url']}",
                start_utc=start, end_utc=end, timezone=zone, category='music',
                venue_name=loc.get('name'), latitude=lat, longitude=lon, coords_exact=True,
                address=address.get('streetAddress'), city=address.get('addressLocality'),
                region=address.get('addressRegion'), country=address.get('addressCountry'),
                ticket_url=ticket, agenda=agenda, agenda_tz=zone))
    return out


def load(path, default):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def save(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(p)


def run(config, candidates, state_path, store_path, report_path, max_candidates=8, max_seconds=240, today=None):
    today = today or date.today()
    state = load(state_path, {'cursor': 0, 'sources': {}})
    nominated = load(candidates, {'candidates': []}).get('candidates', [])
    nominated = [c for c in nominated if c.get('official_url') and c.get('status') not in ('rejected', 'disabled')
                 and (c.get('dates', {}).get('end') or c.get('dates', {}).get('begin') or '9999') >= today.isoformat()]
    registered = load(config, {'festivals': []})['festivals']
    offset = int(state.get('cursor', 0)) % max(1, len(nominated))
    selected = [nominated[(offset + i) % len(nominated)] for i in range(min(max_candidates, len(nominated)))]
    # Already verified sources get a separate refresh allowance, so expanding
    # the nomination queue cannot bury tomorrow's known schedule for months.
    refresh = [c for c in nominated if state['sources'].get(c['source_id'], {}).get('verified_once') and c not in selected]
    refresh.sort(key=lambda c: state['sources'][c['source_id']].get('checked_at', ''))
    refresh = refresh[:8]
    fetcher = Fetcher(max_seconds)
    # Fresh output: a failed parser never replays stale cached events into sync.
    store = EventStore(store_path)
    store.records.clear()
    store.source_to_fp.clear()
    # ...nor replays last run's cancellations: a tombstone is this run's word.
    store.tombstones.clear()
    store.complete_reads.clear()
    results, failures, examined = [], 0, 0
    for source in registered + refresh + selected:
        if time.monotonic() >= fetcher.deadline:
            break
        sid = source.get('id') or source['source_id']
        result = {'id': sid, 'name': source['name'], 'checked_at': datetime.now(timezone.utc).isoformat(),
                  'verified_once': state['sources'].get(sid, {}).get('verified_once', False)}
        try:
            dead = []
            if source.get('parser') == 'jackson-pdf':
                event = jackson_event(source, fetcher, today)
                events = [event] if event else []
            elif source.get('parser') == 'benaroya-open-house':
                event = benaroya_event(source, fetcher, today)
                events = [event] if event else []
            else:
                url = source.get('schedule_url') or source['official_url']
                events = structured_events(fetcher.get_text(url), source, today, dead)
            for event in events:
                store.upsert(event)
            cancelled = sum(store.cancel(tomb, why) == 'cancelled' for tomb, why in dead)
            result.update(status='verified' if events else 'pending', events=len(events),
                          agenda_items=sum(len(e.agenda or []) for e in events))
            if cancelled:
                result['cancelled'] = cancelled
            if events:
                result['verified_once'] = True
            if not events:
                result['reason'] = ('The organizer marks this edition ' + ', '.join(sorted({w for _, w in dead}))
                                    if cancelled else 'No supported upcoming timetable; needs review')
        except Exception as exc:
            # Do not echo arbitrary response text or credentials from exceptions.
            result.update(status='failed' if source in registered else 'pending', reason=type(exc).__name__ + ': ' + str(exc)[:240])
            failures += source in registered
        results.append(result)
        state['sources'][sid] = result
        if source in selected:
            examined += 1
            state['cursor'] = (offset + examined) % len(nominated)
        save(state_path, state)
    if len(results) < len(registered):
        failures += 1
        results.append({'status': 'failed', 'reason': 'Budget ended before all configured festivals were checked'})
    store.save()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'requests': fetcher.requests,
              'events': len(store.records), 'agenda_items': sum(len(r.get('agenda') or []) for r in store.records.values()),
              'configured_failures': failures, 'candidates_examined': examined,
              'candidates_total': len(nominated), 'results': results}
    save(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return failures


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='festival_sources.json')
    p.add_argument('--candidates', default='festival_discovery_state.json')
    p.add_argument('--state', default='festival_schedule_state.json')
    p.add_argument('--store', default='festival_events.json')
    p.add_argument('--report', default='festival_report.json')
    p.add_argument('--max-candidates', type=int, default=8)
    p.add_argument('--max-seconds', type=int, default=240)
    a = p.parse_args()
    if not 0 <= a.max_candidates <= 50 or not 1 <= a.max_seconds <= 600:
        p.error('candidate/time budget outside supported bounds')
    raise SystemExit(bool(run(a.config, a.candidates, a.state, a.store, a.report, a.max_candidates, a.max_seconds)))


if __name__ == '__main__':
    main()
