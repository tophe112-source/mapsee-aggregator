#!/usr/bin/env python3
"""
test_mentions.py - catalog_mentions.py reads named events out of rows we
already ingested, and refuses what is not a lead.

The case it exists for: The Seattle Public Library's Trumba feed carried "The
Public Life of Comics" (2026-11-08), whose description named the Short Run
Comix Festival, its date and its venue a month before the festival - which was
on no channel the catalog had (curation-and-discovery.md, 2026-11-08). Every
refusal below is a class the first real run (611,881 rows, 2026-11-07 stores)
produced before it was a rule; the snippets are one sentence each, quoted or
paraphrased from those rows.

No network, no database. Runs the real scan() and main() on temporary stores.

Run: python test_mentions.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile

import catalog_mentions as M

fails = []


def check(label, cond, detail=""):
    if callable(cond):
        try:
            cond = cond()
        except Exception as exc:  # noqa: BLE001
            cond, detail = False, f"raised {type(exc).__name__}: {exc}"
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


TODAY = dt.date(2026, 11, 8)
SPL = ("Hear 4 cartoonists talk about their recent projects.&nbsp; This event is in "
       "coordination with the 14th annual Short Run Comix Festival, taking place on Saturday, "
       "November 7th from 11 am to 6 pm at Seattle Design Center in Georgetown. About the panelists:")


def row(title, desc, start="2026-11-08T14:00:00-08:00", source="ics:spl", url="https://www.spl.org/e/1"):
    return {"name": title, "description": desc, "start_local": start,
            "sources": [{"source": source, "source_id": title, "url": url}]}


def found(title, desc, start="2026-11-08T14:00:00-08:00"):
    return M.mentions_in_row(row(title, desc, start), TODAY)


def names(title, desc, start="2026-11-08T14:00:00-08:00"):
    return [m["name"] for m in found(title, desc, start)]


# --- the case it exists for --------------------------------------------------
sr = found("The Public Life of Comics", SPL)
check("SPL: Short Run Comix Festival is read out of the description",
      lambda: [m["name"] for m in sr] == ["Short Run Comix Festival"], sr)
check("SPL: its date, a yearless 'Saturday, November 7th', is 2026-11-07",
      lambda: sr[0]["dates"] == ["2026-11-07"], sr)
check("SPL: its venue is Seattle Design Center (read right after the date)",
      lambda: sr[0]["venue"] == "Seattle Design Center", sr)
check("SPL: the frame and the referrer are recorded",
      lambda: sr[0]["frame"] == "in coordination with"
      and sr[0]["referrer"] == {"source": "ics:spl", "title": "The Public Life of Comics",
                                "url": "https://www.spl.org/e/1", "start": "2026-11-08"})
check("SPL: the sample quotes the sentence, entities decoded",
      lambda: sr[0]["sample"].startswith("This event is in coordination with the 14th annual")
      and "&nbsp;" not in sr[0]["sample"] and len(sr[0]["sample"]) <= M.SAMPLE_CHARS)

# --- frames --------------------------------------------------------------------
check("'as part of the X Festival' is a mention",
      lambda: names("Gallery talk", "This talk is part of Wellington Heritage Festival.")
      == ["Wellington Heritage Festival"])
check("'the Nth annual X' is a mention without any other frame",
      lambda: names("Talk", "At the upcoming 13th Annual Islamic Arts Festival, visitors explore art.")
      == ["Islamic Arts Festival"])
check("'Nth-annual' inside the run ends the organiser's part of the name",
      lambda: names("Screening", "Join Cinema St. Louis's 35th-annual St. Louis International Film "
                    "Festival for a screening.") == ["St. Louis International Film Festival"])
check("an unframed name is not a mention (performers' credit lists are)",
      lambda: names("Concert", "They have played Desert Daze and Barcelona Psych Fest.") == [])
check("a non-English frame is read: nell'ambito di",
      lambda: names("Incontro", "Incontro nell’ambito di Empatica Festival") == ["Empatica Festival"])

# --- refusals --------------------------------------------------------------------
check("self: the row's own title names it",
      lambda: names("Short Run Comix Festival 2026", SPL) == [])
check("self: a festival's own session ('Wexford Reads Festival - In Conversation')",
      lambda: names("Wexford Reads Festival - In Conversation with Paul Howard",
                    "Celebrate Irish literature as part of the Wexford Reads Festival.") == [])
check("self: 'during THIS Fall Harvest Hoops Fest', under a title that names no occasion",
      lambda: names("Teen basketball showcase",
                    "During this Fall Harvest Hoops Fest, teens will showcase their skills.") == [])
check("self: every distinctive word in an occasion title (Hucknall)",
      lambda: names("Hucknall Beer and Cider Festival 2027",
                    "The annual Hucknall Beer Festival takes place in February.") == [])
check("self: an organiser's possessive in front of the row's own title",
      lambda: names("The Other Israel Film Festival: Screening & Talkback",
                    "In partnership with Marlene Meyerson JCC Manhattan’s The Other Israel Film "
                    "Festival, BHS is hosting a screening.") == [])
check("self: a generic title the name ends with ('2026 Holiday Festival')",
      lambda: names("2026 Holiday Festival", "Celebrate the season at the annual Levy Park Holiday "
                    "Festival.") == [])
for title, desc in (("Talk", "It was spread around during Banned Books Week."),
                    ("Death Cafe", "As part of National Grief Awareness Week 2026, join us."),
                    ("Hanukkah Luncheon", "Let us celebrate the Jewish Festival of Lights!"),
                    ("Diyas", "Light our homes during Diwali- the Festival of Lights."),
                    ("Party", "We celebrate the Festival of Lights together!"),
                    ("Dinner", "Grab a slice during Cleveland Pizza Week!")):
    check(f"observance or holiday refused: {desc[:46]!r}", lambda d=desc, t=title: names(t, d) == [])
check("generic: 'as part of Science Week' names no organiser",
      lambda: names("Storytime", "As part of Science Week, join a storytime.") == [])
check("own: 'as part of OUR Wexford Reads Festival'",
      lambda: names("Paul Howard", "Join us as part of our Wexford Reads Festival.") == [])
check("own: a library's own festival",
      lambda: names("Draw Pictures", "Part of the Sulzer Regional Library Comic & Arts Festival.") == [])
check("credit: 'has performed at ..., including the annual Cuban Jazz Festival'",
      lambda: names("Reggae Night", "He has performed at numerous events, including the annual "
                    "Cuban Jazz Festival.") == [])
check("past year after the name: 'as part of Good Things Festival in 2025'",
      lambda: names("Tour", "Their recent performances as part of Good Things Festival in 2025.") == [])
check("past year before the name: 'the 2024 Halifax Fringe Festival'",
      lambda: names("Play", "This piece, workshopped as part of the 2024 Halifax Fringe Festival, "
                    "is coming to stage.") == [])
check("past years in brackets: '(2016-2022)'",
      lambda: names("Gig", "Shows as part of the Perth Fringeworld Festival (2016-2022) ran yearly.") == [])
check("inside a longer proper name: '... Convention & Visitors Bureau'",
      lambda: names("Mozart", "The series is presented by the St. Marys Convention & Visitors Bureau.") == [])
check("inside a longer proper name: 'North Fair Oaks Library'",
      lambda: names("Lunch", "Free meal as part of North Fair Oaks Library lunches.") == [])
check("two names joined by 'and' end at the inner keyword",
      lambda: "Cuban Jazz Festival and Grape Grazing Festival" not in names(
          "X", "As part of the Cuban Jazz Festival and Grape Grazing Festival.") )
check("a hyphenated compound is not a keyword ('Micro-Festival #3')",
      lambda: names("Concerts", "Concerts dans le cadre du Micro-Festival #3") == [])

# --- dates -------------------------------------------------------------------------
check("yearless date resolves to the occurrence nearest the referring row (across a new year)",
      lambda: found("Talk", "As part of the Sydney Festival on January 10, a show.",
                    start="2026-12-20T10:00:00")[0]["dates"] == ["2027-01-10"])
check("yearless date before the row resolves backwards, not a year ahead",
      lambda: found("Talk", SPL, start="2026-11-09T10:00:00")[0]["dates"] == ["2026-11-07"])
check("a weekday that contradicts the date refuses the date, not the mention",
      lambda: found("Talk", "Part of the Seattle Design Festival on Friday, November 7th.")[0]["dates"] == [])
check("an explicit year is used as written",
      lambda: found("Talk", "Part of the Dhaatu International Puppet Festival, January 9, 2027.")[0]["dates"]
      == ["2027-01-09"])
check("a range with a month on both ends: '20th November to 27th November 2026'",
      lambda: found("Talk", "It launches the inaugural One Health Festival running from 20th November "
                    "to 27th November 2026.")[0]["dates"] == ["2026-11-20/2026-11-27"])
check("a range in brackets: '(Nov. 5–15)'",
      lambda: found("Talk", "This event is part of the Spirit & Place Festival (Nov. 5–15).")[0]["dates"]
      == ["2026-11-05/2026-11-15"])
check("a range across the new year: 'December 30 - January 2'",
      lambda: found("Talk", "Part of the Hogmanay Fair, December 30 - January 2.",
                    start="2026-12-01T10:00:00")[0]["dates"] == ["2026-12-30/2027-01-02"])
check("a mention whose date ended more than 30 days ago is not a lead",
      lambda: names("Talk", "Part of the Seattle Design Festival on September 12, 2026.") == [])
# The 240-character limit used to be a slice, which hid the "5" of "November
# 15" from the lookahead: read as November 1st (2026-11-08 review).
# The fixture is checked AS THE READER SEES IT: clean_text() collapses a
# double space, and one extra space once moved the cut past the "5" and let a
# slice-searching mutant pass.
SR_HEAD = "As part of the Short Run Comix Festival"
SR_TAIL = " taking place on November 1"
SR_FILL = (" lorem" * 40)[:240 - len(SR_TAIL)]
SR_TEXT = SR_HEAD + SR_FILL + SR_TAIL + "5 at Seattle Design Center."
check("(the fixture puts the 240th character between the '1' and the '5')",
      lambda: M.clean_text(SR_TEXT) == SR_TEXT
      and SR_TEXT[len(SR_HEAD):len(SR_HEAD) + 240].endswith("November 1")
      and M._sentence_end(SR_TEXT, len(SR_HEAD), 240) == len(SR_HEAD) + 240)
check("a date cut by the 240-character limit is read whole: November 15, not November 1",
      lambda: found("Talk", SR_TEXT)[0]["dates"] == ["2026-11-15"], found("Talk", SR_TEXT))
check("...and its venue is read after the whole date",
      lambda: found("Talk", SR_TEXT)[0]["venue"] == "Seattle Design Center")
check("a date that STARTS past the limit is still not read",
      lambda: found("Talk", SR_HEAD + SR_FILL + " lorem lorem taking place on November 15.")[0]
      ["dates"] == [])
V = (" from 11 am to 6 pm, with stalls and talks and readings and more besides, at Seattle Design Center"
     " in Georgetown.")
check("(the fixture puts the venue's 90th character inside 'Design')",
      lambda: V.index(" at ") < M.VENUE_AFTER_DATE < V.index("Center"))
check("a venue that starts inside the 90 characters is read whole, past them",
      lambda: M.venue_near(V, 0) == "Seattle Design Center", M.venue_near(V, 0))
check("the venue reader itself reads 'at Northcote Theatre' (a name that starts with 'No')",
      lambda: M.venue_near("A show at Northcote Theatre the next night.", 0) == "Northcote Theatre")
check("no venue without a date (a tour's next stop is not the festival's venue)",
      lambda: found("Gig", "A show as part of the Sydney Festival, continues onto Melbourne at "
                    "Northcote Theatre the next night.")[0]["venue"] is None)

# --- what is never copied --------------------------------------------------------
# Every address, number, name and festival here is invented: example.org, and
# fictitious numbers (NANP 555-01xx; Ofcom's 020 7946 0xxx and 07700 900xxx;
# ARCEP's 06 39 98 xx xx; ACMA's 0491 570 156). A real one, even a public
# office's, is never a fixture: this repository is public.
FEST = "In partnership with the Fernhollow Book Festival, "
check("a sample never carries an e-mail address or a phone number",
      lambda: "@" not in found("Journal", FEST + "write to someone@example.org or call "
                               "(206) 555-0100.")[0]["sample"]
      and "555-0100" not in found("Journal", FEST + "call (206) 555-0100.")[0]["sample"])
check("scrub leaves dates, year ranges and street numbers alone",
      lambda: M.scrub("On 2026-11-07 (2016-2022), 1000 4th Avenue, WA 98104") ==
      "On 2026-11-07 (2016-2022), 1000 4th Avenue, WA 98104")
# The 2026-11-08 review: the French pairs, the UK mobile's 5-6, the Spanish
# 3-2-2-2 and a bare 0-led run passed mask() whole. Re-scanning the 2026-11-07
# stores also found a bare 9-10 digit run in 12 refused candidates' samples,
# 11 of them right after "Contact Phone:" (2065550100 stands in for them).
PHONES = ["(206) 555-0100", "206-555-0142", "+44 20 7946 0958", "020 7946 0958",
          "06 39 98 01 23", "06.39.98.01.23", "+33 6 39 98 01 23", "+33 (0)6 39 98 01 23",
          "07700 900123", "07700 900 123", "+44 7700 900123", "555 01 01 23", "+34 555 01 01 23",
          "0491570156", "0491 570 156", "+61491570156", "2065550100", "+12065550100"]
UNMASKED = [p for p in PHONES for ctx in ("call {} now", "{}", "Tel: {}.", "({})")
            if any(ch.isdigit() for ch in M.mask(ctx.format(p)))]
check(f"mask() masks every phone shape whole ({len(PHONES)} numbers, 4 contexts each)",
      lambda: not UNMASKED, UNMASKED)
# ...and none of what a description is full of. "07.11.26 14.00" and the
# opening hours are what the French pairs would take with any separator
# between them: one separator throughout is what refuses them.
NOT_PHONES = ["On 2026-11-07 (2016-2022), 1000 4th Avenue, WA 98104", "2026-11-07T11:00:00-08:00",
              "2026-11-07 11:00-18:00", "07.11.2026", "07.11.2026 14:00", "01.07.2026 10.00",
              "07.11.26 14.00", "Mo 09.00-12.00 13.00-17.00", "08.00-10.00 14.00-16.00",
              "11/07/2026", "06/12/2026 18h", "2026/11/07", "Saturday 7 November 2026 at 11:00",
              "Nov 7-8, 2026", "30 11 2026", "20261107", "01022026", "2016 - 2022", "1900-1950",
              "11:00-18:00", "08.00 - 12.00", "09.00-17.00", "10 am - 6 pm", "19h30",
              "doors 19:30, show 20:00-23:00", "1201 2nd Ave", "75011 Paris", "01000 Bourg",
              "$1,250.00", "€12.50", "£5-£10", "$10 - $15", "1.500.000", "tickets 15 / 12 €",
              "ages 5-12", "Room 101", "the 14th annual"]
check(f"mask() leaves dates, years, times, street numbers and prices alone ({len(NOT_PHONES)} cases)",
      lambda: [s for s in NOT_PHONES if M.mask(s) != s] == [],
      [(s, M.mask(s)) for s in NOT_PHONES if M.mask(s) != s])
# The second 2026-11-08 review: after a phone word, Swedish, Belgian, German
# slash, Finnish, Irish-style and "ddd- ddd-dddd" groupings passed whole (950
# runs of 9+ digits in the 2026-11-07 stores). The shapes are the review's; the
# digits are fictitious numbers regrouped (PTS's 070-174 06 xx and 08-465 004
# xx, ARCEP, ACMA, Ofcom's 01632 960xxx, NANP 555-01xx). "0491 5701" is no
# number anywhere: Hong Kong's start 2-9.
KW_PHONES = ["ring 070-174 06 50", "tel 08-465 004 50", "tel 63 998 01 23", "gsm 0639 98 01 23",
             "Tel. 0491/570156", "Telefon 0491 / 570-156", "Tel (01632) 960123", "puh. 07 555 01234",
             "Tel: 0033 6 39 98 01 23", "Tel.: +46 70 174 06 50", "Phone: +61 7 5550 1234",
             "call 206- 555-0100", "Tlf. 555-0123", "Enquiries: 0491 5701", "ph. 0491/570156",
             "WhatsApp: +61 491 57 01 56", "Anmeldung unter 0639 / 98 01 23",
             "Tel.: (01632) 960 12-34 5", "telefon 0639-98 01 23."]
KW_LEFT = [(s, M.mask(s)) for s in KW_PHONES
           if any(ch.isdigit() for ch in M.mask(s)) or len(M.mask(s)) != len(s) or M.mask(M.mask(s)) != M.mask(s)]
check(f"after a phone word any grouping of 7+ digits is masked whole ({len(KW_PHONES)} numbers)",
      lambda: not KW_LEFT, KW_LEFT)
NOT_KW = ["Call for artists 2026", "Tel Aviv 2026-11-07", "Mobile, AL 36602", "boxing ring 3",
          "Tel. 555 01", "phone 2026", "unter 18 Jahren", "call 811 before you dig"]
check(f"a phone word with no 7-digit run after it masks nothing ({len(NOT_KW)} cases)",
      lambda: [s for s in NOT_KW if M.mask(s) != s] == [], [(s, M.mask(s)) for s in NOT_KW if M.mask(s) != s])
# The UK lead "0xxxx" swallowed a US ZIP that starts with 0, so the number
# after it kept its last four digits: 6 tribe rows of the 2026-11-07 stores
# ("CT 06xxx 860-xxx-xxxx"). The ZIP is not a phone number; the whole number is.
ZIPS = ["Hartford, CT 06103 860-555-0100", "Newark, NJ 07102 973-555-0100", "Boston, MA 02115 617 555 0100"]
check("a ZIP that starts with 0 is left alone and the number after it is masked whole",
      lambda: all(M.mask(s)[:-12] == s[:-12] and not any(c.isdigit() for c in M.mask(s)[-12:]) for s in ZIPS),
      [M.mask(s) for s in ZIPS])
# ...and the "0xxxx" lead keeps a German 5-digit area code with 7-8 digits
# after it. A fixed 3-3 tail (the review's first proposal) would let 68 runs
# of the same stores through, in 68 rows, 51 of them right after an e-mail
# address ("99999 9999 999" 32, "99999-9999999" 17).
DE5 = ["someone@example.org · 01632 9601 234", "someone@example.org · 01632-9601234",
       "(01632 9601234)", "Tel 01632 96012345"]
check("a 5-digit area code with 7-8 digits after it is masked whole",
      lambda: not [s for s in DE5 if any(c.isdigit() for c in M.mask(s))], [M.mask(s) for s in DE5])


def quoted_contacts():
    """Every address in catalog_mentions.py and this file that is not at
    example.org/.com/.net (or a cut of one), and every phone-shaped number
    that is not a fictitious one: NANP 555-01xx, Ofcom's 020 7946 0xxx,
    07700 900xxx and 01632 960xxx, ARCEP's 06 39 98 xx xx, ACMA's 0491 570
    xxx and (07) 5550 xxxx, PTS's 070-174 06 xx and 08-465 004 xx. A number
    counts whether its shape says so (_PHONE) or a phone word does
    (_PHONE_KW, 7 digits or more). The 2026-11-08 review found a county
    officer's mailbox and a pastor's name at a church's domain quoted here."""
    out = []
    here = os.path.dirname(os.path.abspath(__file__))
    for fname in ("catalog_mentions.py", "test_mentions.py"):
        with open(os.path.join(here, fname), encoding="utf-8") as fh:
            src = fh.read()
        for m in re.finditer(r"([\w.+-]+)@([\w.-]*)", src):
            local, dom = m.group(1), m.group(2).lower()
            # A bare local part ("name" and an @) is a cut of nothing: the
            # 2026-11-08 quotation was exactly that. Only a regex's own `+@`
            # has no domain.
            if not dom and local != "+":
                out.append((fname, m.group()))
            elif not any(d.startswith(dom) or dom.startswith(d)
                         for d in ("example.org", "example.com", "example.net")):
                out.append((fname, m.group()))
        numbers = [m.group() for m in M._PHONE.finditer(src)]
        numbers += [m.group("num") for m in M._PHONE_KW.finditer(src)
                    if sum(c.isdigit() for c in m.group("num")) >= M._KW_DIGITS]
        for num in numbers:
            digits = re.sub(r"\D", "", num)
            if not any(f in digits for f in ("55501", "79460", "7700900", "1632960", "63998",
                                             "491570", "7017406", "8465004")):
                out.append((fname, num))
    return out


