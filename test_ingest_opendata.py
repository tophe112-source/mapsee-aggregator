"""Socrata schema drift, local-time queries and transient failure contracts."""
import json
from pathlib import Path
from unittest import TestCase,main
from unittest.mock import Mock,patch,mock_open
from datetime import datetime,timezone
import requests
import mapsee_ingest_opendata as ingest


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
