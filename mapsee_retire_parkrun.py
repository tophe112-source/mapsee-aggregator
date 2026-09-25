#!/usr/bin/env python3
"""
mapsee_retire_parkrun.py — hide every parkrun event this pipeline imported, or
(--unhide) put them back.

WHY IT EXISTS. On 2026-09-25 parkrun's terms were read as needing written
permission, and the README's Conduct section said the adapter sat disabled -
while `parkrun_sources.json`, re-created as a "missing config", imported the
worldwide list twice a week (30 of the 91 running/sports/fitness rows within
35 km of Ottawa were parkrun events). Parking the config stops the FUTURE; AN
UPSERT CANNOT DELETE, so the rows already written stay on the map for up to six
weeks (the adapter's horizon) unless something takes them off. This is that
something, and it is the same shape as mapsee_retire_online_events.py. Run 5
hid 17,870 rows. The owner then decided to show parkrun (the `_decision` note in
parkrun_sources.json), and `--apply --unhide` restored them. It stays as the
one-run way to take parkrun off the map if parkrun ever asks.

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
import urllib.parse
from datetime import datetime
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
        description="Hide every parkrun event this pipeline imported, or (--unhide) restore them.")
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
    n = {"scanned": 0, "hits": 0, "claimed": 0}
    examples, skipped_windows = [], []
    skipped_instants, found_instants = set(), set()

    base = ("events?external_source=eq.mapsee&is_private=eq.false"
            f"&hidden_at={want_hidden}")

    def read(path):
        """One request, retried on a transient failure. None when it cannot be
        read at all, [] when there is simply nothing there."""
        for attempt in range(3):
            try:
                return sb(path) or []
            except Exception as e:
                if TIMEOUT_CODE in str(e) or attempt == 2:
                    print(f"    read failed ({e})", file=sys.stderr)
                    return None
                time.sleep(1.5 * (attempt + 1))
        return None

    def first_instant(lo, inclusive, hi):
        """The next starts_at after `lo`, by the index's own order (no id, so no
        sort): cheap even where a page of rows is not."""
        op = "gte" if inclusive else "gt"
        rows = read(f"{base}&select=starts_at&starts_at={op}.{urllib.parse.quote(lo, safe='')}"
                    f"&starts_at=lt.{hi}&order=starts_at.asc&limit=1")
        return rows[0]["starts_at"] if rows else None

    def descriptions(ids):
        """Only the rows whose TITLE names parkrun get their description read:
        a page of ids and titles is small, a page of descriptions is not."""
        out = {}
        for i in range(0, len(ids), PATCH_IDS):
            chunk = ids[i:i + PATCH_IDS]
            for row in sb(f"events?select=id,description&id=in.({','.join(chunk)})") or []:
                out[str(row["id"])] = row.get("description")
        return out

    def consider(rows, where):
        """Decide one batch of rows and write it now. False when the
        descriptions could not be read, so nothing in the batch was judged."""
        page_ids = []
        named = [str(r["id"]) for r in rows
                 if "parkrun" in (r.get("title") or "").lower() and not r.get("claimed_by")]
        try:
            desc = descriptions(named) if named else {}
        except Exception as e:
            print(f"  {where}: description read failed ({e})", file=sys.stderr)
            return False
        for row in rows:
            n["scanned"] += 1
            if "parkrun" not in (row.get("title") or "").lower():
                continue
            if row.get("claimed_by"):
                n["claimed"] += 1
                continue                           # somebody owns this listing now
            if not should_retire(dict(row, description=desc.get(str(row["id"])))):
                continue
            n["hits"] += 1
            found_instants.add(row["starts_at"])
            page_ids.append(str(row["id"]))
            if len(examples) < 30:
                examples.append((str(row.get("starts_at"))[:10], row.get("title") or "?"))
        flush(page_ids)
        return True

    def sweep_instant(t):
        """Every row at exactly `t` that could be a parkrun row, read through
        selective filters rather than a page of the whole instant. At
        2026-09-26T00:00Z, whose pages could not be read at all, equality plus
        category=running answered in 67 ms, and it held a parkrun row. A parkrun
        row is primarily running (kids, if the junior 2k was promoted) and
        carries outdoors or kids as a secondary. None when a read fails or
        reaches PostgREST's 1,000-row cap, which is not an answer."""
        seen = {}
        at = urllib.parse.quote(t, safe="")
        for f in ("category=eq.running", "category=eq.kids", "categories=ov.%7Boutdoors,kids%7D"):
            rows = read(f"{base}&select=id,title,claimed_by,starts_at&starts_at=eq.{at}&{f}&limit=1000")
            if rows is None or len(rows) >= 1000:
                return None
            for r in rows:
                seen[str(r["id"])] = r
        return list(seen.values())

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
             f"&starts_at=gte.{w_a}&starts_at=lt.{w_b}&order=starts_at.asc,id.asc&limit={PAGE}")
        # A KEYSET, not OFFSET. The first dry run (2026-09-25) walked 284,624 rows
        # and two 4-day windows answered 500 at offsets ~54,000: a deep OFFSET is
        # read and thrown away row by row until it hits the statement timeout.
        # Continuing from the last (starts_at, id) seen costs the same on every
        # page, and it needs no correction for the rows a page just hid: the next
        # page starts after the last key, whatever has left the set since. The
        # cursor is the PAIR because many rows share one starts_at. Values are
        # double-quoted, as in mapsee_indexnow: a timestamp carries ':' and '+'.
        last = None      # keyset cursor: (starts_at, id) of the last row read
        after = None     # an instant stepped past; the walk resumes strictly after it
        while True:
            page_q = q
            if after:
                page_q += "&starts_at=gt." + urllib.parse.quote(after, safe="")
            if last:
                ts, rid = last
                page_q += "&or=" + urllib.parse.quote(
                    f'(starts_at.gt."{ts}",and(starts_at.eq."{ts}",id.gt."{rid}"))', safe="")
            rows = read(page_q)
            if rows is None:
                # A page that cannot be read is stuck inside ONE instant. There
                # is no (starts_at, id) index, so a page among rows sharing a
                # starts_at must fetch and sort all of them, and a cluster of
                # standing rows is tens of thousands: the second dry run
                # (2026-09-25) stuck at 2026-09-24T22:00Z and 2026-09-30T06:00Z,
                # neither of them a time parkrun meets at. Step past the instant
                # and record it; the end of the run checks every one.
                stuck = last[0] if last else first_instant(after or w_a, after is None, w_b)
                if not stuck or stuck in skipped_instants:
                    print(f"  window {w_a[:10]} failed and could not be stepped past",
                          file=sys.stderr)
                    skipped_windows.append(w_a[:10])
                    break
                print(f"  stepped past {stuck}: too many rows share it to page through",
                      file=sys.stderr)
                skipped_instants.add(stuck)
                after, last = stuck, None
                continue
            if not rows:
                break
            if not consider(rows, w_a[:10]):
                skipped_windows.append(w_a[:10])
            if len(rows) < PAGE:
                break
            last = (rows[-1]["starts_at"], rows[-1]["id"])
        pages += 1
        t += step

    # Every instant the walk stepped past is read again through its selective
    # filters; only one that cannot be read that way is left to the weekly test.
    examined = {}
    for t in sorted(skipped_instants):
        rows = sweep_instant(t)
        if rows is not None and consider(rows, t):
            examined[t] = len(rows)

    print(f"\n  scanned {n['scanned']} imported rows · {n['hits']} parkrun rows to {verb}"
          + (f" · {n['claimed']} claimed and left alone" if n['claimed'] else ""))
    for when, title in examples:
        print(f"    {when}  {title[:72]}")
    if n["hits"] > len(examples):
        print(f"    … and {n['hits'] - len(examples)} more")
    # parkrun is weekly, so a parkrun instant recurs every seven days to the
    # minute, give or take the hour a clock change moves it. A skipped instant
    # that matches no parkrun row found anywhere cannot be holding one.
    week = 7 * 86400

    def could_hold_parkrun(t):
        T = datetime.fromisoformat(t)
        for f in found_instants:
            r = (T - datetime.fromisoformat(f)).total_seconds() % week
            if r <= 3600 or r >= week - 3600:
                return True
        return not found_instants          # nothing found: nothing to judge by
    unsafe = sorted(t for t in skipped_instants if t not in examined and could_hold_parkrun(t))
    for t in sorted(skipped_instants):
        print(f"  stepped past {t}: "
              + (f"then read through category filters ({examined[t]} candidate rows)" if t in examined
                 else "COULD hold a parkrun row, NOT examined" if t in unsafe
                 else "unreadable, but no weekly parkrun instant falls on it"))
    if skipped_windows or unsafe:
        # A one-off backfill that missed something has not done its job; say so
        # with the exit code, not only in a line nobody reads.
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) failed, "
              f"{len(unsafe)} unexamined instant(s) could hold parkrun rows")
    if not args.apply:
        print("\n  DRY RUN — nothing written. Read the list above, then re-run with --apply.")
        return 1 if (skipped_windows or unsafe) else 0
    print(f"\n  {past} {written[0]} row(s) (hidden_at "
          + ("cleared" if args.unhide else "set") + "; not deleted, and reversible)")
    return 1 if (skipped_windows or unsafe) else 0


if __name__ == "__main__":
    sys.exit(main())
