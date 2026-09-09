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
            'civic': {'opendata', 'tribe', 'gancio', 'mobilizon', 'mapasculturais', 'moshtix', 'ods', 'ckan'},
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


if __name__ == '__main__':
    unittest.main()
