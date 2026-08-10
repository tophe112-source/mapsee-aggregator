#!/usr/bin/env python3
"""
mapsee_ingest_dice_venue.py — a DICE venue's own public page, for venues whose
website is only a DICE widget.

WHY THIS EXISTS RATHER THAN mapsee_ingest_dice.py
--------------------------------------------------
That adapter talks to DICE's PARTNER API and needs `DICE_API_KEY`, which mapsee
does not currently hold. This one reads a public venue page and needs no key.
They are not alternatives: if the partner key ever arrives, prefer it, because a
partner feed is a published interface and this is a page payload.

WHY NOT THE VENUE'S OWN SITE
----------------------------
Worked example, The Vera Project. theveraproject.org/events/ is a WordPress page
whose entire content is `<div id="dice-event-list-widget">` — no event markup,
no JSON-LD. Its robots.txt is also `Disallow: /*?`, which rules out every
query-string route a WordPress venue normally offers: `?ical=1`, the Tribe REST
API, the lot. The events are not on the venue's site in any readable form; they
are on DICE.

CONDUCT — READ THIS BEFORE ADDING A SOURCE
------------------------------------------
dice.fm/robots.txt allows `/` for `User-agent: *` and disallows exactly one
thing: `/api/`. So this adapter reads the VENUE PAGE and never the API, and that
distinction is the whole basis for it being allowed. The events are server-
rendered into the page's own `__NEXT_DATA__` payload, so no API call is needed
to see them.

Three more things that made this defensible rather than merely possible:

  * the venue page is listed in DICE's own published sitemap
    (dice.fm/sitemaps/venues/sitemap.xml), which is how it was found;
  * `Content-Signal: search=yes, use=reference` — mapsee links people onward to
    the DICE page to buy, which is reference use, and does not republish tickets;
  * the events are publicly advertised and the venue is paying to advertise them.

WHAT IS NOT OK, and was considered and rejected: the venue's page embeds a DICE
widget config carrying a `partnerId` and an `apiKey` in plain sight. Those would
reach `api.dice.fm` directly, which is (a) the one path robots.txt disallows and
(b) somebody else's credential. Do not use them. If you find yourself wanting
per-venue pagination beyond what the page carries, that is the signal to go and
get a real `DICE_API_KEY`, not to borrow one.

WHAT IT EMITS
-------------
One event per listing on the page, with the venue's exact coordinates from the
payload — no geocoding, so no Photon budget and no chance of a wrong pin.
Cancelled and postponed listings are dropped; sold-out ones are kept, because a
sold-out show is still a real thing happening at a real place.

    python mapsee_ingest_dice_venue.py --config dice_venue_sources.json \\
                                       --store events.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

WEB = "https://dice.fm"
# The events are in the Next.js hydration payload the server already sent. Same
# shape mapsee_ingest_luma.py reads, and for the same reason: it is the page's
# own content, not a second request to an endpoint we are asked not to call.
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

# A listing that is not going to happen. `sold-out` is deliberately NOT here:
# the show is still on, people still turn up, and it still belongs on a map of
# what is happening tonight.
DEAD_STATUSES = {"cancelled", "canceled", "postponed"}

DEFAULTS = {
    "category": "music",     # DICE is a music ticketing platform; override per venue
    "categories": [],
    "pause_s": 3,            # dice.fm sets no Crawl-delay; be a good guest anyway
    "timeout_s": 45,
}


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


def _find_events(node: Any, depth: int = 0) -> List[dict]:
    """The events list, wherever DICE has moved it to this quarter.

    Searched for by SHAPE — a list of dicts carrying `date_unix` — rather than
    by a fixed path like props.pageProps.venue.events. A hydration payload is
    page internals and its layout is not a promise to anyone; a path would break
    on a Next.js upgrade and break silently, reporting a venue with no events
    rather than an adapter that can no longer read one.
    """
    if depth > 8:
        return []
    if isinstance(node, dict):
        for key, value in node.items():
            if (key == "events" and isinstance(value, list) and value
                    and isinstance(value[0], dict) and "date_unix" in value[0]):
                return value
            hit = _find_events(value, depth + 1)
            if hit:
                return hit
    elif isinstance(node, list):
        for item in node[:8]:
            hit = _find_events(item, depth + 1)
            if hit:
                return hit
    return []


def fetch_events(session, url: str, cfg: Dict[str, Any]) -> List[dict]:
    r = session.get(url, timeout=cfg["timeout_s"])
    r.raise_for_status()
    m = _NEXT_DATA.search(r.text)
    if not m:
        # The page loaded but carries no payload — a redesign, or a challenge
        # page. Either way it is not "this venue has no events", and saying so
        # is the difference between a fixable report and a silent zero.
        raise ValueError(f"no __NEXT_DATA__ in {url} ({len(r.text)} bytes) — "
                         f"the page shape changed, or this is not a venue page")
    return _find_events(json.loads(m.group(1)))


def to_event(ev: dict, src: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _clean(ev.get("name"))
    if not name:
        return None
    if str(ev.get("status") or "").lower() in DEAD_STATUSES:
        return None

    dates = ev.get("dates") or {}
    start = dates.get("event_start_date")
    tzname = dates.get("timezone")
    if not start:
        return None
    # Already carries its offset ("2026-08-11T19:00:00-07:00"), so this is a real
    # instant and the sync has nothing to infer.
    try:
        dt = datetime.fromisoformat(str(start))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    if dt.astimezone(timezone.utc) < datetime.now(timezone.utc):
        return None                       # already happened

    venues = ev.get("venues") or []
    venue = venues[0] if venues and isinstance(venues[0], dict) else {}
    loc = venue.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lng")
    if lat is None or lon is None:
        # The venue's own coordinates are the entire reason this adapter needs no
        # geocoder. Without them there is nothing to fall back on that would not
        # be a guess, so the listing is skipped and counted.
        return None

    # "305 Harrison Street, Seattle, Washington 98109, United States" — the
    # street is everything before the first comma; the rest is already covered by
    # the coordinates, so it is not worth parsing badly.
    address_full = _clean(venue.get("address"))
    street = address_full.split(",")[0].strip() if address_full else None
    city = ((venue.get("city") or {}).get("name")) or src.get("default_city")
    country = ((venue.get("city") or {}).get("country_code")) or src.get("default_country")

    slug = _clean(ev.get("perm_name"))
    images = ev.get("event_images") or {}
    poster = images.get("landscape") or images.get("square") or images.get("portrait")

    primary = src.get("category") or cfg["category"]
    extras = src.get("categories") or cfg["categories"]
    date_key = dt.strftime("%Y-%m-%d")
    venue_name = _clean(venue.get("name")) or src.get("name")
    fp = make_fingerprint(name, date_key, venue_name, city)

    nev = NormalizedEvent(
        source="dice_venue",
        source_id=str(ev.get("id") or slug or fp),
        name=name,
        start_local=dt.strftime("%Y-%m-%dT%H:%M:%S"),
        start_utc=dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        timezone=tzname,
        venue_name=venue_name,
        latitude=float(lat), longitude=float(lon),
        address=street,
        city=_clean(city),
        region=src.get("default_region"),
        country=_clean(country),
        category=primary,
        categories=norm_categories(primary, extras),
        poster_image_url=poster or None,
        ticket_url=f"{WEB}/event/{slug}" if slug else src.get("url"),
    )
    nev.fingerprint = fp
    return nev


def ingest(store: EventStore, session, cfg: Dict[str, Any]) -> int:
    total = 0
    for src in cfg.get("venues") or []:
        url = src.get("url")
        if not url:
            continue
        label = src.get("name") or url
        try:
            raw = fetch_events(session, url, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[dice_venue] {label}: FAILED — {exc}")
            continue
        kept = dropped = 0
        for ev in raw:
            nev = to_event(ev, src, cfg)
            if nev is None:
                dropped += 1
                continue
            store.upsert(nev)
            kept += 1
        note = f", {dropped} past/cancelled/unplaceable" if dropped else ""
        print(f"[dice_venue] {label}: kept {kept} of {len(raw)} listed{note}")
        total += kept
        time.sleep(cfg["pause_s"])
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a DICE venue's public page (no API key, never /api/).")
    ap.add_argument("--config", default="dice_venue_sources.json")
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = dict(DEFAULTS)
    if os.path.exists(a.config):
        try:
            cfg.update(json.loads(open(a.config, encoding="utf-8").read()) or {})
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[dice_venue] unreadable {a.config} ({exc}) — nothing to do")
            return 0
    else:
        print(f"[dice_venue] no {a.config} — skipping")
        return 0

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)",
         "Accept": "text/html"})
    store = EventStore(a.store)
    try:
        ingest(store, session, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[dice_venue] FAILED: {exc}")
        return 0                       # a dead source must not fail the pipeline
    store.save()
    print(f"[dice_venue] done; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
