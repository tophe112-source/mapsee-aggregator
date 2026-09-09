#!/usr/bin/env python3
"""Offline tests through production festival parser, network gate and read-back."""
import copy
import json
from datetime import date
from unittest.mock import patch
from mapsee_ingest_festivals import Page, Fetcher, instant, jackson_pdf_url, structured_events, identity
from verify_festival_sync import verify


def parent():
    return {'@type': 'MusicFestival', '@id': 'https://festival.example/2026', 'name': 'Festival',
            'startDate': '2026-09-12T16:30:00-07:00', 'endDate': '2026-09-12T22:30:00-07:00',
            'eventTimeZone': 'America/Los_Angeles',
            'location': {'name': 'Park', 'geo': {'latitude': 47.6, 'longitude': -122.3}},
            'subEvent': [{'name': 'Artist', 'startDate': '2026-09-12T17:00:00-07:00',
                          'endDate': '2026-09-12T18:00:00-07:00', 'location': {'name': 'Main stage'}}]}


def parse(p):
    return structured_events('<script type="application/ld+json">'+json.dumps(p)+'</script>',
                             {'official_url': 'https://festival.example'}, date(2026, 9, 9))


def refuses(fn):
    try:
        fn()
    except (ValueError, RuntimeError):
        return
    raise AssertionError('invalid input accepted')


def main():
    event = parse(parent())[0]
    assert len(event.agenda) == 1 and event.agenda[0]['at'] == '2026-09-13T00:00:00Z'
    # Change in a published set time updates one stable calendar item.
    p = parent()
    p['subEvent'][0]['startDate'] = '2026-09-12T17:15:00-07:00'
    assert parse(p)[0].agenda[0]['id'] == event.agenda[0]['id']
    assert parse(p)[0].fingerprint == event.fingerprint
    p = parent()
    p['subEvent'][0].pop('startDate')
    refuses(lambda: parse(p))
    p = parent()
    p['subEvent'][0].pop('location')
    refuses(lambda: parse(p))
    p = parent()
    p['subEvent'][0]['startDate'] = '2026-09-12T12:00:00-07:00'
    refuses(lambda: parse(p))
    p = parent()
    p['eventStatus'] = 'https://schema.org/EventCancelled'
    assert parse(p) == []
    refuses(lambda: instant('2026-11-01T01:30:00', 'America/Los_Angeles'))
    refuses(lambda: instant('2026-03-08T02:30:00', 'America/Los_Angeles'))
    refuses(lambda: instant('2026-09-12'))
    assert identity('festival', '2026') != identity('festival', '2027')

    # Follow the currently linked builder asset rather than a frozen PDF URL.
    class Assets:
        def get(self, url):
            return ('PagesStructures["page"] = ' + json.dumps({'items': [{'fileType': 'pdf',
                    'title': '2027 Schedule.pdf', 'storageServer': 6, 'ownerID': 1,
                    'fileName': 'new-id/2027 Schedule.pdf'}]}) + ';').encode()
    url = jackson_pdf_url('<script src="https://storage.googleapis.com/wzukusers/user-1/current.js"></script>', Assets())
    assert url.endswith('new-id/2027%20Schedule.pdf')
    assert jackson_pdf_url('<a href="/files/schedule.pdf">Schedule</a>', Assets()) == 'https://jacksonstreetjazz.org/files/schedule.pdf'
    calendar = Page('<add-to-calendar-button startDate="2026-09-12" timeZone="America/Los_Angeles">').calendars[0]
    assert calendar['startdate'] == '2026-09-12' and calendar['timezone'] == 'America/Los_Angeles'

    # Refusals are not retried or disguised as browsers. Redirect hosts get
    # their own robots check before any page fetch.
    f = Fetcher()
    calls = []
    def raw(url):
        calls.append(url)
        if url.endswith('robots.txt'):
            return 200, b'User-agent: *\nDisallow: /', None
        raise AssertionError('forbidden page fetched')
    f._raw = raw
    refuses(lambda: f.get('https://festival.example/schedule'))
    assert len(calls) == 1
    f = Fetcher()
    f._raw = lambda url: (403, b'', None)
    refuses(lambda: f.get('https://festival.example/schedule'))

    # Missing/stripped agendas cannot masquerade as successful writes.
    from mapsee_supabase_sync import to_row
    row = to_row(event.as_record('now'), 'host')
    actual = dict(row, id='uuid', agenda_rev=1, claimed_at=None)
    class Response:
        def raise_for_status(self): pass
        def json(self): return [actual]
    class Session:
        def get(self, url, **kwargs):
            assert kwargs['headers']['apikey'] == 'test-key'
            return Response()
    assert len(verify([row, row], Session(), 'https://db.example', 'test-key')) == 2
    actual['agenda'] = None
    refuses(lambda: verify([row], Session(), 'https://db.example', 'test-key'))
    print('festival ingest: structured schedule, stable IDs, bounds, DST, assets, robots and read-back passed')


if __name__ == '__main__':
    main()
