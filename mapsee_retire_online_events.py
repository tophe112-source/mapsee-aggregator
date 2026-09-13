#!/usr/bin/env python3
"""
mapsee_retire_online_events.py — hide the rows that were never anywhere.

WHY. Until 2026-09-13 every "is this online?" check in the pipeline asked
whether the row had coordinates. That is only true while the venue box is empty:
an organiser can file a Zoom event as PHYSICAL, type anything into the venue,
and it arrives with a latitude, a longitude and a street address nobody will
ever stand at. `looks_online_only` and `venue_is_only_a_plus_code` in
mapsee_ingest.py refuse them at ingest now — but AN UPSERT CANNOT DELETE, so
every one already written stays exactly where it is and the next sweep simply
stops re-writing it. This is the other half, and it is the same shape as
mapsee_retire_thin_artwork.py, which exists for the same reason.

Measured 2026-09-13 over 2,000 live event pages sampled from mapsee.me's own
sitemaps: 40 rows say in their own words that they happen on Zoom and every one
is pinned to a street. 35 are online-only, ALL 35 arrived through Meetup, and
all 35 are one commercial speed-dating network reposting the same template city
by city ("Seattle Gay Virtual Speed Dating on Zoom — join from home", pinned to
the Plus Code "JP7Q+33 Mercer Island") into groups that have nothing to do with
it: a taekwondo group, a calligraphy club, a vegan cookery crew. Scaled to the
map that is somewhere near 1.8% of upcoming rows.

THE TEST IS THE ROW'S OWN STORED TEXT, which is the same evidence `to_event`
had when it wrote it — no round trip to the source, and nothing to guess. The
predicate is shared with the adapter on purpose: a row this hides is exactly a
row the ingest would now refuse, and the two can never drift apart.

THE OTHER 5 OF THOSE 40 MUST STAY, and they are the reason this is careful. A
sangha, a church and a meditation group really do run a room as well as a
stream; "Online and In-Person" is a real event at a real address that also has
a dial-in. `_HYBRID_RX` withholds on one clear phrase, and the title is read as
well as the blurb because one of the five says it only in its title.

hidden_at, NOT DELETE, for its sibling's reason: hiding is reversible,
`--unhide` puts them all back, and if the rule turns out to be wrong nothing has
been lost. `mapsee_cleanup` deletes them a week after they would have ended like
every other imported row, and hiding is durable against re-import —
`fetch_import_state` does not filter on `hidden_at`, so a hidden row still
counts as existing and `--only-new` will not put it back.

ONE THING TO KNOW ABOUT `--unhide`: hidden_at is a single column and several
tools set it, so a row hidden by mapsee_prune_cancelled for being CANCELLED can
also match this rule — most of these listings are both. The predicate is applied
in unhide mode too, so nothing outside this rule is ever restored; but a
cancelled row restored here is restored wrongly, and the fix is that it is
self-healing: prune-cancelled runs daily and re-hides it within the day. Run
`--unhide` when you think THIS rule was wrong, not as a general undo.

Never touches:
  * a CLAIMED row. Once somebody owns a listing it is theirs.
  * a private or already-hidden row.
  * anything whose `external_source` is not 'mapsee' — a user's own event is
    never in scope, whatever its description says.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_retire_online_events.py                  # dry run, the default
      python mapsee_retire_online_events.py --apply
      python mapsee_retire_online_events.py --apply --unhide # put them back
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from mapsee_ingest import looks_online_only

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

PAGE = 500
PATCH_IDS = 100          # ids per PATCH — the filter travels in the URL (~37 bytes each)
TIMEOUT_CODE = "57014"   # postgres: canceling statement due to statement timeout


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
    """The same predicate the adapter now refuses on, over the stored row.

    One function, shared with the ingest, so a row this hides is exactly a row
    that would not be written today. The description the sync stored has our own
    generated lines appended (📍 address, Tickets / info, 🔎 More on this show);
    they carry no Zoom vocabulary, so there is nothing to strip.
    """
    return looks_online_only(row.get("title"), row.get("description"))


def main():
    ap = argparse.ArgumentParser(
        description="Hide imported events whose own listing says they are online only.")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--unhide", action="store_true", help="reverse: un-hide what this hid")
    ap.add_argument("--days", type=int, default=400, help="how far ahead to sweep")
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
        """Write one batch NOW rather than at the end.

        A failed read is not a failed run: the walk can be cut short by a
        statement timeout or an outage, and whatever was already decided is
        worth keeping. Same reason mapsee_cleanup pages its deletes.
        """
        if not ids or not args.apply:
            return
        for i in range(0, len(ids), PATCH_IDS):
            chunk = ids[i:i + PATCH_IDS]
            try:
                sb(f"events?id=in.({','.join(chunk)})", "PATCH",
                   {"hidden_at": stamp}, prefer="return=minimal")
                written[0] += len(chunk)
            except Exception as e:
                print(f"    PATCH of {len(chunk)} failed: {e}", file=sys.stderr)

    now = time.time()
    start = now - args.back * 86400
    step = 86400 * 4
    t, pages = start, 0
    scanned = hits = claimed_skipped = 0
    examples, skipped_windows = [], []

    # Windowed like mapsee_reclassify and mapsee_prune_cancelled, for the same
    # reason: deep OFFSET paging on this table dies under the statement timeout.
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + args.days * 86400)))
        q = ("events?select=id,title,description,claimed_by,starts_at"
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
            for row in rows:
                scanned += 1
                if row.get("claimed_by"):
                    claimed_skipped += 1
                    continue                       # somebody owns this listing now
                if not should_retire(row):
                    continue
                hits += 1
                page_ids.append(str(row["id"]))
                if len(examples) < 30:
                    examples.append((str(row.get("starts_at"))[:10],
                                     row.get("title") or "?"))
            # WRITE AT THE END OF EACH PAGE, NOT WHEN A BATCH HAPPENS TO FILL,
            # because the cursor below has to know exactly how many rows this
            # page removed from the result set. Carrying a half-full batch over
            # into the next page made that number unknowable, and the first
            # draft of this line used the leftover as if it were the page's
            # count — which would have skipped real rows on every page that hid
            # anything.
            flush(page_ids)
            if len(rows) < PAGE:
                break
            # `hidden_at=is.null` is IN THE FILTER, so under --apply the rows
            # this page just hid have left the result set and everything after
            # them shifted down by that many. Advancing a full PAGE would step
            # over exactly that many unexamined rows. A dry run hides nothing
            # and pages normally; so does --unhide, whose filter is the
            # complement and whose writes remove rows from it the same way —
            # hence the same correction, not a plain PAGE.
            offset += PAGE if not args.apply else max(1, PAGE - len(page_ids))
        pages += 1
        t += step

    print(f"\n  scanned {scanned} imported rows · {hits} to {verb}"
          + (f" · {claimed_skipped} claimed and left alone" if claimed_skipped else ""))
    if skipped_windows:
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) errored and were not "
              f"examined: {', '.join(skipped_windows[:6])}")
    for when, title in examples:
        print(f"    {when}  {title[:72]}")
    if hits > len(examples):
        print(f"    … and {hits - len(examples)} more")
    if not args.apply:
        print("\n  DRY RUN — nothing written. Read the list above, then re-run with --apply.")
        return 0
    print(f"\n  {past} {written[0]} row(s) (hidden_at "
          + ("cleared" if args.unhide else "set") + "; not deleted, and reversible)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
