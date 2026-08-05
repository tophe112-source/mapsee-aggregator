#!/usr/bin/env python3
"""
mapsee_migrate_market_ids.py — delete the market rows orphaned when the market
fingerprint scheme changed, keeping the row the CURRENT importer maintains.

WHY THIS EXISTS. A market's `external_id` is its fingerprint, and the events
table is unique on (external_source, external_id) with upsert-on-conflict — so
re-runs update in place and never duplicate. That guarantee holds only while the
fingerprint RECIPE is stable. Commit 7573994 (2026-08-02) changed it:

    -   fp = make_fingerprint(name, ds, place)    # keyed on the ADDRESS
    +   fp = make_fingerprint(name, ds, ident)    # keyed on a ~5km GEOHASH CELL

Every market's key changed, so the first run afterwards (2026-08-04) inserted a
second copy of every market and left the originals stranded under fingerprints
no run will ever generate again. Seattle search showed "Columbia City Farmers
Market" twice, 3.9km apart, on the day of the Play store screenshots.

WHY NOT mapsee_dedupe_markets.py. That job collapses one market imported from
several SOURCES, and picks the survivor by _rank: claimed first, then OLDEST,
"keeping whatever already points at it". After a recipe change the oldest row is
precisely the ORPHAN — the importer no longer knows its fingerprint. Running it
here deletes the live row, keeps the dead one, and the next nightly import
re-inserts the duplicate. It would churn nightly and never converge.

So this job does not guess from distance or age. It asks the importer which
fingerprints it currently emits, and keeps exactly those:

    python mapsee_ingest_markets.py --config market_sources.json --store canon.json
    python mapsee_migrate_market_ids.py --store canon.json            # dry run
    python mapsee_migrate_market_ids.py --store canon.json --apply

--store MUST cover every configured source, and one run cannot do it: sources
are weekday-gated, and OpenStreetMap (Mondays) and USDA (Wednesdays) never run
on the same day. Import into the SAME store on each of those days — EventStore
merges into what is already there — until the source check below passes. A store
holding MORE than the table needs is harmless; one holding less deletes rows the
next import puts straight back.

A row is deleted only when ALL of these hold. Any group failing them is left
untouched and counted in the summary — this job would rather do nothing than
guess:

  • its (normalized title, local date) group holds EXACTLY ONE row whose
    external_id the importer still emits — the keeper. No keeper (a market
    beyond the import horizon, or a source not in --store) means no deletion:
    without one there is nothing to be a duplicate OF;
  • the row's own external_id is NOT one the importer emits;
  • it is within --radius-km of the keeper, so a same-named market in another
    metro on the same day is never swept up by a title match;
  • it is not CLAIMED — a claimer owns that row now (0043).

ONE-SHOT. Once the orphans are gone every market row carries a fingerprint the
importer reproduces, and the nightly upsert keeps it. This is not a cron job;
re-running it after it has converged deletes nothing.

SAFETY. Scoped to `external_source = 'mapsee'` AND `category = 'market'`, so no
user or community event is reachable. Refuses to run if deletions exceed
--max-share of the rows examined, which is the shape a bug in the grouping or a
truncated --store would take. DRY RUN IS THE DEFAULT: it reports and deletes
nothing until you pass --apply.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import normalize_text
from mapsee_dedupe_markets import (BATCH_DEFAULT, _km, _local_date, _timed_out,
                                   delete_ids)

# external_id is what this job reasons about, so it must be selected; the
# COLUMNS in mapsee_dedupe_markets.py omit it.
COLUMNS = "id,title,lat,lon,starts_at,claimed_at,created_at,external_id"
PAGE = 1000
PAGE_MIN = 100


def canonical_fingerprints(store_path: str) -> Set[str]:
    """The market fingerprints the CURRENT importer emits, from its own store.

    Read from the store rather than recomputed here on purpose: recomputing
    would mean a second implementation of the recipe, and the whole failure this
    script cleans up after was two recipes disagreeing.
    """
    try:
        with open(store_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"Couldn't read --store {store_path}: {exc}")
    out: Set[str] = set()
    for rec in data.get("events", []):
        fp = rec.get("fingerprint")
        if not fp:
            continue
        # Only the market adapter labels its sources "market:<config name>". A
        # shared store (feeds_events.json) also holds ICS/JSON-LD/open-data
        # events, and those must not be mistaken for market keepers.
        if any((s.get("source") or "").startswith("market:")
               for s in rec.get("sources", [])):
            out.add(fp)
    return out


def store_sources(store_path: str) -> Set[str]:
    """Which market source labels actually appear in the store."""
    with open(store_path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {s["source"] for rec in data.get("events", [])
            for s in rec.get("sources", [])
            if (s.get("source") or "").startswith("market:")}


def configured_sources(config_path: str) -> Set[str]:
    """The label mapsee_ingest_markets.py gives each configured source."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"Couldn't read --config {config_path}: {exc}")
    srcs = cfg if isinstance(cfg, list) else cfg.get("sources", [])
    # Mirrors `label = "market:" + src["name"].lower().replace(" ", "-")`.
    return {"market:" + s["name"].lower().replace(" ", "-") for s in srcs if s.get("name")}


