#!/usr/bin/env python3
"""
test_coverage_rows.py — the coverage report's arithmetic and its country names.

WHY THIS EXISTS
---------------
`catalog_curate.py coverage` is not a status page. Its "thin ground" ranker is
what decides where the next curation run spends its budget, and `coverage --json`
is what gets appended to `coverage_history.jsonl` as the record of whether the
catalog is growing. Four separate defects were feeding both, all found on
2026-09-06 by reading one report:

1. `ALL_TYPES = sorted(CONFIG) + sorted(EXTRA_CONFIG)` listed jsonld, mylisting
   and venuepilot TWICE — they are declared in both tables over the same file —
   and `_coverage_rows` walked both paths, so 107 sources were counted twice.
   Reported total 1702; real total 1595. Every `total_sources` ever written to
   coverage_history.jsonl carries that 6.7%.

2. `_rows_parkrun` iterated `countries`, which is a MAP of parkrun's numeric
   country id to an ISO code, as if it were a list — filing all 20 of parkrun's
   countries under names like "97", "3" and "85". The 20 real ones read as
   having no running supply while ~2,965 weekly events sat in them.

3. `default_country` was used raw, and the configs use two conventions: gancio
   writes "DE", mobilizon writes "Germany". Fifteen sources split off into six
   phantom rows. "CA" showed 1 source and was FLAGged as needing feeds, three
   lines under "Canada" with 36.

4. Nothing consulted `_url_country`, though it has been in the file all along,
   so 539 of 1702 rows (32%) sat under "?" — which the ranker cannot tell apart
   from a country with no feeds at all.

Together those invented 26 countries. The report said 60; there were 34.

These tests pin the INVARIANTS rather than today's counts, so they keep holding
as the catalog grows: no type is counted twice, no country is a number, no
country is a bare ISO code, and a source whose URL names its country is not
filed under "?".

    python test_coverage_rows.py
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import catalog_curate as C

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --- 1. no type is counted twice ---------------------------------------------

def t_all_types_lists_each_type_once():
    dupes = [t for t, n in collections.Counter(C.ALL_TYPES).items() if n > 1]
    check("ALL_TYPES lists every type once", not dupes, f"duplicated: {dupes}")

    overlap = sorted(set(C.CONFIG) & set(C.EXTRA_CONFIG))
    check("the overlap between the two config tables is still real (this test "
          "would be vacuous without it)", bool(overlap))
    same_file = {t for t in overlap if C.CONFIG[t][0] == C.EXTRA_CONFIG[t][0]}
    check("every type declared twice over the SAME file is expander-owned",
          same_file == C.EXPANDER_OWNED,
          f"same-file {sorted(same_file)} vs owned {sorted(C.EXPANDER_OWNED)}")


def t_no_source_is_counted_twice():
    rows = C._coverage_rows()
    # market/parkrun/runsignup expanders deliberately emit one row per PLACE or
    # per country from a single source, so (type, name) repeats there by design.
    # An expander-owned type has no such expansion: one entry, one row. A repeat
    # in one of those is the double-walk.
    seen = collections.Counter((t, n) for t, n, _m, _c, _cat in rows
                               if t in C.EXPANDER_OWNED)
    # ...counted against the ENTRIES that share the name, not against one. Two
    # entries may carry the same name legitimately (jsonld_sources.json has two
    # "Lento Dance Studio" listings on 2026-09-14, one URL each), while the
    # double walk this guards against doubles EVERY name, shared or not.
    named = collections.Counter()
    for t in C.EXPANDER_OWNED:
        fname = C.CONFIG[t][0]
        p = os.path.join(C.HERE, fname)
        if os.path.exists(p):
            for e in C._entries(fname, json.load(open(p, encoding="utf-8"))):
                named[(t, str(e.get("name") or ""))] += 1
    dupes = [k for k, v in seen.items() if v > 1 and v > named[k]]
    check("no expander-owned source appears twice in the coverage rows",
          not dupes, f"{len(dupes)} duplicated, e.g. {dupes[:3]}")

    # ...and the per-type count matches the file itself, so a future edit that
    # drops the expander instead of the generic path still lands on one row each.
    for t in sorted(C.EXPANDER_OWNED):
        fname = C.CONFIG[t][0]
        p = os.path.join(C.HERE, fname)
        if not os.path.exists(p):
            continue
        want = len(C._entries(fname, json.load(open(p, encoding="utf-8"))))
        got = sum(1 for r in rows if r[0] == t)
        check(f"{t}: {want} entries in {fname} -> {want} rows", got == want, f"got {got}")


# --- 2. a country is a place, not an id --------------------------------------

def t_no_country_is_a_number():
    rows = C._coverage_rows()
    numeric = sorted({c for _t, _n, _m, c, _cat in rows if re.fullmatch(r"\d+", str(c))})
    check("no source is filed under a numeric country", not numeric,
          f"{len(numeric)} numeric countries: {numeric[:8]}")


def t_parkrun_lands_in_the_countries_it_actually_covers():
    data = json.load(open(os.path.join(C.HERE, "parkrun_sources.json"), encoding="utf-8"))
    declared = data.get("countries") or {}
    check("parkrun's config still maps numeric id -> ISO code (the shape the "
          "adapter needs, and the shape that tripped this)",
          isinstance(declared, dict) and bool(declared), f"got {type(declared).__name__}")

    got = {c for _n, _m, c, _cat in C._rows_parkrun(data)}
    want = {C._ISO_COUNTRY.get(v.upper(), v.upper()) for v in declared.values()}
    check(f"parkrun is filed under all {len(want)} countries it declares",
          got == want, f"missing {sorted(want - got)}; extra {sorted(got - want)}")
    check("...and Great Britain among them is a country name, not '97'",
          "United Kingdom" in got, f"got {sorted(got)[:6]}")


# --- 3. one country, one spelling --------------------------------------------

def t_no_country_is_a_bare_iso_code():
    rows = C._coverage_rows()
    bare = sorted({c for _t, _n, _m, c, _cat in rows
                   if isinstance(c, str) and len(c) == 2 and c.isupper() and c.isalpha()})
    check("no country is a bare two-letter code", not bare,
          f"{bare} — add to _ISO_COUNTRY or normalise at the source")


def t_every_iso_code_in_every_config_has_a_name():
    """The lookup miss is the failure mode, not the lookup. IS and JM reached
    the report as countries because bikereg named them and the table did not."""
    keys = ("countries", "default_country", "country", "only_countries")
    missing = set()

    def walk(o, fname):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys:
                    vals = list(v.values()) if isinstance(v, dict) else (
                        v if isinstance(v, list) else [v])
                    for x in vals:
                        s = str(x).strip()
                        if len(s) == 2 and s.isalpha() and s.upper() not in C._ISO_COUNTRY:
                            missing.add(f"{fname}:{s.upper()}")
                else:
                    walk(v, fname)
        elif isinstance(o, list):
            for x in o:
                walk(x, fname)

    for fname in sorted(os.listdir(C.HERE)):
        if not fname.endswith("_sources.json"):
            continue
        try:
            data = json.load(open(os.path.join(C.HERE, fname), encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed config is another test's job
            continue
        walk(data, fname)
    check("every ISO code any config declares has a name in _ISO_COUNTRY",
          not missing, ", ".join(sorted(missing)))


# --- 4. "?" is a real gap, not an unread URL ---------------------------------

def t_a_cctld_source_is_not_filed_under_unknown():
    check("_first_url reads a plain string key",
          C._first_url({"base_url": "https://example.co.nz/"}, "base_url")
          == "https://example.co.nz/")
    check("...and the first entry of a LIST key, which is how jsonld spells it",
          C._first_url({"listing": ["https://example.dk/events", "https://x.dk/2"]}, "listing")
          == "https://example.dk/events")
    check("...and returns '' rather than raising when there is no URL at all",
          C._first_url({"name": "no url here"}, "url") == "")

    rows = C._coverage_rows()
    unknown = sum(1 for _t, _n, _m, c, _cat in rows if c == "?")
    check("fewer than a quarter of catalog rows have no country",
          unknown < len(rows) / 4, f"{unknown} of {len(rows)}")


def t_the_country_count_matches_the_rows():
    """coverage_history.jsonl's `countries` is what the growth story is told
    with; it must count places, not report artefacts."""
    snap = C.coverage_snapshot()
    rows = C._coverage_rows()
    real = {c for _t, _n, _m, c, _cat in rows if c != "?"}
    check("the snapshot's country count is the number of named countries",
          snap["countries"] == len(real), f"{snap['countries']} vs {len(real)}")
    check("the snapshot's total is the number of rows",
          snap["total_sources"] == len(rows), f"{snap['total_sources']} vs {len(rows)}")


# --- 5. a place outside the United States is read as being outside it --------

def t_a_foreign_iso_code_in_a_suffix_is_a_country():
    """Measured 2026-09-19: 115 sources were filed under the United States with
    a foreign country code standing in as the METRO. ", Zurich, CH" is the shape
    — the venue backend wrote the metro label as `name, ISO` and _parse_place
    knew neither half."""
    for suffix, metro, country in (
            (", Zurich, CH", "Zurich", "Switzerland"),
            (", Stockholm, SE", "Stockholm", "Sweden"),
            (", Brussels, BE", "Brussels", "Belgium"),
            (", Sydney, AU", "Sydney", "Australia"),
            (", Auckland, NZ", "Auckland", "New Zealand"),
            (", Dublin, IE", "Dublin", "Ireland"),
    ):
        got = C._parse_place(suffix.strip(" ,"))
        check(f"'{suffix.strip()}' is {metro}, {country}", got == (metro, country), str(got))


def t_a_country_spelled_out_is_a_country_too():
    """The other half of the same defect: a suffix ending in a country NAME that
    _COUNTRY_ALIASES happened not to list ("Sweden", "Hong Kong") made the NAME
    the metro and the US the country."""
    for suffix, metro, country in (
            (", Gothenburg, Sweden", "Gothenburg", "Sweden"),
            (", Milan, Italy", "Milan", "Italy"),
            (", Hong Kong", "Hong Kong", "Hong Kong"),
            (", Oslo, Norway", "Oslo", "Norway"),
            (", Brno, Czechia", "Brno", "Czechia"),
    ):
        got = C._parse_place(suffix.strip(" ,"))
        check(f"'{suffix.strip()}' is {metro}, {country}", got == (metro, country), str(got))


def t_a_us_state_spelled_out_is_still_the_us_and_is_not_the_metro():
    """What the civic backend writes. `geocode_suffix` is ", City, Region" and
    Wikidata's region label is the full name, so the state was read as the metro
    — 894 rows on 2026-09-19, the whole top of the metro table."""
    for suffix, metro in ((", Eau Claire, Wisconsin", "Eau Claire"),
                          (", Mansfield, Texas", "Mansfield"),
                          (", Poway, California", "Poway"),
                          (", Edina, Minnesota", "Edina")):
        got = C._parse_place(suffix.strip(" ,"))
        check(f"'{suffix.strip()}' is {metro}, United States",
              got == (metro, "United States"), str(got))
    rows = C._coverage_rows()
    # "New York" is the one name that is BOTH, and as a metro it is the right
    # answer — 11 rows, all of them New York City (Prospect Park Alliance,
    # Riverside Park Conservancy, Food Bank For New York City).
    states = {m for _t, _n, m, c, _cat in rows
              if c == "United States" and m in C._US_STATE_NAMES and m != "New York"}
    check("no US row has a STATE where its metro should be", not states,
          f"still states: {sorted(states)[:6]}")


def t_a_two_letter_code_is_never_a_metro():
    """The bare-'(City)'-is-US convention is about a city NAME. It was also
    swallowing province codes (", Winnipeg, MB") and acronyms a curator put in a
    source's name ("(RDA)", "(CFI)"), inventing US metros called MB and RDA."""
    check("a bare province code is not a US metro",
          C._locate("Winnipeg Chinese Cultural Centre", ", Winnipeg, MB")[1] != "United States",
          str(C._locate("Winnipeg Chinese Cultural Centre", ", Winnipeg, MB")))
    rows = C._coverage_rows()
    codes = {m for _t, _n, m, c, _cat in rows
             if c == "United States" and re.fullmatch(r"[A-Z]{2,3}", m or "")
             and m not in C._US_STATES}
    check("no US metro in the catalog is a bare code that is not a state",
          not codes, f"still codes: {sorted(codes)}")


