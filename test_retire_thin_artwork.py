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

    failed = sum(0 if ok else 1 for ok, _ in checks)
    for ok, why in checks:
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
