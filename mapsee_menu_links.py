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
import urllib.error
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
    r"popmenu\.com|menufy\.com|beyondmenu\.com|spoton\.com|owner\.com|"
    # DoorDash's white-label storefront host: every restaurant on it gets
    # <name>.order.online, which is why the leading (^|\.) matters here.
    r"order\.online)$", re.I)
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
#
# `/order/status` is the other shape: order TRACKING, not ordering. Two of the
# four genuinely-dead links the first prune run found were this —
# trustgso.com/order/status and episil.net/order/status — on domains that are
# not restaurants at all. They matched purely because the path begins /order,
# and a page that tells you where your courier is has never sold anybody dinner.
NOT_ORDER_PATH = re.compile(
    r"/(gift|gifts|giftcard|giftcards|gift-card|gift-cards|donate|donation|tip|tips|"
    r"merch|market|jobs|careers|feedback|survey|waitlist|reservations?|"
    r"status|track|tracking|receipt|confirmation)(/|$|\?|#)", re.I)


# THE SHOP IS GONE, and the platform says so in the URL it redirects you to.
# Live: a venue's Slice page redirected to
#   slicelife.com/?display_disabled_shop_notice=true&disabled_shop_name=Mumbai…
# which is a real page on a real ordering host with a real title, so neither the
# host check nor destination_verdict had anything to object to. The URL is
# telling us in plain words that the shop is disabled; read it.
NOT_ORDER_QUERY = re.compile(
    r"(disabled_shop|shop_disabled|store_closed|closed_shop|unavailable|not_found)", re.I)


def looks_like_ordering(url: str) -> bool:
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        path = u.path or ""
        if NOT_ORDER_PATH.search(path):
            return False
        if NOT_ORDER_QUERY.search(u.query or ""):
            return False
        # A PLATFORM'S FRONT DOOR IS NOT A SHOP. Where the shop lives in the
        # PATH — slicelife.com/restaurants/…, toasttab.com/<venue> — a bare root
        # is the marketplace homepage, which is where these links land once the
        # venue is delisted. Where the shop is the SUBDOMAIN
        # (joespizza.square.site, order.toasttab.com/…) the root is the shop, so
        # this only fires when the hostname IS the bare platform domain.
        host = u.hostname.lower()
        bare = host[4:] if host.startswith("www.") else host
        if not path.strip("/") and ORDER_HOSTS.search(bare) and bare.count(".") == 1:
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


TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# Square Online exposes its shop categories as embedded JSON rather than <a>
# hrefs — the rendered page has literally zero links until its JS runs.
SQUARE_CATEGORY_RX = re.compile(
    r'"name"\s*:\s*"([^"]{2,40})"\s*,\s*"updated_date"[^}]*?"site_link"\s*:\s*"([^"]+)"')
FOODY_RX = re.compile(
    r"(menu|food|order|drink|coffee|kitchen|bakery|deli|dinner|lunch|breakfast)", re.I)


# An EMPTY APP SHELL is small. A real page — even one that renders its content in
# JavaScript — ships a catalog, styles and inline state, and runs to tens of KB.
EMPTY_SHELL_BYTES = 10_000


def destination_ok(html: str) -> bool:
    """Given a page we actually RETRIEVED, does it look like a real one?

    A dead ordering link does not 404. Measured 2026-08-12:
    order.chownow.com/order/39625/locations/60228 answers 200 with a 4.1KB React
    shell and NO <title>, while a live ChowNow location answers 200 with 28KB
    titled "Peloton Cafe - Seattle - …". Status codes cannot separate them.

    A MISSING TITLE ALONE IS NOT DEATH, and believing it was is the second false
    positive this check has produced. Square Online stores routinely ship no
    <title> at all while being perfectly alive: basiliskpdx.square.site is 51KB
    with eight categories including "Online Menu", "Drinks" and "Salads", and
    stone-way-cafe.square.site is 43KB with "Stone Way Cafe - Online Menu". Both
    would have been stripped. pelotonseattle.square.site merely happened to say
    "Store | Peloton Cafe", and one confirming example is not evidence.

    So the signature is a title AND a body: dead means we got a page with no
    title that is also too small to contain a shop. That fits the 4.1KB ChowNow
    shell and excludes every live Square store measured.

    ONLY call this with HTML you really got. Passing it a failed fetch is the
    other trap — see destination_verdict, which exists because this function on
    its own called Toast, Uber Eats and DoorDash dead.
    """
    if not html:
        return False
    m = TITLE_RX.search(html)
    if m and m.group(1).strip():
        return True
    return len(html) >= EMPTY_SHELL_BYTES


# HOSTS WE HAVE PROVEN WE CANNOT JUDGE, and the evidence for each.
#
# ubereats.com — measured 2026-08-12. Our request gets HTTP 404 for a store page
# while the SAME full URL opened in a real browser loads
# "Order Zizzi (Bankside) Menu Delivery | London | Uber Eats", and a second one
# redirects to def.uber.com/en/challenge, their bot-defence challenge. So the
# 404 is a refusal wearing a status code, and 15 of the 19 links a prune run
# wanted to cut were on this host — Five Guys, PizzaExpress, Prezzo, Zizzi,
# The Real Greek — across five countries. Chains do not delist in unison.
#
# The honest resolution is to stop asking, not to dress up as a browser: working
# around somebody's bot defences to check their pages is not a thing to do
# because it would be convenient. A link here stays exactly as it is.
UNVERIFIABLE_HOSTS = re.compile(r"(^|\.)(ubereats\.com|uber\.com)$", re.I)