QUOTED = []
check("no real address or phone number is quoted in catalog_mentions.py or this file",
      lambda: not (QUOTED.extend(quoted_contacts()) or QUOTED), QUOTED)
FR = "Dans le cadre du Fernhollow Festival. Réservation auprès de Jeanne : 06 39 98 01 23."
check("a French mobile in a kept sample is masked (the review's end-to-end case)",
      lambda: "39 98" not in found("Soirée", FR, start="2026-11-20T20:00:00+01:00")[0]["sample"],
      found("Soirée", FR, start="2026-11-20T20:00:00+01:00"))
check("mask() keeps every offset: the masked text is exactly as long",
      lambda: len(M.mask("write to someone@example.org or call (206) 555-0100 or 06 39 98 01 23 now"))
      == len("write to someone@example.org or call (206) 555-0100 or 06 39 98 01 23 now"))
# The cut used to come before the scrub: an address cut inside its domain and
# a number cut after its seventh digit were among the 2026-11-07 audit's
# samples. Every pad from 150 to 330 characters puts the cut somewhere in,
# before or after the address and the number.
LEAK = []
for pad in range(150, 331):
    for tail, bits in ((" write to jane.roe@example.org please.", ("@", "jane", "roe", "example")),
                       (" call +44 20 7946 0958 now.", ("7946", "0958", "+44 2"))):
        got = found("Journal", FEST + "x" * pad + tail)
        s = got[0]["sample"] if got else ""
        if any(b in s for b in bits) or len(s) > M.SAMPLE_CHARS:
            LEAK.append((pad, s[-40:]))
