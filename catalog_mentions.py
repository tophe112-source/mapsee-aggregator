#!/usr/bin/env python3
"""
catalog_mentions.py - named events our own feeds mention and the map does not carry.

    python catalog_mentions.py --stores <files or dirs> --queue mention_queue.json [--today YYYYMMDD]
    python catalog_mentions.py --stores stores/ --queue mention_queue.json --save-hits hits.json
    python catalog_mentions.py --hits hits.json --queue mention_queue.json      # re-apply only

WHY THIS EXISTS. Short Run Comix Festival (Seattle, free, one day,
2026-11-07 11:00-18:00 at the Seattle Design Center) was never on the map, and
no discovery channel could have found it: it publishes its date as WordPress
prose on its own site, and every backend in catalog_curate.py starts from a
catalog of DATASETS, a directory of INSTANCES, a city or a VENUE - none from a
festival's organiser (research of 2026-11-07, recorded in
docs/agents/curation-and-discovery.md). But a feed we ALREADY read carried it a
month ahead. The Seattle Public Library's Trumba feed (ics_sources.json) holds
"The Public Life of Comics" (2026-11-08, Central Library), whose DESCRIPTION
says "in coordination with the 14th annual Short Run Comix Festival, taking
place on Saturday, November 7th from 11 am to 6 pm at Seattle Design Center in
Georgetown". That row was live on the map; nothing read the festival out of it.

So this reads every row the daily ingest already wrote and asks one question:
does its prose name ANOTHER event - a festival, fair, expo, con, convention,
biennial, art walk, open studios or week - in a frame that says it is a
separate occasion ("in coordination with", "as part of", "during", "the 14th
annual ...")? Each such name is a lead for a human: a festival whose own site
is prose-only belongs in annual_sources.json (read every run by
mapsee_ingest_annual.py), and one that turns out to have a feed belongs in the
ordinary catalog. This file proposes; it never configures anything.

WHAT IT KEEPS, in mention_queue.json (committed, sorted, so a run with nothing
new changes nothing):

    {"_comment", "_not_included": {name: reason},
     "mentions": {key: {name, state, first_seen, last_seen, times_seen, dates,
                        venue, referrers: [<=5], sample, ref_keys}}}

  state  pending     a lead nobody has looked at
         covered     some row in the stores carries the name AS A TITLE within
                     COVER_DAYS of a mentioned date (or at all, when no date
                     was mentioned): the map already has it. Re-decided on
                     every run that names it, so next year's edition, not yet
                     on the map, goes back to pending
         configured  a site in annual_sources.json has a `name_rx` that
                     matches the name
         declined    a human said no (set by hand; never changed here)

  `site` is the annual_sources.json key that made a mention configured.
  `times_seen` counts DISTINCT referring rows, ever. `ref_keys` is what makes
  that countable without a row being counted again each day: a short hash per
  referring row. `last_seen` moves only when a new referring row, a new date,
  a venue or a change of state is recorded, which is what "nothing new
  changes nothing" requires.

  Pending and covered mentions are pruned alike: a dated one when every date
  ended more than PRUNE_DAYS ago and no row named it this run (undated, or
  with a current date); an undated one when nothing named it this run and
  nothing new was recorded for UNDATED_PRUNE_DAYS. A row that names it only
  with a stale date is no sighting at all, so it can neither add a mention
  nor keep one (merge_queue says why that is one rule and not two).

  A queue that does not parse is never rewritten: the run exits 1 and the
  file stays exactly as the hand edit left it.

WHAT IT REFUSES, and each refusal is a measured class (numbers beside each
rule below, and in docs/agents/curation-and-discovery.md):
  * no frame at all: 5,436 of 6,728 candidates on 2026-11-07, overwhelmingly
    performers' credit lists ("festivals such as ...");
  * a keyword INSIDE a longer proper name ("North Fair Oaks Library", "the
    St. Marys Convention & Visitors Bureau");
  * the row's own title names it (a festival's own sub-event, "Wexford Reads
    Festival - In Conversation with ..."), or the prose says "this";
  * an observance, not an occasion ("National Grief Awareness Week", "Banned
    Books Week", "Diwali- the Festival of Lights");
  * a name with nothing in it that identifies anybody ("Science Week",
    "Community Resource Fair", "Holiday Film Fest");
  * a name the publisher calls its own ("as part of OUR Science Week
    programme"): the organiser is the configured feed itself;
  * a reference to the past ("as part of Good Things Festival in 2025"), or a
    performer's credits ("has performed at ... including the annual ...");
  * a keyword with nothing before it ("the Festival of Lights"): a holiday;
  * a mention whose every date ended more than PRUNE_DAYS ago.

A sample and a referrer title are committed, so an address or a phone number
in either is replaced whole BEFORE the text is cut (mask(), excerpt()).

Stores are EventStore JSON files (mapsee_ingest.EventStore.save): a top-level
"events" list of records with name, description, start_local/start_utc,
venue_name, latitude/longitude and sources [{source, source_id, url}]. A
directory is searched recursively for `*_events.json`, which is how the
feeds-* artifacts of aggregate-events.yml unpack (feeds_events.json,
facility_hours_events.json; the manifests beside them are not stores). Files
are read one at a time: the five stores of 2026-11-07 were 1.03 GB, the
largest 430 MB, and the whole scan peaked at 4.1 GB resident and took 57 s.

No network. No database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(HERE, "mention_queue.json")
ANNUAL_FILE = os.path.join(HERE, "annual_sources.json")

# A pending mention whose every date ended this long ago, and that no row has
# named again with a current date, is dropped; a NEW mention that stale is
# never added, which is what keeps a referring row that still says "as part of
# last month's festival" from re-adding what the prune just removed.
PRUNE_DAYS = 30
# An undated pending mention is dropped when no row named it this run and no
# new referrer was recorded for this long.
UNDATED_PRUNE_DAYS = 60
# "Covered" means some row carries the name as its TITLE within this many days
# of a mentioned date: a festival is often listed on its first day only.
COVER_DAYS = 3
MAX_REFERRERS = 5
SAMPLE_CHARS = 300
MAX_DATES = 12
# A range is kept as "start/end", never expanded into days. One longer than
# this is a season or an exhibition's run, not an occasion: its first day is
# kept and the end dropped.
MAX_RANGE_DAYS = 31

DEFAULT_COMMENT = (
    "Named events (festivals, fairs, expos, cons, biennials, art walks, open "
    "studios, weeks) that rows in our configured feeds mention and the map does "
    "not carry. Written by catalog_mentions.py from the feeds-* artifacts of "
    "aggregate-events.yml, daily in curate-catalog.yml. A pending mention is a "
    "lead for a human: an organiser's own prose-only site goes in "
    "annual_sources.json, a feed in the ordinary catalog. Set state to "
    "\"declined\" (with a reason in _not_included if it should never come back) "
    "to stop seeing one. URLs only; never copy a contact address in here."
)

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(s: Any) -> str:
    """A description as prose: entities decoded (twice, because Trumba and
    WordPress both double-escape - "&amp;#8217;" was in the 2026-11-07 stores),
    tags dropped, every run of whitespace one space."""
    if not isinstance(s, str) or not s:
        return ""
    t = html.unescape(html.unescape(s))
    t = _TAG.sub(" ", t)
    t = t.replace(" ", " ").replace("​", "")
    return _WS.sub(" ", t).strip()


# THE QUEUE IS COMMITTED TO A PUBLIC REPOSITORY, and a description quotes
# whoever the publisher named: the first real run's samples carried a county
# officer's address ("Contact dbnn@...gov with any questions"). An address or a
# phone number is never copied in here, whosever it is.
_EMAIL = re.compile(r"(?:mailto:)?[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A phone number's SHAPE, not "a run of digits": "(914) 864-7000",
# "206-386-4636", "+44 20 7946 0958". "2026-11-07", "2016-2022" and a street
# number do not have it.
_PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])"
                    r"\d{3,4}[ .-]?\d{3,4}(?!\w)")


def scrub(s: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[address]", s))


def _same_length(label: str):
    return lambda m: (label + " " * len(m.group()))[:len(m.group())]


def mask(s: str) -> str:
    """scrub(), but every replacement is exactly as long as what it replaces,
    so an offset into the text is an offset into this.

    WHY: a sample is a CUT of the prose, and scrubbing the cut instead of the
    whole text let half an address through. "maria.fihl@kir" (the cut fell
    inside the domain) does not look like an address to _EMAIL, and "+44 20
    7946 " does not look like a phone number to _PHONE. On the 2026-11-07
    audit 11 of the 6,728 candidate samples ended inside an address or a
    phone-like run, 10 of them real ("questions to: info@chessp", four times;
    "contact filmfestival@", three; "Wexford Library 053 919 6"). None was
    among the 155 kept, so nothing had reached the committed queue. Masking
    the whole text first finds every address whole, wherever the cut then
    falls: the same stores re-scanned give 0 such samples of 6,728."""
    return _PHONE.sub(_same_length("[phone]"), _EMAIL.sub(_same_length("[address]"), s))


def excerpt(masked: str, lo: int, n: int) -> str:
    """At most `n` characters of a mask()ed text from `lo`, never starting or
    ending in the middle of a word: a cut inside "[address]" or a name is
    dropped back to the last whole word."""
    lo = max(0, lo)
    hi = min(len(masked), lo + n)
    s = masked[lo:hi]
    if lo > 0 and not masked[lo - 1].isspace() and s[:1] and not s[:1].isspace():
        s = s.split(" ", 1)[1] if " " in s else ""
    if hi < len(masked) and not masked[hi].isspace() and not masked[hi - 1].isspace():
        s = s.rsplit(" ", 1)[0] if " " in s else ""
    return _WS.sub(" ", s).strip()


def _fold(s: str) -> str:
    return (s.replace("’", "'").replace("‘", "'").replace("“", '"')
            .replace("”", '"').replace("–", "-").replace("—", "-"))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
# The word that makes a capitalised run a NAMED OCCASION. Case-insensitive for
# the English nouns ("Short Run Comix festival" is written both ways), but
# "Con" and the compounds are case-sensitive: "con" is Spanish and Italian for
# "with", and "Falcon", "Bacon" and "Lexicon" end in it.
_KW_SPEC = (r"(?i:festival|fest|fair|expo|exposition|convention|biennial|biennale|"
            r"triennial|week|art\s+walk|artwalk|open\s+studios?|zinefest|bookfair)"
            r"|Con|[A-Z][a-z]{2,}fest|[A-Z][a-z]+Con")
# Not after a hyphen either: "Micro-Festival #3" is one word, and walking back
# from its second half made a name of "Micro-".
KW_RX = re.compile(r"(?<![\w'’-])(?:" + _KW_SPEC + r")(?![\w'’-])")
# The keyword words, for the cheap pre-filter and the title index.
_KW_WORDS = ("festival", "fest", "fair", "expo", "exposition", "con", "convention",
             "biennial", "biennale", "triennial", "week", "walk", "studio", "studios",
             "bookfair")
_PREFILTER = re.compile(r"(?i)fest|fair|expo|conven|\bcon\b|[a-z]Con\b|bienn|trienn|"
                        r"week|art\s*walk|open\s+studio")

# Joiners allowed INSIDE a name, never at either end of it.
_CONNECT = {"of", "the", "and", "&", "de", "du", "des", "la", "le", "del", "y", "et",
            "for", "on", "und", "da", "di", "van", "von", "upon"}
# Abbreviations whose full stop does not end a name or a sentence.
_ABBREV = {"st.", "mt.", "ft.", "jr.", "sr.", "dr.", "u.s.", "d.c.", "n.y.", "l.a.",
           "inc.", "co.", "no.", "ste.", "mr.", "ms.", "mrs."}
# Capitalised words that start a SENTENCE or a frame rather than a name:
# "Join Seattle Design Festival", "Celebrate Pride Week". Trimmed from the left.
_LEAD = {"the", "a", "an", "this", "that", "these", "those", "our", "your", "their",
         "his", "her", "its", "my", "annual", "join", "visit", "celebrate", "attend",
         "welcome", "come", "see", "enjoy", "explore", "discover", "experience",
         "during", "at", "in", "on", "for", "with", "from", "to", "as", "part", "of",
         "and", "presented", "hosted", "celebrating", "ahead", "before", "after",
         "every", "each", "next", "last", "upcoming", "inaugural", "first", "second",
         "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
         "official", "it's", "it’s", "we", "we're", "we’re", "i", "you", "please",
         "don't", "don’t", "kick", "kicks", "off", "back", "returns", "return",
         "support", "supporting", "sponsor", "sponsored", "by", "featuring", "plus",
         "also", "then", "when", "while", "if", "or", "but", "so", "all", "both",
         "save", "date", "mark", "calendar", "calendars", "register", "tickets",
         "nell'ambito", "nell’ambito", "dans", "im", "en", "como", "durante",
         "ticket", "free", "new:", "note:", "update:", "reminder:", "today", "tonight",
         "tomorrow", "weekly", "monthly", "yearly", "fall's", "summer's", "spring's",
         "winter's", "year's", "years"}
_ORDINAL = re.compile(r"^\d{1,3}(?:st|nd|rd|th|e|er|re|ère|eme|ème|ª|º|\.)$", re.I)
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_ORD_WORDS = ("first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
              "eleventh|twelfth|thirteenth|fourteenth|fifteenth|twentieth")
_ANNUAL_TOK = re.compile(r"^(?:(?:\d{1,3}(?:st|nd|rd|th)|" + _ORD_WORDS + r")-)?(?:annual|inaugural)$",
                         re.I)


def _cap(tok: str) -> bool:
    c = tok[:1]
    return bool(c) and (c.isupper() or (c.isdigit() and any(ch.isalpha() for ch in tok)
                                        and not _ORDINAL.match(tok)))


def _name_before(text: str, k: int) -> Tuple[int, List[str]]:
    """Walk back from the keyword at `k` over the capitalised run that names it.

    Returns (start offset, tokens). Stops at punctuation that cannot sit inside
    a name (a comma, a colon, a full stop that is not an abbreviation's), at
    the first lower-case word that is not a joiner, and after eight tokens.
    """
    lo = max(0, k - 160)
    toks = [(m.start() + lo, m.group()) for m in re.finditer(r"\S+", text[lo:k])]
    out: List[Tuple[int, str]] = []
    for pos, tok in reversed(toks):
        if len(out) >= 8:
            break
        bare = tok.lstrip("\"'“‘(")
        opened = bare != tok
        if not bare:
            break
        last = bare[-1]
        if last in ",;:!?)]\"”'’" and bare.lower() not in ("'s",):
            if not (last in "'’" and bare[:-1].endswith("s")):
                break
        if last == "." and bare.lower() not in _ABBREV:
            break
        low = bare.lower()
        if KW_RX.fullmatch(bare) and bare[:1].isupper():
            # Another occasion's keyword: "Cuban Jazz Festival and Grape Grazing
            # Festival" is two names, and this one starts after it.
            break
        if _cap(bare) or low in _CONNECT:
            out.append((pos + (len(tok) - len(bare)), bare))
            if opened:
                break
            continue
        break
    out.reverse()
    # "Cinema St. Louis's 35th-annual St. Louis International Film Festival":
    # an ordinal or "annual" INSIDE the run ends the organiser's part of it, and
    # the name is what follows.
    for i in range(len(out) - 1, -1, -1):
        if _ANNUAL_TOK.match(out[i][1]):
            out = out[i + 1:]
            break
    # Trim the left: joiners and the words that open a sentence or a frame.
    while out and (out[0][1].lower() in _CONNECT or out[0][1].lower() in _LEAD
                   or _ORDINAL.match(out[0][1]) or _YEAR.match(out[0][1])):
        out.pop(0)
    if not out:
        return k, []
    return out[0][0], [t for _p, t in out]


_SUFFIX_OF = re.compile(
    r"\s+of\s+(?:the\s+)?((?:[A-Z][\w'’&+-]*)(?:\s+(?:and|&|of|the|[A-Z][\w'’&+-]*)){0,4})")
_SUFFIX_YEAR = re.compile(r"\s+((?:19|20)\d\d)(?![\w-])")


def norm_name(name: str) -> str:
    """The queue key: lower case, folded quotes, '&' as 'and', no leading
    article, ordinal, 'annual' or trailing year, single spaces."""
    s = _fold(name).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w' -]+", " ", s)
    s = re.sub(r"\b(?:19|20)\d\d\b", " ", s)
    s = re.sub(r"\b\d{1,3}(?:st|nd|rd|th)\b", " ", s)
    s = re.sub(r"\b(?:annual|inaugural)\b", " ", s)
    s = _WS.sub(" ", s).strip(" -'")
    s = re.sub(r"^(?:the|a|an) ", "", s)
    return s


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------
# What sits in front of the name and says the name is a SEPARATE occasion the
# row belongs to or points at, matched against the END of the folded,
# lower-cased text before the name once determiners and "Nth annual" are off.
# Measured on 2026-11-07 and left out: "at", "for", "including", "with" and
# "of the". Their candidates were performers' credits ("has performed at
# Desert Daze and Barcelona Psych Fest") and the names of bodies ("a dancer
# with the Vienna Festival Ballet") far more often than occasions.
FRAMES = (
    "in coordination with", "in conjunction with", "in collaboration with",
    "in partnership with", "in celebration of", "as part of", "part of",
    "in association with", "presented at", "presented during", "presented as part of",
    "co-presented by", "presented by", "during", "ahead of", "celebrating", "celebrate",
    "in honor of", "in honour of", "comes to", "come to", "returns to", "return to",
    # The same frame in the other languages the feeds carry: 57 candidate
    # mentions on 2026-11-07, "nell'ambito di Contemporanea Film Festival",
    # "dans le cadre du NEUFNEUF Festival", "in het kader van het ...".
    "dans le cadre du", "dans le cadre de la", "dans le cadre des", "dans le cadre de",
    "nell'ambito di", "nell'ambito del", "nell'ambito della", "im rahmen des",
    "im rahmen der", "en el marco del", "en el marco de la", "in het kader van",
    "in het kader van het", "in het kader van de",
)
_ORD = r"(?:\d{1,3}(?:st|nd|rd|th)|" + _ORD_WORDS + r")"
_FRAME_TAIL = re.compile(
    r"(?:\s+(?:the|this|this year's|this years|year's|our|their|its|a|an|annual|inaugural|"
    + _ORD + r"(?:-annual)?|(?:19|20)\d\d))*\s*$")
_OWN = re.compile(r"\b(?:our|my)\s*(?:" + _ORD + r"[\s-]+)?(?:(?:annual|inaugural)\s*)?$")
# "During THIS Fall Harvest Hoops Fest": the row is the occasion it names.
_THIS_REF = re.compile(r"\bthis\s+(?:" + _ORD + r"[\s-]+)?(?:annual\s+)?$")
# "the 14th annual X", "the annual X", "Cinema St. Louis's 35th-annual X":
# a publisher counting the editions of something is naming an occasion.
_NTH_ANNUAL = re.compile(r"(?:\b" + _ORD + r"[\s-]+)?\b(?:annual|inaugural)\s*$")


def frame_of(before: str) -> Tuple[Optional[str], bool, bool]:
    """(frame, own, annual) for the text immediately before a name."""
    b = _fold(before[-80:]).lower()
    nth = bool(_NTH_ANNUAL.search(b))
    own = bool(_OWN.search(b))
    core = _FRAME_TAIL.sub("", b)
    for f in FRAMES:
        if core.endswith(" " + f) or core == f:
            return f, own, nth
    return None, own, nth


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
# OBSERVANCES: a day or a week on somebody's calendar, not an occasion anybody
# organises in one place. MEASURED, not imagined: on the 2026-11-07 feeds-*
# stores (611,881 rows) every name below was a candidate before it was a rule,
# and the first draft's 50 week-words and 5 adjectives that matched nothing
# were taken back out. Per pattern, distinct names / candidate mentions:
#   [0]  13 / 62  "national consumer protection week", "world nursery rhyme week"
#   [1]  45 / 137 "banned books week", "science week", "salem restaurant week"
#   [2]   4 / 9   "diwali- the festival of lights"
#   [3]   9 / 91  "festival of lights" (Hanukkah, at 91 the commonest of all)
#   [4]   2 / 2   "jewish festival of lights", "hindu festival of lights"
#   [5]   1 / 1   "read across america week"
#   [6]   5 / 8   "hungry ghost festival", "moon festival", "obon festival"
# A dining week ("Salem Restaurant Week", "Cleveland Pizza Week") is here on
# purpose: forty restaurants on their own menus is a promotion, not a place
# and a time a map can pin.
OBSERVANCE_RX = [re.compile(p) for p in (
    r"^(?:national|international|world|global|australian)\b.*\bweek$",
    r"\b(?:awareness|appreciation|recognition|education|literacy|inclusion|kindness|"
    r"entrepreneurship|library|reading|science|climate action|consumer protection|"
    r"digital health|pollinator|tree|book|books|finals|pride|mlk|veterans?'?|holy|"
    r"thanksgiving|restaurant|pizza|community table|baby)\s+week$",
    r"^diwali\b",
    r"^festival of lights\b",
    r"^(?:jewish|hindu|sikh|buddhist|christian|muslim|islamic)\s+festival of\b",
    r"^read across america\b",
    r"^(?:lunar new year|mid-autumn|moon|lantern|dragon boat|hungry ghost|"
    r"spring|qingming|holi|obon|bon|harvest|boat)\s+festival$",
)]
# NOTHING IN THE NAME SAYS WHOSE IT IS. A name every one of whose words is in
# this list names a kind of event, not an event: "Science Week", "Community
# Resource Fair", "Holiday Film Fest", "Arts & Crafts Festival". On the
# 2026-11-07 stores it refused 108 distinct candidate names; read by hand, 4
# were real occasions ("Anime Expo", "Great Rivers Biennial", "National Folk
# Festival", "Reading Festival"), so "anime" and "great" came out of the list
# and the other two are the price of "national" and "reading" staying in.
GENERIC = set("""
a an the of and for on in at to annual inaugural community family families kids kid
children child youth teen teens adult adults senior seniors library libraries school
schools college university campus student students holiday holidays christmas xmas
halloween easter thanksgiving winter summer spring fall autumn harvest seasonal
film films movie movies music art arts craft crafts maker makers makerspace science
sciences engineering technology tech health wellness resource resources book books
reading literacy food foods wine beer cocktail coffee tea chocolate cookie cookies
pizza taco burger dessert career careers job jobs employment hiring volunteer
volunteers fringe folk jazz blues comedy dance theatre theater poetry story stories
storytelling international national world global regional local city county state
street block neighborhood neighbourhood village town cultural culture heritage
history historical mini open studio studios walk week weeks weekend day days night
nights festival festivals fest fair fairs expo exposition con convention biennial
biennale triennial new old big little grand annual fun free green garden
gardens plant plants tree trees nature outdoor outdoors water river lake sea ocean
beach park parks spirit pride equity diversity inclusion multicultural global
digital virtual online virtual hybrid sale market markets vendor vendors fashion
design architecture photo photography print prints zine zines comic comics
game games gaming esports robot robotics stem steam math reading writers writing
author authors poets health safety safe fire police emergency disaster preparedness
edition editions édition du des la le der die das
""".split())


def observance_reason(key: str) -> Optional[str]:
    for rx in OBSERVANCE_RX:
        if rx.search(key):
            return "observance"
    return None


def _generic_word(w: str) -> bool:
    """In GENERIC as written, as a possessive, or as a plural: "writers'",
    "children's", "rivers"."""
    if w in GENERIC:
        return True
    base = re.sub(r"(?:'s|s')$", "", w)
    if base in GENERIC:
        return True
    return len(base) > 3 and base.endswith("s") and base[:-1] in GENERIC


def generic_reason(key: str) -> Optional[str]:
    words = [w for w in re.split(r"[\s-]+", key) if w]
    if not words or all(_generic_word(w) for w in words):
        return "generic"
    return None


_KW_WORD_RX = re.compile(r"^(?:" + "|".join(_KW_WORDS) + r"|\w+fest|\w+con)$")


def key_variants(key: str) -> List[str]:
    """The key, and the name after an organiser's possessive: "marlene meyerson
    jcc manhattan's the other israel film festival" is also "other israel film
    festival", which is how the row that IS it titles itself."""
    out = [key]
    if "'s " in key:
        tail = norm_name(key.rsplit("'s ", 1)[1])
        if len(tail.split()) >= 2 and not generic_reason(tail):
            out.append(tail)
    return out


def _distinctive(words: Iterable[str]) -> List[str]:
    return [w for w in words if not _generic_word(w) and not _KW_WORD_RX.match(w)]


def _has(needle: str, hay: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", hay))


def is_self(key: str, title: str) -> bool:
    """The row's own title names it: the row IS the occasion, or one of its
    own sessions ("Wexford Reads Festival - In Conversation with ...")."""
    t = norm_name(title)
    if not t or not key:
        return False
    for k in key_variants(key):
        if _has(k, t):
            return True
    # "10th Annual Alexandria Pride" names "Alexandria Pride Fair";
    # "Tipperariana" names "Tipperariana Book Fair". Eight letters at least and
    # not a kind of thing, so "Storytime" or "Craft Fair" names nothing.
    if len(t) >= 8 and not generic_reason(t) and _has(t, key):
        return True
    # "2026 Holiday Festival" is "the annual Levy Park Holiday Festival": a
    # generic title is the row itself when the name ENDS with it.
    if len(t.split()) >= 2 and _KW_WORD_RX.match(t.split()[-1]) and key.endswith(" " + t):
        return True
    # EVERY distinctive word of the name in a title that is itself an
    # occasion: "Hucknall Beer and Cider Festival 2027" is "the annual Hucknall
    # Beer Festival", "Urban Craft and Art Fair" is "the annual Urban Art and
    # Craft Fair" (both 2026-11-07, in the publisher's own description).
    tw = t.split()
    kd = _distinctive(key.split())
    if kd and set(kd) <= set(tw) and any(_KW_WORD_RX.match(w) for w in tw):
        return True
    return False


# ---------------------------------------------------------------------------
# Dates and venues near a name
# ---------------------------------------------------------------------------
MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
          "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
          "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
          "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2,
            "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4,
            "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_WD = r"(?:(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\.?,?\s+)?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_TO = r"\s*(?:-|–|—|to|through|thru|until|and|&)\s*"
_DATE_MDY = re.compile(r"(?i)\b" + _WD + _MON + r"\s+" + _DAY + r"(?![\d:])"
                       r"(?:" + _TO + r"(?:" + _MON + r"\s+)?" + _DAY + r"(?![\d:]))?"
                       r"(?:,?\s+((?:19|20)\d\d))?")
# "Saturday 24 October", "11th-14th February 2027", and "20th November to
# 27th November 2026" (the One Health Festival's own words, 2026-11-07).
_DATE_DMY = re.compile(r"(?i)\b" + _WD + _DAY + r"(?:\s+(?:of\s+)?" + _MON + r"(?![a-z]))?"
                       r"(?:" + _TO + _DAY + r")?\s+(?:of\s+)?"
                       + _MON + r"(?![a-z])(?:,?\s+((?:19|20)\d\d))?")
_VENUE = re.compile(r"\bat\s+(?:the\s+)?((?:[A-Z][\w'’.&+-]*)(?:\s+(?:of|the|and|&|on|[A-Z][\w'’.&+-]*)){0,6})")
# Whole words: without the \b, "no" refused "Northcote Theatre" (found by a
# mutation run of test_mentions.py on 2026-11-08).
_NOT_VENUE = re.compile(r"(?i)^(?:noon|midnight|night|dawn|dusk|sunset|sunrise|"
                        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                        r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
                        r"this|that|our|your|their|its|which|least|most|all|home|"
                        r"times|first|last|once|any|no|an?)\b|^\d")


def _sentence_end(text: str, start: int, limit: int) -> int:
    """Where the sentence that starts at or before `start` ends, within limit."""
    stop = min(len(text), start + limit)
    for m in re.finditer(r"[.!?](?=\s+[A-Z\"“(]|\s*$)", text[start:stop]):
        j = start + m.start()
        word = text[max(0, j - 6):j + 1].split()[-1].lower() if text[max(0, j - 6):j + 1].split() else ""
        if word in _ABBREV or re.fullmatch(r"(?:" + _MON + r")", word.rstrip(".")) \
                or word.rstrip(".") in ("a.m", "p.m", "am", "pm", "approx", "est", "vs"):
            continue
        return j
    return stop


def _resolve(month: int, day: int, year: Optional[int], wd: Optional[int],
             anchor: dt.date) -> Optional[dt.date]:
    """A yearless date is the occurrence nearest the referring row's own start;
    a weekday that disagrees with every candidate refuses the date."""
    if year:
        try:
            d = dt.date(year, month, day)
        except ValueError:
            return None
        return d if wd is None or d.weekday() == wd else None
    best = None
    for y in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        if wd is not None and d.weekday() != wd:
            continue
        if best is None or abs((d - anchor).days) < abs((best - anchor).days):
            best = d
    if best is not None and abs((best - anchor).days) > 200:
        return None
    return best


def _dates_from(m: re.Match, mdy: bool, anchor: dt.date) -> List[str]:
    g = m.groups()
    if mdy:
        wd, mon1, d1, mon2, d2, yr = g
    else:
        wd, d1, mon_a, d2, mon1, yr = g
        mon2 = None
        if mon_a and d2:
            # "20th November to 27th November": the first month is the start's.
            mon1, mon2 = mon_a, mon1
        elif mon_a and not d2:
            # "Saturday 24 October ... November" cannot be one date; keep the first.
            mon1 = mon_a
    # "may" is the month only when it is written "May".
    if mon1 and mon1.lower().rstrip(".") == "may" and not mon1.startswith("M"):
        return []
    m1 = MONTHS.get(mon1.lower().rstrip("."))
    m2 = MONTHS.get(mon2.lower().rstrip(".")) if mon2 else m1
    wdi = WEEKDAYS.get(wd.lower().rstrip(".")) if wd else None
    year = int(yr) if yr else None
    a = _resolve(m1, int(d1), year, wdi, anchor) if m1 else None
    if a is None:
        return []
    if not d2:
        return [a.isoformat()]
    try:
        b = dt.date(a.year + (1 if m2 < m1 else 0), m2, int(d2))
    except (ValueError, TypeError):
        return [a.isoformat()]
    if b <= a or (b - a).days > MAX_RANGE_DAYS:
        return [a.isoformat()]
    return [f"{a.isoformat()}/{b.isoformat()}"]


def dates_near(text: str, start: int, anchor: dt.date) -> Tuple[List[str], int]:
    """The FIRST date in the rest of the name's sentence (and its parenthesis):
    "taking place on Saturday, November 7th ..." / "(Nov. 5-15)". Returns the
    dates and the offset just past them (or -1 when there are none).

    The date has to START inside the sentence (or within 240 characters); it
    is read from the WHOLE text. Searching a slice cut at 240 hid whatever
    followed the cut from the regex's lookahead, so "November 15" cut after
    its "1" was read as November 1st, and "20th November to 27th November
    2026" cut before its end lost the range (2026-11-08 review). On the
    2026-11-07 stores it changed 1 of the 155 kept observations: One Health
    Festival's 2026-11-20 became 2026-11-20/2026-11-27. All 8 dated ones
    already carried their festival's start."""
    end = _sentence_end(text, start, 240)
    best = None
    for rx, mdy in ((_DATE_MDY, True), (_DATE_DMY, False)):
        m = rx.search(text, start)
        if m and m.start() < end and (best is None or m.start() < best[0].start()):
            best = (m, mdy)
    if not best:
        return [], -1
    dates = _dates_from(best[0], best[1], anchor)
    return dates, best[0].end() if dates else -1


# A VENUE IS READ ONLY RIGHT AFTER THE OCCASION'S OWN DATE. Read anywhere in the
# sentence, the first hand check (2026-11-08) gave 1 right of 3: "as part of
# the Sydney Festival, continues onto Melbourne at Northcote Theatre ... at the
# Triffid" named the tour's next stops. After a date it is the occasion's:
# "Saturday, November 7th from 11 am to 6 pm at Seattle Design Center".
VENUE_AFTER_DATE = 90


def venue_near(text: str, start: int) -> Optional[str]:
    if start < 0:
        return None
    # The venue has to START within VENUE_AFTER_DATE; it may run to the end of
    # its sentence. An endpos at the 90th character could cut a name in two
    # the way the 240-character slice cut a date ("at Seattle Desig", built in
    # test_mentions.py); on the 2026-11-07 stores it had cut none of the 155.
    send = _sentence_end(text, start, VENUE_AFTER_DATE + 160)
    for m in _VENUE.finditer(text, start, send):
        if m.start() >= start + VENUE_AFTER_DATE:
            break
        v = m.group(1)
        v = re.split(r"\s+(?:in|on|from|between|and then|with)\s+", v)[0]
        words = v.split()
        while words and words[-1].lower() in _CONNECT:
            words.pop()
        v = " ".join(words).rstrip(".,")
        if not v or _NOT_VENUE.match(v) or len(v) < 4:
            continue
        return v
    return None


_CREDIT = re.compile(
    r"(?i)\b(?:has|have|had)\s+(?:also\s+)?(?:performed|appeared|exhibited|shown|played|"
    r"toured|screened|premiered|presented|been\s+(?:featured|selected|shown|presented))\b|"
    r"\bperformed\s+(?:at|in|with)\b|\bwas\s+(?:presented|screened|shown|selected|featured)\b|"
    r"\bincluding\b")


def _clause_before(text: str, start: int) -> str:
    """The part of the name's own sentence that comes before it."""
    pre = text[max(0, start - 200):start]
    cut = max(pre.rfind(". "), pre.rfind("! "), pre.rfind("? "))
    return pre[cut + 2:] if cut >= 0 else pre


# NO "^": pattern.match(text, pos) anchors "^" at the start of the STRING, so
# with it this never matched anywhere but offset 0, and "Perth Fringeworld
# Festival (2016-2022)" was kept as a lead (found by the 2026-11-08 hand check).
_PAST_YEAR = re.compile(r"[ \t]*(?:in[ \t]+|\(|,[ \t]*)?((?:19|20)\d\d)\b")
# The next word, when it continues a capitalised run with no punctuation
# between: the keyword was not the end of the name.
_NEXT_CAP = re.compile(r"[ \t]+&?\s*([A-Z][\w'’.-]*)")
# ...except a date, which follows an occasion's name all the time.
_MAY_FOLLOW = set(WEEKDAYS) | set(MONTHS) | {"sept"}


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------
def _row_start(row: Dict[str, Any]) -> Optional[dt.date]:
    for k in ("start_local", "start_utc"):
        v = row.get(k)
        if isinstance(v, str) and re.match(r"\d{4}-\d\d-\d\d", v):
            try:
                return dt.date.fromisoformat(v[:10])
            except ValueError:
                continue
    return None


def _referrer(row: Dict[str, Any]) -> Dict[str, Any]:
    src = (row.get("sources") or [{}])
    first = src[0] if src and isinstance(src[0], dict) else {}
    start = _row_start(row)
    # A title is prose too, and is committed as well: masked, then cut on a
    # word, for the same reason as a sample.
    return {"source": first.get("source") or "?",
            "title": excerpt(mask(clean_text(row.get("name"))), 0, 140),
            "url": first.get("url") or None, "start": start.isoformat() if start else None}


def _ref_key(ref: Dict[str, Any]) -> str:
    raw = f"{ref.get('source')}|{ref.get('title')}|{ref.get('start')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def mentions_in_row(row: Dict[str, Any], today: dt.date,
                    audit: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every named occasion this row's prose points at, refusals applied
    except "covered" (which needs every store) and "configured" (the queue's).

    With `audit`, every candidate is appended there with its verdict, so a
    precision measurement can read the refusals as well as the keeps.
    """
    text = clean_text(row.get("description"))
    if not text or not _PREFILTER.search(text):
        return []
    title = clean_text(row.get("name"))
    anchor = _row_start(row) or today
    masked: Optional[str] = None
    out: Dict[str, Dict[str, Any]] = {}
    for km in KW_RX.finditer(text):
        k = km.start()
        start, toks = _name_before(text, k)
        end = km.end()
        suffix_of = _SUFFIX_OF.match(text, end)
        if suffix_of and km.group().lower() not in ("festival", "fair", "week", "biennial", "fest"):
            suffix_of = None
        if suffix_of:
            end = suffix_of.end()
        if not toks and not suffix_of:
            continue
        name = (" ".join(toks + [text[k:end]])).strip()
        yr = _SUFFIX_YEAR.match(text, end)
        name_year = int(yr.group(1)) if yr else None
        tail_from = yr.end() if yr else end
        key = norm_name(name)
        frame, own, nth = frame_of(text[:start])
        nxt = _NEXT_CAP.match(text, tail_from)
        verdict = None
        if not key or len(key) < 4:
            verdict = "empty"
        elif nxt and nxt.group(1).lower().rstrip(".") not in _MAY_FOLLOW:
            # "North Fair Oaks Library", "Glyndebourne Festival Opera", "the
            # St. Marys Convention & Visitors Bureau": the keyword is INSIDE a
            # longer proper name, which is a place or a body, not an occasion.
            verdict = "inside"
        elif frame is None and not nth:
            verdict = "no-frame"
        elif own or "library" in key.split() or "libraries" in key.split():
            # "as part of OUR Science Week programme", "Part of the Sulzer
            # Regional Library Comic & Arts Festival": the organiser is the
            # configured feed itself, so there is no site to find.
            verdict = "own"
        elif _THIS_REF.search(_fold(text[max(0, start - 30):start]).lower()):
            # "During this Fall Harvest Hoops Fest": the row is the occasion.
            verdict = "self"
        elif is_self(key, title):
            verdict = "self"
        elif not toks:
            # "the Festival of Lights", "the festival of Lohri": nothing before
            # the keyword names a place or an organiser; every one of these in
            # the 2026-11-07 stores was a holiday.
            verdict = "holiday"
        else:
            verdict = observance_reason(key) or generic_reason(key)
        past = _PAST_YEAR.match(text, end)
        before_year = re.search(r"\b((?:19|20)\d\d)[ \t]+$", text[max(0, start - 8):start])
        if verdict is None:
            ref_year = anchor.year
            if (name_year and name_year < ref_year) or (
                    past and not yr and int(past.group(1)) < ref_year) or (
                    before_year and int(before_year.group(1)) < ref_year):
                # "as part of Good Things Festival in 2025", "the 2024 Halifax
                # Fringe Festival", "(2016-2022)": a credit, not a lead.
                verdict = "past-year"
        if verdict is None and _CREDIT.search(_clause_before(text, start)):
            # "he has performed at numerous ... events, including the annual
            # Cuban Jazz Festival": a performer's credits, not a lead. 3 of the
            # 177 keeps on 2026-11-07, and all 3 false.
            verdict = "credit"
        dates, date_end = dates_near(text, tail_from, anchor) if verdict is None else ([], -1)
        if verdict is None and dates:
            last = max(d.split("/")[-1] for d in dates)
            if dt.date.fromisoformat(last) < today - dt.timedelta(days=PRUNE_DAYS):
                verdict = "stale"
        sample_lo = max(0, text.rfind(". ", 0, start) + 2 if text.rfind(". ", 0, start) >= 0 else 0)
        sample_lo = max(sample_lo, start - 120)
        if masked is None:
            masked = mask(text)       # once per row, and only for a row with a candidate
        sample = excerpt(masked, sample_lo, SAMPLE_CHARS)
        if audit is not None:
            audit.append({"key": key, "name": name, "frame": frame or ("annual" if nth else None),
                          "own": own, "verdict": verdict or "keep", "title": title[:140],
                          "source": _referrer(row)["source"], "sample": sample,
                          "dates": dates})
        if verdict is not None:
            continue
        hit = out.get(key)
        if hit is None:
            out[key] = {"key": key, "name": name, "dates": dates,
                        "venue": venue_near(text, date_end), "sample": sample,
                        "frame": frame or "annual", "referrer": _referrer(row)}
        else:
            for d in dates:
                if d not in hit["dates"]:
                    hit["dates"].append(d)
    return list(out.values())


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------
def store_files(paths: Iterable[str]) -> List[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if f.endswith("_events.json"):
                        out.append(os.path.join(root, f))
        elif os.path.isfile(p):
            out.append(p)
    return sorted(set(out))


def iter_rows(files: Iterable[str]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  skipped {f}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            print(f"  skipped {f}: no events list")
            continue
        for row in events:
            if isinstance(row, dict):
                yield f, row
        del data, events


class TitleIndex:
    """Normalised titles that carry a keyword word, with their start dates.

    Only those can contain a name that ends in one, so the index holds about
    one row in twenty (measured in the notes) and a substring test against it
    is cheap enough to run for every candidate."""

    def __init__(self) -> None:
        self.by_word: Dict[str, Dict[str, set]] = {}

    def add(self, row: Dict[str, Any]) -> None:
        t = norm_name(clean_text(row.get("name")))
        if not t:
            return
        words = set(t.split())
        hit = [w for w in _KW_WORDS if w in words] or (
            ["fest"] if re.search(r"\w+fest\b", t) else []) or (
            ["con"] if re.search(r"\w+con\b", t) else [])
        if not hit:
            return
        start = _row_start(row)
        for w in hit:
            self.by_word.setdefault(w, {}).setdefault(t, set()).add(start)

    def covered(self, key: str, dates: List[str], anchor: Optional[dt.date]) -> bool:
        return any(self._covered(k, dates) for k in key_variants(key))

    def _covered(self, key: str, dates: List[str]) -> bool:
        words = key.split()
        last = words[-1] if words else ""
        bucket_keys = [w for w in _KW_WORDS if w in words] or (
            ["fest"] if last.endswith("fest") else ["con"] if last.endswith("con") else [])
        spans = []
        for d in dates:
            a, _, b = d.partition("/")
            lo = dt.date.fromisoformat(a) - dt.timedelta(days=COVER_DAYS)
            hi = dt.date.fromisoformat(b or a) + dt.timedelta(days=COVER_DAYS)
            spans.append((lo, hi))
        rx = re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)")
        for w in bucket_keys:
            for title, starts in self.by_word.get(w, {}).items():
                if key not in title or not rx.search(title):
                    continue
                if not spans:
                    return True
                for s in starts:
                    if s is not None and any(lo <= s <= hi for lo, hi in spans):
                        return True
        return False


def scan(files: List[str], today: dt.date,
         audit: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Every kept observation across the stores, each marked `covered`."""
    index = TitleIndex()
    found: List[Dict[str, Any]] = []
    stats = {"rows": 0, "described": 0, "files": len(files), "candidates": 0}
    for _f, row in iter_rows(files):
        stats["rows"] += 1
        index.add(row)
        if row.get("description"):
            stats["described"] += 1
        before = len(audit) if audit is not None else 0
        found.extend(mentions_in_row(row, today, audit))
        if audit is not None:
            stats["candidates"] += len(audit) - before
    for ob in found:
        anchor = dt.date.fromisoformat(ob["referrer"]["start"]) if ob["referrer"].get("start") else None
        ob["covered"] = index.covered(ob["key"], ob["dates"], anchor)
    stats["observations"] = len(found)
    stats["covered_obs"] = sum(1 for o in found if o["covered"])
    return found, stats


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------
def load_json(path: str, default: Any) -> Any:
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def annual_sites(path: str = ANNUAL_FILE) -> List[Dict[str, Any]]:
    """annual_sources.json's sites, or [] when the file is missing or broken:
    {"sites": [{"key", "name", "name_rx", "url", ...}]}."""
    doc = load_json(path, {})
    sites = doc.get("sites") if isinstance(doc, dict) else None
    return [s for s in sites or [] if isinstance(s, dict)]


def configured_site(name: str, sites: List[Dict[str, Any]]) -> Optional[str]:
    for s in sites:
        rx = s.get("name_rx")
        if not isinstance(rx, str) or not rx:
            continue
        try:
            if re.search(rx, name, re.I) or re.search(rx, norm_name(name), re.I):
                return s.get("key") or s.get("name") or "?"
        except re.error:
            continue
    return None


def _date_end(d: str) -> dt.date:
    return dt.date.fromisoformat(d.split("/")[-1])


def _current(ob: Dict[str, Any], horizon: dt.date) -> bool:
    """An observation is a SIGHTING when it names no date, or names one that
    ended on or after the horizon. One that names only stale dates is a row
    still talking about a past edition, and counts for nothing."""
    ds = ob.get("dates") or []
    return not ds or any(_date_end(d) >= horizon for d in ds)


def merge_queue(queue: Dict[str, Any], observations: List[Dict[str, Any]],
                today: dt.date, sites: List[Dict[str, Any]]) -> Dict[str, int]:
    """Fold one run's observations into the queue, in place. Deterministic:
    the same observations on the same queue give the same bytes, and a second
    application of them changes nothing.

    ONE PREDICATE DECIDES ADDING, FOLDING AND PRUNING: _current(). It used to
    be two. A new mention was refused only when ITS OWN observation's dates
    were all stale, while the prune looked at the dates folded from every
    row. One Health Festival (2026-11-07 stores: the only one of 53 keys with
    both a dated and an undated referrer) shows both ways that went wrong.
    The real scan folded daily from 2026-11-09 for 140 days: from 2026-12-29
    an undated row created it and a stale-dated row's date pruned it IN THE
    SAME RUN, 90 days running, each printed as "1 new ... 1 pruned" with the
    mention gone. With the stale-dated row sorting first (2026-11-08 review)
    it was absent and present on alternate days from 2026-12-21, first_seen
    reset each time, the bytes changing daily with nothing new. Now a
    stale-dated observation is dropped before anything else sees it, only
    current dates are folded, and a key a current observation named this run
    is never pruned: the same 140 days add nothing and change the bytes on 2
    days, both a real prune.

    COVERED IS RE-DECIDED EVERY RUN, and pruned like pending. It used to move
    one way only: a festival some row titled once stayed covered for good, so
    next year's edition, uncovered, never came back as a lead, and covered
    entries were kept forever (11 of the 46 mentions in the first committed
    queue). Now a key named this run is covered when any of this run's
    current observations is, and pending otherwise; configured and declined
    are a human's, and never re-decided here."""
    queue.setdefault("_comment", DEFAULT_COMMENT)
    declined = {norm_name(k) for k in (queue.get("_not_included") or {})}
    queue.setdefault("_not_included", {})
    mentions = queue.setdefault("mentions", {})
    iso = today.isoformat()
    stats = {"new": 0, "updated": 0, "pruned": 0, "not_included": 0, "stale": 0}
    horizon = today - dt.timedelta(days=PRUNE_DAYS)
    named_now: Dict[str, List[Dict[str, Any]]] = {}
    for ob in sorted(observations, key=lambda o: (o["key"], o["referrer"].get("start") or "",
                                                  o["referrer"].get("source") or "",
                                                  o["referrer"].get("title") or "")):
        if ob["key"] in declined:
            stats["not_included"] += 1
        elif not _current(ob, horizon):
            # A saved scan re-applied weeks later is the same case: it cannot
            # add what the prune would remove at once.
            stats["stale"] += 1
        else:
            named_now.setdefault(ob["key"], []).append(ob)
    for key, obs in named_now.items():
        m = mentions.get(key)
        existed = m is not None
        changed = False
        if m is None:
            first = obs[0]
            m = mentions[key] = {
                "name": first["name"], "state": "pending", "first_seen": iso, "last_seen": iso,
                "times_seen": 0, "dates": [], "venue": first.get("venue"), "referrers": [],
                "sample": first.get("sample") or "", "ref_keys": []}
            stats["new"] += 1
        for ob in obs:
            ref = ob["referrer"]
            rk = _ref_key(ref)
            if rk not in m.setdefault("ref_keys", []):
                m["ref_keys"].append(rk)
                m["ref_keys"].sort()
                m["times_seen"] = len(m["ref_keys"])
                if len(m.setdefault("referrers", [])) < MAX_REFERRERS:
                    m["referrers"].append(ref)
                    m["referrers"].sort(key=lambda r: (r.get("start") or "", r.get("source") or "",
                                                       r.get("title") or ""))
                changed = True
            for d in ob.get("dates") or []:
                if _date_end(d) >= horizon and d not in m.setdefault("dates", []):
                    m["dates"].append(d)
                    changed = True
            if not m.get("venue") and ob.get("venue"):
                m["venue"] = ob["venue"]
                changed = True
        m["dates"] = sorted(set(m.get("dates") or []))[-MAX_DATES:]
        if m.get("state") in ("pending", "covered"):
            state = "covered" if any(ob.get("covered") for ob in obs) else "pending"
            if m["state"] != state:
                m["state"] = state
                changed = True
        if changed:
            m["last_seen"] = iso
            if existed:
                stats["updated"] += 1
    for key in list(mentions):
        m = mentions[key]
        if key in declined and m.get("state") != "configured":
            # A name a human put in _not_included leaves the list, whatever
            # state it was in; the reason stays in _not_included.
            del mentions[key]
            stats["not_included"] += 1
            continue
        if m.get("state") in ("pending", "covered"):
            site = configured_site(m.get("name") or key, sites)
            if site:
                m["state"] = "configured"
                m["site"] = site
        if m.get("state") not in ("pending", "covered") or key in named_now:
            # Named this run by a current observation: its current dates were
            # folded above, and an undated naming keeps it as it kept it on
            # the run that added it.
            continue
        if m.get("dates"):
            if all(_date_end(d) < horizon for d in m["dates"]):
                del mentions[key]
                stats["pruned"] += 1
        else:
            last = dt.date.fromisoformat(m.get("last_seen") or iso)
            if last < today - dt.timedelta(days=UNDATED_PRUNE_DAYS):
                del mentions[key]
                stats["pruned"] += 1
    return stats


def read_queue(path: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """(queue, "") or (None, why). A missing file is an empty queue; one that
    is there and does not parse, or is not the queue's shape, is NOT.

    The file is hand-edited (a decline, a reason in _not_included), and a
    hand edit can break it. This used to read a broken file as {} and carry
    on: re-applying the 2026-11-08 scan onto the committed queue with one
    doubled comma added exited 0, reported "53 new", and wrote a queue with
    all 7 _not_included reasons gone and the 7 declined names back as
    pending (34 -> 41), which the commit step would have committed. A broken
    queue is now left byte for byte as it is, and the run exits 1, which the
    workflow already reads as "queue unchanged"."""
    if not os.path.exists(path):
        return {}, ""
    try:
        with open(path, encoding="utf-8") as fh:
            q = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"does not parse ({type(exc).__name__}: {str(exc)[:80]})"
    if not isinstance(q, dict):
        return None, "is not a JSON object"
    for k in ("_not_included", "mentions"):
        if k in q and not isinstance(q[k], dict):
            return None, f"has a {k} that is not an object"
    if any(not isinstance(m, dict) for m in (q.get("mentions") or {}).values()):
        return None, "has a mention that is not an object"
    return q, ""


def _write_atomic(path: str, body: str, prefix: str) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=prefix, dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        os.chmod(tmp, 0o644)           # mkstemp's 0600 is not what a repo file is
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_queue(path: str, queue: Dict[str, Any]) -> None:
    """Sorted keys, a trailing newline, written whole or not at all."""
    body = json.dumps(queue, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    _write_atomic(path, body, ".mention_queue.")


def newest_pending(queue: Dict[str, Any], today: dt.date, n: int = 15) -> List[Tuple[str, Dict[str, Any]]]:
    rows = [(k, m) for k, m in (queue.get("mentions") or {}).items() if m.get("state") == "pending"]

    def soon(m):
        recent = [d for d in m.get("dates") or []
                  if _date_end(d) >= today - dt.timedelta(days=PRUNE_DAYS)]
        return min(recent) if recent else "9999"
    rows.sort(key=lambda km: (-(dt.date.fromisoformat(km[1].get("first_seen") or "1970-01-01").toordinal()),
                              soon(km[1]), km[0]))
    return rows[:n]


def mention_line(key: str, m: Dict[str, Any]) -> str:
    when = ",".join(m.get("dates") or []) or "no date"
    venue = f" @ {m['venue']}" if m.get("venue") else ""
    ref = (m.get("referrers") or [{}])[0]
    return (f"  MENTION {m.get('name') or key} [{when}]{venue} x{m.get('times_seen', 0)} "
            f"<- {ref.get('source', '?')}: {(ref.get('title') or '')[:70]}")


def _today(s: Optional[str]) -> dt.date:
    s = s or os.environ.get("MAPSEE_TODAY")
    if s and re.fullmatch(r"\d{8}", s):
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return dt.date.today()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stores", nargs="+", default=[], help="EventStore JSON files or directories")
    ap.add_argument("--hits", help="re-apply a saved scan instead of reading stores")
    ap.add_argument("--save-hits", help="write this run's observations here (for re-applying)")
    ap.add_argument("--queue", default=QUEUE_FILE)
    ap.add_argument("--annual", default=ANNUAL_FILE)
    ap.add_argument("--today", help="YYYYMMDD (default MAPSEE_TODAY, then today)")
    ap.add_argument("--audit", help="write every candidate and its verdict here (measurement)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    today = _today(a.today)
    # Before the scan, so a broken hand edit costs a minute, not the scan's.
    queue, why = read_queue(a.queue)
    if queue is None:
        print(f"catalog_mentions: {a.queue} {why} - left as it is")
        return 1
    if a.hits:
        saved = load_json(a.hits, None)
        if not isinstance(saved, dict) or not isinstance(saved.get("observations"), list):
            print(f"catalog_mentions: {a.hits} is not a saved scan - queue left as it is")
            return 1
        observations = saved["observations"]
        if not a.today and saved.get("today"):
            today = dt.date.fromisoformat(saved["today"])
        stats = dict(saved.get("stats") or {})
    else:
        files = store_files(a.stores)
        if not files:
            print("catalog_mentions: no *_events.json store found in "
                  + (" ".join(a.stores) or "(nothing given)") + " - queue left as it is")
            return 1
        audit: Optional[List[Dict[str, Any]]] = [] if a.audit else None
        observations, stats = scan(files, today, audit)
        if a.audit:
            with open(a.audit, "w", encoding="utf-8") as fh:
                json.dump(audit, fh, ensure_ascii=False, indent=1)
        if a.save_hits:
            # Whole or not at all: the workflow step that writes it has a
            # timeout, and the commit step re-applies whatever is there.
            _write_atomic(a.save_hits, json.dumps({"today": today.isoformat(), "stats": stats,
                                                   "observations": observations},
                                                  ensure_ascii=False, indent=1), ".mention_hits.")
    merged = merge_queue(queue, observations, today, annual_sites(a.annual))
    save_queue(a.queue, queue)
    by_state: Dict[str, int] = {}
    for m in queue["mentions"].values():
        by_state[m.get("state", "?")] = by_state.get(m.get("state", "?"), 0) + 1
    if not a.quiet:
        print(f"catalog_mentions {today.isoformat()}: {stats.get('rows', 0):,} rows in "
              f"{stats.get('files', 0)} store(s), {stats.get('described', 0):,} with prose; "
              f"{len(observations)} mention(s) kept "
              f"({sum(1 for o in observations if o.get('covered'))} already carried as a title)")
        print(f"queue {a.queue}: {merged['new']} new, {merged['updated']} updated, "
              f"{merged['pruned']} pruned, {merged['not_included']} not included; "
              + ", ".join(f"{k} {v}" for k, v in sorted(by_state.items())))
        for key, m in newest_pending(queue, today):
            print(mention_line(key, m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
