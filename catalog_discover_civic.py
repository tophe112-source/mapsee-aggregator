#!/usr/bin/env python3
"""
catalog_discover_civic.py — find a CITY's calendar, not a venue's.

catalog_discover_osm finds places that PROGRAMME things: a theatre, a zendo, a
bookshop with a reading series. It is very good at that and it is structurally
blind to the object this module exists for, which is the calendar that covers a
whole town at once. Visit Issaquah is the worked example and the measurement
that started this file:

    510 future events behind ONE base_url — Village Theatre (134), the Salmon
    Hatchery (68), a wine bar (41), Cougar Mountain Zoo (33), the Historic Shell
    Station (30), Pickering Barn, the Train Depot — none of them separately
    configured, and most of them things OSM discovery could never propose,
    because a wine bar with a Tuesday tasting is not tagged as a programme venue.

    Run the OSM backend's own Overpass query over Issaquah and it returns 11
    civic objects. The only one that is this kind of thing, "Issaquah Visitor
    Information", carries no `website` tag. Zero of the 11 would have found it.

So the candidate list cannot come from the map. It comes from WIKIDATA, which
knows every incorporated city, its official website, its population and its
coordinates — 5,770 US cities carry all four — and answers a class-scoped query
in about two seconds. That is a geographic generator in the same sense the
Overpass one is: it works the same way in every country that has the class.

AND IT WORKS IN MORE THAN ONE COUNTRY NOW, which the sentence above promised and
CITY_CLASSES did not deliver for the file's whole life: it held one entry, the
cursor held one offset, and every civic run walked further down the same list of
US cities while the report FLAGged twenty-odd countries as having no community
feeds at all. catalog_curate._civic_next_country rotates, thinnest catalog
first, and CITY_CLASSES now names a class per country with the measurement that
proved it beside each one.

TWO STREAMS OUT OF ONE FETCH
The city's own site is fetched once and asked two questions:

  1. DOES IT HAVE A CALENDAR? Usually yes, and usually CivicPlus, which is how a
     large share of US municipalities publish anything. The fingerprint for it
     lives in catalog_discover_osm with the rest, and it needed one: before it
     existed, find_calendar landed on issaquahwa.gov/calendar.aspx and returned
     `no-calendar` — a city hall with eleven readable iCal feeds reading as a
     city hall with none.

  2. DOES IT POINT AT THE TOURISM BOARD? It does not, and that is the measured
     answer rather than an assumption. The DMO calendar is the better content —
     Visit Issaquah's 510 events against the city's own mix of community events
     and tax deadlines — and it lives on a different domain, so it has to be
     found rather than derived. Three generators were tried:
       • THE CITY'S OWN LINKS. The obvious one, and free, because the homepage
         is already in hand. It finds NOTHING: across seattle.gov, issaquahwa
         .gov, bendoregon.gov and ashevillenc.gov there is not one outbound host
         anywhere in the homepage HTML carrying a tourism word at all — not even
         seattle.gov to visitseattle.org. Written, measured, deleted.
       • NAME PATTERNS. visitissaquah.com, visitissaquahwa.com,
         exploreissaquah.com and discoverissaquah.com all resolve, and so does
         every pattern for all twelve cities tested. DNS says yes to parked
         domains, so existence carries no information whatsoever.
       • WIKIPEDIA'S EXTERNAL LINKS, which do work: visitbend.com,
         exploreasheville.com, visitithaca.com and visitbruges.be all appear on
         their city's article. It is half a generator — Issaquah's and
         Kanazawa's are missing — but half of something beats all of nothing,
         and it is nearly free: Wikidata hands over the article title in the
         same query as the city, and the extlinks API takes 50 titles at once,
         so the whole stream costs about one request per fifty cities.
     A link is only a NOMINATION either way. It is probed like any other site
     and has to verify before it becomes a source.

WHAT A CITY-WIDE CANDIDATE IS NOT: A VENUE.
Every candidate the OSM backend emits carries a `venue` block — the surveyed
point of the one place whose calendar it is — and for a city that block would be
a lie with coordinates on it. A town-wide calendar has no single point, and
filling one in would pin every event that arrived without an address onto the
town hall. So these carry city-wide DEFAULTS instead, and only the adapters that
have such a mechanism are proposed at all: `ics` places by geocode_suffix,
`tribe` and `mylisting` by default_city/default_region/default_country. A find
that lands on any other adapter is COUNTED AND REPORTED (`no-citywide-shape`),
never quietly shaped into a venue it is not. That is the same refusal
why_no_candidate already makes for the venue backend, applied to this one.

Nothing here adds a source. It proposes; catalog_curate.py verify has to prove
the feed returns future events before merge will write it anywhere.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import catalog_discover_osm as osm

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"

# THE CLASS, PER COUNTRY, AND WHY IT IS NOT ONE QUERY FOR THE WORLD.
#
# The obvious query — every instance of `human settlement` (Q486972) and its
# subclasses, with an official website — times out. Measured: 504 after 66s,
# both globally and narrowed to one US state with a transitive P131. The subclass
# walk over Q486972 is the expensive half, and a transitive `located in` on top
# of it is worse.
#
# Naming the country's OWN city class instead answers in 1.9 seconds and returns
# 5,770 US cities with website, coordinates and population. The cost is that the
# class has to be named per country rather than derived — which is honest about
# what this is: Wikidata models settlements differently in different places, and
# pretending otherwise is what made the general query unusable. Countries are
# added here as they are swept, each one verified to return rows before it goes
# in, rather than listed hopefully.
#
# Each entry is (classes, country scope, label). The scope is the country's QID
# and it is REQUIRED beside a general class and absent beside a national one:
# Q1093829 is "city in the United States" and can only be American, while
# Q3957 "town" and Q532 "village" are worldwide.
#
# EVERY ONE WAS MEASURED BEFORE IT WENT IN, with the query this file actually
# runs — 40 cities, population descending, all four properties present. A
# country that returns nothing, or times out, is NOT listed hopefully; five are
# missing for exactly that reason and the note below says which.
#
# Measured 2026-09-19/20. Every row is 40 cities out of 40, and the site is the
# real town hall — the point of the exercise:
#
#   MX 13.1s Mexico City      SE 13.7s Stockholm     IN 16.6s Mumbai
#   FI 19.1s Helsinki         CA 20.5s Toronto       DK 36.8s Copenhagen
#   NO 42.2s Oslo             NZ 46.9s Auckland      AT 49.2s Vienna
#   IE 49.9s Dublin           NL 50.8s Amsterdam     DE 58.5s Berlin
#   JP 60.0s Tokyo            CH 64.9s Zurich        ES 65.9s Madrid
#   PT 72.9s Lisbon           AU 80.2s Sydney        GB 81.2s London
#   BE 85.3s Antwerp          ZA 211.9s Johannesburg
#
# TWO THINGS THOSE SECONDS ARE NOT. They are not the steady state: WDQS spent
# both days answering 500, 502, 504 and 429 to queries of every size, including
# the US one that has always taken 1.9s, so each is an upper bound measured on a
# bad day. And they are from BEFORE the trim below, which only ever removes
# classes — a smaller VALUES set cannot cost more. ZA's 211.9s was the trim's
# whole reason: it included `human settlement`, which is every hamlet on earth,
# and it ran past cities()'s own timeout. Re-measure any of them with
# `python catalog_curate.py cityclass <ISO>`.
#
# WHAT IS DROPPED FROM EACH SET AND WHY. The seeds propose, and a seed town is
# also other things: Mississauga is a weather station and a federal electoral
# district, and an electoral district has a population AND a website, so the
# query cannot tell them apart afterwards. Dropped: `human settlement` (CH, ZA),
# `district of Austria` (a Bezirk is an administration, not a town), `district
# of India` (which is why "Thane district" came back as a city), `district of
# Madrid` (part of one city), `port city` and `place with town rights` (already
# cities), `municipality seat` and `municipality capital` (the country class
# covers them), `Ceremonial cities of the Republic of Ireland` (a title), and
# every sub-national class a national one already covers — Victoria,
# Saskatchewan, British Columbia, the canton of Zurich, Bavaria, Catalonia,
# Galicia. `college town` went too: the German seeds were four university towns
# and it is their artefact, not Germany's shape.
#
# THE FIVE THAT ARE NOT HERE — France, Italy, Poland, Czechia and Brazil — are
# not missing by oversight. Each was attempted three times over two days and
# answered 500 or 504 every time, and they have a structure in common: they are
# the countries whose settlements are tens of thousands of small municipalities
# (France alone has ~35,000 communes), so `ORDER BY DESC(?pop)` has the largest
# set to sort before LIMIT sees any of it. A population floor is the obvious
# next thing to try and is UNMEASURED — one attempt at FILTER(?pop >= 3000) for
# France answered 500 after 103s, on a day WDQS was refusing healthy queries
# too, so it proved nothing either way. Brazil is the one that matters most:
# mapasculturais is its only other supply.
CITY_CLASSES = {
    "US": (("Q1093829",), None, "city in the United States"),
    "CA": (("Q130626256", "Q515", "Q1549591", "Q15210668"), "Q16",
           "city in Canada, city, big city, lower-tier municipality of Ontario"),
    "GB": (("Q100503226", "Q1357964", "Q1115575", "Q18511725", "Q515", "Q3957"),
           "Q145", "town of the UK, county town, civil parish, market town, city, town"),
    "IE": (("Q3559227", "Q515", "Q3957", "Q1357964"), "Q27",
           "city in Ireland, city, town, county town"),
    "AU": (("Q515", "Q1549591", "Q3957"), "Q408", "city, big city, town"),
    "NZ": (("Q515", "Q2881272", "Q3957"), "Q664",
           "city, district of New Zealand, town"),
    "DE": (("Q262166", "Q42744322", "Q134626", "Q1549591"), "Q183",
           "municipality in Germany, urban municipality, district capital, big city"),
    "NL": (("Q2039348", "Q515", "Q707813"), "Q55",
           "municipality of the Netherlands, city, Hanseatic city"),
    "BE": (("Q493522", "Q15273785", "Q3957", "Q1549591", "Q515"), "Q31",
           "municipality of Belgium, municipality titled city, town, big city, city"),
    "CH": (("Q70208", "Q54935504", "Q14770218", "Q1549591"), "Q39",
           "municipality of Switzerland, city, cantonal capital, big city"),
    "AT": (("Q667509", "Q262882", "Q515"), "Q40",
           "municipality of Austria, statutory city, city"),
    "SE": (("Q127448", "Q515"), "Q34", "municipality of Sweden, city"),
    "NO": (("Q755707", "Q515", "Q1549591"), "Q20",
           "municipality of Norway, city, big city"),
    "DK": (("Q2177636", "Q1549591", "Q515"), "Q35",
           "municipality of Denmark, big city, city"),
    "FI": (("Q856076", "Q11870638", "Q1549591", "Q515"), "Q33",
           "municipality of Finland, city in Finland, big city, city"),
    "ES": (("Q2074737", "Q515"), "Q29", "municipality of Spain, city"),
    "PT": (("Q13217644", "Q15647906", "Q19833170", "Q1549591", "Q1131296"),
           "Q45", "municipality, city, town and freguesia of Portugal, big city"),
    "MX": (("Q1952852", "Q15045178", "Q123440126", "Q20202352", "Q515",
            "Q1549591"), "Q96",
           "municipality, City of, city in and locality of Mexico, city, big city"),
    "JP": (("Q1054813", "Q494721", "Q1059478", "Q4174776", "Q1137833",
            "Q1549591"), "Q17",
           "municipality, city, town, village and core city of Japan, big city"),
    "ZA": (("Q2666845", "Q515", "Q1500352", "Q3957"), "Q258",
           "municipality of South Africa, city, local municipality, town"),
    "IN": (("Q112684326", "Q58339518", "Q56436498", "Q1549591", "Q515"),
           "Q668", "municipality, town and village in India, big city, city"),
}

# The DMO hop. A host is nominated only if the city's own site links to it AND
# its domain carries both a tourism word and the city's name — either half alone
# is noise. `visitseattle.org` linked from seattle.gov qualifies; a link to
# `visitflorida.com` from a Florida town does not, and neither does a link to
# some other city's board.
TOURISM_WORD_RX = re.compile(
    r"visit|tourism|tourist|explore|discover|experience|cvb|"
    r"conventionandvisitors|travel", re.I)

# Hosts that carry a tourism word and are never a city's own board: state and
# national boards, booking engines, and the platforms a DMO's site is built on.
TOURISM_NOT_DMO = (
    "tripadvisor.", "expedia.", "booking.com", "airbnb.", "yelp.",
    "visitusa.", "travelocity.", "hotels.com", "viator.", "getyourguide.",
    "google.", "facebook.com", "instagram.com", "youtube.com",
)

# The adapters that can be pointed at a whole town. See the header: everything
# else would need a `venue` block, and a town has no single point.
CITYWIDE_SHAPEABLE = {"ics", "tribe", "mylisting"}

DEFAULT_CATEGORY = "community"

# A CivicPlus city is not one calendar, it is one per PROGRAMME — and the
# platform hands the programme's name over in the same link that carries the
# feed URL. CIVIC_DENY_RX already reads that name to refuse a tax calendar, and
# then every survivor was filed under DEFAULT_CATEGORY regardless of what it
# said. Measured 2026-09-13 over all 363 civic-discovered ics sources (324
# shipped plus 39 verified that day): every one of them `community`, including
# 34 named "Library", 8 named for a library's kids or teen programme, 2 named
# "Farmers Market" and one "Volunteer Opportunities". The fattest category in
# the catalog was being fed by calendars belonging to the thinnest — `kids` has
# 25 sources on the planet and `volunteer` 10, against `community`'s 1,010.
#
# WHAT IS AND IS NOT HERE IS THE WHOLE RULE, and it is AGENTS.md's own: "a
# config's category is a DEFAULT, so the right value depends on whether the
# calendar is pure or mixed."
#
#   • A library sub-calendar named for children ("Library - Kids & Teens",
#     "Ready to Read (Ages 0 - 5)") is PURE: 39 of 39 upcoming entries on West
#     Fargo's are storytimes. It states `kids`.
#   • A library's whole calendar is `learning`, which is what all 58 of the
#     hand-curated library feeds in ics_sources.json already say. This only
#     makes the discovered ones agree with them.
#   • "Parks & Recreation" is NOT here, and it is the biggest thing left out: 51
#     of the 52 name-matches for anything outdoorsy are a parks DEPARTMENT's
#     whole calendar, which is mixed by construction — it is where a town puts
#     its summer concert series, its movies in the park and its block party
#     alongside the trail walks. Filing those `outdoors` would have moved 51
#     sources of exactly that supply off awaresie, the neighbourhood door, to
#     win one genuinely-pure nature centre. The promotions in
#     mapsee_supabase_sync handle the individual trail walk far better than a
#     config default can.
#   • Nothing matches on a bare word that a PROPER NAME can carry. The first
#     pass used `\bmarkets?\b` and `\bservice\b` and duly proposed "The Hub At
#     Market Square" as a farmers market and "City Service Changes" as
#     volunteering — the same trap as `market` in map_category, one level up.
#
# Order matters: kids before learning, so a library's children's programme is
# read as children's rather than as a library.
_FEED_CATEGORY = [
    ("kids", re.compile(
        r"\b(kids?|childrens?|children'?s|teens?|tweens?|youth|preschool|"
        r"toddlers?|story\s?times?|ready\s+to\s+read)\b", re.I)),
    ("learning", re.compile(r"\blibrar(?:y|ies)\b", re.I)),
    # OUTDOORS and FITNESS, added 2026-09-13 once the park-agency seed list made
    # them worth having. Measured over all 587 civic+parks ics sources:
    #   • outdoors  6 sub-calendars ("Open Space", "Open Space Master Calendar",
    #     "Remington Nature Center") — small, but `outdoors` is the thinnest
    #     curated category on the map and every one of the six is unambiguous.
    #   • fitness  16, and they are nearly all AQUATICS: "Aquatic Center",
    #     "Aquatic Fitness", "Aquatics Public Swim", "Aquatics Jr High Swim
    #     Team", "Fitness Center", "Masters Swimming". A town's pool timetable
    #     is the one part of a parks department that is purely movement.
    #
    # `wellness` was written here and REMOVED: its only two hits are
    # "Department of Health, Wellness and Animal Services" and "Community -
    # Wellness and Recovery", neither of which is exercise. `parks & rec` is
    # still absent for the reason the note above this list records — 81
    # sub-calendars carry it and they are mixed by construction.
    ("outdoors", re.compile(
        r"\b(open\s+space|outdoor\s+adventures?|nature\s+cent(?:er|re)|"
        r"naturalist|trails?)\b", re.I)),
    ("fitness", re.compile(
        r"\b(aquatics?|fitness|masters?\s+swim(?:ming)?|"
        r"swim\s+(?:teams?|lessons?)|yoga)\b", re.I)),
    ("market", re.compile(r"\bfarmers?'?\s+markets?\b", re.I)),
    ("volunteer", re.compile(r"\bvolunteers?\b", re.I)),
]


def category_for_feed(name: str) -> str:
    """The category a CivicPlus programme's own NAME states, or the default."""
    for key, rx in _FEED_CATEGORY:
        if rx.search(name or ""):
            return key
    return DEFAULT_CATEGORY


