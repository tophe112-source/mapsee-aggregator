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

from mapsee_menu_links import looks_like_ordering, order_link_on

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

    print(f"\n{len(CASES) + 2} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
