#!/usr/bin/env python3
"""
mapsee_reclassify.py — re-run the classifier over rows already in the table.

WHY. A classifier fix reaches new rows first: `--only-new` is the default in
CI, so a scheduled run can only ADD. Wednesday's run drops the flag and rebuilds
every row a source still returns from that source's OWN category under today's
rules, and `--skip-unchanged` rewrites the ones whose category changed, so most
of a fix lands the next Wednesday with nothing run by hand. This sentence used to
say the opposite ("a change in OUR rules never does"), and the volunteer
backfill of 2026-09-27 was pointed here because of it.

What the refresh cannot reach is a row no source returns any more: a feed taken
out of its config, an event past its adapter's horizon, an Events Calendar site
the step's deadline did not reach that Wednesday. That is what this is for, with
a limit of its own (see `recompute`): it starts from the STORED category, so it
can promote and it can rewrite secondaries, but it cannot take a promotion back.
A row stored as `volunteer` recomputes to volunteer.

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

A ROW THE COLLAPSER KEPT (mapsee_dedupe_events: other rows of the same event
were hidden behind it, events.duplicate_of) carries the categories of those
copies, so the event stays in every door it was in. recompute() reads only the
row's own text, so on its own this sweep would strip that union: on the
2026-10-16 plan it would have dropped a category from 89 of the 91 kept rows the
union changed (community 18, learning 18, theater 15, music 15 ...), and the
next nightly pass would put them back. So a kept row's answer is its own
recomputed categories PLUS its copies' (merged_categories, the sync's one
function), and the hidden copies are swept with it (a classifier fix reaches
the categories the union reads). A copy whose link is another event's page
counts for nothing (copy_counts), which is how a misfile an earlier union
carried ('fitness' on a comedy show) comes off: it shows here as a
'<primary> -<category>' transition.

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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mapsee_supabase_sync import (categories_cover, copy_counts, derive_categories, merged_categories,
                                  read_hidden_copies)

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

    THE STORED SECONDARIES ARE DELIBERATELY NOT PASSED IN, and that is the whole
    reason this sweep can now remove anything. derive_categories rule 2 —
    "anything a source already told us explicitly" — re-adds every key it is
    handed, verbatim and unjudged. Feeding the row's own `categories` back into
    it made the sweep a FIXED POINT BY CONSTRUCTION: a wrong secondary was read
    out, handed straight back, and written down again as if a source had asserted
    it. The sweep could add secondaries and never take one away, and reported
    zero changes while doing it.

    Which is exactly how "the food category is clean" got verified wrong on
    2026-08-12. `Gentle Morning Hatha Yoga` (category=fitness, categories=[food])
    was live on the food map the whole time, and re-running the backfill until it
    returned 0 was never going to move it — 0 was the laundering, not the answer.
    On a re-classification pass the "source" making the claim is our own previous
    output, and rule 2 exists to trust a SOURCE, not to trust ourselves.
    """
    return derive_categories({
        "name": row.get("title") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "categories": [],
    })


COPY_SELECT = "id,title,description,category,categories,place_name,starts_at,claimed_by"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self.text = status, body

    def json(self):
        return json.loads(self.text) if self.text.strip() else None


class _Http:
    """The one get() read_hidden_copies asks of a session, over urllib."""

    def get(self, url, headers=None, timeout=60):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _Resp(r.status, r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read().decode("utf-8", "replace"))


def recompute_kept(row, copies, after):
    """recompute(row), and for a row the collapser kept, its copies'
    categories carried as the sync and the collapser carry them
    (merged_categories over the copies whose link is their own, copy_counts).
    `after(copy)` is the (category, categories) the copy has once this sweep is
    done with it."""
    primary, extras = recompute(row)
    use = [after(c) for c in copies if copy_counts(c)]
    return primary, (merged_categories(primary, extras, use) if use else extras)


