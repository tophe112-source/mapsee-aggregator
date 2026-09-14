#!/usr/bin/env python3
"""
mapsee_spam_audit.py - how much of a source is advertising?

    python mapsee_spam_audit.py --config mobilizon_sources.json
    python mapsee_spam_audit.py --config mobilizon_sources.json --pages 3 --json out.json

mapsee_spam.py stops the individual row. This answers the different question the
row cannot: is this an ordinary calendar that one spam account found, or is it a
spam host with a calendar attached? Those want opposite decisions — the first
keeps its place in the sources file and lets the gate do its work, the second
belongs in `_not_included`, because continuing to poll it is bandwidth spent on
somebody else's SEO and a standing invitation for whatever the gate does not yet
recognise.

The distinction has to be MEASURED and not eyeballed. gamenight.host looks, from
its front page, like a games-night calendar; it is, and it also carries a French
marabout trade three pages deep. Reading the first screen would have kept it.
Reading the whole feed puts a number on it.

WHAT IT PROBES. Mobilizon instances, through the ADAPTER'S OWN query and
normaliser rather than a second implementation of them — so the population it
scores is exactly the population that would have been ingested, including the
rows dropped for having no title or no date. A second reader would drift from
the adapter within a release and start scoring a feed nobody imports.

READ IT AS A RATE, NOT A COUNT. A busy instance with twelve scam listings out of
nine hundred is healthy and the gate handles it. A quiet instance with twelve out
of forty is a host. The suggestion at the bottom is exactly that comparison and
nothing cleverer; it does not edit the config, because putting an instance in
`_not_included` is an editorial no with a reason attached, and a script cannot
write the reason.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

import mapsee_ingest_mobilizon as MOB
from mapsee_spam import implausible_end, spam_reason

# Every line here quotes a REMOTE title back at the console, and this script
# exists to read the feeds where the titles are WEIRDEST. Same rule, and the same
# reason, as the block at the top of catalog_curate.py: on a cp1252 console one
# character outside the codepage raises out of print() itself and unwinds a sweep
# that had already spent forty network round trips.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

UA = "MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me)"

# The two ends of the judgement. Between them is "one spam account got in", which
# is the case the per-row gate exists for and needs no config change at all.
HOST_RATE = 0.30          # a third of the feed or more: the calendar is the cover
HOST_MIN_ROWS = 20        # ...but not off eight listings, where one row is 12%

# BOTH SIGNALS COUNT TOWARD THE RATE, and this is the part worth stating plainly.
# The per-row gate refuses on CONTENT and merely un-dates a row whose end is
# years out, because implausible_end is not a spam verdict — but at the SOURCE
# level the distinction stops mattering. An instance publishing a hundred
# listings that run to 2032 is not publishing events, whether or not any single
# one of them says "marabout", and it is the same editorial call either way.
# meet.debian.net is the case: 71% of its feed, and not one keyword between them.


def audit_site(session, site: Dict[str, Any], pages: int) -> Dict[str, Any]:
    base = (site.get("base_url") or "").rstrip("/")
    name = site.get("name") or base
    out = {"name": name, "base_url": base, "read": 0, "spam": 0, "refused": 0,
           "undated": 0, "reasons": {}, "samples": [], "error": None}
    if not base:
        out["error"] = "no base_url"
        return out
    begins = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    limit = int(site.get("limit", 100))
    for page in range(1, pages + 1):
        payload = {"query": MOB.QUERY, "variables": {"b": begins, "l": limit, "p": page}}
        try:
            r = session.post(f"{base}/api", data=json.dumps(payload), timeout=45)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {str(exc)[:60]}"
            break
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
            break
        try:
            body = r.json()
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"bad JSON: {str(exc)[:60]}"
            break
        if body.get("errors"):
            out["error"] = f"query errors: {str(body['errors'])[:80]}"
            break
        node = (body.get("data") or {}).get("searchEvents") or {}
        rows = [e for e in (node.get("elements") or []) if e]
        if not rows:
            break
        for ev in rows:
            # THROUGH to_event, so the denominator is what the pipeline would
            # actually have seen — not the raw feed, which includes rows the
            # adapter drops for having no title or no start.
            nev = MOB.to_event(ev, site)
            if not nev:
                continue
            out["read"] += 1
            reason = spam_reason(nev.name, nev.description, nev.start_utc, nev.end_utc)
            if not reason:
                span = implausible_end(nev.start_utc or nev.start_local,
                                       nev.end_utc or nev.end_local)
                if span is not None:
                    reason = f"ends {span} days out"
                    out["undated"] += 1
            else:
                out["refused"] += 1
            if reason:
                out["spam"] += 1
                out["reasons"][reason] = out["reasons"].get(reason, 0) + 1
                if len(out["samples"]) < 5:
                    out["samples"].append(f"{reason}: {nev.name[:70]}")
        if page * limit >= int(node.get("total") or 0):
            break
        time.sleep(float(site.get("crawl_delay", 1)))
    return out


def verdict(row: Dict[str, Any]) -> str:
    if row["error"] and not row["read"]:
        return "unreachable"
    if not row["read"]:
        return "empty"
    rate = row["spam"] / row["read"]
    if row["read"] >= HOST_MIN_ROWS and rate >= HOST_RATE:
        return "SPAM HOST"
    if row["spam"]:
        return "gated"
    return "clean"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure how much of each source is advertising.")
    ap.add_argument("--config", default="mobilizon_sources.json")
    ap.add_argument("--pages", type=int, default=5, help="pages per instance (default 5)")
    ap.add_argument("--only", help="just this site name (substring match)")
    ap.add_argument("--json", dest="json_out", help="write the full result here")
    a = ap.parse_args(argv)

    cfg = json.loads(open(a.config, encoding="utf-8").read())
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Content-Type": "application/json",
                            "Accept": "application/json"})
    rows: List[Dict[str, Any]] = []
    sites = [s for s in cfg.get("sites", [])
             if not a.only or a.only.lower() in str(s.get("name", "")).lower()]
    for i, site in enumerate(sites, 1):
        row = audit_site(session, site, a.pages)
        row["verdict"] = verdict(row)
        rows.append(row)
        rate = (100.0 * row["spam"] / row["read"]) if row["read"] else 0.0
        print(f"[{i:>3}/{len(sites)}] {row['verdict']:<10} {row['spam']:>4}/{row['read']:<5} "
              f"({rate:5.1f}%  {row['refused']} refused, {row['undated']} undated)  {row['name']}"
              + (f"   [{row['error']}]" if row["error"] else ""))

    hosts = [r for r in rows if r["verdict"] == "SPAM HOST"]
    gated = [r for r in rows if r["verdict"] == "gated"]
    read = sum(r["read"] for r in rows)
    spam = sum(r["spam"] for r in rows)
    print()
    print(f"{len(rows)} source(s); {spam}/{read} rows are not events as published "
          f"({(100.0 * spam / read) if read else 0:.1f}%) — "
          f"{sum(r['refused'] for r in rows)} refused outright, "
          f"{sum(r['undated'] for r in rows)} kept with the end dropped")
    if hosts:
        print()
        print("PROPOSED for _not_included — the calendar is the cover, not the content:")
        for r in hosts:
            print(f"  {r['base_url']}   {r['spam']}/{r['read']} refused")
            for s in r["samples"][:3]:
                print(f"      {s}")
        print()
        print("  Not applied here. `_not_included` is an editorial no WITH A REASON,")
        print("  and the reason is the part a script cannot write (see catalog_curate")
        print("  ._not_included). Paste the URL and a sentence saying what you saw.")
    if gated:
        print()
        print("Keeping, and letting the per-row gate work — a real calendar somebody "
              "spammed:")
        for r in gated:
            print(f"  {r['name']}: {r['spam']}/{r['read']}")
    if a.json_out:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "sources": rows},
                  open(a.json_out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
