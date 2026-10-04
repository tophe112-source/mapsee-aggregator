"""Actual Oct 1 Unsie notice shapes: source facts, stable IDs, safe refresh.

Small source fixtures keep only factual fields and the DOM contract. No
production calls; the calendar integration fails on unexpected network use.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mapsee_event_details import ramart_details, read_ramart_details
from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
import mapsee_ingest_ics as ICS
from mapsee_supabase_sync import to_row, needs_detail_sync

URL = 'https://www.ramart.org/event/potters-wheel/'
START = '2099-10-03T16:00:00Z'
TITLE = "Potter's Wheel (Ages 7-13)"
LOC = 'RAM Wustum, 2519 Northwestern Avenue, Racine, WI, 53404, United States'


def page(title=TITLE, artist='Mallory Olesen Willing', tickets='<div>$104</div><div>RAM Member</div><div>$130</div><div>Non-Member</div>'):
    event = {'@type': 'Event', 'name': title, 'startDate': '2099-10-03T11:00:00-05:00',
             'location': {'address': {'streetAddress': '2519 Northwestern Avenue',
                                     'addressLocality': 'Racine', 'addressRegion': 'WI',
                                     'postalCode': '53404', 'addressCountry': 'United States'}},
             'performer': 'Organization'}
    return ('<script type="application/ld+json">' + json.dumps(event) + '</script>'
            '<div id="overview"><h2>Overview</h2><div><p>6 Weeks</p>'
            + (f'<p>Instructor/Artist: {artist}</p>' if artist else '')
            + '<p>Learn wheel throwing and create pottery.</p></div></div>'
            '<div id="buy-tickets"><h2>Buy Tickets</h2><div>' + tickets + '</div></div>'
            '<footer><p>Instructor/Artist: Footer Name</p><div>$1 Non-Member</div></footer>')


class Details(unittest.TestCase):
    def test_public_rate_and_real_artist(self):
        got = ramart_details(page(), URL, TITLE, START)
        self.assertEqual(got['source_details']['offer'], {'url': URL, 'price': '130', 'currency': 'USD'})
        self.assertEqual(got['source_details']['performers'], [{'type': 'Person', 'name': 'Mallory Olesen Willing'}])
        self.assertEqual((got['address'], got['city'], got['region'], got['postal_code']),
                         ('2519 Northwestern Avenue', 'Racine', 'WI', '53404'))
        self.assertIn('Admission: USD 130 (non-member).', got['description'])
        self.assertNotIn('Footer Name', got['description'])

    def test_sold_out_does_not_invent_price(self):
        got = ramart_details(page(tickets='<p>This class is sold out.</p>'), URL, TITLE, START)
        self.assertEqual(got['source_details']['offer'], {'url': URL, 'availability': 'SoldOut'})
        self.assertTrue(got['description'].startswith('This class is sold out.'))

    def test_member_only_is_not_public_price(self):
        got = ramart_details(page(tickets='<div>$104</div><div>RAM Member</div>'), URL, TITLE, START)
        self.assertNotIn('offer', got['source_details'])

    def test_registration_cutoff_survives_prose_cap_without_inventing_stock(self):
        notice = ('Online registration closes four days before class begins. '
                  'After that, call RAM Wustum at 262.636.9177 to confirm availability.')
        body = page().replace('<p>Learn wheel throwing and create pottery.</p>',
                              '<p>' + 'Long class overview. ' * 70 + '</p><p>' + notice + '</p>')
        got = ramart_details(body, URL, TITLE, START)
        self.assertIn(notice, got['description'][:800])
        self.assertEqual(got['source_details']['offer'], {'url': URL, 'price': '130', 'currency': 'USD'})
        footer = page().replace('</footer>', '<p>' + notice + '</p></footer>')
        self.assertNotIn(notice, ramart_details(footer, URL, TITLE, START)['description'])

    def test_free_festival_has_no_placeholder_performer(self):
        got = ramart_details(page(title='Free Fall Family Fun Fest', artist=None, tickets=''),
                             URL, 'Free Fall Family Fun Fest', START)
        self.assertTrue(got['source_details']['free'])
        self.assertEqual(got['source_details']['offer']['price'], '0')
        self.assertNotIn('performers', got['source_details'])

    def test_wrong_occasion_is_ignored(self):
        self.assertIsNone(ramart_details(page(), URL, 'Other Class', START))
        self.assertIsNone(ramart_details(page(), URL, TITLE, '2099-10-10T16:00:00Z'))

    def test_refusals_and_offsite_urls_make_no_page_request(self):
        class Session:
            def get(self, *args, **kwargs):
                raise AssertionError('unexpected page request')
        class Refused:
            def check(self, url):
                return {'allowed': False}
        self.assertIsNone(read_ramart_details(Session(), Refused(), URL, TITLE, START))
        self.assertIsNone(read_ramart_details(Session(), Refused(), 'https://elsewhere.test/event/one/', TITLE, START))

    def test_ics_integration_keeps_fingerprint_and_exact_time(self):
        text = ('BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test\nSUMMARY:' + TITLE + '\n'
                'DTSTART:20991003T160000Z\nDTEND:20991003T173000Z\nLOCATION:' + LOC + '\n'
                'URL:' + URL + '\nEND:VEVENT\nEND:VCALENDAR\n')
        class Store:
            rows = []
            def upsert(self, ev):
                self.rows.append(ev)
        store = Store()
        with patch.object(ICS, '_fetch_ics', return_value=(text, '200')), \
             patch.object(ICS, 'make_location_geocoder', return_value=lambda loc: (42.7414667, -87.8125342)), \
             patch('mapsee_event_details.ramart_reader', return_value=lambda url, title, start: ramart_details(page(), url, title, start)):
            ICS.ingest_ics(store, None, {'name': 'RAM', 'url': 'https://www.ramart.org/events/?ical=1', 'details': 'ramart'})
        ev = store.rows[0]
        self.assertEqual(ev.fingerprint, make_fingerprint(TITLE, '2099-10-03', LOC))
        self.assertEqual(ev.start_utc, START)
        self.assertEqual(ev.address, '2519 Northwestern Avenue')
        row = to_row(ev.as_record('2099-10-01T00:00:00Z'), 'fixture-host')
        self.assertEqual(row['source_details']['offer']['price'], '130')
        self.assertIn('Admission: USD 130', row['description'])
        self.assertIn('Tickets / info: ' + URL, row['description'])

    def test_absence_preserves_and_explicit_empty_clears(self):
        ev = NormalizedEvent(source='test', source_id='a', name=TITLE, fingerprint='fp',
                             start_utc=START, end_utc='2099-10-03T17:30:00Z', venue_name=LOC,
                             latitude=42.7414667, longitude=-87.8125342)
        self.assertNotIn('source_details', ev.as_record('2099-10-01T00:00:00Z'))
        self.assertNotIn('source_details', to_row(ev.as_record('now'), 'fixture-host'))
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(str(Path(tmp) / 'events.json'))
            ev.source_details = {'offer': {'url': URL, 'price': '130', 'currency': 'USD'}}
            ev.description = 'Admission: USD 130.'
            store.upsert(ev)
            ev.source_details = None
            store.upsert(ev)
            self.assertIn('offer', store.records['fp']['source_details'])
            ev.source_details = {'offer': {'url': URL, 'availability': 'SoldOut'}}
            ev.description = 'This class is sold out.'
            store.upsert(ev)
            self.assertEqual(store.records['fp']['description'], 'This class is sold out.')
            ev.source_details = {}
            store.upsert(ev)
            self.assertEqual(store.records['fp']['source_details'], {})
            self.assertIsNone(to_row(store.records['fp'], 'fixture-host')['source_details'])

    def test_a_community_centre_session_is_city_data_not_a_show(self):
        # The sync's "More on this show" search and its violet pin are a
        # deny-list: every adapter not named inherits them. A lane swim is not a show.
        for source in ('toronto-rec', 'toronto-earlyon', 'linkedevents:helsinki', 'perfectmind'):
            ev = NormalizedEvent(source=source, source_id='s1', name='Lane Swim, ages 7+',
                                 description='Admission: free.', start_local='2099-10-19T11:30:00-04:00',
                                 start_utc='2099-10-19T15:30:00Z', venue_name='Antibes Community Centre',
                                 latitude=43.77, longitude=-79.45, ticket_url='https://www.toronto.ca/x')
            ev.fingerprint = make_fingerprint(ev.name, '2099-10-19', ev.venue_name)
            row = to_row(ev.as_record('2099-10-01T00:00:00Z'), 'fixture-host')
            self.assertNotIn('More on this show', row['description'], source)
            self.assertEqual(row['color_hex'], '#0891b2', source)

    def test_only_new_refreshes_facts_but_respects_claims_and_failed_rekeys(self):
        rec = {'fingerprint': 'fp', 'source_details': {}}
        self.assertTrue(needs_detail_sync(rec, {'fp': False}))
        self.assertFalse(needs_detail_sync(rec, {'fp': True}))
        self.assertFalse(needs_detail_sync(rec, {'fp': False}, held={'fp'}))
        self.assertFalse(needs_detail_sync({'fingerprint': 'fp'}, {'fp': False}))
        self.assertTrue(needs_detail_sync({'fingerprint': 'new'}, {'fp': False}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
