#!/usr/bin/env python3
"""
mapsee_meetup_category_cache.py - hold each Meetup event's FIRST category guess
across runs, so a row stops changing door every day.

WHY. A Meetup row's category is a guess: the keyword whose eventSearch results
held it (mapsee_ingest_meetup DEFAULT_KEYWORDS, "social:community",
"dance:party", ...). Within a run the first (metro, keyword) pair to return an
event decides, because the store fills an empty category and never overwrites
one. But both Meetup stores are rebuilt from nothing every run, and which pair
comes first moves from day to day: eventSearch is capped at 200 per keyword per
metro (NYC answered 9,855 hits for 1,656 events on 10-26, so the keywords
overlap and the top 200 drift), and the international leg starts at a different
country every day (mapsee_sweep_global.rotate_countries). Measured from the
sync's own blame lines, 10-22..10-26: category differed on 4,408-4,818 stored
rows a day on the US leg and 4,146-4,569 on the international one, about 20%
of the ~21-23k compared, and each of those UPDATEs carried ends_at (the default
end time is per category, blamed 2,483-2,910) and the music-only Listen line
(1,613-1,796). Re-sending the same store blames ~0 (10-24: the international
sync compared 48,482 rows, 22.7k of them US rows written 3.5 h earlier, and
blamed 4,486), so the flips are day to day and this is where they stop.

WHAT IT DOES, once per leg, between that leg's sweep and its sync: a cache file
maps fingerprint -> [category, event date]. An event already in it gets its
held category back; an event not in it is added with today's guess. Entries
whose date is before yesterday (UTC) are pruned, so the file never outlives the
sweep's 90-day window. The fingerprint carries the event's date, so a moved
event is a new entry.

WHAT IT TOUCHES: events[*].category in the store, and the cache file. Records
are never added or removed; tombstones, seen, complete_reads and _meta are
written back exactly as read (a tombstone is what keeps a cancelled event from
being imported again, and a hidden row hidden). The store is rewritten only
when a category actually changes, the cache always; both through a temporary
file and os.replace, so a kill mid-write leaves the last good copy.

VERSIONED BY THE KEYWORD LIST: the cache carries the sha1 of the parsed
(keyword, category) list, and any edit to DEFAULT_KEYWORDS starts it empty. A
remapped keyword ("dance:party" -> "dance:fitness") then reaches every event the
next day instead of after the 90 days its old guesses would have been held. A
held category that is not one of the list's own is ignored too.

REPORT ONLY BY DEFAULT IN CI: the workflow passes --report-only unless the
repository variable MEETUP_STICKY_CATEGORIES is 'true'. A report still writes
the cache, keeping first sightings, so it measures what holding would change:
how many events today's guess moves off their held category, the top
today->held pairs, and the net rows each door would gain or lose. Holding
freezes the FIRST sighting, which is found far out and so plausibly by a narrow
keyword late in the list (singles:party, yoga:fitness, storytime:kids), and the
sync guards only kids, market and volunteer guesses (_refused_sweep_guess):
read the net line for party, fitness and food before turning it on.

ANY ERROR is a ::warning:: and exit 0. The sync after it goes ahead with the
store as it stands: a missing or corrupt cache costs one day of flips, never a
sync.

Run:  python mapsee_meetup_category_cache.py --store meetup_events.json --cache meetup_category_cache.json --report-only
      python mapsee_meetup_category_cache.py --store meetup_events.json --cache meetup_category_cache.json
Env:  MAPSEE_TODAY=YYYYMMDD fixes "today", as everywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from mapsee_ingest_meetup import DEFAULT_KEYWORDS, parse_keywords

# The sweep's --within-days. A record with no readable date is held this long,
# which is as far out as the sweep could have found it.
HORIZON_DAYS = 90
TOP_PAIRS = 8
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def keyword_version(keywords: Optional[List[Tuple[str, str]]] = None) -> str:
    """sha1 of the parsed (keyword, category) list the sweep runs with."""
    kw = parse_keywords(DEFAULT_KEYWORDS) if keywords is None else keywords
    return hashlib.sha1(json.dumps([list(k) for k in kw], ensure_ascii=False)
                        .encode("utf-8")).hexdigest()


def _today() -> date:
    """Today in UTC. MAPSEE_TODAY=YYYYMMDD fixes it, as everywhere."""
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed and re.fullmatch(r"\d{8}", fixed):
        return date(int(fixed[:4]), int(fixed[4:6]), int(fixed[6:8]))
    return datetime.now(timezone.utc).date()


def write_atomic(path: str, payload: Any, indent: Optional[int] = None) -> None:
    """json to `path` through `path`.tmp and os.replace; on any failure the
    temporary file is removed and `path` is what it was."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            if indent is None:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(payload, fh, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_cache(path: str, version: str) -> Tuple[Dict[str, List[str]], Optional[str]]:
    """-> (entries, why they start empty, or None). Never raises: an unreadable
    cache is an empty one, which costs a day of flips and nothing else."""
    if not os.path.exists(path):
        return {}, "no cache yet (a first run, or evicted): every event is a first sighting today"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return {}, f"::warning::{path} could not be read ({exc}); starting it empty"
    if not isinstance(data, dict):
        return {}, f"::warning::{path} is not a cache; starting it empty"
    if data.get("version") != version:
        return {}, "the keyword list changed since the cache was written; starting it empty"
    raw = data.get("entries")
    if not isinstance(raw, dict):
        return {}, f"::warning::{path} has no entries map; starting it empty"
    entries = {fp: [v[0], v[1]] for fp, v in raw.items()
               if isinstance(fp, str) and fp and isinstance(v, list) and len(v) == 2
               and isinstance(v[0], str) and isinstance(v[1], str) and _DAY.match(v[1])}
    return entries, None


