#!/usr/bin/env python3
"""
mapsee_ingest_osm_secondhand.py — second-hand shops, for the fleabop door.

READ mapsee_ingest_osm_food.py's header FIRST. This is its sibling: the other
adapter that ingests BUSINESSES from OpenStreetMap rather than events somebody
published. Everything that header says about ODbL, about hubs, about tiling and
about never inventing a city applies here unchanged, and most of the machinery
is imported from it rather than copied.

WHY THIS EXISTS. fleabop.com is the thinnest door that ships — measured
2026-08-16, 2,552 events in 30 days, 56 cities with >=5 and 31 with >=10 — and
its weakness is not only the city count. A flea market is a WEEKEND EVENT, so
the door is empty on a Tuesday morning. A charity shop is open on Tuesday. This
adapter is what makes fleabop answer "where can I find second-hand things near
me, now", which is the question its creed promises to answer and its inventory
currently cannot.

Measured 2026-08-19 against OSM, grouping by addr:city:

    shop=second_hand + shop=charity      38,218 venues, 15,043 city-tagged
                                         689 cities >=5, 225 cities >=10
    …gated on readable opening_hours      6,601 venues
                                         248 cities >=5, 78 cities >=10

The gated row is the honest one and it is still ~2.5x fleabop. It is also a
FLOOR: addr:city is present on only 35-39% of the pool, and that is a limit of
the grouping instrument, not of the data — the coordinate is always there.

NO NEW CATEGORY KEY. Every row here is `market`, which already exists in
MAPSEE_CATEGORY_KEYS and which fleabop already opens onto. That is the whole
reason this is cheap: a key no lens opens onto reaches only mapsee.me, and the
cycling case was lost on exactly that. Nothing in ../mapsee needs editing.

WHERE THE BAR MOVED, and it is the one judgement in this file worth arguing
with. The food adapter requires A PUBLISHED WAY TO TRANSACT — an order or
booking link — because a restaurant merely existing is not a listing, and that
is what lets outreach honestly say nobody at the venue put this here. A charity
shop has no order link and never will. You walk in.

So the bar here is READABLE OPENING HOURS, and the claim is that hours ARE the
transaction for this kind of place: the promise the pin makes is "you can go now
and it will be open", which is the same promise the food map makes and the same
one we can actually keep. It is a weaker bar than the food adapter's and it is
deliberately the only one that moved:

  * ONLY shops whose opening_hours parse cleanly. Same parser, same refusal to
    approximate. A shop we cannot read stays off the map.
  * ONLY shops with a name.
  * NO menu items are created, exactly as with food, so has_storefront still
    reads false and a claimed shop is still offered a storefront at 0%.
  * --require-website is there for the stricter reading, where a shop that
    published a website is the closest thing to "they put it out there
    themselves". It costs roughly two thirds of the supply; measure before
    turning it on.

WHAT THIS DOES NOT DO, and it is the cheap half of the job. It fetches NOBODY'S
WEBSITE. The food adapter's expensive and impolite half is loading venue pages
to discover order links; there is nothing to discover here, because the
`website` tag goes straight into the Website line that ../mapsee 0153 reads for
claiming. So this adapter is a guest on Overpass and on no one else's server,
and --max-places bounds run time rather than being a politeness budget.

ATTRIBUTION. OSM data is ODbL. Every record carries the source in its
description, as the food adapter's do, and mapsee_retire_perday_osm.py keys on
that same "OpenStreetMap contributors" line.

Env:  none (Overpass is public; be polite with --delay)
Run:  python mapsee_ingest_osm_secondhand.py --config osm_secondhand_sources.json \
          --store feeds_events.json [--dry-run] [--only london] [--warm-cache]
"""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
from mapsee_menu_links import NOT_A_VENUE_SITE
# Imported, not copied. parse_opening_hours is 80 lines of refusals, each one
# bought with a live failure — the 27:00 past-midnight bug silently dropped four
# Portland late-night rows and reported itself as a moderation block. Forking it
# would fork those bugs back in, and test_osm_food.py only guards the original.
from mapsee_ingest_osm_food import (
    parse_opening_hours, area_bbox, tiles, cache_path,
    load_cursor, save_cursor, clean_public_phone, window_at,
)