check("a sample never carries part of an address or a phone number, wherever the cut falls (362 cuts)",
      lambda: not LEAK, LEAK[:3])
check("a sample never ends inside a word: a cut word is dropped",
      lambda: found("J", FEST + "abcdefghij " * 40)[0]
      ["sample"].endswith("abcdefghij"))
TITLES = [M._referrer({"name": "Ask " + "y" * n + " jane.roe@example.org today"})["title"]
          for n in (120, 130)]                 # the cut at 140 falls after, and inside, "[address]"
check("a referrer title is masked and cut on a word, too",
      lambda: TITLES == ["Ask " + "y" * 120 + " [address]", "Ask " + "y" * 130], TITLES)


# --- scan(): covered needs every store ------------------------------------------
def write_store(d, name, rows):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"_meta": {"version": 1}, "events": rows}, fh)
    return p


with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, "feeds-ics-1"))
    os.makedirs(os.path.join(d, "feeds-local-1"))
    write_store(os.path.join(d, "feeds-ics-1"), "feeds_events.json",
                [row("The Public Life of Comics", SPL)])
    write_store(os.path.join(d, "feeds-local-1"), "feeds_events.json",
                [row("Short Run Comix Festival", "Comics all day.", start="2026-11-07T11:00:00-08:00",
                     source="jsonld")])
    with open(os.path.join(d, "feeds-ics-1", "rec_absence_manifest.json"), "w") as fh:
        fh.write("{}")
    files = M.store_files([d])
    check("store_files walks a directory for *_events.json and skips the manifests",
          lambda: len(files) == 2 and all(f.endswith("feeds_events.json") for f in files), files)
    obs, stats = M.scan(files, TODAY)
    check("covered: another store titles it within 3 days of the mentioned date",
          lambda: len(obs) == 1 and obs[0]["covered"] is True, obs)
    write_store(os.path.join(d, "feeds-local-1"), "feeds_events.json",
                [row("Short Run Comix Festival", "Comics.", start="2026-11-20T11:00:00-08:00",
                     source="jsonld")])
    obs2, _ = M.scan(M.store_files([d]), TODAY)
    check("not covered: the same title 13 days away is another occasion",
          lambda: obs2[0]["covered"] is False, obs2)
    check("scan counts rows across files",
          lambda: stats["rows"] == 2 and stats["files"] == 2, stats)

