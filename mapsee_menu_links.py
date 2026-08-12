#!/usr/bin/env python3
"""
mapsee_menu_links.py — find the REAL "order pickup" link for food venues.

WHY THIS EXISTS. Measured against production on 2026-08-12: of 400 upcoming
food-category events carrying a "Tickets / info" link, ZERO pointed anywhere you
could place an order. 352 went to meetup.com, the rest to brewery event pages, a
university calendar and Eventbrite. The product used to render an "Order pickup"
button on all of them purely because the category said food; ../mapsee now
requires the URL to prove itself, which is correct and leaves the map with no
pickup options at all — because it has none.

This pass creates them. For each food venue it resolves the venue's own website,
fetches it, and looks for a link to somewhere an order can actually be placed.
When it finds one it appends a dedicated line to the event description:

    🛒 Order: https://order.toasttab.com/online/…

which the client parses into the "Order pickup" button (and re-validates, so one
bad write here cannot put a false promise on the map).

WHAT IT DELIBERATELY DOES NOT DO. It does not guess. A venue whose site has no
ordering link gets nothing written, which leaves the map honest rather than
hopeful — the whole reason the button was broken was a guess dressed as a fact.

THE STRATEGIC POINT, since it looks like we are advertising competitors: yes,
most links this finds will be Toast, Square or DoorDash. That is fine. It makes
the food map immediately useful with no restaurant onboarding, and it creates
the one outreach opener that has been shown to work here — "you are already on
the map, with your DoorDash link" — which is a true statement that starts a
conversation about keeping 100%, rather than a cold ask.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_menu_links.py [--limit 200] [--dry-run] [--verbose]
"""
import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
UA = "mapsee-aggregator/1.0 (+https://mapsee.me; menu-link discovery)"

# Kept BEHAVIOURALLY identical to looksLikeOrdering() in ../mapsee/site/js/app.js
# — not textually, because a JS regex literal must escape its slashes and Python
# need not. Verified by running both over the same URL set and diffing. The
# aggregator deciding one thing and the button deciding another is exactly how
# the 400-of-400 mislabel happened; if these two ever disagree, the client wins
# (it re-validates) and this pass silently writes lines that never render.
ORDER_HOSTS = re.compile(
    r"(^|\.)(toasttab\.com|toast\.site|square\.site|clover\.com|chownow\.com|"
    r"doordash\.com|ubereats\.com|grubhub\.com|slicelife\.com|olo\.com|"
    r"popmenu\.com|menufy\.com|beyondmenu\.com|spoton\.com|owner\.com)$", re.I)
# `/menu` is deliberately absent — see the matching note in ../mapsee/site/js/app.js.
# A dry run over 144 live venues matched a town website and a tourism board on it.
ORDER_PATH = re.compile(r"/(order|order-online|online-ordering|order-now|orderonline)(/|$|\?|#)", re.I)

# Aggregators and socials are never a venue's "own site" for this purpose.
NOT_A_VENUE_SITE = re.compile(
    r"(^|\.)(meetup\.com|facebook\.com|instagram\.com|eventbrite\.[a-z.]+|"
    r"ticketmaster\.[a-z.]+|seatgeek\.com|linktr\.ee|google\.com|yelp\.com)$", re.I)


# A known ordering HOST is not enough — the path has to be about food.
#
# Measured on the first live OSM pull: of 13 "order links" found in central
# Seattle, SEVEN were gift cards. toasttab.com/<venue>/giftcards,
# squareup.com/gift/<id>/order (which even matches /order), and a Toast /market
# retail page. Every one is on a genuine ordering host, and none of them feeds
# anybody tonight. Same lie as the original category-based button, wearing the
# uniform of the fix.
NOT_ORDER_PATH = re.compile(
    r"/(gift|gifts|giftcard|giftcards|gift-card|gift-cards|donate|donation|tip|tips|"
    r"merch|market|jobs|careers|feedback|survey|waitlist|reservations?)(/|$|\?|#)", re.I)


def looks_like_ordering(url: str) -> bool:
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        path = u.path or ""
        if NOT_ORDER_PATH.search(path):
            return False
        return bool(ORDER_HOSTS.search(u.hostname) or ORDER_PATH.search(path))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Booking a table — the same bar as ordering, held deliberately high