UA = "mapsee-aggregator/1.0 (+https://mapsee.me; OSM second-hand discovery)"
OVERPASS = "https://overpass-api.de/api/interpreter"
CURSOR_PATH = "osm_secondhand_cursor.json"

# BIGGER TILES THAN osm-food, because this selector is an order of magnitude
# sparser. osm-food's 0.35 degrees is sized for restaurants — Seattle holds
# 9,765 of them — and inheriting it here cut a 50-mile hub into ~35 cells that
# each came back nearly empty.
#
# That is not merely wasteful, it is the whole cost of the job: measured on the
# first production sweep (run 32335784569), London ran 24 tiles in 217s, ~9s
# each, so a hub cost ~5 minutes and thirteen of them ran the warm step past its
# 45-minute cap. Per-request overhead dominates when the answer is small, so the
# fix is fewer and larger, not a longer timeout.
#
# Measured directly against Overpass on 2026-08-20, same selector:
#   0.80 x 1.20 deg  ->  1,600 elements, 0.6 MB, 14s
#   1.45 x 2.33 deg  ->  2,774 elements, 1.0 MB, 111s   (a WHOLE London hub)
#
# So one request per hub is possible and is NOT taken: 111s is a long time to
# hold a free volunteer-run service on one query, and a 504 on it would lose the
# entire metro instead of a corner. 1.2 degrees puts a 50-mile hub at ~4 cells —
# roughly a ninth of the requests, each still answering in well under a minute,
# and a failure still costs a quarter of a city rather than all of it.
TILE_DEG = 1.2

# Shops that ARE the category, whatever else they are tagged.
#   shop=second_hand 19,954   shop=charity 18,264   shop=antiques 14,816
# antiques is in because fleabop's own copy sells "vintage fairs" — a vintage
# shop is the permanent version of the stall.
DIRECT_SHOPS = ("second_hand", "charity", "antiques")

# A normal shop that sells ONLY used goods is a second-hand shop, and
# `second_hand=only` says so 29,256 times. But 15,853 of those — 55% — are
# shop=car, i.e. used car lots, which are not what anybody opens fleabop for.
#
# So this is an ALLOW-list rather than a deny-list. A deny-list is a promise to
# have thought of every shop type that will ever be tagged, and the cost of
# getting it wrong is a used-car dealership on a map about clothing swaps. The
# cost of an allow-list being wrong is a missing pin, which is the direction
# this repo already errs in everywhere else.
#
# shop=clothes alone is 8,101 of these and is the best fit in the file: "a
# jacket that fit somebody else" is fleabop's own first line.
SECOND_HAND_ONLY_SHOPS = (
    "clothes", "books", "furniture", "shoes", "bag", "jewelry", "music",
    "video_games", "toys", "sports", "bicycle", "electronics", "computer",
    "musical_instrument", "houseware", "kitchen", "baby_goods", "art",
    "camera", "hifi", "video", "appliance", "carpet", "watches", "fabric",
    "craft", "collector", "games", "department_store", "variety_store",
)

# What the shop SELLS, for the detail line.
_SELLS = {
    "clothes": "Clothing", "books": "Books", "furniture": "Furniture",
    "shoes": "Shoes", "bag": "Bags", "jewelry": "Jewellery", "music": "Records",
    "video_games": "Video games", "toys": "Toys", "sports": "Sports kit",
    "bicycle": "Bicycles", "electronics": "Electronics", "computer": "Computers",
    "musical_instrument": "Instruments", "houseware": "Homeware",
    "kitchen": "Kitchenware", "baby_goods": "Baby goods", "art": "Art",
    "camera": "Cameras", "hifi": "Hi-fi", "video": "Film & TV",
    "appliance": "Appliances", "carpet": "Rugs", "watches": "Watches",
    "fabric": "Fabric", "craft": "Craft supplies", "collector": "Collectables",
    "games": "Games", "antiques": "Antiques & vintage",
}


