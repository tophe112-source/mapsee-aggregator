"""Publish actual Actions step outcomes, including tolerated adapter failures."""
import argparse
import json
import os
from pathlib import Path


def report(steps, group):
    rows = [(key, value.get('outcome', 'unknown')) for key, value in steps.items()
            if key.startswith('ingest_') or key in {'checkpoint', 'sync'}]
    failures = [key for key, outcome in rows if outcome in {'failure', 'cancelled'}]
    lines = [f'### Feed imports: {group}', '',
             'These are process outcomes; source freshness is checked separately.', '',
             '| Step | Outcome |', '|---|---|']
    lines += [f'| {key} | {outcome} |' for key, outcome in rows if outcome != 'skipped']
    lines += ['', f'{len(failures)} failed or cancelled steps. Recovery store attached as an artifact.', '']
    return '\n'.join(lines)


def outstanding_failures(steps):
    sync = steps.get('sync', {})
    recovered = sync.get('outcome') == 'success' and sync.get('outputs', {}).get('synced_store') == 'true'
    return [key for key, step in steps.items() if step.get('outcome') in {'failure', 'cancelled'}
            and not (key == 'checkpoint' and recovered)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('steps', nargs='?')
    parser.add_argument('--group', choices=['ics', 'local', 'civic', 'rec'])
    parser.add_argument('--check', action='store_true', help="Fail for outstanding step failures.")
    args = parser.parse_args()
    if args.check:
        failures = outstanding_failures(json.loads(os.environ['STEP_RESULTS']))
        if failures:
            raise SystemExit('Outstanding failures: ' + ', '.join(failures))
        print('The final cumulative sync recovered the failed checkpoint.')
        return
    if not args.steps or not args.group:
        parser.error('the report requires a steps file and --group')
    text = report(json.loads(Path(args.steps).read_text(encoding='utf-8')), args.group)
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as target:
            target.write(text)


if __name__ == '__main__':
    main()
