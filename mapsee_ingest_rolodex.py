#!/usr/bin/env python3
"""
mapsee_ingest_rolodex.py - import improv/sketch shows and open jams from The
Rolodex (https://rolodex.lol), a community-run directory of independent comedy
troupes. Public JSON API, documented at https://rolodex.lol/developers: no key,
no auth, 5-minute cache, 60 req/min/IP.

Why this source is worth an adapter of its own: it lists the shows the ticketing
APIs do not. A four-person troupe playing a 40-seat back room on a Thursday has
no Ticketmaster, SeatGeek or DICE presence, and often no Eventbrite either - the
tickets are sold on Crowdwork or Ticket Tailor, or at the door. That is exactly
the `theater` long tail mapsee wants and cannot reach any other way.

    python mapsee_ingest_rolodex.py --config rolodex_sources.json \
        --store feeds_events.json

Two endpoints, two shapes:
    /api/events        show_name, date, time, venue, ticket_link, troupe, co_performers
    /api/events/jams   jam_name,  date, time, location, location_address, host, price

PLACING THEM IS THE WHOLE PROBLEM. The API carries no coordinates and its `venue`
is free text - sometimes a bare room name ("Shanghai Room"), sometimes a full
address glued on ("Studio East, 10718 N.E. 68th St. Kirkland, WA 98033"). The
sync geocodes street+city+region and DROPS anything with no street, so a bare
name would silently vanish. So: parse an address out of the string when there is
one, otherwise look the room up in the config's venue book, otherwise skip it
loudly - an unplaceable event is worse than no event, and the log line is how a
new room gets added to the book.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

_TAG = re.compile(r"<[^>]+>")
# A street line starts with a number: "10718 N.E. 68th St", "203 N 36th St".
# PO boxes and suite-only fragments deliberately do not match.
_STREET = re.compile(r"^\d+[\w\-]*\s+\S")
# Trailing state (+ optional ZIP) at the end of an address blob.
_STATE_TAIL = re.compile(r"\s+(?P<st>[A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")
# Words that are part of the STREET, never the start of a city name. Without
# this, peeling a city off "10718 N.E. 68th St. Kirkland" takes "St. Kirkland"
# and leaves "10718 N.E. 68th" as the street - which geocodes to nothing.
_STREET_WORD = frozenset("""
st street ave avenue rd road blvd boulevard way dr drive ln lane pl place ct court
ter terrace pkwy parkway hwy highway cir circle sq square aly alley walk row
n s e w ne nw se sw north south east west
suite ste apt unit fl floor rm room #
""".split())


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _norm_room(s: str) -> str:
    """Venue-book key: lowercase, no punctuation, no leading 'the'."""
    s = re.sub(r"[^a-z0-9& ]+", " ", (s or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^the ", "", s)


def split_venue(raw: str) -> Tuple[str, Optional[Dict[str, str]]]:
    """('Room name', {address, city, region} | None) from the API's free-text venue.

    "Studio East, 10718 N.E. 68th St. Kirkland, WA 98033" is one string doing
    three jobs. Split on commas and look for the first part that starts with a
    house number; everything before it is the room, that part is the street, and
    a trailing "City, ST" is picked off the end when present. Anything that does
    not fit this shape is returned as a bare name for the venue book to answer.
    """
    parts = [p.strip(" .") for p in (raw or "").split(",") if p.strip(" .")]
    if not parts:
        return (raw or "").strip(), None
    idx = next((i for i, p in enumerate(parts) if _STREET.match(p)), None)
    if idx is None or idx == 0:
        # No street, or the whole string IS a street with no room name: either
        # way there is no name/address split to make here.
        return (raw or "").strip(), None
    name = ", ".join(parts[:idx]).strip()
    # ONE blob, one peeler. The commas in these strings are decorative - the same
    # address turns up as "…St. Kirkland, WA 98033" and "…St, Kirkland, WA 98033"
    # and both have to land in the same place, so normalise to spaces and peel
    # from the right: ZIP, then state, then the city.
    street, city, region = _peel_city_state(" ".join(parts[idx:]))
    if not street:
        return name or (raw or "").strip(), None
    loc: Dict[str, str] = {"address": street}
    if city:
        loc["city"] = city
    if region:
        loc["region"] = region
    return name or street, loc


def _peel_city_state(blob: str) -> Tuple[str, Optional[str], Optional[str]]:
    """('10718 N.E. 68th St.', 'Kirkland', 'WA') from an address blob.

    Takes up to two trailing Capitalised words as the city, but stops at anything
    that is part of a street ("St", "Ave", "NE"). That guard is the whole trick:
    without it "68th St. Kirkland" reads as a city of "St. Kirkland".
    """
    s = re.sub(r"\s+", " ", (blob or "").replace(",", " ")).strip(" .")
    m = _STATE_TAIL.search(s)
    if not m:
        return s, None, None
    region = m.group("st")
    head = s[:m.start()].strip(" .")
    toks = head.split()
    city_toks: List[str] = []
    while toks and len(city_toks) < 2:
        t = toks[-1]
        if t.strip(".#").lower() in _STREET_WORD:
            break
        if not re.match(r"^[A-Z]", t):
            break
        city_toks.insert(0, toks.pop())
    if not city_toks:
        return head, None, region
    return " ".join(toks).strip(" ."), " ".join(city_toks).strip(" ."), region


def place(raw_venue: str, site: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, str]]]:
    """Room name + a geocodable location, or (name, None) if we cannot place it."""
    name, inline = split_venue(raw_venue or "")
    if inline and inline.get("address"):
        return name, {
            "address": inline["address"],
            "city": inline.get("city") or site.get("default_city"),
            "region": inline.get("region") or site.get("default_region"),
            "country": site.get("default_country"),
        }
    book = {_norm_room(k): v for k, v in (site.get("venues") or {}).items()}
    hit = book.get(_norm_room(name))
    if hit and hit.get("address"):
        return name, {
            "address": hit["address"],
            "city": hit.get("city") or site.get("default_city"),
            "region": hit.get("region") or site.get("default_region"),
            "country": hit.get("country") or site.get("default_country"),
        }
    return name, None


def _start_local(date: str, tm: Optional[str]) -> Optional[str]:
    date = (date or "").strip()[:10]
    if len(date) != 10:
        return None
    t = (tm or "19:00:00").strip()[:8]
    if len(t) == 5:
        t += ":00"
    if not re.match(r"^\d{2}:\d{2}:\d{2}$", t):
        t = "19:00:00"
    return f"{date}T{t}"                              # naive local; sync -> UTC via coords


def to_show(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    name = _clean(ev.get("show_name"))
    start = _start_local(ev.get("date"), ev.get("time"))
    if not name or not start:
        return None
    room, loc = place(ev.get("venue") or "", site)
    if not loc:
        print(f"[rolodex] unplaceable venue, skipped: {room!r} ({name})")
        return None
    troupe = (ev.get("troupe") or {}).get("name")
    lineup = [str(p).strip() for p in (ev.get("co_performers") or []) if str(p or "").strip()]
    nev = NormalizedEvent(
        source="rolodex",
        source_id=str(ev.get("id") or make_fingerprint(name, start[:10], room)),
        name=name,
        description=_clean(ev.get("description")),
        start_local=start,
        venue_name=room,
        address=loc.get("address"), city=loc.get("city"),
        region=loc.get("region"), country=loc.get("country"),
        category=site.get("category", "theater"),
        promoter=_clean(troupe),
        lineup=lineup,
        poster_image_url=ev.get("image_url") or None,
        ticket_url=ev.get("ticket_link") or None,
    )
    nev.fingerprint = make_fingerprint(name, start[:10], room)
    return nev


def to_jam(ev: Dict[str, Any], site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    """A jam is a drop-in session, not a ticketed show - same category, and the
    address arrives in its own field rather than glued to the room name."""
    name = _clean(ev.get("jam_name"))
    start = _start_local(ev.get("date"), ev.get("time"))
    if not name or not start:
        return None
    room = _clean(ev.get("location")) or ""
    addr = _clean(ev.get("location_address"))
    loc: Optional[Dict[str, str]] = None
    if addr:
        _, parsed = split_venue(f"x, {addr}")          # reuse the street/city parser
        if parsed and parsed.get("address"):
            loc = {
                "address": parsed["address"],
                "city": parsed.get("city") or site.get("default_city"),
                "region": parsed.get("region") or site.get("default_region"),
                "country": site.get("default_country"),
            }
    if not loc:
        room, loc = place(room, site)
    if not loc:
        print(f"[rolodex] unplaceable jam venue, skipped: {room!r} ({name})")
        return None
    nev = NormalizedEvent(
        source="rolodex",
        source_id=str(ev.get("id") or make_fingerprint(name, start[:10], room)),
        name=name,
        description=_clean(ev.get("description")),
        start_local=start,
        venue_name=room or loc.get("address"),
        address=loc.get("address"), city=loc.get("city"),
        region=loc.get("region"), country=loc.get("country"),
        category=site.get("category", "theater"),
        promoter=_clean(ev.get("host")),
    )
    nev.fingerprint = make_fingerprint(name, start[:10], nev.venue_name)
    return nev


def _get(session, url: str, params: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    try:
        r = session.get(url, params=params, timeout=30)
    except Exception as exc:
        print(f"[rolodex] {url} failed: {exc}")
        return None
    if r.status_code != 200:
        print(f"[rolodex] {url} HTTP {r.status_code}")
        return None
    try:
        body = r.json()
    except Exception as exc:
        print(f"[rolodex] {url} bad JSON: {exc}")
        return None
    return body if isinstance(body, list) else (body.get("events") or body.get("data") or [])


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    base = (site.get("base_url") or "https://rolodex.lol").rstrip("/")
    now = datetime.now(timezone.utc)
    params = {
        "from": now.strftime("%Y-%m-%d"),
        "to": (now + timedelta(days=int(site.get("within_days", 365)))).strftime("%Y-%m-%d"),
    }
    kept = 0
    rows = _get(session, f"{base}/api/events", params)
    for ev in rows or []:
        nev = to_show(ev, site)
        if nev:
            store.upsert(nev)
            kept += 1
    if site.get("jams", True):
        jams = _get(session, f"{base}/api/events/jams", params)
        for ev in jams or []:
            nev = to_jam(ev, site)
            if nev:
                store.upsert(nev)
                kept += 1
    print(f"[rolodex] {site.get('name','?')}: kept {kept} events")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import The Rolodex improv listings into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[rolodex] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[rolodex] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