def wanted(tags):
    """Is this element in the pool? The Python half of the Overpass selector.

    Defence in depth on purpose: the query already filters server-side, so this
    should never reject anything in practice. It is here because the query is a
    STRING built from these same tuples, and this is the half a test can reach —
    a regex typo that widened the selector would otherwise stay invisible until
    a used-car lot turned up in fleabop.
    """
    shop = str(tags.get("shop") or "").strip().lower()
    if not shop:
        return False
    if shop in DIRECT_SHOPS:
        return True
    return (str(tags.get("second_hand") or "").strip().lower() == "only"
            and shop in SECOND_HAND_ONLY_SHOPS)


def selector(s, w, n, e):
    """The Overpass union for one tile. Filters server-side so the 15,853 used
    car lots are never transferred at all — Overpass is free and volunteer-run,
    and the politest query is the one that asks for less."""
    parts = [f'nwr["shop"="{k}"]["name"]({s},{w},{n},{e});' for k in DIRECT_SHOPS]
    alt = "|".join(SECOND_HAND_ONLY_SHOPS)
    parts.append(
        f'nwr["second_hand"="only"]["shop"~"^({alt})$"]["name"]({s},{w},{n},{e});')
    return "".join(parts)


def _overpass_one(bbox, delay=2.0, tries=4):
    """One tile, backing off rather than re-asking harder. See osm_food: a third
    city in quick succession answers 429 and a wide box answers 504, and both are
    the service asking us to slow down."""
    q = f"[out:json][timeout:180];({selector(*bbox)});out tags center;"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            time.sleep(delay)
            return data.get("elements", [])
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(delay * (3 ** attempt))
    raise last


def overpass(area, delay=2.0, tries=4):
    """(elements, complete). A failed tile loses a square, not the metro.

    `complete` is why the caller's save_places is conditional: a partial answer
    is fine to USE and not fine to KEEP for thirty days behind a run that
    reports itself healthy. That lesson is the food adapter's, paid for by two
    Seattle tiles that would have under-covered the metro until September.
    """
    cells = tiles(area_bbox(area), max_deg=TILE_DEG)
    out, seen, failed = [], set(), 0
    for i, cell in enumerate(cells, 1):
        try:
            els = _overpass_one(cell, delay=delay, tries=tries)
        except Exception as exc:
            failed += 1
            print(f"[osm-2nd]   {area['name']} tile {i}/{len(cells)} failed: {exc}", flush=True)
            continue
        for el in els:
            k = (el.get("type"), el.get("id"))
            if k not in seen:
                seen.add(k)
                out.append(el)
        if len(cells) > 1:
            print(f"[osm-2nd]   {area['name']} tile {i}/{len(cells)}: "
                  f"+{len(els)} ({len(out)} unique)", flush=True)
    if failed:
        print(f"[osm-2nd]   {area['name']}: {failed} of {len(cells)} tiles did not answer — "
              f"this area is INCOMPLETE this run and will NOT be cached", flush=True)
    return out, failed == 0


# The place cache, same contract as the food adapter's: keyed on area name AND
# box, because "London at 20 miles" and "London at 50 miles" are different sets
# of shops and a narrow run must not poison a wide one for thirty days —
# silently, since a smaller list looks exactly like a quiet week.
def load_places(cache_dir, area, max_age_days, bbox):
    want = tuple(round(v, 6) for v in bbox)
    try:
        with open(cache_path(cache_dir, area["name"]), encoding="utf-8") as fh:
            blob = json.load(fh)
        got = blob.get("bbox")
        got = tuple(round(v, 6) for v in got) if got else None
        if got != want:
            print(f"[osm-2nd] {area['name']}: cached list covers a different box "
                  f"— refetching", flush=True)
            return None, False
        age = (time.time() - float(blob.get("fetched_at", 0))) / 86400.0
        if age <= max_age_days and blob.get("elements"):
            print(f"[osm-2nd] {area['name']}: {len(blob['elements'])} shops from cache "
                  f"({age:.1f}d old)", flush=True)
            return blob["elements"], True
    except Exception:
        pass
    return None, False


