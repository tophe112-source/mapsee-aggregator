#!/usr/bin/env python3
"""
test_menu_links.py — what may and may not be called an "Order pickup" link.

This is the repo's "well-formed, plausible, wrong" class of bug, and it has
already shipped once: ../mapsee rendered an Order pickup button on any food
event that had a link, and 400 of 400 upcoming food events pointed somewhere you
could not order — 352 at meetup.com, the rest at brewery event pages and a
university calendar. A yoga class labelled Food & Drink carried one.

The second version failed differently and only a dry run over live venues caught
it: matching a bare `/menu` path pulled in mobilizon.fr/menu, carbondale.info/menu
and winebc.com/menu — an events platform, a town website and a tourism board that
each happen to have a nav item called Menu. Even a true restaurant hit like
surlybrewing.com/menu is "here is what we serve", which is not the promise the
button makes.

So both failures are pinned here. Run: python test_menu_links.py
"""
import sys

from mapsee_menu_links import (
    looks_like_ordering, order_link_on, looks_like_booking, booking_link_on,
    destination_ok, refine_storefront)

CASES = [
    # (url, expected, why)
    ("https://order.toasttab.com/online/joes-pizza", True, "Toast order page"),
    ("https://www.toasttab.com/the-great-lakes-brewing-company/v3", True, "Toast venue page"),
    ("https://joespizza.square.site/", True, "Square Online store"),
    ("https://www.doordash.com/store/joes-123/", True, "DoorDash store"),
    ("https://www.ubereats.com/store/joes", True, "Uber Eats store"),
    ("https://joes.chownow.com/", True, "ChowNow"),
    ("https://joespizza.com/order-online/", True, "explicit ordering path"),
    ("https://joespizza.com/order", True, "explicit ordering path"),

    # GIFT CARDS. A known ordering host is not enough — seven of the first
    # thirteen "order links" the OSM pull found in central Seattle were these.
    ("https://www.toasttab.com/taurus-ox-903-19th-ave-e/giftcards", False, "Toast gift cards"),
    ("https://squareup.com/gift/7VCT1Z6FWEX7B/order", False, "Square gift card — contains /order"),
    ("https://app.squareup.com/gift/MLWT06JXTR38Y/order", False, "same, on the app host"),
    ("https://www.toasttab.com/the-virginia-inn-1937-1st-avenue/market", False, "Toast retail market"),
    ("https://joule.com/donate", False, "donations"),
    ("https://joule.com/careers", False, "jobs"),
    # ORDER TRACKING is not ordering. Both of these were live in production,
    # on domains that are not restaurants, matched purely because /order.
    ("https://www.trustgso.com/order/status", False, "order status page"),
    ("https://www.episil.net/order/status", False, "same, another non-restaurant"),
    ("https://joespizza.com/order/tracking", False, "courier tracking"),
    ("https://joespizza.com/order/confirmation", False, "post-purchase receipt"),
    # …but the ordering paths themselves must survive it
    ("https://joespizza.com/order/", True, "plain /order still orders"),

    # THE SHOP IS GONE and the platform says so in the URL. Live: a venue's Slice
    # page redirected to the marketplace homepage carrying a disabled-shop
    # notice — a real page, on a real ordering host, with a real title, so
    # nothing else had grounds to object.
    ("https://slicelife.com/?display_disabled_shop_notice=true&disabled_shop_name=Mumbai",
     False, "Slice disabled-shop notice"),
    # A PLATFORM'S FRONT DOOR IS NOT A SHOP, where the shop lives in the path.
    ("https://slicelife.com/", False, "marketplace homepage"),
    ("https://www.slicelife.com", False, "same, with www and no slash"),
    ("https://www.toasttab.com/", False, "Toast front door"),
    ("https://slicelife.com/restaurants/wa/auburn/mumbai-grand", True, "the actual shop page"),
    # …but where the shop IS the subdomain, the root is the shop.
    ("https://joespizza.square.site/", True, "Square: subdomain is the shop"),
    # …and the genuine ones from that same run, which must survive it.
    ("https://rojosmexicanfood.square.site", True, "Square Online store"),
    ("https://ordering.chownow.com/order/6656/locations", True, "ChowNow ordering"),
    ("https://order.online/online-ordering/business/top-pot-doughnuts", True, "order.online"),
    ("http://toasttab.com/plenty-of-clouds", True, "bare Toast venue page"),

    # The dry-run false positives. A nav item called Menu is not a shop.
    ("https://mobilizon.fr/menu", False, "events platform nav"),
    ("https://carbondale.info/menu", False, "town website nav"),
    ("https://winebc.com/menu", False, "tourism board nav"),
    ("https://surlybrewing.com/menu", False, "a real menu, but you cannot order from it"),
    ("https://joespizza.com/menus-and-hours", False, "menu is a substring, not the path"),

    # The original 400-of-400.
    ("https://www.meetup.com/yogawithvivi/events/315709836/", False, "Meetup event"),
    ("https://www.seattle.gov/parks/all-community-centers/miller-community-center", False, "civic page"),
    ("https://www.eventbrite.com/e/supper-club-tickets-123", False, "ticketing, not ordering"),
    ("https://greatlakesbrewing.com/events/", False, "brewery events page"),

    # Not URLs at all.
    ("not-a-url", False, "unparseable"),
    ("ftp://joespizza.com/order", False, "non-http scheme"),
    ("", False, "empty"),
]