# ---- the candidate generator -------------------------------------------------
# WDQS THROTTLES, AND IT SAYS SO. A 429 carries Retry-After and means "you have
# spent your query seconds, come back"; it is not a fact about the query and it
# is not a fact about the country. The driver treats any exception here as an
# UNREAD batch and leaves the cursor, which is right for a 504 — but a run that
# throws its whole civic budget away because the first request arrived a few
# seconds early is a country skipped for a whole rotation. Measured while
# building the country list: five queries in a row answered 200, the sixth and
# seventh answered 429, and waiting the header out cleared it.
#
# Once, not a loop: if the second attempt is also refused, the service means it.
SPARQL_RETRY_CAP = 75

# How many pages cities() will ask for before giving the caller what it has.
# Three, because two is not enough for Switzerland (2 cities in 40 rows) and
# more than three spends the civic run's budget on Wikidata rather than on the
# town halls the run exists to probe.
CITY_PAGES_MAX = 3


def _sparql(session, query: str, timeout: int = 120) -> List[Dict[str, Any]]:
    r = session.get(WIKIDATA_ENDPOINT,
                    params={"query": query, "format": "json"}, timeout=timeout)
    if r.status_code in (429, 503):
        wait = r.headers.get("Retry-After") or ""
        secs = int(wait) if wait.isdigit() else 30
        secs = min(secs, SPARQL_RETRY_CAP)
        print(f"  wikidata {r.status_code}, waiting {secs}s and asking once more")
        time.sleep(secs)
        r = session.get(WIKIDATA_ENDPOINT,
                        params={"query": query, "format": "json"}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"wikidata http {r.status_code}: {r.text[:120]}")
    return r.json()["results"]["bindings"]


