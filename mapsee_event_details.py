"""Optional, source-specific supplements to a thin public calendar.

Racine Art Museum's iCal DESCRIPTION is empty, though its own event pages have
an overview, a full PostalAddress and ticket status. Read that existing surface
with the identified crawler and robots permission; keep the ICS identity/time.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

from mapsee_ingest import extract_jsonld_events
from robots_txt import Robots


class _Sections(HTMLParser):
    """Only the event's overview and ticket block, never nav/footer/signup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = {"overview": [], "buy-tickets": []}
        self.depth = 0
        self.active = None
        self.at = None

    def handle_starttag(self, tag, attrs):
        if tag not in {"br", "img", "input", "hr", "meta", "link"}:
            self.depth += 1
        section = dict(attrs).get("id")
        if section in self.sections:
            self.active, self.at = section, self.depth
        if self.active and tag in {"p", "div", "h2", "br"}:
            self.sections[self.active].append("\n")

    def handle_endtag(self, tag):
        if self.active and tag in {"p", "div", "h2"}:
            self.sections[self.active].append("\n")
        if self.active and self.depth == self.at:
            self.active = self.at = None
        if tag not in {"br", "img", "input", "hr", "meta", "link"}:
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.active:
            self.sections[self.active].append(data)

    def lines(self, section):
        return [re.sub(r"\s+", " ", line).strip() for line in
                "".join(self.sections[section]).splitlines() if line.strip()]


def _title(value):
    return html.unescape(str(value or "")).replace("\\'", "'").strip().casefold()


def _instant(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def ramart_details(body, url, title, starts_at):
    """Facts from the matching occasion, or None when the URL is stale/wrong."""
    obj = next((event for event in extract_jsonld_events(body)
                if _title(event.get("name")) == _title(title)
                and _instant(event.get("startDate")) == _instant(starts_at)
                and _instant(starts_at) is not None), None)
    if obj is None:
        return None
    sections = _Sections()
    sections.feed(body)
    overview = [line for line in sections.lines("overview") if line != "Overview"]
    tickets = sections.lines("buy-tickets")
    ticket_text = " ".join(tickets)
    details, lead = {}, []
    performers = []
    for line in overview:
        artist = re.fullmatch(r"Instructor/Artist:\s*(.+)", line)
        if artist and len(artist[1]) <= 200:
            performers.append({"type": "Person", "name": artist[1]})
    if performers:
        details["performers"] = performers[:10]
    # The source's schema.org performer='Organization' is a TYPE PLACEHOLDER,
    # not a named performer. Only its explicit Instructor/Artist label counts.
    offer = {"url": url}
    sold_out = bool(re.search(r"\b(?:this class is )?sold out\b", ticket_text, re.I))
    if sold_out:
        offer["availability"] = "SoldOut"
        lead.append("This class is sold out.")
    # Public non-member price. A RAM-member rate has eligibility conditions and
    # must not be presented as the admission price available to every visitor.
    price = re.search(r"\$(\d+(?:\.\d{1,2})?)\s+Non-Member\b", ticket_text, re.I)
    if price:
        offer.update(price=price[1], currency="USD")
        lead.append(f"Admission: USD {price[1]} (non-member).")
    # This is the museum's explicitly named free family festival, not a generic
    # 'free fall' keyword match. Do not weaken the shared free-title classifier.
    if _title(title) == "free fall family fun fest" and not price and not sold_out:
        offer.update(price="0", currency="USD")
        details["free"] = True
        lead.append("Free admission.")
    if price or sold_out or details.get("free"):
        details["offer"] = offer
    address = (obj.get("location") or {}).get("address") or {}
    out = {"source_details": details}
    for prop, column in (("streetAddress", "address"), ("addressLocality", "city"),
                         ("addressRegion", "region"), ("postalCode", "postal_code"),
                         ("addressCountry", "country")):
        if isinstance(address.get(prop), str) and address[prop].strip():
            out[column] = address[prop].strip()
    # Keep actionable status first so the existing 800-character prose cap
    # cannot hide it behind the museum's long registration/refund policy.
    summary = lead + overview[:4]
    out["description"] = "\n\n".join(summary) or None
    return out


def read_ramart_details(session, robots, url, title, starts_at):
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname != "www.ramart.org" or not parsed.path.startswith("/event/"):
        return None
    permission = robots.check(url)
    if permission.get("allowed") is not True:
        return None
    time.sleep(max(1, permission.get("crawl_delay") or 0))
    try:
        response = session.get(url, timeout=20, allow_redirects=False)
        if response.status_code != 200 or len(response.content) > 1_000_000:
            return None
        # The museum declares UTF-8; requests defaults to Latin-1 for charsetless
        # text/html. Preserve actual artist names, not mojibake.
        return ramart_details(response.content.decode("utf-8", "replace"), url, title, starts_at)
    except Exception as exc:
        print(f"[event details] {url}: {type(exc).__name__}", flush=True)
        return None


def ramart_reader(session):
    robots = Robots(session)
    return lambda url, title, starts_at: read_ramart_details(session, robots, url, title, starts_at)
