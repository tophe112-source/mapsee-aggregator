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
CITY_CLASSES = {
    "US": ("Q1093829", "city in the United States"),
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


# ---- the candidate generator -------------------------------------------------
def _sparql(session, query: str, timeout: int = 120) -> List[Dict[str, Any]]:
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
           timeout: int = 120) -> Tuple[List[Dict[str, Any]], int]:
    """(cities, rows_read). Ordered by population, largest first.

    DE-DUPLICATED BY QID HERE RATHER THAN IN SPARQL. San Antonio comes back
    three times and Azle twice, because a city sits in more than one
    administrative chain and the OPTIONALs multiply rows. Doing it in the query
    means GROUP BY plus SAMPLE around the label service, which is where these
    queries start timing out again; doing it here costs a dict.

    WHICH IS WHY THE ROW COUNT COMES BACK TOO. LIMIT/OFFSET are over ROWS, and
    a caller holding a cursor has only the deduplicated cities to count — so
    advancing the offset by "cities I walked" under-advances by however many
    duplicates the batch happened to contain, and the next run re-reads the tail
    of this one for ever. Measured on the first live run: a 12-row batch was 7
    cities. The two numbers are different things and both have to be returned.
    """
    cls = CITY_CLASSES.get(country.upper())
    if not cls:
        raise ValueError(f"no city class known for {country!r} — see CITY_CLASSES")
    qid = cls[0]
    query = f"""
SELECT ?city ?cityLabel ?stateLabel ?site ?pop ?coord ?article WHERE {{
  ?city wdt:P31 wd:{qid} ;
        wdt:P856 ?site ;
        wdt:P625 ?coord ;
        wdt:P1082 ?pop .
  OPTIONAL {{ ?city wdt:P131 ?county . ?county wdt:P131 ?state .
             ?state wdt:P31 wd:Q35657 . }}
  OPTIONAL {{ ?article schema:about ?city ;
                       schema:isPartOf <https://en.wikipedia.org/> . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?pop) LIMIT {int(limit)} OFFSET {int(offset)}
"""
    out: Dict[str, Dict[str, Any]] = {}
    rows = 0
    for b in _sparql(session, query, timeout):
        rows += 1
        qid_city = b["city"]["value"].rsplit("/", 1)[-1]
        if qid_city in out:
            continue
        lat, lon = _coord(b.get("coord", {}).get("value", ""))
        out[qid_city] = {
            "qid": qid_city,
            "kind": "city",
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
    return list(out.values()), rows


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
def _suffix(place: Dict[str, Any]) -> str:
    """What an iCal LOCATION gets appended before it is geocoded.

    An ics config carries no coordinates, so this is the ONLY thing placing its
    events — the same rule the venue backend's _suffix states, and it matters
    more here: a city calendar's LOCATION lines are bare venue names ('Pickering
    Barn') far more often than a single venue's are.
    """
    bits = [x for x in (place.get("city"), place.get("region")) if x]
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
            "category": DEFAULT_CATEGORY,
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