def _coord(point: str) -> Tuple[Optional[float], Optional[float]]:
    """Wikidata gives `Point(lon lat)`. Note the order — it is not lat/lon."""
    m = re.match(r"Point\(([-\d.]+)\s+([-\d.]+)\)", point or "")
    if not m:
        return None, None
    return float(m.group(2)), float(m.group(1))


def cities(session, country: str = "US", limit: int = 200, offset: int = 0,
           timeout: int = 200) -> Tuple[List[Dict[str, Any]], int]:
    """(cities, rows_read). Ordered by population, largest first.

    DE-DUPLICATED BY QID HERE RATHER THAN IN SPARQL. San Antonio comes back
    three times and Azle twice, because a city sits in more than one
    administrative chain and the OPTIONALs multiply rows. Doing it in the query
    means GROUP BY plus SAMPLE around the label service, which is where these
    queries start timing out again; doing it here costs a dict.

    THE TIMEOUT IS 200 SECONDS AND THAT IS NOT GENEROSITY. The US query answers
    in 1.9s because one class names the country. Everywhere else takes several,
    and the cost climbs steeply with the number of them: Canada over five
    classes is 34s and over six it is 117s, because ORDER BY DESC(?pop) has to
    sort the whole matched set before LIMIT sees it. 120 would have timed out
    on a country that works, and a timeout here is not a slow answer — the
    driver treats the batch as UNREAD and the cursor stays, so a country too
    slow to read would never advance past its own first page.

    WHICH IS WHY THE ROW COUNT COMES BACK TOO. LIMIT/OFFSET are over ROWS, and
    a caller holding a cursor has only the deduplicated cities to count — so
    advancing the offset by "cities I walked" under-advances by however many
    duplicates the batch happened to contain, and the next run re-reads the tail
    of this one for ever. Measured on the first live run: a 12-row batch was 7
    cities. The two numbers are different things and both have to be returned.
    """
    entry = CITY_CLASSES.get(country.upper())
    if not entry:
        raise ValueError(f"no city class known for {country!r} — see CITY_CLASSES")
    classes, scope, _label = entry
    # ONE CLASS OR SEVERAL, AND THE DIFFERENCE IS THE COUNTRY'S DOING. The US
    # models every incorporated place as one class and 5,770 of them carry all
    # four properties. Nowhere else does: Canada's settlements-with-a-website
    # split across `municipality`, `city or town of Quebec`, `town`, `parish
    # municipality`, `village` and a per-province class for Ontario and Alberta,
    # so naming one would take 652 of 1,500 and miss Toronto outright.
    values = " ".join(f"wd:{q}" for q in classes)
    # And the country filter comes WITH the general classes, not instead of
    # them. `town` (Q3957) and `village` (Q532) are worldwide classes; without
    # wdt:P17 a Canadian sweep returns Bavarian villages.
    scope_line = f"?city wdt:P17 wd:{scope} ." if scope else ""
    # The state OPTIONAL is a US shape — Q35657 IS "U.S. state" — and it walks
    # two P131 hops to get there. Outside the US it can only ever bind nothing,
    # and it is not free, so it is not asked. Region is then empty and _suffix
    # names the country instead, which is the part that actually places a pin.
    state_line = ("OPTIONAL { ?city wdt:P131 ?county . ?county wdt:P131 ?state . "
                  "?state wdt:P31 wd:Q35657 . }" if country.upper() == "US" else "")
    # Built per page rather than once and .format()ted: the body is full of
    # SPARQL braces, and a template that survives an f-string is one .format()
    # then reads as field names.
    def _query(lim, off):
        return f"""
SELECT ?city ?cityLabel ?stateLabel ?site ?pop ?coord ?article WHERE {{
  VALUES ?cls {{ {values} }}
  ?city wdt:P31 ?cls ;
        wdt:P856 ?site ;
        wdt:P625 ?coord ;
        wdt:P1082 ?pop .
  {scope_line}
  {state_line}
  OPTIONAL {{ ?article schema:about ?city ;
                       schema:isPartOf <https://en.wikipedia.org/> . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?pop) LIMIT {int(lim)} OFFSET {int(off)}
"""

    out: Dict[str, Dict[str, Any]] = {}
    rows = 0
    # PAGE UNTIL `limit` CITIES, NOT UNTIL `limit` ROWS, and the difference is
    # not small. A row is a (city, class, population statement) combination, so
    # a country whose classes overlap and whose towns carry a population series
    # returns the same town many times. Measured 2026-09-20 over a 40-row page:
    # the United States is 40 cities and Denmark 37, but Belgium is 12, South
    # Africa 9, Finland 4 and SWITZERLAND 2 — a civic run that was meant to
    # probe forty Swiss towns probed two, and the rotation only comes back to
    # Switzerland once a turn of the wheel. Asking for more rows is the whole
    # fix; asking in SPARQL for one row per city means GROUP BY and SAMPLE
    # around the label service, which is where these queries start timing out.
    #
    # The page cap is what keeps a pathological country from spending the whole
    # civic budget on Wikidata rather than on town halls, and `rows` is the
    # TOTAL consumed, because that is what the caller's cursor advances by.
    for _page in range(CITY_PAGES_MAX):
        want = max(int(limit), 1)
        page_size = min(want * 2, 500) if _page else want
        page = _sparql(session, _query(page_size, int(offset) + rows), timeout)
        if not page:
            break
        rows += _absorb(page, out, country, rows)
        if len(out) >= want or len(page) < page_size:
            break
    # NOT truncated to `limit`. A later page can carry the tail of a city whose
    # first row was in an earlier one, and `rows` counts every row consumed — so
    # dropping cities here would advance the cursor past towns nobody probed.
    # An overshoot is a bigger batch, which the caller's own budget bounds.
    return list(out.values()), rows