def destination_verdict(url: str, timeout: int = 20) -> str:
    """"alive" | "dead" | "unknown". Only "dead" is grounds for dropping a link.

    THE BUG THIS EXISTS TO PREVENT, caught before it shipped anywhere but one
    city: fetch() returns None for every failure — 403, timeout, non-HTML — and
    the first version of this check read None as "dead". The big ordering hosts
    all block scrapers, so order.toasttab.com, www.toasttab.com and
    ubereats.com every one came back as zero bytes and would have been judged
    dead. That is not a few false positives, it is most of the map's order links,
    including a Toast URL this repo's own test suite asserts is valid.

    So the three cases are kept apart and the default is to KEEP the link. A
    destination we were not allowed to look at is not a destination we know is
    broken, and the prior behaviour — no check at all — was already fail-open.
    Only a page we genuinely fetched and found empty is called dead.
    """
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if host and UNVERIFIABLE_HOSTS.search(host):
        return "unknown"                    # proven to refuse us; see the note above
    try:
        req = urllib.request.Request(url, headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = (r.headers.get("content-type") or "").lower()
            if "html" not in ct and "text" not in ct:
                return "unknown"            # a PDF menu is not evidence either way
            html = r.read(400_000).decode("utf-8", "replace")
            return "alive" if destination_ok(html) else "dead"
    except urllib.error.HTTPError as e:
        # Gone is gone. Everything else — 403 bot-block, 429, 5xx — is us being
        # refused, not the restaurant being closed.
        return "dead" if e.code in (404, 410) else "unknown"
    except Exception:
        return "unknown"


def refine_storefront(url: str, html: str) -> str:
    """Point at the food, not at the shop's front door.

    pelotonseattle.square.site/ is a real, live Square store on a genuine
    ordering host — and the featured block on its landing page is titled "Gift
    Cards", so somebody hungry arrives at gift cards. The menus are one level in,
    at /shop/online-menu/<id>. The link was not broken; it was pointed at the
    wrong shelf, which is the same failure as the gift-card paths NOT_ORDER_PATH
    already refuses, wearing a different disguise.

    Only refines a ROOT url — a link that already names a path was chosen
    deliberately and is left alone. Falls back to the original on anything
    unexpected, so Square changing this JSON costs us a refinement, never a link.
    """
    try:
        if (urllib.parse.urlparse(url).path or "/").strip("/"):
            return url                                    # already specific
    except Exception:
        return url
    if not html:
        return url
    cats = [(n, l.replace("\\/", "/")) for n, l in SQUARE_CATEGORY_RX.findall(html)]
    foody = [(n, l) for n, l in cats if FOODY_RX.search(n) or FOODY_RX.search(l)]
    if not foody:
        return url
    # "Online Menu" is the ordering flow where a store has one; otherwise the
    # first food-ish category, which still beats the gift-card landing.
    best = next((c for c in foody if "online" in (c[0] + c[1]).lower()), foody[0])
    try:
        return urllib.parse.urljoin(url, best[1])
    except Exception:
        return url


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


SB_ATTEMPTS = 3


def sb(path: str, method: str = "GET", body=None, prefer: str = ""):
    """One Supabase call, retrying only what is worth retrying.

    A 5xx from the REST edge is not an answer, it is the absence of one:
    "upstream connect error or disconnect/reset before headers" is Envoy saying
    it could not reach Postgres, and it clears on its own. This used to be a bare
    urlopen, so the first one ended the job with a urllib traceback — nine frames
    of stack ending in `HTTPError: HTTP Error 503`, which reads like a bug in
    this file and is not. mapsee_health_check._rpc has retried 5xx for a while;
    this is the same rule, and a 4xx still returns immediately because a missing
    table or a bad key does not improve with waiting.

    On exhaustion it raises with the status and body rather than a stack, so a
    provider outage is one greppable line. It is still a FAILURE — an hour-long
    outage is not something to swallow — it is just an honest one.
    """
    last = ""
    for attempt in range(SB_ATTEMPTS):
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None)
        req.add_header("apikey", SERVICE_KEY)
        req.add_header("authorization", f"Bearer {SERVICE_KEY}")
        req.add_header("content-type", "application/json")
        if prefer:
            req.add_header("prefer", prefer)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as ex:
            if ex.code < 500:
                raise                                   # settled: our request is wrong
            last = f"HTTP {ex.code}: {ex.read().decode('utf-8', 'replace')[:200]}"
        except urllib.error.URLError as ex:
            last = f"{type(ex).__name__}: {ex.reason}"
        if attempt < SB_ATTEMPTS - 1:
            print(f"[menu-links] {method} {path.split('?')[0]}: {last} — retrying")
            time.sleep(2 ** (attempt + 1))              # 2s, then 4s
    raise RuntimeError(
        f"Supabase unreachable after {SB_ATTEMPTS} attempts ({method} "
        f"{path.split('?')[0]}): {last}. Nothing was written. If the next "
        f"scheduled run is green, it was a transient upstream outage.")


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
