#!/usr/bin/env python3
"""
mapsee_ingest_tribe.py - import events from any WordPress site running "The
Events Calendar" (Modern Tribe), via its public REST API.

    python mapsee_ingest_tribe.py --config tribe_sources.json --store feeds_events.json

One adapter, many sites: the plugin ships the same `/wp-json/tribe/events/v1/`
route on every host that installs it, so adding a source is a config line. The
plugin is on hundreds of thousands of sites and is especially common exactly
where mapsee is thin - comedy rooms, running clubs, native plant societies,
audubon chapters, small museums.

WHY NOT THE .ics FEED. mapsee_ingest_ics.py already reads the same plugin's
`/events/?ical=1`, and for a handful of sources that is fine. The REST API is
strictly better where it exists: it carries `venue.geo_lat`/`geo_lng`, so the
event lands on the map without a geocoder round trip; it separates venue name
from street, city and state instead of gluing them into one LOCATION line; it
gives a real ticket URL; and it paginates, so a site with 10,000 events can be
walked instead of downloading one enormous calendar. Probing
`/wp-json/tribe/events/v1/events` also cleanly trisects a new candidate: JSON
with `events` means yes, a `rest_no_route` JSON error means WordPress without
the plugin, HTML means it is not WordPress at all.

CRAWL DELAY IS PER SITE AND IT IS NOT OPTIONAL. These are small nonprofits on
shared hosting, and several of them say so in robots.txt: The Comedy Bureau asks
10s, the Georgia Native Plant Society asks 150s. `crawl_delay` defaults to a
polite 2s and every configured value is honoured between page requests. A source
whose robots.txt disallows the API does not belong in the config at all.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories
from catalog_discover_osm import CIVIC_TITLE_RX, CIVIC_HOLIDAY_RX
from mapsee_admission import admission_description, normalize_admission_facts

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _f(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # The plugin writes 0/0 when a venue has no coordinates rather than leaving
    # them null, and 0,0 is the Atlantic. Treat it as absent.
    return None if f == 0.0 else f


def _obj(v) -> Dict[str, Any]:
    """One of the plugin's object fields, as a dict — whatever shape it arrived in.

    `venue` is a dict on most events, `[]` when unset, and `[{...}]` on some — all
    three from the SAME site: measured on bicyclecolorado.org, 105 dicts, 4
    populated lists, out of 109. `image` is a dict or the bare boolean `false`
    (72 of 109). `or {}` alone covers the empty list, which is why this went
    unnoticed; a POPULATED list sails through it and raises AttributeError on the
    first `.get`.

    That exception is not caught per event — `ingest_site` wraps the whole site —
    so a single malformed record does not lose one event, it loses the ENTIRE
    SOURCE, and it does it while printing a line that looks like a network fault
    ("FAILED: 'list' object has no attribute 'get'"). Bicycle Colorado ingested 0
    of its 105 placeable events that way.
    """
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return next((x for x in v if isinstance(x, dict)), {})
    return {}


def _venue_key(name: Optional[str]) -> str:
    return re.sub(r"[^\w]+", " ", (name or "").casefold()).strip()


def _http_host(url: str) -> str:
    try:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme.lower() not in {"http", "https"}:
            return ""
        return (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _known_admission_ids_for_site(store: EventStore, base_url: str) -> set:
    """Previously known Tribe admission facts owned by this publisher host.

    A free-only refresh still needs to see a source event when its publisher
    changes the price to paid, restricted, or unknown. The source ID alone is
    not enough: Tribe IDs are only unique within a WordPress installation.
    Build this once per site so rejected new rows stay cheap. New records carry
    a transient publisher host in their local admission owner, independent of
    the selected ticket URL; legacy records fall back to a same-host source ref.
    """
    base_host = _http_host(base_url)
    records = getattr(store, "records", {})
    if not base_host or not isinstance(records, dict):
        return set()

    known = set()
    for rec in records.values():
        details = rec.get("source_details")
        if not isinstance(details, dict) or not any(
                key in details for key in ("free", "offer", "restricted")):
            continue
        owner = rec.get("_admission_source")
        refs = rec.get("sources")
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict) or ref.get("source") != "tribe":
                continue
            source_id = ref.get("source_id")
            if not source_id:
                continue
            publisher = str(ref.get("publisher") or "")
            source_host = (_http_host(publisher if "://" in publisher
                                      else "https://" + publisher)
                           or _http_host(ref.get("url")))
            owns_current = (isinstance(owner, dict)
                            and owner.get("source") == "tribe"
                            and owner.get("source_id") == source_id
                            and owner.get("publisher") == base_host)
            owns_legacy = (isinstance(owner, dict)
                           and owner.get("source") == "tribe"
                           and owner.get("source_id") == source_id
                           and not owner.get("publisher")
                           and source_host == base_host)
            legacy_free = (owner is None and details.get("free") is True
                           and source_host == base_host)
            if owns_current or owns_legacy or legacy_free:
                known.add(str(source_id))
    return known


def _block_stands_in(v: Dict[str, Any], vd: Dict[str, Any]) -> bool:
    """May the config's `venue` block fill this event's place?

    By default, yes: most blocks belong to a single-venue site whose events name
    a room, an alias or nothing, and 1,974 of 7,042 coordless events on 183
    sites with a block (2026-09-27) gave the block's own address. A block that
    lists its own `names` is stricter: it fills only an event that names no
    venue, or names one of those. UMFA files its Land Art Week trips under
    "Rozel Point", "Powder Mountain" and "Kimball Arts Center" with no address,
    beside eighteen rows that say only "UMFA". The Finnish Deafblind
    Association's centre in Tampere lists events in Oulu, Kemi and Rovaniemi.
    Each of those was pinned at the block, and took its street too, so the
    Census pass put the US ones straight back there.

    Not the default because a city or address test cannot tell a place from its
    other names: Den Haag is 's-Gravenhage, Etobicoke is Toronto, Vanier is
    Ottawa, and 336 coordless events in that sample differed from their block's
    city that way, almost all of them the same place.
    """
    names = vd.get("names") if vd else None
    if not names:
        return True
    own = _venue_key(_clean(v.get("venue")))
    return not own or any(re.search(rf"\b{re.escape(_venue_key(n))}\b", own) for n in names)


def to_event(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _clean(ev.get("title"))
    start = (ev.get("start_date") or "").strip()          # "2026-08-04 10:00:00", site-local
    if not name or len(start) < 10:
        return None
    start_local = start.replace(" ", "T")[:19]
    v = _obj(ev.get("venue"))
    lat, lon = _f(v.get("geo_lat")), _f(v.get("geo_lng"))
    # A SITE OUTSIDE THE US THAT LEAVES geo_lat BLANK CANNOT BE PLACED AT ALL.
    # The sync's only geocoder is the US Census batch service, and a row with no
    # coordinates is dropped there — so for a Canadian or European calendar whose
    # organiser never filled in the venue's map fields, "ingested 43 events" and
    # "put 0 events on the map" look identical from here. Calgary Buddhist Temple
    # is the live example: 43 future events, every one of them coordless.
    #
    # The config's `venue` block FILLS those gaps and never overrides real data,
    # which is the same contract the Squarespace and JSON-LD adapters already
    # have. Discovery supplies it for free: catalog_discover_osm proposes every
    # candidate with the surveyed point OSM holds for that venue.
    vd = site.get("venue") or {}
    if not _block_stands_in(v, vd):
        vd = {}                                   # not its coordinates, and not its street either
    if lat is None or lon is None:
        lat, lon = _f(vd.get("lat")), _f(vd.get("lon"))
    # The plugin's own taxonomy, folded in alongside the configured default so a
    # site that files things usefully (a running club tagging "volunteer") lands
    # on more than one lens. norm_categories drops anything outside mapsee's
    # vocabulary, so a source's private labels can never reach the database.
    primary = site.get("category", "community")
    cats = ev.get("categories")
    extras = norm_categories(primary, [c.get("slug") or c.get("name")
                                       for c in (cats if isinstance(cats, list) else [])
                                       if isinstance(c, dict)])
    description = _clean(ev.get("description"))
    # `website` is the organiser's free text, and a link without a scheme is no
    # link: 7 of BCUT Timisoara's 8 rows said "www.bcut.ro" and 26 of Trekanten's
    # "www.makerspace0220.dk" (2026-10-04). The event's own page is always whole.
    website = ev.get("website") if isinstance(ev.get("website"), str) else ""
    website = website.strip()
    ticket_url = website if re.match(r"https?://", website, re.I) else (ev.get("url") or None)
    admission = normalize_admission_facts(
        ev.get("cost"), url=ticket_url, currency_hint=site.get("currency"),
        context=f"{name} {description or ''}")
    description = admission_description(description, admission)
    nev = NormalizedEvent(
        source="tribe",
        source_id=str(ev.get("id") or ev.get("global_id") or make_fingerprint(name, start_local[:10], v.get("venue"))),
        name=name,
        description=description,
        start_local=start_local,
        end_local=((ev.get("end_date") or "").strip().replace(" ", "T")[:19] or None),
        timezone=ev.get("timezone") or None,
        venue_name=_clean(v.get("venue")) or site.get("venue_name") or vd.get("name"),
        latitude=lat, longitude=lon,
        address=_clean(v.get("address")) or vd.get("address"),
        city=_clean(v.get("city")) or site.get("default_city") or vd.get("city"),
        region=(_clean(v.get("state") or v.get("stateprovince") or v.get("province"))
                or site.get("default_region") or vd.get("region")),
        country=_clean(v.get("country")) or site.get("default_country") or vd.get("country"),
        postal_code=_clean(v.get("zip")) or vd.get("postal_code"),
        category=primary,
        categories=extras,
        poster_image_url=_obj(ev.get("image")).get("url"),
        # `website` is the venue's own ticket link when set; `url` is the event
        # page on the source site, which is always present and always useful.
        ticket_url=ticket_url,
        source_details=admission,
        admission_checked=True,
        admission_publisher=_http_host(site.get("base_url")) or None,
    )
    nev.fingerprint = make_fingerprint(name, start_local[:10], nev.venue_name, nev.city)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        print(f"[tribe] {site.get('name','?')}: no base_url"); return 0
    api = f"{base}/wp-json/tribe/events/v1/events"
    delay = float(site.get("crawl_delay", 2))
    per_page = int(site.get("per_page", 50))
    max_pages = int(site.get("max_pages", 20))
    now = datetime.now(timezone.utc)
    if site.get("timezone"):
        # A UTC date at midnight is already tomorrow in Hawai'i. Asking for
        # that date skips still-upcoming local-day activities. Only publishers
        # with a configured calendar zone change the existing UTC default.
        from zoneinfo import ZoneInfo
        now = now.astimezone(ZoneInfo(site["timezone"]))
    params = {
        "per_page": per_page,
        "start_date": now.strftime("%Y-%m-%d"),
        "end_date": (now + timedelta(days=int(site.get("within_days", 180)))).strftime("%Y-%m-%d"),
    }
    # Publisher categories can separate public welcomes from private sessions.
    # Request only those categories, then check every returned row too: a server
    # that ignores the query must not let a private event inherit our venue pin.
    included = site.get("include_categories")
    if included is not None:
        if (not isinstance(included, list) or not included
                or any(isinstance(c, bool) or not isinstance(c, (str, int))
                       or not str(c).strip() or "," in str(c) for c in included)):
            raise ValueError("include_categories must be a nonempty list of category IDs or slugs")
        included = {str(c).strip().lower() for c in included}
        params["categories"] = ",".join(sorted(included))
    # A parish council's calendar files "Planning & Highways Meeting" beside its
    # memory café, under titles the town-hall phrases (CIVIC_TITLE_RX) do not
    # know, and its category filter would cost the café too: Haydon Wick's
    # committee rows carry only a committee slug and its Companions Café none
    # (2026-10-04). Same option, same case-insensitive search, as ics and jsonld.
    skip_title = None
    if site.get("skip_title"):
        try:
            skip_title = re.compile(site["skip_title"], re.I)
        except (re.error, TypeError) as exc:
            raise ValueError(f"{site.get('name', '?')}: invalid skip_title regex: {exc}") from exc
    kept = 0
    malformed = 0
    governance = 0
    excluded = 0
    admission_excluded = 0
    title_filtered = 0
    is_civic = str(site.get("_found", "")).startswith("civic:")
    refreshable_admission_ids = (_known_admission_ids_for_site(store, base)
                                 if site.get("free_only") else set())
    for page in range(1, max_pages + 1):
        try:
            r = session.get(api, params=dict(params, page=page), timeout=45)
        except Exception as exc:
            print(f"[tribe] {site.get('name')} p{page} failed: {exc}"); break
        # The plugin answers a page past the end with 404 + rest_post_invalid_page_number.
        if r.status_code == 404:
            break
        if r.status_code != 200:
            print(f"[tribe] {site.get('name')} p{page} HTTP {r.status_code}"); break
        try:
            body = r.json()
        except Exception as exc:
            print(f"[tribe] {site.get('name')} p{page} bad JSON: {exc}"); break
        rows = body.get("events") or []
        if not rows:
            break
        for ev in rows:
            # Per EVENT, not per site. The site-level try in main() means one
            # unreadable record costs the whole calendar (see _obj), and it
            # reports as a site failure, which reads like the host was down.
            # Counted and printed rather than swallowed: a silent skip is how a
            # shape change eats a source one record at a time.
            try:
                if included is not None:
                    cats = ev.get("categories")
                    keys = {str(c[k]).strip().lower()
                            for c in (cats if isinstance(cats, list) else [])
                            if isinstance(c, dict) for k in ("id", "slug")
                            if c.get(k) is not None}
                    if included.isdisjoint(keys):
                        excluded += 1
                        continue
                if site.get("free_only"):
                    title = _clean(ev.get("title")) or ""
                    description = _clean(ev.get("description")) or ""
                    facts = normalize_admission_facts(
                        ev.get("cost"), currency_hint=site.get("currency"),
                        context=f"{title} {description}")
                    source_id = str(ev.get("id") or ev.get("global_id") or "")
                    if (not facts or facts.get("free") is not True) and (
                            source_id not in refreshable_admission_ids):
                        admission_excluded += 1
                        continue
                nev = to_event(ev, site)
            except Exception as exc:                       # noqa: BLE001
                if not malformed:
                    print(f"[tribe] {site.get('name')}: skipping unreadable record "
                          f"{ev.get('id')!r} ({type(exc).__name__}: {exc})")
                malformed += 1
                continue
            # A TOWN HALL'S CALENDAR IS TWO CALENDARS, here as in the ics adapter:
            # Sherwood, OR carried a City Council Meeting and Dormont, PA six
            # "CLOSED:" rows beside its programme (2026-09-27). Same phrases,
            # same gate: only a source whose `_found` says it is a town hall's.
            if nev and is_civic and (CIVIC_TITLE_RX.search(nev.name or "")
                                     or CIVIC_HOLIDAY_RX.match((nev.name or "").strip())):
                governance += 1
                continue
            if nev and skip_title and skip_title.search(nev.name or ""):
                title_filtered += 1
                continue
            if nev:
                store.upsert(nev)
                kept += 1
        if page >= int(body.get("total_pages") or page):
            break
        if delay:
            time.sleep(delay)                              # robots.txt Crawl-delay
    print(f"[tribe] {site.get('name')}: kept {kept} events"
          + (f" ({malformed} unreadable record(s) skipped)" if malformed else "")
          + (f" ({governance} town-hall row(s) refused)" if governance else "")
          + (f" ({title_filtered} title-filtered)" if title_filtered else "")
          + (f" ({excluded} outside configured categories)" if excluded else "")
          + (f" ({admission_excluded} outside explicit-free admission scope)"
             if admission_excluded else ""))
    return kept


def _load_cursor(path: str) -> Optional[str]:
    try:
        cur = json.load(open(path, encoding="utf-8"))
        return cur.get("site") if isinstance(cur, dict) and isinstance(cur.get("site"), str) else None
    except (OSError, ValueError):
        return None


def _save_cursor(path: str, base_url: Optional[str]) -> None:
    if not base_url:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"site": base_url}, fh)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import 'The Events Calendar' sites into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    # THE STEP'S CLOCK, NOT THIS PROCESS'S — the same deadline `mapsee_sweep_global`
    # takes, and for the reason in docs/agents/ci-and-jobs.md: `store.save()` below
    # runs ONCE, after the loop, so a step cancelled by `timeout-minutes` kills this
    # process before it and the whole run goes in the bin. It did: 532 sites at ~6.8 s
    # is ~60 min against a 60-minute cap, and run 35339055917 was cancelled at exactly
    # 60m00s having reported "kept" for 495 of them — an hour of runner time that
    # wrote nothing, and a red workflow. The workflow stamps an epoch deadline at step
    # start; this starts no further site once it has passed, so the save still happens
    # and the sites that did run are kept. 0 = no deadline (local runs).
    ap.add_argument("--deadline", type=float, default=0.0,
                    help="epoch seconds; start no site at or after this (0 = none)")
    # A DEADLINE WITHOUT A CURSOR STARVES THE SAME TAIL EVERY DAY. Run
    # 36317891716 (2026-09-27) stopped at the deadline "before Autodromo Nazionale
    # di Monza: 65 of 673 sites not started", and because every run began at site
    # one it was the same 65 every night: those calendars were never read at all.
    # With a deadline, the run starts at the site the last one stopped before and
    # walks the list round, so every site is read in turn, the way the ics and
    # jsonld adapters resume. Local runs (no deadline) are unchanged.
    ap.add_argument("--cursor", default=os.environ.get("TRIBE_CURSOR", "tribe_cursor.json"),
                    help="with --deadline: file naming the site to start at")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    sites = [s for s in cfg.get("sites", [])
             if not (a.only and a.only.lower() not in str(s.get("name", "")).lower())]
    done = 0
    stopped = False
    if a.deadline and sites:
        start_at = _load_cursor(a.cursor)
        start = next((i for i, s in enumerate(sites) if s.get("base_url") == start_at), 0)
        sites = sites[start:] + sites[:start]
        if start:
            print(f"[tribe] resuming at {sites[0].get('name','?')} (site {start + 1} of {len(sites)})")
    for site in sites:
        # Checked BEFORE the site is counted: a site never started must not appear
        # in the "done" line, which is the only line anyone reads.
        if a.deadline and time.time() >= a.deadline:
            # `::warning::` first, or the runner never annotates it.
            print(f"::warning::[tribe] deadline reached before {site.get('name','?')}: "
                  f"{len(sites) - done} of {len(sites)} sites not started this run", flush=True)
            _save_cursor(a.cursor, site.get("base_url"))
            stopped = True
            break
        done += 1
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[tribe] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[tribe] done: +{total} events from {done} of {len(sites)} sites; "
          f"store now holds {len(store.records)} unique events."
          + (" (stopped at the step deadline)" if stopped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