def _absorb(page, out, country, rows_before=0):
    """Fold one page of bindings into `out`, returning the rows it held.

    Each city remembers `_row`, the 0-based row it FIRST appeared at, counted
    from the start of the batch. That is what makes a partial read exact: a run
    cut short by its deadline advances the cursor to the first unread city's own
    row, where advancing by "cities I read" would under-advance by however many
    duplicate rows they held — three quarters of a Danish batch — and the walk
    would re-read its own head for ever.
    """
    rows = 0
    for b in page:
        rows += 1
        qid_city = b["city"]["value"].rsplit("/", 1)[-1]
        if qid_city in out:
            continue
        lat, lon = _coord(b.get("coord", {}).get("value", ""))
        out[qid_city] = {
            "qid": qid_city,
            "kind": "city",
            "_row": rows_before + rows - 1,
            "name": b["cityLabel"]["value"],
            "city": b["cityLabel"]["value"],
            "region": (b.get("stateLabel") or {}).get("value"),
            "country": country.upper(),
            "url": b["site"]["value"],
            "lat": lat, "lon": lon,
            "population": int(float(b["pop"]["value"])),
            # The DMO stream's only handle. Absent for a city with no English
            # article, and that is a skip rather than a guess — see
            # dmo_nominations.
            "article": (b.get("article") or {}).get("value"),
        }
    return rows


