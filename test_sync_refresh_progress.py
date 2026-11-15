"""A timed-out full refresh makes progress without reusing ownership permission."""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import mapsee_supabase_sync as sync


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name, 'events.json')
        self.store.write_text('{"records": []}', encoding='utf-8')
        self.progress = str(Path(self.tmp.name, 'progress.json'))
        self.rows = [{'external_id': str(i), 'title': f'Event {i}',
                      'description': 'source text', 'starts_at': '2099-01-01T00:00:00Z'}
                     for i in range(6)]
        self.claimed = set()
        self.reads, self.writes, self.compares, self.cancel_calls = [], [], [], []
        self.written_rows = []

    def run_main(self, rows=None, stop_on_call=None, lost_on_call=None, extra=(), unchanged=False,
                 total_outage=False, census_held=(), real_merge=False):
        rows = copy.deepcopy(self.rows if rows is None else rows)
        calls = 0

        def state(_session, _url, _key, ids, fields=(), detail=None):
            ids = list(ids)
            self.reads.append(ids)
            if detail is not None:
                detail.update({eid: dict(dict.fromkeys(sync.STATE_FIELDS),
                                        starts_at='2099-01-01T00:00:00Z', id='kept-' + eid) for eid in ids})
            return {eid: eid in self.claimed for eid in ids}

        def build(*_args, **kwargs):
            if kwargs.get('census_held') is not None:
                kwargs['census_held'].update(census_held)
            return rows

        def upsert(batch, *_args):
            nonlocal calls
            calls += 1
            if calls == stop_on_call:
                raise RuntimeError('simulated process termination')
            self.writes.append([r['external_id'] for r in batch])
            self.written_rows.extend(copy.deepcopy(batch))
            lost = (len(batch) if total_outage else 1) if calls == lost_on_call else 0
            return len(batch) - lost, 0, lost

        def compare(_session, _url, _key, batch):
            self.compares.append([r['external_id'] for r in batch])
            return {r['external_id'] for r in batch} if unchanged else set()

        args = ['sync', '--store', str(self.store), '--skip-unchanged',
                '--refresh-batch-size', '2', '--refresh-progress', self.progress, *extra]
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'MAPSEE_HOST_PROFILE_ID': 'host',
                'SUPABASE_URL': 'https://db.example', 'SUPABASE_SERVICE_ROLE_KEY': 'unused'}))
            stack.enter_context(patch('sys.argv', args))
            stack.enter_context(patch.object(sync, 'wait_for_database'))
            stack.enter_context(patch.object(sync, 'build_rows', side_effect=build))
            stack.enter_context(patch.object(sync, 'rekey_legacy', return_value=(set(), set())))
            stack.enter_context(patch.object(sync, 'rekey_absent', return_value=(set(), set())))
            stack.enter_context(patch.object(sync, 'fetch_import_state', side_effect=state))
            stack.enter_context(patch.object(sync, 'unchanged_ids', side_effect=compare))
            if not real_merge:
                stack.enter_context(patch.object(sync, 'merge_copy_categories', return_value=0))
            blocklist = stack.enter_context(patch.object(sync, 'load_blocklist', return_value=[]))
            stack.enter_context(patch.object(sync, 'upsert', side_effect=upsert))
            stack.enter_context(redirect_stdout(io.StringIO()))
            sync.main()
            return blocklist.call_count

    def test_retry_skips_settled_values_and_rechecks_remaining_ownership(self):
        with self.assertRaisesRegex(RuntimeError, 'termination'):
            self.run_main(stop_on_call=2)
        self.assertEqual(self.writes, [['0', '1']])
        self.assertEqual(set(json.loads(Path(self.progress).read_text())['done']), {'0', '1'})
        self.reads.clear()
        self.claimed.add('2')
        self.run_main()
        self.assertEqual(self.reads, [['2', '3'], ['4', '5']])
        self.assertEqual(self.writes, [['0', '1'], ['3'], ['4', '5']])
        self.assertEqual(json.loads(Path(self.progress).read_text())['done'], {})
        self.reads.clear()
        self.run_main()
        self.assertEqual(self.reads, [['0', '1'], ['2', '3'], ['4', '5']])

    def test_changed_normalized_content_is_not_skipped_after_interruption(self):
        with self.assertRaises(RuntimeError):
            self.run_main(stop_on_call=2)
        changed = copy.deepcopy(self.rows)
        changed[0]['title'] = 'New title'
        self.reads.clear()
        self.run_main(changed)
        self.assertEqual(self.reads, [['0', '2'], ['3', '4'], ['5']])

    def test_lost_batch_is_not_settled(self):
        with self.assertRaises(SystemExit):
            self.run_main(lost_on_call=2)
        self.assertEqual(self.reads, [['0', '1'], ['2', '3']])
        self.assertEqual(set(json.loads(Path(self.progress).read_text())['done']), {'0', '1'})
        self.reads.clear()
        self.run_main()
        self.assertEqual(self.reads, [['2', '3'], ['4', '5']])

    def test_total_outage_does_not_restart_the_breaker_for_each_refresh_batch(self):
        with self.assertRaises(SystemExit):
            self.run_main(lost_on_call=1, total_outage=True)
        self.assertEqual(self.reads, [['0', '1']])
        self.assertFalse(Path(self.progress).exists())
        self.reads.clear()
        self.run_main()
        self.assertEqual(self.reads, [['0', '1'], ['2', '3'], ['4', '5']])

    def test_progress_is_scoped_and_malformed_progress_only_adds_reads(self):
        progress = sync.RefreshProgress(self.progress, ['database', 'host', 'store'], self.rows)
        progress.settled(self.rows[:2])
        self.assertEqual(len(sync.RefreshProgress(self.progress, ['database', 'other', 'store'],
                                                  self.rows).pending(self.rows)), 6)
        Path(self.progress).write_text('{broken', encoding='utf-8')
        self.run_main()
        self.assertEqual(self.reads, [['0', '1'], ['2', '3'], ['4', '5']])

    def test_unchanged_rows_are_not_written_and_blocklist_read_is_once(self):
        calls = self.run_main(unchanged=True)
        self.assertEqual(calls, 1)
        self.assertEqual(self.writes, [[], [], []])
        self.assertEqual(self.compares, self.reads)
        self.assertTrue(all(len(batch) <= 2 for batch in self.reads))

    def test_only_new_and_offline_preview_leave_refresh_progress_untouched(self):
        progress = sync.RefreshProgress(self.progress, ['https://db.example', 'host', 'events.json'], self.rows)
        progress.settled(self.rows[:2])
        before = Path(self.progress).read_bytes()
        self.run_main(extra=('--only-new',))
        self.assertEqual(Path(self.progress).read_bytes(), before)
        self.run_main(extra=('--dry-run',))
        self.assertEqual(Path(self.progress).read_bytes(), before)

    def test_cancellations_still_cover_the_full_source_snapshot(self):
        self.store.write_text(json.dumps({'events': [], 'tombstones': [
            {'fingerprint': 'outside-batch', 'reason': 'STATUS:CANCELLED', 'source': 'ics:test'}]}),
            encoding='utf-8')
        with patch.object(sync, 'apply_cancellations', return_value=(set(), set(), set())) as cancel:
            self.run_main()
        self.assertEqual(set(cancel.call_args.args[3]), {'outside-batch'})

    def test_later_snapshot_removes_stale_progress_without_losing_remaining_work(self):
        with self.assertRaises(RuntimeError):
            self.run_main(stop_on_call=2)
        remaining = self.rows[1:]
        self.reads.clear()
        self.run_main(remaining)
        self.assertEqual(self.reads, [['2', '3'], ['4', '5']])

    def test_resumed_refresh_carries_completed_rows_state_cache_without_using_it_to_write(self):
        cache = str(Path(self.tmp.name, 'state.json'))
        now = datetime.now(timezone.utc)
        entry = [None] * len(sync.STATE_FIELDS) + [False, (now - timedelta(days=1)).date().isoformat()]
        rows = {r['external_id']: list(entry) for r in self.rows}
        sync.save_state_cache(cache, 'events.json|state.json', rows, now)
        with self.assertRaises(RuntimeError):
            self.run_main(stop_on_call=2, unchanged=True, extra=('--state-cache', cache))
        self.reads.clear()
        self.claimed.add('2')
        self.run_main(unchanged=True, extra=('--state-cache', cache))
        self.assertEqual(self.reads, [['2', '3'], ['4', '5']])
        saved = json.loads(Path(cache).read_text())['rows']
        self.assertEqual(saved['0'], entry)
        self.assertEqual(saved['1'], entry)
        self.assertTrue(saved['2'][len(sync.STATE_FIELDS)])
        self.assertEqual(set(saved), set(rows))

    def test_census_held_row_keeps_partial_cycle_open(self):
        self.run_main(census_held={'0'})
        saved = json.loads(Path(self.progress).read_text())['done']
        self.assertEqual(set(saved), {'1', '2', '3', '4', '5'})
        self.reads.clear()
        self.run_main()
        self.assertEqual(self.reads, [['0']])

    def test_real_merge_scans_global_copies_once_but_reads_text_only_for_each_batch(self):
        copies = {'kept-' + str(i): [{'id': 'copy-' + str(i), 'category': 'kids', 'categories': None}]
                  for i in range(6)}

        def text(_session, _url, _key, ids):
            return {eid: {'id': eid, 'title': 'Event', 'place_name': 'Library',
                          'starts_at': '2099-01-01T00:00:00Z', 'description': 'source text'} for eid in ids}

        rows = [dict(r, category='community', categories=None) for r in self.rows]
        with patch.object(sync, 'read_hidden_copies', return_value=copies) as scan, \
             patch.object(sync, 'read_copy_text', side_effect=text) as read_text:
            self.run_main(rows, real_merge=True)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual([set(c.args[3]) for c in read_text.call_args_list],
                         [{'copy-0', 'copy-1'}, {'copy-2', 'copy-3'}, {'copy-4', 'copy-5'}])
        self.assertEqual(len(self.written_rows), 6)
        self.assertTrue(all('kids' in (row.get('categories') or []) for row in self.written_rows))

    def test_failed_global_copy_scan_is_bounded_per_run_and_retried_next_run(self):
        with patch.object(sync, 'read_hidden_copies', return_value=None) as scan:
            self.run_main(real_merge=True)
            self.assertEqual(scan.call_count, 1)
            self.run_main(real_merge=True)
            self.assertEqual(scan.call_count, 2)


if __name__ == '__main__':
    unittest.main()
