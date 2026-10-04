#!/usr/bin/env python3
"""
robots_txt.py - may MapseeAggregator fetch this URL? RFC 9309, read once per origin.

WHY THIS EXISTS. The rule has always been "respect robots.txt", and until
2026-09-30 nothing in the loop that ADDS sources ever read one. `verify` proved
a feed parses and has something upcoming; the weekly sweep merged whatever
passed and committed it. Measured that day against every configured request
(the URL each adapter actually fetches, not the page it was found on): 127 of
the 3,468 whose host's robots.txt could be read were disallowed for us. 30 of
them are Google calendars - calendar.google.com/robots.txt is `Allow: /$` and
`Disallow: /`, so every `calendar/ical/<id>/public/basic.ics` feed is off
limits. The rest: every LibraryCalendar tenant and 7 LibCal tenants (FIU,
Denver, UMN, Tulane, Whitby, Fresno, Johannesburg) whose `*` group is
`Disallow: /`; OpenDatasoft portals, whose stock file disallows `/api/`;
WordPress and Drupal calendars that disallow `/*?`, `/wp-json/` or `/*ical=`.
Squarespace was the same lesson learned once by hand (its stock file disallows
`?format=json`), and this is the version that does not depend on somebody
remembering to look. docs/agents/curation-and-discovery.md has the breakdown.

WHAT IT IMPLEMENTS, because the obvious implementation is wrong. Python's
urllib.robotparser does not honour `*` or `$` inside a path and takes the FIRST
matching rule rather than the longest: it reads houstonfoodbank.org's
`Disallow: *ical=*` and minneapolisparks.org's `Disallow: /*?*ical=` as
allowing the `?ical=1` exports both files refuse. Four real files from that
audit decide the rest:

  * calendar.google.com: `Allow: /$` then `Disallow: /`. `/$` is the root and
    nothing else; everything under it is disallowed.
  * torontobotanicalgarden.ca: two `User-agent: *` groups (Yoast appends its
    own). RFC 9309 MERGES them, so `Disallow: /events/` from the first still
    binds though the second says `Disallow:` with no value.
  * santafelibrary.org: a second `*` group with `Disallow: /` and then an allow
    list of named crawlers. Unnamed means disallowed.
  * houstonfoodbank.org: `Disallow: *ical=*` - a pattern that does not start
    with `/`. Google's parser matches it (the leading `*` covers the path), so
    this does too.

The longest matching pattern wins; a tie goes to Allow. A group that names our
product token (`MapseeAggregator`) replaces the `*` group entirely - none of the
2,127 readable files in the audit named us.

WHEN THE FILE CANNOT BE READ (RFC 9309 section 2.3.1):
  * 4xx - "unavailable": nothing is disallowed.
  * 5xx, a timeout, a refused connection - "unreachable": assume EVERYTHING is
    disallowed. Callers that park a source for 90 days on a refusal should read
    `status` and treat this one as transient, not as a verdict.
  * A bot challenge served ON /robots.txt: permission cannot be established,
    which catalog_probe has always called a stronger blocker than a Disallow.
    `allowed` is None and the caller decides.

Crawl-delay is not in RFC 9309, but the files that set it mean it, and the ics
and tribe adapters already pace a host by a config's `crawl_delay`. It is
reported from the group that applies, so verify can carry it into the config.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

PRODUCT_TOKEN = "mapseeaggregator"      # of "MapseeAggregator/1.0 (+https://mapsee.me; ...)"

# RFC 9309 says a crawler MUST parse at least 500 KiB. The largest file in the
# audit was 61 KB.
MAX_BYTES = 512_000

# Shared with catalog_discover_osm's list, which is where every one of these
# strings was found in the wild; copied rather than imported so this module
# stays free of the discovery code's imports.
CHALLENGE_RX = re.compile(
    r"sgcaptcha|just a moment|one moment,?\s*please|being verified|"
    r"cf-browser-verification|challenge-platform|captcha-delivery|_Incapsula_|"
    r"/cdn-cgi/challenge|checking your browser|enable javascript and cookies|"
    # Cloudflare's BLOCK page, as distinct from its challenge: "Sorry, you have
    # been blocked / You are unable to access myvscloud.com", served as the 403
    # for /robots.txt on every DC-area WebTrac tenant checked 2026-10-04 (DC
    # DPR, Arlington, Montgomery Rec, Prince William). As a bare 403 it read as
    # "no file, allow everything", so verify would have passed a host that is
    # refusing us by name.
    r"sorry,\s*you\s+have\s+been\s+blocked|attention\s+required!?\s*\|\s*cloudflare",
    re.I)

_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _normalise(s: str) -> str:
    """Percent-encoding the way RFC 9309 compares it: an escaped UNRESERVED
    character is decoded (`%7E` is `~`), every other escape is upper-cased and
    kept (`%40` stays `%40`, never `@`), and anything outside ASCII is encoded
    as UTF-8. Applied to the rule and to the path alike."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "%" and re.fullmatch(r"[0-9A-Fa-f]{2}", s[i + 1:i + 3]):
            ch = chr(int(s[i + 1:i + 3], 16))
            out.append(ch if ch in _UNRESERVED else "%" + s[i + 1:i + 3].upper())
            i += 3
            continue
        if ord(c) > 127:
            out.append("".join(f"%{b:02X}" for b in c.encode("utf-8")))
        else:
            out.append(c)
        i += 1
    return "".join(out)


