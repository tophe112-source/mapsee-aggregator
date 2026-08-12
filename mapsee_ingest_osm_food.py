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
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from mapsee_ingest import EventStore, NormalizedEvent, make_fingerprint
from mapsee_menu_links import looks_like_ordering, order_link_on, fetch as fetch_page

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


def overpass(area, delay=2.0, tries=4):
    """Places of interest in one bounding box, with a website to check.

    Overpass is a free, shared, volunteer-run service and it says so: measured
    while building this, a third city in quick succession answered 429, and a
    wide box answers 504. Both are it asking us to slow down, and a scheduled job
    that treats them as failures just re-asks harder tomorrow. So: exponential
    backoff, and the delay is between AREAS as well as retries.
    """
    s, w, n, e = area["bbox"]
    parts = "".join(
        f'nwr["amenity"="{a}"]["name"]({s},{w},{n},{e});' for a in AMENITIES)
    q = f"[out:json][timeout:120];({parts});out tags center;"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            time.sleep(delay)
            return data.get("elements", [])
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(delay * (3 ** attempt))
    raise last


def order_url_for(tags, session_delay=0.5):
    """The place's own ordering page, or None. Never a guess."""
    # OSM sometimes carries it outright. Cheapest possible answer.
    for k in ("website:menu", "contact:menu", "menu:url", "order:url"):
        v = tags.get(k)
        if v and looks_like_ordering(v):
            return v
    site = tags.get("website") or tags.get("contact:website")
    if not site:
        return None
    if not site.startswith("http"):
        site = "https://" + site
    if looks_like_ordering(site):
        return site
    html, final = fetch_page(site)
    time.sleep(session_delay)
    return order_link_on(final, html)


def to_events(el, area, order_url, hours, days_ahead):
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
    desc = (f"{name} — {kind} in {area['name']}. Order for pickup on their own site; "
            f"mapsee.me is not taking the order.\n\n"
            f"🛒 Order: {order_url}\n\n"
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
            ticket_url=order_url,
        ))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import OSM takeaway places that have a real order link.")
    ap.add_argument("--config", default="osm_food_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one area by name (substring)")
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--max-places", type=int, default=60, help="per area, per run")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    areas = [x for x in cfg.get("areas", [])
             if not a.only or a.only.lower() in str(x.get("name", "")).lower()]
    store = None if a.dry_run else EventStore(a.store)
    tot_seen = tot_hours = tot_order = tot_events = 0

    for area in areas:
        try:
            els = overpass(area, a.delay)
        except Exception as exc:
            print(f"[osm-food] {area['name']} overpass FAILED: {exc}")
            continue
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
        tot_seen += len(els)
        tot_hours += len(cands)

        made = 0
        for el, hrs in cands[: a.max_places]:
            try:
                url = order_url_for(el.get("tags", {}))
            except Exception:
                url = None
            if not url:
                continue
            tot_order += 1
            for nev in to_events(el, area, url, hrs, a.days_ahead):
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
              f"{made} slots", flush=True)

    if store:
        store.save()
    print(f"[osm-food] done: {tot_seen} places seen · {tot_hours} readable · "
          f"{tot_order} with an order link · {tot_events} slots"
          + (" (dry run, nothing written)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