def save_places(cache_dir, area, elements, bbox):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path(cache_dir, area["name"]), "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(),
                       "bbox": list(bbox), "elements": elements}, fh)
    except Exception as exc:
        print(f"[osm-2nd] could not cache {area['name']}: {exc}", flush=True)


def shop_kind(tags):
    """The human noun for this shop. Never the bare word 'shop'."""
    shop = str(tags.get("shop") or "").strip().lower()
    if shop == "charity":
        return "charity shop"
    if shop == "antiques":
        return "antiques shop"
    if shop == "second_hand":
        return "second-hand shop"
    sells = _SELLS.get(shop)
    return f"second-hand {sells.lower()}" if sells else "second-hand shop"


def secondhand_detail_lines(tags):
    """Stable marker lines for Mapsee's business details card.

    Same emoji-prefixed shape business_detail_lines uses for food — the product
    parses the marker, so inventing a new prefix here renders it as prose.
    """
    lines = []
    phone = clean_public_phone(tags.get("phone") or tags.get("contact:phone"))
    if phone:
        lines.append(f"☎ Phone: {phone}")
    # A charity shop's OPERATOR is the thing people actually search for — "is
    # there an Oxfam near me" — and `charity=yes`, which is 534 of the 546 uses
    # of that key, is a bare flag carrying none of it.
    if str(tags.get("shop") or "").lower() == "charity":
        org = (tags.get("operator") or tags.get("brand") or "").strip()
        if org:
            lines.append(f"🎗 Charity: {org}")
    sells = []
    if _SELLS.get(str(tags.get("shop") or "").lower()):
        sells.append(_SELLS[str(tags.get("shop") or "").lower()])
    for raw in re.split(r"[;,]", str(tags.get("second_hand") or "")):
        v = _SELLS.get(re.sub(r"[\s]+", "_", raw.strip().lower()))
        if v and v not in sells:
            sells.append(v)
    if sells:
        lines.append("🧺 Sells: " + " · ".join(sells[:5]))
    wheelchair = str(tags.get("wheelchair") or "").lower()
    access = {"yes": "Wheelchair accessible", "limited": "Limited wheelchair access",
              "no": "Not wheelchair accessible"}.get(wheelchair)
    if access:
        lines.append(f"♿ Accessibility: {access}")
    return lines


def own_website(tags):
    """The shop's OWN domain, or None. The line ../mapsee 0153 reads for claims.

    A Facebook page is not a domain anybody can prove they own, so it is not
    emitted — spread would reject it anyway, and a line that can only ever be
    rejected is a line not worth writing.
    """
    site = tags.get("website") or tags.get("contact:website")
    if not site:
        return None
    if not site.startswith("http"):
        site = "https://" + site
    try:
        host = urllib.parse.urlparse(site).hostname or ""
    except Exception:
        return None
    return site if host and not NOT_A_VENUE_SITE.search(host) else None