def parse(text: str) -> List[Tuple[List[str], List[Tuple[str, str]]]]:
    """[(user-agent values, [(field, value)])] in file order.

    A group is one or more user-agent lines followed by its rules; a user-agent
    line AFTER a rule starts the next group. Crawl-delay counts as a rule for
    that purpose, so `User-agent: *`, `Crawl-delay: 10`, `User-agent: Googlebot`
    is two groups. Lines before any user-agent belong to nobody.

    A leading UTF-8 byte-order mark is not part of the first field name.
    reservation.frontdesksuite.ca serves BOM + `User-agent: *` + `Disallow: /`
    (2026-10-03), and with the BOM kept the first line's field read
    "\\ufeffuser-agent", matched nothing, and the whole refusal parsed as no
    groups at all - allow everything. `str.strip()` does not remove U+FEFF.
    """
    groups: List[Tuple[List[str], List[Tuple[str, str]]]] = []
    agents: List[str] = []
    rules: List[Tuple[str, str]] = []
    if text.startswith("﻿"):
        text = text[1:]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        field, value = field.strip().lower(), value.strip()
        if field in ("user-agent", "useragent"):
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif field in ("allow", "disallow", "crawl-delay") and agents:
            rules.append((field, value))
    if agents:
        groups.append((agents, rules))
    return groups


def _names_us(agent: str, token: str) -> bool:
    # The product token is the leading run of letters, `_` and `-`; RFC 9309
    # compares it case-insensitively and whole, so `Mapsee` is not us and
    # `MapseeAggregator/1.0` is.
    m = re.match(r"[a-z_-]+", agent)
    return bool(m) and m.group(0) == token


def rules_for(groups, token: str = PRODUCT_TOKEN) -> Tuple[List[Tuple[str, str]], str]:
    """(rules, which): every group naming us merged, else every `*` group
    merged, else nothing. `which` is "named", "*" or "none"."""
    named = [r for agents, rules in groups for r in rules
             if any(_names_us(a, token) for a in agents)]
    if named:
        return named, "named"
    star = [r for agents, rules in groups for r in rules if "*" in agents]
    if star or any("*" in agents for agents, _ in groups):
        return star, "*"
    return [], "none"


_RX_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _pattern(value: str) -> "re.Pattern[str]":
    rx = _RX_CACHE.get(value)
    if rx is None:
        v = _normalise(value)
        anchored = v.endswith("$")
        if anchored:
            v = v[:-1]
        rx = re.compile("^" + "".join(".*" if c == "*" else re.escape(c) for c in v)
                        + ("$" if anchored else ""))
        _RX_CACHE[value] = rx
    return rx


def verdict(rules, path: str) -> Tuple[bool, Optional[str]]:
    """(allowed, the rule that decided it). Longest match wins, Allow wins a
    tie, an empty value is no rule, no match is allowed."""
    path = _normalise(path or "/")
    best: Optional[Tuple[int, bool, str]] = None
    for field, value in rules:
        if field not in ("allow", "disallow") or not value:
            continue
        if _pattern(value).match(path):
            length, allow = len(_normalise(value)), field == "allow"
            if best is None or length > best[0] or (length == best[0] and allow and not best[1]):
                best = (length, allow, f"{field.capitalize()}: {value}")
    if best is None:
        return True, None
    return best[1], best[2]