def t_the_generators_write_a_country_a_reader_can_read():
    """The read side above is a rescue. These two are why it stops being needed:
    both backends now spell the country out in the suffix they SHIP."""
    import catalog_discover_civic as civic
    import catalog_discover_osm as osm

    suffix = civic._suffix({"city": "Bern", "region": None, "country": "CH"})
    check("civic writes the country for a town outside the US",
          C._parse_place(suffix.strip(" ,")) == ("Bern", "Switzerland"), suffix)
    us = civic._suffix({"city": "Issaquah", "region": "Washington", "country": "US"})
    check("...and leaves a US town in the shorter form 900 sources already use",
          us == ", Issaquah, Washington", us)

    metros = osm.metros()
    missing = [m for m in metros if not m.get("country_name")]
    check("every metro in the sweep knows its country's NAME", not missing,
          f"{len(missing)} without one, e.g. {missing[:2]}")
    ch = next((m for m in metros if m["country"] == "CH"), None)
    if ch:
        label = f"{ch['name']}, {ch['country_name']}"
        check("...so an ics candidate's suffix floor names a country",
              C._parse_place(label)[1] == "Switzerland", label)


def t_a_curated_file_the_report_cannot_see_reads_as_a_gap():
    """`coverage` reads CONFIG and EXTRA_CONFIG and nothing else, so a curated
    file in neither does not read as supply — it reads as empty ground, and the
    thin-ground ranker sends the next run at it. Three files were in that state
    on 2026-09-19, and each one was a country the report was FLAGging."""
    rows = C._coverage_rows()
    by_type = collections.Counter(t for t, _n, _m, _c, _k in rows)
    for t, least in (("openactive", 12), ("bibliocommons", 6), ("mapasculturais", 2)):
        check(f"{t} is counted at all", by_type.get(t, 0) >= least,
              f"{by_type.get(t, 0)} rows")

    brazil = {k for t, _n, _m, c, k in rows if c == "Brazil"}
    check("Brazil's own adapter counts as Brazilian community supply",
          "community" in brazil, f"Brazil has {sorted(brazil)}")
    uk_vol = [n for t, n, _m, c, k in rows
              if c == "United Kingdom" and k == "volunteer"]
    check("GoodGym is UK volunteer supply, which the report used to FLAG as absent",
          uk_vol, "no UK volunteer row")
    ca_lib = [n for t, n, _m, c, k in rows
              if t == "bibliocommons" and c == "Canada"]
    check("the two Canadian library systems are not American",
          len(ca_lib) == 2, f"{ca_lib}")
    # The expanders must not double-count: these three files are in no other
    # table, which is the whole reason EXPANDER_OWNED exists for the ones that
    # are.
    dupes = [t for t in ("openactive", "bibliocommons", "mapasculturais")
             if t in C.CONFIG]
    check("...and none of the three is ALSO in CONFIG, where it would count twice",
          not dupes, f"{dupes}")


