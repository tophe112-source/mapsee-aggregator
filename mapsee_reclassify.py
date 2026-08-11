#!/usr/bin/env python3
"""
mapsee_reclassify.py — re-run the classifier over rows already in the table.

WHY. A classifier fix only reaches events ingested AFTER it. `--only-new` is the
default in CI, so a scheduled run can only ADD; Wednesday's run drops the flag
and re-reads the sources, which is the one time a change at the SOURCE reaches
the map — but a change in OUR rules never does, because those rows are not new.

So the yoga-in-food fix (2026-08-12) corrected the future and left the past.
Measured at the time: of 1,000 upcoming food-classified events, only 16% carried
a food word in the title, and yoga, pilates, tai chi, qigong, zumba and karate
were sitting in the food bucket where the Order pickup button used to render.

WHAT IT WILL NOT TOUCH.
  * anything not imported by this pipeline (external_source <> 'mapsee') — a
    community event someone typed in is theirs, and its category is their choice
  * a CLAIMED row — same rule mapsee_link_series.py follows: once a venue owns a
    listing we stop rewriting it underneath them
  * a row whose recomputed primary equals the stored one (the vast majority)

DRY RUN BY DEFAULT, and it prints the TRANSITION TABLE before it writes
anything. Read that table. Re-running a classifier over live rows is exactly the
kind of sweep that looks fine in aggregate and is wrong in the particular, and
the table is the only place that shows up before it is a support ticket.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_reclassify.py                          # dry run, shows the table
      python mapsee_reclassify.py --only food --days 400   # the food bucket, all of it
      python mapsee_reclassify.py --apply --allow food->fitness --days 400

--apply REFUSES to run without --allow. See the note on that argument.
"""
import argparse
import collections
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mapsee_supabase_sync import derive_categories

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PAGE = 500


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


def recompute(row):
    """The stored row, shaped the way derive_categories expects.

    NOTE the input is the row's CURRENT category, not the source's original one —
    that is not kept. This is therefore a re-run of the PROMOTION rules over an
    already-classified row, which is precisely what the fix needs: the stored key
    says food, the title says yoga, and the rule that was missing now fires.
    """
    return derive_categories({
        "name": row.get("title") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "categories": row.get("categories") or [],
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--only", help="only rows currently in this category")
    ap.add_argument("--days", type=int, default=120, help="how far ahead to sweep")
    ap.add_argument("--max-pages", type=int, default=60)
    # --allow is REQUIRED to write, and it is the whole safety story.
    #
    # Re-running the classifier over old rows does not only replay the rule you
    # just fixed; it replays every rule that has ever been added, against rows
    # that predate all of them. The first full dry run made that concrete: 80
    # food -> fitness (the intended fix) arrived alongside "MAG's Neighborhood of
    # the Arts Block Party" -> fitness and "Killer Core" -> volunteer, both of
    # which are the description-reading rules firing on prose that is not about
    # what the event IS. Those are arguable changes at best and wrong at worst,
    # and they have nothing to do with the fix being backfilled.
    #
    # So a sweep writes only the transitions an operator has named and read.
    ap.add_argument("--allow", action="append", default=[],
                    help="transition to write, e.g. --allow food->fitness (repeatable)")
    args = ap.parse_args()
    allowed = {a.strip().replace(" ", "") for a in args.allow}
    if args.apply and not allowed:
        print("refusing to write without --allow. Run the dry run, read the transition\n"
              "table, then name the transitions you want, e.g. --allow food->fitness",
              file=sys.stderr)
        return 2

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    now = time.time()
    a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    b = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + args.days * 86400))

    moves = collections.Counter()
    skipped_windows = []
    examples = collections.defaultdict(list)
    scanned = changed = written = 0
    held = collections.Counter()

    # Walk in time windows rather than by OFFSET: deep offsets on this table die
    # under the statement timeout (../mapsee 0147 header has the same note).
    step = 86400 * 2
    t = now
    pages = 0
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(t + step, now + args.days * 86400)))
        q = ("events?select=id,title,description,category,categories,claimed_by"
             "&external_source=eq.mapsee&is_private=eq.false&hidden_at=is.null"
             f"&starts_at=gte.{w_a}&starts_at=lt.{w_b}&order=starts_at.asc&limit={PAGE}")
        if args.only:
            q += f"&category=eq.{urllib.parse.quote(args.only)}"
        # PAGINATE WITHIN THE WINDOW. Without this the sweep silently SAMPLES: a
        # busy two-day window holds far more than PAGE rows and the rest are
        # never examined. Caught by two dry runs disagreeing — `--only food` over
        # 30 days found 92 changes while an all-category pass over 10 days found
        # 4, because there the food rows were competing with every other category
        # for the same 500 slots. A sweep that quietly skips rows reports "done"
        # and leaves the bug in, which is the worst outcome available to it.
        rows = []
        offset = 0
        while True:
            batch = None
            for attempt in range(3):
                try:
                    batch = sb(q + f"&offset={offset}") or []
                    break
                except Exception as e:
                    # A 500 here is the statement timeout, and it is transient
                    # often enough to be worth two more tries — the audit-vs-
                    # candidate lesson in CLAUDE.md: a failure on something we
                    # will otherwise SKIP deserves a second look, because
                    # skipping it silently is the expensive outcome.
                    if attempt == 2:
                        print(f"  window {w_a[:10]} offset {offset} failed 3x ({e})",
                              file=sys.stderr)
                        skipped_windows.append(w_a[:10])
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if batch is None:
                break
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
            if len(batch) < PAGE:
                break
            offset += PAGE
            if offset > 20000:
                print(f"  window {w_a[:10]} exceeded 20k rows; shorten --days", file=sys.stderr)
                skipped_windows.append(w_a[:10])
                break
        pages += 1

        for row in rows:
            scanned += 1
            if row.get("claimed_by"):
                continue                                  # a venue owns this listing
            old_primary = row.get("category") or ""
            new_primary, new_extras = recompute(row)
            if new_primary == old_primary:
                continue
            changed += 1
            key = f"{old_primary} -> {new_primary}"
            moves[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(row.get("title") or "")
            if args.apply:
                if key.replace(" ", "") not in allowed:
                    held[key] += 1
                    continue
                sb(f"events?id=eq.{row['id']}", "PATCH",
                   {"category": new_primary, "categories": new_extras},
                   prefer="return=minimal")
                written += 1
        t += step

    print(f"\n  scanned {scanned} aggregator rows over {args.days} days · "
          f"{changed} would change" + ("" if args.apply else "  (DRY RUN — nothing written)"))
    if skipped_windows:
        # Never let an incomplete sweep read as a finished one. The repo's own
        # lesson: a green run that quietly examined less than it claims is worse
        # than a red one, because nobody goes back to it.
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) errored, rows in them "
              f"were not examined: {', '.join(skipped_windows[:6])}")
    if args.apply:
        print(f"  written {written}")
        if held:
            print("  held back (not named in --allow):")
            for k, n in held.most_common():
                print(f"    {n:>5}  {k}")
    if moves:
        print("\n  transition table:")
        for key, n in moves.most_common():
            print(f"    {n:>5}  {key}")
            for ex in examples[key]:
                print(f"           {ex[:66]}")
    if not args.apply and changed:
        print("\n  Read the table above. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
