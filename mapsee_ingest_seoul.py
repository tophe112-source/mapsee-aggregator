#!/usr/bin/env python3
"""
mapsee_ingest_seoul.py - Seoul Open Data Plaza cultural events -> Mapsee store.

South Korea's civic data does not run on Socrata/OpenDataSoft; Seoul publishes
through its own OpenAPI at data.seoul.go.kr. The culturalEventInfo service
lists the city's cultural events (concerts, exhibitions, festivals, kids
programs) with venue names, lat/lon, dates, and links - already geo-tagged, so
no geocoding is spent. Korean-language content is fine: the app translates
event text into the viewer's language.

    SEOUL_OPEN_API_KEY=<key> python mapsee_ingest_seoul.py --store feeds_events.json

Key-gated: a silent no-op until SEOUL_OPEN_API_KEY is set (free, instant
signup at data.seoul.go.kr -> login -> 인증키 신청). API shape:
    http://openapi.seoul.go.kr:8088/{KEY}/json/culturalEventInfo/{start}/{end}/
Rows carry: TITLE, CODENAME (genre), STRTDATE/END_DATE ("YYYY-MM-DD HH:MM:SS.S"),
PLACE, GUNAME (district), ORG_LINK, LOT (lat!), LAT (lon!) - yes, the API's
LAT/LOT columns are swapped relative to their names; we detect by value range.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
# CODENAME genre -> mapsee category key (unlisted genres fall back to community)
GENRE = {
    "콘서트": "music", "클래식": "music", "국악": "music", "무용": "arts",
    "연극": "theater", "뮤지컬/오페라": "theater", "영화": "theater",
    "전시/미술": "arts", "교육/체험": "learning", "축제": "community",
    "축제-문화/예술": "community", "축제-기타": "community", "기타": "other",
}


def _dt(s):
    s = (s or "").strip()
    return s[:10] + "T" + (s[11:16] or "00:00") + ":00" if len(s) >= 10 else None


def ingest(store: EventStore, session, key: str, max_rows: int, days_ahead: int) -> int:
    kept = 0
    horizon = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    for start in range(1, max_rows + 1, 1000):          # API pages 1000 rows max
        url = f"http://openapi.seoul.go.kr:8088/{key}/json/culturalEventInfo/{start}/{min(start + 999, max_rows)}/"
        r = session.get(url, timeout=30)
        r.raise_for_status()
        body = (r.json() or {}).get("culturalEventInfo") or {}
        rows = body.get("row") or []
        if not rows:
            break
        for row in rows:
            title = (row.get("TITLE") or "").strip()
            s_loc, e_loc = _dt(row.get("STRTDATE")), _dt(row.get("END_DATE"))
            if not title or not s_loc:
                continue
            end_day = (e_loc or s_loc)[:10]
            if end_day < today or s_loc[:10] > horizon:
                continue                                  # over, or too far out
            # the API's LAT/LOT names are notoriously swapped; sort by magnitude
            try:
                a, b = float(row.get("LAT") or 0), float(row.get("LOT") or 0)
            except Exception:
                continue
            lat, lon = (a, b) if 33 <= a <= 39 else (b, a)
            if not (33 <= lat <= 39 and 124 <= lon <= 132):
                continue                                  # not actually in Korea
            place = (row.get("PLACE") or "").strip() or None
            link = (row.get("ORG_LINK") or row.get("HMPG_ADDR") or "").strip() or None
            ev = NormalizedEvent(
                source="seoul-open-data",
                source_id=f"{title}#{s_loc[:10]}#{place or ''}",
                name=title,
                description=(row.get("PROGRAM") or row.get("ETC_DESC") or "").strip()[:600] or None,
                start_local=s_loc, end_local=e_loc,
                venue_name=place, latitude=lat, longitude=lon,
                city="Seoul", country="KR",
                category=GENRE.get((row.get("CODENAME") or "").strip(), "community"),
                ticket_url=link,
            )
            ev.fingerprint = make_fingerprint(title, s_loc[:10], place)
            store.upsert(ev)
            kept += 1
        if len(rows) < 1000:
            break
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Seoul Open Data Plaza cultural events into the Mapsee store.")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--max-rows", type=int, default=3000)
    ap.add_argument("--days-ahead", type=int, default=60)
    a = ap.parse_args(argv)
    key = os.environ.get("SEOUL_OPEN_API_KEY", "").strip()
    if not key:
        print("[seoul] SEOUL_OPEN_API_KEY unset - skipped")
        return 0
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    store = EventStore(a.store)
    kept = ingest(store, session, key, a.max_rows, a.days_ahead)
    store.save()
    print(f"[seoul] done: +{kept} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
