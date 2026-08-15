#!/usr/bin/env python3
"""
test_ingest_mylisting.py — the MyListing adapter, against markup taken verbatim
from a live get_listings response.

Prints one line per case and exits non-zero on failure, like the other 12.

What is pinned here is what was measured and what would have gone wrong:

  * ONE RESULT IS ONE OCCURRENCE, and the card carries two dates. The wrapper's
    is this occurrence; the inner span's is the listing's next upcoming date,
    stamped identically into every card the listing produces. 55 of 80 cards on
    one live page disagreed. Reading the inner one collapses a fifty-night music
    residency to the same Friday, fifty times — well formed, plausible, wrong.
  * THE HORIZON. Annual events with open-ended recurrence rules project to 2050
    in the live data. Nothing downstream removes a future event.
  * AN EVENT THAT STARTED BEFORE TODAY BUT HAS NOT ENDED IS STILL ON. The live
    example is a months-long registration window; judging it by its start alone
    drops it while it is still open.
  * THE ADDRESS SPLIT never invents a venue name. Google glues an optional venue
    onto the front, and a street written with a comma in it looks identical
    until you notice which part carries a digit.
  * SOURCE_ID CARRIES THE OCCURRENCE. The sync upserts on it; an id that is only
    the listing would make every night of a run overwrite the last one.
"""
import sys

