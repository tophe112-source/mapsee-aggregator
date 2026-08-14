#!/usr/bin/env python3
"""
mapsee_retire_perday_osm.py — hide the OLD per-day OSM restaurant rows.

WHY. Until 2026-08-13 the OpenStreetMap adapter wrote one row per open day:
measured, 6.1 rows per venue. ../mapsee 0156 replaced that with ONE rolling row
per venue carrying its weekly pattern, and the new rows use a new fingerprint —
so they are INSERTED alongside the old ones rather than replacing them. Left
alone the old rows age out as their windows pass, roughly a week of both models
on the map at once, during which restaurants keep crowding the Nearby list
because 0158 cannot demote them: is_standing reads recurring_hours, and the old
rows have none.

So this retires them early. hidden_at, not DELETE: hiding is reversible, and a
restaurant is somebody's business rather than a row we happen to own.

THE SAFETY RULE, and it is the whole design: an old row is hidden ONLY when a
replacement standing row exists at the same venue key. Without that check a
venue whose new row failed to import — a fetch that 504'd, a link that went
dead, a place the cursor has not reached yet — would simply vanish from the map,
and the sweep that was supposed to replace it is exactly the thing that might
not have finished.

Never touches:
  * a CLAIMED row. Once a venue owns its listing the row is theirs.
  * anything without the OpenStreetMap attribution line. `external_source =
    'mapsee'` is every adapter in this repo, not this one.
  * a row that already carries recurring_hours — that IS the new model.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_retire_perday_osm.py                 # dry run, the default
      python mapsee_retire_perday_osm.py --apply
      python mapsee_retire_perday_osm.py --apply --unhide # put them back
"""
import argparse
import collections
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PAGE = 500
OSM_MARK = "OpenStreetMap contributors"


def sb(path, method="GET", body=None, prefer=""):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("content-type", "application/json")
    if prefer:
        req.add_header("prefer", prefer)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None


def key(lat, lon):
    """venue_key's 4dp cluster, computed the same way the database does."""
    return f"{round(float(lat), 4):.4f},{round(float(lon), 4):.4f}"


def collapse_orphans(by_venue):
    """Rows to hide so each venue keeps exactly ONE per-day row: the soonest.

    Pure, and separated from main() for one reason — the property that matters
    is "this can never hide the last row at a venue", and that is worth a test
    rather than a careful read. A venue with a single row is returned empty, and
    a missing starts_at sorts LAST so a row with no time cannot win the keep slot
    by accident.
    """
    out = []
    for rows in by_venue.values():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda r: r.get("starts_at") or "9999")
        out.extend(ordered[1:])
    return out