def hold_first_categories(store_path: str, cache_path: str, today: Optional[date] = None,
                          apply: bool = False,
                          keywords: Optional[List[Tuple[str, str]]] = None) -> Dict[str, Any]:
    """Hold (apply) or measure (report) each stored event's first category.
    -> the counts printed. Raises on a store it cannot read; the store is then
    untouched (main turns that into a ::warning::)."""
    today = today or _today()
    kw = parse_keywords(DEFAULT_KEYWORDS) if keywords is None else keywords
    version = keyword_version(kw)
    valid = {cat for _, cat in kw} | {"community"}
    counts: Dict[str, Any] = {"events": 0, "held": 0, "changed": 0, "new": 0, "pruned": 0,
                              "entries": 0, "pairs": Counter(), "net": Counter(),
                              "store_written": False, "cache_written": False}
    if not os.path.exists(store_path):
        print(f"Meetup category cache: no {store_path} - nothing to hold.")
        return counts
    with open(store_path, encoding="utf-8") as fh:
        store = json.load(fh)
    events = store.get("events") if isinstance(store, dict) else None
    if not isinstance(events, list):
        raise ValueError(f"{store_path} has no events list")

    entries, note = load_cache(cache_path, version)
    if note:                                        # a ::warning:: is printed as itself
        print(note if note.startswith("::") else f"Meetup category cache: {note}")
    cutoff = (today - timedelta(days=1)).isoformat()
    for fp in [fp for fp, (_, day) in entries.items() if day < cutoff]:
        del entries[fp]
        counts["pruned"] += 1
    default_day = (today + timedelta(days=HORIZON_DAYS)).isoformat()

    pairs, net = counts["pairs"], counts["net"]
    for rec in events:
        fp = rec.get("fingerprint") if isinstance(rec, dict) else None
        if not isinstance(fp, str) or not fp:
            continue
        counts["events"] += 1
        current = rec.get("category")
        hit = entries.get(fp)
        if hit and hit[0] in valid:
            counts["held"] += 1
            if current != hit[0]:
                was = current if isinstance(current, str) and current else "-"
                counts["changed"] += 1
                pairs[(was, hit[0])] += 1
                net[hit[0]] += 1
                net[was] -= 1
                if apply:
                    rec["category"] = hit[0]        # the ONE field this ever writes
        elif isinstance(current, str) and current in valid:
            day = str(rec.get("start_local") or "")[:10]
            day = day if _DAY.match(day) else default_day
            if day >= cutoff:
                entries[fp] = [current, day]
                counts["new"] += 1
    counts["entries"] = len(entries)

    # The store first: it is what today's sync reads. A cache write that fails
    # after it costs only today's new sightings.
    if apply and counts["changed"]:
        write_atomic(store_path, store, indent=2)   # EventStore.save's own layout
        counts["store_written"] = True
    write_atomic(cache_path, {"version": version, "entries": entries})
    counts["cache_written"] = True

    top = ", ".join(f"{a}->{b} x{n:,}" for (a, b), n in pairs.most_common(TOP_PAIRS)) or "none"
    print(f"Meetup category cache{'' if apply else ' (report only)'}: {counts['changed']:,} of "
          f"{counts['events']:,} stored events {'changed' if apply else 'would have changed'} category "
          f"({counts['held']:,} already held; top today->held: {top}); {counts['new']:,} new; "
          f"{counts['pruned']:,} pruned; {counts['entries']:,} entries")
    moves = sorted(((c, n) for c, n in net.items() if n), key=lambda cn: (-abs(cn[1]), cn[0]))
    if moves:
        print(f"  net rows per door {'held' if apply else 'if held'}: "
              + ", ".join(f"{c} {n:+,}" for c, n in moves))
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hold each Meetup event's first category across runs.")
    ap.add_argument("--store", required=True, help="a Meetup leg's store (meetup_events.json, ...)")
    ap.add_argument("--cache", default="meetup_category_cache.json")
    ap.add_argument("--report-only", action="store_true",
                    help="measure and keep the cache, but leave the store's categories as swept")
    a = ap.parse_args(argv)
    try:
        hold_first_categories(a.store, a.cache, apply=not a.report_only)
    except Exception as exc:                          # never the sync's problem
        print(f"::warning::Meetup category cache could not finish on {a.store} "
              f"({type(exc).__name__}: {exc}); its sync goes ahead, and the cache keeps its last "
              f"good copy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