# ---- the DMO hop -------------------------------------------------------------
def _host(u: str) -> str:
    return urlparse(u).netloc.lower().removeprefix("www.")


def _slug(name: str) -> str:
    """'Mount Vernon' -> 'mountvernon'. What a domain would spell it as."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _looks_like_dmo(host: str, city_name: str) -> bool:
    """A tourism word AND the city's name, and both halves are load-bearing.

    A tourism word alone matches tripadvisor and the STATE board — a Florida
    town's article links visitflorida.com, which is a real tourism site and not
    this town's calendar. The city's name alone matches every department the
    city runs. Together they name one thing.
    """
    slug = _slug(city_name)
    if len(slug) < 4:
        return False                   # "Ely" would match half the internet
    if any(bad in host for bad in TOURISM_NOT_DMO):
        return False
    if not TOURISM_WORD_RX.search(host):
        return False
    return slug in re.sub(r"[^a-z0-9]", "", host)


WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_TITLES_PER_CALL = 50          # the API's own limit for a titles= batch


def dmo_nominations(session, places: Iterable[Dict[str, Any]],
                    timeout: int = 45) -> Dict[str, List[str]]:
    """{qid: [tourism-board homepage, ...]} from the cities' Wikipedia articles.

    Batched at the API's own limit, so a 200-city sweep spends four requests on
    this stream rather than 200. Places with no `article` are skipped rather
    than looked up by name: a title guessed from a label is how you end up
    reading the article about a different Springfield.
    """
    by_title = {p["article"].rsplit("/", 1)[-1].replace("_", " "): p
                for p in places if p.get("article")}
    out: Dict[str, List[str]] = {}
    titles = list(by_title)
    for i in range(0, len(titles), WIKI_TITLES_PER_CALL):
        batch = titles[i:i + WIKI_TITLES_PER_CALL]
        try:
            r = session.get(WIKI_API, timeout=timeout, params={
                "action": "query", "prop": "extlinks", "ellimit": "max",
                "titles": "|".join(batch), "format": "json", "formatversion": 2})
            pages = r.json().get("query", {}).get("pages", [])
        except Exception:                                         # noqa: BLE001
            continue                   # one bad batch is not the whole sweep
        for page in pages:
            place = by_title.get(page.get("title") or "")
            if not place:
                continue
            hosts, seen = [], set()
            for link in page.get("extlinks") or []:
                u = link if isinstance(link, str) else link.get("url", "")
                h = _host(u)
                if not h or h in seen:
                    continue
                seen.add(h)
                if _looks_like_dmo(h, place.get("city") or place.get("name") or ""):
                    hosts.append(f"https://{h}/")
            if hosts:
                out[place["qid"]] = hosts
    return out


# ---- turning a find into a city-wide candidate -------------------------------
# The countries this file can NAME in a geocode suffix. An ISO code is not a
# name — ", Zurich, CH" is what the venue backend used to write and it is
# readable neither by a geocoder nor by catalog_curate's coverage report, which
# read the code itself as the metro. Only the countries CITY_CLASSES sweeps need
# to be here; a code with no name falls back to the code, which is still better
# than nothing and is visible in the config for a curator to fix.
COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom",
    "IE": "Ireland", "AU": "Australia", "NZ": "New Zealand",
    "DE": "Germany", "FR": "France", "NL": "Netherlands", "BE": "Belgium",
    "CH": "Switzerland", "AT": "Austria", "SE": "Sweden", "NO": "Norway",
    "DK": "Denmark", "FI": "Finland", "ES": "Spain", "IT": "Italy",
    "PT": "Portugal", "PL": "Poland", "CZ": "Czechia", "BR": "Brazil",
    "MX": "Mexico", "JP": "Japan", "ZA": "South Africa", "IN": "India",
    "SG": "Singapore",
}


def _suffix(place: Dict[str, Any]) -> str:
    """What an iCal LOCATION gets appended before it is geocoded.

    An ics config carries no coordinates, so this is the ONLY thing placing its
    events — the same rule the venue backend's _suffix states, and it matters
    more here: a city calendar's LOCATION lines are bare venue names ('Pickering
    Barn') far more often than a single venue's are.

    THE COUNTRY IS PART OF IT FOR EVERYWHERE BUT THE US. While this file swept
    one country the suffix could be ", Issaquah, Washington" and mean it: the
    geocoder's default is the US and so was the coverage report's. Neither is
    true for Bern, and a bare ", Bern" is a town in Kansas — 'Bern, Indiana',
    'Bern, Idaho' and 'Bern, Kansas' are all real places a US-default geocoder
    reaches first. The US keeps the shorter form because 900 shipped sources are
    already written that way and the state name already pins the country.
    """
    bits = [x for x in (place.get("city"), place.get("region")) if x]
    code = (place.get("country") or "US").upper()
    if code != "US":
        bits.append(COUNTRY_NAMES.get(code, code))
    return (", " + ", ".join(bits)) if bits else ""


def why_no_candidate(found: Dict[str, Any]) -> str:
    """Why a successful probe still produced nothing to verify."""
    adapter = found.get("adapter")
    if not adapter:
        return "no-adapter(" + ("+".join(found.get("labels") or []) or "nothing") + ")"
    if not found.get("cal_url"):
        return "no-listing-url"
    if adapter not in CITYWIDE_SHAPEABLE:
        # Not a failure of the site: a real calendar on an adapter that has no
        # way to say "everything here is in this town". Named so the gap is a
        # to-do rather than a silent loss.
        return "no-citywide-shape(" + adapter + ")"
    if adapter == "ics" and not found.get("ics"):
        return "ics-without-feed(" + ("+".join(found.get("labels") or []) or "?") + ")"
    return "unknown"


def to_candidate(place: Dict[str, Any], found: Dict[str, Any],
                 kind: str = "city") -> Optional[Dict[str, Any]]:
    """A verify-able candidate in the shape its adapter's config file wants.

    `kind` is city or dmo, and it only changes the NAME and the provenance
    string — the shape is the same, because both are calendars for a whole town.
    """
    adapter, cal = found.get("adapter"), found.get("cal_url")
    if not adapter or not cal or adapter not in CITYWIDE_SHAPEABLE:
        return None
    label = "+".join(found.get("labels") or [])
    common = {
        "name": place.get("name"),
        "category": DEFAULT_CATEGORY,
        "_found": f"civic:{kind} -> {label}",
    }
    if adapter == "ics":
        ics = found.get("ics")
        if not ics:
            return None
        return dict(common, type="ics", url=osm._https(urljoin(cal, ics)),
                    geocode_suffix=_suffix(place), limit=300)
    if adapter == "tribe":
        o = urlparse(cal)
        # 365 rather than the venue backend's 120: a town calendar carries the
        # once-a-year things a venue's does not — Visit Issaquah has 68 events
        # past the 180-day line — and those are the ones worth planning around.
        return dict(common, type="tribe", base_url=f"{o.scheme}://{o.netloc}",
                    within_days=365, max_pages=15,
                    default_city=place.get("city"),
                    default_region=place.get("region"),
                    default_country=place.get("country"))
    if adapter == "mylisting":
        return dict(common, type="mylisting", explore_url=cal,
                    default_city=place.get("city"),
                    default_region=place.get("region"),
                    default_country=place.get("country"))
    return None


def civicplus_candidates(session, place: Dict[str, Any], origin: str,
                         timeout: int = 15
                         ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """One candidate per PROGRAMME category on a CivicPlus site.

    CivicPlus has no whole-calendar export — see the note above
    CIVICPLUS_ICAL_RX — so a city is several sources, and taking one of them
    would take a tenth of the town. Each is proposed separately and verified
    separately, which is also what lets one dead category fail without the
    other nine.
    """
    kept, rejected = osm.civicplus_feeds(session, origin, timeout)
    out = []
    for url, cat in kept:
        out.append({
            "type": "ics",
            "name": f"{place.get('name')} — {cat}",
            # `cat` is the programme's own name, which is the only thing that
            # knows this feed is a library's or a farmers market's. See
            # category_for_feed.
            "category": category_for_feed(cat),
            "url": url,
            "geocode_suffix": _suffix(place),
            "limit": 300,
            "_found": f"civic:city -> civicplus[{cat}]",
        })
    return out, rejected


def probe(session, place: Dict[str, Any], timeout: int = 18
          ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch a city's site and return (candidates, find).

    A CivicPlus city is SEVERAL candidates, one per programme category, because
    the platform has no whole-calendar export — see civicplus_feeds. Anything
    else is at most one.
    """
    found = osm.find_calendar(session, place["url"], timeout=timeout)
    cands: List[Dict[str, Any]] = []
    if "civicplus" in (found.get("labels") or []):
        cands, dropped = civicplus_candidates(session, place, place["url"], timeout)
        found["extra"] = dict(found.get("extra") or {}, civicplus_dropped=dropped)
    if not cands:
        one = to_candidate(place, found, "city")
        if one:
            cands = [one]
    return cands, found


def probe_dmo(session, place: Dict[str, Any], home: str, timeout: int = 18
              ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The second hop: a nominated tourism board, probed like any other site.

    The candidate carries the CITY's name and defaults, not the board's, because
    that is what the events are about — and it is what makes a Visit Issaquah
    entry say Issaquah, WA even though nothing on the domain does.
    """
    found = osm.find_calendar(session, home, timeout=timeout)
    one = to_candidate(place, found, "dmo")
    cands = [one] if one else []
    if not cands and "civicplus" in (found.get("labels") or []):
        cands, dropped = civicplus_candidates(session, place, home, timeout)
        found["extra"] = dict(found.get("extra") or {}, civicplus_dropped=dropped)
    if cands:
        host = _host(home)
        for c in cands:
            c["name"] = f"{place.get('name')} ({host})"
    return cands, found