def scan(q, label, days, back, max_pages):
    """Walk time windows, as mapsee_reclassify does — deep OFFSET dies here."""
    now = time.time()
    t = now - back * 86400
    step = 86400 * 2
    out, pages, skipped = [], 0, 0
    while t < now + days * 86400 and pages < max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + days * 86400)))
        offset = 0
        while True:
            url = f"{q}&starts_at=gte.{w_a}&starts_at=lt.{w_b}&limit={PAGE}&offset={offset}"
            batch = None
            for attempt in range(3):
                try:
                    batch = sb(url) or []
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  window {w_a[:10]} failed 3x ({e})", file=sys.stderr)
                        skipped += 1
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if not batch:
                break
            out.extend(batch)
            if len(batch) < PAGE:
                break
            offset += PAGE
        pages += 1
        t += step
    print(f"  {label}: {len(out)} rows" + (f"  ({skipped} windows errored)" if skipped else ""))
    return out, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--unhide", action="store_true",
                    help="reverse: un-hide rows this pass hid")
    ap.add_argument("--collapse", action="store_true",
                    help="at venues with NO standing replacement, keep only the soonest "
                         "per-day row and hide the rest (the venue stays on the map once)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--back", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=40)
    args = ap.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    mark = urllib.parse.quote(f"*{OSM_MARK}*")
    base = "events?select=id,title,lat,lon,starts_at,claimed_by&external_source=eq.mapsee"

    # The NEW model: one rolling row per venue. These are the replacements, and
    # their existence is what makes retiring an old row safe.
    #
    # NOT time-windowed, unlike the old rows. There is one of these per venue
    # rather than one per open day, so the whole set is small — and windowing it
    # by starts_at defeats ../mapsee 0156's partial index, which is keyed on
    # ends_at. Measured against production: the windowed form took 0.77s and
    # timed out (57014) on the attempt before that; ordered by ends_at to match
    # the index it takes 0.22s. A safety check that intermittently fails is
    # worse than no safety check, because it fails by hiding LESS and looking
    # fine.
    new_rows, offset = [], 0
    while True:
        batch = sb(f"{base}&recurring_hours=not.is.null&hidden_at=is.null"
                   f"&order=ends_at.asc&limit={PAGE}&offset={offset}") or []
        new_rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
        if offset > 100000:
            print("  standing rows exceeded 100k — refusing to page further", file=sys.stderr)
            break
    print(f"  standing rows (new model): {len(new_rows)} rows")
    replaced = {key(r["lat"], r["lon"]) for r in new_rows if r.get("lat") is not None}

    if args.unhide:
        old, _ = scan(f"{base}&recurring_hours=is.null&hidden_at=not.is.null"
                      f"&description=ilike.{mark}",
                      "hidden per-day rows", args.days, args.back, args.max_pages)
        print(f"\n  would un-hide {len(old)} rows")
        if not args.apply:
            print("  DRY RUN — re-run with --apply")
            return 0
        n = 0
        for r in old:
            try:
                sb(f"events?id=eq.{r['id']}", "PATCH", {"hidden_at": None}, prefer="return=minimal")
                n += 1
            except Exception as e:
                print(f"    {r['id']} failed: {e}", file=sys.stderr)
        print(f"  un-hid {n}")
        return 0

    old, skipped = scan(f"{base}&recurring_hours=is.null&hidden_at=is.null"
                        f"&description=ilike.{mark}",
                        "per-day OSM rows (old model)", args.days, args.back, args.max_pages)

    # ORPHANS: per-day rows at a venue the new model has not reached. The rule
    # above will not hide them, and it is right not to — a venue with no standing
    # row would vanish. But leaving them ALL is the bug the user actually sees:
    # Top Pot Doughnuts, five identical rows one day apart, none of them carrying
    # recurring_hours and no replacement anywhere in the table. Confirmed live on
    # 2026-08-14 at (47.6099, -122.3239): rows for the 12th through the 16th, not
    # one of them standing.
    #
    # So --collapse keeps the SOONEST future row at each such venue and hides the
    # rest. The venue stays on the map exactly once, which is the rule 0156 set
    # out to enforce; the safety property is untouched, because the thing that
    # rule protects against is a venue disappearing and this can never remove the
    # last row. Off by default: hiding a row somebody can still see is a decision,
    # not a tidy-up.
    hide, orphan, claimed = [], collections.Counter(), 0
    by_venue = collections.defaultdict(list)
    for r in old:
        if r.get("claimed_by"):
            claimed += 1
            continue                      # the venue owns this listing
        if r.get("lat") is None:
            continue
        k = key(r["lat"], r["lon"])
        if k in replaced:
            hide.append(r)
        else:
            orphan[r.get("title") or "?"] += 1
            by_venue[k].append(r)

    collapsed = 0
    if args.collapse:
        extra = collapse_orphans(by_venue)
        hide.extend(extra)
        collapsed = len(extra)

    print(f"\n  venues with a standing replacement : {len(replaced)}")
    print(f"  old rows to hide                   : {len(hide)}")
    print(f"  old rows KEPT (no replacement yet)  : {sum(orphan.values())}"
          f"  across {len(orphan)} venues")
    if args.collapse:
        print(f"  ...of which collapsed to one each  : {collapsed} hidden, "
              f"{len(by_venue)} venues keep their soonest row")
    print(f"  skipped, claimed by a venue        : {claimed}")
    if orphan:
        print("  kept, most rows first:")
        for name, n in orphan.most_common(8):
            print(f"    {n:4}  {name[:44]}")
    if skipped:
        print(f"\n  INCOMPLETE: {skipped} window(s) errored — rows in them were not examined")

    if not hide:
        print("\n  nothing to hide")
        return 0
    if not args.apply:
        print("\n  DRY RUN — nothing written. Re-run with --apply.")
        return 0

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    n = 0
    for r in hide:
        try:
            sb(f"events?id=eq.{r['id']}", "PATCH", {"hidden_at": stamp}, prefer="return=minimal")
            n += 1
        except Exception as e:
            print(f"    {r['id']} failed: {e}", file=sys.stderr)
    print(f"\n  hidden {n} of {len(hide)} (hidden_at set; --unhide reverses it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