# --- the queue ---------------------------------------------------------------------
OB = {"key": "short run comix festival", "name": "Short Run Comix Festival",
      "dates": ["2026-11-07"], "venue": "Seattle Design Center", "sample": "s", "frame": "x",
      "covered": False, "referrer": {"source": "ics:spl", "title": "The Public Life of Comics",
                                     "url": "https://www.spl.org/e/1", "start": "2026-11-08"}}


def ob(**kw):
    o = json.loads(json.dumps(OB))
    ref = kw.pop("referrer", None)
    o.update(kw)
    if ref:
        o["referrer"].update(ref)
    return o


def merged(queue, obs, today=TODAY, sites=()):
    q = json.loads(json.dumps(queue))
    st = M.merge_queue(q, obs, today, list(sites))
    return q, st


def dump(q):
    return json.dumps(q, indent=2, ensure_ascii=False, sort_keys=True)


q1, s1 = merged({}, [ob()])
m1 = q1["mentions"]["short run comix festival"]
check("a new mention is pending, seen once, first_seen = last_seen = today",
      lambda: m1["state"] == "pending" and m1["times_seen"] == 1
      and m1["first_seen"] == m1["last_seen"] == "2026-11-08" and s1["new"] == 1)
q2, s2 = merged(q1, [ob()], today=TODAY + dt.timedelta(days=1))
check("the same referrer the next day changes NOTHING (not even last_seen)",
      lambda: dump(q2) == dump(q1) and s2["updated"] == 0, (s2, q2["mentions"]))
