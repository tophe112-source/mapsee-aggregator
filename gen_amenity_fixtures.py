#!/usr/bin/env python3
"""
gen_amenity_fixtures.py — write ../mapsee/tools/fixtures-amenity-rows.json.

../mapsee decides for itself whether a civic pin opens (`amenityHasContent`,
0195), which is a second opinion on this repo's `pin_only` — and the thing it
actually reads is a STRING THIS REPO ASSEMBLES IN TWO PLACES: the adapter
writes the description and the sync appends to it. Neither file is wrong on
its own, so a bug that lives only in what the two make together is invisible
from either.

That is not hypothetical. The first version of that check ran against
fixtures written BY HAND, and passed, while every genuine row carried a
"🔎 More on this show" Google link the sync appends to any source outside its
deny-list. Every drinking fountain on earth would have been clickable.

So the fixtures are GENERATED, by this, from `to_event` and `to_row`. Run it
after touching either, and commit what changes.

Two traps worth keeping, both paid for:
  * Build the record with `NormalizedEvent.as_record`, never `vars()`. The
    sync's `primary_url` reads `rec["sources"]`, which the dataclass does not
    have as a field — so a hand-shaped rec silently drops the
    "Tickets / info:" line, one of the strings the client has to strip.
  * Keep a case for every SHAPE, not every selector: the two openers (named
    and unnamed), an image with no words, a row carrying only the sync's own
    additions. The failure being guarded is a strip that eats one line too
    many or one too few.

Run: python gen_amenity_fixtures.py [--out PATH]
"""
import argparse
import json
import os
import sys
import mapsee_ingest_osm_amenities as A
import mapsee_supabase_sync as S

AREA = {"name": "Seattle", "region": "WA", "country": "US"}

CASES = {
  "furniture_bare":       {"amenity": "drinking_water"},
  "furniture_named":      {"amenity": "public_bookcase", "name": "Sarah's Book Box"},
  "furniture_addressed":  {"leisure": "playground", "name": "Cal Anderson Play Area",
                           "addr:city": "Seattle", "addr:housenumber": "1635",
                           "addr:street": "11th Ave"},
  "furniture_24_7":       {"leisure": "playground", "name": "Volunteer Park",
                           "opening_hours": "24/7"},
  "listing_hours":        {"social_facility": "food_bank", "name": "Rainier Food Bank",
                           "operator": "Northwest Harvest",
                           "opening_hours": "Tu,Th 10:00-14:00"},
  # A FOOD BANK NOBODY TAGGED. The one row whose sheet exists because of WHERE
  # IT IS rather than because of what is written on it — and the one whose
  # entire prose is a sentence saying we do not know the hours. If the client's
  # boilerplate strip ever eats that sentence, this row goes inert and the
  # highest-stakes pin on the map stops opening.
  "listing_food_bank_no_hours": {"social_facility": "food_bank",
                                 "name": "ACRS Food Bank", "addr:city": "Seattle"},
  "pin_open_artist":       {"tourism": "artwork", "name": "The Wall",
                           "artist_name": "Ellen Sollod"},
  # THE CASE THE 24/7 RULE IS FOR. A playground with an operator, a surface
  # and "lit after dark" is well described and is open at 3am — so it is a
  # PIN that hovers and opens, never a row in the list of what is on. The
  # client has to find real prose here or the pin goes inert.
  "pin_open_always":      {"leisure": "playground", "name": "Cal Anderson Play Area",
                           "operator": "Seattle Parks", "lit": "yes",
                           "surface": "sand", "addr:city": "Seattle"},
  # ...and the one shape that IS a listing: it can be shut.
  "listing_bounded_hours": {"leisure": "playground", "name": "Gated Play Area",
                            "opening_hours": "Mo-Su 08:00-20:00"},
  "pin_open_image_only":   {"tourism": "artwork", "name": "Untitled",
                           "wikimedia_commons": "File:Seattle Art.jpg"},
  "pin_open_freetext":     {"leisure": "playground", "name": "Kerry Park",
                           "description": "Fully fenced, shaded, with a splash pad."},
  # Kept as a GIVE BOX with a real-shaped website, because this row is the one
  # that also carries the sync's "Tickets / info:" line — another piece of
  # boilerplate the client has to strip before deciding the pin opens.
  "pin_open_website":      {"amenity": "give_box", "name": "Fremont Give Box",
                           "website": "https://fremontgivebox.org"},
}

out = {}
for key, tags in CASES.items():
    el = {"type": "node", "id": abs(hash(key)) % 10**8, "lat": 47.61, "lon": -122.33,
          "tags": tags}
    ev = A.to_event(el, AREA)
    assert ev is not None, key
    # as_record, not vars(): it is what the STORE writes and what the sync
    # actually reads. `primary_url` looks in rec["sources"], which vars() has
    # not got — so a hand-shaped rec silently drops the "Tickets / info:" line,
    # which is one of the strings the client has to strip.
    rec = ev.as_record("2026-08-26T00:00:00Z")
    row = S.to_row(rec, host_id="00000000-0000-0000-0000-000000000000")
    out[key] = {
        "title": row.get("title"),
        "description": row.get("description"),
        "poster_path": row.get("poster_path") or row.get("poster_url") or None,
        "pin_only": row.get("pin_only"),
    }

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "mapsee", "tools",
    "fixtures-amenity-rows.json"))
dest = os.path.abspath(ap.parse_args().out)
with open(dest, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print("wrote", dest)
for k, v in out.items():
    print(f"  {k:30} pin_only={v['pin_only']}  {v['description'].splitlines()[0][:60]}")