def t_a_surveyed_venue_is_a_place_the_report_can_read():
    """840 entries place themselves with a `venue` block rather than a suffix —
    a surveyed OSM point, which is the BEST location data in the catalog — and
    `_locate` reads only the name and the geocode suffix. Measured 2026-09-20:
    824 of those 840 reported metro "?", which is the bucket the thin-ground
    ranker cannot tell apart from a country with no feeds at all."""
    e = {"name": "Nectar Lounge",
         "venue": {"city": "Seattle", "region": "WA", "lat": 47.65, "lon": -122.35}}
    check("a venue block that STATES its city and state locates the source",
          C._locate_entry(e, e["name"], "") == ("Seattle", "United States"),
          str(C._locate_entry(e, e["name"], "")))

    # `_metro` is what the sweep recorded about itself, and its tail is always a
    # country — never a US state, which is the opposite of a geocode suffix.
    check("`_metro` is parsed country-first: CA there is Canada, not California",
          C._parse_metro_field("Winnipeg, CA") == ("Winnipeg", "Canada"),
          str(C._parse_metro_field("Winnipeg, CA")))
    check("...and a suffix keeps the opposite rule, because CA there IS California",
          C._parse_place("Somewhere, CA") == ("Somewhere", "United States"),
          str(C._parse_place("Somewhere, CA")))
    check("a spelled-out country in `_metro` reads too",
          C._parse_metro_field("Bend, United States") == ("Bend", "United States"),
          str(C._parse_metro_field("Bend, United States")))

    rows = C._coverage_rows()
    unloc = sum(1 for _t, _n, m, _c, _k in rows if m == "?")
    check("fewer than a tenth of catalog rows have no metro",
          unloc < len(rows) / 10, f"{unloc} of {len(rows)}")