q3, _ = merged(q2, [ob(referrer={"title": "Comics Q&A", "start": "2026-11-05"})],
               today=TODAY + dt.timedelta(days=2))
m3 = q3["mentions"]["short run comix festival"]
check("a new referring row counts once and moves last_seen",
      lambda: m3["times_seen"] == 2 and m3["last_seen"] == "2026-11-10"
      and [r["title"] for r in m3["referrers"]] == ["Comics Q&A", "The Public Life of Comics"])
qa, _ = merged({}, [ob(referrer={"title": f"T{i}"}) for i in range(8)])
qb, _ = merged({}, list(reversed([ob(referrer={"title": f"T{i}"}) for i in range(8)])))
check("observation order does not change the bytes; referrers capped at 5, times_seen counts 8",
      lambda: dump(qa) == dump(qb) and len(qa["mentions"]["short run comix festival"]["referrers"]) == 5
      and qa["mentions"]["short run comix festival"]["times_seen"] == 8)
qc, _ = merged(q1, [ob(covered=True)])
check("covered: a pending mention some title now carries becomes covered",
      lambda: qc["mentions"]["short run comix festival"]["state"] == "covered")
SITES = [{"key": "short-run", "name": "Short Run Comix Festival", "name_rx": r"short\s+run",
          "url": "https://shortrun.org/"}]
