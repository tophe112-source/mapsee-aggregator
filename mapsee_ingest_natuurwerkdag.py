#!/usr/bin/env python3
"""
mapsee_ingest_natuurwerkdag.py - import the volunteer jobs ("klussen") of the
Natuurwerkdag (https://www.natuurwerkdag.nl), the Netherlands' national
volunteer nature day, into the Mapsee store.

    python mapsee_ingest_natuurwerkdag.py --config natuurwerkdag_sources.json \
        --store feeds_events.json [--cache natuurwerkdag_cache.json] \
        [--max-minutes 12] [--dry-run]

WHAT THE SOURCE IS
------------------
One weekend a year (2026: Friday 6 and Saturday 7 November) LandschappenNL
and the twelve provincial landscape-management organisations open ~500 sites
to volunteers: pollarding willows, clearing heath, cleaning ponds, planting
bulbs. Every job has a day, a time window, a meeting place, a point on the
site's own map and a sign-up form on its own page. Until this adapter the
catalog held no Dutch volunteer source at all.

CONDUCT (checked 2026-10-24 with our own User-Agent)
----------------------------------------------------
robots.txt is "User-agent: * / Allow: /" with no Crawl-delay. The site has no
terms of use: its disclaimer (/disclaimer/) only disclaims accuracy and
liability, and its privacy statement covers participants' data. So this reads
the public pages at one request a second and copies NO person: the organiser's
e-mail sits in a `data-email` attribute and the "quote" block names a site
manager - neither is read - and any description sentence that gives an e-mail
address, a phone number or a named contact is dropped (1 of 22 sampled pages
carried "Contact: <name>, 06-..."). The job page's own question form is the way
to reach an organiser, and it is the row's link.

THE LISTING IS THE AUTHORITY, NOT THE SITEMAP
---------------------------------------------
/klussen/ renders EVERY open job in one page (2 MB, FacetWP with per_page -1):
530 cards on 2026-10-24, and a FWP_JSON map block holding 523 markers whose
point is the job page's own `data-lat`/`data-lng` (15 of 15 sampled agree to
the last digit). jobs-sitemap.xml lists 551 URLs: the 21 extra are pages the
finder does not show - one is called "test Marije" - and every one of them
renders an empty shell whose only date belongs to ANOTHER job, in the "Meer
in de buurt" rail (seattlecenter's related-events trap). So which jobs exist,
and when and where, is read from the listing alone; the sitemap is used only
for its <lastmod>, and a detail page is only ever read for a job the listing
shows, and only inside its hero, summary, content and form blocks.

WHAT A DETAIL PAGE ADDS, AND WHY IT IS CACHED
--------------------------------------------
The description ("Wat ga je doen?", "Onze impact", "Praktische informatie"),
the meeting point ("Verzamelen bij ..."), the organiser's "Geschikt voor
kinderen" flag and whether sign-up has closed. 530 pages are ~175 MB and ~9
minutes at one a second, for facts that rarely move, so each read is kept in
--cache (an Actions cache file, like the ICS cursor) with the sitemap's lastmod
for that URL. A page is read again when its lastmod moves (20-26 a day in the
week before 2026-10-24), when its cached date/clock/point disagrees with the
listing, when it was never read, or when its read is `refresh_days` old (at
most `max_refresh_per_run` of those per run, oldest first, so a whole first
read does not come due again on one day). Read order is exactly that, so a
run cut by --max-minutes resumes with what it had not read: the cache IS the
cursor. Without the cache file every run reads every page, and is still
inside a 12-minute budget.

A job whose page was not read this run is still written, from the listing
(same fingerprint, no description): the listing alone places it, and the day
is two weeks away. --only-new rows pick the description up on the Wednesday
refresh.

REFUSED, AND COUNTED
--------------------
  * a listed job with no point (7 of 530: no marker in the listing and none on
    the page either) - the only geocoder here is US Census, so nothing places
    a Dutch job that does not bring its own point;
  * a point outside the Netherlands' box, one more than `province_radius_km`
    from the median of its own province's jobs (measured: the farthest is 71
    km, Bergen op Zoom in Noord-Brabant), or one shared by more than
    `max_titles_per_point` different titles (a default pin; measured max 1);
  * a title carrying a URL (1: "Ringpark Oosterbhttps://.../wp-admin/
    post.php?post=5965&action=editoer", which also has no point);
  * a weekday that disagrees with its date (0 seen) - nothing is re-dated;
  * a detail page whose date, clock or point disagrees with the listing (0 of
    15) - not written this run, not cached, read again next run;
  * a full job: the page's form says "Het maximale aantal deelnemers is
    bereikt. Het is helaas niet meer mogelijk om je in te schrijven." (3 of 22
    listed pages read). Full is not cancelled, so it is never a tombstone.
Every refused job that has a date is `mark_seen`, so the rec sync's absence
check (--retire-absent) never reads "full" or "no point" as "gone".

CANCELLATIONS
-------------
Only on the source's own evidence. A job taken off the finder is absent from a
COMPLETE read of the listing (every card parsed, as many as the pager's
total_rows), and `mark_complete` lets the sync retire it behind its 10%
breaker. A title that says "GEANNULEERD" or "afgelast" is EventStore's notice
rule; the fingerprint is make_fingerprint("<title> HH:MM", day, place) - the
title-plus-clock recipe notice_fingerprint can prove - so such a notice
tombstones the job it names. Prose ("bij slecht weer gaat de klus niet door")
cancels nothing.

TIMES AND IDENTITY
------------------
"Zaterdag 7 november 2026" and "09:00 - 12:00" (514 of 530; the other 16 give
no clock and are date-only rows), wall clock in Europe/Amsterdam (CET on both
days). source_id is the WordPress post id; the link is the job's own page,
where the sign-up form is. Two posts with the same title, place, day and start
(Maarn's "Heide schoonhouden op Landgoed Huis te Maarn", ids 21806 and 20782,
one point) are one occasion posted twice: one fingerprint, merged by the
store, and printed every run.

MEASURED 2026-10-24 (live, scratch store): run 1 with --max-minutes 0.75
made 42 requests in 46 s (listing, sitemap, 40 pages; robots.txt apart) and
kept 518 of 530 listed - 517 records, the Maarn pair being one - with 6 no
point, 1 URL title and 5 full refused; 483 rows came from the listing alone
and the read was complete. Run 2 (--max-minutes 0.25) took those 40 from the
cache for no request and read the next 9: 11 requests. Of the 49 pages, 7 are
full, 35 carry the kids flag and 45 a meeting point. All 515 written rows
reach derive_categories as volunteer (484 + outdoors, 31 + outdoors and kids).
A whole first read is ~525 requests, ~9 minutes at one a second.

HTTP: our own User-Agent, robots.txt checked per host before the first
request, one request per `pause` seconds (Crawl-delay honoured if larger), a
redirect followed only on the same site, and a 401/403/429 or a bot challenge
ends the reading - a refusal - though rows already decided from the listing
are still written. A network error or a 5xx costs one page; five failures in a
row end the reading.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint

SOURCE = "natuurwerkdag"
TZ_NAME = "Europe/Amsterdam"
USER_AGENT = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
DEFAULT_MAX_MINUTES = 12.0
DEFAULT_WITHIN_DAYS = 60
DEFAULT_CACHE = "natuurwerkdag_cache.json"
CACHE_VERSION = 1
SAVE_EVERY = 40           # rows between store saves, so a cancelled step keeps its work
CACHE_SAVE_EVERY = 25     # detail reads between cache saves, for the same reason
DESCRIPTION_MAX = 800     # mapsee_supabase_sync's cap; our lines lead so they survive it

_MONTHS = {m: i for i, m in enumerate(
    ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
     "september", "oktober", "november", "december"], start=1)}
_WEEKDAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]

_TAG = re.compile(r"<[^>]+>")
_DATE_RX = re.compile(r"^\s*(?:([a-z]+)\s+)?(\d{1,2})\s+([a-z]+)\s+(\d{4})\s*$", re.I)
_CLOCK_RX = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*(?:-|–|—|tot)\s*(\d{1,2})[:.](\d{2})\s*(?:uur)?\s*$", re.I)
_URL_IN_TITLE = re.compile(r"https?://|/wp-admin/", re.I)

# Listing markup (FacetWP loop, read 2026-10-24).
_FWP_JSON = re.compile(r"FWP_JSON\s*=\s*(\{.*?\});\s*\n", re.S)
_CARD = re.compile(r'(?s)<article class="section-grid__col loop-jobs post-(\d+)([^"]*)">(.*?)</article>')
_CARD_URL = re.compile(r'<a href="([^"]+)"')
_CARD_TITLE = re.compile(r'(?s)<h3 class="heading-800">(.*?)</h3>')
_META_ITEM = re.compile(r'(?s)<li>\s*<i class="([^"]+)"></i>(.*?)</li>')
_PROVINCE = re.compile(r"\bprovince-([a-z-]+)")

# Detail markup: ONLY these four blocks are read. Everything else on the page -
# the "Meer in de buurt" rail above all, which carries other jobs' dates - is not.
_HERO = re.compile(r'(?s)<section class="section-hero-single-jobs[^"]*">(.*?)</section>')
_HERO_TITLE = re.compile(r'(?s)<h1 class="heading-300">(.*?)</h1>')
_SUMMARY = re.compile(r'(?s)<div class="section-jobs-summary">(.*?)</ul>')
_MARKER = re.compile(r'data-lat="([^"]*)"\s*data-lng="([^"]*)"')
_CONTENT = re.compile(r'(?s)<section[^>]*class="section section-jobs-content">(.*?)</section>')
_WYSIWYG = re.compile(r'(?s)<div class="wysiwyg-content"[^>]*>(.*?)</div>')
_FORM = re.compile(r'(?s)id="form" class="section section-jobs-form">(.*?)</section>')
_FULL = re.compile(r"maximale aantal deelnemers is bereikt|niet meer mogelijk om je in te schrijven", re.I)
_ANCHOR_BLOCK = re.compile(r"(?s)<a\b[^>]*>.*?</a>")

# People. A sentence that gives an e-mail address or a phone number, or names
# somebody to contact, is not the row's to carry (drupal_fullcalendar's rule,
# here in Dutch too). A phone is 9-13 digits with spaces, dots, dashes or
# brackets between them: "06-14648889", "+31 6 1464 8889", "0570 123456";
# dates ("2026-11-07", 8 digits) and clocks ("09:00 - 12:00") are not.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w/])\(?\+?\d[\d\s().\-]{7,}\d(?![\w/])")
_CONTACT_LABEL = re.compile(
    r"^\W*(?:contact(?:persoon|gegevens)?|klusleider|aanspreekpunt|co[öo]rdinator|"
    r"organisator|informatie\s+bij|info)\s*:", re.I)
_NAMED_CONTACT = re.compile(
    r"\b(?:contact\s+op\s+met|bel\s+(?:met\s+)?|mail\s+(?:naar\s+)?|vraag\s+naar|"
    r"contact|e-?mail|call|ask\s+for|reach)\s+(?:[A-Z][\w'.-]*,?\s+){1,5}(?:op|via|at|on|\(|$)")
_LEAD_IN = re.compile(r"^\W*(?:\w+\s+){0,2}\w+\s*[?:]\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“‘])")


# --------------------------------------------------------------------------- #
# Small helpers

def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip() or None


def _paragraphs(s: str) -> str:
    """HTML -> text with one line per paragraph or list item; headings dropped
    (they are the template's "Onze impact" / "Praktische informatie")."""
    s = re.sub(r"(?is)<h[1-6][^>]*>.*?</h[1-6]>", "\n", s)
    s = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</ul>|</ol>|</div>", "\n", s)
    s = re.sub(r"(?is)<li[^>]*>", "- ", s)
    s = html_mod.unescape(_TAG.sub(" ", s)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln and ln not in ("-",))


def _trim(s: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def gives_contact(sentence: str) -> bool:
    """Does this sentence hand out a person's (or anyone's) direct contact?"""
    if _EMAIL.search(sentence) or _CONTACT_LABEL.search(sentence) or _NAMED_CONTACT.search(sentence):
        return True
    return any(9 <= _digits(m.group(0)) <= 13 for m in _PHONE.finditer(sentence))


def without_contacts(text: str) -> Tuple[str, int]:
    """(text without any sentence that gives contact details, how many went)."""
    kept_lines: List[str] = []
    gone = 0
    for line in (text or "").split("\n"):
        kept = []
        for s in _SENTENCE_END.split(line.strip()):
            if not s:
                continue
            if gives_contact(s):
                gone += 1
                # "Vragen?" / "Info:" left standing in front of the address it introduced
                if kept and _LEAD_IN.match(kept[-1]):
                    kept.pop()
            else:
                kept.append(s)
        if kept:
            kept_lines.append(" ".join(kept).strip())
    return "\n".join(kept_lines), gone


def province_label(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    return "-".join(p.capitalize() for p in slug.split("-"))


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


# --------------------------------------------------------------------------- #
# Dates and clocks

def parse_date(text: Optional[str]) -> Tuple[Optional[date], Optional[str]]:
    """("Zaterdag 7 november 2026") -> (date, None), or (None, why). The year is
    the page's own; a weekday that names another day refuses the date."""
    m = _DATE_RX.match(text or "")
    if not m:
        return None, "unreadable date"
    wd, day, mon, year = m.group(1), int(m.group(2)), m.group(3).lower(), int(m.group(4))
    if mon not in _MONTHS:
        return None, "unreadable date"
    try:
        d = date(year, _MONTHS[mon], day)
    except ValueError:
        return None, "unreadable date"
    if wd and wd.lower() in _WEEKDAYS and _WEEKDAYS[d.weekday()] != wd.lower():
        return None, "weekday disagrees with the date"
    if wd and wd.lower() not in _WEEKDAYS:
        return None, "unreadable date"
    return d, None


def parse_clock(text: Optional[str]) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """("09:00 - 12:00") -> ((9, 0), (12, 0)); None for no clock or a window
    that does not run forwards inside one day (0 of 514 seen)."""
    m = _CLOCK_RX.match(text or "")
    if not m:
        return None
    sh, sm, eh, em = (int(x) for x in m.groups())
    if not (0 <= sh <= 23 and 0 <= eh <= 23 and sm < 60 and em < 60):
        return None
    if (eh, em) <= (sh, sm):
        return None
    return (sh, sm), (eh, em)


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:      # pragma: no cover - zoneinfo ships with 3.9+
        return None


def localize(d: date, hm: Optional[Tuple[int, int]]) -> Tuple[str, Optional[str]]:
    """(local ISO, UTC ISO). No clock -> a bare date and no instant."""
    if hm is None:
        return d.isoformat(), None
    naive = datetime(d.year, d.month, d.day, hm[0], hm[1])
    tz = _tz()
    if tz is None:
        return naive.isoformat(), None
    dt = naive.replace(tzinfo=tz)
    return dt.isoformat(), dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock_text(clock) -> Optional[str]:
    if not clock:
        return None
    (sh, sm), (eh, em) = clock
    return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


# --------------------------------------------------------------------------- #
# Pages

def parse_listing(page: str) -> Dict[str, Any]:
    """The finder page -> {"cards": [...], "total_rows": int or None, "markers": n}.

    A card: post_id, url, title, date_text, time_text, place, tasks, province,
    lat, lon (from the FWP_JSON map block, keyed on post_id). Cards are read
    from the HTML BEFORE the FWP_JSON script, whose map popups repeat them."""
    head, _, _ = page.partition("FWP_JSON")
    markers: Dict[str, Tuple[float, float]] = {}
    total_rows = None
    m = _FWP_JSON.search(page)
    if m:
        try:
            data = json.loads(m.group(1), strict=False)
        except ValueError:
            data = {}
        settings = ((data.get("preload_data") or {}).get("settings") or {})
        for loc in ((settings.get("map") or {}).get("locations") or []):
            try:
                pos = loc.get("position") or {}
                markers[str(int(loc["post_id"]))] = (float(pos["lat"]), float(pos["lng"]))
            except (KeyError, TypeError, ValueError):
                continue
        pager = settings.get("pager") or {}
        if isinstance(pager.get("total_rows"), int):
            total_rows = pager["total_rows"]
    cards = []
    for pid, cls, body in _CARD.findall(head):
        url = _CARD_URL.search(body)
        title = _CARD_TITLE.search(body)
        meta: Dict[str, str] = {}
        for icon, val in _META_ITEM.findall(body):
            meta[icon.split()[-1]] = _clean(val) or ""
        prov = _PROVINCE.search(cls)
        tasks = [t.strip() for t in (meta.get("fa-shovel") or "").split(",") if t.strip()]
        point = markers.get(pid)
        cards.append({
            "post_id": pid,
            "url": html_mod.unescape(url.group(1)) if url else None,
            "title": _clean(title.group(1)) if title else None,
            "date_text": meta.get("fa-calendar"),
            "time_text": meta.get("fa-clock"),
            "place": meta.get("fa-map") or None,
            "tasks": tasks,
            "province": prov.group(1) if prov else None,
            "lat": point[0] if point else None,
            "lon": point[1] if point else None,
        })
    return {"cards": cards, "total_rows": total_rows, "markers": len(markers)}


def parse_sitemap(xml: str) -> Dict[str, str]:
    """{loc: lastmod} for every <url> that has both."""
    out = {}
    for block in re.findall(r"(?s)<url>(.*?)</url>", xml or ""):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        mod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
        if loc and mod:
            out[html_mod.unescape(loc.group(1))] = mod.group(1)
    return out


def parse_detail(page: str) -> Optional[Dict[str, Any]]:
    """A job page -> its facts, or None when the page carries no job (the
    shell the finder's hidden pages render: no hero, no summary - only the
    rail of OTHER jobs, which is never read).

    Returns date (ISO) / clock ("HH:MM-HH:MM") / lat / lon for the agreement
    check, and meeting / kids / full / text / dropped for the row."""
    hero = _HERO.search(page)
    summary = _SUMMARY.search(page)
    if not hero and not summary:
        return None
    items: Dict[str, str] = {}
    lat = lon = None
    if summary:
        body = summary.group(1)
        mk = _MARKER.search(body)
        if mk:
            try:
                lat, lon = float(mk.group(1)), float(mk.group(2))
            except ValueError:
                lat = lon = None
        for icon, val in _META_ITEM.findall(body):
            items[icon.split()[-1]] = _clean(_ANCHOR_BLOCK.sub(" ", val)) or ""
    if hero:
        for icon, val in _META_ITEM.findall(hero.group(1)):
            items.setdefault(icon.split()[-1], _clean(val) or "")
    d, _why = parse_date(items.get("fa-calendar"))
    clock = parse_clock(items.get("fa-clock"))
    text = ""
    content = _CONTENT.search(page)
    if content:
        text = "\n".join(_paragraphs(w) for w in _WYSIWYG.findall(content.group(1)))
    text, dropped = without_contacts(text)
    meeting, gone = without_contacts(items.get("fa-map-marker") or "")
    dropped += gone
    form = _FORM.search(page)
    title = _HERO_TITLE.search(hero.group(1)) if hero else None
    return {
        "title": _clean(title.group(1)) if title else None,
        "date": d.isoformat() if d else None,
        "clock": clock_text(clock),
        "lat": lat, "lon": lon,
        "meeting": meeting.replace("\n", " ").strip() or None,
        "kids": "fa-child" in items,
        "full": bool(form and _FULL.search(_clean(form.group(1)) or "")),
        "text": text,
        "dropped": dropped,
    }


# --------------------------------------------------------------------------- #
# Placement

def province_outliers(cards: List[Dict[str, Any]], radius_km: float, min_peers: int = 5) -> Dict[str, float]:
    """{post_id: km} for each point farther than radius_km from the median of
    the OTHER points of its own province (provinces with min_peers others).
    Measured 2026-10-24: the farthest of 523 is 71 km (Bergen op Zoom); a job
    pinned in the wrong province of this country lands 120-300 km off."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for c in cards:
        if c.get("lat") is not None and c.get("province"):
            by.setdefault(c["province"], []).append(c)
    out = {}
    for prov, group in by.items():
        if len(group) <= min_peers:
            continue
        for c in group:
            others = [o for o in group if o is not c]
            mid = (statistics.median(o["lat"] for o in others), statistics.median(o["lon"] for o in others))
            km = _km((c["lat"], c["lon"]), mid)
            if km > radius_km:
                out[c["post_id"]] = km
    return out


def crowded_points(cards: List[Dict[str, Any]], max_titles: int) -> set:
    """Post ids on a point shared by more than max_titles DIFFERENT titles: a
    default pin, not a site. Same-title posts on one point (a Friday and a
    Saturday job, a double posting) are one site. Measured max 1 title."""
    by: Dict[Tuple[float, float], Dict[str, List[str]]] = {}
    for c in cards:
        if c.get("lat") is None:
            continue
        key = (round(c["lat"], 6), round(c["lon"], 6))
        by.setdefault(key, {}).setdefault((c.get("title") or "").lower(), []).append(c["post_id"])
    out = set()
    for titles in by.values():
        if len(titles) > max_titles:
            for ids in titles.values():
                out.update(ids)
    return out


# --------------------------------------------------------------------------- #
# Rows

def fingerprint_for(title: str, d: date, clock, place: Optional[str]) -> str:
    """make_fingerprint("<title> HH:MM", day, place) for a timed job, the plain
    recipe for a date-only one: both provable by EventStore's notice_fingerprint,
    so "GEANNULEERD: <title>" tombstones the job it names."""
    if clock:
        (sh, sm), _ = clock
        return make_fingerprint(f"{title} {sh:02d}:{sm:02d}", d.isoformat(), place)
    return make_fingerprint(title, d.isoformat(), place)


def agrees(card: Dict[str, Any], detail: Dict[str, Any], max_m: float = 100.0) -> Optional[str]:
    """None when the page says what the listing says, else what differs."""
    if detail.get("date") != card["date"].isoformat():
        return "date"
    if detail.get("clock") != clock_text(card["clock"]):
        return "clock"
    if detail.get("lat") is not None and card.get("lat") is not None:
        if _km((detail["lat"], detail["lon"]), (card["lat"], card["lon"])) * 1000 > max_m:
            return "point"
    return None


def build_event(card: Dict[str, Any], detail: Optional[Dict[str, Any]], site: Dict[str, Any]) -> NormalizedEvent:
    """One row from a checked listing card (date, clock and point already read)
    and, when there is one that agrees, its page's facts."""
    title, d, clock = card["title"], card["date"], card["clock"]
    if clock:
        start_local, start_utc = localize(d, clock[0])
        end_local, end_utc = localize(d, clock[1])
    else:
        start_local, start_utc = localize(d, None)
        end_local = end_utc = None
    head: List[str] = []
    if detail and detail.get("meeting"):
        meet = _trim(detail["meeting"].rstrip(), int(site.get("meeting_chars", 200)))
        head.append(f"Meeting point: {meet}" + ("" if meet.endswith((".", "!", "?")) else "."))
    if card.get("tasks"):
        head.append("Tasks: " + ", ".join(card["tasks"]) + ".")
    if detail and detail.get("kids"):
        head.append("Suitable for children.")
    head.append(site.get("signup_line", "Volunteer job on the Natuurwerkdag; sign up on its page."))
    head_text = "\n".join(head)
    body = (detail or {}).get("text") or ""
    room = min(int(site.get("description_chars", 700)), DESCRIPTION_MAX - len(head_text) - 2)
    body = _trim(body, room) if body else ""
    extras = [c for c in (site.get("categories") or []) if c]
    if detail and detail.get("kids") and "kids" not in extras:
        extras.append("kids")
    place = card.get("place")
    nev = NormalizedEvent(
        source=SOURCE,
        source_id=card["post_id"],
        name=title,
        description=head_text + ("\n\n" + body if body else ""),
        start_local=start_local, start_utc=start_utc,
        end_local=end_local, end_utc=end_utc,
        timezone=TZ_NAME,
        venue_name=place,
        city=place,
        region=province_label(card.get("province")),
        country=site.get("country", "NL"),
        latitude=float(card["lat"]), longitude=float(card["lon"]),
        coords_exact=True,      # the organiser's own pin; the sync must not geocode prose over it
        category=site.get("category", "volunteer"),
        categories=extras[:2],
        promoter=site.get("promoter"),
        ticket_url=card["url"],
    )
    nev.fingerprint = fingerprint_for(title, d, clock, place)
    return nev


def check_card(card: Dict[str, Any], site: Dict[str, Any], today: date, horizon: date,
               outliers: Dict[str, float], crowded: set) -> Optional[str]:
    """Reads the card's date and clock into it, and says why it is not a row
    (None when it is one). Never raises on a card's content."""
    card["date"], why = parse_date(card.get("date_text"))
    card["clock"] = parse_clock(card.get("time_text"))
    if card["date"] is None:
        return why
    if card["date"] < today:
        return "past"
    if card["date"] > horizon:
        return "beyond the horizon"
    if not card.get("title") or not card.get("url"):
        return "card without a title or link"
    if _URL_IN_TITLE.search(card["title"]):
        return "title carries a URL"
    if card.get("lat") is None:
        return "no point on the site's map"
    s, w, n, e = site.get("bbox", [50.70, 3.20, 53.70, 7.30])
    if not (s <= card["lat"] <= n and w <= card["lon"] <= e):
        return "point outside the Netherlands"
    if card["post_id"] in outliers:
        return "point far from its province's other jobs"
    if card["post_id"] in crowded:
        return "point shared by several different jobs (a default pin)"
    if card.get("time_text") and card["clock"] is None:
        return "unreadable clock"
    return None


# --------------------------------------------------------------------------- #
# The page cache (the cursor)

def load_cache(path: Optional[str]) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {"version": CACHE_VERSION, "pages": {}}
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        print(f"[{SOURCE}] cache unreadable ({type(exc).__name__}); starting without it", flush=True)
        return {"version": CACHE_VERSION, "pages": {}}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION or \
            not isinstance(data.get("pages"), dict):
        return {"version": CACHE_VERSION, "pages": {}}
    return data


def save_cache(path: Optional[str], cache: Dict[str, Any]) -> None:
    if not path:
        return
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, sort_keys=True, indent=0)
    os.replace(tmp, path)


def read_reason(card: Dict[str, Any], entry: Optional[Dict[str, Any]], lastmod: Optional[str],
                today: date, refresh_days: int) -> Optional[str]:
    """Why this job's page must be read this run, or None when the cache holds it."""
    if not entry or not isinstance(entry.get("detail"), dict):
        return "new"
    if lastmod and entry.get("lastmod") != lastmod:
        return "changed"
    if agrees(card, entry["detail"]):
        return "changed"
    try:
        age = (today - date.fromisoformat(entry.get("read") or "")).days
    except ValueError:
        return "changed"
    return "stale" if age >= refresh_days else None


# --------------------------------------------------------------------------- #
# HTTP

class Refused(Exception):
    """The host said no (401/403/429, a challenge, robots.txt)."""


class Stop(Exception):
    """The run deadline passed, or the host stopped answering."""


class Client:
    """One host, paced, robots-checked, deadline-aware (mapsee_ingest_glenecho's
    contract). `get` returns the body of a 2xx and None for any other answer
    worth skipping, a network error included; raises Refused for a refusal and
    Stop at the deadline or after `max_failures` failures in a row."""

    def __init__(self, session, pause: float, deadline: Optional[float],
                 robots=None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, max_failures: int = 5):
        self.session = session
        self.pause = pause
        self.deadline = deadline
        self.clock, self.sleep = clock, sleep
        self.robots = robots
        self.requests = 0
        self.errors = 0
        self.max_failures = max_failures
        self._in_row = 0
        self._last = None

    def _failed(self, why: str) -> None:
        self._in_row += 1
        if self._in_row >= self.max_failures:
            raise Stop(f"{self._in_row} requests in a row failed (last: {why}) - the host is not answering")

    def _allowed(self, url: str) -> None:
        if self.robots is None:
            return
        v = self.robots.check(url)
        if v.get("allowed") is not True:
            raise Refused(f"robots.txt ({v.get('status')}): {v.get('rule')} for {url}")
        cd = v.get("crawl_delay")
        if cd and cd > self.pause:
            self.pause = float(cd)

    def get(self, url: str) -> Optional[str]:
        from robots_txt import CHALLENGE_RX
        host = urlsplit(url).netloc
        for _hop in range(4):
            if self.deadline is not None and self.clock() >= self.deadline:
                raise Stop("run deadline reached")
            self._allowed(url)
            if self._last is not None:
                wait = self.pause - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=40, allow_redirects=False)
            except OSError as exc:    # requests.RequestException is an OSError
                self.errors += 1
                print(f"[{SOURCE}] network error, page skipped: {type(exc).__name__}: {exc} - {url}")
                self._failed(type(exc).__name__)
                return None
            code = r.status_code
            if code in (301, 302, 303, 307, 308):
                nxt = urljoin(url, r.headers.get("Location") or "")
                if urlsplit(nxt).netloc.removeprefix("www.") != host.removeprefix("www."):
                    print(f"[{SOURCE}] redirect off the site, not followed: {url} -> {nxt}")
                    return None
                url = nxt
                continue
            if code in (401, 403, 429):
                raise Refused(f"HTTP {code} for {url}")
            text = r.text or ""
            if CHALLENGE_RX.search(text[:6000]):
                raise Refused(f"bot challenge for {url}")
            if not 200 <= code < 300:
                print(f"[{SOURCE}] HTTP {code}: {url}")
                if code >= 500:
                    self._failed(f"HTTP {code}")
                else:
                    self._in_row = 0
                return None
            self._in_row = 0
            return text
        print(f"[{SOURCE}] too many redirects: {url}")
        return None


# --------------------------------------------------------------------------- #
# The run

def read_site(site: Dict[str, Any], client: Client, today: date,
              emit: Callable[[NormalizedEvent], None],
              seen: Callable[[str], None],
              cache: Dict[str, Any],
              save: Callable[[], None] = lambda: None) -> Dict[str, Any]:
    """Read the listing, refuse what is refused, read the pages the cache does
    not hold (soonest-needed first, until the deadline), and emit one row per
    job. `seen(fingerprint)` is called for every listed job in the window that
    is not written. Returns the report; "complete" means the listing was read
    whole, so a job missing from it has gone. Never raises on content."""
    horizon = today + timedelta(days=int(site.get("within_days", DEFAULT_WITHIN_DAYS)))
    refresh_days = int(site.get("refresh_days", 7))
    max_refresh = int(site.get("max_refresh_per_run", 60))
    rep: Dict[str, Any] = {"listed": 0, "kept": 0, "refused": {}, "notes": {}, "stopped": None,
                           "complete": False, "window": (today, horizon), "requests": 0,
                           "details": {"read": 0, "cached": 0, "listing only": 0}}
    pages: Dict[str, Any] = cache.setdefault("pages", {})

    def count(bucket: str, key: str, n: int = 1) -> None:
        rep[bucket][key] = rep[bucket].get(key, 0) + n

    try:
        page = client.get(site["listing"])
    except (Stop, Refused) as exc:
        rep["stopped"] = (f"REFUSED - {exc}" if isinstance(exc, Refused) else str(exc))
        rep["requests"] = client.requests
        return rep
    if page is None:
        rep["stopped"] = "listing not read"
        rep["requests"] = client.requests
        return rep
    parsed = parse_listing(page)
    cards = parsed["cards"]
    rep["listed"] = len(cards)
    whole = bool(cards) and parsed["total_rows"] == len(cards)
    if not cards:
        print(f"[{SOURCE}] !! the listing matched no cards - the markup has probably changed. "
              f"Check _CARD and _FWP_JSON.", flush=True)
    elif not whole:
        count("notes", f"listing shows {len(cards)} cards of the pager's {parsed['total_rows']}")
        print(f"[{SOURCE}] !! {len(cards)} cards parsed, the pager says {parsed['total_rows']} - "
              f"the read is not complete (paged, or markup changed).", flush=True)

    outliers = province_outliers(cards, float(site.get("province_radius_km", 110)),
                                 int(site.get("province_min_peers", 5)))
    crowded = crowded_points(cards, int(site.get("max_titles_per_point", 2)))
    kept: List[Dict[str, Any]] = []
    for c in cards:
        why = check_card(c, site, today, horizon, outliers, crowded)
        if why is None:
            kept.append(c)
            continue
        if why == "past" or why == "beyond the horizon":
            count("notes", why)
            continue
        count("refused", why)
        if c.get("date") is not None and c.get("title"):
            seen(fingerprint_for(c["title"], c["date"], c["clock"], c.get("place")))
        else:
            whole = False           # a listed job we could not even identify
        if why in ("point far from its province's other jobs",):
            print(f"[{SOURCE}] {why} ({outliers.get(c['post_id'], 0):.0f} km): {c.get('title')} {c.get('url')}")
        elif why not in ("no point on the site's map",):
            print(f"[{SOURCE}] refused ({why}): {c.get('title')} {c.get('url')}")

    # Same title, place, day and start under two post ids: one occasion posted
    # twice (one fingerprint; the store merges them). Named every run.
    twins: Dict[str, str] = {}
    for c in kept:
        fp = fingerprint_for(c["title"], c["date"], c["clock"], c.get("place"))
        if fp in twins:
            count("notes", "same title, place, day and start as another post (a double posting?)")
            print(f"[{SOURCE}] same title, place, day and start: posts {twins[fp]} and {c['post_id']} "
                  f"({c['title']}, {c['date']} {clock_text(c['clock']) or ''}) - one row")
        else:
            twins[fp] = c["post_id"]

    lastmods: Dict[str, str] = {}
    halted = False
    if kept and site.get("sitemap"):
        try:
            xml = client.get(site["sitemap"])
        except (Stop, Refused) as exc:
            xml, halted = None, True
            rep["stopped"] = (f"REFUSED - {exc}" if isinstance(exc, Refused) else str(exc))
        if xml is None:
            count("notes", "sitemap not read (pages re-read by age only)")
        else:
            lastmods = parse_sitemap(xml)

    def write(c: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> None:
        if detail and detail.get("full"):
            count("refused", "full: sign-up closed")
            seen(fingerprint_for(c["title"], c["date"], c["clock"], c.get("place")))
            return
        nev = build_event(c, detail, site)
        rep["kept"] += 1
        emit(nev)

    # Which pages to read, in the order they matter: never read, changed (its
    # lastmod moved or the cached facts disagree with the listing), then the
    # oldest reads past refresh_days - at most max_refresh of those per run.
    order = {"new": 0, "changed": 1, "stale": 2}
    queue: List[Tuple[int, str, Dict[str, Any], str]] = []
    for c in kept:
        entry = pages.get(c["url"])
        why = read_reason(c, entry, lastmods.get(c["url"]), today, refresh_days)
        if why is None:
            rep["details"]["cached"] += 1
            write(c, entry["detail"])
        else:
            queue.append((order[why], (entry or {}).get("read") or "", c, why))
    queue.sort(key=lambda q: (q[0], q[1]))
    stale = [q for q in queue if q[3] == "stale"]
    for q in stale[max_refresh:]:                       # not due this run: the cache still agrees
        rep["details"]["cached"] += 1
        write(q[2], pages[q[2]["url"]]["detail"])
    queue = [q for q in queue if q[3] != "stale"] + stale[:max_refresh]
    for k in ("new", "changed", "stale"):
        n = sum(1 for q in queue if q[3] == k)
        if n:
            count("notes", f"pages to read: {k}", n)

    def from_cache_or_listing(c: Dict[str, Any]) -> None:
        entry = pages.get(c["url"])
        if entry and isinstance(entry.get("detail"), dict) and not agrees(c, entry["detail"]):
            rep["details"]["cached"] += 1
            write(c, entry["detail"])
        else:
            rep["details"]["listing only"] += 1
            write(c, None)

    stopped_at = 0 if halted else None
    reads = 0
    for i, (_o, _r, c, _why) in enumerate(queue):
        if stopped_at is not None:
            break
        try:
            page = client.get(c["url"])
        except (Stop, Refused) as exc:
            stopped_at = i
            rep["stopped"] = (f"REFUSED - {exc}" if isinstance(exc, Refused) else str(exc))
            break
        detail = parse_detail(page) if page is not None else None
        if page is None:
            count("notes", "job page not read")
        elif detail is None:
            count("notes", "job page carries no job (shell)")
            print(f"[{SOURCE}] job page carries no job, listing only: {c['url']}")
        if detail is not None:
            diff = agrees(c, detail)
            if diff:
                count("refused", f"page and listing disagree on the {diff}")
                print(f"[{SOURCE}] page and listing disagree on the {diff}: {c['title']} {c['url']} "
                      f"(page {detail.get('date')} {detail.get('clock')}, listing {c['date']} "
                      f"{clock_text(c['clock'])}) - not written this run")
                seen(fingerprint_for(c["title"], c["date"], c["clock"], c.get("place")))
                pages.pop(c["url"], None)
                continue
            pages[c["url"]] = {"lastmod": lastmods.get(c["url"]), "read": today.isoformat(),
                               "detail": detail}
            rep["details"]["read"] += 1
            if detail.get("dropped"):
                count("notes", "contact sentences dropped", detail["dropped"])
            reads += 1
            if reads % CACHE_SAVE_EVERY == 0:
                save()
            write(c, detail)
        else:
            from_cache_or_listing(c)
    if stopped_at is not None:
        # The deadline (or a refusal) came first: the rest are written from the
        # cache when it still agrees with the listing, else from the listing.
        for _o, _r, c, _why in queue[stopped_at:]:
            from_cache_or_listing(c)
        if len(queue) > stopped_at:
            count("notes", "pages left for the next run", len(queue) - stopped_at)

    # The cache holds only jobs the finder still shows.
    live = {c["url"] for c in cards if c.get("url")}
    for url in [u for u in pages if u not in live]:
        pages.pop(url, None)
    if client.errors:
        rep["notes"]["network error, page skipped"] = client.errors
    rep["requests"] = client.requests
    # Complete = the LISTING was read whole and every card was decided. Detail
    # reads never decide which jobs exist, so a cut there leaves it complete.
    rep["complete"] = whole
    return rep


def _report(name: str, rep: Dict[str, Any]) -> None:
    refused = sum(rep["refused"].values())
    detail = ", ".join(f"{n} {w}" for w, n in sorted(rep["refused"].items(), key=lambda kv: -kv[1]))
    d = rep["details"]
    print(f"[{SOURCE}] {name}: kept {rep['kept']} of {rep['listed']} listed; refused {refused}"
          + (f" ({detail})" if detail else "")
          + f"; pages: {d['read']} read, {d['cached']} from the cache, {d['listing only']} listing only; "
          f"read {'COMPLETE' if rep.get('complete') else 'NOT complete'}; {rep['requests']} request(s)",
          flush=True)
    for w, n in sorted(rep["notes"].items()):
        print(f"[{SOURCE}] {name}: {w}: {n}", flush=True)
    if rep.get("stopped"):
        print(f"[{SOURCE}] {name}: STOPPED - {rep['stopped']}", flush=True)


def _today_local() -> date:
    fixed = os.environ.get("MAPSEE_TODAY")
    if fixed:
        return datetime.strptime(fixed, "%Y%m%d").date()
    tz = _tz()
    return datetime.now(tz).date() if tz else date.today()


def main(argv=None, session_factory=None, sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description="Import the Natuurwerkdag's volunteer jobs.")
    ap.add_argument("--config", default="natuurwerkdag_sources.json")
    ap.add_argument("--store", default="feeds_events.json")
    ap.add_argument("--cache", default=DEFAULT_CACHE,
                    help="per-page read cache, keyed on the sitemap's lastmod (the resume cursor)")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="whole-run deadline; no request starts after it (0 = none)")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    a = ap.parse_args(argv)

    if session_factory is None:
        try:
            import requests
        except ImportError:  # pragma: no cover
            sys.exit("This script needs 'requests'.  Install it with:  pip install requests")
        session_factory = requests.Session
    from robots_txt import Robots

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    started = time.monotonic()
    deadline = started + a.max_minutes * 60 if a.max_minutes else None
    store = None if a.dry_run else EventStore(a.store)
    cache_path = None if a.dry_run else a.cache
    cache = load_cache(cache_path)
    today = _today_local()
    total = 0
    reads: List[Optional[Tuple[date, date]]] = []
    for site in cfg.get("sites", []):
        if site.get("skip"):
            continue
        name = site.get("name", "?")
        session = session_factory()
        session.headers.update({"User-Agent": USER_AGENT,
                                "Accept": "text/html,application/xhtml+xml,application/xml",
                                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5"})
        client = Client(session, float(site.get("pause", 1.0)), deadline, robots=Robots(session),
                        sleep=sleep, max_failures=int(site.get("max_failures_in_row", 5)))
        pending = [0]

        def emit(ev: NormalizedEvent) -> None:
            if store is None:
                return
            store.upsert(ev)
            pending[0] += 1
            if pending[0] >= SAVE_EVERY:
                store.save()
                pending[0] = 0

        def seen(fp: str) -> None:
            if store is not None:
                store.mark_seen(SOURCE, fp)

        try:
            rep = read_site(site, client, today, emit, seen, cache, lambda: save_cache(cache_path, cache))
        except Exception as exc:                          # noqa: BLE001
            print(f"[{SOURCE}] {name} FAILED: {type(exc).__name__}: {exc}", flush=True)
            reads.append(None)
            continue
        finally:
            if store is not None:
                store.save()
            save_cache(cache_path, cache)
        _report(name, rep)
        reads.append(rep["window"] if rep["complete"] else None)
        total += rep["kept"]
    # The listing was read whole, so a job the last complete read wrote and this
    # one did not has left the finder (mapsee_supabase_sync --retire-absent).
    if store is not None and reads and all(reads):
        store.mark_complete(SOURCE, max(w[0] for w in reads), min(w[1] for w in reads))
        store.save()
    took = (time.monotonic() - started) / 60
    if store is not None:
        print(f"[{SOURCE}] done: +{total} events in {took:.1f} min; "
              f"store now holds {len(store.records)} unique events.", flush=True)
    else:
        print(f"[{SOURCE}] dry run - {total} event(s) would be written; {took:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
