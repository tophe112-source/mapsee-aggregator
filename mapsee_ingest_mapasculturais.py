#!/usr/bin/env python3
"""
mapsee_ingest_mapasculturais.py — import events from Brazilian Mapas Culturais instances.

    python mapsee_ingest_mapasculturais.py --config mapasculturais_sources.json --store feeds_events.json

Mapas Culturais (https://github.com/mapasculturais/mapasculturais) is GPLv3
software built for the Brazilian culture ministry and run by states and
municipalities as their official cultural register: agents, spaces, projects and
EVENTS, entered by the cultural workers themselves. Public REST, no key, no
account. It is the closest thing Brazil has to a national community-events
commons, and it is the reason this repo can put anything on the map there at
all — see `_not_included` in the config for the sources that turned out not to.

    GET {base}/api/eventOccurrence/find?@select=…&@limit=100&@page=N

WHY THE OCCURRENCE ENTITY AND NOT `/api/event/find`. An event's dates live on
its occurrences, and `/api/event/find` cannot filter or sort by them: every
date-ish parameter it accepts is ignored (below). `/api/eventOccurrence/find`
exposes the real columns, so it is the only endpoint that can answer "what is
on next week".

FIVE THINGS THAT WILL CATCH YOU OUT, every one of them measured on live
instances on 2026-08-28 and every one silent:

1. THE DATE FILTER IS HONOURED ON SOME INSTANCES AND SILENTLY IGNORED ON
   OTHERS, and when ignored it returns THE WHOLE ARCHIVE — which on a small
   instance looks exactly like a filtered result. Espírito Santo answered
   `_startsOn=GTE(today)` with 1,575 rows, of which ELEVEN were in the future;
   the rest ran back through 2014 to one dated 1911. João Pessoa answered with
   34, of which ZERO were future. Configure either on the strength of that
   count and you publish a decade of dead listings.

   The tell is free and exact: ask for `@count=1` WITH the filter and again
   WITHOUT it. Equal counts mean the filter did nothing. `filter_honoured()`
   does that once per instance, prints which it got, and the walk is bounded
   accordingly — but the count is never the guarantee. Every row is re-checked
   against `rule.startsOn` client-side no matter what the server said, because
   that is the only thing that cannot be silently wrong.

   Same family as BikeReg's search endpoint answering 200 with a hidden
   hundred-row ceiling, and as "counting records is not checking dates" —
   wisconsinbikefed's beautifully-formed iCal in which every event was past.

2. `rule.startsOn` IS THE DATE. The structured `_startsOn`/`_startsAt` columns
   are populated on some instances and NULL on others (all 1,575 of Espírito
   Santo's, all 34 of João Pessoa's) — while `rule`, a JSON blob, carries the
   real value on every row of every instance seen. That is why filtering on
   `_startsOn` can silently pass a 2023 event: the column it tests is empty.
   Two spellings of one fact, and this one was chosen only after finding the
   records where they DISAGREE.

3. `timezoneName` SAYS `Etc/UTC` AND IT IS NOT TRUE. All 1,938 occurrences read
   across every instance carry that string, including a Fortaleza cinema
   session whose own `_startsOn` was serialised `America/Fortaleza`. The times
   in `rule` are naive LOCAL clock times. Read `timezoneName` and a 19:40 show
   is served at 16:40. So the adapter emits `start_local` and lets the sync
   turn the venue's coordinates into a zone (`_tz_for`), which is what
   `mapsee_ingest_jsonld` does with WP Event Manager's naive stamps. Brazil
   spans four zones, so "just add three hours" is not available either.

4. `undefined` IS A LITERAL IN THE ADDRESS STRING — the platform interpolates
   a missing field's JS value straight into `endereco`. 167 of Ceará's 329 live
   rows: "Rua Dragão do Mar, , Praia de Iracema, Fortaleza, undefined, CE,
   60060-390". It is TRUTHY, so it survives every `if not x` gap-fill and
   reaches the geocoder as part of a street. The WP Event Manager `"-"` lesson
   in Portuguese.

5. A RECURRING OCCURRENCE OFTEN HAS NO `until`, and that projects pins for
   ever — 145 of Espírito Santo's. `mapsee_cleanup.py` deletes the PAST and a
   2050 weekly is not past. So the horizon belongs HERE, where the count that
   was dropped can still be printed. Same as MyListing's 2050 Polar Bear Plunge
   and British Cycling's rides dated 2500.

COORDINATES ARE THE POINT, AND THEY ARE WHY THIS SOURCE IS WORTH HAVING. The
only geocoder in this pipeline is US Census, so outside the US a source that
does not bring its own position is dropped at the sync with nothing in any log
to say so (`mapsee_ingest_tribe` reported "kept 43 events" for Calgary and
placed none of them). 341 of the 345 genuinely-future occurrences found across
eight instances carry `space.location`. Those coordinates are set `coords_exact`
so the sync does not re-geocode over them: "Rua Dragão do Mar, Fortaleza" handed
to a US geocoder is the Berlin/Paris near-miss that `mapsee_ingest_markets`
already paid for, and a Brazilian street that happens to match a US one would
move the pin to another hemisphere.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_ingest import NormalizedEvent, EventStore, make_fingerprint, norm_categories

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"
_TAG = re.compile(r"<[^>]+>")

# What to ask for. `rule` is the load-bearing one (see header note 2); the
# underscored columns are requested too so `filter_honoured` and anyone
# debugging can see what the instance actually stores.
SELECT = (
    "id,_startsOn,frequency,_until,timezoneName,rule,"
    "event.{id,name,shortDescription,singleUrl,terms,type},"
    "space.{id,name,location,endereco,singleUrl}"
)

# Mapas Culturais' `linguagem` taxonomy -> mapsee's 15 keys. All 26 values here
# were observed live rather than read off a spec, so this covers what the
# instances actually emit. "Outros" is deliberately absent: it means the
# organiser declined to say, and the instance's configured default is a better
# answer than a guess.
_LINGUAGEM = {
    "teatro": ("theater", []),
    "teatro de bonecos": ("theater", ["kids"]),
    "humor": ("theater", []),
    "circo": ("arts", []),
    "artes circenses": ("arts", []),
    "performance": ("arts", []),
    "dança": ("arts", []),
    "danca": ("arts", []),
    "cinema": ("arts", []),
    "audiovisual": ("arts", []),
    "exposição": ("arts", []),
    "exposicao": ("arts", []),
    "artes visuais": ("arts", []),
    "artes integradas": ("arts", []),
    "música": ("music", []),
    "musica": ("music", []),
    "música popular": ("music", []),
    "musica popular": ("music", []),
    "música erudita": ("music", []),
    "musica erudita": ("music", []),
    "hip hop": ("music", []),
    "curso ou oficina": ("learning", []),
    "formação": ("learning", []),
    "formacao": ("learning", []),
    "cultura digital": ("learning", []),
    "livro e literatura": ("learning", ["arts"]),
    "contadores de histórias e mediadores de leitura": ("kids", ["learning"]),
    "contadores de historias e mediadores de leitura": ("kids", ["learning"]),
    "palestra, debate ou encontro": ("community", ["learning"]),
    "cultura tradicional": ("community", ["arts"]),
    "cultura indígena": ("community", ["arts"]),
    "cultura indigena": ("community", ["arts"]),
    "jogos": ("party", []),
    "rádio": ("other", []),
    "radio": ("other", []),
}

# Occurrence frequencies seen live, as day steps. `once` needs no expansion and
# anything unrecognised is treated as a single date — inventing a cadence from a
# word we have not seen is how a source starts publishing events nobody planned.
_STEP_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30, "yearly": 365}


def _clean(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_mod.unescape(_TAG.sub(" ", str(s)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# The 27 federal units. A two-letter token that is one of these is the anchor
# the address is read outward from — see split_address.
_UF = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
       "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
       "SP", "SE", "TO"}
# What the platform writes where a field was left empty. Every one of these is
# TRUTHY and reached the stored row as address text. "undefined" and "value" are
# both live: the template interpolates a missing JS value, and a missing one
# from its own form. Header note 4.
_JUNK = {"undefined", "null", "value", "-", "s/n", "sn"}
_CEP = re.compile(r"^\d{5}-?\d{3}$")


def _segments(raw: Optional[str]) -> List[str]:
    return [p.strip() for p in str(raw or "").split(",")]


def clean_address(raw: Optional[str]) -> Optional[str]:
    """The street half of `endereco`, with the placeholders and empty slots gone.

    `endereco` is built by interpolating fields that may not exist, so a missing
    one arrives as the literal "undefined" or "value" and a missing pair arrives
    as ", ,". 167 of Ceará's 329 live rows carry one. They are truthy, so they
    survive every `if not x` gap-fill and would reach the row as street text.

    Only the STREET is kept here: the city, UF and CEP are pulled out separately
    by split_address into their own columns. Putting the city into `address` is
    what invited the geocoder to move an OSM market's pin, and while
    coords_exact keeps this source out of the geocoder entirely, a street column
    holding "Fortaleza, CE, 60060-390" is still wrong on the page.
    """
    parts = [p for p in _segments(raw) if p and p.lower() not in _JUNK]
    if not parts:
        return None
    street = parts[0]
    # A bare house number in the next slot belongs with the street.
    if len(parts) > 1 and re.fullmatch(r"\d[\d\-/]*", parts[1]):
        street = f"{street}, {parts[1]}"
    return street or None


def split_address(raw: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(city, uf, cep) read outward from the UF token — never by position.

    Two layouts are live on the same instance and a positional parser gets one
    of them wrong:

        street, number, , bairro, CITY, undefined, UF, CEP     (UF second-last)
        street, number, bairro, CEP, CITY, UF                  (UF last)

    So: find the UF, then walk BACKWARDS past the placeholders and the CEP; the
    first real token is the city. That reads both, and it is the same discipline
    `mapsee_ingest_mylisting` needed — anchor on the part whose SHAPE is
    unmistakable and read out from it, because counting commas assumes every
    optional field was filled in.
    """
    parts = _segments(raw)
    cep = next((p.replace("-", "") for p in parts if _CEP.match(p)), None)
    if cep:
        cep = f"{cep[:5]}-{cep[5:]}"
    idx = next((i for i in range(len(parts) - 1, -1, -1)
                if parts[i].upper() in _UF), None)
    if idx is None:
        return None, None, cep
    uf = parts[idx].upper()
    for j in range(idx - 1, -1, -1):
        tok = parts[j]
        if not tok or tok.lower() in _JUNK or _CEP.match(tok):
            continue
        if re.fullmatch(r"\d[\d\-/]*", tok):            # a house number, not a city
            continue
        return tok.title() if tok.isupper() else tok, uf, cep
    return None, uf, cep