HTML = """
<html><body>
  <a href="/about">About</a>
  <a href="mailto:hi@joes.com">Email</a>
  <a href="/menu">Menu</a>
  <a href="https://order.toasttab.com/online/joes">Order online</a>
</body></html>
"""
HTML_NO_ORDER = """<html><body><a href="/menu">Menu</a><a href="/contact">Contact</a></body></html>"""


def main():
    failed = 0
    for url, want, why in CASES:
        got = looks_like_ordering(url)
        ok = got == want
        if not ok:
            failed += 1
        print(f"{'ok  ' if ok else 'FAIL'}  {str(want):<5} {why:<46} {url[:52]}")

    # link extraction picks the order link, not the first link and not /menu
    got = order_link_on("https://joespizza.com/", HTML)
    ok = got == "https://order.toasttab.com/online/joes"
    failed += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'}  extracts the ordering link from a page -> {got}")

    # a page with nothing orderable yields nothing, rather than the nav Menu
    got2 = order_link_on("https://joespizza.com/", HTML_NO_ORDER)
    ok2 = got2 is None
    failed += 0 if ok2 else 1
    print(f"{'ok  ' if ok2 else 'FAIL'}  a page with no order link yields None -> {got2}")

    # ---- booking a table ------------------------------------------------
    # Same bar as ordering and the same failure mode to avoid. `/menu` pulled in
    # a town website and a tourism board; the equivalent trap here is `/book`,
    # which is a bookshop, a book club, a hotel room and a terms page before it
    # is ever a table. Bare /book and bare /reserve are deliberately unmatched.
    print("\n-- booking a table --")
    BOOKING_CASES = [
        # (url, want, why)
        ("https://www.opentable.com/r/oddfellows-cafe-seattle", True, "opentable venue page"),
        ("https://resy.com/cities/sea/venue", True, "resy"),
        ("https://www.exploretock.com/place", True, "tock"),
        ("https://www.sevenrooms.com/reservations/place", True, "sevenrooms"),
        ("https://www.thefork.co.uk/restaurant/x", True, "thefork, non-.com TLD"),
        ("https://bistro.com/reservations", True, "explicit path on own domain"),
        ("https://bistro.com/book-a-table", True, "unambiguous phrasing"),
        ("https://bistro.com/booktable", True, "same, unhyphenated"),
        ("https://bistro.com/book", False, "bare /book means too many things"),
        ("https://bistro.com/reserve", False, "bare /reserve likewise"),
        ("https://bookshop.org/books", False, "a bookshop is not a table"),
        ("https://hotel.com/book-a-room", False, "a room is not a table"),
        ("https://bistro.com/private-hire", False, "venue hire is not a table"),
        ("https://www.opentable.com/gift-cards", False, "gift cards, on a real host"),
        ("https://bistro.com/booking-terms", False, "terms page"),
        ("https://bistro.com/menu", False, "a menu is not a booking"),
        ("ftp://bistro.com/reservations", False, "not http(s)"),
        ("not a url", False, "garbage"),
    ]
    for url, want, why in BOOKING_CASES:
        got = looks_like_booking(url)
        ok = got == want
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {str(want):<5} {why:<46} {url[:52]}")

    # extraction prefers the real booking link over the nav
    BOOK_HTML = """<a href="/menu">Menu</a>
      <a href="/book-club">Book club</a>
      <a href="https://www.opentable.com/r/joes">Reserve</a>"""
    gotb = booking_link_on("https://joes.com/", BOOK_HTML)
    okb = gotb == "https://www.opentable.com/r/joes"
    failed += 0 if okb else 1
    print(f"{'ok  ' if okb else 'FAIL'}  extracts the booking link, skipping /book-club -> {gotb}")

    gotc = booking_link_on("https://joes.com/", HTML_NO_ORDER)
    okc = gotc is None
    failed += 0 if okc else 1
    print(f"{'ok  ' if okc else 'FAIL'}  a page with no booking link yields None -> {gotc}")

    # ---- two bugs the first live booking run exposed ---------------------
    print("\n-- extraction hazards, both found against live sites --")

    # 1. A PATH match on SOMEBODY ELSE'S domain books the wrong restaurant.
    #    WingDome's own page linked elliottsoysterhouse.com/reservations, and
    #    the matcher took it. A known booking HOST is still trusted anywhere,
    #    because those URLs name the venue; a bare /reservations is not.
    CROSS = '<a href="https://elliottsoysterhouse.com/reservations">Reserve</a>'
    got3 = booking_link_on("https://wingdome.com/", CROSS)
    ok3 = got3 is None
    failed += 0 if ok3 else 1
    print(f"{'ok  ' if ok3 else 'FAIL'}  /reservations on another venue's domain is refused -> {got3}")

    SAME = '<a href="/reservations">Reserve</a>'
    got4 = booking_link_on("https://cafeturko.com/", SAME)
    ok4 = got4 == "https://cafeturko.com/reservations"
    failed += 0 if ok4 else 1
    print(f"{'ok  ' if ok4 else 'FAIL'}  ...but the venue's OWN /reservations is kept -> {got4}")

    PLATFORM = '<a href="https://www.opentable.com/r/joes">Book</a>'
    got5 = booking_link_on("https://wingdome.com/", PLATFORM)
    ok5 = got5 == "https://www.opentable.com/r/joes"
    failed += 0 if ok5 else 1
    print(f"{'ok  ' if ok5 else 'FAIL'}  a known booking host is trusted cross-domain -> {got5}")

    # 2. HTML entities in the href corrupt every query param after the first.
    #    Live: opentable.com/r/neb-reservations-seattle?restref=1278643&amp;lang=en-US
    ENT = '<a href="https://www.opentable.com/r/neb?restref=127&amp;lang=en-US">Book</a>'
    got6 = booking_link_on("https://neb.com/", ENT)
    ok6 = got6 == "https://www.opentable.com/r/neb?restref=127&lang=en-US"
    failed += 0 if ok6 else 1
    print(f"{'ok  ' if ok6 else 'FAIL'}  &amp; in an href is decoded -> {got6}")

    # the ordering side shares the code and therefore both fixes
    got7 = order_link_on("https://joes.com/", '<a href="https://rivals.com/order">Order</a>')
    ok7 = got7 is None
    failed += 0 if ok7 else 1
    print(f"{'ok  ' if ok7 else 'FAIL'}  /order on another venue's domain is refused too -> {got7}")

    # ---- a 200 is not a working destination ------------------------------
    # Both links reported broken on 2026-08-12 answered 200. A dead ChowNow
    # location serves a 4.1KB React shell with no <title>; a live one serves 28KB
    # titled "Peloton Cafe - Seattle - …". Status codes cannot separate them.
    # An unpublished Square store is the same shape: 43KB and <title></title>.
    #
    # destination_ok JUDGES A PAGE WE RETRIEVED. It is deliberately NOT the whole
    # check, and the reason is a bug caught before it reached more than one city:
    # fetch() returns None for every failure — 403, timeout, non-HTML — and the
    # first version read None as "dead". The big ordering hosts all block
    # scrapers, so order.toasttab.com, www.toasttab.com and ubereats.com came
    # back as zero bytes and would every one have been judged dead. That is most
    # of the map's order links, including the Toast URL asserted valid on line 28
    # of this very file. destination_verdict keeps "unknown" apart from "dead"
    # and only "dead" may drop a link.
    print("\n-- the destination has to actually be there --")
    # A MISSING TITLE ALONE IS NOT DEATH — the second false positive this check
    # produced. Square Online stores routinely ship no <title> while being
    # perfectly alive: basiliskpdx.square.site is 51KB with eight categories
    # including "Online Menu" and "Salads"; stone-way-cafe.square.site is 43KB
    # with "Stone Way Cafe - Online Menu". Both would have been stripped.
    # pelotonseattle.square.site merely happened to say "Store | Peloton Cafe",
    # and one confirming example is not evidence. So: no title AND too small to
    # hold a shop.
    BIG = "<html><head></head><body>" + ("<div>catalog</div>" * 900) + "</body></html>"
    DEST = [
        ("", False, "empty body"),
        ("<html><head></head><body><div id=root></div></body></html>", False,
         "tiny app shell, no title -> dead"),
        ("<html><head><title>   </title></head><body>x</body></html>", False,
         "whitespace title on a tiny body -> dead"),
        ("<html><head><title>Peloton Cafe - Seattle</title></head><body>x</body></html>",
         True, "a real product page"),
        (BIG, True, "no title but 16KB of catalog -> a live Square store"),
    ]
    assert len(BIG) >= 10_000, "the live-Square fixture must exceed the shell threshold"

    # A host proven to refuse us is never judged, whatever it answers.
    # ubereats.com returns HTTP 404 to this pipeline while the SAME full URL
    # opens in a real browser as "Order Zizzi (Bankside) … | Uber Eats", and
    # another redirects to def.uber.com/en/challenge. 15 of the 19 links a prune
    # run wanted to cut were on that host, across five countries.
    from mapsee_menu_links import UNVERIFIABLE_HOSTS
    for host, want in (("www.ubereats.com", True), ("ubereats.com", True),
                       ("order.toasttab.com", False), ("notubereats.com", False)):
        got = bool(UNVERIFIABLE_HOSTS.search(host))
        ok = got == want
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  unverifiable={str(want):<5} {host}")
    for h, want, why in DEST:
        got = destination_ok(h)
        ok = got == want
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {str(want):<5} {why}")

    # ---- point at the food, not the shop's front door ---------------------
    # pelotonseattle.square.site is live, on a genuine ordering host, and its
    # landing block is titled "Gift Cards" — the menus are at /shop/online-menu.
    SQ = ('{"name":"Online Menu","updated_date":"2026-07-09","permalink":"",'
          '"site_link":"\\/shop\\/online-menu\\/MY5U"}'
          '{"name":"Merch","updated_date":"2026-07-09","permalink":"",'
          '"site_link":"\\/shop\\/merch\\/JRVT"}')
    got8 = refine_storefront("https://pelotonseattle.square.site/", SQ)
    ok8 = got8 == "https://pelotonseattle.square.site/shop/online-menu/MY5U"
    failed += 0 if ok8 else 1
    print(f"{'ok  ' if ok8 else 'FAIL'}  a storefront root is refined to its menu -> {got8}")

    # a link that already names a path was chosen deliberately: leave it be
    deep = "https://order.toasttab.com/online/joes"
    got9 = refine_storefront(deep, SQ)
    ok9 = got9 == deep
    failed += 0 if ok9 else 1
    print(f"{'ok  ' if ok9 else 'FAIL'}  an already-specific link is left alone -> {got9}")

    # nothing food-ish to move to => keep the root rather than invent a page
    got10 = refine_storefront("https://shop.square.site/",
                              '{"name":"Merch","updated_date":"x","site_link":"\\/shop\\/merch\\/A"}')
    ok10 = got10 == "https://shop.square.site/"
    failed += 0 if ok10 else 1
    print(f"{'ok  ' if ok10 else 'FAIL'}  no food category => unchanged -> {got10}")

    # A REDIRECT MUST NOT DEMOTE A VALIDATED LINK. Chipotle's own location page
    # links chipotle.com/order#menu — correctly matched — and fetching it
    # redirects to the bare homepage. Refining from the post-redirect URL made
    # THAT the stored link, and the client then rightly refused to call a
    # homepage "Order pickup" and fell through to "Tickets & info" on a burrito
    # shop. refine_storefront on a root with no food categories must hand the
    # url back unchanged, and the caller re-validates before accepting it.
    got11 = refine_storefront("https://www.chipotle.com/", "<html>no square json</html>")
    ok11 = got11 == "https://www.chipotle.com/"
    failed += 0 if ok11 else 1
    print(f"{'ok  ' if ok11 else 'FAIL'}  a redirect target with no menu is not invented -> {got11}")
    ok12 = looks_like_ordering("https://www.chipotle.com/order#menu") and \
        not looks_like_ordering("https://www.chipotle.com/")
    failed += 0 if ok12 else 1
    print(f"{'ok  ' if ok12 else 'FAIL'}  ...and only the /order form qualifies, which is why it matters")

    # ---- a 5xx from Supabase is not an answer ---------------------------- #
    # The 2026-08-14 scheduled run died on a bare urllib traceback ending in
    # `HTTPError: HTTP Error 503` — an hour-long provider outage, reported as
    # nine frames of stack that read like a bug in this file. sb() retries what
    # is transient and refuses to retry what is settled.
    failed += _sb_retry_cases()

    total = len(CASES) + 2 + len(BOOKING_CASES) + 2 + 5 + len(DEST) + 5 + 4 + 4
    print(f"\n{total} cases, {failed} failed")
    return 1 if failed else 0


