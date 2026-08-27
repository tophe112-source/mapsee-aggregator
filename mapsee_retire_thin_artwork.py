#!/usr/bin/env python3
"""
mapsee_retire_thin_artwork.py — hide the anonymous `tourism=artwork` pins.

WHY. Until 2026-08-27 the civic-amenity adapter imported every `tourism=artwork`
element. OSM's definition of that tag takes in every tagged wall, so along
Eastlake in Seattle it drew a solid line of 🎨 down the side of I-5 — one pin per
graffiti tag, 82% of the pins in one reported viewport, burying ten drinking
fountains and nine playgrounds in the same view.

Measured in that box: 404 artworks, 146 of them unnamed, and 55 tagged
`artwork_type=graffiti` of which ZERO carried a name.

`Kind.bare_is_enough` now refuses them at ingest — but AN UPSERT CANNOT DELETE,
so every one already written stays exactly where it is, and the weekly sweep
simply stops re-writing it. This is the other half.

hidden_at, NOT DELETE, for the sibling's reason (mapsee_retire_perday_osm.py):
hiding is reversible, and `--unhide` puts them all back. If the rule turns out
to be wrong, nothing has been lost.

THE TEST IS THE ROW'S OWN STORED DESCRIPTION, which is the same evidence
`to_event` used when it wrote it — no OSM round trip, and no guessing. A row is
hidden only when it carries NONE of:

    a name          (its title is something other than the bare noun)
    an artist       🎨
    an inscription  📝
    a photograph    poster_path
    a real description (any prose line that is not the generated opener)

Never touches:
  * a CLAIMED row. Once somebody owns a listing it is theirs.
  * anything without the OpenStreetMap attribution line — `external_source` is
    'mapsee' for every adapter in this repo, not just this one.
  * anything that is not a public artwork row.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_retire_thin_artwork.py                  # dry run, the default
      python mapsee_retire_thin_artwork.py --apply
      python mapsee_retire_thin_artwork.py --apply --unhide # put them back
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

ODBL = "OpenStreetMap contributors (ODbL)"
# The opener has TWO shapes and the unnamed one is exactly the shape that
# matters here: a named row reads "The Wall — public artwork in Seattle." and an
# unnamed one reads "Public artwork in Seattle." with no dash at all, because a
# thing named after its own kind does not introduce itself twice. Matching on
# the dash found only the rows this was never going to hide.
NOUN = "public artwork"
# NO `order` CLAUSE, AND THAT IS THE WHOLE OF THE TUNING. Measured against
# 2,037 live rows: `order=id.asc` at a page of 100 took 2.35s and raised 57014
# at 500, and `order=created_at.asc` timed out at 1,000 — there is no index
# serving this filter AND that sort, so Postgres collects every matching row
# and sorts it before returning any. Unordered it streams and stops at the
# limit: 1,000 rows in 1.3s.
#
# Which leaves nothing to keyset on, and nothing that needs it. HIDING IS THE
# CURSOR — `hidden_at=is.null` is in the filter, so every row this run hides
# drops out of the next page by itself. The `seen` set is what makes a DRY run,
# which hides nothing, terminate as well.
#
# That asymmetry is worth knowing before you time one: `--apply` makes real
# progress every page, while a DRY run keeps being handed an arbitrary
# thousand of the same rows and grinds out the last few dozen a handful at a
# time. Both finish; the dry one is the slow case, which is the opposite of
# what you would guess.
PAGE = 1000
PAGE_MIN = 100
PATCH_IDS = 100          # ids per PATCH — see flush(): the filter travels in the URL
TIMEOUT_CODE = "57014"
# The generated opener and the lines the sync appends. Anything left after these
# is the row saying something of its own.
BOILERPLATE_PREFIXES = ("\U0001f4cd", "Tickets / info:", "\U0001f50e")


def _req(path, method="GET", body=None, extra=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {"apikey": SERVICE_KEY, "authorization": f"Bearer {SERVICE_KEY}",
               "content-type": "application/json"}
    headers.update(extra or {})
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip() else []
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            # TWO DIFFERENT 5xx ARRIVE HERE AND THEY WANT OPPOSITE THINGS. A
            # statement timeout (57014) means we asked for too much, and the
            # answer is a SMALLER bite — re-issuing the identical request just
            # spends the run doing what already failed. An upstream 5xx means
            # the request never happened and the same one works once the edge
            # recovers. Quote the server either way: a status code alone
            # diagnoses nothing.
            if TIMEOUT_CODE in detail:
                raise Timeout(detail) from None
            if e.code >= 500 and attempt < 3:
                print(f"  {e.code} from PostgREST, retrying: {detail}", flush=True)
                time.sleep(2 ** attempt)
                continue
            # A 400 from the EDGE carries no body at all, so the status alone
            # says nothing — and the commonest cause here is a request line too
            # long. Name the method and the URL length: that is the fact that
            # separates "the filter is wrong" from "the filter is too big".
            raise RuntimeError(
                f"HTTP {e.code} from PostgREST: {detail or '(empty body)'} "
                f"[{method} {len(url)}-char url]") from None
    raise RuntimeError("unreachable")


class Timeout(RuntimeError):
    """A 57014. The caller shrinks rather than re-asking."""


# The edge refuses a request line past ~8 KB with a bare 400 and no body, so
# this is the ceiling that matters and it is asserted rather than assumed.
MAX_URL = 6000


def patch_paths(ids):
    """`events?id=in.(...)` paths, each short enough for the edge to accept.

    Module-level and pure so a test can drive it: this script has failed three
    times on its own query rather than on its judgement, and a constant nobody
    can check is how the third one happened.
    """
    for i in range(0, len(ids), PATCH_IDS):
        path = "events?id=in.(" + ",".join(ids[i:i + PATCH_IDS]) + ")"
        if len(path) > MAX_URL:                    # a UUID is 36; this cannot fire
            raise RuntimeError(f"PATCH url is {len(path)} chars — lower PATCH_IDS")
        yield path


def is_thin(row) -> bool:
    """True when this artwork row says nothing about what you would go to see."""
    desc = row.get("description") or ""
    if ODBL not in desc or NOUN not in desc.split("\n")[0].lower():
        return False                                  # not one of ours
    if row.get("poster_path"):
        return False                                  # a photograph is the best reason of all
    title = (row.get("title") or "").strip().lower()
    if title and title != "public artwork":
        return False                                  # named: findable, so worth a dot
    for line in desc.split("\n")[1:]:
        line = line.strip()
        if not line or ODBL in line or line.startswith("Details can change"):
            continue
        if line.startswith(BOILERPLATE_PREFIXES):
            continue
        if line.startswith("\U0001f5ff"):             # 🗿 Type: — a restatement of the
            continue                                  # category, not a reason to walk there
        return False                                  # an artist, an inscription, real prose
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true", help="actually write; default is a dry run")
    ap.add_argument("--unhide", action="store_true", help="reverse: un-hide what this hid")
    a = ap.parse_args()
    if not SUPABASE_URL or not SERVICE_KEY:
        print("FAIL set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        return 2

    want_hidden = "not.is.null" if a.unhide else "is.null"
    verb = "un-hide" if a.unhide else "hide"
    sel = "id,title,description,poster_path"
    stamp = None if a.unhide else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def flush(batch):
        """Write one batch NOW rather than at the end.

        A FAILED READ IS NOT A FAILED RUN. The walk can be cut short by a
        statement timeout, and collecting the whole table before writing
        anything would throw away everything found so far. Hiding is idempotent
        and the next page's filter excludes what is already hidden, so a short
        run simply leaves less for the next one — which is why this reports and
        exits 0 rather than failing a workflow that made real progress.
        """
        if not batch or not a.apply:
            return 0
        # CHUNKED, BECAUSE `id=in.(...)` IS A URL AND A URL HAS A LENGTH.
        # A page of 1,000 yields ~285 thin rows, and 285 UUIDs is a ~10 KB
        # request line — past the edge's ~8 KB ceiling, which answers a bare
        # `400 Bad Request` with no body to explain itself. 100 ids is ~3.7 KB
        # and comfortable. The version before the walk was rewritten chunked at
        # 100 and the rewrite dropped it: the read got faster and the write got
        # too big in the same edit.
        done = 0
        for path in patch_paths([r["id"] for r in batch]):
            _req(path, method="PATCH", body={"hidden_at": stamp},
                 extra={"prefer": "return=minimal"})
            done += path.count(",") + 1
        return done

    hidden, seen_ids = 0, set()
    page, pending, cut_short = PAGE, [], False
    while True:
        q = {"select": sel, "external_source": "eq.mapsee", "hidden_at": want_hidden,
             "claimed_at": "is.null", "category": "eq.arts", "pin_only": "is.true",
             "limit": str(page)}
        # NO `description=ilike.*public artwork*`. It reads as the obvious filter
        # and it is a wildcard scan of 622,291 rows with no index to serve it —
        # it is what raised 57014 on the first real run. `is_thin` re-reads the
        # description in Python anyway, so the server was doing the work twice
        # and the expensive copy was the one that could not be indexed.
        try:
            rows = _req("events?" + urllib.parse.urlencode(q))
        except Timeout as e:
            if page > PAGE_MIN:
                page = max(PAGE_MIN, page // 2)
                print(f"  statement timeout; retrying with a page of {page}", flush=True)
                continue
            print(f"::warning::walk cut short by a statement timeout at page {page}: {e}",
                  flush=True)
            cut_short = True
            break
        fresh = [r for r in rows if r["id"] not in seen_ids]
        if not fresh:
            break                    # nothing new came back — the walk is done
        seen_ids.update(r["id"] for r in fresh)
        for r in fresh:
            if is_thin(r):
                pending.append(r)
        if a.apply and pending:
            hidden += flush(pending)
            pending = []
        print(f"  walked {len(seen_ids)} artwork pin(s); "
              f"{hidden + len(pending)} thin so far", flush=True)
        if len(rows) < page:
            break
    hidden += flush(pending)

    print(f"\n{len(seen_ids)} unclaimed public-artwork pin(s) examined"
          f"{' (walk cut short — the next run continues)' if cut_short else ''}")
    if a.apply:
        print(f"  {verb}d {hidden}")
    else:
        n = hidden + len(pending)
        print(f"  {n} carry no name, artist, inscription, prose or photograph")
        for r in pending[:8]:
            print(f"    would {verb}: {r['id']}  {(r.get('title') or '')[:40]}")
        if n:
            print(f"\nDRY RUN — pass --apply to {verb} these {n} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
