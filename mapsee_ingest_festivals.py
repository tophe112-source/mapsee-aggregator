#!/usr/bin/env python3
"""Festival parents with internal agendas; bounded official-site verification.

Configured PDF parsers and verified JSON-LD subEvent schedules share the normal
EventStore/sync path. Discovery nominations are NEVER treated as timetables.
An unread or unsupported source is reported, not replaced with an empty agenda.
"""
from __future__ import annotations

import argparse
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
            return 200, bytes(data), None

    def get(self, url, check_robots=True):
        for _ in range(5):
            p = urlsplit(url)
            origin = f'{p.scheme}://{p.netloc}'
            if check_robots:
                if origin not in self.robots:
                    # No recursive redirect following for robots. An unavailable
                    # policy defers this candidate, never grants crawl permission.
                    code, body, _ = self._raw(origin + '/robots.txt')
                    if code not in (200, 404):
                        raise ValueError(f'robots unavailable: HTTP {code}')
                    robot = RobotFileParser()
                    robot.parse(body.decode('utf-8', 'replace').splitlines() if code == 200 else [])
                    if code == 404:
                        robot.allow_all = True
                    self.robots[origin] = robot
                if not self.robots[origin].can_fetch(UA, url):
                    raise ValueError('robots disallows this URL')
            code, body, redirect = self._raw(url)
            if redirect:
                url = redirect
                continue
            if code != 200:
                raise ValueError(f'source HTTP {code}')
            return body
        raise ValueError('too many redirects')


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
        body = fetcher.get(asset).decode('utf-8')
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
    schedule_html = fetcher.get(source['schedule_url']).decode('utf-8')
    pdf_url = jackson_pdf_url(schedule_html, fetcher)
    parsed = parse_jackson_pdf(fetcher.get(pdf_url), timezone=source['timezone'])
    buttons = Page(fetcher.get(source['ticket_url']).decode('utf-8')).calendars
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


def structured_events(body, source, today):
    """Only explicit festival parents with complete timed subEvents qualify.

    No inference from a lineup, ticket-sale date, neighboring event, or city
    centroid. Unsupported sites stay in the review queue for another parser.
    """
    out = []
    for doc in Page(body).jsonld:
        for item in walk(doc):
            kinds = item.get('@type', [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if not any(k.rsplit('/', 1)[-1] in ('Festival', 'MusicFestival') for k in kinds if isinstance(k, str)):
                continue
            if any(s in str(item.get('eventStatus', '')) for s in ('EventCancelled', 'EventPostponed')):
                continue
            children = item.get('subEvent') or item.get('subEvents')
            if isinstance(children, dict):
                children = [children]
            if not isinstance(children, list) or not children or len(children) > 60:
                raise ValueError('festival needs 1–60 explicit schedule items')
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
            if not upcoming(start, end, today):
                continue
            agenda = []
            for child in children:
                if not isinstance(child, dict) or not child.get('name'):
                    raise ValueError('incomplete schedule item')
                if 'EventCancelled' in str(child.get('eventStatus', '')):
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
            event_id = str(item.get('@id') or item.get('url') or source['official_url'])
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
    results, failures, examined = [], 0, 0
    for source in registered + refresh + selected:
        if time.monotonic() >= fetcher.deadline:
            break
        sid = source.get('id') or source['source_id']
        result = {'id': sid, 'name': source['name'], 'checked_at': datetime.now(timezone.utc).isoformat(),
                  'verified_once': state['sources'].get(sid, {}).get('verified_once', False)}
        try:
            if source.get('parser') == 'jackson-pdf':
                event = jackson_event(source, fetcher, today)
                events = [event] if event else []
            else:
                url = source.get('schedule_url') or source['official_url']
                events = structured_events(fetcher.get(url).decode('utf-8'), source, today)
            for event in events:
                store.upsert(event)
            result.update(status='verified' if events else 'pending', events=len(events),
                          agenda_items=sum(len(e.agenda or []) for e in events))
            if events:
                result['verified_once'] = True
            if not events:
                result['reason'] = 'No supported upcoming timetable; needs review'
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