def t_a_sweep_area_is_not_a_border():
    """The fallback that nearly shipped: matching a surveyed point to the metro
    whose bbox contains it, and taking that metro's COUNTRY. A metro bbox is a
    sweep radius, not a frontier — Basel's covers Alsace and Baden, Liege's
    reaches Maastricht, Salzburg's reaches Bavaria — and it moved 24 rows to the
    wrong country, every one a border case the ccTLD had right."""
    # A point inside a swept metro still names the METRO...
    e = {"name": "Somewhere", "venue": {"lat": 47.5596, "lon": 7.5886}}   # Basel
    metro = C._observed_metro(e)
    check("a surveyed point names the metro that swept it", bool(metro), str(metro))
    # ...and must NOT name the country.
    m, c = C._locate_entry(e, e["name"], "")
    check("...and NEVER the country, so the ccTLD still decides", c == "?",
          f"geometry claimed {c!r}")

    # The venue's OWN statement is different evidence and may name a country.
    e2 = {"name": "X", "venue": {"city": "Liege", "country": "BE"}}
    check("a venue that states its country is believed",
          C._locate_entry(e2, "X", "")[1] == "Belgium",
          str(C._locate_entry(e2, "X", "")))


def t_jsonld_keeps_its_country_rule_while_gaining_a_metro():
    """`_rows_jsonld` must not start using `_locate`: that reads a name's
    parenthetical as a city and defaults it to the US, which turned
    "i45 (industrie 45)" — a club in Zug — into a US metro called
    "industrie 45"."""
    site = {"name": "i45 (industrie 45)", "category": "music",
            "listing": ["https://i45.ch/events/"],
            "venue": {"city": "Zug", "lat": 47.1822, "lon": 8.5209},
            "_metro": "Zurich, CH"}
    rows = C._rows_jsonld({"sites": [site]})
    check("a Swiss club stays in Switzerland", rows[0][2] == "Switzerland",
          str(rows[0]))
    check("...and gains the metro its venue block knows", rows[0][1] in ("Zug", "Zurich"),
          str(rows[0]))
    # The US default is still keyed to the NAME scan, not to the new metro.
    us = C._rows_jsonld({"sites": [{"name": "Songbyrd Music House (Washington DC)",
                                    "listing": ["https://songbyrddc.com/events"],
                                    "category": "music"}]})
    check("a US metro in the NAME still defaults the country to the US",
          us[0][2] == "United States", str(us[0]))


def main() -> int:
    for t in (t_all_types_lists_each_type_once,
              t_no_source_is_counted_twice,
              t_no_country_is_a_number,
              t_parkrun_lands_in_the_countries_it_actually_covers,
              t_no_country_is_a_bare_iso_code,
              t_every_iso_code_in_every_config_has_a_name,
              t_a_cctld_source_is_not_filed_under_unknown,
              t_the_country_count_matches_the_rows,
              t_a_foreign_iso_code_in_a_suffix_is_a_country,
              t_a_country_spelled_out_is_a_country_too,
              t_a_us_state_spelled_out_is_still_the_us_and_is_not_the_metro,
              t_a_two_letter_code_is_never_a_metro,
              t_the_generators_write_a_country_a_reader_can_read,
              t_a_curated_file_the_report_cannot_see_reads_as_a_gap,
              t_a_surveyed_venue_is_a_place_the_report_can_read,
              t_a_sweep_area_is_not_a_border,
              t_jsonld_keeps_its_country_rule_while_gaining_a_metro):
        print(f"\n--- {t.__name__} ---")
        t()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("all coverage-row tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
