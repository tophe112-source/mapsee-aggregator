#!/usr/bin/env python3
"""
mapsee_retire_parkrun.py — hide the parkrun events imported while parkrun was
meant to be parked.

WHY. parkrun is held pending permission: their terms reserve all rights in site
content and reference an anti-scraping policy, and the README's Conduct section
says, publicly, that the adapter "sits disabled until someone at parkrun says yes
in writing". But `parkrun_sources.json` was re-created as a "missing config" and
the workflow step, guarded only on that file existing, imported the worldwide
list twice a week. On 2026-09-25, 30 of the 91 running/sports/fitness rows within
35 km of Ottawa were parkrun events. The config is parked again, which stops the
FUTURE; AN UPSERT CANNOT DELETE, so the rows already written stay on the map for
up to six weeks (the adapter's horizon) unless something takes them off. This is
that something, and it is the same shape as mapsee_retire_online_events.py.

THE TEST IS THE ROW'S OWN STORED TEXT, and it is the adapter's own text: a title
naming parkrun AND a description that begins with one of the two blurbs the
adapter writes (imported from it, so the two cannot drift). A parkrun meetup
somebody else published — a club's Saturday social, a Meetup group — has neither
blurb and is never touched.

hidden_at, NOT DELETE: reversible with `--unhide`, and durable against re-import
because `fetch_import_state` does not filter on `hidden_at`. mapsee_cleanup reaps
them a week after they would have ended, like every other imported row.

Never touches:
  * a CLAIMED row. Once somebody owns a listing it is theirs.
  * a private row.
  * anything whose `external_source` is not 'mapsee' — a user's own event is
    never in scope, whatever it says.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_retire_parkrun.py                   # dry run, the default
      python mapsee_retire_parkrun.py --apply
      python mapsee_retire_parkrun.py --apply --unhide  # put them back
"""
import argparse
import json
import os
import sys
import time
import urllib.request

from mapsee_ingest_parkrun import SERIES

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

PAGE = 500
PATCH_IDS = 100          # ids per PATCH — the filter travels in the URL (~37 bytes each)
TIMEOUT_CODE = "57014"   # postgres: canceling statement due to statement timeout

# The adapter's own openings, so this can only ever match what it wrote.
_BLURBS = tuple(s["blurb"] for s in SERIES.values())


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


def should_retire(row):
    """A row the parkrun adapter wrote: its title names parkrun, and its stored
    description opens with the adapter's blurb (the sync appends its own lines
    after it, never before)."""
    title = (row.get("title") or "").lower()
    desc = (row.get("description") or "").lstrip()
    return "parkrun" in title and desc.startswith(_BLURBS)


