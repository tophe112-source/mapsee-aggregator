#!/usr/bin/env python3
"""
mapsee_ingest_osm_amenities.py — the civic things a neighbourhood already has.

READ mapsee_ingest_osm_food.py's header FIRST, then its sibling
mapsee_ingest_osm_secondhand.py. This is the third adapter that imports PLACES
from OpenStreetMap rather than events somebody published, and everything those
say about ODbL, hubs, tiling, Overpass manners and never inventing a city
applies here unchanged. The machinery is imported from them, not copied.

WHAT IS NEW HERE, AND IT IS THE ONLY INTERESTING THING IN THE FILE: most of
these places have NOTHING WORTH READING. A drinking fountain is a drinking
fountain. Its pin already says so, and opening a sheet that repeats the pin's
own icon back at you is worse than not opening one — it costs a tap, takes the
bottom of the screen, pushes a history entry, and answers a question nobody
asked. So this adapter sorts its own output into two kinds:

    A LISTING   carries at least one fact a person could act on that they
                cannot already see from the map: opening hours, an operator or
                charity, a fee or access rule, an accessibility note, a website,
                a real description. It behaves like any other pin.

    FURNITURE   is everything else. It is DRAWN and it is not tappable, not in
                the Nearby list, and not in a sitemap. `pin_only` on the row
                says so, and ../mapsee 0194 is the other half.

ONE SELECTOR IS EXEMPT, AND THE EXEMPTION IS THE STAKES. A food bank is always
a listing, tagged or bare, because WHERE ONE IS is itself the actionable fact —
it is the thing somebody came to the map looking for, and whether it can be
opened, named, routed to and shared must not depend on whether a mapper filled
in a phone number. That is not true of a fountain, whose pin already carries
the whole of what its row knows. See `Kind.always_list`, and note it is
deliberately one selector: widening it to give boxes and bike stands would undo
the split above by degrees, and the argument does not carry to them.

That split is the whole design. The alternative — put every playground in the
events table as a listing — makes the map louder and the product worse: it
buries the concert three streets away under four hundred benches, and it fills
the Nearby list, which is a list of WHAT IS ON, with things that are simply
there. `is_standing` (0158) already demotes standing rows below occasions; that
is right for a restaurant, which is a listing somebody may want to open, and
insufficient for a bin.

WHY THESE EIGHT SELECTORS. Measured against taginfo on 2026-08-26 (global, all
element types), chosen because each one answers a question a person actually
asks a map, and each is free at the point of use:

    leisure=playground            1,006,477    kids
    tourism=artwork                 366,557    arts
    amenity=drinking_water          365,393    outdoors
    leisure=fitness_station         100,841    fitness   (outdoor gyms)
    amenity=public_bookcase          46,908    learning  (little free libraries)
    amenity=bicycle_repair_station   23,216    outdoors  (free pumps and tools)
    social_facility=food_bank         4,938    volunteer
    amenity=give_box                  1,478    community (free/community fridges)

NOTE `social_facility=food_bank` AND NOT `amenity=food_bank`. The latter exists
and has 16 uses worldwide; the former has 4,938. Reaching for the obvious key
would have produced an adapter that ran clean, reported success and imported
essentially nothing — the same shape as the parkrun config that was never
committed while the job printed a friendly skip every night.

NO NEW CATEGORY KEY. Every row lands on a key that already exists in
MAPSEE_CATEGORY_KEYS and that a lens already opens onto, for the reason
osm_secondhand gives: a key no lens opens onto reaches only mapsee.me.

HOURS ARE OPTIONAL HERE, AND THAT IS THE ONE BAR THAT MOVED. The food adapter
requires an order link; the second-hand adapter requires readable opening hours,
on the argument that hours ARE the transaction for a shop you walk into. A
playground has no hours because it never closes, and refusing it for that would
throw away a million surveyed places to enforce a rule that does not apply.
Where hours ARE present they are parsed with the same parser and the same
refusals; where they are absent the row is written open-all-week, which is what
`opening_hours=24/7` means and what an untagged playground almost always is.

Refusing an UNREADABLE hours string still matters most of all rules, for the
food adapter's reason: ignoring "shut" advertises a place as open. An amenity
whose hours we cannot parse carries NO hours claim — no weekly pattern, and
nothing in the body that reads as one.

For a food bank, which is a listing either way, that leaves three different
silences and they want three different sentences: `24/7` (which the other kinds
deliberately do not print, because "never closes" is what a fountain's pin
already implies, and which for a food bank is the best news on the row); a
string tagged that our parser refused, quoted verbatim and attributed, because
"we cannot ACT on this" is not "a person cannot READ it"; and nothing tagged at
all, said out loud rather than left as a silence a reader fills in with "open,
presumably". `hours_unknown_line`. Never a guessed time — parkrun's rule, and
for the same reason.

ATTRIBUTION. ODbL, on every row, in the same words the other two use, because
mapsee_retire_perday_osm.py keys on that line.

Env:  none (Overpass is public; be polite with --delay)
Run:  python mapsee_ingest_osm_amenities.py --config osm_amenity_sources.json \
          --store feeds_events.json [--dry-run] [--only london] [--warm-cache]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from mapsee_ingest import EventStore, NormalizedEvent
from mapsee_menu_links import NOT_A_VENUE_SITE
# Imported, not copied — see osm_secondhand's note. parse_opening_hours is 80
# lines of refusals each bought with a live failure, and only test_osm_food.py
# guards the original.
from mapsee_ingest_osm_food import (
    parse_opening_hours, area_bbox, tiles, cache_path,
    load_cursor, save_cursor, clean_public_phone, window_at, sweep_tiles,
)

UA = "mapsee-aggregator/1.0 (+https://mapsee.me; OSM civic amenity discovery)"
OVERPASS = "https://overpass-api.de/api/interpreter"
CURSOR_PATH = "osm_amenity_cursor.json"

# Sparser than second-hand shops in most boxes and far denser in a few
# (playgrounds are everywhere). 1.0 splits the difference: a 50-mile hub is ~6
# cells, and a failed cell costs a sixth of a metro rather than all of it.
TILE_DEG = 1.0

# A week of "always". What an untagged playground is, and what 24/7 means.
ALWAYS = {str(d): [["00:00", "23:59"]] for d in range(7)}


class Kind:
    """One selector, and everything that follows from it."""

    def __init__(self, key, value, category, noun, glyph, secondary=(),
                 always_open=True, always_list=False):
        self.key, self.value = key, value
        self.category, self.noun, self.glyph = category, noun, glyph
        self.secondary = list(secondary)
        # IS "NO HOURS TAGGED" THE SAME AS "NEVER CLOSES"?
        #
        # For a playground, a fountain or a bike stand: yes. They are street
        # furniture, they are 24/7, and the overwhelming majority are untagged
        # simply because there is nothing to say.
        #
        # For a food bank: emphatically no, and getting it wrong is the food
        # adapter's worst failure wearing a different hat — ignoring "shut"
        # advertises a place as open, and here the person turned away at a
        # locked door came for food. So a food bank with no readable hours
        # gets NO weekly pattern and makes no claim about being open.
        self.always_open = always_open
        # IS "NOTHING TAGGED" THE SAME AS "NOTHING WORTH OPENING"?
        #
        # For every other selector here: yes, and that is the whole of 0194. A
        # drinking fountain's pin already carries everything the row knows, so
        # a sheet costs a tap to be told what the icon said.
        #
        # For a food bank: no. WHERE ONE IS is itself the actionable fact —
        # it is the thing somebody is looking for, it is worth a name, a
        # walking route and a share link, and the answer to "can I click it"
        # must not depend on whether a mapper happened to fill in a phone
        # number. Whether it is OPEN is a second question, answered honestly
        # below (parkrun's rule: an all-day window and a sentence saying the
        # real time is not in the feed, never a guess).
        #
        # Deliberately ONE selector. Widening this to give boxes and bike
        # stands would undo 0194 by degrees; the argument here is the stakes,
        # and they are not the same stakes.
        self.always_list = always_list

    @property
    def slug(self):
        return f"{self.key}={self.value}"


KINDS = [
    Kind("leisure", "playground", "kids", "playground", "🛝", ["outdoors"]),
    Kind("tourism", "artwork", "arts", "public artwork", "🎨"),
    Kind("amenity", "drinking_water", "outdoors", "drinking water point", "🚰"),
    Kind("leisure", "fitness_station", "fitness", "outdoor gym", "🏋", ["outdoors"]),
    Kind("amenity", "public_bookcase", "learning", "little free library", "📚", ["community"]),
    Kind("amenity", "bicycle_repair_station", "outdoors", "bike repair stand", "🔧"),
    Kind("social_facility", "food_bank", "volunteer", "food bank", "🥫", ["community"],
         always_open=False, always_list=True),
    Kind("amenity", "give_box", "community", "give box", "🎁", ["market"]),
]
BY_SLUG = {k.slug: k for k in KINDS}


def kind_of(tags: Dict[str, str]) -> Optional[Kind]:
    """Which selector this element matched, or None.

    Defence in depth, exactly as osm_secondhand's `wanted` is: the Overpass
    query already filters server-side, so this should never reject anything.
    It is here because the query is a STRING built from the same table, and
    this is the half a test can reach.
    """
    for kind in KINDS:
        if str(tags.get(kind.key) or "").strip().lower() == kind.value:
            return kind
    return None


def selector(s, w, n, e) -> str:
    """The Overpass union for one tile.

    No ["name"] filter, unlike the other two adapters. A drinking fountain does
    not have a name and requiring one would delete the category — which is the
    same reasoning that put `pin_only` in this file rather than a name test.
    """
    return "".join(f'nwr["{k.key}"="{k.value}"]({s},{w},{n},{e});' for k in KINDS)


# ---------------------------------------------------------------------------
# IS THERE ANYTHING WORTH READING HERE?
#
# The one judgement in this file. Everything below answers a single question:
# does this element carry a fact a person could ACT on that they cannot already
# see from the pin? If yes it is a listing; if no it is furniture.
#
# The bar is deliberately "a fact", not "a string". A name is not a fact — a
# little free library called "Sarah's Book Box" tells you nothing you did not
# already know from a book icon on a corner, and a sheet that says only that is
# the app taking the bottom of the screen to say nothing. An OPERATOR, a set of
# HOURS, a FEE, an ACCESS rule, an ACCESSIBILITY note, a WEBSITE, a real
# DESCRIPTION or, for a sculpture, its ARTIST — those change what somebody does
# next, and each one is worth the tap it costs.
# ---------------------------------------------------------------------------
_YES = ("yes", "true", "1")

# A VALUE THAT MATCHES THE ASSUMPTION IS NOT A FACT.
#
# This is the same rule as "a name is not a fact", one level down, and it was
# missed on the first pass. `access=yes` is the commonest tag on a playground
# and `fee=no` on a drinking fountain — and a sheet whose entire content is
#
#     Cal Anderson — playground.
#     🚪 Access: Open to everyone
#
# has charged somebody a tap to be told what the pin already implied. Free and
# public is what a civic amenity IS; only the DEVIATION is worth a sheet.
# "This playground is private" and "there is a charge" change whether you walk
# over, so those still count and still print.
#
# Deliberately NOT extended to `wheelchair`. Accessibility is not something
# anybody may assume either way, so all three of its values are real facts.
_ACCESS_ASSUMED = {"yes", "public", "permissive"}
_FEE_ASSUMED = {"no", "free"}

_ACCESS = {
    "customers": "Customers only", "private": "Private",
    "permit": "Permit holders only", "no": "No public access",
}
_WHEELCHAIR = {"yes": "Wheelchair accessible", "limited": "Limited wheelchair access",
               "no": "Not wheelchair accessible"}

# What a bike stand actually has. The whole reason to walk to one.
_BIKE_SERVICE = [
    ("service:bicycle:pump", "pump"),
    ("service:bicycle:tools", "tools"),
    ("service:bicycle:stand", "repair stand"),
    ("service:bicycle:chain_tool", "chain tool"),
]


def _clean(value: Any, limit: int = 300) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text in {"-", "--", "n/a", "N/A", "?"}:
        # The WP Event Manager lesson: a placeholder is TRUTHY and survives every
        # `if not x` gate, then reaches the reader as if it were content.
        return None
    return text[:limit]


def own_website(tags: Dict[str, str]) -> Optional[str]:
    site = tags.get("website") or tags.get("contact:website")
    if not site:
        return None
    if not str(site).startswith("http"):
        site = "https://" + str(site)
    try:
        host = urllib.parse.urlparse(site).hostname or ""
    except ValueError:
        return None
    return site if host and not NOT_A_VENUE_SITE.search(host) else None


def own_image(tags: Dict[str, str]) -> Optional[str]:
    """A photograph of this thing, or None.

    A PICTURE IS CONTENT ON ITS OWN, and for the two kinds where OSM actually
    carries one it is the best content there is: a sculpture you can see before
    you walk to it, a playground a parent can size up. So an image alone
    promotes a row out of furniture.

    Two sources, and Commons is the one that matters. `wikimedia_commons` names
    a file rather than a URL, and Special:FilePath is the documented redirect to
    the image itself — building it is not a guess. A bare `image` tag may point
    anywhere, so only plain https is accepted: an http URL would be blocked as
    mixed content on the way into the app, which renders as a broken image
    rather than as no image.
    """
    commons = _clean(tags.get("wikimedia_commons"), 200)
    if commons and commons.lower().startswith("file:"):
        name = urllib.parse.quote(commons[5:].strip().replace(" ", "_"), safe="")
        if name:
            return f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}"
    raw = _clean(tags.get("image"), 400)
    if raw and raw.startswith("https://"):
        return raw
    return None


def hours_unknown_line(tags: Dict[str, str]) -> str:
    """What to say to somebody who may travel to this on an empty stomach.

    Only reached for an `always_list` kind, i.e. a food bank, and only when
    `read_hours` produced no summary — which is three different situations
    that want three different sentences:

    * `24/7` parses, and `read_hours` deliberately prints nothing for it
      because "never closes" is what a fountain's pin already implies. For a
      food bank it is not implied and it is the best news on the row.
    * A string is tagged and our parser refused it. The refusals are the
      valuable part of that parser — an unreadable rule must never become an
      open-all-week claim — but "we cannot ACT on this" is not "a person
      cannot READ it", and `Mo-Fr 09:00-17:00; PH off` is perfectly plain to
      the person standing outside. Quote it, say where it came from, and make
      no claim of our own.
    * Nothing is tagged at all. Say that, rather than leaving a silence a
      reader would fill in with "open, presumably".

    Never a guessed time. parkrun's rule, and for the same reason: the number
    varies, nobody downstream can tell an invented one from a surveyed one,
    and being wrong sends somebody to a locked door.
    """
    raw = (tags.get("opening_hours") or "").strip()
    if raw in ("24/7", "24 hours", "24hrs"):
        return "🕑 Open at all hours."
    if raw:
        return "🕑 Hours, as tagged in OpenStreetMap: " + _clean(raw, 120)
    return "🕑 Opening times are not listed — check before travelling."


def useful_lines(tags: Dict[str, str], kind: Kind,
                 hours_text: Optional[str] = None) -> List[str]:
    """Every actionable fact, as the product's emoji-marker detail lines.

    The emoji prefixes are the ones ../mapsee already parses (see
    business_detail_lines in the food adapter) — inventing a new one renders it
    as prose instead of as a detail row.
    """
    lines: List[str] = []

    operator = _clean(tags.get("operator") or tags.get("brand"), 120)
    if operator:
        lines.append(f"🏛 Run by: {operator}")

    if hours_text:
        lines.append(f"🕑 Open: {hours_text}")
    elif kind.always_list:
        lines.append(hours_unknown_line(tags))

    fee = str(tags.get("fee") or "").strip().lower()
    charge = _clean(tags.get("charge"), 80)
    if fee in _YES:                       # only a CHARGE is news; free is assumed
        lines.append(f"🎟 Charge: {charge}" if charge else "🎟 There is a charge.")

    # _ACCESS holds only the restrictive values now — see _ACCESS_ASSUMED.
    access = _ACCESS.get(str(tags.get("access") or "").strip().lower())
    if access:
        lines.append(f"🚪 Access: {access}")

    chair = _WHEELCHAIR.get(str(tags.get("wheelchair") or "").strip().lower())
    if chair:
        lines.append(f"♿ Accessibility: {chair}")

    # ---- what this particular KIND of thing is asked about -----------------
    if kind.slug == "leisure=playground":
        ages = []
        for key, label in (("min_age", "from"), ("max_age", "to")):
            val = _clean(tags.get(key), 12)
            if val:
                ages.append(f"{label} {val}")
        if ages:
            lines.append("👶 Ages " + " ".join(ages))
        extras = []
        if str(tags.get("fenced") or "").lower() in _YES:
            extras.append("fenced")
        if str(tags.get("lit") or "").lower() in _YES:
            extras.append("lit after dark")
        surface = _clean(tags.get("surface"), 40)
        if surface:
            extras.append(surface.replace("_", " "))
        if extras:
            lines.append("🛝 " + ", ".join(extras).capitalize())

    elif kind.slug == "tourism=artwork":
        artist = _clean(tags.get("artist_name") or tags.get("artist"), 120)
        if artist:
            lines.append(f"🎨 Artist: {artist}")
        art_type = _clean(tags.get("artwork_type"), 60)
        if art_type:
            lines.append(f"🗿 Type: {art_type.replace('_', ' ')}")
        inscription = _clean(tags.get("inscription"), 300)
        if inscription:
            lines.append(f"📝 Inscription: “{inscription}”")

    elif kind.slug == "amenity=drinking_water":
        if str(tags.get("bottle") or "").lower() in _YES:
            lines.append("🍶 Bottle refill.")
        seasonal = str(tags.get("seasonal") or "").strip().lower()
        if seasonal and seasonal not in ("no", "false"):
            # A fountain turned off for the winter is the one thing worth
            # warning about, and the reason is the food adapter's: a pin that
            # says "open" about a thing that is off is worse than no pin.
            lines.append("❄️ Seasonal — may be turned off out of season.")

    elif kind.slug == "leisure=fitness_station":
        equipment = _clean(tags.get("fitness_station"), 120)
        if equipment and equipment.lower() not in _YES:
            lines.append("🏋 Equipment: " + equipment.replace("_", " ").replace(";", ", "))

    elif kind.slug == "amenity=public_bookcase":
        capacity = _clean(tags.get("capacity"), 20)
        if capacity:
            lines.append(f"📚 Holds about {capacity} books.")
        books = _clean(tags.get("books"), 80)
        if books:
            lines.append("📖 Mostly: " + books.replace("_", " ").replace(";", ", "))

    elif kind.slug == "amenity=bicycle_repair_station":
        have = [label for tag, label in _BIKE_SERVICE
                if str(tags.get(tag) or "").lower() in _YES]
        if have:
            lines.append("🔧 Has: " + ", ".join(have))

    elif kind.slug == "social_facility=food_bank":
        who = _clean(tags.get("social_facility:for"), 120)
        if who:
            lines.append("🤝 For: " + who.replace("_", " ").replace(";", ", "))

    phone = clean_public_phone(tags.get("phone") or tags.get("contact:phone"))
    if phone:
        lines.append(f"☎ Phone: {phone}")
    site = own_website(tags)
    if site:
        lines.append(f"🌐 Website: {site}")

    body = _clean(tags.get("description"), 400)
    if body:
        lines.insert(0, body)
    return lines


def has_content(tags: Dict[str, str], kind: Kind,
                hours_text: Optional[str] = None) -> bool:
    """Does this element earn a sheet? A FACT or a PICTURE, never a name.

    The client re-applies its own version of this test against what actually
    reaches it (see ../mapsee's amenityHasContent), the way the product
    re-validates every order link this repo writes. A disagreement therefore
    fails safe as a pin that draws and does not open.
    """
    return bool(useful_lines(tags, kind, hours_text)) or bool(own_image(tags))


# ---------------------------------------------------------------------------
# Hours
# ---------------------------------------------------------------------------
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def read_hours(tags: Dict[str, str], kind: Kind) -> Tuple[Optional[dict], Optional[str], str]:
    """(recurring_days, human summary, verdict).

    Verdict is one of `always` / `parsed` / `unreadable` / `absent`, and the
    caller uses it for exactly one thing: an UNREADABLE string must never
    become an open-all-week claim. The parser's refusals are the valuable part
    of it (see test_osm_food.py's `off` rule, which crashed the first real run
    and whose fix matters most of all rules).
    """
    raw = (tags.get("opening_hours") or "").strip()
    if not raw:
        return (dict(ALWAYS), None, "absent") if kind.always_open else (None, None, "absent")
    if raw in ("24/7", "24 hours", "24hrs"):
        # True and not worth a line: "open all the time" is what the pin
        # already implies for this kind of thing.
        return dict(ALWAYS), None, "always"
    try:
        parsed = parse_opening_hours(raw)
    except Exception:                                   # noqa: BLE001
        parsed = None
    if not parsed:
        return (dict(ALWAYS), None, "unreadable") if kind.always_open else (None, None, "unreadable")
    days = {str(d): [[o, c] for o, c in spans] for d, spans in sorted(parsed.items())}
    return days, summarise_hours(parsed), "parsed"


def summarise_hours(parsed: Dict[int, List[Tuple[str, str]]], limit: int = 120) -> Optional[str]:
    """"Mon-Fri 09:00-17:00, Sat 10:00-14:00" — the line a person reads.

    Consecutive days sharing an identical set of windows collapse into a range,
    because seven lines of the same thing is not a summary.
    """
    if not parsed:
        return None
    groups: List[Tuple[int, int, str]] = []
    for day in range(7):
        spans = parsed.get(day)
        if not spans:
            continue
        text = ", ".join(f"{o}-{c}" for o, c in spans)
        if groups and groups[-1][2] == text and groups[-1][1] == day - 1:
            groups[-1] = (groups[-1][0], day, text)
        else:
            groups.append((day, day, text))
    out = []
    for first, last, text in groups:
        label = _DAY_NAMES[first] if first == last else f"{_DAY_NAMES[first]}-{_DAY_NAMES[last]}"
        out.append(f"{label} {text}")
    joined = "; ".join(out)
    return joined[:limit] if joined else None


# ---------------------------------------------------------------------------
# One element -> one standing row
# ---------------------------------------------------------------------------
def to_event(el: dict, area: dict, days_ahead: int = 7) -> Optional[NormalizedEvent]:
    tags = el.get("tags") or {}
    kind = kind_of(tags)
    if not kind:
        return None
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None

    days, hours_text, verdict = read_hours(tags, kind)
    if days is None:
        # A food bank whose hours we cannot read. The all-week window here is
        # NOT a claim — it exists so ../mapsee 0156's roller keeps the single
        # row alive and dated, the way parkrun emits an all-day event rather
        # than inventing a start time. What tells the reader the truth is the
        # line `hours_unknown_line` puts in the body, and that line is why
        # this row is worth opening at all.
        days, hours_text = dict(ALWAYS), None

    # IS THERE A TIME THIS IS SHUT? `read_hours` hands back the all-week window
    # for three different silences — 24/7, nothing tagged on an always-open
    # kind, and a food bank whose hours we refused to guess — and none of them
    # is a schedule anybody could miss. Compared against ALWAYS rather than
    # against the verdict, because a rule that parses cleanly to
    # "Mo-Su 00:00-24:00" is 24/7 written the long way.
    shuts = days != ALWAYS

    facts = useful_lines(tags, kind, hours_text)
    image = own_image(tags)
    name = _clean(tags.get("name"), 120)
    title = name or kind.noun.capitalize()

    town = _clean(tags.get("addr:city") or tags.get("addr:suburb"), 80)
    street = " ".join(x for x in [tags.get("addr:housenumber"),
                                  tags.get("addr:street")] if x).strip() or None

    # "Food bank — food bank." is what naming a thing after its own kind
    # produces, and it only started showing when food banks became listings:
    # every unnamed row before this was furniture, so nobody ever read one.
    where = f" in {town}" if town else ""
    head = (f"{title} — {kind.noun}{where}." if name else f"{title}{where}.")

    if facts or image:
        # A LISTING. Same description shape the other two OSM adapters write.
        body = ("\n".join(facts) + "\n\n") if facts else ""
        description = (head + "\n\n" + body +
                       "Public details from OpenStreetMap contributors (ODbL). "
                       "Details can change; anyone can correct them on OpenStreetMap.")
    else:
        # FURNITURE. The description is still written — a claimed or promoted
        # row could one day need it, and ../mapsee 0194 hides the row rather
        # than relying on it being empty — but nothing will render it.
        description = (head + "\n\n"
                       "Public details from OpenStreetMap contributors (ODbL).")

    osm_ref = f"{el.get('type', 'n')}/{el.get('id')}"
    today = datetime.now(timezone.utc).date()
    first = None
    for offset in range(max(days_ahead, 8)):        # 8 sees every weekday once
        day = today + timedelta(days=offset)
        spans = days.get(str(day.weekday()))
        if spans:
            first = (day, spans[0][0], spans[0][1])
            break
    if not first:
        return None
    day, opens, closes = first

    return NormalizedEvent(
        source=f"osm-amenity:{kind.value}",
        source_id=osm_ref,
        fingerprint=hashlib.sha1(f"osm-amenity|{osm_ref}".encode("utf-8")).hexdigest(),
        name=title,
        description=description,
        start_local=f"{day.isoformat()}T{opens}:00",
        end_local=f"{day.isoformat()}T{closes}:00",
        venue_name=name or None,
        latitude=float(lat), longitude=float(lon),
        address=street,
        # OSM's own town or nothing — never the hub's name. The food adapter
        # shipped that bug in both the field and the prose and moved pins
        # twenty-seven miles for it.
        city=town,
        region=area.get("region"),
        country=area.get("country"),
        postal_code=_clean(tags.get("addr:postcode"), 20),
        category=kind.category,
        categories=list(kind.secondary),
        ticket_url=own_website(tags),
        poster_image_url=image,
        coords_exact=True,
        recurring_days=days,
        # THE WHOLE POINT OF THIS ADAPTER, AND THE QUESTION IS "CAN IT BE
        # SHUT?" — not "does it carry a fact".
        #
        # Nearby is a list of WHAT IS ON. A thing that is always there is not
        # on, however much is written about it: a playground tagged with its
        # operator, its surface and "lit after dark" is a well-described
        # playground, and it is open at 3am tomorrow exactly as it is now.
        # Measured 2026-08-26 in one Seattle box: 752 rows from this adapter
        # were in the Nearby list and 745 of them were open 24/7 — 58% of
        # everything under `kids` and 67% under `arts`, several of them titled
        # simply "Playground". That is the list the product exists for, filled
        # with things that never change.
        #
        # So a row earns a Nearby listing only if there is a time it is SHUT,
        # or if it is a food bank — the one selector where WHERE IT IS is the
        # fact somebody came for, and the stakes are the argument.
        #
        # Everything else is a PIN, and what it carries decides what the pin
        # DOES rather than whether the row is a listing: ../mapsee's
        # amenityHasContent reads the description this function wrote and gives
        # a pin with something to say a hover label and a tap that opens its
        # sheet. The two judgements are no longer the same question asked
        # twice — this one is "listing or scenery", that one is "is this
        # scenery worth opening".
        pin_only=not (shuts or kind.always_list),
    )


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------
def _overpass_one(bbox, delay=2.0, tries=4):
    """One tile. Backs off rather than re-asking harder, and RAISES when it
    finally cannot answer — sweep_tiles is what turns that into a lost cell and
    a second pass, and returning [] instead would report an unread tile as an
    empty one. That conflation cost nine metros for 78 runs.
    """
    query = f"[out:json][timeout:180];({selector(*bbox)});out tags center;"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                OVERPASS, data=urllib.parse.urlencode({"data": query}).encode(),
                headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            time.sleep(delay)
            return data.get("elements", [])
        except Exception as exc:                     # noqa: BLE001
            last = exc
            if attempt < tries - 1:
                time.sleep(delay * (3 ** attempt))
    raise last


def overpass(area, delay=2.0, tries=4):
    """(elements, complete), exactly as osm_food's does.

    `complete` is what decides whether the answer may be CACHED. A partial
    answer is fine to use and never fine to keep for thirty days: doing so
    under-covers a corner of the metro while every later run reports itself
    healthy.
    """
    cells = tiles(area_bbox(area), max_deg=TILE_DEG)
    return sweep_tiles(cells, lambda c: _overpass_one(c, delay=delay, tries=tries),
                       f"[osm-amenity] {area['name']}", delay=delay)


def load_places(cache_dir, area, max_age_days, bbox=None):
    try:
        with open(cache_path(cache_dir, area["name"]), encoding="utf-8") as fh:
            blob = json.load(fh)
        if bbox and list(blob.get("bbox") or []) != list(bbox):
            return None, False
        age = (time.time() - float(blob.get("fetched_at", 0))) / 86400.0
        if age <= max_age_days and blob.get("elements"):
            print(f"[osm-amenity] {area['name']}: {len(blob['elements'])} element(s) "
                  f"from cache ({age:.1f}d old)", flush=True)
            return blob["elements"], True
    except Exception:                                # noqa: BLE001
        pass
    return None, False


def save_places(cache_dir, area, elements, bbox):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path(cache_dir, area["name"]), "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "bbox": list(bbox),
                       "elements": elements}, fh)
    except Exception as exc:                         # noqa: BLE001
        print(f"[osm-amenity] could not cache {area['name']}: {exc}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Import civic amenities from OpenStreetMap as standing map pins.")
    ap.add_argument("--config", default="osm_amenity_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--only", help="one area by name (substring)")
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--max-places", type=int, default=1200,
                    help="per area, per run. Nothing here fetches a third-party "
                         "website, so this bounds run time rather than politeness.")
    ap.add_argument("--kinds", help="comma-separated slugs (leisure=playground,…) to "
                                    "restrict this run to")
    ap.add_argument("--listings-only", action="store_true",
                    help="skip furniture entirely — only elements carrying a fact "
                         "worth reading. Useful for measuring the split.")
    ap.add_argument("--ignore-cursor", action="store_true")
    ap.add_argument("--places-cache", default="osm_amenity_cache")
    ap.add_argument("--cache-days", type=float, default=30.0)
    ap.add_argument("--radius-miles", type=float)
    ap.add_argument("--warm-cache", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    areas = [x for x in cfg.get("areas", [])
             if not a.only or a.only.lower() in str(x.get("name", "")).lower()]
    if a.radius_miles:
        areas = [dict(x, radius_miles=a.radius_miles) for x in areas]
    wanted_kinds = None
    if a.kinds:
        wanted_kinds = {s.strip() for s in a.kinds.split(",") if s.strip()}
        unknown = wanted_kinds - set(BY_SLUG)
        if unknown:
            sys.exit(f"unknown --kinds: {', '.join(sorted(unknown))}. "
                     f"Known: {', '.join(sorted(BY_SLUG))}")

    if a.warm_cache:
        for area in areas:
            bbox = area_bbox(area)
            cached, _ = load_places(a.places_cache, area, a.cache_days, bbox)
            if cached is not None:
                continue
            elements, complete = overpass(area, a.delay)
            if not complete:
                print(f"[osm-amenity] {area['name']}: INCOMPLETE, not cached", flush=True)
                continue
            save_places(a.places_cache, area, elements, bbox)
        return 0

    store = None if a.dry_run else EventStore(a.store)
    cursor = {} if a.ignore_cursor else load_cursor(CURSOR_PATH)
    listings = furniture = 0

    for area in areas:
        bbox = area_bbox(area)
        elements, from_cache = load_places(a.places_cache, area, a.cache_days, bbox)
        if elements is None:
            elements, complete = overpass(area, a.delay)
            if not elements and not complete:
                # UNREAD, not empty. The cursor must not move past a box nobody
                # managed to read at all — that conflation lost nine metros for
                # 78 runs. A PARTIAL answer is still used (below); it is simply
                # never cached.
                print(f"[osm-amenity] {area['name']}: UNREAD this run, cursor held",
                      flush=True)
                continue
            if complete:
                save_places(a.places_cache, area, elements, bbox)

        candidates = [el for el in elements
                      if kind_of(el.get("tags") or {})
                      and (not wanted_kinds or kind_of(el["tags"]).slug in wanted_kinds)]
        # window_at returns a LIST, and the caller owns the next cursor —
        # osm_secondhand's `(start + len(window)) % n`. Unpacking it as a pair
        # is a ValueError at runtime and nothing static catches it.
        start = int(cursor.get(area["name"], 0))
        window = window_at(candidates, start, a.max_places)
        next_start = ((start + len(window)) % len(candidates)) if candidates else 0
        area_listings = area_furniture = 0
        for el in window:
            event = to_event(el, area, a.days_ahead)
            if not event:
                continue
            if event.pin_only:
                if a.listings_only:
                    continue
                area_furniture += 1
            else:
                area_listings += 1
            if store is not None:
                store.upsert(event)
        listings += area_listings
        furniture += area_furniture
        # THE SPLIT IS THE HEADLINE, so it is printed per area. A run whose
        # furniture count is the whole of it is a run that added pins and no
        # listings, which is fine and worth being able to see.
        print(f"[osm-amenity] {area['name']}: {len(candidates)} candidate(s), "
              f"{area_listings} listing(s) + {area_furniture} furniture from "
              f"{len(window)} examined", flush=True)
        if not a.ignore_cursor:
            cursor[area["name"]] = next_start

    if store is not None:
        store.save()
        if not a.ignore_cursor:
            save_cursor(cursor, CURSOR_PATH)
    print(f"[osm-amenity] {'dry run — ' if a.dry_run else ''}"
          f"{listings} listing(s) + {furniture} furniture pin(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
