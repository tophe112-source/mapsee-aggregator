"""Offline contracts for the automation that can otherwise fail hours later."""
from pathlib import Path
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
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
                      'moshtix', 'ods', 'ckan', 'repaircafe_ch'},
            'local': {'markets', 'parkrun', 'fairs', 'programs', 'jsonld', 'squarespace',
                      'mylisting', 'luma', 'restaurants', 'seoul', 'affiliates', 'ubereats',
                      'venuepilot', 'dice_venue', 'rolodex', 'pioneersquare', 'seattlecenter', 'slu',
                      'ava', 'rallybound', 'luminate', 'recitals', 'kct'},
            'rec': {'toronto_rec', 'linkedevents', 'perfectmind', 'drupal_fullcalendar', 'revize',
                    'glenecho', 'montreal_loisirs', 'phl_parks', 'barcelona', 'lcsd', 'madrid',
                    'facility_hours', 'natuurwerkdag', 'galaxydigital'},
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

    @staticmethod
    def _gh_expr(expr, context):
        """A GitHub `${{ }}` boolean/string expression, evaluated as Python.
        Enough for `a != 'x' && b != 'y' && 'flag' || ''`: an unset context
        key is null, which is unequal to any string, as in Actions."""
        parts = re.split(r"('[^']*')", expr)
        for i, part in enumerate(parts):
            if not part.startswith("'"):
                part = part.replace('&&', ' and ').replace('||', ' or ')
                parts[i] = re.sub(r"[A-Za-z_][\w-]*(?:\.[\w-]+)+",
                                  lambda m: repr(context.get(m[0])), part)
        return eval(''.join(parts), {'__builtins__': {}})   # noqa: S307 - our own workflow text

    @unittest.skipUnless(shutil.which('bash'), 'needs bash')
    def test_osm_amenities_holds_the_fill_unless_lifted_and_pulls_one_area_per_job(self):
        # 2026-10-27: the first full turn of all 260 hubs inserted 465,774 rows
        # in five Sundays and 94 dense hubs still held 415,471 candidates. The
        # hold is the DEFAULT and lifting it is one variable or one input.
        wf = yaml.safe_load((ROOT / '.github/workflows/osm-amenities.yml').read_text(encoding='utf-8'))
        fill = wf.get('on', wf.get(True))['workflow_dispatch']['inputs']['fill']
        self.assertEqual((fill['type'], fill['default']), ('boolean', False))
        pull = next(s for s in wf['jobs']['pull']['steps'] if str(s.get('name', '')).startswith('Pull '))
        exprs = [e for e in re.findall(r'\$\{\{\s*(.*?)\s*\}\}', pull['run']) if '--frontier' in e]
        self.assertEqual(len(exprs), 1, exprs)
        held = '--frontier osm_amenity_frontier.json'
        for context, want in (({}, held),                                          # schedule
                              ({'github.event.inputs.fill': 'false'}, held),       # a dispatch
                              ({'vars.OSM_AMENITY_FILL': 'on'}, ''),               # the lift
                              ({'vars.OSM_AMENITY_FILL': 'off'}, held),
                              ({'github.event.inputs.fill': 'true'}, '')):         # one run
            self.assertEqual(self._gh_expr(exprs[0], context), want, context)
        self.assertTrue((ROOT / 'osm_amenity_frontier.json').is_file())
        # The plan's `only` is EXACT, as the ingest's --only is: a substring
        # sent only=Reno to Grenoble as well. Run the step's own script.
        pick = next(s for s in wf['jobs']['plan']['steps'] if s.get('id') == 'pick')
        with tempfile.TemporaryDirectory() as tmp:
            for only, want in (('reno', 0), ('Portland', 0), ('Gren', 1)):
                out_file = Path(tmp, f'out-{only}')
                env = dict(os.environ, ONLY=only, SHARD='', GITHUB_OUTPUT=str(out_file))
                out = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', pick['run']],
                                     cwd=ROOT, capture_output=True, text=True, env=env)
                self.assertEqual(out.returncode != 0, bool(want), (only, out.stderr[-300:]))
                if not want:
                    areas = json.loads(out_file.read_text().split('areas=', 1)[1])
                    self.assertEqual(areas, ['Reno'] if only == 'reno' else ['Portland'])

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
        self.assertIn('[ -f meetup_global_events.json ]', final['run'])
        self.assertIn('[ -f meetup_events.json ]', final['run'])

    def test_meetup_legs_keep_their_own_stores_and_the_final_sync_backs_up_the_us_one(self):
        # One shared store made the always() final sync re-read 51,996 ids in
        # 116.9 s and re-send 29,890 Census addresses on 2026-10-23, ~24.7k of
        # them US rows us_sync had already written. The international sweep has
        # its own store; the final syncs the US one again only when us_sync did
        # not succeed (a failed us_sync also skips the sweep).
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['meetup']['steps']
        us = next(s for s in steps if s.get('name') == 'Sweep Meetup across the US metros')
        us_sync = next(s for s in steps if s.get('id') == 'us_sync')
        intl = next(s for s in steps if s.get('name') == 'Sweep Meetup across the international metros')
        final = next(s for s in steps if s.get('name', '').startswith('Sync Meetup events to Supabase'))
        self.assertLess(steps.index(us), steps.index(us_sync))
        self.assertLess(steps.index(us_sync), steps.index(intl))
        self.assertLess(steps.index(intl), steps.index(final))
        self.assertIn('--store meetup_events.json', us['run'])
        self.assertIn('--store meetup_events.json --skip-unchanged $ONLY_NEW', us_sync['run'])
        self.assertIn('--store meetup_global_events.json', intl['run'])
        self.assertNotIn('meetup_events.json', intl['run'])
        for step in (us_sync, final):
            self.assertIn('[ "$(date -u +%u)" = "3" ] && ONLY_NEW=""', step['run'])
            self.assertIn('[ "${{ github.event.inputs.full_refresh }}" = "true" ] && ONLY_NEW=""', step['run'])
        self.assertIn('steps.us_sync.outcome', final['run'])
        if not shutil.which('bash'):
            return
        run = final['run']
        both = ('meetup_events.json', 'meetup_global_events.json')
        cases = [  # (us_sync outcome, stores on disk, store whose sync fails) -> stores synced, step ok
            ('success', both, None, ['meetup_global_events.json'], True),
            ('success', ('meetup_events.json',), None, [], True),
            ('failure', ('meetup_events.json',), None, ['meetup_events.json'], True),
            ('', ('meetup_events.json',), None, ['meetup_events.json'], True),
            ('cancelled', both, None, list(both), True),
            ('failure', both, 'meetup_events.json', list(both), False),
            ('success', both, 'meetup_global_events.json', ['meetup_global_events.json'], False),
            ('failure', (), None, [], True),
        ]
        for outcome, present, failing, synced, ok in cases:
            for refresh in ('true', 'false'):
                code, calls, _, _ = self._sync_calls(
                    run, {'steps.us_sync.outcome': outcome, 'github.event.inputs.full_refresh': refresh},
                    {name: '{"events": []}' for name in present}, failing)
                case = (outcome, present, failing, refresh)
                self.assertEqual([c[c.index('--store') + 1] for c in calls], synced, case)
                self.assertEqual(code == 0, ok, case)
                self.assertTrue(all('--skip-unchanged' in c for c in calls), case)
                if refresh == 'true':
                    self.assertFalse(any('--only-new' in c for c in calls), case)

    def test_meetup_final_sync_writes_the_us_copy_of_a_border_event(self):
        # Three international circles overlap a US one (Tijuana 15.6 mi from San
        # Diego; Victoria and Vancouver inside Seattle's 100 mi). One event both
        # legs find has one fingerprint and can carry a different category and
        # ends_at in each store (each process keeps its first keyword's), and
        # needs_detail_sync keeps every stored Meetup row on --only-new days: two
        # copies would both be written daily. The US copy wins, as it did in the
        # shared store; the international store keeps its own tombstones.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['meetup']['steps']
        run = next(s for s in steps if s.get('name', '').startswith('Sync Meetup events to Supabase'))['run']
        if not shutil.which('bash'):
            return
        ev = lambda fp, cat: {'fingerprint': fp, 'category': cat, 'sources': [{'source': 'meetup', 'source_id': fp}]}
        us = json.dumps({'_meta': {'version': 1, 'count': 2},
                         'events': [ev('a', 'community'), ev('border', 'community')],
                         'tombstones': [{'fingerprint': 'called-off', 'source': 'meetup', 'reason': 'status:CANCELLED'}]})
        intl = json.dumps({'_meta': {'version': 1, 'count': 4},
                           'events': [ev('border', 'party'), ev('called-off', 'party'), ev('tj', 'party'),
                                      ev('mx', 'community')],
                           'tombstones': [{'fingerprint': 'a', 'source': 'meetup', 'reason': 'status:CANCELLED'}],
                           'seen': ['unwritten']})
        fps = lambda store: [r['fingerprint'] for r in store['events']]
        for outcome, synced in (('success', ['meetup_global_events.json']),
                                ('failure', ['meetup_events.json', 'meetup_global_events.json'])):
            exprs = {'steps.us_sync.outcome': outcome, 'github.event.inputs.full_refresh': 'false'}
            code, calls, snaps, out = self._sync_calls(
                run, exprs, {'meetup_events.json': us, 'meetup_global_events.json': intl}, None)
            self.assertEqual(code, 0, outcome)
            self.assertEqual([c[c.index('--store') + 1] for c in calls], synced, outcome)
            if outcome == 'failure':                 # the US copy is written first, whole
                self.assertEqual(fps(snaps[0]), ['a', 'border'])
            left = snaps[-1]
            self.assertEqual(fps(left), ['tj', 'mx'], outcome)
            self.assertEqual(left['_meta']['count'], 2, outcome)
            self.assertEqual(left['tombstones'], json.loads(intl)['tombstones'], outcome)
            self.assertEqual(left['seen'], ['unwritten'], outcome)
            self.assertIn('Border overlap: 2 of 4 international events', out, outcome)
        # No US store: nothing to defer to, the international store syncs whole.
        code, calls, snaps, _ = self._sync_calls(
            run, {'steps.us_sync.outcome': 'success', 'github.event.inputs.full_refresh': 'false'},
            {'meetup_global_events.json': intl}, None)
        self.assertEqual((code, fps(snaps[-1])), (0, ['border', 'called-off', 'tj', 'mx']))
        # A filter that cannot read the US store leaves the international one
        # whole, still syncs it, and fails the step instead of hiding it.
        code, calls, snaps, out = self._sync_calls(
            run, {'steps.us_sync.outcome': 'success', 'github.event.inputs.full_refresh': 'false'},
            {'meetup_events.json': '{"events": [', 'meetup_global_events.json': intl}, None)
        self.assertEqual([c[c.index('--store') + 1] for c in calls], ['meetup_global_events.json'])
        self.assertEqual(fps(snaps[-1]), ['border', 'called-off', 'tj', 'mx'])
        self.assertNotEqual(code, 0)
        self.assertIn('::warning::', out)

    def test_meetup_held_categories_keep_their_own_cache_pair_and_never_stop_a_sync(self):
        # Category was blamed on 4,146-4,818 UPDATEs per Meetup leg per day
        # (10-22..10-26): the store is rebuilt every run and the first keyword
        # to find an event moves. mapsee_meetup_category_cache holds the first
        # guess. Its file gets its OWN cache pair: actions/cache hashes the path
        # list into an entry's version, so a third path in the Census/Spotify
        # pair would orphan every sync-answers-meetup-* entry and send the US
        # leg's ~13.5k addresses to Census cold (12,825 answered from it on
        # 10-26). One hold per leg, between its sweep and its sync, never inside
        # a sync step, and neither can fail the job.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['meetup']['steps']
        caches = [s for s in steps if str(s.get('uses', '')).startswith('actions/cache/')]
        answers = [s for s in caches if s['with']['key'].startswith('sync-answers-')]
        held = [s for s in caches if 'meetup_category_cache.json' in s['with']['path']]
        self.assertEqual(len(answers), 2)
        for step in answers:                      # byte-identical to before the held pair existed
            self.assertEqual(step['with']['path'], 'census_cache.json\nspotify_cache.json\n', step['name'])
            self.assertEqual(step['with']['key'],
                             'sync-answers-meetup-${{ github.run_id }}-${{ github.run_attempt }}')
        self.assertEqual(answers[0]['with']['restore-keys'], 'sync-answers-meetup-\n')
        self.assertEqual([s['uses'] for s in held], ['actions/cache/restore@v4', 'actions/cache/save@v4'])
        for step in held:
            self.assertEqual(step['with']['path'], 'meetup_category_cache.json', step['name'])
            self.assertEqual(step['with']['key'],
                             'meetup-categories-${{ github.run_id }}-${{ github.run_attempt }}')
            self.assertFalse(step['with']['key'].startswith('sync-answers-'))
        self.assertEqual(held[0]['with']['restore-keys'], 'meetup-categories-\n')
        self.assertEqual(held[1].get('if'), 'always()')
        self.assertEqual(len(caches), 4)

        def at(pred):
            found = [i for i, s in enumerate(steps) if pred(s)]
            self.assertEqual(len(found), 1, found)
            return found[0]
        us = at(lambda s: s.get('name') == 'Sweep Meetup across the US metros')
        us_hold = at(lambda s: s.get('id') == 'us_hold')
        us_sync = at(lambda s: s.get('id') == 'us_sync')
        intl = at(lambda s: s.get('name') == 'Sweep Meetup across the international metros')
        intl_hold = at(lambda s: s.get('id') == 'intl_hold')
        final = at(lambda s: s.get('name', '').startswith('Sync Meetup events to Supabase'))
        restore, save = steps.index(held[0]), steps.index(held[1])
        self.assertEqual(sorted([restore, us, us_hold, us_sync, intl, intl_hold, final, save]),
                         [restore, us, us_hold, us_sync, intl, intl_hold, final, save])
        self.assertEqual(us_hold, us + 1)
        self.assertEqual(intl_hold, intl + 1)
        self.assertEqual(steps[intl_hold].get('if'), 'always()')
        for i, store in ((us_hold, 'meetup_events.json'), (intl_hold, 'meetup_global_events.json')):
            step = steps[i]
            self.assertIs(step.get('continue-on-error'), True, step['name'])
            self.assertEqual(step['env'], {'MEETUP_STICKY_CATEGORIES': '${{ vars.MEETUP_STICKY_CATEGORIES }}'})
            self.assertIn(f'python mapsee_meetup_category_cache.py --store {store} '
                          '--cache meetup_category_cache.json $MODE', step['run'])
        # Nothing but the holds runs the module; no sync step gained a call.
        for i, step in enumerate(steps):
            if i not in (us_hold, intl_hold):
                self.assertNotIn('mapsee_meetup_category_cache', step.get('run', ''), step.get('name'))
        self.assertEqual(sorted(re.findall(r'\bpython\s+(\S+)', steps[final]['run'])),
                         ['-', 'mapsee_supabase_sync.py', 'mapsee_supabase_sync.py'])
        self.assertEqual(re.findall(r'\bpython\s+(\S+)', steps[us_sync]['run']), ['mapsee_supabase_sync.py'])
        if not shutil.which('bash'):
            return
        # REPORT ONLY unless the repository variable is exactly 'true'.
        for value, report in (('', True), ('false', True), ('TRUE', True), ('yes', True), ('true', False)):
            for i, store in ((us_hold, 'meetup_events.json'), (intl_hold, 'meetup_global_events.json')):
                script = 'python() { echo CALL "$@"; }\n' + steps[i]['run']
                out = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], capture_output=True, text=True,
                                     env=dict(os.environ, MEETUP_STICKY_CATEGORIES=value))
                self.assertEqual(out.returncode, 0, (value, out.stderr))
                call = [line.split()[1:] for line in out.stdout.splitlines() if line.startswith('CALL')]
                self.assertEqual(len(call), 1, value)
                self.assertEqual('--report-only' in call[0], report, value)
                self.assertEqual(call[0][:5], ['mapsee_meetup_category_cache.py', '--store', store,
                                               '--cache', 'meetup_category_cache.json'])

    @staticmethod
    def _sync_calls(run, exprs, stores, failing):
        """Run a sync step's shell as Actions would (bash -eo pipefail), with
        its ${{ }} expressions filled from `exprs`, `stores` ({name: text}) on
        disk, `python` this interpreter, and the sync replaced by an echo that
        fails for `failing`: -> (exit code, [the argument list of each call],
        [the store each call was given, parsed, or None], stdout)."""
        script = re.sub(r'\$\{\{\s*(.+?)\s*\}\}', lambda m: exprs[m[1]], run)
        script = ('python() { "$PYTHON_EXE" "$@"; }\n'
                  'fake_sync() { echo CALL "$@"; printf \'SNAP %s\\n\' "$(tr -d \'\\n\' < "$2")"; '
                  '[ "$2" != "$FAILING" ]; }\n'
                  + script.replace('python mapsee_supabase_sync.py', 'fake_sync'))
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in stores.items():
                Path(tmp, name).write_text(text, encoding='utf-8')
            out = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], cwd=tmp, capture_output=True,
                                 text=True, env=dict(os.environ, FAILING=failing or '', PYTHON_EXE=sys.executable))
        calls = [line.split()[1:] for line in out.stdout.splitlines() if line.startswith('CALL')]

        def parsed(text):
            try:
                return json.loads(text)
            except ValueError:
                return None
        snaps = [parsed(line[5:]) for line in out.stdout.splitlines() if line.startswith('SNAP ')]
        return out.returncode, calls, snaps, out.stdout

    def test_ics_skips_its_final_sync_after_a_checkpoint_that_succeeded(self):
        # Nothing imports after the checkpoint in the ics group, so its final
        # sync wrote 0 rows after a 239.2 s state read of 130,979 ids on
        # 2026-10-23 (163.5 s, 0 rows on 10-22). It still runs when the
        # checkpoint did not succeed; under continue-on-error only `.outcome`
        # says so (the conclusion is always success).
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['feeds']['steps']
        index = {s['id']: i for i, s in enumerate(steps) if 'id' in s}
        sync, checkpoint = steps[index['sync']], steps[index['checkpoint']]
        self.assertLess(index['ingest_ics'], index['checkpoint'])
        self.assertLess(index['checkpoint'], index['sync'])
        self.assertEqual(sync.get('if'), "matrix.group != 'ics' || steps.checkpoint.outcome != 'success'")
        self.assertNotIn('conclusion', sync.get('if') or '')
        self.assertTrue(sync['continue-on-error'])
        self.assertTrue(checkpoint['continue-on-error'])
        self.assertIn('--store feeds_events.json', checkpoint['run'])
        self.assertIn('--store feeds_events.json', sync['run'])
        # Every step after the checkpoint that can run in the ics group (no
        # guard, always(), or any guard other than one naming another group)
        # imports nothing and writes no store but the one the checkpoint synced.
        for step in steps[index['checkpoint'] + 1:]:
            only = re.fullmatch(r"matrix.group == '(\w+)'", step.get('if') or '')
            if only and only[1] != 'ics':
                continue
            run = step.get('run') or ''
            name = step.get('name') or step.get('uses')
            self.assertIsNone(re.search(r'mapsee_ingest_\w+\.py', run), name)
            if step is not sync:
                self.assertNotIn('--store', run, name)

    def test_feed_logs_are_unbuffered(self):
        # Block-buffered, the ics step printed its first line ~33 minutes in
        # (2026-10-23): no feed's cost could be read from the log.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        self.assertEqual(workflow['jobs']['feeds']['env'].get('PYTHONUNBUFFERED'), '1')

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
        # writes only when vars.DEDUPE_APPLY is 'true'. In 'bounded' and 'true'
        # it runs after BOTH schedules: the Mon/Thu one syncs SeatGeek and
        # Eventbrite. Unset, the schedules SKIP it: 2026-10-20..10-26 it
        # reported 9 times (9,428 GETs, 102 statement timeouts, 2,489,674 rows,
        # 6,138 s) a count that moved 8,110-9,065 in 12 days, and was the only
        # failed job in 8 of the 9 runs. A dispatch still runs it.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['dedupe']
        self.assertNotIn('continue-on-error', job)
        self.assertFalse(any('continue-on-error' in s for s in job['steps']))
        self.assertIn('dedupe', workflow['jobs']['indexnow']['needs'])
        self.assertEqual(workflow['jobs']['indexnow']['if'], 'always()')
        self.assertTrue(job['if'].startswith('${{ always() &&'))
        crons = [c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']]
        for cron in crons:
            self.assertIn(f"github.event.schedule == '{cron}'", job['if'])
        self.assertNotIn('extra_only', job['if'])
        # The switch, and nothing else from vars, decides a scheduled run.
        self.assertEqual(sorted(re.findall(r"vars\.[\w.]+\s*==\s*'[^']*'", job['if'])),
                         ["vars.DEDUPE_APPLY == 'bounded'", "vars.DEDUPE_APPLY == 'true'"])
        self.assertEqual(len(re.findall(r'vars\.', job['if'])), 2)
        for cron in crons:
            for value in ('', 'false', 'yes', 'bounded', 'true', 'True', 'BOUNDED'):
                ran = self._gh_if(job['if'], {'github.event_name': 'schedule', 'github.event.schedule': cron,
                                              'vars.DEDUPE_APPLY': value})
                self.assertEqual(ran, value.lower() in ('bounded', 'true'), (cron, value))
        self.assertFalse(self._gh_if(job['if'], {'github.event_name': 'schedule',
                                                 'github.event.schedule': '0 12 * * *', 'vars.DEDUPE_APPLY': 'true'}))
        for value in ('', 'bounded', 'true'):
            for group, runs in (('', True), ('all', True), ('ics', False)):
                ran = self._gh_if(job['if'], {'github.event_name': 'workflow_dispatch', 'inputs.feed_group': group,
                                              'vars.DEDUPE_APPLY': value})
                self.assertEqual(ran, runs, (value, group))
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

    def test_link_series_waits_for_its_switch_and_stays_tolerated(self):
        # Its listing died on page 1 at the statement timeout (57014) on 7 of 8
        # days 2026-10-19..10-26 (a ReadTimeout on the 8th) and linked nothing,
        # green every time. Off unless vars.LINK_SERIES is 'true', on a schedule
        # and on a dispatch, until fetch_events is a keyset walk; and when it is
        # on again it is still a step nobody's run may fail on.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        found = [(name, s) for name, job in workflow['jobs'].items() for s in job.get('steps', [])
                 if 'mapsee_link_series.py' in (s.get('run') or '')]
        self.assertEqual([name for name, _ in found], ['indexnow'])
        step = found[0][1]
        self.assertIs(step.get('continue-on-error'), True)
        self.assertEqual(len(re.findall(r'vars\.', step['if'])), 1)
        crons = [c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']]
        daily = next(c for c in crons if f"github.event.schedule == '{c}'" in step['if'])
        for value in ('', 'false', 'yes', 'true', 'True'):
            on = value.lower() == 'true'
            for cron in crons:
                ran = self._gh_if(step['if'], {'github.event_name': 'schedule', 'github.event.schedule': cron,
                                               'vars.LINK_SERIES': value})
                self.assertEqual(ran, on and cron == daily, (cron, value))
            for group, runs in (('', True), ('all', True), ('ics', False)):
                ran = self._gh_if(step['if'], {'github.event_name': 'workflow_dispatch', 'inputs.feed_group': group,
                                               'vars.LINK_SERIES': value})
                self.assertEqual(ran, on and runs, (group, value))
            self.assertFalse(self._gh_if(step['if'], {'github.event_name': 'workflow_dispatch',
                                                      'inputs.extra_only': 'true', 'vars.LINK_SERIES': value}))

    def test_indexnow_extra_run_announces_its_events_not_the_landing_pages(self):
        # The Mon/Thu run's IndexNow job started 49-74 min after the daily
        # run's on 10-19, 10-22 and 10-26 and sent the same 3,739 /c/ URLs again
        # (landing_urls() is each host's whole sitemap-pages.xml). That cron,
        # read from on.schedule, passes --no-pages; the daily cron and a
        # dispatch do not, and the job still runs `if: always()` for its /e/ rows.
        workflow = yaml.safe_load((ROOT / '.github/workflows/aggregate-events.yml').read_text(encoding='utf-8'))
        crons = [c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']]
        extra_if = workflow['jobs']['extra_sources']['if']
        extra = [c for c in crons if f"github.event.schedule == '{c}'" in extra_if]
        self.assertEqual(len(extra), 1, extra_if)
        extra = extra[0]
        job = workflow['jobs']['indexnow']
        self.assertEqual(job['if'], 'always()')
        self.assertIn('extra_sources', job['needs'])
        submit = [s for s in job['steps'] if 'mapsee_indexnow.py' in (s.get('run') or '')]
        self.assertEqual(len(submit), 1)
        self.assertNotIn('if', submit[0])
        run = submit[0]['run']
        self.assertIn(f"github.event.schedule == '{extra}'", run)

        def argv(context):
            line = re.sub(r'\$\{\{(.*?)\}\}', lambda m: self._gh_if(m[1], context, value=True), run)
            return line.split()
        for cron in crons:
            got = argv({'github.event_name': 'schedule', 'github.event.schedule': cron})
            self.assertEqual(got[:2], ['python', 'mapsee_indexnow.py'])
            self.assertEqual(got[got.index('--since-hours') + 1], '26', cron)
            self.assertEqual('--no-pages' in got, cron == extra, (cron, got))
        got = argv({'github.event_name': 'workflow_dispatch'})
        self.assertNotIn('--no-pages', got)
        self.assertEqual(got[got.index('--since-hours') + 1], '26')

    @staticmethod
    def _gh_if(expr, context, value=False):
        """Evaluate an `if:` as Actions would, for the subset these workflows
        write: '...' literals, context names (missing ones are ''), ==, !=,
        !, &&, ||, brackets and always(). Actions compares strings WITHOUT
        regard to case ('True' == 'true'), where a shell `case` does not.
        value=True returns what a `${{ }}` inside a `run:` renders instead:
        && and || hand back an operand, so `a == 'x' && '--flag' || ''` is
        '--flag' or '', as on Actions."""
        body = re.fullmatch(r'\s*\$\{\{(.*)\}\}\s*', expr, re.S)
        body = body[1] if body else expr

        class S(str):
            def __eq__(self, other):
                return self.lower() == str(other).lower()

            def __ne__(self, other):
                return not self.__eq__(other)
            __hash__ = str.__hash__
        token = re.compile(r"\s*(?:('(?:[^']|'')*')|(always\(\))|(&&|\|\||==|!=|!|\(|\))|([A-Za-z_][\w.-]*))")
        src, pos = [], 0
        while body[pos:].strip():
            m = token.match(body, pos)
            if not m:
                raise ValueError(f'cannot read {body[pos:]!r}')
            lit, always, op, name = m.groups()
            if lit:
                src.append(f'S({lit[1:-1].replace(chr(39) * 2, chr(39))!r})')
            elif always:
                src.append('True')
            elif op:
                src.append({'&&': ' and ', '||': ' or ', '!': ' not '}.get(op, op))
            else:
                src.append(f'S({str(context.get(name, ""))!r})')
            pos = m.end()
        out = eval(''.join(src), {'S': S})
        return (str(out) if out else '') if value else bool(out)

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
            # 60 back only where it hides: the census's only lookback hides
            # started 21-29 days before. Reporting, 6 back drops the 10-18/19
            # band ../mapsee 0239 froze its pins into (49-51 pages, ~10 of 13
            # statement timeouts and the run's only OWED slice: 300 GETs and
            # 131-148 s a run) for 39-43 of ~8,000 report clusters.
            lookback = int(full[0][full[0].index('--lookback') + 1])
            if value == 'true':
                self.assertGreaterEqual(lookback, 30, value)
            else:
                self.assertLessEqual(lookback, 7, value)
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

    @staticmethod
    def _prune_calls(context, dow):
        """Run prune-cancelled's hide step with python and `date -u +%u`
        stubbed: -> (exit code, [argv of each mapsee_prune_cancelled call]).
        `context` maps an expression (`github.event.schedule`, `inputs.days`,
        `inputs.dry_run`) to its value, '' when absent as on Actions; `dow` is
        what `date -u +%u` answers."""
        workflow = yaml.safe_load((ROOT / '.github/workflows/prune-cancelled.yml').read_text(encoding='utf-8'))
        step = next(s for s in workflow['jobs']['prune']['steps']
                    if 'mapsee_prune_cancelled.py' in (s.get('run') or ''))

        def sub(text):
            return re.sub(r'\$\{\{\s*(.*?)\s*\}\}', lambda m: str(context.get(m[1], '')), str(text))
        real_date = shutil.which('date')
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp, 'bin')
            stubs.mkdir()
            (stubs / 'python').write_text('#!/bin/sh\necho "CALL $*"\n')
            (stubs / 'date').write_text('#!/bin/sh\n'
                                        f'if [ "$*" = "-u +%u" ]; then echo {dow}; else exec {real_date} "$@"; fi\n')
            for f in stubs.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")
            env.update({k: sub(v) for k, v in (step.get('env') or {}).items()})
            out = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', sub(step['run'])],
                                 cwd=tmp, capture_output=True, text=True, env=env)
        calls = [line.split()[1:] for line in out.stdout.splitlines() if line.startswith('CALL')]
        return out.returncode, calls

    @unittest.skipUnless(shutil.which('bash'), 'needs bash')
    def test_prune_cancelled_mornings_are_near_and_only_sunday_or_a_blank_dispatch_is_full(self):
        # 7 of 7 morning full sweeps 2026-10-20..10-26 capped "through" their
        # own run date: they read 662,579-686,946 rows each (4.72 M a week) to
        # probe the URLs a --days 2 run picks. The far rows still carried at
        # least 143 of the week's 360 hides (a listing found off hides every
        # row of its URL it collected), so Sunday morning keeps the 120-day
        # sweep and a dispatch with days blank is one; nothing else reaches it,
        # and a cron with no branch fails instead of falling into it.
        workflow = yaml.safe_load((ROOT / '.github/workflows/prune-cancelled.yml').read_text(encoding='utf-8'))
        job = workflow['jobs']['prune']
        step = next(s for s in job['steps'] if 'mapsee_prune_cancelled.py' in (s.get('run') or ''))
        crons = [c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']]
        self.assertIn('55 7 * * *', crons)
        self.assertIn('55 16 * * *', crons)
        full, budgets = set(), []
        for cron in crons:
            for dow in '1234567':
                code, calls = self._prune_calls({'github.event.schedule': cron}, dow)
                self.assertEqual((code, len(calls)), (0, 1), (cron, dow))
                call = calls[0]
                self.assertIn('--apply', call, (cron, dow))
                budgets.append(int(self._flag(call, '--max-seconds')))
                if '--days' in call:
                    self.assertEqual(self._flag(call, '--days'), '2', (cron, dow))
                else:
                    full.add((cron, dow))
        self.assertEqual(full, {('55 7 * * *', '7')})
        self.assertEqual(self._flag(self._prune_calls({'github.event.schedule': '55 16 * * *'}, '3')[1][0],
                                    '--max-seconds'), '900')
        self.assertEqual(self._flag(self._prune_calls({'github.event.schedule': '55 7 * * *'}, '3')[1][0],
                                    '--max-seconds'), '2100')
        for dow in '17':
            code, calls = self._prune_calls({}, dow)
            self.assertEqual(code, 0, dow)
            self.assertNotIn('--days', calls[0], dow)
            self.assertIn('--apply', calls[0], dow)
            budgets.append(int(self._flag(calls[0], '--max-seconds')))
        code, calls = self._prune_calls({'inputs.days': '5', 'inputs.dry_run': 'true'}, '7')
        self.assertEqual((code, self._flag(calls[0], '--days')), (0, '5'))
        self.assertNotIn('--apply', calls[0])
        code, calls = self._prune_calls({'github.event.schedule': '0 12 * * *'}, '7')
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        # --max-seconds is paired with timeout-minutes: five minutes are left
        # for checkout, setup-python and the writes.
        budgets += [int(b) for b in re.findall(r'BUDGET=(\d+)', step['run'])]
        for budget in budgets:
            self.assertLessEqual(budget, job['timeout-minutes'] * 60 - 300, budget)
        # Block-buffered, "scanned" reached the log only with "probed 50/4000"
        # (121635448161): no collect time could be read from it.
        self.assertEqual(str((step.get('env') or {}).get('PYTHONUNBUFFERED')), '1')

    @staticmethod
    def _curate_discovers(context, dow='1'):
        """Run curate-catalog's discover step with python and date stubbed:
        -> (exit code, [(backend, argv of its `discover` call)] in call order).

        `context` maps an expression (`inputs.backend`, `github.event.schedule`,
        `steps.target.outputs.cats`) to its value; `dow` is what `date -u +%u`
        answers, so a Wednesday can be asked for on any day."""
        workflow = yaml.safe_load((ROOT / '.github/workflows/curate-catalog.yml').read_text(encoding='utf-8'))
        step = next(s for s in workflow['jobs']['curate']['steps'] if s.get('name') == 'Discover, verify and merge')
        # A schedule the tests inject must be one the workflow has, or an
        # edited cron line leaves every case passing on a run that never comes.
        crons = [c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']]
        when = context.get('github.event.schedule')
        assert when is None or when in crons, f"{when!r} is not one of curate-catalog's crons {crons}"

        def expr(m):
            for term in m[1].split('||'):
                term = term.strip()
                value = term.strip("'") if term.startswith("'") else str(context.get(term, ''))
                if value:
                    return value
            return ''

        def sub(text):
            return re.sub(r'\$\{\{\s*(.*?)\s*\}\}', expr, str(text))
        real_date = shutil.which('date')
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp, 'bin')
            stubs.mkdir()
            (stubs / 'python').write_text('#!/bin/sh\necho "CALL $*"\n')
            (stubs / 'date').write_text('#!/bin/sh\n'
                                        f'if [ "$*" = "-u +%u" ]; then echo {dow}; else exec {real_date} "$@"; fi\n')
            for f in stubs.iterdir():
                f.chmod(0o755)
            env = dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                       GITHUB_STEP_SUMMARY=str(Path(tmp, 'summary.md')))
            env.update({k: sub(v) for k, v in (step.get('env') or {}).items()})
            out = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', sub(step['run'])],
                                 cwd=tmp, capture_output=True, text=True, env=env)
        calls = [line.split()[1:] for line in out.stdout.splitlines() if line.startswith('CALL')]
        found = []
        for c in calls:
            if c[1:2] == ['discover']:
                backend = c[2] if len(c) > 2 and not c[2].startswith('-') else 'socrata'
                found.append((backend, c))
        return out.returncode, found

    @staticmethod
    def _flag(argv, name):
        return argv[argv.index(name) + 1] if name in argv else None

    @unittest.skipUnless(shutil.which('bash'), 'needs bash')
    def test_curate_schedules_spend_their_osm_and_ckan_time_where_it_was_measured(self):
        # 2026-10-23, runs 108-124. ckan took 1.8-2.5 min in each of 6 daily
        # runs and proposed 0, so the daily run reads it on Wednesdays only.
        # The 3-metro cap left run 116 about 30 of its 55 minutes, so the
        # daily run defaults to 40 metros and lets the budget decide. The 09:30
        # community walk verified 15.4 an hour against the full sweep's 77, so
        # 09:30 is a second full-sweep pass on the shared cursor.
        daily = {'github.event.schedule': '20 3 * * *'}
        code, calls = self._curate_discovers(daily, dow='1')
        self.assertEqual(code, 0)
        self.assertEqual([b for b, _ in calls], ['socrata', 'mobilizon', 'civic', 'osm'])
        osm = dict(calls)['osm']
        self.assertEqual(self._flag(osm, '--metros'), '40')
        self.assertEqual(self._flag(osm, '--max-minutes'), '55')
        self.assertNotIn('--kinds', osm)
        self.assertNotIn('--workers', osm)
        self.assertEqual(self._flag(dict(calls)['civic'], '--max-minutes'), '10')

        code, calls = self._curate_discovers(daily, dow='3')
        self.assertEqual([b for b, _ in calls], ['socrata', 'ckan', 'mobilizon', 'civic', 'osm'])

        # The gap sweep is the Wednesday 03:50 cron and keeps ckan, pinned.
        gap = {'github.event.schedule': '50 3 * * 3', 'steps.target.outputs.cats': 'running,volunteer'}
        code, calls = self._curate_discovers(gap, dow='3')
        self.assertEqual([b for b, _ in calls], ['socrata', 'ckan', 'mobilizon', 'civic', 'osm'])
        self.assertEqual(self._flag(dict(calls)['ckan'], '--category'), 'running,volunteer')
        self.assertIsNone(self._flag(dict(calls)['osm'], '--metros'))

        code, calls = self._curate_discovers({'github.event.schedule': '30 9 * * *'}, dow='1')
        self.assertEqual([b for b, _ in calls], ['osm'])
        osm = calls[0][1]
        self.assertEqual(self._flag(osm, '--metros'), '40')
        self.assertNotIn('--kinds', osm)
        self.assertNotIn('--category', osm)

        # The community walk's slot outlives the change, so it can be resumed
        # where it was once the rows an older probe wrote have lapsed.
        cursor = json.loads((ROOT / 'curation_cursor.json').read_text(encoding='utf-8'))
        self.assertIn('osm|community_centre,library,social_centre', cursor)

    def test_curate_compares_only_its_own_crons_and_runs_one_at_a_time(self):
        # The 03:20 and 09:30 crons walk one `osm` cursor slot (2026-10-23).
        # Only the group keeps them apart: two at once would probe one metro
        # from two runners, and _merge_cursors lets the later snapshot's `osm`
        # dict win, so a run that advanced two metros is rewound to one.
        workflow = yaml.safe_load((ROOT / '.github/workflows/curate-catalog.yml').read_text(encoding='utf-8'))
        self.assertEqual(workflow['concurrency'], {'group': 'curate-catalog', 'cancel-in-progress': False})
        crons = {c['cron'] for c in workflow.get('on', workflow.get(True))['schedule']}

        def strings(o):
            if isinstance(o, dict):
                for v in o.values():
                    yield from strings(v)
            elif isinstance(o, list):
                for v in o:
                    yield from strings(v)
            elif isinstance(o, str):
                yield o
        said = set()
        for s in strings(workflow['jobs']):
            said.update(re.findall(r'github\.event\.schedule\s*\}\}"\s*=\s*"([^"]+)"', s))
            said.update(re.findall(r"github\.event\.schedule\s*==\s*'([^']+)'", s))
        # Today: '20 3 * * *' (daily), '50 3 * * 3' (gap) and '30 9 * * *' (second pass).
        self.assertEqual(len(said), 3, said)
        self.assertLessEqual(said, crons)

    @unittest.skipUnless(shutil.which('bash'), 'needs bash')
    def test_curate_dispatches_keep_their_defaults_and_osm_workers_reaches_the_walk(self):
        # A dispatch of 'all' on any day reads ckan, and one of ckan reads
        # only ckan; a dispatch's osm keeps OSM_METROS_PER_RUN (no --metros)
        # unless it names osm_metros; osm_kinds is still the pinned walk.
        code, calls = self._curate_discovers({'inputs.backend': 'all'}, dow='1')
        self.assertEqual([b for b, _ in calls], ['socrata', 'ckan', 'mobilizon', 'civic', 'osm'])
        self.assertIsNone(self._flag(dict(calls)['osm'], '--metros'))
        code, calls = self._curate_discovers({'inputs.backend': 'ckan'}, dow='1')
        self.assertEqual([b for b, _ in calls], ['ckan'])
        code, calls = self._curate_discovers({'inputs.backend': 'osm', 'inputs.osm_metros': '5',
                                              'inputs.osm_kinds': 'community_centre,library'})
        osm = calls[0][1]
        self.assertEqual((self._flag(osm, '--metros'), self._flag(osm, '--kinds')),
                         ('5', 'community_centre,library'))
        # osm_workers, for the owner's A/B of 8 against 4: digits only, and
        # nothing else in argv when it is blank or not a number.
        code, calls = self._curate_discovers({'inputs.backend': 'osm', 'inputs.osm_workers': '8'})
        argv = calls[0][1]
        self.assertEqual(self._flag(argv, '--workers'), '8')
        for bad in ('8 --kinds library', 'eight'):
            code, calls = self._curate_discovers({'inputs.backend': 'osm', 'inputs.osm_workers': bad})
            self.assertEqual(code, 0, bad)
            self.assertNotIn('--workers', calls[0][1], bad)
            self.assertNotIn('--kinds', calls[0][1], bad)
        # ...and main() hands it to the walk: the real argv, the real main()
        # and cmd_discover, with the network and the walk itself stubbed.
        import catalog_curate as cc
        seen = []
        stubs = {'_config_keys': lambda: set(), '_load_ledger': lambda: {}, '_session': lambda: None,
                 'curated_categories': lambda _s: ['community'], '_load_cursor': lambda: {},
                 '_save_cursor': lambda _c: None,
                 '_discover_osm': lambda *a, **k: (seen.append(k), ({}, {}))[1]}
        real = {k: getattr(cc, k) for k in stubs}
        try:
            for k, v in stubs.items():
                setattr(cc, k, v)
            daily = dict(self._curate_discovers({'github.event.schedule': '20 3 * * *'})[1])['osm']
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
                    warnings.catch_warnings():
                warnings.simplefilter('ignore', ResourceWarning)   # cmd_discover's json.dump(open(...))
                for call in (argv, daily):
                    out = call.index('--out') + 1
                    call = call[:out] + [str(Path(tmp, 'cand-osm.json'))] + call[out + 1:]
                    self.assertEqual(cc.main(['catalog_curate.py'] + call[1:]), 0)
        finally:
            for k, v in real.items():
                setattr(cc, k, v)
        self.assertEqual([(k['workers'], k['metros_per_run'], k['kinds']) for k in seen],
                         [(8, None, ()), (None, 40, ())])


if __name__ == '__main__':
    unittest.main()