qd, _ = merged(q1, [], sites=SITES)
check("configured: an annual_sources.json name_rx that matches it",
      lambda: qd["mentions"]["short run comix festival"]["state"] == "configured"
      and qd["mentions"]["short run comix festival"]["site"] == "short-run")
check("a missing annual_sources.json is an empty one",
      lambda: M.annual_sites(os.path.join(tempfile.gettempdir(), "no-such-annual.json")) == [])
qe = json.loads(json.dumps(q1))
qe["mentions"]["short run comix festival"]["state"] = "declined"
qe2, _ = merged(qe, [ob(covered=True)], sites=SITES)
check("declined is never changed by a run",
      lambda: qe2["mentions"]["short run comix festival"]["state"] == "declined")
qf = dict(json.loads(json.dumps(q1)), _not_included={"Short Run Comix Festival": "not a lead"})
qf2, sf = merged(qf, [ob()])
check("_not_included takes a name out and keeps it out",
      lambda: "short run comix festival" not in qf2["mentions"] and sf["not_included"] >= 1)

# Prune: dates ended more than 30 days ago, and not seen again with a current date.
qp, sp = merged(q1, [], today=dt.date(2026, 12, 8))
check("prune: 31 days after its last date, unseen, a pending mention is dropped",
      lambda: "short run comix festival" not in qp["mentions"] and sp["pruned"] == 1)
qk, _ = merged(q1, [], today=dt.date(2026, 12, 7))
check("prune: 30 days after, it stays",
      lambda: "short run comix festival" in qk["mentions"])
qn, _ = merged(q1, [ob(dates=["2027-11-06"], referrer={"title": "Next year"})], today=dt.date(2026, 12, 8))
check("prune: named again with next year's date, it stays and gains the date",
      lambda: qn["mentions"]["short run comix festival"]["dates"] == ["2026-11-07", "2027-11-06"])
qs, ss = merged({}, [ob()], today=dt.date(2026, 12, 8))
check("a NEW mention already that stale is never added (a re-applied old scan)",
      lambda: qs["mentions"] == {} and ss["stale"] == 1)
# Covered is re-decided by every run that names it, and pruned like pending
# (2026-11-08 review: it used to move one way only and never leave).
qcov, scov = merged(qc, [], today=dt.date(2026, 12, 8))
check("prune: a covered mention whose every date is stale, unnamed, is dropped too",
      lambda: "short run comix festival" not in qcov["mentions"] and scov["pruned"] == 1)
qnext, _ = merged(qc, [ob(dates=["2027-11-06"], covered=False, referrer={"title": "Next year",
                                                                          "start": "2027-11-01"})],
                  today=dt.date(2027, 11, 1))
