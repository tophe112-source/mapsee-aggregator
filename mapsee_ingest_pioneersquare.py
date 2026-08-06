#!/usr/bin/env python3
"""
mapsee_ingest_pioneersquare.py - import the Visit Pioneer Square neighbourhood
calendar (https://pioneersquare.org/events/) into the Mapsee store.

    python mapsee_ingest_pioneersquare.py --config pioneersquare_sources.json \
        --store feeds_events.json

WHY THIS IS A SCRAPER, WHICH NOTHING ELSE HERE IS
-------------------------------------------------
Every other adapter in this repo reads a documented feed or API. This one reads
HTML, because the site publishes no feed at all. Checked, all of it:

    /wp-json/tribe/events/v1/events   404  - The Events Calendar is not installed
    /wp-json/wp/v2/types              no event custom post type
    /events/?ical=1                   returns the HTML page, not a VCALENDAR
    schema.org JSON-LD on an event    only Yoast's WebPage/BreadcrumbList graph

So there is nothing to consume but the markup. That is a real cost - a theme
change breaks this in a way a feed change would not - and it is worth paying
here because the neighbourhood is dense, walkable, arts-heavy, and almost
entirely absent from the ticketing APIs. Gallery openings, artist talks and
history walks are precisely the long tail Ticketmaster does not index.

The mitigation is source-health.yml: a scraper that silently stops matching
shows up as a source that has gone quiet, and that now opens an issue.

WHERE THE EVENTS COME FROM
--------------------------
The listing page, not events-sitemap.xml. The sitemap carries 761 URLs going
back years; the listing is the site's own "what is coming up" and was 31 events
when this was written. One fetch plus one per event, which is cheap enough to
run daily and polite enough not to need throttling beyond `pause`.

The markup this depends on is narrow, so it is worth naming exactly:

    <div class="business-info"> ... <p><strong>DATE | TIME</strong></p>
                                    <p>VENUE NAME</p>

That <strong> and the <p> after it are the whole contract. If either moves,
this adapter finds nothing and says so rather than guessing.

PLACING THEM IS THE WHOLE PROBLEM - same as rolodex
---------------------------------------------------
The pages carry a venue NAME and nothing else: no street address, no
coordinates, no microdata. mapsee_supabase_sync geocodes street+city+region and
DROPS anything with no street, so a bare name silently vanishes. Hence a venue
book in the config, exactly as mapsee_ingest_rolodex.py does it - and the same
rule: a venue that is not in the book is skipped LOUDLY, because an unplaceable
event is worse than no event and the log line is how the book grows.

The book was built from the site's own directory (/businesses/<slug>/ carries a
"Contact:" block with the street address), not from memory. Venues that have no
directory page are simply absent, and will log until someone adds them.

BLOCKS NON-BROWSER CLIENTS. The site 403s a default user agent, so this sends a
browser one. That is not evasion of a paywall or a login - the pages are public
and unauthenticated - it is the minimum needed to read what a visitor reads.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

# The MapseeAggregator UA every other adapter sends gets a 403 here. A browser
# string is what the pages answer to; see the module docstring.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_TAG = re.compile(r"<[^>]+>")

# "August 09, 2026 | 10:00 AM - 12:00 PM"
# The day may be a range ("August 12-08, 2026"), which the site uses loosely and
# sometimes descending; the FIRST number is the one that means anything, so a
# range yields one event on its first day rather than a guess at a span.
_WHEN = re.compile(
    r"(?P<mon>[A-Z][a-z]{2,8})\s+(?P<day>\d{1,2})(?:\s*[-–]\s*\d{1,2})?\s*,\s*(?P<year>\d{4})"
    r"\s*\|\s*(?P<t1>\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?)"
    r"(?:\s*[-–]\s*(?P<t2>\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?))?"
)

# <p><strong>when</strong></p> <p>venue</p> — see the docstring.
_WHEN_VENUE = re.compile(r"(?is)<p>\s*<strong>(?P<when>.*?)</strong>\s*</p>\s*<p>(?P<venue>.*?)</p>")
_TITLE = re.compile(r"(?is)<title>(.*?)</title>")
_H1 = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
_OG_IMAGE = re.compile(r"(?is)<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']")
_PARA = re.compile(r"(?is)<p[^>]*>(.*?)</p>")
# The "Learn More" button, which is where the actual booking lives - a Shopify
# product page, an Eventbrite listing, the venue's own site. There is no
# og:description or meta description on these pages, so the body copy has to
# come from the markup too; both sit in the col-right content block after the
# <h1>. Attribute order varies, so the whole tag is captured and href picked out.
_LEARN = re.compile(r"(?is)<a\s([^>]*\bclass=[\"'][^\"']*btn-large[^\"']*[\"'][^>]*)>")
_HREF = re.compile(r"(?is)href=[\"']([^\"']+)[\"']")


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _norm_room(s: str) -> str:
    """Venue-book key: lowercase, no punctuation, no leading 'the'.

    Same normalisation as rolodex, so 'The Shop @ LMN Architects' and
    'the shop  @lmn architects' land on one entry.
    """
    s = re.sub(r"[^a-z0-9& ]+", " ", (s or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^the ", "", s)


def parse_when(txt: str) -> Optional[Tuple[str, Optional[str]]]:
    """('2026-08-09T10:00:00', '2026-08-09T12:00:00' | None) from the date line."""
    m = _WHEN.search(txt or "")
    if not m:
        return None
    try:
        day = datetime.strptime(
            f"{m.group('mon')[:3]} {int(m.group('day'))} {m.group('year')}", "%b %d %Y").date()
    except ValueError:
        return None

    def _t(raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        raw = re.sub(r"[.\s]", "", raw).upper()          # "6:30 P.M." -> "6:30PM"
        try:
            return datetime.strptime(raw, "%I:%M%p")
        except ValueError:
            return None

    t1, t2 = _t(m.group("t1")), _t(m.group("t2"))
    if not t1:
        return None
    start = datetime.combine(day, t1.time())
    end = None
    if t2:
        end = datetime.combine(day, t2.time())
        # "4:00 PM - 6:00 AM" is a night that ends tomorrow, not one that ended
        # ten hours before it began.
        if end <= start:
            end += timedelta(days=1)
    return start.strftime("%Y-%m-%dT%H:%M:%S"), (end.strftime("%Y-%m-%dT%H:%M:%S") if end else None)


def body_text(page: str, limit: int = 1200) -> Optional[str]:
    """The event's own copy: the <p> run after the <h1>, up to the next block.

    Regex over nested markup is a bad idea in general, so this deliberately
    stops at the first opening <div> rather than trying to match the closing
    one - the paragraphs sit directly under the heading, and anything after a
    new block is somebody else's content.
    """
    m = _H1.search(page)
    if not m:
        return None
    tail = page[m.end():]
    cut = tail.find("<div")
    if cut != -1:
        tail = tail[:cut]
    paras = [_clean(p) for p in _PARA.findall(tail)]
    text = " ".join(p for p in paras if p)
    if not text:
        return None
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def learn_more(page: str, base_host: str) -> Optional[str]:
    """The external booking link, or None when it just points back at the site."""
    m = _LEARN.search(page)
    if not m:
        return None
    h = _HREF.search(m.group(1))
    if not h:
        return None
    url = html_mod.unescape(h.group(1)).strip()
    if not url.startswith("http") or base_host in url:
        return None
    return url


def place(venue: str, site: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A geocodable location for a venue name, or None if the book has no entry.

    An entry needs an address OR a lat/lon pair. Coordinates are there for the
    places a street address describes badly - a park on a triangle at an
    intersection is the case in hand - and they skip the geocode entirely, which
    is also the safer answer: a fuzzy name lookup for "Pioneer Place Park"
    returns three parks in RENTON before it finds anything in Seattle.
    """
    book = {_norm_room(k): v for k, v in (site.get("venues") or {}).items()}
    hit = book.get(_norm_room(venue))
    if not hit:
        return None
    lat, lon = hit.get("lat"), hit.get("lon")
    if not hit.get("address") and (lat is None or lon is None):
        return None
    return {
        "address": hit.get("address"),
        "city": hit.get("city") or site.get("default_city"),
        "region": hit.get("region") or site.get("default_region"),
        "country": hit.get("country") or site.get("default_country"),
        "postal_code": hit.get("postal_code"),
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
    }


