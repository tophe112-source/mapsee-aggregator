"""Offline contracts for the automation that can otherwise fail hours later."""
from pathlib import Path
import re
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
                      'venuepilot', 'dice_venue', 'rolodex', 'pioneersquare', 'seattlecenter', 'slu'},
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


if __name__ == '__main__':
    unittest.main()
