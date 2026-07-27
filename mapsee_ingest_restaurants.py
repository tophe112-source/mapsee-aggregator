#!/usr/bin/env python3
"""
mapsee_ingest_restaurants.py - turn restaurants' OWN websites into pickup-window
events with a read-only menu preview, via schema.org Restaurant JSON-LD.

Above-board by construction: only the restaurant's own site (or a page it
controls) is read - the same published-for-SEO JSON-LD posture as the event
ingester. Wix Restaurants, Squarespace, BentoBox, Popmenu, and Toast sites emit
Restaurant / openingHoursSpecification / hasMenu(MenuSection/MenuItem) blocks.

Each restaurant becomes one event PER OPEN DAY for the next N days (like the
markets ingester expands recurring schedules), category "food", with the menu
as a text preview in the description and a link back. These land UNCLAIMED via
the normal sync; the owner can claim the listing, onboard payouts, and use
"Import menu from a link" to make it an orderable storefront.

    python mapsee_ingest_restaurants.py --config restaurant_sources.json --store feeds_events.json

Config (restaurant_sources.json):
    { "days_ahead": 21,
      "restaurants": [
        { "url": "https://example-cafe.com",
          "name": "Example Cafe",                    # optional - else from JSON-LD
          "hours": {"days": [1,2,3,4,5], "open": "11:00", "close": "19:00"} }  # optional override
      ] }
`hours` (0=Sun..6=Sat) overrides/fills openingHoursSpecification when a site
doesn't publish one. Restaurants with NO hours from either source are skipped
(there is no honest window to list).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint
from mapsee_ingest_jsonld import _LD_RX, _parse_ld, _iter_items, _clean, _address_parts, _geocode, _save_geo_cache, UA

_DOW = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}


def _types(n: Any) -> str:
    t = (n or {}).get("@type")
    return ",".join(t if isinstance(t, list) else [t or ""])


def _find_restaurant(docs: List[Any]) -> Optional[Dict[str, Any]]:
    for doc in docs:
        for item in _iter_items(doc):
            if any(k in _types(item) for k in
                   ("Restaurant", "FoodEstablishment", "CafeOrCoffeeShop", "Bakery", "BarOrPub", "FastFoodRestaurant")):
                return item
    return None


def _hours_from_spec(spec: Any) -> Dict[int, tuple]:
    """openingHoursSpecification -> {weekday(0=Sun): (opens, closes)}"""
    out: Dict[int, tuple] = {}
    for s in (spec if isinstance(spec, list) else [spec] if spec else []):
        if not isinstance(s, dict):
            continue
        opens, closes = str(s.get("opens") or "")[:5], str(s.get("closes") or "")[:5]
        if not opens or not closes or closes <= opens:
            continue
        days = s.get("dayOfWeek") or []
        for d in (days if isinstance(days, list) else [days]):
            key = str(d).rsplit("/", 1)[-1].strip().lower()   # accepts schema.org URLs and names
            if key in _DOW:
                out[_DOW[key]] = (opens, closes)
    return out


def _collect_menu_items(node: Any, out: List[Dict[str, Any]], depth: int = 0):
    if depth > 6 or not node or len(out) >= 24:
        return
    if isinstance(node, list):
        for x in node:
            _collect_menu_items(x, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if "MenuItem" in _types(node):
        name = _clean(node.get("name"))
        offers = node.get("offers")
        offers = offers[0] if isinstance(offers, list) and offers else offers
        price = (offers or {}).get("price") if isinstance(offers, dict) else node.get("price")
        if name:
            out.append({"name": name, "price": _clean(str(price)) if price is not None else None})
    for k in ("hasMenu", "hasMenuSection", "hasMenuItem", "itemListElement", "@graph"):
        if node.get(k):
            _collect_menu_items(node[k], out, depth + 1)


def ingest_restaurant(store: EventStore, session, cfg: Dict[str, Any], days_ahead: int) -> int:
    url = cfg["url"]
    try:
        r = session.get(url, timeout=25)
        r.raise_for_status()
    except Exception as exc:
        print(f"[restaurants] {url} fetch failed: {exc}")
        return 0
    docs = [d for d in (_parse_ld(b) for b in _LD_RX.findall(r.text)) if d is not None]
    biz = _find_restaurant(docs) or {}
    name = _clean(cfg.get("name")) or _clean(biz.get("name"))
    if not name:
        print(f"[restaurants] {url}: no Restaurant JSON-LD and no config name - skipped")
        return 0
    # hours: site's openingHoursSpecification, else the config override
    hours = _hours_from_spec(biz.get("openingHoursSpecification"))
    h_cfg = cfg.get("hours") or {}
    if not hours and h_cfg.get("days") and h_cfg.get("open") and h_cfg.get("close"):
        hours = {int(d) % 7: (h_cfg["open"], h_cfg["close"]) for d in h_cfg["days"]}
    if not hours:
        print(f"[restaurants] {name}: no open hours published or configured - skipped")
        return 0
    # location: JSON-LD address/geo, else one cached Photon lookup
    loc = {"address": biz.get("address"), "name": biz.get("name")}
    parts = _address_parts(loc)
    geo = biz.get("geo") or {}
    lat, lon = geo.get("latitude"), geo.get("longitude")
    if lat is None and not (parts["address"] and parts["city"]):
        q = ", ".join(x for x in (name, parts["city"], parts["region"]) if x)
        lat, lon = _geocode(session, q)
        if lat is None:
            print(f"[restaurants] {name}: no address and geocode failed - skipped")
            return 0
    # menu preview (read-only; ordering starts when the owner claims + onboards)
    menu: List[Dict[str, Any]] = []
    hm = biz.get("hasMenu")
    if isinstance(hm, str) and hm.startswith("http"):      # menu on its own page
        try:
            mr = session.get(hm, timeout=25)
            time.sleep(1.0)
            for b in _LD_RX.findall(mr.text):
                _collect_menu_items(_parse_ld(b), menu)
        except Exception:
            pass
    else:
        _collect_menu_items(hm, menu)
    if not menu:
        for d in docs:
            _collect_menu_items(d, menu)
    today = datetime.now()
    lines = [f"- {m['name']}" + (f" - ${m['price']}" if m.get("price") and m["price"].replace(".", "", 1).isdigit() else "")
             for m in menu[:12]]
    desc = "🛒 Order-ahead pickup. This listing was imported from the restaurant's site - " \
           "if this is your restaurant, claim it to take orders here."
    if lines:
        desc += f"\n\nMenu (as of {today:%b %d}, from the restaurant's site):\n" + "\n".join(lines)
    kept = 0
    for d in range(0, days_ahead):
        day = today + timedelta(days=d)
        win = hours.get((day.weekday() + 1) % 7)           # python Mon=0 -> our Sun=0
        if not win:
            continue
        date_key = day.strftime("%Y-%m-%d")
        ev = NormalizedEvent(
            source="restaurant",
            source_id=f"{url}#{date_key}",
            name=f"{name} · pickup",
            description=desc,
            start_local=f"{date_key}T{win[0]}:00",
            end_local=f"{date_key}T{win[1]}:00",
            venue_name=name,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            address=parts["address"], city=parts["city"], region=parts["region"],
            country=parts["country"], postal_code=parts["postal_code"],
            category=cfg.get("category", "food"),
            ticket_url=url,
        )
        ev.fingerprint = make_fingerprint(f"{name} · pickup", date_key, name)
        store.upsert(ev)
        kept += 1
    print(f"[restaurants] {name}: {kept} pickup windows, {len(menu)} menu lines")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import restaurants (pickup windows + menu preview) into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="feeds_events.json")
    a = ap.parse_args(argv)
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    days_ahead = min(int(cfg.get("days_ahead", 21)), 45)
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    store = EventStore(a.store)
    total = 0
    for rc in cfg.get("restaurants", []):
        try:
            total += ingest_restaurant(store, session, rc, days_ahead)
        except Exception as exc:                          # one site failing must not abort the sweep
            print(f"[restaurants] {rc.get('url','?')} FAILED: {exc}")
        time.sleep(1.0)
    store.save()
    _save_geo_cache()
    print(f"[restaurants] done: +{total} windows; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