def transition(old_primary, old_extras, new_primary, new_extras):
    """The name of a change, or None. See the note in main()."""
    old_sorted, new_sorted = sorted(old_extras or []), sorted(new_extras or [])
    if new_primary == old_primary and new_sorted == old_sorted:
        return None
    if new_primary != old_primary:
        return f"{old_primary} -> {new_primary}"
    dropped = [c for c in old_sorted if c not in new_sorted]
    added = [c for c in new_sorted if c not in old_sorted]
    return old_primary + "".join(f" -{c}" for c in dropped) + "".join(f" +{c}" for c in added)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--only", help="only rows currently in this category")
    ap.add_argument("--days", type=int, default=120, help="how far ahead to sweep")
    # SWEEP A LITTLE INTO THE PAST TOO. The window used to open at `now`, so an
    # event that had already STARTED was never examined — and an in-progress
    # event is the one a person is looking at, because events_near returns it
    # until it ends. "Gentle Morning Hatha Yoga" survived the first apply run
    # for exactly this reason: it began at 17:15Z, the sweep ran at 20:00Z, and
    # it stayed on the food map having never been read.
    ap.add_argument("--back", type=int, default=2,
                    help="days BEFORE now to include, so in-progress events are swept")
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
    start = now - args.back * 86400          # see --back: in-progress events count
    a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start))
    b = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + args.days * 86400))

    # THE COPIES THE COLLAPSER HID, by the row it kept (one keyset; {} before
    # ../mapsee 0236). Without them a write here would strip every kept row's
    # union, so a failed read refuses --apply.
    copies_by_kept = read_hidden_copies(_Http(), SUPABASE_URL, SERVICE_KEY,
                                        datetime.fromtimestamp(start, timezone.utc))
    if copies_by_kept is None:
        if args.apply:
            print("refusing to write: the hidden copies (events.duplicate_of) could not be read, and a\n"
                  "kept row written without them loses its copies' categories", file=sys.stderr)
            return 2
        print("  WARNING: the hidden copies could not be read; kept rows are shown without their union")
        copies_by_kept = {}
    copies_scanned = no_fit = 0

    moves = collections.Counter()
    skipped_windows = []
    examples = collections.defaultdict(list)
    scanned = changed = written = 0
    held = collections.Counter()

    # Walk in time windows rather than by OFFSET: deep offsets on this table die
    # under the statement timeout (../mapsee 0147 header has the same note).
    step = 86400 * 2
    t = start
    pages = 0
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(t + step, now + args.days * 86400)))
        q = ("events?select=id,title,description,category,categories,claimed_by"
             "&external_source=eq.mapsee&is_private=eq.false&hidden_at=is.null"
             f"&starts_at=gte.{w_a}&starts_at=lt.{w_b}&order=starts_at.asc&limit={PAGE}")
        if args.only:
            # PRIMARY *OR* SECONDARY. `category=eq.food` alone selects only rows
            # whose primary is food — but the map does not filter that way.
            # events_near (../mapsee 0108) matches
            #     coalesce(e.category,'other') = any(p_categories)
            #     OR e.categories && p_categories
            # so an event carrying food as a SECONDARY is on the food map with
            # everything else. Selecting on the primary alone meant the sweep
            # never even looked at those rows: measured 2026-08-12, 163 of the
            # 246 events the Seattle food filter returned had a non-food primary,
            # among them a yoga class. A backfill that cannot see the rows the
            # user is complaining about will always report success.
            only = urllib.parse.quote(args.only)
            q += f"&or=(category.eq.{only},categories.cs.%7B{only}%7D)"
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

        # The copies hidden behind this window's kept rows, with their text.
        wanted = [c["id"] for r in rows for c in copies_by_kept.get(str(r["id"]), ())]
        copy_rows = {}
        for i in range(0, len(wanted), 100):
            ids = ",".join(wanted[i:i + 100])
            try:
                for c in sb(f"events?select={COPY_SELECT}&external_source=eq.mapsee&id=in.({ids})") or []:
                    copy_rows[str(c["id"])] = c
            except Exception as e:                    # noqa: BLE001
                print(f"  window {w_a[:10]}: hidden copies' text failed ({e})", file=sys.stderr)
                skipped_windows.append(w_a[:10])
                copy_rows = None
                break
        if copy_rows is None:
            t += step
            continue                                  # never write a kept row without its copies
        after = {}
        for cid, c in copy_rows.items():
            copies_scanned += 1
            after[cid] = (c.get("category"), c.get("categories"))
            if c.get("claimed_by"):
                continue
            new_primary, new_extras = recompute(c)
            key = transition(c.get("category") or "", c.get("categories"), new_primary, new_extras)
            if not key:
                continue
            changed += 1
            moves[key] += 1
            if len(examples[key]) < 3:
                examples[key].append("[hidden copy] " + (c.get("title") or ""))
            if args.apply and key.replace(" ", "") not in allowed:
                held[key] += 1
                continue
            after[cid] = (new_primary, new_extras)
            if args.apply:
                sb(f"events?id=eq.{c['id']}&external_source=eq.mapsee", "PATCH",
                   {"category": new_primary, "categories": new_extras}, prefer="return=minimal")
                written += 1

        for row in rows:
            scanned += 1
            if row.get("claimed_by"):
                continue                                  # a venue owns this listing
            old_primary = row.get("category") or ""
            old_extras = sorted(row.get("categories") or [])
            mine = [copy_rows[c["id"]] for c in copies_by_kept.get(str(row["id"]), ()) if c["id"] in copy_rows]
            new_primary, new_extras = recompute_kept(row, mine, lambda c: after[str(c["id"])])
            no_fit += sum(1 for c in mine if copy_counts(c) and not categories_cover(
                new_primary, new_extras, *after[str(c["id"])]))
            # THE SECONDARIES ARE PART OF THE ANSWER. This used to be
            # `if new_primary == old_primary: continue`, which skipped every row
            # whose only fault was a wrong SECONDARY — and a secondary is enough
            # to put an event on a lens by itself (0108: `e.categories &&
            # p_categories`). The yoga class on the food map was fitness+[food]
            # both before and after, so it was skipped here even once the other
            # two defects were fixed.
            #
            # A secondary-only change needs a name an operator can read and
            # then pass to --allow. `fitness -> fitness` is both meaningless
            # and dangerous: it would authorise every row whose primary is
            # already right. transition() names the actual edit instead —
            # `fitness -food` is "drop the food secondary from fitness rows",
            # which is a thing somebody can agree to on purpose.
            key = transition(old_primary, old_extras, new_primary, new_extras)
            if not key:
                continue
            changed += 1
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

    print(f"\n  scanned {scanned} aggregator rows over {args.days} days (and {copies_scanned} hidden copies "
          f"behind them) · {changed} would change" + ("" if args.apply else "  (DRY RUN — nothing written)"))
    if no_fit:
        print(f"  {no_fit} hidden copies would no longer fit on their kept row: mapsee_dedupe_events' "
              f"nightly pass un-hides them")
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