check("covered -> pending: next year's edition, which no title carries, is a lead again",
      lambda: qnext["mentions"]["short run comix festival"]["state"] == "pending"
      and "2027-11-06" in qnext["mentions"]["short run comix festival"]["dates"], qnext["mentions"])
qback, _ = merged(qnext, [ob(dates=["2027-11-06"], covered=True, referrer={"title": "Next year",
                                                                           "start": "2027-11-01"})],
                  today=dt.date(2027, 11, 2))
check("pending -> covered again once a title carries the new edition",
      lambda: qback["mentions"]["short run comix festival"]["state"] == "covered")
qkeepcov, _ = merged(qc, [ob(covered=True)], today=TODAY + dt.timedelta(days=1))
check("a covered mention named again, still covered, changes nothing",
      lambda: dump(qkeepcov) == dump(qc))
qdecl = json.loads(json.dumps(q1))
qdecl["mentions"]["short run comix festival"]["state"] = "declined"
qdecl2, _ = merged(qdecl, [], today=dt.date(2027, 6, 1))
check("prune never touches a declined mention (a human's)",
      lambda: qdecl2["mentions"]["short run comix festival"]["state"] == "declined")

# ONE predicate adds, folds and prunes (2026-11-08 review). One Health
# Festival on 2026-11-07 was named by rows with a date and rows without one;
# once the date passed the horizon the old code created it and pruned it in
# one run every day (the real sort order, 90 of 140 replayed days), or, with
# the stale-dated row first as here, dropped and re-added it on alternate
# days with a new first_seen.
OH = dict(key="one health festival", name="One Health Festival")
oh_dated = ob(dates=["2026-11-20"], referrer={"title": "A talk", "start": "2026-11-01"}, **OH)
oh_undated = ob(dates=[], referrer={"title": "B talk", "start": "2026-11-05"}, **OH)
q_oh, days, bodies = {}, [], []
for i in range(6):                         # 2026-12-19 .. 12-24; the date goes stale on 12-21
    day = dt.date(2026, 12, 19) + dt.timedelta(days=i)
    q_oh, _st = merged(q_oh, [oh_dated, oh_undated], today=day)
    days.append("one health festival" in q_oh["mentions"])
    bodies.append(dump(q_oh))
check("dated + undated referrers: present on every day, before and after the date goes stale",
      lambda: all(days), days)
check("dated + undated referrers: the bytes are identical from the second day on (nothing new)",
      lambda: len(set(bodies[1:])) == 1, [b[:0] for b in bodies])
check("dated + undated referrers: first_seen is the first day, never reset",
      lambda: q_oh["mentions"]["one health festival"]["first_seen"] == "2026-12-19")
q_late, st_late = merged({}, [oh_dated, oh_undated], today=dt.date(2026, 12, 22))
check("a stale-dated observation is no sighting: added past the horizon, only its undated twin counts",
      lambda: q_late["mentions"]["one health festival"]["dates"] == []
      and q_late["mentions"]["one health festival"]["times_seen"] == 1 and st_late["stale"] == 1,
      (st_late, q_late["mentions"]))
q_gone, st_gone = merged(q_oh, [oh_dated], today=dt.date(2026, 12, 25))
q_gone2, st_gone2 = merged(q_gone, [oh_dated], today=dt.date(2026, 12, 26))
check("only the stale-dated row left: pruned, and not re-added the next day",
      lambda: "one health festival" not in q_gone["mentions"] and st_gone["pruned"] == 1
      and "one health festival" not in q_gone2["mentions"] and st_gone2["stale"] == 1
      and st_gone2["new"] == 0, (st_gone, st_gone2))
q_mixed, _ = merged({}, [ob(dates=["2026-09-01", "2026-12-12"])], today=TODAY)
check("only current dates are folded from an observation that carries both",
      lambda: q_mixed["mentions"]["short run comix festival"]["dates"] == ["2026-12-12"])

# The committed queue itself: a hand edit is what can break it.
QUEUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mention_queue.json")
STATES = {"pending", "covered", "configured", "declined"}


def committed_ok():
    with open(QUEUE_FILE, encoding="utf-8") as fh:
        q = json.load(fh)
    assert sorted(q) == ["_comment", "_not_included", "mentions"], sorted(q)
    assert isinstance(q["_not_included"], dict) and isinstance(q["mentions"], dict)
    bad = {k: m.get("state") for k, m in q["mentions"].items()
           if not isinstance(m, dict) or m.get("state") not in STATES}
    assert not bad, bad
    return True


check("the committed mention_queue.json parses, has exactly its three keys, every state known",
      committed_ok)
check("the committed queue carries no e-mail address",
      lambda: not M._EMAIL.search(open(QUEUE_FILE, encoding="utf-8").read()))


def unmasked_in_queue():
    """Every committed sample and referrer title that today's mask() would
    change: 0 of 138 (46 samples, 92 titles) on 2026-11-08, when _PHONE was
    widened to the French, Spanish, UK-mobile and bare-run shapes."""
    with open(QUEUE_FILE, encoding="utf-8") as fh:
        q = json.load(fh)
    texts = []
    for m in q["mentions"].values():
        texts.append(m.get("sample") or "")
        texts.extend(r.get("title") or "" for r in m.get("referrers") or [])
    return [t[:60] for t in texts if M.mask(t) != t]


