#!/usr/bin/env python3
"""
mapsee_spam.py - the one predicate that decides an ingested row is not an event.

WHY THIS IS NOT A SOURCE BLOCKLIST. The row that prompted it was a black-magic
advertisement — "Shatru Nashak Sudarshan Chakra Maran Mantra ॐ ☎ +91 9965500027",
running from April 2026 to April 2036, pinned to Northeast Juanita Drive in
Kirkland — and it arrived through an ordinary, well-behaved Mobilizon instance
whose feed parses perfectly and answers every probe. Verification proves a feed
WORKS; it has never had anything to say about what a stranger posted to it.

So the defence has to sit BELOW the source list, because the source list can only
ever be a list of instances somebody has already been burned by. Any federated,
open-registration platform — Mobilizon, Gancio, Mobilizon's fediverse peers, a
self-hosted Tribe calendar with public submissions — is one spam account away
from the same thing, and there are 74 Mobilizon instances configured. One of them
having been noticed is not a system.

WHAT IT REFUSES, AND WHY EACH ONE IS SAFE

  * A DIALLING CODE IN THE TITLE. Every scam listing in the sample carried its
    own phone number where the event name goes, because the phone number IS the
    product: "+229 01 99 13 18 40", "0022991113322 ou +22953077815", "+91
    9965500027". Real event titles do not, and the shape is unambiguous — an
    explicit international prefix, or ten-plus digits with no separators at all.

    The naive version of this check is the dangerous one. "\\d[\\d\\s.()-]{7,}\\d"
    reads a date range — "2026-05-16 - 2026-05-18" — as a sixteen-digit phone
    number, because a space and a hyphen are both plausible number separators.
    That is why this wants a PREFIX or an unbroken run, and why the tests below
    pin dates, years, times and postcodes as the things it must not touch.

  * A HANDFUL OF PHRASES THAT ARE NEVER AN EVENT. "marabout", "vashikaran",
    "retour affectif", "portefeuille magique", "kala jadu", "buy … accounts".
    Deliberately tiny and deliberately specific: a keyword list is the part of
    this that rots, and every entry has to be a phrase that cannot plausibly
    name a gathering somebody would go to. "astrologer" is NOT on it — an
    astronomy society talk is a real event — and "spiritual", "psychic",
    "healing" and "tarot" are not either, because a tarot night at a bar is a
    perfectly ordinary listing and the point is not to have opinions about what
    people are into.

AND ONE THING IT DOES NOT REFUSE, IT CORRECTS. The Kirkland listing ran for ten
years; the same instance carries a limousine service running to 2032 and a
printer-driver ad running to 2134. An end date that far out is not a long
festival, and it matters more than it looks: mapsee_cleanup.py deletes rows where
`starts_at < cutoff AND (ends_at IS NULL OR ends_at < cutoff)`, so a pin that ends
in 2036 is one NOTHING IN THIS PIPELINE WILL EVER REMOVE. Same hazard CLAUDE.md
records for unbounded recurrence, arriving through a different door.

The first version of this rejected on span, and the audit immediately found the
false positive that says why it must not: Extinction Rebellion France publish
"Organiser un évènement à La Perm" over 487 days, which is a real thing a real
person can go to. There is no threshold that separates it from a 447-day advert
for Roman blinds, because the span is not what makes one of them an advert.

So an implausible end is treated as what it is — a FACT WE CANNOT USE, not a
verdict about the row. `implausible_end()` says so and the store drops the end
rather than the event. Two things follow, and the second is the good one: the
sync fills a default duration for a row with no end (mapsee_supabase_sync.py:640),
and cleanup can then reach it. Every one of those XR adverts starts in 2021-2024,
so dropping an end nobody could have meant makes the EXISTING cleanup delete them
on its next pass. The blind advert survives as a normal-length listing, and the
thing that catches it is the source-level rate in mapsee_spam_audit.py.

WHAT IT DELIBERATELY DOES NOT DO. It does not judge topic, language, taste or
belief. A prayer meeting, a psychic fair and a Reiki workshop are events, and
somebody wants to find them. The three signals above are about the SHAPE of an
advertisement, not its subject, which is the only version of this that can be
run over 41 adapters without quietly deciding what belongs on the map.

Used by EventStore.upsert in mapsee_ingest.py — the single choke point every
adapter passes through — and by mapsee_spam_audit.py, which measures a source's
spam rate so the editorial call about a whole instance is a number rather than
an impression. test_spam.py is the pin.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

# An explicit international prefix, then 9+ digits with only the separators a
# person actually types between them. "+91 9965500027", "00229 91 11 33 22".
_INTL_PHONE = re.compile(r"(?:\+|\b00)\s?\d(?:[\s.()/-]?\d){8,}")
# ...or an unbroken run of 10+ digits, which no date, year, time or postcode is.
# Bounded on both sides so a longer numeric token (an id, a hash) does not match
# a window inside itself and report a phone number that is not there.
_BARE_PHONE = re.compile(r"(?<!\d)\d{10,}(?!\d)")
# Note what is NOT here: "WhatsApp", "Telegram", "call now". A venue that
# honestly takes WhatsApp bookings says so, and the number beside it is what the
# patterns above already see — adding the word would only catch the honest one.

# Phrases that are not a topic, they are a trade. Every one of these was read off
# the live feed before being added; none of them can name a gathering.
_SCAM_PHRASES = (
    "marabout",
    "vashikaran",
    "retour affectif",
    "retour d'affection",
    "retour d affection",
    "portefeuille magique",
    "porte-monnaie magique",
    "porte monnaie magique",
    "portefeuille mystique",
    "multiplication d'argent",
    "money multiplication",
    "magic wallet",
    "kala jadu",
    "black magic specialist",
    "love back specialist",
    "love problem solution",
    "all problem solution",
    "husband wife problem",
    "intercaste marriage problem",
    "maran mantra",
)
_SCAM_RX = re.compile("|".join(re.escape(p) for p in _SCAM_PHRASES), re.IGNORECASE)
# EVERY APOSTROPHE A KEYBOARD OR A CMS CAN PRODUCE IS THE SAME APOSTROPHE.
# "retour d'affection" and "retour d’affection" are one phrase; the curly one is
# what a CMS autocorrects to and therefore what most of the live sample actually
# carries, so a list written with ASCII quotes matches the minority spelling. The
# same normalisation flattens whitespace, because "retour  affectif" across a
# line break is not a different phrase either.
_APOSTROPHES = str.maketrans({"‘": "'", "’": "'", "ʼ": "'",
                              "´": "'", "`": "'", "′": "'"})


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").translate(_APOSTROPHES))

# "Buy Gmail Accounts SEO", "buy verified paypal accounts". The trade in bulk
# accounts, which is never local and never an event.
_ACCOUNT_TRADE_RX = re.compile(
    r"\bbuy\b[^.\n]{0,40}\b(?:accounts?|followers?|likes|reviews|backlinks?)\b", re.IGNORECASE)

# A YEAR AND A BIT. Long enough for a museum exhibition, a season ticket or a
# year-long residency — all real, all in the catalogue — and short enough that a
# pin nothing will ever clean up cannot be created. 400 matches the horizon
# mapsee_ingest_mylisting.py already uses for expanded recurrence, deliberately:
# the two limits are the same fact ("beyond here we are inventing map furniture")
# seen from the ingest side and the storage side.
#
# Crossing it costs the END DATE, never the event — see the module header for the
# 487-day Extinction Rebellion listing that is exactly why.
MAX_SPAN_DAYS = 400


def _phone_in(text: str) -> bool:
    # Both patterns read the text AS WRITTEN. Nothing is stripped first, which is
    # the whole defence: normalise separators out and "2026-05-16 - 2026-05-18"
    # becomes the sixteen-digit number this must never see.
    return bool(_INTL_PHONE.search(text) or _BARE_PHONE.search(text))


def spam_reason(name: Optional[str],
                description: Optional[str] = None,
                start: Optional[str] = None,
                end: Optional[str] = None) -> Optional[str]:
    """Why this row is an advertisement, or None if it is an event.

    The string is for logs and audits: it names the signal that fired so a false
    positive can be argued with, rather than "rejected" with nothing to inspect.
    """
    title = _flatten(name).strip()
    body = _flatten(description)

    if title and _phone_in(title):
        return "phone number in title"
    if _SCAM_RX.search(title):
        return "scam phrase in title"
    if _ACCOUNT_TRADE_RX.search(title):
        return "bulk-account trade in title"

    # The description is scanned for the phrases but NOT for a phone number: an
    # organiser putting a contact number in the blurb is ordinary and correct,
    # and it is only in the TITLE that a number is the product being sold.
    if body:
        if _SCAM_RX.search(body):
            return "scam phrase in description"
        if _ACCOUNT_TRADE_RX.search(body):
            return "bulk-account trade in description"

    return None


def implausible_end(start: Optional[str], end: Optional[str]) -> Optional[int]:
    """The span, when the end is too far out to be a fact — else None.

    Not a spam verdict and deliberately not part of spam_reason: the caller drops
    the END, keeps the EVENT, and lets the sync's default duration and
    mapsee_cleanup do the rest. Returns the span so the log can say how far out
    the claim was, which is the number somebody would want when arguing that 400
    is too tight.
    """
    span = span_days(start, end)
    return span if (span is not None and span > MAX_SPAN_DAYS) else None


def span_days(start: Optional[str], end: Optional[str]) -> Optional[int]:
    """Whole days between two ISO timestamps, or None if either is unreadable.

    Date-only is enough and is all that is compared: the question is "is this a
    classified ad" and no answer to it turns on the hour. Parsing by hand rather
    than through datetime.fromisoformat because the stores carry every spelling
    an adapter has ever produced — 'Z', '+00:00', a bare date — and a
    ValueError here would have to be caught anyway and mean the same thing.
    """
    a, b = _ymd(start), _ymd(end)
    if a is None or b is None:
        return None
    try:
        return (dt.date(*b) - dt.date(*a)).days
    except ValueError:
        return None


_YMD = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def _ymd(s: Optional[str]):
    m = _YMD.match(str(s or ""))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return y, mo, d


def is_spam(name, description=None, start=None, end=None) -> bool:
    return spam_reason(name, description, start, end) is not None
