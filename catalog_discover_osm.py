#!/usr/bin/env python3
"""
catalog_discover_osm.py — find venue CALENDARS anywhere on earth, from OSM.

The other discovery backends read CATALOGS: Socrata and CKAN list open-data
datasets, joinmobilizon lists federated instances. That finds civic and
open-data feeds, and it structurally cannot find the long tail — a gallery, a
zendo, a bookshop with a reading series, a brewery with a Tuesday quiz. Nobody
publishes those to a data portal, so no catalog query will ever return them,
however the query is worded.

They are, however, ON THE MAP. OpenStreetMap tags the places that PROGRAMME
things — theatre, arts_centre, community_centre, library, nightclub, museum,
place_of_worship, sports_centre — and a good share of them carry a `website`.
That makes the candidate list a geographic query rather than a text search, and
it works identically in Lisbon, Osaka and Seattle, which is the whole point.

Measured before this was written, against the 1,181 hand-curated Seattle sources
another aggregator publishes (uncouchme.com/about):

    1,656 programme-venues in the Seattle bbox, 599 carrying a website
    98 of those hosts are ALSO on that hand-built list — the method finds the
       same real venues a human found
    387 more are not on it at all, and 114 of those 387 (29%) turned out to
       have a calendar on a platform this repo already ingests

So it is not a way of copying somebody's list; it is a way of generating one,
and the overlap is what says the generated list is about the right places.

WHAT IT CANNOT FIND, and this is worth knowing before trusting it: a Meetup
group, an Eventbrite organiser, a blog, a newspaper's listings column, or any
venue whose OSM entry has no `website`. Those were 638 of the 745 hosts on the
hand-built list. Discovery here is a COMPLEMENT to that kind of curation, not a
replacement for it, and a metro swept clean by this is not a metro finished.

THE PIPELINE
    metro bbox -> Overpass -> venues with a website
               -> fetch the site, find the page that is its calendar
               -> fingerprint the platform  (Squarespace? The Events Calendar?)
               -> emit a candidate typed for THAT adapter's config file
    catalog_curate.py verify then has to prove it returns future events, and
    only then will merge write it into a *_sources.json. Nothing here adds a
    source; it only proposes one.

WHY THE COORDINATES MATTER MORE THAN THEY LOOK
Overpass hands back the venue's surveyed point and its addr:* tags, and a
single-venue calendar needs exactly that: The Royal Room's every event carries
the placeholder location "-", so its config's `venue` block is the only thing
that places it — and, through the coordinates, the only thing that turns its
naive local timestamps into real instants. The discovery source and the missing
half of the config are the same query. That is why a candidate from here ships
a venue block already filled in.

TWO REFUSALS, both deliberate:
  • A BOT CHALLENGE IS A NO. SiteGround answers 202 with an `sgcaptcha` body and
    Cloudflare answers 403 with "Just a moment"; both are somebody deliberately
    turning bot management on, and getting past them means impersonating a
    browser. Recorded as declined, never retried with a browser UA. The live
    example is dmhsus.org, which challenges even /robots.txt.
  • AN OFF-HOST CALENDAR IS NOT THIS BACKEND'S FIND. Plenty of venues keep their
    events on Eventbrite or Meetup. Those need an organiser/group id and belong
    to those adapters; emitting the venue's own domain for them would produce a
    source that ingests nothing. They are counted and reported, not proposed.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_BACKOFF_S = 5

# Places that put on a programme. Deliberately narrow: a restaurant that serves
# dinner every night is not a calendar, and sweeping every shop would bury the
# real finds under thousands of sites with nothing to import.
OSM_SELECTORS = [
    'nwr["amenity"~"^(theatre|arts_centre|community_centre|library|nightclub|'
    'cinema|social_facility|events_venue|conference_centre|music_venue)$"]',
    'nwr["tourism"~"^(museum|gallery|zoo|aquarium)$"]',
    'nwr["leisure"~"^(sports_centre|dance|garden)$"]',
    'nwr["club"]',
    'nwr["amenity"="place_of_worship"]',
    'nwr["shop"="books"]',
    'nwr["craft"="brewery"]',
    'nwr["office"="ngo"]',
]

# What kind of place it is -> the config `category` a candidate is proposed with.
# A config category is a DEFAULT the classifier then refines, so the rule is the
# one CLAUDE.md draws from the cycling clubs: a PURE calendar may state its
# specific key, a MIXED one must state `community` and let the promotion rules
# sort it. A community centre or a church hall runs volunteering, a toddler
# group and a concert in the same week, so those stay `community` — from there
# the volunteer and kids rules can rescue what belongs to them, and nothing is
# claimed that was not earned.
KIND_CATEGORY = {
    "theatre": "theater", "cinema": "theater",
    "nightclub": "music", "music_venue": "music",
    "arts_centre": "arts", "museum": "arts", "gallery": "arts",
    "library": "learning", "books": "learning",
    "sports_centre": "fitness", "dance": "fitness",
    "brewery": "food",
    "garden": "outdoors", "zoo": "outdoors", "aquarium": "outdoors",
    "community_centre": "community", "social_facility": "community",
    "place_of_worship": "community", "events_venue": "community",
    "conference_centre": "community", "club": "community",
    "ngo": "community",
}
DEFAULT_CATEGORY = "community"

# href or link text that means "the calendar is through here". Multilingual on
# purpose: the backend sweeps 28 countries and an English-only matcher would
# quietly make it a US/UK tool wearing a global name.
CAL_LINK_RX = re.compile(
    r"(events?|calendar|kalender|calendrier|calendario|agenda|programm|programme|"
    r"programa|whats[-_ ]?on|shows?|gigs?|concerts?|veranstaltung|evenement|"
    r"evenimente|aktiviteter|tapahtumat|arrangement)", re.I)

# A challenge does not answer 403. SiteGround answers 202, and the WAF in front
# of theblackaltar.org answers a clean 200 with a spinner that says "One moment,
# please..." — on `/wp-json/.../events?from=…`, while the SAME endpoint without a
# query string returns real JSON. A 200 is the dangerous case: the body is HTML
# where JSON was expected, so a parser reads it as a broken feed, and an
# HTML-tolerant one reads it as a calendar with nothing on. Neither is true, and
# both are silent. Match the words these pages actually show.
CHALLENGE_RX = re.compile(
    r"sgcaptcha|just a moment|one moment,?\s*please|being verified|"
    r"cf-browser-verification|challenge-platform|captcha-delivery|_Incapsula_|"
    r"/cdn-cgi/challenge|checking your browser|enable javascript and cookies",
    re.I)

# Hosts whose events belong to a DIFFERENT adapter that needs its own id.
OFFSITE_HOSTS = (
    "eventbrite.", "meetup.com", "facebook.com", "fb.me", "instagram.com",
    "linktr.ee", "ticketmaster.", "axs.com", "seatgeek.com", "dice.fm",
    "lu.ma", "songkick.com", "bandsintown.com", "tickettailor.com",
    "universe.com", "showpass.com", "humanitix.com", "trybooking.com",
)

# A website tag that is not a website we can read.
BAD_SITE_RX = re.compile(r"\.(pdf|jpe?g|png|doc x?|zip)$|^mailto:|^tel:", re.I)


# ---- the platform fingerprint ------------------------------------------------
# Each entry: (label, regex over the page source, the adapter that reads it).
# The label is what the ledger and the report show; the adapter decides which
# *_sources.json a verified candidate is merged into.
PLATFORM_SIGNS = [
    ("tribe",            re.compile(r"/wp-content/plugins/the-events-calendar|tribe-events|tribe_events", re.I), "tribe"),
    ("wp-event-manager", re.compile(r"/wp-content/plugins/wp-event-manager", re.I), "jsonld"),
    ("mylisting",        re.compile(r"/themes/my-listing|CASE27", re.I), "mylisting"),
    ("squarespace",      re.compile(r"static1\.squarespace\.com|squarespace\.com/universal|Squarespace\.afterBodyLoad", re.I), "squarespace"),
    ("wix",              re.compile(r"wixstatic\.com|static\.parastorage\.com", re.I), "jsonld"),
    ("localist",         re.compile(r"localist\.com|/api/2/events", re.I), "localist"),
    ("trumba",           re.compile(r"trumba\.com", re.I), "ics"),
    ("libcal",           re.compile(r"libcal\.com", re.I), "ics"),
    ("gancio",           re.compile(r"gancio", re.I), "gancio"),
    ("venuepilot",       re.compile(r"venuepilot", re.I), "venuepilot"),
    # Events Manager does NOT emit schema.org Event blocks — the Bongo Club's
    # page carries WebPage and WebSite and nothing else — so routing it to the
    # JSON-LD adapter proposed a source that could never read it. What it does
    # have is an iCal export on any calendar page.
    ("events-manager",   re.compile(r"/wp-content/plugins/events-manager", re.I), "ics"),
    ("modern-events",    re.compile(r"modern-events-calendar|mec-event", re.I), "jsonld"),
    # My Calendar (100k+ WordPress installs, and the plugin small arts orgs and
    # congregations actually reach for). It publishes iCal at a FIXED path and
    # never links to it from the calendar page, so scraping for an .ics href
    # finds nothing and a perfectly readable site looks unreadable. A platform
    # can imply a feed URL — see FEED_TEMPLATES.
    ("my-calendar",      re.compile(r"/wp-content/plugins/my-calendar|mc-navigation-button|mc-events-link", re.I), "ics"),
]

# DETECTING A SITE BUILDER IS NOT DETECTING A CALENDAR, and conflating the two
# is where this backend wastes most of its verification budget. tribe,
# my-calendar and wp-event-manager are calendar PLUGINS: finding one means the
# site has an events system, and a feed follows. Squarespace and Wix are how the
# whole site is BUILT — every page of them matches, including a hand-written
# "What's On" with nothing behind it. Measured on the first London sweep: 4 of
# the 5 candidates that failed verification with "no schema.org Event blocks"
# were Wix sites detected this way, and White Bear Theatre's turned out not to
# use Wix Events at all.
#
# So a builder has to show its EVENTS app before it may be proposed. Squarespace
# names the collection in the body class and stamps every rendered item; Wix
# routes its events through /event-info/. Volunteer Park Trust (a real events
# collection) matches 2 and 448 times; Tramshed's What's On page, 0 and 0.
BUILDER_EVIDENCE = {
    "squarespace": re.compile(r"collection-type-events|eventlist-meta|sqs-events", re.I),
    "wix": re.compile(r"/event-info/|wix-events|events-page-app", re.I),
}

# Feeds that live at a known path rather than in a link. Probed only when the
# platform was actually detected, and only believed when the fetch comes back a
# calendar: a constructed URL that is merged unproven is a source ingesting zero.
# {origin} is the site root, {cal} the calendar page we landed on. Prefer {cal}
# where the plugin scopes its export to the page: the Bongo Club's site root
# gives 27KB of iCal and its /events-main/ page gives 3.1MB of the same feed.
FEED_TEMPLATES = {
    "my-calendar": "{origin}/?feed=my-calendar-ics",
    "events-manager": "{cal}?ical=1",
}
_LD_EVENT_RX = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_LD_IS_EVENT = re.compile(r'"@type"\s*:\s*(?:"[A-Za-z]*Event"|\[[^\]]*Event)', re.I)
_ICS_RX = re.compile(r'href="([^"]*\.ics(?:\?[^"]*)?)"|href="(webcal://[^"]+)"', re.I)


def constructed_feed(session, origin: str, labels: Iterable[str],
                     timeout: int = 15, cal: Optional[str] = None) -> Optional[str]:
    """A feed URL implied by the PLATFORM, proved by fetching it.

    Constructing a URL is a guess; a guess that is merged into a config becomes a
    source that ingests nothing. So the guess is fetched and has to come back as
    a calendar — and the challenge check runs first, because the WAF that sits in
    front of these sites answers the guess with 200 and a spinner.
    """
    for label in labels:
        tmpl = FEED_TEMPLATES.get(label)
        if not tmpl:
            continue
        base = (cal or origin).split("?")[0]
        url = tmpl.format(origin=origin.rstrip("/"),
                          cal=base if base.endswith("/") else base + "/")
        try:
            r = session.get(url, timeout=timeout)
        except Exception:                                         # noqa: BLE001
            continue
        if r.status_code != 200 or CHALLENGE_RX.search(r.text[:6000]):
            continue
        if "BEGIN:VCALENDAR" in r.text[:4000]:
            return url
    return None


def _has_event_block(body: str) -> bool:
    """True when the adapter that would ingest this page can find an Event on it."""
    import mapsee_ingest_jsonld as jl
    for blk in jl._LD_RX.findall(body):
        doc = jl._parse_ld(blk)
        if doc is None:
            continue
        for item in jl._iter_items(doc):
            if jl._is_event(item):
                return True
    return False


def fingerprint(body: str) -> Tuple[List[str], Optional[str]]:
    """(labels, ics_url). Labels are every platform the page shows signs of —
    except a site builder with no sign of its events app, which is dropped: see
    BUILDER_EVIDENCE."""
    labels = []
    for name, rx, _ in PLATFORM_SIGNS:
        if not rx.search(body):
            continue
        ev = BUILDER_EVIDENCE.get(name)
        if ev and not ev.search(body):
            continue
        labels.append(name)
    # READ IT THE WAY THE INGESTER WILL. This used to be a regex over the raw
    # block, which said yes to 40 Event blocks on the Royal Lyceum's programme
    # that mapsee_ingest_jsonld could not parse at all — so discovery proposed
    # the page, verification found "no Event blocks", and the disagreement was
    # invisible from either end. Parse with the adapter's own parser and the two
    # cannot drift: whatever it can read is what gets proposed.
    if _has_event_block(body):
        labels.append("jsonld-event")
    m = _ICS_RX.search(body)
    ics = _https((m.group(1) or m.group(2)) if m else None)
    if ics:
        labels.append("ics")
    return labels, ics


def adapter_for(labels: Iterable[str]) -> Optional[str]:
    """The adapter to propose this to. Order matters: a Squarespace site that
    ALSO ships an Event block is still a Squarespace site, and its adapter reads
    the collection page rather than crawling per-event URLs."""
    # my-calendar outranks the page's own Event block deliberately: its iCal
    # export is the WHOLE calendar, where the JSON-LD on the page is whatever
    # that one view happened to render.
    order = ["tribe", "mylisting", "localist", "gancio", "venuepilot",
             "squarespace", "my-calendar", "trumba", "libcal",
             "wp-event-manager", "events-manager", "modern-events", "wix",
             "jsonld-event", "ics"]
    labs = set(labels)
    by_label = {name: adapter for name, _, adapter in PLATFORM_SIGNS}
    by_label["jsonld-event"] = "jsonld"
    by_label["ics"] = "ics"
    for name in order:
        if name in labs:
            return by_label.get(name)
    return None


# ---- Overpass ----------------------------------------------------------------
def _overpass_query(bbox: str) -> str:
    parts = "".join(f"{sel}({bbox});" for sel in OSM_SELECTORS)
    return f"[out:json][timeout:180];({parts});out tags center;"


def overpass_venues(session, bbox: str, name: str = "?",
                    endpoint: str = OVERPASS_ENDPOINT, quiet: bool = False):
    """Venues in the bbox that publish a website.

    Returns None if OVERPASS NEVER ANSWERED, and [] if it answered with nothing.
    Those are not the same fact and returning [] for both is what let a sweep
    report "Adelaide: 0 venues publish a website" for nine metros in a row that
    had simply never been asked — while the cursor advanced past all nine. The
    endpoint hands out a couple of slots and answers 429 or 504 when they are
    busy, which is normal traffic across a sweep and worth waiting out; a
    connection reset after four tries is the endpoint declining, and the metro is
    unread.
    """
    q = _overpass_query(bbox)
    for attempt in range(4):
        try:
            r = session.post(endpoint, data=q.encode("utf-8"), timeout=200)
            if r.status_code in (429, 504) and attempt < 3:
                wait = int(r.headers.get("Retry-After") or 0) or (OVERPASS_BACKOFF_S * 3 ** attempt)
                if not quiet:
                    print(f"  overpass {name}: {r.status_code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except Exception as exc:                                  # noqa: BLE001
            if attempt == 3:
                if not quiet:
                    print(f"  overpass {name} FAILED: {type(exc).__name__}: {exc}")
                return None
            time.sleep(OVERPASS_BACKOFF_S * 3 ** attempt)
    else:
        return None
    out = []
    for e in elements:
        t = e.get("tags") or {}
        site = t.get("website") or t.get("contact:website")
        if not site or BAD_SITE_RX.search(site):
            continue
        if not site.startswith(("http://", "https://")):
            site = "https://" + site.lstrip("/")
        kind = (t.get("amenity") or t.get("tourism") or t.get("leisure")
                or ("club" if t.get("club") else None) or t.get("shop")
                or t.get("craft") or t.get("office") or "")
        out.append({
            "name": t.get("name") or t.get("operator") or "?",
            "url": site,
            "kind": kind,
            "lat": e.get("lat") or (e.get("center") or {}).get("lat"),
            "lon": e.get("lon") or (e.get("center") or {}).get("lon"),
            "street": " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x) or None,
            "city": t.get("addr:city"),
            "region": t.get("addr:state") or t.get("addr:province"),
            "postal_code": t.get("addr:postcode"),
            "country": t.get("addr:country"),
        })
    return out


# ---- finding the calendar on a venue's own site ------------------------------
def _https(u: Optional[str]) -> Optional[str]:
    """webcal:// is https:// wearing a hat.

    It is the standard scheme for "subscribe to this calendar", and plenty of
    parish and club sites publish their .ics that way. requests has no adapter
    for it, so a candidate carrying one does not fail verification for a reason
    about the FEED — it raises InvalidSchema and reads as a broken source. Two
    Sydney candidates were lost to that before this existed, and the ics adapter
    would have raised the same way on the merged config.
    """
    if u and u.lower().startswith("webcal://"):
        return "https://" + u[9:]
    return u


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower().lstrip("www.") == urlparse(b).netloc.lower().lstrip("www.")


def _offsite(u: str) -> Optional[str]:
    h = urlparse(u).netloc.lower()
    for host in OFFSITE_HOSTS:
        if host in h:
            return host.strip(".")
    return None


def _rank(u: str) -> Tuple[int, int]:
    """A bare /events beats /about/events-policy. Shorter path wins ties."""
    p = urlparse(u).path.lower().rstrip("/")
    exact = 0 if re.fullmatch(
        r"/(events?|calendar|agenda|shows?|gigs?|programme?|whats-on|kalender|"
        r"calendrier|calendario|veranstaltungen)", p) else 1
    return (exact, len(p))


def _prefer_listing(session, deep_url: str, timeout: int = 18) -> Optional[str]:
    """Walk up from a single event's page to the calendar it sits in.

    A JSON-LD site usually carries its Event blocks on the EVENT pages and
    nothing on the index — the Royal Lyceum's /events/ has three blocks and not
    one Event, while /events/guys-dolls has forty. So the fingerprint lands
    deep, and a config listing one show is a source that dies the day that show
    closes, silently, having only ever imported one production.

    The adapter is built to crawl: give it the index as `listing` and a
    link_pattern for the show pages and it reads the whole programme. So walk up
    one segment and take the parent INSTEAD — but only once it has been fetched
    and actually links to sibling event pages, because a parent that does not is
    a listing of nothing.
    """
    o = urlparse(deep_url)
    parts = [p for p in o.path.split("/") if p]
    if len(parts) < 2:
        return None
    parent = f"{o.scheme}://{o.netloc}/" + "/".join(parts[:-1]) + "/"
    try:
        r = session.get(parent, timeout=timeout, allow_redirects=True)
    except Exception:                                             # noqa: BLE001
        return None
    if r.status_code >= 400 or CHALLENGE_RX.search(r.text[:6000]):
        return None
    sibling = re.compile(re.escape("/" + "/".join(parts[:-1]) + "/") + r"[A-Za-z0-9\-]+")
    if len(set(sibling.findall(r.text))) < 2:
        return None                       # not an index of anything
    return str(r.url)


def find_calendar(session, home_url: str, timeout: int = 18,
                  max_follow: int = 2) -> Dict[str, Any]:
    """Locate the calendar on a venue site and say what runs it.

    status is one of: ok | no-calendar | offsite:<host> | bot-challenge |
    unreachable | http<code>.
    """
    out = {"cal_url": None, "labels": [], "adapter": None, "ics": None,
           "status": None, "offsite": None, "extra": {}}
    try:
        r = session.get(home_url, timeout=timeout, allow_redirects=True)
    except Exception as exc:                                      # noqa: BLE001
        out["status"] = "unreachable"
        out["note"] = type(exc).__name__
        return out
    if CHALLENGE_RX.search(r.text[:6000]):
        out["status"] = "bot-challenge"
        return out
    if r.status_code >= 400:
        out["status"] = f"http{r.status_code}"
        return out

    base, body = str(r.url), r.text
    home_labels, home_ics = fingerprint(body)

    onsite, offsite_hit = [], None
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if not (CAL_LINK_RX.search(href) or CAL_LINK_RX.search(text)):
            continue
        u = urljoin(base, href)
        if not u.startswith(("http://", "https://")):
            continue
        off = _offsite(u)
        if off:
            offsite_hit = offsite_hit or off
        elif _same_host(u, base):
            onsite.append(u)

    for u in sorted(dict.fromkeys(onsite), key=_rank)[:max_follow]:
        try:
            r2 = session.get(u, timeout=timeout, allow_redirects=True)
        except Exception:                                          # noqa: BLE001
            continue
        if CHALLENGE_RX.search(r2.text[:6000]):
            out["status"] = "bot-challenge"
            return out
        if r2.status_code >= 400:
            continue
        labels, ics = fingerprint(r2.text)
        if labels:
            ics = ics or constructed_feed(session, base, labels, cal=str(r2.url))
            m = _VP_IDS_RX.search(r2.text) or _VP_IDS_RX.search(body)
            if m:
                out["extra"]["account_ids"] = [int(x) for x in m.group(1).replace(" ", "").split(",") if x]
            adapter = adapter_for(labels)
            cal_url = str(r2.url)
            if adapter == "jsonld":
                cal_url = _prefer_listing(session, cal_url, timeout) or cal_url
            out.update(cal_url=cal_url, labels=labels, ics=ics,
                       adapter=adapter, status="ok")
            return out
        out["cal_url"] = out["cal_url"] or str(r2.url)

    m = _VP_IDS_RX.search(body)
    if m:
        out["extra"]["account_ids"] = [int(x) for x in m.group(1).replace(" ", "").split(",") if x]
    if home_labels:
        # The platform is visible but no page announced itself as the calendar.
        # Still worth proposing — verification is what decides — but the URL is
        # the weaker one, so say so rather than dress it up as a calendar page.
        out.update(cal_url=out["cal_url"] or base, labels=home_labels,
                   ics=home_ics or constructed_feed(session, base, home_labels),
                   adapter=adapter_for(home_labels), status="ok-homepage")
        return out
    if offsite_hit:
        out.update(status=f"offsite:{offsite_hit}", offsite=offsite_hit)
        return out
    out["status"] = "no-calendar"
    return out


# ---- turning a find into a candidate ----------------------------------------
def _venue_block(v: Dict[str, Any]) -> Dict[str, Any]:
    """The config `venue` block, from the survey. This is what makes a
    single-venue calendar placeable AND time-correct — see the module header."""
    b = {"name": v.get("name")}
    for src, dst in (("street", "address"), ("city", "city"), ("region", "region"),
                     ("postal_code", "postal_code"), ("country", "country")):
        if v.get(src):
            b[dst] = v[src]
    if v.get("lat") is not None:
        b["lat"], b["lon"] = round(float(v["lat"]), 7), round(float(v["lon"]), 7)
    return b


# The adapters to_candidate knows how to write a config entry for. Anything
# adapter_for can NAME but this cannot SHAPE is a find that reaches the end of
# the pipeline and evaporates, so the two lists have to be compared out loud.
SHAPEABLE = {"ics", "tribe", "squarespace", "localist", "jsonld", "gancio",
             "venuepilot"}

# VenuePilot's public GraphQL wants ACCOUNT IDS, and a venue's own embedded
# widget carries them in plain sight — window.venuepilotSettings.general
# .accountIds. Without them the find is unusable, which is why every venuepilot
# detection used to evaporate; with them it is an ordinary config entry.
_VP_IDS_RX = re.compile(r"accountIds\s*:\s*\[([\d,\s]+)\]", re.I)


def why_no_candidate(found: Dict[str, Any]) -> str:
    """Why a successful probe still produced nothing to verify.

    These used to be counted as "ok", which is how 25 finds in one sweep came to
    be reported under the same word as a success. A skip counter that cannot say
    what it skipped is a measurement that reads as coverage.
    """
    adapter, cal = found.get("adapter"), found.get("cal_url")
    if not adapter:
        return "no-adapter(" + ("+".join(found.get("labels") or []) or "nothing") + ")"
    if not cal:
        return "no-listing-url"
    if adapter == "ics" and not found.get("ics"):
        # A platform that HAS a calendar and did not hand over a feed URL: the
        # page linked no .ics and no FEED_TEMPLATE matched or fetched.
        return "ics-without-feed(" + ("+".join(found.get("labels") or []) or "?") + ")"
    if adapter == "venuepilot" and not (found.get("extra") or {}).get("account_ids"):
        return "venuepilot-without-accountIds"
    if adapter not in SHAPEABLE:
        return "no-config-shape(" + adapter + ")"
    return "unknown"


def to_candidate(v: Dict[str, Any], found: Dict[str, Any],
                 metro: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A verify-able candidate in the shape its adapter's config file wants."""
    adapter, cal = found.get("adapter"), found.get("cal_url")
    if not adapter or not cal:
        return None
    cat = KIND_CATEGORY.get(v.get("kind") or "", DEFAULT_CATEGORY)
    name = v.get("name") or urlparse(cal).netloc
    common = {"name": name, "category": cat,
              "_found": f"osm:{v.get('kind')} -> {'+'.join(found.get('labels') or [])}"}
    if adapter == "ics":
        ics = found.get("ics")
        if not ics:
            return None                       # the platform said ics, the site did not serve one
        return dict(common, type="ics", url=_https(urljoin(cal, ics)),
                    geocode_suffix=_suffix(v, metro), limit=300)
    if adapter == "tribe":
        o = urlparse(cal)
        return dict(common, type="tribe", base_url=f"{o.scheme}://{o.netloc}",
                    within_days=120, max_pages=10, venue=_venue_block(v))
    if adapter == "squarespace":
        return dict(common, type="squarespace", collection=cal,
                    max_events=200, venue=_venue_block(v))
    if adapter == "gancio":
        o = urlparse(cal)
        return dict(common, type="gancio", base_url=f"{o.scheme}://{o.netloc}",
                    within_days=180, max=500,
                    default_city=v.get("city") or (metro or "").split(",")[0].strip() or None,
                    default_country=v.get("country"))
    if adapter == "venuepilot":
        ids = (found.get("extra") or {}).get("account_ids")
        if not ids:
            return None                       # no ids = nothing to ask the API for
        return dict(common, type="venuepilot", account_ids=ids,
                    within_days=120, venue=_venue_block(v))
    if adapter == "localist":
        o = urlparse(cal)
        return dict(common, type="localist", base_url=f"{o.scheme}://{o.netloc}", days=90)
    if adapter == "jsonld":
        o = urlparse(cal)
        host = re.escape(o.netloc)
        if "wix" in (found.get("labels") or []):
            # Wix Events does not put its listing in anchors — it ships the whole
            # set as {"slug": ...} and routes each one through /event-info/. A
            # WordPress-shaped /event/<slug>/ pattern matches none of it, which is
            # how four London candidates were proposed with a link_pattern that
            # could never fire. Same shape as the Sea Monster Lounge entry.
            return dict(common, type="jsonld", listing=[cal],
                        link_pattern=r'"slug":"([a-zA-Z0-9-]+)"',
                        url_template="/event-info/{}",
                        max_events=100, venue=_venue_block(v))
        return dict(common, type="jsonld", listing=[cal],
                    link_pattern=rf"https://{host}/(?:event|events)/[a-z0-9\-]+/?",
                    max_events=150, venue=_venue_block(v))
    return None


