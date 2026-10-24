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

THREE SITES AT ONCE, NEVER TWO ON ONE HOST. On 2026-10-23 the step read 586 of
1,408 sites before its 80-minute deadline (8.2 s a site), and it stopped at the
deadline in 15 of 15 runs, so a site was read every ~2.4 days. The 1,408 sites are
on 1,408 hosts and most of a site's time is its own Crawl-delay (1,328 ask 2 s,
54 ask 10 s, 9 ask 30 s or more), so `--workers` (3, as PerfectMind) reads three
hosts at once and each host still sees one request at a time, Crawl-delay apart.
A worker only reads: the store is written in the main thread, which hands each
worker its own Session.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import html as html_mod
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, looks_online_only, make_fingerprint, norm_categories
from catalog_discover_osm import CIVIC_TITLE_RX, CIVIC_HOLIDAY_RX
from mapsee_admission import admission_description, normalize_admission_facts

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")

# Sites read at once (`--workers`, or TRIBE_WORKERS). Each is its own host.
DEFAULT_WORKERS = 3

# The store and cursor are written together at most this often, and at the end:
# the step stops at its deadline every run, and a store saved only at the end is
# lost whole the day the step's cap ends it.
SAVE_EVERY_SECONDS = 120

_SAY = threading.Lock()


def _say(line: str) -> None:
    # Whole lines, flushed: three workers print at once, and a block-buffered log
    # cannot say which site took the time (the 10-23 ICS log printed its first
    # line ~33 min into the step).
    with _SAY:
        print(line, flush=True)


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


def site_admission_ids(store: EventStore, site: Dict[str, Any]) -> set:
    """The ids `ingest_site` may refresh on a `free_only` site, read from the store.

    In the main thread, before the site goes to a worker: the store is not
    thread-safe. A site on the same host never starts until the last one's rows
    are written, so it sees them, as it did when every site ran in turn.
    """
    if not site.get("free_only"):
        return set()
    return _known_admission_ids_for_site(store, (site.get("base_url") or "").rstrip("/"))


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


# A ZOOM TALK IS NOT AT THE J. Peninsula JCC's "Let's Talk About It: Virtual
# Discussion Group" (a Zoom link, 18 of its 200 rows on 2026-10-04) names no
# venue, so the config's venue block pinned every one at the centre. A row that
# says it is online in its own words (looks_online_only, as Meetup and Linked
# Events read it) AND names no street of its own, nor any place but "Online" or
# "Zoom", has nowhere to turn up. A row with a street of its own is kept: the
# words may be about a stream beside the room.
_ONLINE_PLACE_RX = re.compile(r"\b(?:online|virtual|zoom|webinar|live\s*stream)\b", re.I)


def online_with_no_place(ev: Dict[str, Any], name: Optional[str],
                         description: Optional[str]) -> bool:
    if not looks_online_only(name, description):
        return False
    v = _obj(ev.get("venue"))
    if _clean(v.get("address")):
        return False
    place = _clean(v.get("venue"))
    return not place or bool(_ONLINE_PLACE_RX.search(place))