from mapsee_ingest_mylisting import (
    parse_cards, parse_data_date, read_bootstrap, _split_address, _region_code,
    event_description, resolve_place, to_event,
)

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got {got!r}\n        want {want!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# --- fixtures: verbatim from https://www.bainbridgeisland.com/ --------------
PAGE_BOOTSTRAP = '''
<script src="https://www.bainbridgeisland.com/wp-content/themes/my-listing/assets/dist/explore.js?ver=2.11.70"></script>
<script>var CASE27 = {"ajax_url":"https:\\/\\/www.bainbridgeisland.com\\/wp-admin\\/admin-ajax.php",
"mylisting_ajax_url":"\\/?mylisting-ajax=1","theme_version":"2.11.7","env":"production",
"ajax_nonce":"b0543da9e3"};</script>
'''

# Two cards for the SAME listing (a Friday concert series), plus one for a
# different listing. Note every wrapper date differs while the inner
# codicts-mlsre-date-manager span repeats 2026-08-21 — that is the trap.
CARDS = '''<div data-date="2026-08-21T18:00:00-07:00:::2026-08-21T20:00:00-07:00" class="col-md-4 col-sm-6 grid-item codicts-mlsre-date-wrap"><div
    class="lf-item-container listing-preview type-event  no-logo has-tagline has-info-fields level-normal priority-0"
    data-id="listing-id-38730"
    data-locations="[{&quot;address&quot;:&quot;168 Winslow Way West, Bainbridge Island, Washington 98110, United States&quot;,&quot;lat&quot;:&quot;47.62507&quot;,&quot;lng&quot;:&quot;-122.52183&quot;}]"
>
<div class="lf-item lf-item-default" data-template="default">
    <a href="https://www.bainbridgeisland.com/event/music-on-the-green/">
        <div class="lf-background" style="background-image: url('https://www.bainbridgeisland.com/wp-content/uploads/2025/05/Music-on-the-Green-Bainbridge-Island-768x615.jpg');"></div>
        <div class="lf-item-info">
            <h4 class="case27-primary-text listing-preview-title">
                Music on the Green                            </h4>
        </div>
    </a>
    <div class="lf-head level-normal"><div class="lf-head-btn " >
        <span data-date="2026-08-21T18:00:00-07:00:::2026-08-21T20:00:00-07:00" data-format="MMM D" class="codicts-mlsre-date-manager"></span>
    </div></div></div>
</div>
</div><div data-date="2026-08-28T18:00:00-07:00:::2026-08-28T20:00:00-07:00" class="col-md-4 col-sm-6 grid-item codicts-mlsre-date-wrap"><div
    class="lf-item-container listing-preview type-event  no-logo has-tagline has-info-fields level-normal priority-0"
    data-id="listing-id-38730"
    data-locations="[{&quot;address&quot;:&quot;168 Winslow Way West, Bainbridge Island, Washington 98110, United States&quot;,&quot;lat&quot;:&quot;47.62507&quot;,&quot;lng&quot;:&quot;-122.52183&quot;}]"
>
<div class="lf-item lf-item-default" data-template="default">
    <a href="https://www.bainbridgeisland.com/event/music-on-the-green/">
        <div class="lf-item-info">
            <h4 class="case27-primary-text listing-preview-title">
                Music on the Green                            </h4>
        </div>
    </a>
    <div class="lf-head level-normal"><div class="lf-head-btn " >
        <span data-date="2026-08-21T18:00:00-07:00:::2026-08-21T20:00:00-07:00" data-format="MMM D" class="codicts-mlsre-date-manager"></span>
    </div></div></div>
</div>
</div><div data-date="2029-07-04T07:00:00-07:00:::2029-07-04T16:00:00-07:00" class="col-md-4 col-sm-6 grid-item codicts-mlsre-date-wrap"><div
    class="lf-item-container listing-preview type-event  no-logo has-tagline has-info-fields level-normal priority-0"
    data-id="listing-id-11111"
    data-locations="[{&quot;address&quot;:&quot;Bainbridge Island Museum of Art, 550 Winslow Way E, Bainbridge Island, Washington 98110, United States&quot;,&quot;lat&quot;:&quot;47.62289&quot;,&quot;lng&quot;:&quot;-122.51930&quot;}]"
>
<div class="lf-item lf-item-default" data-template="default">
    <a href="https://www.bainbridgeisland.com/event/grand-old-fourth-of-july/">
        <div class="lf-item-info">
            <h4 class="case27-primary-text listing-preview-title">
                Grand Old Fourth of July                            </h4>
        </div>
    </a>
</div>
</div>
</div>'''

# A site WITHOUT the recurring-dates plugin: no wrapper, one card, the date only
# on the inner span.
CARDS_NO_WRAPPER = '''<div class="lf-item-container listing-preview type-event level-normal"
    data-id="listing-id-777"
    data-locations="[{&quot;address&quot;:&quot;12 Main St, Poulsbo, Washington 98370, United States&quot;,&quot;lat&quot;:&quot;47.7&quot;,&quot;lng&quot;:&quot;-122.6&quot;}]">
<div class="lf-item"><a href="https://example.org/event/quiz-night/">
<h4 class="listing-preview-title">Quiz Night</h4></a>
<div class="lf-head-btn"><span data-date="2026-09-03T19:00:00-07:00:::2026-09-03T21:00:00-07:00" class="codicts-mlsre-date-manager"></span></div>
</div></div>'''

DETAIL = '''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"WebPage","name":"Music on the Green"},
{"@context":"http://www.schema.org","@type":"Event","name":"Music on the Green",
"description":"Come listen to music, bring chairs, blankets and food.",
"url":"https://www.bainbridgeisland.com/event/music-on-the-green/"}]}</script>'''

SITE = {"name": "Bainbridge", "explore_url": "https://www.bainbridgeisland.com/events/?type=event",
        "category": "community", "timezone": "America/Los_Angeles",
        "default_city": "Bainbridge Island", "default_region": "WA"}


def main():
    print("read_bootstrap — the endpoint and nonce come from the page, never a guess")
    boot = read_bootstrap(PAGE_BOOTSTRAP, "https://www.bainbridgeisland.com/events/?type=event")
    check("ajax url is resolved against the site root",
          boot["ajax_url"], "https://www.bainbridgeisland.com/?mylisting-ajax=1")
    check("nonce is read", boot["nonce"], "b0543da9e3")
    check_true("the my-listing theme is detected", boot["is_mylisting"])
    empty = read_bootstrap("<html><body>a plain page</body></html>", "https://example.org/events/")
    check("a page that names no ajax url yields none — nothing is requested",
          empty["ajax_url"], None)

    print("\nparse_data_date — a local wall clock with a real offset")
    d = parse_data_date("2026-08-28T18:00:00-07:00:::2026-08-28T20:00:00-07:00")
    check("start_local keeps the wall clock", d["start_local"], "2026-08-28T18:00:00")
    check("start_utc applies the offset", d["start_utc"], "2026-08-29T01:00:00Z")
    check("end_utc applies the offset", d["end_utc"], "2026-08-29T03:00:00Z")
    d2 = parse_data_date("2026-08-28T18:00:00-07:00")
    check("a missing end half is None, not a copy of the start", d2["end_utc"], None)
    d3 = parse_data_date("2026-08-28T18:00:00")
    check("no offset means no exact instant — the UTC side stays empty",
          d3["start_utc"], None)
    check("...but the wall clock is still reported", d3["start_local"], "2026-08-28T18:00:00")

    print("\nparse_cards — one card is one OCCURRENCE, and the wrapper date is the one")
    rows = parse_cards(CARDS)
    check("three cards parsed", len(rows), 3)
    check("two of them are the same listing",
          sum(1 for r in rows if r["listing_id"] == "38730"), 2)
    starts = [r["start_local"][:10] for r in rows if r["listing_id"] == "38730"]
    check("THE TRAP: the two occurrences keep their own dates, not the repeated "
          "next-date from the inner span", starts, ["2026-08-21", "2026-08-28"])
    check("title is unescaped and collapsed", rows[0]["title"], "Music on the Green")
    check("listing url is captured", rows[0]["url"],
          "https://www.bainbridgeisland.com/event/music-on-the-green/")
    check_true("the poster image is read from the inline background-image",
               rows[0]["image"] and rows[0]["image"].endswith("768x615.jpg"))
    check("data-locations is unescaped and parsed", rows[0]["location"]["lat"], "47.62507")

    print("\nparse_cards — a site with no recurring-dates plugin still yields a dated row")
    plain = parse_cards(CARDS_NO_WRAPPER)
    check("one card", len(plain), 1)
    check("the date falls back to the inner span", plain[0]["start_local"], "2026-09-03T19:00:00")
    check("title survives the fallback path", plain[0]["title"], "Quiz Night")

    print("\n_split_address — read from the right; never invent a venue")
    a = _split_address("168 Winslow Way West, Bainbridge Island, Washington 98110, United States")
    check("street", a["address"], "168 Winslow Way West")
    check("city", a["city"], "Bainbridge Island")
    check("region is coded for the Census geocoder", a["region"], "WA")
    check("postal", a["postal_code"], "98110")
    check("country", a["country"], "United States")
    check("no venue was invented", a["venue"], None)
    b = _split_address("Bainbridge Island Museum of Art, 550 Winslow Way E, "
                       "Bainbridge Island, Washington 98110, United States")
    check("a leading non-numeric run before a numbered street IS a venue",
          b["venue"], "Bainbridge Island Museum of Art")
    check("...and the street is what follows it", b["address"], "550 Winslow Way E")
    c = _split_address("Fort Ward Hill Road Northeast & Belfair Avenue East, "
                       "Bainbridge Island, Washington 98110, United States")
    check("an intersection is a street even with no number — left whole",
          c["address"], "Fort Ward Hill Road Northeast & Belfair Avenue East")
    check("...and produces no venue", c["venue"], None)
    d4 = _split_address("100 Main St, Apt 2, Seattle, Washington 98101, United States")
    check("a street written with a comma is not split into a venue",
          (d4["venue"], d4["address"]), (None, "100 Main St, Apt 2"))
    # The live regression. Counting commas from the right assumes a street is
    # always present; six real events had a city and none, and landed as
    # city="Washington 98110", region="United States".
    e = _split_address("Bainbridge Island, Washington 98110, United States")
    check("REGRESSION: an address with no street keeps its city",
          (e["address"], e["city"], e["region"], e["postal_code"], e["country"]),
          (None, "Bainbridge Island", "WA", "98110", "United States"))
    f = _split_address("550 Winslow Way E, Bainbridge Island, WA 98110")
    check("a two-letter code and no country parse the same way",
          (f["address"], f["city"], f["region"], f["postal_code"], f["country"]),
          ("550 Winslow Way E", "Bainbridge Island", "WA", "98110", None))
    g = _split_address("Bainbridge Island, Washington")
    check("a spelled-out state with no postal is still the region tail",
          (g["city"], g["region"]), ("Bainbridge Island", "WA"))
    h = _split_address("12 Rue de Rivoli, Paris, France")
    check("no recognisable region tail: the country is still popped by name, "
          "and the city is not swallowed",
          (h["address"], h["city"], h["region"], h["country"]),
          ("12 Rue de Rivoli", "Paris", None, "France"))
    check("_region_code passes a code through", _region_code("wa"), "WA")
    check("_region_code leaves an unknown region alone", _region_code("Ontario"), "Ontario")

    print("\nresolve_place — the config fills only what the address did not carry")
    p = resolve_place({"address": "168 Winslow Way West", "lat": "47.6", "lng": "-122.5"}, SITE)
    check("city comes from default_city", p["city"], "Bainbridge Island")
    check("region comes from default_region", p["region"], "WA")
    check("coordinates are floats", (p["lat"], p["lon"]), (47.6, -122.5))
    check("an event with neither coordinates nor a street+city is unplaceable",
          resolve_place({}, {"name": "x"}), None)

    print("\nevent_description — the Event node, which carries prose but no startDate")
    check("description is read from the @graph",
          event_description(DETAIL), "Come listen to music, bring chairs, blankets and food.")
    check("a page with no Event node yields None", event_description("<html></html>"), None)

    print("\nto_event — horizon, past, and the identity the sync upserts on")
    today, horizon = "2026-08-15", "2027-09-19"
    evs = [to_event(r, SITE, today, horizon) for r in rows]
    check("THE HORIZON: the 2029 projection of an annual event is dropped",
          evs[2], None)
    check("the two in-window occurrences survive",
          [bool(e) for e in evs[:2]], [True, True])
    check("SOURCE_ID CARRIES THE OCCURRENCE — the sync upserts on it, so a "
          "listing-only id would make each night overwrite the last",
          [e.source_id for e in evs[:2]],
          ["www.bainbridgeisland.com|38730|2026-08-21T18:00:00",
           "www.bainbridgeisland.com|38730|2026-08-28T18:00:00"])
    check("...and the fingerprints differ per date",
          evs[0].fingerprint != evs[1].fingerprint, True)
    check("timezone comes from the config, since the payload names no zone",
          evs[0].timezone, "America/Los_Angeles")
    check("the default category is the config's", evs[0].category, "community")

    past = dict(rows[0], start_local="2026-06-08T09:00:00", start_utc="2026-06-08T16:00:00Z",
                end_local=None, end_utc=None)
    check("a finished occurrence is dropped", to_event(past, SITE, today, horizon), None)
    ongoing = dict(past, end_local="2026-08-28T15:00:00")
    check("AN EVENT THAT STARTED BEFORE TODAY BUT HAS NOT ENDED IS STILL ON",
          bool(to_event(ongoing, SITE, today, horizon)), True)
    undated = dict(rows[0], start_utc=None)
    check("an occurrence with no exact instant is refused, not guessed at",
          to_event(undated, SITE, today, horizon), None)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all mylisting adapter checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
