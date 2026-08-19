#!/usr/bin/env python3
"""
catalog_probe.py — what does this URL actually serve US, and from where?

A diagnostic, not an ingester. It answers the question that keeps stopping
curation dead: "we cannot read this site — is that the site's decision, or is it
about the address we are calling from?"

The two are constantly confused and they want opposite responses. A publisher
who has turned bot management on is a NO we honour and record. But a SiteGround
or Cloudflare WAF scoring a datacenter IP is not a decision anybody made about
mapsee — the same page answers fine from somewhere else, and the aggregator's
own CI runs from somewhere else. dmhsus.org is the worked example: it answers
202 with an `sgcaptcha` body on every path including /robots.txt, and the
challenge URL it redirects to carries THE CALLER'S OWN IP ADDRESS in its `y=`
parameter (`y=ipr:<ip>` on one probe, `y=ipc:<ip>` on the next). What those
letter codes mean is SiteGround's business and this does not guess; the
observable fact is that the address is what the challenge is keyed on, and the
address is a datacenter one. Changing path or User-Agent changes nothing.

So this prints what it sees and WHERE IT SAW IT FROM, and the useful thing is to
run it in two places and compare:

    python catalog_probe.py https://dmhsus.org/events/          # here
    # ...and the same, dispatched through .github/workflows/probe-url.yml

Same output from a runner and a laptop means the site is closed to us and the
answer belongs in a config's `_not_included`. Different output means the block
is about the address, the production pipeline can read the site perfectly well,
and refusing it here would have thrown away a working calendar.

It fetches, it never negotiates: no browser impersonation, no retry with a
different User-Agent, no cookie games. The production UA, once per URL, and a
report of whatever came back.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

import catalog_discover_osm as osm

UA = "Mozilla/5.0 (compatible; MapseeAggregator/1.0; +https://mapsee.me; events@mapsee.me)"


def _egress_ip(session) -> str:
    """The address the far end sees. Half the point of the report."""
    for url, key in (("https://api.ipify.org?format=json", "ip"),
                     ("https://ifconfig.me/all.json", "ip_addr")):
        try:
            r = session.get(url, timeout=10)
            if r.status_code == 200:
                return str(r.json().get(key) or "?")
        except Exception:                                         # noqa: BLE001
            continue
    return "unknown"


def robots(session, url: str) -> dict:
    """What the site's robots.txt says — and whether it can be read at all.

    A challenge served on /robots.txt is its own finding: permission cannot be
    ESTABLISHED, which is a stronger blocker than a Disallow. A Disallow can be
    honoured; an unreadable robots file leaves nothing to honour.
    """
    o = urlparse(url)
    out = {"url": f"{o.scheme}://{o.netloc}/robots.txt"}
    try:
        r = session.get(out["url"], timeout=15)
    except Exception as exc:                                      # noqa: BLE001
        return dict(out, status=None, verdict=f"unreachable ({type(exc).__name__})")
    out["status"] = r.status_code
    if osm.CHALLENGE_RX.search(r.text[:6000]):
        return dict(out, verdict="CHALLENGED — permission cannot be established")
    if r.status_code != 200:
        return dict(out, verdict=f"http {r.status_code} (no robots = nothing disallowed)")
    star = re.split(r"(?im)^user-agent:\s*\*\s*$", r.text)
    rules = []
    if len(star) > 1:
        for line in star[1].splitlines():
            if re.match(r"(?i)^user-agent:", line):
                break
            if re.match(r"(?i)^(dis)?allow:", line.strip()):
                rules.append(line.strip())
    return dict(out, verdict="readable", star_group=rules[:12] or ["(no rules for *)"])


def probe(session, url: str) -> dict:
    rep = {"url": url, "robots": robots(session, url)}
    try:
        r = session.get(url, timeout=25, allow_redirects=True)
    except Exception as exc:                                      # noqa: BLE001
        rep["fetch"] = {"status": None, "verdict": f"unreachable ({type(exc).__name__})"}
        return rep
    challenged = bool(osm.CHALLENGE_RX.search(r.text[:6000]))
    rep["fetch"] = {"status": r.status_code, "final_url": str(r.url),
                    "bytes": len(r.content),
                    "verdict": "CHALLENGED" if challenged else
                               ("ok" if r.status_code < 400 else f"http {r.status_code}")}
    if challenged or r.status_code >= 400:
        # Say WHY where the challenge tells us, because "ipr:" changes the answer.
        m = re.search(r"(sgcaptcha[^\"'<>\s]*|/cdn-cgi/challenge[^\"'<>\s]*)", r.text)
        if m:
            rep["fetch"]["challenge_hint"] = m.group(1)[:160]
        return rep
    labels, ics = osm.fingerprint(r.text)
    o = urlparse(str(r.url))
    rep["platform"] = {
        "labels": labels or ["(none detected)"],
        "adapter": osm.adapter_for(labels),
        "feed": ics or osm.constructed_feed(session, f"{o.scheme}://{o.netloc}",
                                            labels, cal=str(r.url)),
    }
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    a = ap.parse_args(argv)

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en;q=0.9"})
    where = _egress_ip(s)
    reports = [probe(s, u) for u in a.urls]
    if a.json:
        print(json.dumps({"egress_ip": where, "reports": reports}, indent=1))
        return 0
    print(f"probing from egress IP {where}\n")
    for rep in reports:
        print(f"=== {rep['url']}")
        rb = rep["robots"]
        print(f"  robots.txt : {rb.get('status')} — {rb['verdict']}")
        for line in rb.get("star_group", [])[:6]:
            print(f"               {line}")
        f = rep["fetch"]
        print(f"  page       : {f.get('status')} — {f['verdict']} ({f.get('bytes','?')} bytes)")
        if f.get("challenge_hint"):
            print(f"  challenge  : {f['challenge_hint']}")
            if re.search(r"y=[a-z]+:\d+\.\d+\.\d+\.\d+", f["challenge_hint"]):
                print("               ^ the challenge is keyed on the CALLER'S IP, which is")
                print("                 in that parameter. Re-probe from elsewhere before")
                print("                 recording this as the publisher's decision.")
        p = rep.get("platform")
        if p:
            print(f"  platform   : {'+'.join(p['labels'])}  -> adapter {p['adapter']}")
            print(f"  feed       : {p['feed'] or '(none constructed)'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
