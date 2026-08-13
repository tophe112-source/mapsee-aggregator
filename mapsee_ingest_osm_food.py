#!/usr/bin/env python3
"""
mapsee_ingest_osm_food.py — takeaway places that can actually take an order.

WHY THIS IS DIFFERENT FROM EVERY OTHER ADAPTER HERE, and read this before
extending it. The other 32 adapters ingest EVENTS: things a venue published a
listing for. This one ingests BUSINESSES, from OpenStreetMap, which nobody
published as a listing at all.

That distinction is the whole reason venue outreach can honestly open with "they
came from your public listings; nobody at your end put them there". A restaurant
existing is not a listing. So this adapter is deliberately narrow, and the
narrowness IS the design:

  * ONLY places with a resolvable ORDER link. A restaurant we cannot send an
    order to is a pin with nothing behind it — it makes the map bigger and no
    more useful, and it puts a business on our map for our benefit rather than
    theirs. No order link, no import.
  * ONLY places whose opening hours parse cleanly. We do not invent hours. A
    "hungry right now" map is worse than useless if the place is shut, and OSM's
    opening_hours grammar is deep enough that a confident half-parse is the most
    likely way to get that wrong.
  * NO menu items are created. We are not taking the order — the button goes to
    the shop's own ordering page. `has_storefront` (../mapsee 0148) reads
    event_menu_items, so these correctly read as NOT having a storefront, which
    means a claimed restaurant still gets offered "Sell food for pickup here" at
    0%. That is the onboarding hook, and it only works because we did not
    pretend to be their till.

ATTRIBUTION. OSM data is ODbL. Every record carries the source in its
description and its source ref, and the product's map already credits
OpenStreetMap on every tile.

Env:  none (Overpass is public; be polite with --delay)
Run:  python mapsee_ingest_osm_food.py --config osm_food_sources.json \
          --store feeds_events.json [--dry-run] [--only seattle]
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
from mapsee_menu_links import (
    looks_like_ordering, order_link_on,
    looks_like_booking, booking_link_on,
    destination_verdict, refine_storefront,
    NOT_A_VENUE_SITE, fetch as fetch_page,
)

UA = "mapsee-aggregator/1.0 (+https://mapsee.me; OSM takeaway discovery)"
OVERPASS = "https://overpass-api.de/api/interpreter"

# amenity values worth asking about. cafe and bar are in because a great many
# of them do takeaway coffee and food; fast_food obviously; restaurant obviously.
AMENITIES = ("restaurant", "fast_food", "cafe", "bakery")

DOW = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
_DAYSPEC = re.compile(r"^(mo|tu|we|th|fr|sa|su)(?:\s*-\s*(mo|tu|we|th|fr|sa|su))?$", re.I)
_TIMESPAN = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def parse_opening_hours(spec: str):
    """OSM opening_hours -> {weekday: (open, close)} or None if not confidently readable.

    Supports the shapes that cover most food listings — "Mo-Fr 11:00-22:00",
    "Mo-Su 10:00-20:00; Su off", "24/7", day lists, and several rules separated
    by ';'. Anything else returns None and the place is SKIPPED.

    Deliberately refuses rather than approximates:
      * split service ("11:00-14:00,17:00-22:00") — one schedule row holds one
        span, and collapsing lunch and dinner into one claims they are open
        through the break
      * PH / SH / seasonal / week-number / month rules
      * "sunrise", "sunset", open-ended "11:00+"
    A place we cannot read stays off the map, which is the right way round: the
    cost of skipping it is one missing pin, and the cost of guessing is telling
    somebody hungry to walk to a locked door.
    """
    if not spec:
        return None
    s = spec.strip().lower()
    if s in ("24/7", "24x7"):
        return {d: ("00:00", "23:59") for d in range(7)}
    if re.search(r"\b(ph|sh|easter|sunrise|sunset|week\s|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", s):
        return None
    if "+" in s or "," in s.split(";")[0] and re.search(r"\d,\d|\d\s*,\s*\d{1,2}:", s):
        # a comma between TIME spans is split service; a comma between DAYS is fine
        if re.search(r"\d{2}\s*,\s*\d{1,2}:", s):
            return None
    out = {}
    for rule in s.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        if rule.endswith("off"):
            # _days_in returns None for anything it cannot read, and iterating
            # that crashed the whole run on the first real Overpass response.
            # An unreadable closure is the WORST thing to shrug at: it is the
            # rule that says "shut", so ignoring it would leave the place
            # advertised as open. Refuse the listing outright.
            ds = _days_in(rule[:-3].strip())
            if ds is None:
                return None
            for d in ds:
                out.pop(d, None)
            continue
        m = re.match(r"^(.*?)\s+(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})$", rule)
        if not m:
            return None
        days, span = m.group(1), m.group(2)
        tm = _TIMESPAN.match(span.replace(" ", ""))
        if not tm:
            return None
        o = f"{int(tm.group(1)):02d}:{tm.group(2)}"
        c = f"{int(tm.group(3)):02d}:{tm.group(4)}"
        if c <= o:                       # crosses midnight; one row cannot say that
            return None
        ds = _days_in(days)
        if ds is None:
            return None
        for d in ds:
            out[d] = (o, c)
    return out or None


def _days_in(spec: str):
    """'Mo-Fr' / 'Mo,We,Fr' / 'Mo' -> [0,1,2,3,4]. None if unreadable."""
    spec = spec.strip()
    if not spec:
        return None
    days = []
    for part in spec.split(","):
        m = _DAYSPEC.match(part.strip())
        if not m:
            return None
        a = DOW[m.group(1).lower()]
        b = DOW[m.group(2).lower()] if m.group(2) else a
        d = a
        while True:
            days.append(d)
            if d == b:
                break
            d = (d + 1) % 7
    return days


CURSOR_PATH = "osm_food_cursor.json"


def load_cursor(path=CURSOR_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_cursor(cur, path=CURSOR_PATH):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, indent=1, sort_keys=True)
    except Exception as exc:
        print(f"[osm-food] could not write cursor: {exc}", flush=True)


def area_bbox(area):
    """(s, w, n, e) for an area, from an explicit bbox or a centre + radius.

    centre+radius is the form worth writing new hubs in: "Seattle, 50 miles" is
    a thing somebody can check, and a bbox is four numbers nobody can.
    """
    if area.get("bbox"):
        return tuple(area["bbox"])
    lat, lon = area["center"]
    mi = float(area.get("radius_miles", 50))
    dlat = (mi * 1.609) / 111.0
    dlon = (mi * 1.609) / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def tiles(bbox, max_deg=0.35):
    """Split a bbox into a grid no cell of which is wider than max_deg.

    A 50-mile radius is a 1.45° box, and Overpass will not serve that in one
    piece for four amenity types: the COUNT alone took 76 seconds, and the same
    box asking for tags is far heavier. The service already tells us this — a
    wide box answers 504, which is how Seattle and later New York dropped out of
    whole runs. Tiling turns one request it will refuse into a handful it will
    answer, and each one is small enough to retry cheaply.
    """
    s, w, n, e = bbox
    rows = max(1, math.ceil((n - s) / max_deg))
    cols = max(1, math.ceil((e - w) / max_deg))
    dh, dw = (n - s) / rows, (e - w) / cols
    return [(s + r * dh, w + c * dw, s + (r + 1) * dh, w + (c + 1) * dw)
            for r in range(rows) for c in range(cols)]


def _overpass_one(bbox, delay=2.0, tries=4):
    """One tile. Backs off rather than re-asking harder.

    Overpass is a free, shared, volunteer-run service and it says so: measured
    while building this, a third city in quick succession answered 429, and a
    wide box answers 504. Both are it asking us to slow down.
    """
    s, w, n, e = bbox
    parts = "".join(
        f'nwr["amenity"="{a}"]["name"]({s},{w},{n},{e});' for a in AMENITIES)
    q = f"[out:json][timeout:180];({parts});out tags center;"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
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
    """(elements, complete) — every named food place in an area, tile by tile.

    A tile that will not answer after its retries is REPORTED and skipped rather
    than failing the area: losing one square of a metro is a smaller loss than
    losing the metro, and a silent partial would be worse than either.

    `complete` exists because of what the first live 50-mile run did: 2 of
    Seattle's 35 tiles timed out, the run correctly SAID so — and then cached
    the partial list for thirty days, which would have quietly under-covered two
    squares of the metro until September while every subsequent run reported
    itself perfectly healthy. A partial answer is fine to USE and not fine to
    KEEP.
    """
    bbox = area_bbox(area)
    cells = tiles(bbox)
    out, seen, failed = [], set(), 0
    for i, cell in enumerate(cells, 1):
        try:
            els = _overpass_one(cell, delay=delay, tries=tries)
        except Exception as exc:
            failed += 1
            print(f"[osm-food]   {area['name']} tile {i}/{len(cells)} failed: {exc}", flush=True)
            continue
        for el in els:
            k = (el.get("type"), el.get("id"))
            if k not in seen:
                seen.add(k)
                out.append(el)
        if len(cells) > 1:
            print(f"[osm-food]   {area['name']} tile {i}/{len(cells)}: "
                  f"+{len(els)} ({len(out)} unique)", flush=True)
    if failed:
        print(f"[osm-food]   {area['name']}: {failed} of {len(cells)} tiles did not answer — "
              f"this area is INCOMPLETE this run and will NOT be cached", flush=True)
    return out, failed == 0


# ---------------------------------------------------------------------------
# The places cache
# ---------------------------------------------------------------------------
# A restaurant's EXISTENCE does not change weekly, and until this cache the job
# re-downloaded every place in every area on every run to examine sixty of them:
# 18,282 places pulled from a volunteer-run service to look at 480. That was
# tolerable at a 2-mile radius and is not at 50, where Seattle alone holds 9,765.
#
# So the expensive half is now monthly and the cheap half stays weekly. The
# weekly run walks the cursor through a cached list; Overpass is only asked when
# the cache is older than --cache-days or missing.
def cache_path(cache_dir, area_name):
    safe = re.sub(r"[^a-z0-9]+", "-", area_name.lower()).strip("-")
    return os.path.join(cache_dir, f"{safe}.json")


def load_places(cache_dir, area, max_age_days):
    p = cache_path(cache_dir, area["name"])
    try:
        with open(p, encoding="utf-8") as fh:
            blob = json.load(fh)
        age = (time.time() - float(blob.get("fetched_at", 0))) / 86400.0
        if age <= max_age_days and blob.get("elements"):
            print(f"[osm-food] {area['name']}: {len(blob['elements'])} places from cache "
                  f"({age:.1f}d old)", flush=True)
            return blob["elements"], True
    except Exception:
        pass
    return None, False


def save_places(cache_dir, area, elements):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path(cache_dir, area["name"]), "w", encoding="utf-8") as fh:
            json.dump({"area": area["name"], "fetched_at": time.time(),
                       "elements": elements}, fh)
    except Exception as exc:
        print(f"[osm-food] could not cache {area['name']}: {exc}", flush=True)


def links_for(tags, session_delay=0.5):
    """(order_url, booking_url, site) for one place, from ONE page fetch.

    All three come out of the same request on purpose. The page fetch is the
    expensive and impolite half of this job — we are a guest on somebody's
    server — so asking twice to answer two questions about the same restaurant
    would double the cost of the thing we should be minimising.

    Never a guess, for either link. See looks_like_ordering / looks_like_booking.

    THE SITE is returned as well, and it is the reason the whole claim story
    works: it is the venue's OWN domain, the only evidence an independent
    restaurant has that the place is theirs. It was being fetched and thrown
    away — measured 2026-08-12, 86% of imported restaurants had no way to claim
    themselves while we held their website in a local variable. ../mapsee 0153
    is the other half.
    """
    order = None
    booking = None

    # OSM sometimes carries either outright. Cheapest possible answer, no fetch.
    for k in ("website:menu", "contact:menu", "menu:url", "order:url"):
        v = tags.get(k)
        if v and looks_like_ordering(v):
            order = v
            break
    # Measured across 1,086 Seattle places: `website:reservation` appears ONCE
    # and `reservation` 13 times as a yes/no/required FLAG carrying no link. So
    # tags are close to useless for booking and the page scan below is where the
    # yield is — but reading them costs nothing and they are the most reliable
    # signal when present, because a mapper put them there deliberately.
    for k in ("website:reservation", "reservation:website", "booking:website",
              "contact:booking", "reservation:url"):
        v = tags.get(k)
        if v and looks_like_booking(v):
            booking = v
            break

    site = tags.get("website") or tags.get("contact:website")
    if site and not site.startswith("http"):
        site = "https://" + site
    if not site:
        return order, booking, None

    # A Facebook page is not a domain a restaurant can prove it owns, and
    # emitting it as "Website:" would put facebook.com in front of the claim
    # machinery for no benefit (spread would reject it anyway — but a line that
    # can only ever be rejected is a line not worth writing).
    try:
        host = urllib.parse.urlparse(site).hostname or ""
    except Exception:
        host = ""
    own_site = site if host and not NOT_A_VENUE_SITE.search(host) else None

    if not order and looks_like_ordering(site):
        order = site
    if not booking and looks_like_booking(site):
        booking = site
    if not (order and booking):
        html, final = fetch_page(site)
        time.sleep(session_delay)
        if not order:
            order = order_link_on(final, html)
        if not booking:
            booking = booking_link_on(final, html)

    # VERIFY THE DESTINATION, once, before promising anything about it. Both
    # failures reported on 2026-08-12 were 200s: a ChowNow location that serves
    # an empty React shell, and a Square store whose landing block is gift cards
    # with the menus one level in. Neither a status code nor the URL itself can
    # see either, so the only honest check is to look at the page.
    # Only a destination we genuinely fetched and found EMPTY is dropped.
    # destination_verdict keeps "unknown" separate from "dead" for a reason worth
    # restating here: the big ordering hosts block scrapers, so treating an
    # unfetchable page as dead would delete most of the map's order links.
    if order and destination_verdict(order) == "dead":
        order = None
    elif order:
        # Refinement needs the page, so it only happens when we could read it —
        # which is exactly when refine_storefront has anything to work with.
        ohtml, ofinal = fetch_page(order)
        time.sleep(session_delay)
        if ohtml:
            refined = refine_storefront(ofinal or order, ohtml)
            # NEVER LET A DERIVED URL REPLACE A VALIDATED ONE UNLESS IT IS ALSO
            # VALID. `ofinal` is the URL after redirects, and a redirect can land
            # somewhere that is not an ordering page at all.
            #
            # Live example: Chipotle's own location page links
            # chipotle.com/order#menu — a good link, correctly matched — and
            # fetching it redirects to chipotle.com/, the bare homepage. Handing
            # that to refine_storefront made it the stored link, and the client
            # then rightly refused to call the homepage "Order pickup" and fell
            # through to a generic "Tickets & info" button on a burrito shop.
            # The client's re-validation caught my bad write, which is what it is
            # for; the write should not have happened.
            if refined and looks_like_ordering(refined):
                order = refined
    if booking and destination_verdict(booking) == "dead":
        booking = None
    return order, booking, own_site


def to_events(el, area, order_url, hours, days_ahead, booking_url=None, site=None):
    """One NormalizedEvent per open-day in the window ahead.

    Each is a real, bounded slot rather than a permanent pin, because that is
    what the product's map and its "on now / next up" reading expect — and
    because a takeaway place that has closed for the night should stop being
    offered to somebody hungry at 1am.
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
    kind = tags.get("amenity", "restaurant").replace("_", " ")
    # The opening line has to match what the listing can actually DO, because a
    # place may now arrive with a booking link and no ordering one.
    if order_url and booking_url:
        lead = "Order for pickup or book a table on their own site"
    elif booking_url:
        lead = "Book a table on their own site"
    else:
        lead = "Order for pickup on their own site"
    lines = []
    if order_url:
        lines.append(f"🛒 Order: {order_url}")
    # The product turns this into a "Reserve a table" button, exactly as it does
    # the Order line — and re-validates the URL before rendering either.
    if booking_url:
        lines.append(f"🍽️ Reserve: {booking_url}")
    # Not decoration: this is the line ../mapsee 0153's event_link_domain reads,
    # and it is the ONLY thing that lets the owner of an imported restaurant
    # claim it. Written last so it never out-ranks a transactional link visually,
    # and only when it is the venue's own domain (links_for filters socials).
    if site:
        lines.append(f"🌐 Website: {site}")
    body = ("\n".join(lines) + "\n\n") if lines else ""
    desc = (f"{name} — {kind} in {area['name']}. {lead}; "
            f"mapsee.me is not taking the order.\n\n"
            f"{body}"
            f"Listing from OpenStreetMap contributors (ODbL). "
            f"Is this your place? Claim it on mapsee.me to correct it.")
    out = []
    today = datetime.now(timezone.utc).date()
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        span = hours.get(d.weekday())
        if not span:
            continue
        o, c = span
        out.append(NormalizedEvent(
            source="osm-food",
            source_id=f"{el.get('type','n')}/{el.get('id')}/{d.isoformat()}",
            name=name,
            description=desc,
            start_local=f"{d.isoformat()}T{o}:00",
            end_local=f"{d.isoformat()}T{c}:00",
            venue_name=name,
            latitude=float(lat), longitude=float(lon),
            address=addr, city=area.get("city"), region=area.get("region"),
            country=area.get("country"), postal_code=tags.get("addr:postcode"),
            category="food",
            ticket_url=order_url or booking_url,
        ))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import OSM takeaway places that have a real order link.")
    ap.add_argument("--config", default="osm_food_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one area by name (substring)")
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--max-places", type=int, default=60, help="per area, per run")
    ap.add_argument("--ignore-cursor", action="store_true",
                    help="start at the first candidate and do not advance the cursor "
                         "(backfill: re-examine places already imported)")
    ap.add_argument("--places-cache", default="osm_places_cache",
                    help="where the per-area OSM place lists live")
    ap.add_argument("--cache-days", type=float, default=30.0,
                    help="re-ask Overpass only when the cached list is older than this")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    areas = [x for x in cfg.get("areas", [])
             if not a.only or a.only.lower() in str(x.get("name", "")).lower()]
    store = None if a.dry_run else EventStore(a.store)
    cursor = load_cursor()
    tot_seen = tot_hours = tot_order = tot_booking = tot_events = 0

    for area in areas:
        els, from_cache = load_places(a.places_cache, area, a.cache_days)
        if els is None:
            try:
                els, complete = overpass(area, a.delay)
            except Exception as exc:
                print(f"[osm-food] {area['name']} overpass FAILED: {exc}")
                continue
            # Use a partial list, never keep one. Caching an incomplete pull for
            # thirty days would hide the gap behind a run that looks healthy.
            if els and complete and not a.dry_run:
                save_places(a.places_cache, area, els)
        # Only the ones worth a fetch: a website AND hours we can read. Doing the
        # cheap filters first is what keeps this to a few dozen page loads.
        cands = []
        for el in els:
            t = el.get("tags", {})
            if not (t.get("website") or t.get("contact:website")
                    or any(t.get(k) for k in ("website:menu", "contact:menu", "menu:url", "order:url"))):
                continue
            hrs = parse_opening_hours(t.get("opening_hours", ""))
            if not hrs:
                continue
            cands.append((el, hrs))
        # STABLE ORDER, then a CURSOR. Without both, --max-places made this
        # feature permanently capped: every run examined cands[:60], the same
        # sixty, so 480 of 3,134 candidates would have been the entire ceiling
        # forever and the other 2,654 would never have been looked at once.
        # Nothing would have reported that — the store dedupes, so a weekly run
        # re-examining identical places just adds nothing and looks idle.
        # Same pattern as curation_cursor.json, for the same reason.
        cands.sort(key=lambda c: (c[0].get("type", ""), c[0].get("id", 0)))
        tot_seen += len(els)
        tot_hours += len(cands)

        # --ignore-cursor starts at the beginning and does not move the cursor,
        # so a backfill re-examines places already imported instead of marching
        # past them. Needed because the weekly run can only ever go FORWARD: a
        # change to what we write (the Website line, a verified order link) never
        # reaches a row that was imported before it, and --only-new means the
        # sync would skip it even if it did. It leaves the cursor untouched so a
        # backfill does not also cost the normal rotation its place.
        start = 0 if a.ignore_cursor else int(cursor.get(area["name"], 0)) % max(len(cands), 1)
        window = cands[start : start + a.max_places]
        if len(window) < a.max_places:                 # wrap around the end
            window += cands[: a.max_places - len(window)]
        if not a.dry_run and not a.ignore_cursor:
            cursor[area["name"]] = (start + a.max_places) % max(len(cands), 1)

        made = 0
        for el, hrs in window:
            try:
                url, booking, site = links_for(el.get("tags", {}))
            except Exception:
                url, booking, site = None, None, None
            # A PUBLISHED WAY TO TRANSACT is still the bar — a restaurant merely
            # existing is not a listing, which is what lets outreach honestly say
            # nobody at the venue put this here. Booking a table meets that bar
            # exactly as ordering does: it is a time, a seat and a commitment,
            # and it is arguably more of an event than a pickup order. So the
            # gate widens by one door rather than loosening.
            if not (url or booking):
                continue
            if url:
                tot_order += 1
            if booking:
                tot_booking += 1
            for nev in to_events(el, area, url, hrs, a.days_ahead,
                                 booking_url=booking, site=site):
                nev.fingerprint = make_fingerprint(
                    nev.name, (nev.start_local or "")[:10], nev.venue_name, nev.city)
                if store:
                    store.upsert(nev)
                made += 1
        tot_events += made
        # flush=True because this job runs for tens of minutes and Python buffers
        # stdout when it is not a TTY: a local run of all eight areas printed
        # NOTHING for ten minutes and looked hung. In CI that is worse — the log
        # is the only window into a job whose whole cost is being a polite guest
        # on other people's servers.
        print(f"[osm-food] {area['name']}: {len(els)} places, {len(cands)} with hours+site, "
              f"examined {start}-{start + len(window)}, {made} slots", flush=True)

    if store:
        store.save()
    if not a.dry_run:
        save_cursor(cursor)
    print(f"[osm-food] done: {tot_seen} places seen · {tot_hours} readable · "
          f"{tot_order} with an order link · {tot_booking} bookable · {tot_events} slots"
          + (" (dry run, nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
