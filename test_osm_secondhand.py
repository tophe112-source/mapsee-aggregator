#!/usr/bin/env python3
"""
test_osm_secondhand.py — what belongs in fleabop, and what must never reach it.

The opening-hours parser is NOT retested here: this adapter imports it from
mapsee_ingest_osm_food and test_osm_food.py already owns those cases. What this
file guards is the half that is genuinely new — the selector.

The selector is the whole risk in this adapter. `second_hand=only` is 29,256
elements and 15,853 of them (55%) are shop=car, i.e. used car lots. Widen the
allow-list by accident and a Ford dealership lands on a map whose first line of
copy is about a jacket that fit somebody else. Nothing downstream would catch
it: the row would have a name, a coordinate, readable hours and a valid
category, so it would sync cleanly and look exactly like a good row.

Run: python test_osm_secondhand.py
"""
import re
import sys

from mapsee_ingest_osm_secondhand import (
    wanted, selector, shop_kind, own_website, secondhand_detail_lines,
    to_events, DIRECT_SHOPS, SECOND_HAND_ONLY_SHOPS,
)

fails = []


def check(cond, label):
    if not cond:
        fails.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


print("selector: what is in the pool")
for k in DIRECT_SHOPS:
    check(wanted({"shop": k}), f"shop={k} is in on its own")
check(wanted({"shop": "clothes", "second_hand": "only"}),
      "shop=clothes + second_hand=only is a second-hand clothes shop")
check(wanted({"shop": "CLOTHES", "second_hand": "Only"}),
      "case and stray case in the tag value do not change the answer")

print("\nselector: what must stay out")
check(not wanted({"shop": "car", "second_hand": "only"}),
      "a USED CAR LOT is not fleabop — the 15,853-element failure mode")
check(not wanted({"shop": "car_parts", "second_hand": "only"}), "nor used car parts")
check(not wanted({"shop": "motorcycle", "second_hand": "only"}), "nor used motorcycles")
check(not wanted({"shop": "clothes"}),
      "a NEW clothes shop is not second-hand without second_hand=only")
check(not wanted({"shop": "clothes", "second_hand": "yes"}),
      "second_hand=yes means 'some used stock', not a second-hand shop")
check(not wanted({}), "an element with no shop tag is not a shop")
check(not wanted({"shop": ""}), "an empty shop tag is not a shop")
check("car" not in SECOND_HAND_ONLY_SHOPS, "car is absent from the allow-list itself")

print("\nselector: the Overpass string agrees with the Python filter")
# The query is built by string interpolation from the same tuples `wanted` reads.
# A typo there would widen the server-side half only, so the two are compared
# rather than trusted.
q = selector(0, 0, 1, 1)
for k in DIRECT_SHOPS:
    check(f'"shop"="{k}"' in q, f"query asks for shop={k}")
m = re.search(r'"shop"~"\^\(([^)]*)\)\$"', q)
check(m is not None, "query carries an anchored shop regex for second_hand=only")
if m:
    in_q = set(m.group(1).split("|"))
    check(in_q == set(SECOND_HAND_ONLY_SHOPS),
          "the regex alternation is exactly the allow-list, no more and no less")
    check("car" not in in_q, "and the server is never asked for used car lots")
check('"second_hand"="only"' in q, "query pins second_hand=only, not =yes")
check(q.count('["name"]') == len(DIRECT_SHOPS) + 1, "every branch requires a name")

print("\nthe human noun")
check(shop_kind({"shop": "charity"}) == "charity shop", "charity shop")
check(shop_kind({"shop": "antiques"}) == "antiques shop", "antiques shop")
check(shop_kind({"shop": "second_hand"}) == "second-hand shop", "second-hand shop")
check(shop_kind({"shop": "clothes", "second_hand": "only"}) == "second-hand clothing",
      "a second-hand clothes shop reads as one")
check(shop_kind({"shop": "zzz"}) == "second-hand shop",
      "an unknown shop falls back, and never to the bare word 'shop'")

print("\nthe website line (../mapsee 0153 reads it for claims)")
check(own_website({"website": "https://oxfam.org.uk/shop/1"}) == "https://oxfam.org.uk/shop/1",
      "a real domain is kept")
check(own_website({"website": "oxfam.org.uk"}) == "https://oxfam.org.uk",
      "a bare domain is given a scheme")
check(own_website({"website": "https://www.facebook.com/someshop"}) is None,
      "a Facebook page is not a domain anybody can prove they own")
check(own_website({}) is None, "no website tag, no line")

print("\ndetail lines")
lines = secondhand_detail_lines({"shop": "charity", "operator": "Oxfam", "wheelchair": "yes"})
check(any(l.startswith("🎗 Charity: Oxfam") for l in lines),
      "the charity's NAME is emitted — 'is there an Oxfam near me' is the search")
check(any(l.startswith("♿") for l in lines), "accessibility is carried through")
check(not secondhand_detail_lines({"shop": "charity", "charity": "yes"}),
      "charity=yes is a bare flag and produces no line on its own")

print("\none standing row per shop, and it is fleabop's")
EL = {"type": "node", "id": 42, "lat": 51.5, "lon": -0.12,
      "tags": {"name": "Oxfam Marylebone", "shop": "charity", "operator": "Oxfam",
               "addr:housenumber": "91", "addr:street": "Marylebone High St",
               "addr:city": "London", "addr:postcode": "W1U 4RB",
               "opening_hours": "Mo-Sa 09:00-17:30"}}
rows = to_events(EL, {"name": "London", "country": "GB"}, {d: ("09:00", "17:30") for d in range(6)}, 7)
check(len(rows) == 1, "ONE row, not one per open day (../mapsee 0156)")
r = rows[0]
check(r.category == "market", "category is market, the key fleabop already opens onto")
check(r.recurring_days is not None and len(r.recurring_days) == 6,
      "the weekly pattern rides on the row for the roller to move")
check(r.coords_exact is True, "OSM's surveyed point is never geocoded over")
check(r.city == "London", "the city is the SHOP's, from OSM")
check("OpenStreetMap contributors (ODbL)" in (r.description or ""),
      "ODbL attribution is on every row — mapsee_retire_perday_osm keys on it")
check(r.source == "osm-secondhand" and "42" in r.source_id, "identity is the OSM ref")

# The fingerprint carries NO date, which is what makes a re-run update this
# shop's row instead of adding a second one every week.
again = to_events(EL, {"name": "London", "country": "GB"},
                  {d: ("09:00", "17:30") for d in range(6)}, 7)[0]
check(again.fingerprint == r.fingerprint, "the fingerprint is stable across runs")
check(len(r.fingerprint) == 40 and all(c in "0123456789abcdef" for c in r.fingerprint),
      "fingerprint is a sha1 hex digest")
check(r.start_local[:10] not in r.fingerprint,
      "and carries NO date — a dated one would add a row per week per shop")

print("\nthe hub's name is never the shop's town")
noc = dict(EL["tags"]); noc.pop("addr:city")
row = to_events({**EL, "tags": noc}, {"name": "London", "country": "GB"},
                {d: ("09:00", "17:30") for d in range(6)}, 7)[0]
check(row.city is None, "no addr:city means NO city — never the hub's")
check(" in London" not in (row.description or ""),
      "and the prose does not invent one either (the Everett/Seattle bug)")

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("all second-hand selector cases pass")