def main():
    ap = argparse.ArgumentParser(
        description="Hide the parkrun events imported while parkrun was meant to be parked.")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--unhide", action="store_true", help="reverse: un-hide what this hid")
    ap.add_argument("--days", type=int, default=45,
                    help="how far ahead to sweep (the adapter's horizon is 42)")
    ap.add_argument("--back", type=int, default=1, help="days BEFORE now to include")
    ap.add_argument("--max-pages", type=int, default=400)
    args = ap.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    want_hidden = "not.is.null" if args.unhide else "is.null"
    verb = "un-hide" if args.unhide else "hide"
    past = "un-hid" if args.unhide else "hid"
    stamp = None if args.unhide else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    written = [0]

    def flush(ids):
        """Write one page's decisions NOW: a walk cut short by a timeout keeps
        what it had already decided."""
        if not ids or not args.apply:
            return 0
        done = 0
        for i in range(0, len(ids), PATCH_IDS):
            chunk = ids[i:i + PATCH_IDS]
            try:
                sb(f"events?id=in.({','.join(chunk)})", "PATCH",
                   {"hidden_at": stamp}, prefer="return=minimal")
                written[0] += len(chunk)
                done += len(chunk)
            except Exception as e:
                print(f"    PATCH of {len(chunk)} failed: {e}", file=sys.stderr)
        return done

    now = time.time()
    start = now - args.back * 86400
    step = 86400 * 4
    t, pages = start, 0
    scanned = hits = claimed_skipped = 0
    examples, skipped_windows = [], []

    def descriptions(ids):
        """Only the rows whose TITLE names parkrun get their description read:
        a page of ids and titles is small, a page of descriptions is not."""
        out = {}
        for i in range(0, len(ids), PATCH_IDS):
            chunk = ids[i:i + PATCH_IDS]
            for row in sb(f"events?select=id,description&id=in.({','.join(chunk)})") or []:
                out[str(row["id"])] = row.get("description")
        return out

    # Windowed along the (external_source, starts_at) index, like its siblings,
    # and with NO text filter in the query: a `title=ilike` or
    # `description=ilike` makes the planner give up that index for a sequential
    # scan, which comes back 57014 every time (docs/agents/spam-and-content.md;
    # the same filter as the anon role timed out at ~3.1s on a ONE-day window,
    # 2026-09-25). The title test runs here instead.
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + args.days * 86400)))
        q = ("events?select=id,title,claimed_by,starts_at"
             "&external_source=eq.mapsee&is_private=eq.false"
             f"&hidden_at={want_hidden}"
             f"&starts_at=gte.{w_a}&starts_at=lt.{w_b}&order=starts_at.asc&limit={PAGE}")
        offset = 0
        while True:
            rows = None
            for attempt in range(3):
                try:
                    rows = sb(q + f"&offset={offset}") or []
                    break
                except Exception as e:
                    if TIMEOUT_CODE in str(e) or attempt == 2:
                        print(f"  window {w_a[:10]} offset {offset} failed ({e})",
                              file=sys.stderr)
                        skipped_windows.append(w_a[:10])
                        break
                    time.sleep(1.5 * (attempt + 1))
            if not rows:
                break
            page_ids = []
            named = [str(r["id"]) for r in rows
                     if "parkrun" in (r.get("title") or "").lower() and not r.get("claimed_by")]
            try:
                desc = descriptions(named) if named else {}
            except Exception as e:
                print(f"  window {w_a[:10]}: description read failed ({e})", file=sys.stderr)
                skipped_windows.append(w_a[:10])
                desc = {}
            for row in rows:
                scanned += 1
                if "parkrun" not in (row.get("title") or "").lower():
                    continue
                if row.get("claimed_by"):
                    claimed_skipped += 1
                    continue                       # somebody owns this listing now
                if not should_retire(dict(row, description=desc.get(str(row["id"])))):
                    continue
                hits += 1
                page_ids.append(str(row["id"]))
                if len(examples) < 30:
                    examples.append((str(row.get("starts_at"))[:10],
                                     row.get("title") or "?"))
            moved = flush(page_ids)
            if len(rows) < PAGE:
                break
            # Under --apply the rows this page just changed have left the result
            # set (hidden_at is in the filter, in both directions), so the next
            # page starts that many rows earlier. PAGE minus what was actually
            # WRITTEN, not what was decided: a page hidden whole starts the next
            # one at the same offset (the old max(1, ...) skipped its first row),
            # and a failed PATCH leaves its rows in the set, so the cursor steps
            # over them rather than re-reading them for ever. Either rows leave
            # or the offset moves.
            offset += PAGE - (moved if args.apply else 0)
        pages += 1
        t += step

    print(f"\n  scanned {scanned} imported rows · {hits} parkrun rows to {verb}"
          + (f" · {claimed_skipped} claimed and left alone" if claimed_skipped else ""))
    for when, title in examples:
        print(f"    {when}  {title[:72]}")
    if hits > len(examples):
        print(f"    … and {hits - len(examples)} more")
    if skipped_windows:
        # A one-off backfill that missed a window has not done its job; say so
        # with the exit code, not only in a line nobody reads.
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) errored and were not "
              f"examined: {', '.join(skipped_windows[:6])}")
    if not args.apply:
        print("\n  DRY RUN — nothing written. Read the list above, then re-run with --apply.")
        return 1 if skipped_windows else 0
    print(f"\n  {past} {written[0]} row(s) (hidden_at "
          + ("cleared" if args.unhide else "set") + "; not deleted, and reversible)")
    return 1 if skipped_windows else 0


if __name__ == "__main__":
    sys.exit(main())