def fetch_markets(base: str, auth: Dict[str, str], since: Optional[str]
                  ) -> List[Dict[str, Any]]:
    """Every aggregator market row (optionally only upcoming ones), paged.

    Unlike its counterpart in mapsee_dedupe_markets.py this one honours a
    statement timeout instead of exiting: deep offsets over this table return
    57014 readily, and there the exit is swallowed by `|| true` in CI, which
    reads in the log exactly like a run that found nothing to do.
    """
    rows: List[Dict[str, Any]] = []
    page = PAGE
    offset = 0
    while True:
        q = f"{base}&select={COLUMNS}&order=id.asc&limit={page}&offset={offset}"
        if since:
            q += f"&starts_at=gte.{since}"
        r = requests.get(q, headers=auth, timeout=120)
        if _timed_out(r):
            if page <= PAGE_MIN:
                sys.exit(f"Listing times out even at {page} rows/page — "
                         "nothing was deleted. Retry when the table is quieter.")
            page = max(PAGE_MIN, page // 2)
            print(f"Listing timed out — retrying with pages of {page}.")
            continue
        if r.status_code >= 300:
            sys.exit(f"Couldn't list market events [{r.status_code}]: {r.text[:300]}")
        batch = r.json() or []
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page


def plan(rows: List[Dict[str, Any]], canon: Set[str], radius_km: float
         ) -> Tuple[List[Tuple[Dict[str, Any], List[Dict[str, Any]]]], Dict[str, int]]:
    """-> ([(keeper, [orphan, ...]), ...], why-we-skipped counts)."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        title = normalize_text(row.get("title"))
        day = _local_date(row.get("starts_at") or "", row.get("lon"))
        if not title or not day:
            continue                       # not safely identifiable — never touched
        groups.setdefault((title, day), []).append(row)

    out: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    skipped = {"no_keeper": 0, "many_keepers": 0, "claimed": 0, "too_far": 0}
    for group in groups.values():
        keepers = [r for r in group if r.get("external_id") in canon]
        if len(keepers) != 1:
            # 0: beyond the import horizon, or a source missing from --store.
            # 2+: two cells share a title and date — genuinely different markets.
            if len(group) > 1:
                skipped["no_keeper" if not keepers else "many_keepers"] += 1
            continue
        keeper = keepers[0]
        orphans: List[Dict[str, Any]] = []
        for row in group:
            if row is keeper or row.get("external_id") in canon:
                continue                   # never delete a row the importer maintains
            if row.get("claimed_at"):
                skipped["claimed"] += 1
                continue
            if (row.get("lat") is None or row.get("lon") is None
                    or keeper.get("lat") is None or keeper.get("lon") is None):
                skipped["too_far"] += 1    # no coordinates -> cannot prove same place
                continue
            if _km(row["lat"], row["lon"], keeper["lat"], keeper["lon"]) > radius_km:
                skipped["too_far"] += 1
                continue
            orphans.append(row)
        if orphans:
            out.append((keeper, orphans))
    return out, skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Delete market rows orphaned by the fingerprint scheme change.")
    ap.add_argument("--store", required=True,
                    help="Store written by mapsee_ingest_markets.py — the fingerprints "
                         "the importer currently emits.")
    ap.add_argument("--config", default="market_sources.json",
                    help="Market source config, to check --store covers every source "
                         "(default market_sources.json).")
    ap.add_argument("--allow-missing-sources", action="store_true",
                    help="Proceed even when --store is missing a configured source. "
                         "Only safe if that source has no rows in the table.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without it this is a DRY RUN (the default).")
    ap.add_argument("--radius-km", type=float, default=25.0,
                    help="An orphan must be this close to its keeper (default 25). "
                         "Generous on purpose — the drift being cleaned up reached 5km — "
                         "while still refusing to match a same-named market in another metro.")
    ap.add_argument("--include-past", action="store_true",
                    help="Also clean events that have already started "
                         "(default: upcoming only — mapsee_cleanup.py prunes the past).")
    ap.add_argument("--batch", type=int, default=BATCH_DEFAULT,
                    help=f"Ids per delete statement (default {BATCH_DEFAULT}).")
    ap.add_argument("--max-seconds", type=int, default=600,
                    help="Stop deleting after this long; re-run to continue (default 600).")
    ap.add_argument("--max-share", type=float, default=0.6,
                    help="Refuse if more than this share of rows would go (default 0.6). "
                         "A truncated --store looks exactly like this.")
    ap.add_argument("--show", type=int, default=20, help="Examples to print (default 20).")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")

    # SAFETY: imported market events only. Asserted rather than trusted, because
    # everything below appends to this string.
    flt = "external_source=eq.mapsee&category=eq.market"
    assert "external_source=eq.mapsee" in flt and "category=eq.market" in flt, \
        "refusing an unscoped delete"
    base = url.rstrip("/") + "/rest/v1/events?" + flt
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}

    canon = canonical_fingerprints(a.store)
    if not canon:
        sys.exit(f"No market fingerprints in {a.store} — run mapsee_ingest_markets.py "
                 "first. Refusing to treat every row as an orphan.")
    print(f"Fingerprints the importer currently emits: {len(canon)}")

    # A store missing a source is the one way this job destroys good data: every
    # row from that source looks like an orphan, gets deleted, and is re-inserted
    # by the next import that does run it. Not hypothetical — sources are
    # weekday-gated ("run_weekdays"), and OpenStreetMap (Mondays) and USDA
    # (Wednesdays) can never both appear in a store built on one day. Being
    # over-inclusive here is harmless (it only means fewer deletions), so build
    # the store across several days: EventStore loads what is already in the file
    # and merges, so re-running the importer into the SAME --store accumulates.
    missing = configured_sources(a.config) - store_sources(a.store)
    if missing:
        msg = (f"\n{a.store} is missing {len(missing)} configured source(s):\n"
               + "".join(f"    {s}\n" for s in sorted(missing))
               + "Every row from those would look like an orphan and be deleted, then\n"
                 "re-inserted by the next import that runs them — nightly churn.\n"
                 "Run the importer into this SAME store on the days those sources run\n"
                 "(and with any key they need, e.g. USDA_LOCALFOOD_API_KEY); the store\n"
                 "accumulates. Override with --allow-missing-sources only if you know\n"
                 "those sources have no rows in the table.")
        if not a.allow_missing_sources:
            sys.exit("REFUSING: " + msg)
        print("WARNING: " + msg)

    since = None if a.include_past else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_markets(base, auth, since)
    print(f"Imported market events examined: {len(rows)}"
          f"{'' if a.include_past else ' (upcoming only)'}")
    if not rows:
        return 0

    pairs, skipped = plan(rows, canon, a.radius_km)
    doomed = [r for _, orphans in pairs for r in orphans]
    live = sum(1 for r in rows if r.get("external_id") in canon)
    print(f"Rows the importer still maintains: {live}")
    print(f"Groups with an orphan: {len(pairs)}   rows to delete: {len(doomed)}")
    print(f"Left alone — no keeper in --store: {skipped['no_keeper']} group(s); "
          f"several keepers: {skipped['many_keepers']}; "
          f"claimed: {skipped['claimed']} row(s); "
          f"beyond --radius-km: {skipped['too_far']} row(s)")
    if not doomed:
        print("Nothing to do — every market row carries a fingerprint the importer emits.")
        return 0

    for keeper, orphans in pairs[:a.show]:
        print(f"  {keeper.get('title')} — {(keeper.get('starts_at') or '')[:10]}")
        print(f"      keep {keeper['id']}  {keeper.get('external_id')}  "
              f"({(keeper.get('created_at') or '')[:10]})")
        for o in orphans:
            d = _km(o["lat"], o["lon"], keeper["lat"], keeper["lon"])
            print(f"      drop {o['id']}  {o.get('external_id')}  "
                  f"({(o.get('created_at') or '')[:10]}, {d * 1000:.0f}m away)")
    if len(pairs) > a.show:
        print(f"  ... and {len(pairs) - a.show} more group(s)")

    share = len(doomed) / len(rows)
    if share > a.max_share:
        sys.exit(f"\nREFUSING: {len(doomed)}/{len(rows)} rows ({share:.0%}) would go, over "
                 f"the {a.max_share:.0%} limit. A --store that does not cover every market "
                 "source looks exactly like this — check it holds all of them, or raise "
                 "--max-share deliberately.")

    if not a.apply:
        print(f"\nDRY RUN — nothing deleted. {len(doomed)} row(s) would go "
              f"({share:.0%} of those examined). Re-run with --apply to delete.")
        return 0

    gone = delete_ids(base, auth, [r["id"] for r in doomed], a.batch, a.max_seconds)
    print(f"\nDeleted {gone} orphaned market row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