# ---------------------------------------------------------------------------
# A reservation platform HOST is unambiguous: opentable.com/r/<venue> is a
# booking page whoever links it, in any language, with no path guessing. That is
# where almost all of the real yield is.
BOOKING_HOSTS = re.compile(
    r"(^|\.)(opentable\.[a-z.]+|resy\.com|sevenrooms\.com|exploretock\.com|tock\.com|"
    r"thefork\.[a-z.]+|quandoo\.[a-z.]+|bookatable\.[a-z.]+|tablecheck\.com|"
    r"chope\.co|eatapp\.co|formitable\.com|superbexperience\.com|libro\.jp|"
    r"guestplan\.com|dinesuperb\.com)$", re.I)

# PATHS ARE THE DANGEROUS HALF, so this list is shorter than it wants to be.
# `/menu` taught the lesson on the ordering side: a town website, an events
# platform and a tourism board all have a nav item called Menu, and matching the
# obvious word pulled all three in. The equivalent trap here is `/book` — a
# bookshop, a book club, a "book a room", a "booking terms" page. So only
# phrasings that cannot mean anything except a table are listed, and bare /book
# and bare /reserve are deliberately absent.
BOOKING_PATH = re.compile(
    r"/(reservations?|book-?(a-?)?table|reserve-?(a-?)?table|table-?booking|"
    r"book-?your-?table)(/|$|\?|#)", re.I)

# Even on a real booking host or path, these are not a table.
NOT_BOOKING_PATH = re.compile(
    r"/(gift|gifts|giftcard|giftcards|gift-card|gift-cards|book-?a-?room|rooms?|"
    r"hotel|event-?space|private-?hire|venue-?hire|terms|policy|policies|"
    r"cancel|cancellation|book-?club|bookshop|books)(/|$|\?|#)", re.I)


def looks_like_booking(url: str) -> bool:
    """True only for a link that unambiguously books a TABLE.

    Mirrors looksLikeBooking in ../mapsee/site/js/app.js. As with ordering, the
    two are verified behaviourally rather than textually — a JS regex literal
    escapes its slashes and Python's need not — and the client re-validates, so
    a disagreement fails safe as a line that never renders a button.
    """
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        path = u.path or ""
        if NOT_BOOKING_PATH.search(path):
            return False
        return bool(BOOKING_HOSTS.search(u.hostname) or BOOKING_PATH.search(path))
    except Exception:
        return False


def _same_site(a: str, b: str) -> bool:
    """Do two URLs belong to the same registrable-ish site?

    Compares the last two labels, so www.joes.com, order.joes.com and joes.com
    agree while joes.com and elliotts.com do not. Deliberately crude: it is a
    trust boundary, not a public-suffix implementation, and the cost of being
    slightly too strict is one missed link.
    """
    try:
        ha = (urllib.parse.urlparse(a).hostname or "").lower()
        hb = (urllib.parse.urlparse(b).hostname or "").lower()
    except Exception:
        return False
    if not ha or not hb:
        return False
    return ha.split(".")[-2:] == hb.split(".")[-2:]


def booking_link_on(page_url: str, html: str):
    """The first link on this page that genuinely books a table AT THIS PLACE.

    A PATH match is only trusted on the venue's OWN site. A known booking host is
    trusted anywhere — opentable.com/r/joes identifies the restaurant in the URL,
    so whoever links it, it books Joe's. But `/reservations` on somebody else's
    domain identifies THEIR restaurant, and following it books the wrong table.

    Not hypothetical: the first run of this matcher gave WingDome the URL
    elliottsoysterhouse.com/reservations, scraped off a link on WingDome's own
    page. Well-formed, plausible, and it would have sent a hungry person to a
    different restaurant's booking form — the exact failure mode this repo
    already has a note about for coordinates.
    """
    return _link_on(page_url, html, looks_like_booking, BOOKING_HOSTS)


