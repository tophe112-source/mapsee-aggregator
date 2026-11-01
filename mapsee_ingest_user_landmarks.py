#!/usr/bin/env python3
"""
mapsee_ingest_user_landmarks.py — the landmarks mapsee's own members add.

WHAT THIS READS, AND WHY IT IS NOT A TABLE. ../mapsee's Things form has two
groups in its category picker: Classifieds (an item for sale, rent, giving or
lending) and Free — the civic landmarks the Nearby Things filter draws:
bathrooms, drinking water, playgrounds, benches, little free libraries… Until
0250 every pin in the Free group came from OpenStreetMap through
mapsee_ingest_osm_amenities. Now a member standing beside a public toilet OSM
has never heard of can add it, and it lands in a SEPARATE LIST —
public.user_landmarks — that NOTHING on the map reads. This adapter is the only
thing that does, through `landmarks_catalog_feed()`, a service_role-only RPC,
and it writes the catalog row exactly as the OSM adapter writes a fountain:

    a pin_only STANDING row in `events`, external_source 'mapsee',
    external_id 'user-landmark:<id>', the type's own glyph in `icon`,
    recurring_days ALWAYS, the member's text and photo in the description.

So the product treats both alike: amenities_near draws it, the civic card
opens it, ⚑ Report (0004) and the spot rating (0219) apply to it, and
../mapsee's parseImportedDesc reads the attribution line below the way it
reads the ODbL one. "Going forward" means from this run: the contributor sees
the landmark in My listings at once and everybody else after the next run.

THE FEED SAYS WHAT TO CANCEL, BECAUSE AN UPSERT CANNOT DELETE. Every row comes
back with a STATE — live, withdrawn, hidden or banned — and the three that are
not live become `store.cancel()` tombstones here, so the sync hides the catalog
row. The verdict is the database's (0250: at least three ⚠️/reports/poor
ratings and more down than up hides a landmark; two such strikes in 90 days
ban the member and take every landmark of theirs off), and reading the feed is
what folds the catalog row's own reports and ratings into it, which is why the
feed is a function and not a view. The verdict is RE-CHECKED here from the
counts the feed returns (`verdict`), for the reason every OSM adapter re-checks
its Overpass filter in Python: a query is a request, the answer is somebody
else's.

AND THE SHAPES A SCRIPT WRITES ARE REFUSED AGAIN. The posting RPC already
refuses a link or a phone number in a title and more than two links in a
description; `to_event` refuses them a second time, and asks mapsee_spam's
predicate with the source UNKNOWN so the phone rule applies (a member source IS
open registration, whatever its family name says). A refused row is counted
and logged, never written, and never cancelled either — refusing is not a
verdict about the place, only about the text.

NO GEOCODING, NO INVENTED TOWN. A member put the pin where the thing is, so
`coords_exact` is set and city/region/country are left empty rather than
guessed from a hub name — the mistake that moved a Renton restaurant eleven
miles (mapsee_ingest.NormalizedEvent.coords_exact).

ONE SOURCE NAME FOR EVERY ROW ("user-landmark"), with the type in the glyph
and the description, so EventStore.mark_complete has one unit to record and
mapsee_spam.source_family sees a named family whose cancellations are trusted
(an unknown family's are refused as "untrusted", which would leave a withdrawn
landmark on the map for ever).

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY — the feed is service_role only.
Run:  python mapsee_ingest_user_landmarks.py --store user_landmarks_events.json [--dry-run] [--page 500]
      then mapsee_supabase_sync.py --store user_landmarks_events.json --skip-unchanged
      (never --only-new: the description, the photo and the glyph are the member's
      and change when they edit; the sync must rewrite, as osm-amenities.yml does).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from mapsee_ingest import EventStore, NormalizedEvent
from mapsee_spam import spam_reason

SOURCE = "user-landmark"
FEED_RPC = "landmarks_catalog_feed"
PAGE = 500
# A week of "always", the same literal mapsee_ingest_osm_amenities writes on a
# pin: the sync compacts it to the zone alone (its _recurring_hours).
ALWAYS = {str(d): [["00:00", "23:59"]] for d in range(7)}
# The line ../mapsee's parseImportedDesc keys on (`member`), in place of the
# ODbL one. Changing a word here breaks the civic card's "who put this here".
ATTRIBUTION = "Shared by a mapsee member. If it is not here, report it from this card."
IMAGE_BUCKET = "things-images"
_IMAGE_PATH = re.compile(r"^[0-9a-f-]{36}/[0-9a-f-]{36}\.jpg$")
_URL_IN_TITLE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_PHONE_IN_TITLE = re.compile(r"\+\d{7,}|\d{9,}")


class Kind:
    """One landmark type: the lens category it reaches, how it is named, how it is drawn."""

    def __init__(self, category: str, noun: str, glyph: str, secondary: Tuple[str, ...] = ()) -> None:
        self.category, self.noun, self.glyph, self.secondary = category, noun, glyph, list(secondary)


# The keys and glyphs are ../mapsee/site/js/place-categories.js's
# LANDMARK_CATEGORIES, verbatim: the product maps a pin's `icon` back to its
# Free filter type by glyph (landmarkType), so a glyph that drifts is a pin the
# Bathrooms chip can no longer turn off. Categories follow the OSM adapter's
# Kinds for the same thing (a toilet is `outdoors`, a playground `kids`).
KINDS: Dict[str, Kind] = {
    "water": Kind("outdoors", "drinking water point", "🚰"),
    "bathrooms": Kind("outdoors", "public toilet", "🚻"),
    "art": Kind("arts", "public artwork", "🎨"),
    "little_libraries": Kind("learning", "little free library", "📚", ("community",)),
    "dogs": Kind("outdoors", "dog park", "🐕"),
    "playgrounds": Kind("kids", "playground", "🛝", ("outdoors",)),
    "gyms": Kind("fitness", "outdoor gym", "🏋️", ("outdoors",)),
    "repair": Kind("outdoors", "bike repair stand", "🔧"),
    "food": Kind("volunteer", "food bank", "🥫", ("community",)),
    "give": Kind("community", "give box", "🎁", ("market",)),
    "markets": Kind("market", "market", "🧺", ("community",)),
    "parks": Kind("outdoors", "park", "🌳"),
    "benches": Kind("outdoors", "bench", "🪑"),
    "other": Kind("other", "landmark", "📍"),
}
LIVE, WITHDRAWN, HIDDEN, BANNED = "live", "withdrawn", "hidden", "banned"
STATES = (LIVE, WITHDRAWN, HIDDEN, BANNED)


def external_id(landmark_id: str) -> str:
    """The catalog key 0250's landmark_external_id() spells — the one join
    between the two tables, kept identical on both sides and tested on both."""
    return f"{SOURCE}:{landmark_id}"


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN is not a coordinate


def refusal(row: Dict[str, Any]) -> Optional[str]:
    """Why this feed row must not become a catalog row, or None.

    The server refused these shapes at posting time; a row that carries one
    anyway was written by something other than the posting RPC, which is the
    moment to stop trusting the row rather than to write it."""
    if str(row.get("landmark_type") or "") not in KINDS:
        return "unknown landmark type"
    title = _clean(row.get("title"), 100)
    if len(title) < 2:
        return "title too short"
    if _URL_IN_TITLE.search(title):
        return "link in title"
    if _PHONE_IN_TITLE.search(title):
        return "phone number in title"
    description = str(row.get("description") or "")
    if description.lower().count("http") > 2:
        return "description is mostly links"
    lat, lon = _float(row.get("lat")), _float(row.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return "no usable coordinate"
    if lat == 0 and lon == 0:
        return "coordinate is null island"
    if row.get("external_id") not in (None, external_id(str(row.get("id")))):
        return "catalog key differs from the recipe"
    # UNKNOWN source on purpose: the phone-in-title rule counts only from a
    # source anybody can publish to, and a member source is exactly that.
    reason = spam_reason(title, description, source=None)
    if reason:
        return reason
    return None


def verdict(row: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """("upsert", None) or ("cancel", reason) — the feed's state, re-checked.

    The database already applied 0250's rule when it answered; this asks the
    same question of the counts it returned so a row the server called live
    with three down and no up is still cancelled here."""
    state = str(row.get("state") or "")
    if state not in STATES:
        return "cancel", f"unknown state {state!r}"
    if state != LIVE:
        return "cancel", str(row.get("state_reason") or state)
    try:
        up, down = int(row.get("up") or 0), int(row.get("down") or 0)
    except (TypeError, ValueError):
        up, down = 0, 0
    if down >= 3 and down > up:
        return "cancel", "consensus (re-checked)"
    return "upsert", None


def image_url(supabase_url: str, image_path: Any) -> Optional[str]:
    """The public URL of a member's photo, or None for anything but the one
    path shape the bucket policy allows — a path the policy would refuse is
    not one this adapter should turn into a link."""
    path = str(image_path or "")
    if not path or not _IMAGE_PATH.match(path) or not supabase_url:
        return None
    return f"{supabase_url.rstrip('/')}/storage/v1/object/public/{IMAGE_BUCKET}/{path}"


def to_event(row: Dict[str, Any], supabase_url: str = "", today: Optional[datetime] = None) -> Optional[NormalizedEvent]:
    """One feed row as the catalog row it becomes. None when refusal() says so.

    Built identically for a live row and for one to cancel: EventStore.cancel
    finds the row by this fingerprint and nothing else."""
    if refusal(row) is not None:
        return None
    kind = KINDS[str(row["landmark_type"])]
    landmark_id = str(row["id"])
    title = _clean(row.get("title"), 100)
    place = _clean(row.get("place_name"), 120)
    text = re.sub(r"\n{3,}", "\n\n", str(row.get("description") or "").strip())[:2000]
    # The same shape the OSM adapter writes, so ../mapsee's amenityProse strips
    # the opener and the attribution and measures the member's own words:
    # "<title> — <noun>." then the text, then who put it here.
    head = f"{title} — {kind.noun}." if title.lower() != kind.noun.lower() else f"{title}."
    description = head + ("\n\n" + text if text else "") + "\n\n" + ATTRIBUTION
    day = (today or datetime.now(timezone.utc)).date()
    opens, closes = ALWAYS["0"][0]
    return NormalizedEvent(
        source=SOURCE,
        source_id=landmark_id,
        fingerprint=external_id(landmark_id),
        name=title,
        description=description,
        start_local=f"{day.isoformat()}T{opens}:00",
        end_local=f"{day.isoformat()}T{closes}:00",
        venue_name=place or None,
        latitude=_float(row.get("lat")), longitude=_float(row.get("lon")),
        category=kind.category,
        categories=list(kind.secondary),
        poster_image_url=image_url(supabase_url, row.get("image_path")),
        coords_exact=True,
        recurring_days=dict(ALWAYS),
        # A landmark is drawn, and what it carries decides whether its pin
        # opens — the OSM adapter's rule, ../mapsee 0194/0195. It is never a
        # Nearby listing: nobody plans a day around a bench.
        pin_only=True,
        icon=kind.glyph,
    )


# ---------------------------------------------------------------------------
# Reading the feed
# ---------------------------------------------------------------------------
class FeedError(RuntimeError):
    """The feed could not be read whole. Nothing read so far is discarded, but
    nothing is marked complete and the run exits non-zero."""


def feed_pages(fetch: Callable[[Optional[str], int], Any], page: int = PAGE) -> Iterator[List[Dict[str, Any]]]:
    """Walk the keyset-paged feed: `fetch(after_id, limit)` answers one page,
    ordered by id, and a short page ends the walk. A page that does not
    advance the cursor would loop for ever, so it is a FeedError instead."""
    after: Optional[str] = None
    seen_pages = 0
    while True:
        rows = fetch(after, page)
        if not isinstance(rows, list):
            raise FeedError(f"feed answered {type(rows).__name__}, not a list")
        seen_pages += 1
        if rows:
            last = str(rows[-1].get("id") or "")
            if not last or last == after:
                raise FeedError("feed page did not advance the cursor")
            yield rows
            after = last
        if len(rows) < page:
            return
        if seen_pages > 10000:
            raise FeedError("feed did not end after 10,000 pages")


def postgrest_fetch(url: str, key: str, session=None, sleep=time.sleep) -> Callable[[Optional[str], int], Any]:
    """The real feed: one POST to the RPC per page, three tries on a 5xx or a
    network error, a 4xx final (a 404 is the migration not applied yet, and
    retrying it is noise)."""
    import requests
    sess = session or requests.Session()
    endpoint = url.rstrip("/") + f"/rest/v1/rpc/{FEED_RPC}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": "mapsee-aggregator/1.0 (+https://mapsee.me; member landmarks)"}

    def fetch(after: Optional[str], limit: int) -> Any:
        body = {"p_after": after, "p_limit": limit}
        last_error = "no attempt"
        for attempt in range(1, 4):
            try:
                r = sess.post(endpoint, json=body, headers=headers, timeout=60)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 200:
                    return r.json()
                last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                if 400 <= r.status_code < 500:
                    raise FeedError(f"{FEED_RPC}: {last_error}")
            if attempt < 3:
                sleep(2 * attempt)
        raise FeedError(f"{FEED_RPC}: {last_error}")
    return fetch


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def ingest(rows: Iterator[Dict[str, Any]], store: Optional[EventStore], supabase_url: str,
           log=print, today: Optional[datetime] = None) -> Dict[str, Any]:
    """Upsert the live rows, tombstone the rest, refuse the rest of the rest.
    Returns the counts the summary line prints; `store` None is a dry run."""
    counts: Dict[str, Any] = {"read": 0, "upserted": 0, "cancelled": 0, "refused": 0,
                              "by_state": {}, "refusals": {}, "cancel_results": {}}
    for row in rows:
        counts["read"] += 1
        why = refusal(row)
        if why is not None:
            counts["refused"] += 1
            counts["refusals"][why] = counts["refusals"].get(why, 0) + 1
            log(f"[{SOURCE}] refused {row.get('id')}: {why}: {_clean(row.get('title'), 60)!r}")
            continue
        event = to_event(row, supabase_url, today)
        action, reason = verdict(row)
        state = str(row.get("state") or "?")
        counts["by_state"][state] = counts["by_state"].get(state, 0) + 1
        if action == "upsert":
            counts["upserted"] += 1
            if store is not None:
                store.upsert(event)
            else:
                log(f"[{SOURCE}] would upsert {event.fingerprint}: {event.icon} {event.name!r} at {event.latitude},{event.longitude}")
        else:
            counts["cancelled"] += 1
            if store is not None:
                result = store.cancel(event, reason or state)
                counts["cancel_results"][result] = counts["cancel_results"].get(result, 0) + 1
            else:
                log(f"[{SOURCE}] would cancel {event.fingerprint} ({reason})")
    return counts


def summary(counts: Dict[str, Any], dry_run: bool) -> str:
    states = ", ".join(f"{k} {v}" for k, v in sorted(counts["by_state"].items()))
    refusals = ", ".join(f"{k} {v}" for k, v in sorted(counts["refusals"].items()))
    line = (f"[{SOURCE}] {'dry run — ' if dry_run else ''}{counts['read']} row(s) read: "
            f"{counts['upserted']} upserted, {counts['cancelled']} cancelled, {counts['refused']} refused")
    if states:
        line += f" (states: {states})"
    if refusals:
        line += f" (refusals: {refusals})"
    if counts.get("cancel_results"):
        line += " (tombstones: " + ", ".join(f"{k} {v}" for k, v in sorted(counts["cancel_results"].items())) + ")"
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import the landmarks mapsee members add, as standing map pins.")
    ap.add_argument("--store", default="user_landmarks_events.json")
    ap.add_argument("--page", type=int, default=PAGE, help="rows per feed page (1-1000)")
    ap.add_argument("--dry-run", action="store_true", help="read the feed and print what would be written; write nothing")
    a = ap.parse_args(argv)

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        # Loud, not a friendly skip: a scheduled run that printed "nothing to do"
        # for want of a secret is the parkrun config that was never committed.
        print(f"[{SOURCE}] SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required: the feed is service_role only",
              file=sys.stderr, flush=True)
        return 2
    page = max(1, min(int(a.page or PAGE), 1000))
    store = None if a.dry_run else EventStore(a.store)
    fetch = postgrest_fetch(url, key)

    def rows() -> Iterator[Dict[str, Any]]:
        for chunk in feed_pages(fetch, page):
            yield from chunk

    try:
        counts = ingest(rows(), store, url)
    except FeedError as exc:
        print(f"[{SOURCE}] feed read FAILED: {exc}", file=sys.stderr, flush=True)
        if store is not None:
            # What was read is good; what was not read cannot be cancelled by
            # absence, because nothing is marked complete.
            store.save()
        return 1
    if store is not None:
        today = datetime.now(timezone.utc)
        store.mark_complete(SOURCE, today, today + timedelta(days=8))
        store.save()
    print(summary(counts, a.dry_run), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