def event_urls(session, site: Dict[str, Any]) -> List[str]:
    listing = site.get("listing") or "https://pioneersquare.org/events/"
    pat = site.get("link_pattern") or r"https://pioneersquare\.org/our-events/[a-z0-9\-]+/"
    try:
        r = session.get(listing, timeout=40)
    except Exception as exc:
        print(f"[pioneersquare] listing {listing} failed: {exc}")
        return []
    if r.status_code != 200:
        print(f"[pioneersquare] listing {listing} HTTP {r.status_code}")
        return []
    urls = sorted(set(re.findall(pat, r.text)))
    if not urls:
        # The single most likely breakage, and it must not look like "no events
        # on this week". source-health.yml turns a quiet source into an issue,
        # but only the log says WHY.
        print("[pioneersquare] !! listing matched no event links - the markup has "
              "probably changed. Check the link_pattern in the config.")
    return urls[: int(site.get("max_events", 200))]


def to_event(url: str, page: str, site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    m = _WHEN_VENUE.search(page)
    if not m:
        print(f"[pioneersquare] no date/venue block: {url}")
        return None
    when = parse_when(_clean(m.group("when")) or "")
    venue = _clean(m.group("venue"))
    # The <h1> is the event's own heading; <title> is the same thing with the
    # site name glued on, so it is only the fallback.
    hm = _H1.search(page)
    name = _clean(hm.group(1)) if hm else None
    if not name:
        tm = _TITLE.search(page)
        name = _clean((tm.group(1).split(" - ")[0] if tm else "")) or None
    if not name or not when or not venue:
        missing = ", ".join(w for w, ok in
                            (("name", name), ("when", when), ("venue", venue)) if not ok)
        print(f"[pioneersquare] incomplete ({missing}): {url}")
        return None
    start, end = when
    loc = place(venue, site)
    if not loc:
        print(f"[pioneersquare] unplaceable venue, skipped: {venue!r} ({name}) - "
              f"add it to `venues` in the config")
        return None
    desc = body_text(page)
    ig = _OG_IMAGE.search(page)
    img = ig.group(1) if ig else None
    nev = NormalizedEvent(
        source="pioneersquare",
        source_id=url.rstrip("/").rsplit("/", 1)[-1],
        name=name,
        description=desc,
        start_local=start,
        end_local=end,
        venue_name=venue,
        address=loc.get("address"), city=loc.get("city"),
        region=loc.get("region"), country=loc.get("country"),
        postal_code=loc.get("postal_code"),
        latitude=loc.get("lat"), longitude=loc.get("lon"),
        category=site.get("category", "community"),
        poster_image_url=img,
        # Where you actually book, when the page offers it; the listing page
        # itself when it does not, so the row always has somewhere to go.
        ticket_url=learn_more(page, "pioneersquare.org") or url,
    )
    nev.fingerprint = make_fingerprint(name, start[:10], venue)
    return nev


def ingest_site(store: EventStore, session, site: Dict[str, Any]) -> int:
    urls = event_urls(session, site)
    pause = float(site.get("pause", 0.25))
    horizon = datetime.now() + timedelta(days=int(site.get("within_days", 400)))
    kept = skipped = 0
    for u in urls:
        try:
            r = session.get(u, timeout=40)
        except Exception as exc:
            print(f"[pioneersquare] {u} failed: {exc}")
            continue
        if r.status_code != 200:
            print(f"[pioneersquare] {u} HTTP {r.status_code}")
            continue
        nev = to_event(u, r.text, site)
        if not nev:
            skipped += 1
        else:
            # A neighbourhood calendar keeps finished events on the listing for a
            # while; the store has no opinion about time, so filter here.
            if nev.start_local and nev.start_local[:10] < datetime.now().strftime("%Y-%m-%d"):
                skipped += 1
            elif nev.start_local and datetime.fromisoformat(nev.start_local) > horizon:
                skipped += 1
            else:
                store.upsert(nev)
                kept += 1
        time.sleep(pause)
    print(f"[pioneersquare] {site.get('name','?')}: kept {kept}, skipped {skipped}, of {len(urls)} listed")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import the Visit Pioneer Square calendar into the Mapsee store.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA,
                            "Accept": "text/html,application/xhtml+xml",
                            "Accept-Language": "en-US,en;q=0.9"})
    store = EventStore(a.store)
    total = 0
    for site in cfg.get("sites", []):
        try:
            total += ingest_site(store, session, site)
        except Exception as exc:
            print(f"[pioneersquare] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[pioneersquare] done: +{total} events; store now holds {len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