def _link_on(page_url: str, html: str, predicate, trusted_hosts):
    if not html:
        return None
    seen = set()
    for m in LINK_RX.finditer(html):
        # DECODE HTML ENTITIES. An href in real markup is entity-encoded, so a
        # query string arrives as `?a=1&amp;b=2` and every parameter after the
        # first is silently corrupted. Live example from the first booking run:
        # opentable.com/r/neb-reservations-seattle?restref=1278643&amp;lang=en-US.
        # The old order-link pass had the same hole.
        href = html_mod.unescape(m.group(1).strip())
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(page_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        if not predicate(absolute):
            continue
        host = urllib.parse.urlparse(absolute).hostname or ""
        if trusted_hosts.search(host) or _same_site(absolute, page_url):
            return absolute
    return None


def sb(path: str, method: str = "GET", body=None, prefer: str = ""):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("content-type", "application/json")
    if prefer:
        req.add_header("prefer", prefer)
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None


def fetch(url: str, timeout: int = 15):
    """One GET, best-effort. Never raises — a dead venue site is normal."""
    try:
        req = urllib.request.Request(url, headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = (r.headers.get("content-type") or "").lower()
            if "html" not in ct and "text" not in ct:
                return None, r.geturl()
            return r.read(400_000).decode("utf-8", "replace"), r.geturl()
    except Exception:
        return None, url


LINK_RX = re.compile(r"""<a[^>]+href=["']([^"'#][^"']*)["']""", re.I)


def order_link_on(page_url: str, html: str):
    """The first link on this page that genuinely orders food FROM THIS PLACE.

    Shares _link_on with booking, so it gained two fixes found while adding the
    booking matcher: HTML entities in the href are decoded, and a PATH-based
    match (`/order`) is only trusted on the venue's own site. A known ordering
    host is still trusted anywhere, because those URLs name the venue.
    """
    return _link_on(page_url, html, looks_like_ordering, ORDER_HOSTS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="venues to examine")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    q = ("events?select=id,title,place_name,description,lat,lon"
         f"&category=eq.food&is_private=eq.false&hidden_at=is.null&starts_at=gt.{now}"
         f"&order=starts_at.asc&limit={args.limit}")
    rows = sb(q) or []

    # One venue, one fetch. Food venues repeat across many events and the site is
    # a property of the PLACE, so keying the work by venue and applying the
    # result to every event there is the difference between 200 fetches and 12.
    by_venue = {}
    for e in rows:
        if "🛒 Order:" in (e.get("description") or ""):
            continue                                   # already has one
        key = f"{round(float(e['lat']), 4)},{round(float(e['lon']), 4)}"
        by_venue.setdefault(key, []).append(e)

    examined = fetched = found = written = 0
    for key, events in by_venue.items():
        examined += 1
        # The venue's own site, from a link we already hold. Anything on the
        # aggregator/social list is not a venue site and is skipped rather than
        # followed — following it is how you end up "confirming" a Meetup page.
        site = None
        for e in events:
            m = re.search(r"Tickets / info: (\S+)", e.get("description") or "")
            if not m:
                continue
            host = urllib.parse.urlparse(m.group(1)).hostname or ""
            if NOT_A_VENUE_SITE.search(host):
                continue
            site = m.group(1)
            break
        if not site:
            continue

        # If the link we hold IS already an order page, no fetch needed.
        target = site if looks_like_ordering(site) else None
        if not target:
            html, final = fetch(site)
            fetched += 1
            target = order_link_on(final, html)
            if not target:
                # One retry on the obvious place, then give up. A third attempt
                # almost never lands (same rule as the venue-enrich skill).
                base = f"{urllib.parse.urlparse(site).scheme}://{urllib.parse.urlparse(site).hostname}"
                html2, final2 = fetch(base + "/menu")
                fetched += 1
                target = order_link_on(final2, html2) or (final2 if looks_like_ordering(final2) else None)
            time.sleep(0.5)                            # be a polite guest

        if not target:
            continue
        found += 1
        name = events[0].get("place_name") or events[0].get("title") or key
        if args.verbose or args.dry_run:
            print(f"  {name[:38]:<40} -> {target[:76]}")
        if args.dry_run:
            continue

        for e in events:
            desc = (e.get("description") or "").rstrip()
            sb(f"events?id=eq.{e['id']}", "PATCH",
               {"description": f"{desc}\n\n🛒 Order: {target}"},
               prefer="return=minimal")
            written += 1

    print(f"venues examined {examined} · pages fetched {fetched} · "
          f"order links found {found} · events updated {written}"
          + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