def crawl_delay(rules) -> Optional[float]:
    delays = []
    for field, value in rules:
        if field == "crawl-delay":
            try:
                delays.append(float(value))
            except ValueError:
                continue
    return max(delays) if delays else None


def request_path(url: str) -> str:
    """What a rule is matched against: the path plus its query."""
    p = urlparse(url)
    return (p.path or "/") + ("?" + p.query if p.query else "")


def origin_of(url: str) -> str:
    u = "https://" + url[9:] if url.lower().startswith("webcal://") else url
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}".lower()


class Robots:
    """robots.txt per origin, fetched once per instance with the caller's
    session (so the User-Agent it is read with is the one it is judged for).

    check(url) -> {"allowed": True | False | None, "status": ok | html |
    http<4xx> | unreachable | challenge, "rule": the deciding line or None,
    "crawl_delay": float or None, "robots": the robots.txt URL}.
    """

    def __init__(self, session, timeout: float = 15.0):
        self.session = session
        self.timeout = timeout
        self._files: Dict[str, Dict[str, Any]] = {}
        self.retried = self.recovered = 0

    def _load(self, origin: str) -> Dict[str, Any]:
        got = self._files.get(origin)
        if got is not None:
            return got
        url = origin + "/robots.txt"
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            body = r.content[:MAX_BYTES].decode("utf-8", "replace")
            code = r.status_code
        except Exception as exc:                                  # noqa: BLE001
            got = {"status": "unreachable", "note": type(exc).__name__, "robots": url}
            self._files[origin] = got
            return got
        if CHALLENGE_RX.search(body[:6000]) and not re.search(r"(?im)^\s*user-agent\s*:", body):
            got = {"status": "challenge", "code": code, "robots": url}
        elif 200 <= code < 300:
            html = bool(re.match(r"\s*<(?:!doctype|html)", body, re.I))
            got = {"status": "html" if html else "ok", "code": code, "robots": url,
                   "groups": [] if html else parse(body)}
        elif 400 <= code < 500:
            got = {"status": f"http{code}", "code": code, "robots": url, "groups": []}
        else:
            got = {"status": "unreachable", "code": code, "robots": url}
        self._files[origin] = got
        return got

    def prefetch(self, urls, workers: int = 16, make_session=None) -> None:
        """Read every origin's file in parallel, so check() answers from memory.

        For a whole-catalog audit: 2,372 origins one after another is twenty
        minutes of waiting on other people's servers, and one Session shared
        across threads is not something requests promises to survive. Each
        worker gets its own, carrying the same headers.
        """
        import concurrent.futures as cf
        origins = sorted({origin_of(u) for u in urls} - set(self._files))
        if make_session is None:
            import requests
            headers = dict(self.session.headers)

            def make_session():
                s = requests.Session()
                s.headers.update(headers)
                return s

        def one(origin):
            return origin, Robots(make_session(), self.timeout)._load(origin)

        with cf.ThreadPoolExecutor(max(1, workers)) as ex:
            for origin, got in ex.map(one, origins):
                self._files[origin] = got
        # A BURST IS ITSELF WHAT GETS CHALLENGED. Sixteen connections at once
        # from one address is what a shared host's WAF answers with its "One
        # moment, please" page, and the same host hands over an ordinary
        # robots.txt when asked alone. So nothing that came back challenged or
        # unreachable is believed until it has been asked once more, one at a
        # time - the ingest adapters never burst, so the quiet answer is the one
        # they would get.
        again = [o for o in origins if self._files[o]["status"] in ("challenge", "unreachable")]
        self.retried, self.recovered = len(again), 0
        quiet = Robots(make_session(), self.timeout)
        for origin in again:
            got = quiet._load(origin)
            if got["status"] not in ("challenge", "unreachable"):
                self.recovered += 1
            self._files[origin] = got

    def check(self, url: str) -> Dict[str, Any]:
        f = self._load(origin_of(url))
        out = {"status": f["status"], "robots": f["robots"], "rule": None, "crawl_delay": None}
        if f["status"] == "challenge":
            return dict(out, allowed=None)
        if f["status"] == "unreachable":
            return dict(out, allowed=False, rule="(robots.txt unreachable: RFC 9309 says assume Disallow: /)")
        rules, which = rules_for(f.get("groups") or [])
        allowed, rule = verdict(rules, request_path(url))
        return dict(out, allowed=allowed, rule=rule, group=which, crawl_delay=crawl_delay(rules))