def _suffix(v: Dict[str, Any], metro: Optional[str] = None) -> str:
    """What to append to an iCal event's LOCATION before geocoding it.

    An ics config carries no coordinates — geocode_suffix is the ONLY thing
    placing its events — so an empty one is not a harmless default, it hands the
    geocoder a bare venue name and lets it land anywhere on earth that shares it.
    Erith Yacht Club has no addr:city in OSM and would have shipped exactly that.
    The metro being swept is always known, so it is the floor.
    """
    bits = [x for x in (v.get("city"), v.get("region")) if x]
    if not bits and metro:
        bits = [p.strip() for p in metro.split(",") if p.strip()]
    return (", " + ", ".join(bits)) if bits else ""


# ---- the metro walk ----------------------------------------------------------
def _bbox_from(latlong: str, radius_km: float) -> str:
    lat, lon = (float(x) for x in latlong.split(","))
    dlat = radius_km / 111.0
    import math
    dlon = radius_km / max(1e-6, 111.0 * math.cos(math.radians(lat)))
    return f"{lat - dlat:.4f},{lon - dlon:.4f},{lat + dlat:.4f},{lon + dlon:.4f}"


def metros(path_global: str = "metros_global.json",
           path_us: str = "metros_us.txt") -> List[Dict[str, Any]]:
    """Every metro this repo already sweeps, as (name, country, bbox).

    Ordered international-first so a cursor that has only ever run a few times
    has spent its budget where the catalog is thinnest — the US is the part
    already covered by the ticketing APIs.
    """
    out: List[Dict[str, Any]] = []
    p = os.path.join(HERE, path_global)
    if os.path.exists(p):
        for c in json.load(open(p, encoding="utf-8")).get("countries", []):
            for m in c.get("metros", []):
                if not m.get("latlong"):
                    continue
                out.append({"name": m["name"], "country": c.get("code", "??"),
                            "bbox": _bbox_from(m["latlong"], float(m.get("radius") or 25))})
    p = os.path.join(HERE, path_us)
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            latlong, _, name = line.partition("#")
            latlong = latlong.strip()
            if not re.match(r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$", latlong):
                continue
            out.append({"name": (name.strip() or latlong), "country": "US",
                        "bbox": _bbox_from(latlong, 25.0)})
    return out
