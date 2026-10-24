"""Socrata schema drift, local-time queries and transient failure contracts."""
import json
from pathlib import Path
from unittest import TestCase,main
from unittest.mock import Mock,patch,mock_open
from datetime import datetime,timezone
import requests
import mapsee_ingest_opendata as ingest
import catalog_curate as curator


class OpenDataTests(TestCase):
    def setUp(self):
        self.src=next(s for s in json.loads(Path('opendata_sources.json').read_text())
                      if s['name']=='NYC Parks Events')
        self.src={**self.src,'drop_past':False}

    def test_current_parks_schema_and_timezone(self):
        row={'guid':'2181303','title':'Summer Sports Experience: Pickleball',
             'starttime':'2026-09-10T07:00:00.000','endtime':'2026-09-10T12:00:00.000',
             'coordinates':'40.591982601343, -74.139472484589',
             'location':'Greenbelt Recreation Center',
             'link':{'url':'https://www.nycgovparks.org/events/2026/09/10/pickleball'}}
        event=ingest.row_to_event(row,self.src)
        self.assertEqual(event.start_utc,'2026-09-10T11:00:00Z')
        self.assertEqual(event.end_utc,'2026-09-10T16:00:00Z')
        self.assertEqual(event.source_id,'2181303')
        self.assertAlmostEqual(event.latitude,40.591982601343)
        self.assertEqual(self.src['order'],'starttime, guid')
        self.assertGreaterEqual(self.src['limit'],1079)

    def test_offset_is_preserved_and_date_only_stays_all_day(self):
        row={'title':'Test','starttime':'2030-09-10T07:00:00+02:00','coordinates':'40.6,-74.1'}
        self.assertEqual(ingest.row_to_event(row,self.src).start_utc,'2030-09-10T05:00:00Z')
        row['starttime']='2030-09-10'
        self.assertIsNone(ingest.row_to_event(row,self.src).start_utc)

    def test_timeout_retries_but_refusal_and_bad_schema_do_not(self):
        ok=Mock(status_code=200);ok.json.return_value=[]
        session=Mock();session.get.side_effect=[requests.Timeout('timeout'),ok]
        with patch.object(ingest.time,'sleep') as sleep:
            ingest.ingest_socrata(Mock(),session,self.src)
        self.assertEqual(session.get.call_count,2)
        sleep.assert_called_once_with(2)
        for status in (400,403):
            response=Mock(status_code=status)
            response.raise_for_status.side_effect=requests.HTTPError(str(status))
            session=Mock();session.get.return_value=response
            with self.assertRaises(requests.HTTPError):ingest.ingest_socrata(Mock(),session,self.src)
            self.assertEqual(session.get.call_count,1)

    def test_query_clock_uses_source_zone(self):
        real=datetime
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return real(2026,9,9,22,0,tzinfo=timezone.utc).astimezone(tz)
        response=Mock(status_code=200);response.json.return_value=[]
        session=Mock();session.get.return_value=response
        with patch.object(ingest,'datetime',Clock):ingest.ingest_socrata(Mock(),session,self.src)
        self.assertEqual(session.get.call_args.kwargs['params']['$where'],"endtime >= '2026-09-09T18:00:00'")

    def test_fee_column_is_said_in_words_the_offer_tagger_reads(self):
        src={'name':'Chicago Park District Activities (Chicago)','timezone':'America/Chicago','drop_past':False,
             'map':{'title':'title','start':'start_date','lat':'latitude','lon':'longitude',
                    'venue':'location_facility','description':'description','fee':'fee'}}
        row={'title':'Ice Skating - Freestyle Ice','start_date':'2030-10-03T06:00:00.000',
             'latitude':'41.95','longitude':'-87.69','location_facility':'McFetridge Sports Center',
             'description':'October 3, 2030 to October 3, 2030','fee':'8'}
        self.assertEqual(ingest.row_to_event(row,src).description,
                         'October 3, 2030 to October 3, 2030\n\nAdmission: $8.')
        # "Admission: free" is what 0227's strict `free` pattern matches; a bare "Free." is not.
        self.assertEqual(ingest.row_to_event({**row,'fee':'0'},src).description.splitlines()[-1],'Admission: free.')
        self.assertEqual(ingest.row_to_event({**row,'fee':'0','description':None},src).description,'Admission: free.')
        # no price is not a price: a blank or unreadable fee says nothing
        for raw in ('',None,'TBD','-1'):
            self.assertEqual(ingest.row_to_event({**row,'fee':raw},src).description,'October 3, 2030 to October 3, 2030')
        self.assertEqual(ingest.row_to_event({**row,'fee':'12.5'},{**src,'currency':'C$'}).description.splitlines()[-1],
                         'Admission: C$12.5.')

    def test_chicago_public_library_is_read_once_through_bibliocommons(self):
        # Retired 2026-10-13. The city's copy of CPL's calendar (vsdy-d8k7, keyed
        # on event_page) wrote every event a second time under another
        # fingerprint: of the 188 records it returned that morning, 187 were in a
        # BiblioCommons chipublib read of the same morning (by event id), 186 at
        # the same start, 0 with the same fingerprint. CPL was 358 of the 1,373
        # duplicate pairs a census of 25,369 live rows found (26%). BiblioCommons
        # is the one reader; the open-data copy may come back only if chipublib
        # leaves bibliocommons_sources.json (the parkrun twin rule).
        sources=json.loads(Path('opendata_sources.json').read_text())
        biblio=json.loads(Path('bibliocommons_sources.json').read_text())
        if any(s.get('slug')=='chipublib' for s in biblio['sites']):
            self.assertEqual([s['name'] for s in sources if 'vsdy-d8k7' in s.get('url','')],[])

    def test_my_chi_leaves_chicago_public_library_to_bibliocommons(self):
        # 2026-10-23: 11,095 of My CHI's 13,237 future rows (84%) were CPL's own
        # programmes, written clockless (a naive midnight is all day) - 42 of
        # Chicago's 500 visible rows, 31 of 34 sampled twins of a timed
        # BiblioCommons row. The same twin rule as vsdy-d8k7, kept in the query.
        sources=json.loads(Path('opendata_sources.json').read_text())
        biblio=json.loads(Path('bibliocommons_sources.json').read_text())
        if any(s.get('slug')=='chipublib' for s in biblio['sites']):
            my_chi=[s for s in sources if 'w22p-bfyb' in s.get('url','')]
            self.assertEqual(len(my_chi),1)
            self.assertIn("org_name != 'Chicago Public Library'", my_chi[0]['where'])
            self.assertIn("{now}", my_chi[0]['where'])

    def test_merge_does_not_restore_retired_chicago_duplicate_while_primary_exists(self):
        import tempfile
        duplicate = {'type':'opendata','name':'Chicago Public Library Events',
                     'url':'https://data.cityofchicago.org/resource/vsdy-d8k7.json?$limit=1000'}
        other = {'type':'opendata','name':'Other Chicago Calendar',
                 'url':'https://data.cityofchicago.org/resource/other.json'}
        for primary_configured, expected in ((True, {'Other Chicago Calendar'}),
                                             (False, {'Chicago Public Library Events',
                                                      'Other Chicago Calendar'})):
            with self.subTest(primary_configured=primary_configured), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root/'opendata_sources.json').write_text(json.dumps([{'name':'Existing','url':'https://example.org/existing'}]))
                (root/'bibliocommons_sources.json').write_text(json.dumps(
                    {'sites':[{'slug':'chipublib'}] if primary_configured else []}))
                verified = root/'verified.json'
                verified.write_text(json.dumps([duplicate, other]))
                with patch.object(curator, 'HERE', folder):
                    curator.cmd_merge(str(verified))
                merged = json.loads((root/'opendata_sources.json').read_text())
                names = {entry['name'] for entry in merged}
                self.assertEqual(names, {'Existing'} | expected)

    def test_verify_skips_duplicate_but_allows_fallback_after_primary_removal(self):
        import tempfile
        candidate = {'type':'opendata','name':'Chicago Public Library Events',
                     'url':'https://data.cityofchicago.org/resource/vsdy-d8k7.json?$limit=1000'}
        for primary_configured, expected_count in ((True, 0), (False, 1)):
            with self.subTest(primary_configured=primary_configured), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root/'bibliocommons_sources.json').write_text(json.dumps(
                    {'sites':[{'slug':'chipublib'}] if primary_configured else []}))
                candidates = root/'candidates.json'
                candidates.write_text(json.dumps([candidate]))
                with patch.object(curator, 'HERE', folder), \
                     patch.object(curator, '_session', return_value=object()), \
                     patch.object(curator, '_load_ledger', return_value={}), \
                     patch.object(curator, '_save_ledger'), \
                     patch.object(curator, '_config_keys', return_value=set()), \
                     patch.dict(curator.VERIFIERS, {'opendata': lambda _s, _e: (True, 'verified')}):
                    curator.cmd_verify(str(candidates))
                verified = json.loads((root/'candidates.verified.json').read_text())
                self.assertEqual(len(verified), expected_count)

    def test_failed_source_is_reported_after_other_sources_are_saved(self):
        sources=[{'name':'good'},{'name':'broken'},{'name':'also good'}]
        store=Mock(records={})
        with patch('builtins.open',mock_open(read_data=json.dumps(sources))), \
             patch.object(ingest,'EventStore',return_value=store), \
             patch.object(ingest,'_save_geo_cache'), \
             patch.object(ingest,'ingest_socrata',side_effect=[3,requests.HTTPError('400'),2]) as run:
            code=ingest.main(['--config','unused.json','--store','unused-store.json'])
        self.assertEqual(code,1)
        self.assertEqual(run.call_count,3)
        store.save.assert_called_once()

if __name__=='__main__':main()