def parse_location(space: Optional[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    """`space.location` is {"latitude": "-3.72…", "longitude": "-38.52…"} — STRINGS.

    0,0 IS THE COMMONEST COORDINATE IN THIS SOURCE, and because it arrives as
    the STRING "0" it passes every presence check. 55 of the 345 genuinely
    future occurrences found across all instances are at null island — a space
    whose registrant never dragged the pin. The first pass of the audit that
    produced this adapter asked `if loc.get("latitude")` and reported Ceará at
    328 of 329 placeable; the real figure is 281, and two other instances that
    look live put nothing on the map at all.

    That is the `"-"` placeholder in WP Event Manager and the `undefined` in
    this platform's own address strings, one field over: a falsy-looking value
    that is truthy. Anything out of range is a parse that went wrong rather
    than a real place.
    """
    loc = (space or {}).get("location")
    if not isinstance(loc, dict):
        return None, None
    try:
        lat = float(loc.get("latitude"))
        lon = float(loc.get("longitude"))
    except (TypeError, ValueError):
        return None, None
    if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None
    return lat, lon


def _rule(occ: Dict[str, Any]) -> Dict[str, Any]:
    """`rule` is a JSON blob and some instances hand it over as a STRING."""
    r = occ.get("rule")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except Exception:  # noqa: BLE001
            return {}
    return r if isinstance(r, dict) else {}


def _date_str(v: Any) -> Optional[str]:
    """A date out of `rule` (plain "YYYY-MM-DD") or a PHP-serialised column."""
    if not v:
        return None
    if isinstance(v, dict):
        v = v.get("date")
    s = str(v).strip()
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def occurrence_dates(occ: Dict[str, Any], today: str, horizon_days: int) -> List[str]:
    """Every date this occurrence puts on the map, inside the horizon.

    A `once` is itself. A recurrence runs to `until` — and when `until` is
    missing or blank, which is the common case (145 of Espírito Santo's live
    rows), it runs to the horizon and STOPS. Without that bound a weekly with no
    end date projects pins for ever and nothing downstream can remove them:
    cleanup deletes the past, and a 2050 Tuesday is not past. Header note 5.
    """
    ru = _rule(occ)
    start = _date_str(ru.get("startsOn")) or _date_str(occ.get("_startsOn"))
    if not start:
        return []
    freq = str(ru.get("frequency") or occ.get("frequency") or "once").strip().lower()
    step = _STEP_DAYS.get(freq)
    limit = (date.fromisoformat(today) + timedelta(days=horizon_days)).isoformat()
    if not step:                                        # `once`, or a word we do not know
        return [start] if today <= start <= limit else []
    until = _date_str(ru.get("until")) or _date_str(occ.get("_until")) or limit
    stop = min(until, limit)
    try:
        cur = date.fromisoformat(start)
    except ValueError:
        return []
    out: List[str] = []
    stop_d = date.fromisoformat(stop)
    # An occurrence that started years ago is still running: walk forward from
    # its start, but only COLLECT from today, so a weekly yoga class registered
    # in 2021 contributes the next few weeks and not eight hundred dead dates.
    guard = 0
    while cur <= stop_d and guard < 4000:
        guard += 1
        iso = cur.isoformat()
        if iso >= today:
            out.append(iso)
        cur += timedelta(days=step)
    return out


def _categories(ev: Dict[str, Any], site: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Primary + secondaries from the `linguagem` taxonomy."""
    terms = (ev.get("terms") or {}) if isinstance(ev.get("terms"), dict) else {}
    langs = [str(x).strip().lower() for x in (terms.get("linguagem") or []) if x]
    primary, extras = None, []
    for l in langs:
        hit = _LINGUAGEM.get(l)
        if hit and not primary:
            primary, extras = hit[0], list(hit[1])
        elif hit:
            extras.append(hit[0])
    if not primary:
        primary = site.get("category", "community")
    return primary, norm_categories(primary, extras)


def _time_of(ru: Dict[str, Any], key: str) -> Optional[str]:
    """"19:40" -> "19:40:00". Anything else is not a clock time."""
    t = str(ru.get(key) or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or s > 59:
        return None
    return f"{h:02d}:{mi:02d}:{s:02d}"


def to_event(occ: Dict[str, Any], day: str, site: Dict[str, Any]) -> Optional[NormalizedEvent]:
    """One occurrence on one date -> one row, or None if it cannot be placed."""
    ev = occ.get("event") or {}
    name = _clean(ev.get("name"))
    if not name:
        return None                                     # orphan occurrence; counted by the caller
    ru = _rule(occ)
    space = occ.get("space") or {}
    lat, lon = parse_location(space)

    start_at = _time_of(ru, "startsAt")
    end_at = _time_of(ru, "endsAt")
    # NAIVE LOCAL, deliberately — header note 3. The sync turns these
    # coordinates into an IANA zone and converts. A date with no clock time is
    # left date-only, which the sync reads as all-day rather than midnight.
    start_local = f"{day}T{start_at}" if start_at else day
    end_local = f"{day}T{end_at}" if end_at and start_at and end_at > start_at else None

    occ_id = str(occ.get("id") or ru.get("spaceId") or "")
    # THE DATE IS PART OF THE KEY. One occurrence id expands to many dates, and
    # EventStore.upsert keys on (source, source_id) and POPS the stored record
    # when the fingerprint moves — so a bare id makes each date DELETE the one
    # before it and a fifty-week class survives as its last Tuesday. BikeReg
    # paid 121 rows to learn this.
    source_id = f"{site.get('slug') or site.get('name')}:{occ_id}:{day}"

    primary, extras = _categories(ev, site)
    venue = _clean(space.get("name"))
    desc = _clean(ev.get("shortDescription"))
    endereco = space.get("endereco")
    city, uf, cep = split_address(endereco)
    nev = NormalizedEvent(
        source="mapasculturais",
        source_id=source_id,
        name=name,
        description=desc,
        start_local=start_local,
        end_local=end_local,
        venue_name=venue,
        latitude=lat, longitude=lon,
        address=clean_address(endereco),
        # The address is the better witness where it has one; the config's
        # value only FILLS a gap, never overrides — a state-wide instance has
        # no one city and guessing one would relabel every event in it.
        city=city or _clean(site.get("default_city")),
        region=uf or _clean(site.get("default_region")),
        postal_code=cep,
        country=site.get("default_country", "Brazil"),
        category=primary,
        categories=extras,
        ticket_url=_clean(ev.get("singleUrl")) or _clean(space.get("singleUrl")),
        # The instance's own point for the venue. Not a geocoder's guess, and a
        # Brazilian street handed to US Census is the pin-in-another-hemisphere
        # failure mapsee_ingest_markets already paid for.
        coords_exact=bool(lat is not None and lon is not None),
    )
    nev.fingerprint = make_fingerprint(name, day, venue, nev.city)
    return nev


def filter_honoured(session, base: str, today: str) -> Optional[bool]:
    """Does this instance actually APPLY `_startsOn=GTE(today)`?

    Header note 1. Ask for the count twice — with the filter and without — and
    if they match, the filter did nothing and the walk would be the whole
    archive. Returns None when either count could not be read, which is not the
    same as "ignored": an instance that will not answer a count still gets
    walked, just conservatively.
    """
    def count(q: str) -> Optional[int]:
        try:
            r = session.get(f"{base}/api/eventOccurrence/find?@select=id&@limit=1&@count=1{q}",
                            timeout=45)
        except Exception:  # noqa: BLE001
            return None
        if r.status_code != 200 or not r.text.strip().isdigit():
            return None
        return int(r.text.strip())
    total = count("")
    filtered = count(f"&_startsOn=GTE({today})")
    if total is None or filtered is None:
        return None
    # A FILTERED COUNT OF ZERO PROVES NOTHING, and believing it is the one way
    # this check can cost a whole instance in silence. Two instances answer
    # `filtered=0, total=N` and they want opposite things: mapassaas has a
    # populated `_startsOn` and genuinely holds nothing future (measured
    # 2026-08-30: 5,986 occurrences, newest 2025-08-30, zero at-or-after today),
    # while an instance with `_startsOn` NULL on every row — Espírito Santo's
    # 1,575 and João Pessoa's 34, per header note 2 — answers 0 to the same
    # question while carrying live dates in `rule`. Read as "honoured" the walk
    # then asks the server for the filtered set, gets nothing, and prints
    # "kept 0 events": identical to an empty instance and wrong on the second.
    # Zero is not evidence, so say so — None already means "walk it
    # conservatively and judge every row on rule.startsOn", which costs one
    # unfiltered walk and cannot be silently wrong. The rule is flat rather
    # than conditional on `total`: equal ZEROS are the same non-evidence, and
    # reading them as "ignored" would be a verdict drawn from an empty answer.
    if filtered == 0:
        return None
    return filtered != total


def ingest_site(store: EventStore, session, site: Dict[str, Any], today: str) -> int:
    name = site.get("name", "?")
    base = (site.get("base_url") or "").rstrip("/")
    if not base:
        print(f"[mapasculturais] {name}: no base_url")
        return 0
    horizon = int(site.get("horizon_days", 42))
    per_page = int(site.get("limit", 100))
    max_pages = int(site.get("max_pages", 40))
    delay = float(site.get("crawl_delay", 1))

    honoured = filter_honoured(session, base, today)
    if honoured is False:
        # Say so out loud. A silently-ignored filter is the one failure here
        # that looks exactly like success, and this line is the only place it
        # can ever be seen.
        print(f"[mapasculturais] {name}: server ignores _startsOn — walking the "
              f"archive and filtering on rule.startsOn (max {max_pages} pages)")
    q = f"&_startsOn=GTE({today})" if honoured else ""

    rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = f"{base}/api/eventOccurrence/find?@select={SELECT}&@limit={per_page}&@page={page}{q}"
        try:
            r = session.get(url, timeout=90)
        except Exception as exc:  # noqa: BLE001
            print(f"[mapasculturais] {name} p{page} failed: {exc}")
            break
        if r.status_code != 200:
            # Quote the server back rather than a bare status: a 500 here is
            # usually a rejected parameter and the body is what says which.
            print(f"[mapasculturais] {name} p{page} HTTP {r.status_code}: "
                  f"{r.text[:160].strip()}")
            break
        try:
            batch = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[mapasculturais] {name} p{page} bad JSON: {exc}")
            break
        if not isinstance(batch, list) or not batch:
            break
        rows += batch
        if len(batch) < per_page:
            break
        if delay:
            time.sleep(delay)

    kept = orphan = unplaced = past = 0
    expanded = 0
    for occ in rows:
        days = occurrence_dates(occ, today, horizon)
        if not days:
            past += 1
            continue
        if not (occ.get("event") or {}).get("name"):
            orphan += 1
            continue
        expanded += len(days)
        for day in days:
            nev = to_event(occ, day, site)
            if nev is None:
                orphan += 1
                continue
            if nev.latitude is None or nev.longitude is None:
                # The sync drops a coordless row and says nothing, so count it
                # here: "ingested" and "put zero on the map" must not read the
                # same. mapsee_ingest_tribe's Calgary lesson.
                unplaced += 1
                continue
            store.upsert(nev)
            kept += 1
    print(f"[mapasculturais] {name}: {len(rows)} occurrence(s) read -> {expanded} dated, "
          f"kept {kept} (skipped {past} past/out-of-horizon, {unplaced} with no "
          f"coordinates, {orphan} with no event)")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Brazilian Mapas Culturais instances.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--store", default="mapsee_events.json")
    ap.add_argument("--only", help="ingest just this site (substring match on name)")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    store = EventStore(a.store)
    today = date.today().isoformat()
    total = 0
    for site in cfg.get("sites", []):
        if a.only and a.only.lower() not in str(site.get("name", "")).lower():
            continue
        try:
            total += ingest_site(store, session, site, today)
        except Exception as exc:  # noqa: BLE001
            print(f"[mapasculturais] {site.get('name','?')} FAILED: {exc}")
    store.save()
    print(f"[mapasculturais] done: +{total} events; store now holds "
          f"{len(store.records)} unique events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
