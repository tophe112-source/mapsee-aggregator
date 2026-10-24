#!/usr/bin/env python3
"""
kct_places_build.py - build kct_places.json, the table of Czech places that
mapsee_ingest_kct.py checks a club's pin against (its rule 2).

    python kct_places_build.py [--config kct_sources.json] [--out kct_places.json]

WHAT IT HOLDS. For each of the 77 districts in kct_sources.json (76 okresy and
Prague), every municipality (obec, Wikidata P31 Q5153359) and every part of
one (část obce: an item with a Czech neighbourhood ID, P2788) that has a
Wikidata point (P625), keyed by the slug of its Czech name:

    {"places": {"pisek": {"hrejkovice": [[49.4744, 14.2908, 2.4]], ...}, ...}}

Each point carries its REACH in km: the radius of a circle of the place's own
area (P2046), so Prague (496 km2) reaches 12.6 km and a village with no area
on Wikidata 0. A name held twice in one district (two villages called Babice)
keeps both points. Hills and city quarters are NOT in it: measured 2026-10-24,
adding Wikidata's hills (Q8502, Q54050) and quarters (Q123705) checked 16 more
pins and refused two good ones, because "Kamenná bouda" is a stone hut and the
Homolka a walk names is not the Homolka Wikidata holds.

Three SPARQL reads of the Wikidata Query Service (a documented public API) and
one more for parents that are neither a municipality nor a district (Prague's
and Brno's city districts): about half a minute. Run it again when a district
or municipality changes; the adapter itself never queries Wikidata.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date
from typing import Any, Dict, List, Set

from mapsee_ingest import USER_AGENT
from mapsee_ingest_kct import slug

ENDPOINT = "https://query.wikidata.org/sparql"
PRAHA = "Q1085"


def sparql(session, query: str) -> List[Dict[str, Any]]:
    for attempt in (1, 2):
        r = session.post(ENDPOINT, data={"query": query, "format": "json"}, timeout=180)
        if r.status_code in (429, 503) and attempt == 1:
            wait = r.headers.get("Retry-After") or ""
            time.sleep(min(int(wait) if wait.isdigit() else 30, 75))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"wikidata http {r.status_code}: {r.text[:160]}")
        return r.json()["results"]["bindings"]
    return []


def qid(b: Dict[str, Any], var: str) -> str:
    return b[var]["value"].rsplit("/", 1)[1]


def point(b: Dict[str, Any]) -> List[float]:
    lon, lat = map(float, re.findall(r"[-\d.]+", b["c"]["value"])[:2])
    return [lat, lon]


def district_key(label: str, q: str) -> str:
    return "hlavni-mesto-praha" if q == PRAHA else slug(re.sub(r"^okres\s+", "", label))


def build(session, want: Set[str]) -> Dict[str, Any]:
    rows = sparql(session, """
        SELECT ?d ?dLabel WHERE {
          { ?d wdt:P31 wd:Q548611 . FILTER NOT EXISTS { ?d wdt:P576 [] } } UNION { VALUES ?d { wd:%s } }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "cs". } }""" % PRAHA)
    dkey = {qid(b, "d"): district_key(b["dLabel"]["value"], qid(b, "d")) for b in rows}
    dkey = {q: k for q, k in dkey.items() if k in want}
    missing = want - set(dkey.values())
    if missing:
        raise SystemExit(f"districts in the config that Wikidata did not give: {sorted(missing)}")
    values = " ".join("wd:" + q for q in dkey)

    # item -> the districts it lies in
    within: Dict[str, Set[str]] = {q: {k} for q, k in dkey.items()}
    places: Dict[str, Dict[str, Any]] = {}

    def add(q: str, label: str, latlon: List[float], area: Any) -> None:
        rec = places.setdefault(q, {"name": label, "ll": latlon, "area": None})
        if area and float(area) > (rec["area"] or 0.0):          # P2046 can hold several
            rec["area"] = float(area)

    # Prague is a district and a municipality in one item, so the query below
    # (municipalities IN a district) does not return it.
    for b in sparql(session, """
        SELECT ?c ?area WHERE { wd:%s wdt:P625 ?c . OPTIONAL { wd:%s wdt:P2046 ?area } }""" % (PRAHA, PRAHA)):
        add(PRAHA, "Praha", point(b), b.get("area", {}).get("value"))

    for b in sparql(session, """
        SELECT ?o ?oLabel ?d ?c ?area WHERE {
          VALUES ?d { %s } ?o wdt:P31 wd:Q5153359; wdt:P131 ?d; wdt:P625 ?c .
          OPTIONAL { ?o wdt:P2046 ?area }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "cs". } }""" % values):
        within.setdefault(qid(b, "o"), set()).add(dkey[qid(b, "d")])
        add(qid(b, "o"), b["oLabel"]["value"], point(b), b.get("area", {}).get("value"))

    parts = sparql(session, """
        SELECT ?x ?xLabel ?p ?c ?area WHERE {
          ?x wdt:P2788 ?id; wdt:P625 ?c .
          OPTIONAL { ?x wdt:P131 ?p } OPTIONAL { ?x wdt:P2046 ?area }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "cs". } }""")
    part_ids = {qid(b, "x") for b in parts}
    parents = {qid(b, "p") for b in parts if "p" in b} - set(within) - part_ids
    if parents:
        for b in sparql(session, """
            SELECT DISTINCT ?t ?d WHERE { VALUES ?t { %s } VALUES ?d { %s } ?t wdt:P131+ ?d . }"""
                        % (" ".join("wd:" + q for q in sorted(parents)), values)):
            within.setdefault(qid(b, "t"), set()).add(dkey[qid(b, "d")])
    # A part's parent can be another part (or the municipality's own item):
    # carry the districts down until nothing changes.
    changed = True
    while changed:
        changed = False
        for b in parts:
            if "p" not in b:
                continue
            up = within.get(qid(b, "p"))
            if up and not up <= within.setdefault(qid(b, "x"), set()):
                within[qid(b, "x")] |= up
                changed = True
    for b in parts:
        if within.get(qid(b, "x")):
            add(qid(b, "x"), b["xLabel"]["value"], point(b), b.get("area", {}).get("value"))

    table: Dict[str, Dict[str, Dict[tuple, float]]] = {}
    for q, rec in places.items():
        name = slug(rec["name"])
        if not name or re.fullmatch(r"q\d+", name):       # no Czech label
            continue
        reach = round(math.sqrt(rec["area"] / math.pi), 1) if rec["area"] else 0.0
        ll = (round(rec["ll"][0], 4), round(rec["ll"][1], 4))
        for k in within.get(q, ()):
            pts = table.setdefault(k, {}).setdefault(name, {})
            pts[ll] = max(pts.get(ll, 0.0), reach)
    return {k: {n: [[la, lo, r] for (la, lo), r in sorted(pts.items())] for n, pts in sorted(names.items())}
            for k, names in sorted(table.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build kct_places.json from Wikidata.")
    ap.add_argument("--config", default="kct_sources.json")
    ap.add_argument("--out", default="kct_places.json")
    a = ap.parse_args(argv)
    import requests
    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    table = build(session, set(cfg["districts"]))
    names = sum(len(v) for v in table.values())
    points = sum(len(p) for v in table.values() for p in v.values())
    head = {
        "_comment": "Czech municipalities and their parts, per district, for mapsee_ingest_kct.py's rule 2: "
                    "{district: {name slug: [[lat, lon, reach_km], ...]}}. Built by kct_places_build.py from "
                    "Wikidata (CC0); reach_km is the radius of a circle of the place's own area. Do not edit "
                    "by hand: rebuild.",
        "_built": f"{date.today().isoformat()}: {len(table)} districts, {names} names, {points} points",
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("{\n")
        for k, v in head.items():
            fh.write(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n")
        fh.write('"places": {\n')
        fh.write(",\n".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                            for k, v in table.items()))
        fh.write("\n}}\n")
    print(head["_built"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
