#!/usr/bin/env python3
"""
test_retire_thin_artwork.py — which artwork rows may be hidden.

An upsert cannot delete, so refusing anonymous wall tags at INGEST does nothing
about the ones already written; this is the other half, and the failure mode is
asymmetric. Hiding one row too FEW leaves a graffiti tag on the map. Hiding one
too MANY takes a real sculpture off it, and nothing would report that — the pin
simply stops being there.

So every case here is about the boundary, and the safety direction is: when in
doubt, KEEP.

Run: python test_retire_thin_artwork.py
"""
import contextlib
import io
import sys

import mapsee_retire_thin_artwork as R

ODBL = "Public details from OpenStreetMap contributors (ODbL)."


def row(title, *body, poster=None, opener=None):
    head = opener if opener is not None else (
        f"{title} — public artwork." if title != "Public artwork" else "Public artwork.")
    desc = "\n\n".join([head] + ([("\n".join(body))] if body else []) + [ODbL_LINE])
    return {"id": "x", "title": title, "description": desc, "poster_path": poster}


ODbL_LINE = ODBL


def main():
    checks = []

    # ---- hidden: it says nothing about what you would go to see -------------
    for r, why in [
        (row("Public artwork", "\U0001f5ff Type: graffiti"),
         "an unnamed wall tag with only its TYPE"),
        (row("Public artwork", "\U0001f5ff Type: sculpture"),
         "...and the type being 'sculpture' does not change that"),
        (row("Public artwork"),
         "an unnamed piece with nothing at all"),
        (row("Public artwork", "\U0001f5ff Type: mural", "\U0001f4cd 5th Ave, Seattle, WA"),
         "the sync's own address line is not the row talking"),
    ]:
        checks.append((R.is_thin(r) is True, why))

    # ---- kept: something tells you what it is -------------------------------
    for r, why in [
        (row("The Wall", "\U0001f5ff Type: sculpture"),
         "a NAME makes it findable, whatever else is missing"),
        (row("Public artwork", "\U0001f3a8 Artist: Ellen Sollod"),
         "an artist, on an untitled piece"),
        (row("Public artwork", "\U0001f4dd Inscription: “1898-1902”"),
         "an inscription — it is what you would go to read"),
        (row("Public artwork", "A ceramic catfish in the pavement."),
         "a real description"),
        (row("Public artwork", "\U0001f5ff Type: graffiti", poster="a/b.jpg"),
         "a PHOTOGRAPH is the best reason of all, and outranks everything"),
        (row("Public artwork", "\U0001f310 Website: https://example.org/art"),
         "somewhere to read more"),
    ]:
        checks.append((R.is_thin(r) is False, why))

    # ---- never ours ---------------------------------------------------------
    checks.append((R.is_thin({"id": "x", "title": "Public artwork",
                              "description": "Public artwork.\n\nno attribution"}) is False,
                   "a row without the ODbL line is not this adapter's to hide"))
    checks.append((R.is_thin({"id": "x", "title": "Open Mic",
                              "description": f"Open Mic — a gig.\n\n{ODBL}"}) is False,
                   "and neither is anything that is not a public artwork"))
    checks.append((R.is_thin({"id": "x", "title": None, "description": None}) is False,
                   "a row with no description at all is left alone"))

    # ---- BOTH opener shapes, which is what the first version got wrong ------
    #
    # A named row reads "The Wall — public artwork in Seattle." and an unnamed
    # one reads "Public artwork in Seattle." with NO DASH, because a thing named
    # after its own kind does not introduce itself twice. Matching on the dash
    # found only the rows this was never going to hide — it reported zero thin
    # rows against live data where 120 were sitting.
    checks.append((R.is_thin(row("Public artwork", "\U0001f5ff Type: graffiti",
                                 opener="Public artwork in Seattle.")) is True,
                   "the DASHLESS opener is matched — it is the unnamed shape, and "
                   "the unnamed rows are the whole point"))
    checks.append((R.is_thin(row("Fremont Troll", "\U0001f5ff Type: sculpture",
                                 opener="Fremont Troll — public artwork in Seattle.")) is False,
                   "...and the dashed one is still recognised, and still kept"))

    # ---- the WRITE, which is where this script has actually failed ---------
    #
    # Twice on its query and once on its write, none of them on its judgement.
    # `id=in.(...)` travels in the URL: a page of 1,000 yields ~285 thin rows,
    # and 285 UUIDs is a ~10 KB request line, past the edge's ceiling — which
    # answers a bare 400 with no body to explain itself.
    uuids = [f"{i:08x}-1111-2222-3333-444444444444" for i in range(250)]
    paths = list(R.patch_paths(uuids))
    checks.append((len(paths) == 3, f"250 ids go out in 3 requests, not 1 ({len(paths)})"))
    checks.append((all(len(p) <= R.MAX_URL for p in paths),
                   f"...none of them past the edge's ceiling "
                   f"(longest {max(len(p) for p in paths)} of {R.MAX_URL})"))
    checks.append((sum(p.count(",") + 1 for p in paths) == 250,
                   "...and every id is in exactly one of them"))
    checks.append((list(R.patch_paths([])) == [], "an empty batch sends nothing"))
    checks.append((len(list(R.patch_paths(uuids[:1]))) == 1, "a single id still goes"))

    # ---- and the THIRD failure was on the report line ----------------------
    #
    # `print(f"  {past} {hidden}")` over a `past` nobody had defined. It is
    # unreachable from every case above, because all of them call is_thin() or
    # patch_paths() DIRECTLY — the bug was in main(), and nothing ran main().
    # Live cost: 9,781 pins walked, 54 correctly hidden and WRITTEN, then a
    # NameError on the summary and a red job over work that had succeeded.
    # Exactly the family already written down for mapsee_ingest_osm_amenities.
    #
    # So this drives the real main() against a stubbed transport, on all three
    # argv shapes, and asserts what each one is FOR: the dry run writes
    # nothing, --apply writes, --unhide clears the stamp rather than setting
    # one. The stub also proves the walk terminates — `seen_ids` is what stops
    # a dry run being handed the same thousand rows for ever.
    thin = [{"id": f"{i:08x}-1111-2222-3333-444444444444",
             "title": "Public artwork",
             "description": f"Public artwork.\n\n\U0001f5ff Type: graffiti\n\n{ODBL}",
             "poster_path": None} for i in range(3)]
    keep = [{"id": "ffffffff-1111-2222-3333-444444444444",
             "title": "The Wall",
             "description": f"The Wall — public artwork.\n\n\U0001f3a8 Artist: A Person\n\n{ODBL}",
             "poster_path": None}]

    def run(argv):
        """Drive the REAL main() and report (exit code, PATCH bodies, output)."""
        patched, pages = [], []

        def fake_req(path, method="GET", body=None, extra=None):
            if method == "PATCH":
                patched.append((path, body))
                return []
            pages.append(path)
            return (thin + keep) if len(pages) == 1 else []

        real_req, real_argv = R._req, sys.argv
        real_url, real_key = R.SUPABASE_URL, R.SERVICE_KEY
        out = io.StringIO()
        try:
            R._req, sys.argv = fake_req, ["x"] + argv
            R.SUPABASE_URL, R.SERVICE_KEY = "https://example.invalid", "k"
            with contextlib.redirect_stdout(out):
                code = R.main()
        finally:
            R._req, sys.argv = real_req, real_argv
            R.SUPABASE_URL, R.SERVICE_KEY = real_url, real_key
        return code, patched, out.getvalue()

    for argv, writes, why in [
        ([], 0, "a DRY run finishes and writes nothing"),
        (["--apply"], 1, "--apply finishes and writes"),
        (["--apply", "--unhide"], 1, "--apply --unhide finishes and writes"),
    ]:
        try:
            code, patched, text = run(argv)
        except Exception as e:                       # NameError lands here
            checks.append((False, f"main() {' '.join(argv) or '(dry)'} raised {e!r}"))
            continue
        checks.append((code == 0, f"{why} (exit {code})"))
        checks.append((len(patched) == writes,
                       f"...{len(patched)} PATCH(es), expected {writes}"))
        if writes and patched:
            sets = patched[0][1]["hidden_at"]
            want_null = "--unhide" in argv
            checks.append(((sets is None) == want_null,
                           f"...hidden_at is {'cleared' if want_null else 'stamped'}"))
            checks.append((patched[0][0].count(",") + 1 == len(thin),
                           f"...and only the {len(thin)} thin rows, not the named one"))

    failed = sum(0 if ok else 1 for ok, _ in checks)
    for ok, why in checks:
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
