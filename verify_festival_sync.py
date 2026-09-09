#!/usr/bin/env python3
"""Read back this run's exact festival identities and verify stored agendas."""
import argparse
import json
import os
from pathlib import Path
import requests
from mapsee_supabase_sync import build_rows, _norm_cmp


def verify(rows, session, url, key):
    result = []
    for row in rows:
        response = session.get(url.rstrip('/') + '/rest/v1/events',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'},
            params={'external_source': 'eq.mapsee', 'external_id': 'eq.' + row['external_id'],
                    'select': 'id,title,starts_at,ends_at,claimed_at,agenda,agenda_tz,agenda_rev', 'limit': 2}, timeout=30)
        response.raise_for_status()
        found = response.json()
        if not isinstance(found, list) or len(found) != 1:
            raise RuntimeError('Festival read-back did not find exactly one event')
        actual = found[0]
        if actual.get('claimed_at') is not None:
            result.append({'id': actual['id'], 'status': 'claimed; organizer edits preserved'})
            continue
        for field in ('agenda', 'agenda_tz', 'starts_at', 'ends_at'):
            if _norm_cmp(field, row.get(field)) != _norm_cmp(field, actual.get(field)):
                raise RuntimeError(f'Festival read-back differs in {field}')
        result.append({'id': actual['id'], 'title': actual['title'], 'agenda_items': len(actual.get('agenda') or []),
                       'agenda_rev': actual['agenda_rev'], 'url': 'https://mapsee.me/e/' + actual['id'], 'status': 'verified'})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store', default='festival_events.json')
    p.add_argument('--out', default='tmp/festival-readback.json')
    a = p.parse_args()
    result = verify(build_rows(a.store, os.environ['MAPSEE_HOST_PROFILE_ID']), requests.Session(),
                    os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