def _sb_retry_cases() -> int:
    import io
    import types
    import urllib.error
    import mapsee_menu_links as M

    def err(code):
        return urllib.error.HTTPError("http://x", code, "boom", {},
                                      io.BytesIO(b"upstream connect error"))

    class Body:
        def __init__(s, t): s._t = t.encode()
        def read(s): return s._t
        def __enter__(s): return s
        def __exit__(s, *a): return False

    url0, key0, urlopen0, time0 = M.SUPABASE_URL, M.SERVICE_KEY, M.urllib.request.urlopen, M.time
    M.SUPABASE_URL, M.SERVICE_KEY = "https://example.test", "k"
    M.time = types.SimpleNamespace(sleep=lambda _s: None)      # no real waiting in tests
    bad = 0
    try:
        calls = []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise err(503)
            return Body('[{"id":1}]')
        M.urllib.request.urlopen = flaky
        got = M.sb("events?select=id")
        for label, cond in (
                ("a transient 503 is retried until it answers", got == [{"id": 1}] and len(calls) == 3),):
            bad += 0 if cond else 1
            print(f"{'ok  ' if cond else 'FAIL'}  {label}")

        calls.clear()

        def settled(req, timeout=None):
            calls.append(1)
            raise err(404)
        M.urllib.request.urlopen = settled
        raised = None
        try:
            M.sb("nope")
        except urllib.error.HTTPError as ex:
            raised = ex.code
        c = raised == 404 and len(calls) == 1
        bad += 0 if c else 1
        print(f"{'ok  ' if c else 'FAIL'}  a 4xx is a settled answer and is not retried")

        calls.clear()

        def dead(req, timeout=None):
            calls.append(1)
            raise err(503)
        M.urllib.request.urlopen = dead
        msg = ""
        try:
            M.sb("events")
        except RuntimeError as ex:
            msg = str(ex)
        c = len(calls) == M.SB_ATTEMPTS and "Supabase unreachable" in msg
        bad += 0 if c else 1
        print(f"{'ok  ' if c else 'FAIL'}  a sustained outage still fails, after {M.SB_ATTEMPTS} tries")
        c = "transient" in msg and "Nothing was written" in msg
        bad += 0 if c else 1
        print(f"{'ok  ' if c else 'FAIL'}  ...and says what happened instead of raising a stack")
    finally:
        M.SUPABASE_URL, M.SERVICE_KEY = url0, key0
        M.urllib.request.urlopen, M.time = urlopen0, time0
    return bad


if __name__ == "__main__":
    sys.exit(main())