UNMASKED_Q = []
check("every committed sample and referrer title is already masked by today's mask()",
      lambda: not (UNMASKED_Q.extend(unmasked_in_queue()) or UNMASKED_Q), UNMASKED_Q)
und, _ = merged({}, [ob(dates=[], key="copenhagen jazz festival", name="Copenhagen Jazz Festival")])
und_keep, _ = merged(und, [ob(dates=[], key="copenhagen jazz festival", name="Copenhagen Jazz Festival")],
                     today=dt.date(2027, 3, 1))
und_drop, _ = merged(und, [], today=dt.date(2027, 1, 8))
check("an undated pending mention stays while rows still name it",
      lambda: "copenhagen jazz festival" in und_keep["mentions"])
check("an undated pending mention nobody has named for 60 days is dropped",
      lambda: "copenhagen jazz festival" not in und_drop["mentions"])

# --- main(): the CLI, the re-apply path, and failing safe -------------------------
def safe_main(args):
    """main()'s exit code, or what it raised: a crash is a FAIL line, not a traceback."""
    try:
        return M.main(args)
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}"


with tempfile.TemporaryDirectory() as d:
    write_store(d, "feeds_events.json", [row("The Public Life of Comics", SPL)])
    queue = os.path.join(d, "mention_queue.json")
    hits = os.path.join(d, "snap", "mention_hits.json")
    annual = os.path.join(d, "annual_sources.json")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = M.main(["--stores", d, "--queue", queue, "--today", "20261101",
                     "--save-hits", hits, "--annual", annual])
    text = out.getvalue()
    check("main: exit 0, a header with counts, a '  MENTION ' line for Short Run",
          lambda: rc == 0 and text.startswith("catalog_mentions 2026-11-01: 1 rows")
          and "\n  MENTION Short Run Comix Festival [2026-11-07] @ Seattle Design Center" in text, text)
    body = open(queue, encoding="utf-8").read()
    check("main: the queue is sorted JSON with a trailing newline and the contract's top-level keys",
          lambda: body.endswith("}\n") and list(json.loads(body)) == ["_comment", "_not_included", "mentions"])
    with contextlib.redirect_stdout(io.StringIO()):
        M.main(["--stores", d, "--queue", queue, "--today", "20261101", "--annual", annual])
    check("main: a second run on the same stores changes nothing",
          lambda: open(queue, encoding="utf-8").read() == body)
    fresh = os.path.join(d, "fresh.json")
    with contextlib.redirect_stdout(io.StringIO()):
        rc2 = M.main(["--hits", hits, "--queue", fresh, "--quiet", "--annual", annual])
    check("main --hits: re-applying the saved scan onto a fresh base gives the same queue",
          lambda: rc2 == 0 and open(fresh, encoding="utf-8").read() == body)
    with open(annual, "w", encoding="utf-8") as fh:
        json.dump({"sites": SITES}, fh)
    with contextlib.redirect_stdout(io.StringIO()):
        M.main(["--hits", hits, "--queue", fresh, "--quiet", "--annual", annual])
    check("main --hits: a site added to annual_sources.json since makes it configured",
          lambda: json.load(open(fresh))["mentions"]["short run comix festival"]["state"] == "configured")
    empty = os.path.join(d, "empty")
    os.makedirs(empty)
    with contextlib.redirect_stdout(io.StringIO()):
        rc3 = M.main(["--stores", empty, "--queue", queue])
        rc4 = M.main(["--hits", os.path.join(d, "missing.json"), "--queue", queue])
    check("main: no store, or no saved scan, exits 1 and leaves the queue as it was",
          lambda: rc3 == 1 and rc4 == 1 and open(queue, encoding="utf-8").read() == body)
    # A hand edit that breaks the file (2026-11-08 review: one doubled comma
    # used to rebuild the queue from {}, dropping every _not_included reason).
    broken = body.replace('"_not_included": {}', '"_not_included": {"Congo Week": "x",,}', 1)
    for label, text in (("a doubled comma", broken), ("a list, not an object", "[]\n"),
                        ("mentions that is a list", '{"mentions": []}\n')):
        with open(queue, "w", encoding="utf-8") as fh:
            fh.write(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc5 = safe_main(["--hits", hits, "--queue", queue, "--annual", annual])
            rc6 = safe_main(["--stores", d, "--queue", queue, "--annual", annual])
        check(f"main: a queue with {label} exits 1 (both paths) and its bytes are left as they are",
              lambda: rc5 == 1 and rc6 == 1 and open(queue, encoding="utf-8").read() == text
              and "left as it is" in out.getvalue(), (rc5, rc6, out.getvalue()[:200]))
    check("main: the saved scan is written atomically (no temporary file left beside it)",
          lambda: sorted(os.listdir(os.path.dirname(hits))) == ["mention_hits.json"])

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
