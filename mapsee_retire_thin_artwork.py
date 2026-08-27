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
PAGE = 500
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
            # A statement timeout wants a SMALLER bite; an upstream 5xx wants the
            # same request once the edge recovers. Quote the server either way —
            # a status code alone diagnoses nothing.
            if e.code >= 500 and attempt < 3:
                print(f"  {e.code} from PostgREST, retrying: {detail}", flush=True)
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {e.code} from PostgREST: {detail}") from None
    raise RuntimeError("unreachable")


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
    sel = "id,title,description,poster_path,claimed_at,hidden_at"
    seen, thin, kept, cursor = 0, [], 0, None
    while True:
        q = {"select": sel, "external_source": "eq.mapsee", "hidden_at": want_hidden,
             "claimed_at": "is.null", "category": "eq.arts",
             "description": f"ilike.*{NOUN}*", "order": "id.asc", "limit": str(PAGE)}
        if cursor:
            q["id"] = f"gt.{cursor}"
        rows = _req("events?" + urllib.parse.urlencode(q))
        if not rows:
            break
        for r in rows:
            seen += 1
            if is_thin(r):
                thin.append(r)
        kept = seen - len(thin)
        cursor = rows[-1]["id"]
        print(f"  walked {seen} artwork row(s); {len(thin)} thin so far", flush=True)
        if len(rows) < PAGE:
            break

    verb = "un-hide" if a.unhide else "hide"
    print(f"\n{seen} unclaimed public-artwork row(s) examined")
    print(f"  {len(thin)} carry no name, artist, inscription, prose or photograph")
    print(f"  {kept} say something and are kept")
    for r in thin[:8]:
        print(f"    would {verb}: {r['id']}  {(r.get('title') or '')[:40]}")
    if len(thin) > 8:
        print(f"    ... and {len(thin) - 8} more")
    if not thin:
        print("nothing to do")
        return 0
    if not a.apply:
        print(f"\nDRY RUN — pass --apply to {verb} these {len(thin)} row(s)")
        return 0

    stamp = None if a.unhide else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    done = 0
    for i in range(0, len(thin), 100):
        ids = [r["id"] for r in thin[i:i + 100]]
        _req("events?id=in.(" + ",".join(ids) + ")", method="PATCH",
             body={"hidden_at": stamp}, extra={"prefer": "return=minimal"})
        done += len(ids)
        print(f"  {verb}d {done}/{len(thin)}", flush=True)
    print(f"\n{verb}d {done} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
