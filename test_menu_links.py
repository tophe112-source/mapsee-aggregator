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
    looks_like_ordering, order_link_on, looks_like_booking, booking_link_on)

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

    total = len(CASES) + 2 + len(BOOKING_CASES) + 2 + 5
    print(f"\n{total} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
