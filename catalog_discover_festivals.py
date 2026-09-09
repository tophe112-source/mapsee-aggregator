#!/usr/bin/env python3
"""Bounded worldwide music-festival discovery via MusicBrainz."""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, time, re
from typing import Any, Optional
from urllib.parse import urlencode
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENDPOINT = "https://musicbrainz.org/ws/2"
DEFAULT_STATE = os.path.join(HERE, "festival_discovery_state.json")
UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
PAGE_SIZE, MAX_PAGE_SIZE = 20, 20
DETAIL_DELAY, BUDGET_SECONDS, DEFAULT_TIMEOUT = 1.1, 300, 10

def _date(value: Optional[str]):
    try: return dt.date.fromisoformat(value[:10]) if value else None
    except (TypeError, ValueError): return None

def _search(session, offset, begin, end, timeout, limit=PAGE_SIZE):
    query = f'type:festival AND begin:[{begin.isoformat()} TO {end.isoformat()}]'
    params = {"query": query, "fmt": "json", "limit": limit, "offset": offset}
    return session.get(ENDPOINT + "/event?" + urlencode(params), timeout=timeout, headers={"User-Agent": UA, "Accept": "application/json"})

def _detail(session, event_id, timeout):
    params = {"fmt": "json", "inc": "url-rels+place-rels+area-rels"}
    return session.get(ENDPOINT + "/event/" + event_id + "?" + urlencode(params), timeout=timeout, headers={"User-Agent": UA, "Accept": "application/json"})

def _relation_url(event, names):
    for rel in event.get("relations", []) or []:
        if str(rel.get("type", "")).lower() in names:
            url = (rel.get("url") or {}).get("resource")
            if isinstance(url, str) and url.startswith(("http://", "https://")): return url
    return None

def _place(event):
    for rel in event.get("relations", []) or []:
        coords = (rel.get("place") or {}).get("coordinates") or {}
        if coords.get("latitude") is not None and coords.get("longitude") is not None:
            try: return {"latitude": float(coords["latitude"]), "longitude": float(coords["longitude"])}
            except (TypeError, ValueError): pass
    return None

def candidate_from_event(event, begin=None, end=None):
    if not isinstance(event, dict) or event.get("cancelled") is True: return None
    event_id, name = event.get("id"), event.get("name")
    if not isinstance(event_id, str) or not event_id or not isinstance(name, str) or not name.strip(): return None
    life = event.get("life-span") or {}; start = _date(life.get("begin"))
    if begin and (not start or start < begin) or end and start and start > end: return None
    area, codes = event.get("area") or {}, (event.get("area") or {}).get("iso-3166-1-codes")
    country = codes[0] if isinstance(codes, list) and codes else area.get("name")
    result = {"source": "musicbrainz", "source_id": event_id, "name": name.strip(), "official_url": _relation_url(event, {"official homepage"}), "schedule_url": _relation_url(event, {"schedule", "event listing"}), "dates": {k: v for k, v in (("begin", life.get("begin")), ("end", life.get("end"))) if v}, "country": country, "status": "pending"}
    coordinates = _place(event)
    if coordinates: result["coordinates"] = coordinates
    return result

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh: value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}

def _save(path, value):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True); temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as fh: json.dump(value, fh, indent=2, ensure_ascii=False); fh.write("\n")
    os.replace(temp, path)

def discover(session=None, limit=PAGE_SIZE, timeout=DEFAULT_TIMEOUT, state_path=DEFAULT_STATE, today=None, budget_seconds=BUDGET_SECONDS, sleep=time.sleep):
    limit = max(1, min(int(limit), MAX_PAGE_SIZE)); now = _date(today) or dt.date.today()
    state = _load(state_path) if state_path else {}; old = state.get("candidates") if isinstance(state.get("candidates"), list) else []; cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}; offset = int(cursor.get("offset", 0) or 0)
    # Freeze the search window for a traversal. Advancing offset while changing
    # its date filter skips festivals when yesterday's results fall out.
    window_begin = _date(cursor.get('begin')) if offset else None
    window_begin = window_begin or now
    window_end = window_begin + dt.timedelta(days=365)
    session = session or requests.Session(); started = time.monotonic(); response = _search(session, offset, window_begin, window_end, timeout, limit)
    if response.status_code in (403, 429): raise RuntimeError(f"MusicBrainz refused discovery (HTTP {response.status_code}); no retry was attempted")
    response.raise_for_status(); payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list): raise ValueError("malformed MusicBrainz search payload; cursor was not advanced")
    if not isinstance(payload.get('count'), int) or payload['count'] < 0:
        raise ValueError('malformed MusicBrainz count; cursor was not advanced')
    events = payload["events"]; by_id = {x.get("source_id"): x for x in old if isinstance(x, dict) and x.get("source_id")}; processed = 0
    failure = None
    for summary in events:
        if time.monotonic() - started >= budget_seconds: break
        event_id = summary.get("id") if isinstance(summary, dict) else None
        if not event_id: processed += 1; continue
        if sleep: sleep(DETAIL_DELAY)  # Includes search -> first lookup.
        try:
            detail = _detail(session, event_id, timeout)
            detail.raise_for_status()
            full = detail.json()
            if not isinstance(full, dict) or full.get('id') != event_id:
                raise ValueError('malformed MusicBrainz event detail')
        except (ValueError, requests.RequestException):
            failure = 'MusicBrainz detail unavailable; unread event retained at cursor'
            break
        candidate = candidate_from_event(full, now, window_end)
        if candidate:
            prior = by_id.get(candidate["source_id"])
            if prior:
                candidate["status"] = prior.get("status", "pending"); candidate.update({k: v for k, v in prior.items() if k not in candidate})
            by_id[candidate["source_id"]] = candidate
        processed += 1
    complete = processed >= len(events); total = payload.get("count")
    next_offset = offset + processed if not complete else (0 if total is None or offset + len(events) >= total else offset + processed)
    result = {"candidates": list(by_id.values()), "cursor": {"offset": next_offset, "begin": window_begin.isoformat() if next_offset else None, "examined": int(cursor.get("examined", 0) or 0) + processed}, "stats": {"examined": processed, "total": total, "failure": failure}}
    if state_path: _save(state_path, result)
    return result

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--limit", type=int, default=PAGE_SIZE); p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT); p.add_argument("--state", default=DEFAULT_STATE); p.add_argument("--out")
    a = p.parse_args(argv); result = discover(limit=a.limit, timeout=a.timeout, state_path=a.state)
    if a.out: _save(a.out, result)
    else: json.dump(result, sys.stdout, indent=2, ensure_ascii=False); print()
    return 1 if result['stats'].get('failure') else 0
if __name__ == "__main__": raise SystemExit(main())