def to_event(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    # NO EVENT STATUS IN THIS API. The Events Calendar's "Canceled"/"Postponed"
    # reaches the event page's JSON-LD (eventStatus, which
    # mapsee_prune_cancelled reads) but not REST v1: its 45 keys on njaudubon.org
    # and gnps.org (2026-10-05) hold none, and `status` is the WordPress post
    # status ("publish"). A title that says CANCELLED is EventStore's to refuse
    # and tombstone (notice_reason), since this key is name|day|venue|city.
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


class SiteEvents(list):
    """The events `ingest_site` read from one site, and `cut`: why it stopped
    before the site's last page, "" when it did not.

    Only OUR clock cuts a site (the deadline, or an interrupt), and a cut site is
    not finished: the next run starts at it (main's cursor). A publisher's own
    failure on a later page (a 500, bad JSON, a page this cannot read) ends the
    site too, keeping the pages before it, but that is the site's answer for
    the day and is not a cut.
    """
    cut = ""


def ingest_site(session, site: Dict[str, Any], admission_ids=frozenset(),
                deadline: float = 0.0,
                stop: Optional[threading.Event] = None) -> SiteEvents:
    """Read one site and return its events. The caller writes them.

    It never touches the store, which is not thread-safe: `admission_ids` is
    `site_admission_ids`, read by the caller first, and `session` is this
    worker's own, because a requests.Session is not thread-safe either.

    `deadline` (epoch seconds, 0 = none) is checked before every page after the
    first, and a page that could not start before it is not waited for: Haydon
    Wick's 15 pages at 30 s are ~7 min of sleeping, which is how a step with an
    80-minute deadline ran 86.9 (2026-10-23). The rows already read are returned,
    with `cut` set. `stop` is set when the run is interrupted, and ends the site
    the same way.
    """
    started = time.monotonic()
    events = SiteEvents()
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        _say(f"[tribe] {site.get('name','?')}: no base_url"); return events
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
    malformed = 0
    governance = 0
    excluded = 0
    admission_excluded = 0
    title_filtered = 0
    online = 0
    cut = ""
    is_civic = str(site.get("_found", "")).startswith("civic:")
    refreshable_admission_ids = admission_ids if site.get("free_only") else frozenset()
    for page in range(1, max_pages + 1):
        if page > 1:
            if deadline and time.time() + delay >= deadline:
                cut = f"stopped at the deadline before page {page}"; break
            if delay:                                      # robots.txt Crawl-delay
                if stop is None:
                    time.sleep(delay)
                elif stop.wait(delay):
                    cut = f"interrupted before page {page}"; break
        if stop is not None and stop.is_set():
            cut = f"interrupted before page {page}"; break
        try:
            r = session.get(api, params=dict(params, page=page), timeout=45)
        except Exception as exc:
            _say(f"[tribe] {site.get('name')} p{page} failed: {exc}"); break
        # The plugin answers a page past the end with 404 + rest_post_invalid_page_number.
        if r.status_code == 404:
            break
        if r.status_code != 200:
            _say(f"[tribe] {site.get('name')} p{page} HTTP {r.status_code}"); break
        try:
            body = r.json()
        except Exception as exc:
            _say(f"[tribe] {site.get('name')} p{page} bad JSON: {exc}"); break
        # A PAGE THIS CANNOT READ ENDS THE SITE, NOT THE PAGES BEFORE IT. Rows
        # were upserted as they came until 2026-10-23, so a page-2 body that is
        # a list or JSON null kept page 1, and a page-2 `total_pages` of "3
        # pages" kept pages 1 and 2 (its rows were in before the int() raised).
        # Now that the events go back to the caller, an exception that left here
        # would take every page with it, under the "FAILED" line that reads like
        # the host was down: the "3 pages" site kept 0 of its 3 rows. Like a 500
        # or bad JSON, it is the site's answer for the day, not a cut: the
        # cursor moves past it.
        try:
            rows = body.get("events") or []
            if not rows:
                break
            for ev in rows:
                # Per EVENT, not per page: the page's own try would end the whole
                # calendar at one unreadable record (see _obj), as main()'s did.
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
                        # Not ev.get: a record that is not an object raises there too.
                        ref = (repr(ev.get("id")) if isinstance(ev, dict)
                               else f"<{type(ev).__name__}>")
                        _say(f"[tribe] {site.get('name')}: skipping unreadable record "
                             f"{ref} ({type(exc).__name__}: {exc})")
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
                if nev and online_with_no_place(ev, nev.name, nev.description):
                    online += 1
                    continue
                if nev:
                    events.append(nev)
            if page >= int(body.get("total_pages") or page):
                break
        except Exception as exc:                           # noqa: BLE001
            _say(f"[tribe] {site.get('name')} p{page} unreadable page: "
                 f"{type(exc).__name__}: {exc}"); break
    _say(f"[tribe] {site.get('name')}: kept {len(events)} events"
         + (f" ({malformed} unreadable record(s) skipped)" if malformed else "")
         + (f" ({governance} town-hall row(s) refused)" if governance else "")
         + (f" ({title_filtered} title-filtered)" if title_filtered else "")
         + (f" ({online} online with no place refused)" if online else "")
         + (f" ({excluded} outside configured categories)" if excluded else "")
         + (f" ({admission_excluded} outside explicit-free admission scope)"
            if admission_excluded else "")
         + (f" ({cut})" if cut else "")
         + f" in {time.monotonic() - started:.1f}s")
    events.cut = cut
    return events


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


def _save_store(store) -> None:
    """store.save(), written beside the file and moved over it.

    The store is the civic group's shared feeds_events.json and is now written
    every SAVE_EVERY_SECONDS: a step killed mid-write must leave the last whole
    copy, not half of one, as `_save_cursor` already does for its own file.
    """
    path = getattr(store, "path", None)
    if path is None:
        store.save()
        return
    final = Path(path)
    tmp = final.with_name(final.name + ".tmp")
    store.path = tmp
    try:
        store.save()
    finally:
        store.path = final
    os.replace(tmp, final)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import 'The Events Calendar' sites into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site name (substring match)")
    # THE STEP'S CLOCK, NOT THIS PROCESS'S — the same deadline `mapsee_sweep_global`
    # takes, and for the reason in docs/agents/ci-and-jobs.md: a store saved only
    # after the loop is lost whole when `timeout-minutes` kills the process first.
    # It was: 532 sites at ~6.8 s is ~60 min against a 60-minute cap, and run
    # 35339055917 was cancelled at exactly 60m00s having reported "kept" for 495 of
    # them — an hour of runner time that wrote nothing, and a red workflow. The
    # workflow stamps an epoch deadline at step start; this starts no further site,
    # and no further page of a site, once it has passed, and the store and cursor
    # are also written every SAVE_EVERY_SECONDS. 0 = no deadline (local runs).
    ap.add_argument("--deadline", type=float, default=0.0,
                    help="epoch seconds; start no site, and no page, at or after this (0 = none)")
    # A DEADLINE WITHOUT A CURSOR STARVES THE SAME TAIL EVERY DAY. Run
    # 36317891716 (2026-09-27) stopped at the deadline "before Autodromo Nazionale
    # di Monza: 65 of 673 sites not started", and because every run began at site
    # one it was the same 65 every night: those calendars were never read at all.
    # With a deadline, the run starts at the site the last one stopped before and
    # walks the list round, so every site is read in turn, the way the ics and
    # jsonld adapters resume. Local runs (no deadline) are unchanged.
    ap.add_argument("--cursor", default=os.environ.get("TRIBE_CURSOR", "tribe_cursor.json"),
                    help="with --deadline: file naming the site to start at")
    ap.add_argument("--workers", type=int, default=os.environ.get("TRIBE_WORKERS", DEFAULT_WORKERS),
                    help="sites read at once, never two on one host (1 = one at a time)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    store = EventStore(a.store)
    total = 0
    sites = [s for s in cfg.get("sites", [])
             if not (a.only and a.only.lower() not in str(s.get("name", "")).lower())]
    done = 0
    stopped = interrupted = False
    if a.deadline and sites:
        start_at = _load_cursor(a.cursor)
        start = next((i for i, s in enumerate(sites) if s.get("base_url") == start_at), 0)
        sites = sites[start:] + sites[:start]
        if start:
            _say(f"[tribe] resuming at {sites[0].get('name','?')} (site {start + 1} of {len(sites)})")
    workers = max(1, a.workers)
    # One request at a time per HOST, as when every site ran in turn: a second site
    # on a busy host waits, and the next site in list order goes instead. A site
    # with no host of its own (no base_url) holds nobody up.
    hosts = [_http_host(s.get("base_url")) or f"#{i}" for i, s in enumerate(sites)]
    local, sessions, stop = threading.local(), [], threading.Event()

    def _read(site: Dict[str, Any], ids) -> tuple:
        t0 = time.monotonic()
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = requests.Session()
            session.headers.update({"User-Agent": UA, "Accept": "application/json"})
            sessions.append(session)
        try:
            return ingest_site(session, site, ids, a.deadline, stop), None, time.monotonic() - t0
        except Exception as exc:                          # noqa: BLE001
            return SiteEvents(), exc, time.monotonic() - t0

    waiting = list(range(len(sites)))     # not started, in list order
    pending: Dict[cf.Future, int] = {}
    busy: set = set()
    finished = [False] * len(sites)
    cut_short = 0
    began = time.monotonic()
    last_save = time.time()
    cursor_path = a.cursor if a.deadline else None

    def _cursor() -> Optional[str]:
        # The first site, in list order, that has not finished: a site after it
        # that did finish is read again next run, which is cheaper than skipping
        # one. A site the deadline cut short has NOT finished (see below). When
        # every site finished, where this run began, so the next run begins there.
        return next((sites[i].get("base_url") for i, f in enumerate(finished) if not f),
                    sites[0].get("base_url") if sites else None)

    pool = cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tribe")
    clean = False
    try:
        while True:
            while waiting and len(pending) < workers and not stopped:
                # Checked BEFORE the site is counted: a site never started must not
                # appear in the "done" line, which is the only line anyone reads.
                if a.deadline and time.time() >= a.deadline:
                    # `::warning::` first, or the runner never annotates it.
                    _say(f"::warning::[tribe] deadline reached before {sites[waiting[0]].get('name','?')}: "
                         f"{len(waiting)} of {len(sites)} sites not started this run")
                    stopped = True
                    break
                k = next((k for k, i in enumerate(waiting) if hosts[i] not in busy), None)
                if k is None:
                    break
                i = waiting.pop(k)
                done += 1
                try:
                    ids = site_admission_ids(store, sites[i])
                except Exception as exc:                  # noqa: BLE001
                    _say(f"[tribe] {sites[i].get('name','?')} FAILED: {exc}")
                    finished[i] = True
                    continue
                busy.add(hosts[i])
                pending[pool.submit(_read, sites[i], ids)] = i
            if not pending:
                break
            ready, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for fut in sorted(ready, key=pending.get):
                i = pending.pop(fut)
                events, error, secs = fut.result()
                cut = "" if error is not None else getattr(events, "cut", "")
                # Written HERE, in the main thread, and before the host is free, so
                # the next site on it sees these rows (site_admission_ids).
                try:
                    if error is not None:
                        raise error
                    for ev in events:
                        store.upsert(ev)
                    total += len(events)
                except Exception as exc:                  # noqa: BLE001
                    _say(f"[tribe] {sites[i].get('name','?')} FAILED: {exc} in {secs:.1f}s")
                # A SITE THE DEADLINE CUT SHORT IS NOT FINISHED. Its rows are written,
                # and the cursor stays on it, so the next run starts there and reads
                # it whole with the step in front of it. Counted as finished, a cut
                # site never held the cursor; once every site had started before the
                # deadline it did not move at all, and the same tail lost its later
                # pages every run (6 sites, 3 workers, 3 runs of 6 s: the same three
                # stopped "before page 3" each time). The one exception is the site
                # this run began at: it HAD the whole step, and holding the cursor
                # for ever would starve the tail, as site one did until 2026-09-27.
                # Today's slowest is ~7.5 min (Haydon Wick, 15 pages at 30 s).
                if cut:
                    cut_short += 1
                finished[i] = not cut or i == 0
                busy.discard(hosts[i])
            if time.time() - last_save >= SAVE_EVERY_SECONDS:
                _save_store(store)
                if cursor_path:
                    _save_cursor(cursor_path, _cursor())
                last_save = time.time()
        clean = True
    except KeyboardInterrupt:
        # A site still reading is not finished, so the cursor stays on it; its
        # rows are not written.
        interrupted = True
        _say("::warning::[tribe] interrupted: saving the sites already read")
    finally:
        if not clean:
            stop.set()                    # the workers stop before their next page
        pool.shutdown(wait=clean, cancel_futures=True)
        if clean:
            for session in sessions:
                session.close()
    _save_store(store)
    if cursor_path:
        _save_cursor(cursor_path, _cursor())
    _say(f"[tribe] done: +{total} events from {done} of {len(sites)} sites "
         f"({workers} at once, {(time.monotonic() - began) / 60:.1f} min); "
         f"store now holds {len(store.records)} unique events."
         + (" (stopped at the step deadline)" if stopped or cut_short else "")
         + (f" ({cut_short} site(s) cut short)" if cut_short else "")
         + (" (interrupted)" if interrupted else ""))
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