def to_events(el, area, hours, days_ahead):
    """ONE standing row per shop, carrying its weekly pattern.

    Not one row per open day. That model cost 6.1 rows per venue and ../mapsee
    0156 replaced it: starts_at/ends_at is only ever the NEXT open window, the
    hourly roller moves it forward, and the row never expires the way a dated
    clone does. It is also what gives the shop a STABLE id for a claim or a
    share link. is_standing (0158) demotes these in Nearby, which is exactly why
    several thousand permanent shops cannot crowd fleabop's actual swaps off
    the list.
    """
    tags = el.get("tags", {})
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    if lat is None or lon is None:
        return []
    name = (tags.get("name") or "").strip()
    if not name:
        return []
    addr = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x) or None
    kind = shop_kind(tags)

    lines = []
    site = own_website(tags)
    if site:
        lines.append(f"🌐 Website: {site}")
    lines.extend(secondhand_detail_lines(tags))
    body = ("\n".join(lines) + "\n\n") if lines else ""
    # THE HUB'S NAME IS NOT THE SHOP'S TOWN. The food adapter shipped that bug
    # in both the field and the prose — an Everett restaurant described as being
    # "in Seattle" and pinned twenty-seven miles south of itself, because the
    # invented city was then fed to the geocoder. OSM's own town, or no phrase.
    town = (tags.get("addr:city") or tags.get("addr:suburb") or "").strip()
    desc = (f"{name} — {kind}{f' in {town}' if town else ''}.\n\n"
            f"{body}"
            f"Public business details from OpenStreetMap contributors (ODbL). "
            f"Hours and details can change; the shop can claim this listing to correct them.")

    today = datetime.now(timezone.utc).date()
    slot = None
    for i in range(max(days_ahead, 8)):        # 8 guarantees every weekday is seen
        d = today + timedelta(days=i)
        span = hours.get(d.weekday())
        if span:
            slot = (d, span[0], span[1])
            break
    if not slot:
        return []
    d, o, c = slot

    # The OSM identity with NO DATE in it, so a re-run UPDATES this shop's row.
    # It also sidesteps make_fingerprint's name|date|place basis, which would
    # collapse two Oxfams in one city into a single row now the date is gone.
    osm_ref = f"{el.get('type','n')}/{el.get('id')}"
    return [NormalizedEvent(
        source="osm-secondhand",
        source_id=osm_ref,
        fingerprint=hashlib.sha1(f"osm-secondhand|{osm_ref}".encode("utf-8")).hexdigest(),
        name=name,
        description=desc,
        start_local=f"{d.isoformat()}T{o}:00",
        end_local=f"{d.isoformat()}T{c}:00",
        venue_name=name,
        latitude=float(lat), longitude=float(lon),
        address=addr,
        # OSM's own city or NOTHING. A missing town is a gap; a confidently
        # wrong one is what somebody drives to — and _addr_parts feeds the
        # geocoder, so a wrong city moves the pin as well as the label.
        city=town or None,
        region=area.get("region"),
        country=area.get("country"),
        postal_code=tags.get("addr:postcode"),
        # THE KEY THAT ALREADY HAS A DOOR. market -> fleabop.com.
        category="market",
        ticket_url=site,
        # OSM's point is surveyed; the address text is derived from it, not the
        # other way round. Never geocode over it.
        coords_exact=True,
        # 0=Monday…6=Sunday, exactly as parse_opening_hours produced it.
        recurring_days={str(k): [v[0], v[1]] for k, v in sorted(hours.items())},
    )]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Import OSM second-hand, charity and vintage shops for fleabop.")
    ap.add_argument("--config", default="osm_secondhand_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one area by name (substring)")
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--max-places", type=int, default=400,
                    help="per area, per run. Higher than the food adapter's 60 because "
                         "nothing here fetches a third-party website — this bounds run "
                         "time, it is not a politeness budget.")
    ap.add_argument("--require-website", action="store_true",
                    help="stricter bar: only shops that published a website. Closer to "
                         "the food adapter's 'they put it out there themselves', and it "
                         "costs roughly two thirds of the supply.")
    ap.add_argument("--ignore-cursor", action="store_true",
                    help="start at the first candidate and do not advance the cursor "
                         "(backfill: re-examine shops already imported)")
    ap.add_argument("--places-cache", default="osm_secondhand_cache",
                    help="where the per-area OSM shop lists live")
    ap.add_argument("--cache-days", type=float, default=30.0,
                    help="re-ask Overpass only when the cached list is older than this")
    ap.add_argument("--radius-miles", type=float,
                    help="override every area's radius for THIS run (config stays as it "
                         "is). The cache keys on the resulting box, so a narrow run "
                         "cannot poison a wide one.")
    ap.add_argument("--warm-cache", action="store_true",
                    help="fetch and cache each area's shop list, then stop. One "
                         "sequential pass before a matrix fan-out, so the jobs do not "
                         "all hit Overpass at the same moment and then back off against "
                         "our own traffic.")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    areas = [x for x in cfg.get("areas", [])
             if not a.only or a.only.lower() in str(x.get("name", "")).lower()]
    # Warming touches neither the store nor the cursor: it is a fetch, not a run.
    # Creating an EventStore would rewrite the store with nothing in it, and
    # moving the cursor would skip shops nobody examined.
    store = None if (a.dry_run or a.warm_cache) else EventStore(a.store)
    cursor = load_cursor(CURSOR_PATH)
    tot_seen = tot_ok = tot_rows = 0

    for area in areas:
        if a.radius_miles:
            area = {**area, "radius_miles": a.radius_miles}
            area.pop("bbox", None)              # a radius override beats a stored box
        bbox = area_bbox(area)
        els, from_cache = load_places(a.places_cache, area, a.cache_days, bbox)
        if els is None:
            try:
                els, complete = overpass(area, a.delay)
            except Exception as exc:
                print(f"[osm-2nd] {area['name']} overpass FAILED: {exc}", flush=True)
                continue
            # Use a partial list, never keep one.
            if els and complete and not a.dry_run:
                save_places(a.places_cache, area, els, bbox)

        if a.warm_cache:
            # Count before continuing, or the summary reports 0 shops while the
            # per-area lines report thousands.
            tot_seen += len(els)
            print(f"[osm-2nd] {area['name']}: cache warm ({len(els)} shops)"
                  f"{' [from cache]' if from_cache else ''}", flush=True)
            continue

        cands = []
        for el in els:
            t = el.get("tags", {})
            if not wanted(t):
                continue
            if a.require_website and not own_website(t):
                continue
            hrs = parse_opening_hours(t.get("opening_hours", ""))
            if not hrs:
                continue
            cands.append((el, hrs))
        # STABLE ORDER, then a CURSOR. Without both, --max-places caps this
        # feature permanently at the same first N candidates, and nothing
        # reports it: the store dedupes, so a re-run examining identical shops
        # adds nothing and simply looks idle.
        cands.sort(key=lambda c: (c[0].get("type", ""), c[0].get("id", 0)))
        tot_seen += len(els)
        tot_ok += len(cands)

        # THE WINDOW CAN NEVER BE LONGER THAN THE CANDIDATE LIST — window_at is
        # osm-food's, and this is the bug it was extracted for. Slice-then-top-up
        # examines every shop TWICE when there are fewer candidates than
        # --max-places; caught on this adapter's first live run, where Edinburgh
        # at 4 miles reported 104 rows over 52 shops. Latent in osm-food (cap 60,
        # Seattle 3,134) and active here, because this cap is 400.
        #
        # Shared rather than copied so there is ONE implementation under
        # test_osm_food.py's eight cases, three of which fail against the old
        # version. Two correct copies drift; one tested copy does not.
        n = len(cands)
        if not n:
            print(f"[osm-2nd] {area['name']}: {len(els)} shops, none with readable hours",
                  flush=True)
            continue
        start = 0 if a.ignore_cursor else int(cursor.get(area["name"], 0)) % n
        window = window_at(cands, start, a.max_places)
        if not a.dry_run and not a.ignore_cursor:
            cursor[area["name"]] = (start + len(window)) % n

        made = 0
        for el, hrs in window:
            for nev in to_events(el, area, hrs, a.days_ahead):
                # to_events sets a date-free fingerprint from the OSM identity.
                # Overwriting it with make_fingerprint would restore the per-day
                # multiplication AND merge two branches of one chain into a row.
                if not nev.fingerprint:
                    nev.fingerprint = make_fingerprint(
                        nev.name, (nev.start_local or "")[:10], nev.venue_name, nev.city)
                if store:
                    store.upsert(nev)
                made += 1
        tot_rows += made
        # flush=True: this runs for tens of minutes and Python buffers stdout
        # when it is not a TTY. In CI the log is the only window into the job.
        print(f"[osm-2nd] {area['name']}: {len(els)} shops, {len(cands)} with readable hours, "
              f"examined {start}-{start + len(window)}, {made} rows", flush=True)

    if store:
        store.save()
    if not a.dry_run and not a.warm_cache:
        save_cursor(cursor, CURSOR_PATH)
    if a.warm_cache:
        print(f"[osm-2nd] cache warm for {len(areas)} area(s) · {tot_seen} shops", flush=True)
        return 0
    print(f"[osm-2nd] done: {tot_seen} shops seen · {tot_ok} with readable hours · "
          f"{tot_rows} rows"
          + (" (dry run, nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
