#!/usr/bin/env python3
"""
test_prune_links.py — what a pruned description must look like afterwards.

This pass EDITS live descriptions, so the transformation is the risky part, not
the probing. Two things it must never do: leave the lead sentence advertising a
link it just removed ("Order for pickup on their own site" with no order line),
and touch a line whose destination was merely unreachable.

Run: python test_prune_links.py
"""
import sys

from mapsee_prune_links import rewrite

DEAD = "https://order.chownow.com/order/39625/locations/60228"
LIVE = "https://order.toasttab.com/online/joes"
BOOK = "https://www.opentable.com/r/joes"

BOTH = (
    "Joe's — restaurant in Seattle. Order for pickup or book a table on their own site; "
    "mapsee.me is not taking the order.\n\n"
    f"\U0001F6D2 Order: {DEAD}\n\n"
    f"\U0001F37D️ Reserve: {BOOK}\n\n"
    "\U0001F310 Website: https://joes.com/\n\n"
    "Listing from OpenStreetMap contributors (ODbL). Is this your place?"
)
ORDER_ONLY = (
    "Vietlicious — fast food in Seattle. Order for pickup on their own site; "
    "mapsee.me is not taking the order.\n\n"
    f"\U0001F6D2 Order: {DEAD}\n\n"
    f"Tickets / info: {DEAD}\n\n"
    "Listing from OpenStreetMap contributors (ODbL). Is this your place?"
)
HEALTHY = (
    "Joe's — restaurant in Seattle. Order for pickup on their own site; "
    "mapsee.me is not taking the order.\n\n"
    f"\U0001F6D2 Order: {LIVE}\n\n"
    "Listing from OpenStreetMap contributors (ODbL)."
)

CASES = []


def check(name, got, want):
    ok = got == want
    CASES.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got : {got!r}")
        print(f"        want: {want!r}")


def main():
    # 1. dead order, live booking => order line goes, lead demotes to booking
    new, has_o, has_b = rewrite(BOTH, {DEAD})
    check("dead order line is removed", DEAD in (new or ""), False)
    check("the live booking line survives", BOOK in (new or ""), True)
    check("the website line survives", "Website: https://joes.com/" in (new or ""), True)
    check("lead demoted to booking only",
          "Book a table on their own site; mapsee.me is not taking the order." in (new or "")
          and "Order for pickup" not in (new or ""), True)
    check("flags report booking only", (has_o, has_b), (False, True))

    # 2. the last transactional link goes => the lead makes NO promise at all,
    #    and the Tickets/info line carrying the same dead url goes with it
    new2, has_o2, has_b2 = rewrite(ORDER_ONLY, {DEAD})
    check("orphan: no order line left", DEAD in (new2 or ""), False)
    check("orphan: Tickets/info with the same dead url also goes",
          "Tickets / info" in (new2 or ""), False)
    check("orphan: lead no longer promises ordering",
          "on their own site" in (new2 or ""), False)
    check("orphan: the listing itself survives",
          "Vietlicious" in (new2 or "") and "OpenStreetMap" in (new2 or ""), True)
    check("orphan: flags report nothing left", (has_o2, has_b2), (False, False))

    # 3. nothing dead => None, meaning "do not write". An unreachable
    #    destination is NOT in the dead set and so never reaches here.
    new3, has_o3, _ = rewrite(HEALTHY, set())
    check("a healthy row is left completely alone", new3, None)
    check("healthy row still reports its order link", has_o3, True)

    # 4. a dead url that this row does not carry must not rewrite it
    new4, _, _ = rewrite(HEALTHY, {DEAD})
    check("an unrelated dead url does not touch the row", new4, None)

    failed = CASES.count(False)
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
