"""Offline contracts for the automation that can otherwise fail hours later."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import yaml
from workflow_report import report

ROOT = Path(__file__).resolve().parent


class WorkflowContracts(unittest.TestCase):
    def test_all_workflows_parse_and_secret_jobs_never_accept_forks(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            text = path.read_text(encoding='utf-8')
            workflow = yaml.safe_load(text)
            self.assertIsInstance(workflow['jobs'], dict, path.name)
            # YAML 1.1 interprets `on` as True; Actions uses YAML 1.2.
            triggers = workflow.get('on', workflow.get(True, {}))
            if 'secrets.' in text:
                self.assertNotIn('pull_request', triggers, path.name)
                self.assertNotIn('pull_request_target', triggers, path.name)

    def test_every_feed_adapter_runs_in_exactly_one_group(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['feeds']
        self.assertFalse(job['strategy']['fail-fast'])
        expected = {
            'ics': {'ics'},
            'civic': {'opendata', 'tribe', 'gancio', 'mobilizon', 'bibliocommons', 'mapasculturais',
                      'moshtix', 'ods', 'ckan'},
            'local': {'markets', 'parkrun', 'fairs', 'programs', 'jsonld', 'squarespace',
                      'mylisting', 'luma', 'restaurants', 'seoul', 'affiliates', 'ubereats',
                      'venuepilot', 'dice_venue', 'rolodex', 'pioneersquare', 'seattlecenter', 'slu',
                      'ava', 'rallybound', 'luminate'},
            'rec': {'toronto_rec', 'linkedevents', 'perfectmind', 'drupal_fullcalendar', 'revize',
                    'glenecho', 'montreal_loisirs', 'phl_parks', 'barcelona', 'lcsd', 'madrid',
                    'facility_hours'},
        }
        found = {group: set() for group in expected}
        for step in job['steps']:
            adapters = re.findall(r'python mapsee_ingest_(\w+)\.py', step.get('run', ''))
            for adapter in adapters:
                group = re.fullmatch(r"matrix.group == '(\w+)'", step['if'])[1]
                self.assertNotIn(adapter, found[group])
                found[group].add(adapter)
                self.assertTrue(step['continue-on-error'])
                self.assertLess(step['timeout-minutes'], job['timeout-minutes'])
                self.assertNotIn('|| true', step['run'])
        self.assertEqual(found, expected)
        steps = job['steps']
        save = next(s for s in steps if s.get('uses') == 'actions/cache/save@v4')
        self.assertEqual(save['if'], 'always()')
        self.assertIn('matrix.group', save['with']['key'])
        self.assertIn("contains(steps.*.outcome, 'failure')", steps[-1]['if'])
        self.assertIn('openactive', workflow['jobs']['indexnow']['needs'])
        extra = next(s for s in workflow['jobs']['extra_sources']['steps']
                     if s.get('name') == 'Sync extra sources + Eventbrite to Supabase')
        self.assertIn('"$(date -u +%u)" = "4"', extra['run'])

    def test_report_uses_failure_outcome_not_tolerated_conclusion(self):
        text = report({'ingest_ics': {'outcome': 'failure', 'conclusion': 'success'},
                       'ingest_tribe': {'outcome': 'skipped'}, 'sync': {'outcome': 'success'}}, 'ics')
        self.assertIn('| ingest_ics | failure |', text)
        self.assertIn('1 failed or cancelled', text)
        self.assertNotIn('| ingest_tribe', text)

    def test_feed_budgets_leave_time_to_sync_and_persist_resume_cursors(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['feeds']['steps']
        restore = next(s for s in steps if s.get('uses') == 'actions/cache/restore@v4')
        save = next(s for s in steps if s.get('uses') == 'actions/cache/save@v4')
        for name in ('ics_cursor.json', 'jsonld_cursor.json'):
            self.assertIn(name, restore['with']['path'])
            self.assertIn(name, save['with']['path'])
        # The Events Calendar step resumes by cursor too: with a deadline and no
        # cursor, the same 65 of 673 sites went unread every night (2026-09-27).
        tribe = next(s for s in steps if s.get('id') == 'ingest_tribe')
        self.assertIn('--deadline', tribe['run'])
        self.assertIn('tribe_cursor.json', restore['with']['path'])
        self.assertIn('tribe_cursor.json', save['with']['path'])
        self.assertEqual(save['if'], 'always()')
        for adapter in ('ics', 'jsonld'):
            step = next(s for s in steps if s.get('id') == f'ingest_{adapter}')
            budget = re.search(r'--max-minutes (\d+)', step['run'])
            self.assertIsNotNone(budget)
            self.assertLessEqual(int(budget[1]) + 10, step['timeout-minutes'])
            self.assertTrue(step['continue-on-error'])
        self.assertTrue(any(s.get('id') == 'sync' for s in steps))
        # The rec group's first sync of ~103,600 rows ran out 20 minutes
        # (2026-10-04), and so did civic's Wednesday refresh of 143,128 events
        # (2026-09-30); both keep a longer cap, and ics and local keep 20.
        sync = next(s for s in steps if s.get('id') == 'sync')
        self.assertEqual(sync['timeout-minutes'],
                         "${{ (matrix.group == 'rec' || matrix.group == 'civic') && 60 || 20 }}")
        self.assertIn("contains(steps.*.outcome, 'failure')", steps[-1]['if'])

    def test_rec_timetables_retire_absent_sessions_against_a_cached_manifest(self):
        # A community-centre session gone from a COMPLETE read is cancelled
        # (mapsee_supabase_sync --retire-absent). The baseline lives in the
        # Actions cache like the cursors: restored and saved always(), and only
        # the rec group's syncs compare against it.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['feeds']['steps']
        restore = next(s for s in steps if s.get('uses') == 'actions/cache/restore@v4')
        save = next(s for s in steps if s.get('uses') == 'actions/cache/save@v4')
        for name in ('rec_absence_manifest.json', 'facility_hours_manifest.json'):
            self.assertIn(name, restore['with']['path'])
            self.assertIn(name, save['with']['path'])
        self.assertEqual(save['if'], 'always()')
        sync = next(s for s in steps if s.get('id') == 'sync')['run']
        self.assertIn('[ "${{ matrix.group }}" = "rec" ] && ABSENT="--retire-absent --manifest '
                      'rec_absence_manifest.json"', sync)
        self.assertIn('--skip-unchanged $ONLY_NEW $ABSENT', sync)
        hours = next(s for s in steps if s.get('id') == 'ingest_facility_hours')['run']
        self.assertIn('python mapsee_supabase_sync.py --store facility_hours_events.json --skip-unchanged '
                      '--retire-absent --manifest facility_hours_manifest.json', hours)
        self.assertNotIn('--only-new', hours)
        # Nowhere else: absence is only meaningful for a source read whole.
        text = (ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8')
        flags = [line for line in text.splitlines()
                 if '--retire-absent' in line and not line.strip().startswith('#')]
        self.assertEqual(len(flags), 2)
        tests = (ROOT / '.github/workflows/tests.yml').read_text(encoding='utf-8')
        self.assertIn('run: python test_cancellations.py', tests)

    def test_cursor_upload_retries_without_silently_losing_progress(self):
        for name in ('osm-food', 'osm-secondhand', 'osm-amenities'):
            workflow = yaml.safe_load((ROOT / f'.github/workflows/{name}.yml').read_text(encoding='utf-8'))
            steps = workflow['jobs']['pull']['steps']
            primary = next(s for s in steps if s.get('id') == 'cursor_upload')
            retry = next(s for s in steps if 'Retry cursor handoff' in s.get('name', ''))
            self.assertTrue(primary['continue-on-error'])
            self.assertNotIn('continue-on-error', retry)
            self.assertIn("steps.cursor_upload.outcome == 'failure'", retry['if'])
            self.assertNotEqual(primary['with']['name'], retry['with']['name'])
            self.assertTrue(retry['with']['name'].startswith('cursor-'))
        food = yaml.safe_load((ROOT / '.github/workflows/osm-food.yml').read_text(encoding='utf-8'))
        self.assertNotIn('--warm-cache', str(food['jobs']['plan']))
        save = next(s for s in food['jobs']['pull']['steps'] if s.get('uses') == 'actions/cache/save@v4')
        self.assertEqual(save['if'], 'always()')
        self.assertIn('matrix.area', save['with']['key'])

    def test_markets_osm_ends_its_own_sweep_and_always_syncs(self):
        # 8 of 9 sweep days (08-21..09-11) were cancelled at the 330-minute cap
        # with the sync skipped. The budget plus the worst single bbox
        # (4 x 180s + 65s backoff, ~14 min) must leave 30 minutes for the sync.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['markets_osm']
        sweep = next(s for s in job['steps'] if s.get('name') == 'Sweep OpenStreetMap marketplaces')
        sync = next(s for s in job['steps'] if s.get('name') == 'Sync OSM markets to Supabase')
        budget = re.search(r'--max-minutes (\d+)', sweep['run'])
        self.assertIsNotNone(budget)
        self.assertLessEqual(int(budget[1]) + 14 + 30, job['timeout-minutes'])
        self.assertEqual(sync['if'], 'always()')
        self.assertIn('[ -f osm_market_events.json ]', sync['run'])
        # Sharded Mon/Fri: each half is only in the store on its own day, so
        # neither day may skip existing rows or that half never refreshes.
        self.assertIn('--skip-unchanged', sync['run'])
        self.assertNotIn('--only-new', sync['run'])

    def test_meetup_stops_at_its_own_deadline_before_githubs(self):
        # timeout-minutes 360 IS GitHub's hard job limit, where no always() step
        # runs; the job reached 326 minutes on 2026-09-04. The deadline is
        # stamped first, passed to the international sweep, and leaves at least
        # 30 minutes of the job cap for the final sync, which runs always().
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['meetup']
        steps = job['steps']
        clock = re.search(r'MEETUP_DEADLINE=\$\(\( \$\(date \+%s\) \+ (\d+)\*60 \)\)" >> "\$GITHUB_ENV"',
                          steps[0].get('run', ''))
        self.assertIsNotNone(clock, 'the first step must stamp MEETUP_DEADLINE')
        self.assertLess(job['timeout-minutes'], 360)
        self.assertLessEqual(int(clock[1]) + 30, job['timeout-minutes'])
        intl = next(s for s in steps if s.get('name') == 'Sweep Meetup across the international metros')
        self.assertIn('--deadline "${MEETUP_DEADLINE:-0}"', intl['run'])
        final = next(s for s in steps if s.get('name', '').startswith('Sync Meetup events to Supabase'))
        self.assertEqual(final['if'], 'always()')
        self.assertIn('[ -f meetup_events.json ]', final['run'])

    def test_osm_place_windows_refresh_changed_rows(self):
        for name in ('osm-food', 'osm-secondhand'):
            workflow = yaml.safe_load(
                (ROOT / f'.github/workflows/{name}.yml').read_text(encoding='utf-8'))
            steps = workflow['jobs']['pull']['steps']
            pull = next(s for s in steps if s.get('name', '').startswith('Pull '))['run']
            sync = next(s for s in steps if s.get('name') == 'Sync to Supabase')['run']
            command = next(line.strip() for line in sync.splitlines()
                           if line.strip().startswith('python mapsee_supabase_sync.py'))
            self.assertIn("full_refresh == 'true' && '--ignore-cursor'", pull, name)
            self.assertIn('--skip-unchanged', command, name)
            self.assertNotIn('--only-new', command, name)

    def test_audit_commit_counts_what_the_audit_probed_not_the_ledger(self):
        # The ledger's `fail` count includes every discovery candidate that ever
        # failed verification and was never configured: on 2026-10-11 the
        # commit read "42772/48760 feeds dead" for an audit that flipped 175
        # configured feeds to fail. The message must come from audit.log.
        workflow = yaml.safe_load((ROOT / '.github/workflows/source-health.yml').read_text(encoding='utf-8'))
        commit = next(s['run'] for s in workflow['jobs']['audit']['steps']
                      if 'Weekly catalog audit:' in (s.get('run') or ''))
        dead = next(l for l in commit.splitlines() if l.strip().startswith('DEAD='))
        total = next(l for l in commit.splitlines() if l.strip().startswith('TOTAL='))
        self.assertIn('entries are BROKEN', dead)
        self.assertIn('audit.log', total)
        self.assertNotIn('curation_ledger.json', dead + total)

    def test_dedupe_is_its_own_red_job_behind_its_switch_before_indexnow(self):
        # The collapser was a continue-on-error step inside indexnow: 12m12s,
        # its timeout, on most runs 2026-09-22..10-07, and green on all 25 runs
        # (09-19..10-12) that annotated exit 1. It hides production rows, so it
        # writes only when vars.DEDUPE_APPLY is 'true'. It runs after BOTH
        # schedules: the Mon/Thu one syncs SeatGeek and Eventbrite.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['dedupe']
        self.assertNotIn('continue-on-error', job)
        self.assertFalse(any('continue-on-error' in s for s in job['steps']))
        self.assertIn('dedupe', workflow['jobs']['indexnow']['needs'])
        self.assertEqual(workflow['jobs']['indexnow']['if'], 'always()')
        self.assertTrue(job['if'].startswith('${{ always() &&'))
        for cron in (c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']):
            self.assertIn(f"github.event.schedule == '{cron}'", job['if'])
        self.assertNotIn('extra_only', job['if'])
        self.assertEqual(set(job['needs']), set(workflow['jobs']['indexnow']['needs']) - {'dedupe'})
        run = next(s['run'] for s in job['steps'] if 'mapsee_dedupe_events.py' in (s.get('run') or ''))
        self.assertIn('set -o pipefail', run)
        # EVERY call's budget, added up, in every position of the switch: with
        # only the first --max-seconds read (300), 'bounded' spent 300 + 1200 s,
        # the whole 25 minutes, before checkout, pip and the test step.
        if shutil.which('bash'):
            for value in ('', 'bounded', 'true'):
                code, calls = self._dedupe_calls(run, {'DEDUPE_APPLY': value})
                total = sum(int(c[c.index('--max-seconds') + 1]) for c in calls)
                self.assertTrue(calls and all('--max-seconds' in c for c in calls), value)
                self.assertLessEqual(total / 60 + 5, job['timeout-minutes'], (value, total))
        for name, other in workflow['jobs'].items():
            if name != 'dedupe':
                for step in other.get('steps', []):
                    self.assertNotIn('mapsee_dedupe_events.py', step.get('run') or '', name)
        manual = yaml.safe_load((ROOT / '.github/workflows/dedupe-events.yml').read_text(encoding='utf-8'))
        self.assertEqual(set(manual.get('on', manual.get(True))), {'workflow_dispatch'})
        mrun = next(s['run'] for s in manual['jobs']['dedupe']['steps'] if 'mapsee_dedupe_events.py' in (s.get('run') or ''))
        self.assertNotIn('${{', mrun)
        self.assertEqual(manual['concurrency']['group'], job['concurrency']['group'])
        for seconds in re.findall(r'--max-seconds (\d+)', mrun):
            self.assertLessEqual(int(seconds) / 60 + 5, manual['jobs']['dedupe']['timeout-minutes'])

    @staticmethod
    def _dedupe_calls(run, env):
        """Run a workflow step's shell with the script replaced by an echo:
        -> (exit code, [the argument list of each call])."""
        script = run.replace('python -u mapsee_dedupe_events.py', 'echo CALL')
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], cwd=tmp, capture_output=True, text=True,
                                 env=dict(os.environ, **env))
        calls = [line.split()[1:] for line in out.stdout.splitlines() if line.startswith('CALL')]
        return out.returncode, calls

    @unittest.skipUnless(shutil.which('bash'), 'needs bash')
    def test_dedupe_switch_has_three_positions_and_bounded_never_arms_the_nightly_hide(self):
        # Rollout step 5 is a BOUNDED first write by hand. With one switch
        # ('true') for both, that write also armed every scheduled run (9 a
        # week) to apply 21 days ahead and 60 back. 'bounded' separates them.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        run = next(s['run'] for s in workflow['jobs']['dedupe']['steps']
                   if 'mapsee_dedupe_events.py' in (s.get('run') or ''))
        for value in ('', 'false', 'True', 'yes', 'bounded', 'true'):
            code, calls = self._dedupe_calls(run, {'DEDUPE_APPLY': value})
            self.assertEqual(code, 0, value)
            full = [c for c in calls if '--days' in c and c[c.index('--days') + 1] != '0']
            self.assertEqual(len(full), 1, value)
            self.assertEqual('--apply' in full[0], value == 'true', value)
            kept = [c for c in calls if c not in full]
            if value == 'bounded':
                # it keeps what was hidden (categories, no-room un-hides) and hides nothing new
                self.assertEqual(kept, [['--apply', '--days', '0', '--lookback', '0', '--max-seconds', '240']])
            else:
                self.assertEqual(kept, [], value)
        manual = yaml.safe_load((ROOT / '.github/workflows/dedupe-events.yml').read_text(encoding='utf-8'))
        mrun = next(s['run'] for s in manual['jobs']['dedupe']['steps'] if 'mapsee_dedupe_events.py' in (s.get('run') or ''))
        # Each input on its own, and fail closed: a number bash cannot compare
        # (2^63) or an empty field used to fall through to the bounded apply.
        cases = [('', '3', '3', False), ('false', '3', '3', False), ('bounded', '3', '3', True),
                 ('bounded', '7', '7', True), ('bounded', '8', '3', False), ('bounded', '3', '21', False),
                 ('bounded', '9223372036854775808', '3', False), ('bounded', '3', '99999999999999999999', False),
                 ('bounded', '', '3', False), ('bounded', '3', '', False), ('bounded', '-1', '3', False),
                 ('bounded', '3 ', '3', False), ('bounded', '007', '3', True), ('true', '21', '60', True),
                 ('true', '121', '60', False), ('true', '21', '9223372036854775808', False), ('true', '', '60', False)]
        for value, days, lookback, allowed in cases:
            code, calls = self._dedupe_calls(mrun, {'APPLY_SWITCH': value, 'MODE': 'apply', 'DAYS': days,
                                                    'LOOKBACK': lookback, 'STAMP': ''})
            self.assertEqual((code == 0, bool(calls)), (allowed, allowed), (value, days, lookback))
            if allowed:
                self.assertEqual(calls[0][:5], ['--apply', '--days', days, '--lookback', lookback])
        code, calls = self._dedupe_calls(mrun, {'APPLY_SWITCH': '', 'MODE': 'unhide', 'DAYS': '21', 'LOOKBACK': '60',
                                                'STAMP': '2026-10-14T06:41:07+00:00'})
        self.assertEqual((code, calls[0][:3]), (0, ['--unhide', '--stamp', '2026-10-14T06:41:07+00:00']))


if __name__ == '__main__':
    unittest.main()
