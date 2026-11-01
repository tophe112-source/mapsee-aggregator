#!/usr/bin/env python3
"""
test_curate_mirror.py - one site, one feed; a single event's download is not a calendar.

On 2026-11-01, 31 hosts were configured under both tribe_sources.json (The
Events Calendar's REST route) and ics_sources.json (the same plugin's `?ical=1`
export), and six URLs of win-e-v.de all answered the same 10,229-VEVENT export,
so every event they publish was imported twice. 34 more iCal URLs were a single
event's download (one VEVENT, 33 of them past). catalog_curate.mirror_of and
_one_event_export are the rules that stop both coming back; this pins them,
and that verify and merge actually ask.

Run: python test_curate_mirror.py
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("MAPSEE_TODAY", "20261101")

import catalog_curate as cc  # noqa: E402

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("ok    " if cond else "FAIL  ") + label + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        fails += 1


# ---- the export's scope --------------------------------------------------------
check("the whole-site export is global", cc._tec_scope("https://a.org/events/?ical=1") == "global")
check("...from any page: the route answers for the site", cc._tec_scope("https://a.org/?ical=1") == "global"
      and cc._tec_scope("https://a.org/programm-oktober/?ical=1") == "global")
check("a category path is a slice", cc._tec_scope("https://a.org/events/category/film/?ical=1") == "archive")
check("...and so is a category in the query",
      cc._tec_scope("https://a.org/events/?post_type=tribe_events&ical=1&tribe_events_cat=volunteer") == "archive")
check("a plain .ics file is not a TEC export (einewelthaus.de: 0 shared UIDs)", cc._tec_scope("https://a.org/events.ics") is None)
check("no ical=1, no scope", cc._tec_scope("https://a.org/events/") is None)

# ---- what is a mirror of what ------------------------------------------------------
by_host = {
    "townhall.org": [("tribe", "https://townhall.org")],
    "mezrab.nl": [("ics", "https://mezrab.nl/events/?ical=1")],
    "wsu.edu": [("ics", "https://wsu.edu/thurston/events/category/mg/?post_type=tribe_events&ical=1")],
    "einewelt.de": [("ics", "https://einewelt.de/events.ics")],
}
m = cc.mirror_of({"type": "ics", "url": "https://www.townhall.org/events/?ical=1"}, by_host)
check("an export of a site whose REST feed is configured is a mirror, naming the REST entry", bool(m) and "townhall.org" in m and "REST" in m, m)
m = cc.mirror_of({"type": "ics", "url": "https://mezrab.nl/events/parviz/?ical=1"}, by_host)
check("a second export URL of a site with a global export is a mirror", bool(m) and "mezrab.nl/events/?ical=1" in m, m)
check("the configured URL itself is not its own mirror",
      cc.mirror_of({"type": "ics", "url": "https://mezrab.nl/events/?ical=1"}, by_host) is None)
check("two slices of one site are not mirrors of each other",
      cc.mirror_of({"type": "ics", "url": "https://wsu.edu/pierce/events/category/mg/?post_type=tribe_events&ical=1"}, by_host) is None)
m = cc.mirror_of({"type": "ics", "url": "https://wsu.edu/events/?ical=1"}, by_host)
check("the whole export beside a configured slice is reported, for a hand swap", bool(m) and "swap by hand" in m, m)
check("a plain .ics beside a REST feed is not provably the same calendar",
      cc.mirror_of({"type": "ics", "url": "https://townhall.org/events.ics"}, by_host) is None)
check("an export on a host with nothing configured is no mirror",
      cc.mirror_of({"type": "ics", "url": "https://new.org/events/?ical=1"}, by_host) is None)
m = cc.mirror_of({"type": "tribe", "base_url": "https://www.mezrab.nl"}, by_host)
check("a REST candidate for a site whose export is configured says: retire the export, then add", bool(m) and "retire the export" in m, m)
check("...but not for a site whose configured ics is a plain file",
      cc.mirror_of({"type": "tribe", "base_url": "https://einewelt.de"}, by_host) is None)
check("other types are never mirrors here", cc.mirror_of({"type": "jsonld", "listing": ["https://townhall.org/events/"]}, by_host) is None)
# the real index reads both files and keys hosts without www
idx = cc._config_feeds_by_host()
check("the live index is keyed by bare host and holds both types",
      idx and all(not h.startswith("www.") for h in idx) and {t for v in idx.values() for t, _u in v} >= {"ics", "tribe"},
      f"{len(idx)} hosts")

# ---- verify and merge ask -------------------------------------------------------------
src = open(os.path.join(HERE, "catalog_curate.py"), encoding="utf-8").read()
verify_src = src[src.index("def cmd_verify("):src.index("def cmd_robots(")]
merge_src = src[src.index("def cmd_merge("):src.index("# --- re-applying a finished run")]
check("verify asks mirror_of before any request, and never of a candidate already configured",
      "why_mirror = None if key in in_config else mirror_of(e, feeds_by_host)" in verify_src
      and verify_src.index("mirror_of(") < verify_src.index("rec = led.get(lkey)"))
check("merge asks mirror_of and remembers what it has just added",
      "why_mirror = mirror_of(e, feeds_by_host)" in merge_src and "feeds_by_host.setdefault(_host_of(kv)" in merge_src)

# ---- a single event's download ------------------------------------------------------------
one = ["https://sprachmobil.ch/events/das-sprachmobil-nach-riehen-bs-2026-09-21/ical/",
       "https://hoffnungszentrum.de/events/ue50-cafe-5/ical/",
       "https://postspace.be/guest/herbarium-algae/event.ics",
       "https://trockendock-hamburg.de/site/assets/files/4691/event.ics",
       "https://casanostra-bb.de/termine/event/termin/weltmaedchentag-20261006-1530-1730.ics",
       "https://termine-e.de/api/events/download.ics?ids[]=3b0c65bd-6c42-458c",
       "https://theater-essen.de/kalender-eintrag/3965/ical-2026-10-25-3965.ics",
       "https://monsenbaroeul.fr/node/3954/field_date/full/0/addtocal.ics"]
check("the per-event shapes match", all(cc._one_event_export(u) for u in one), [u for u in one if not cc._one_event_export(u)])
whole = ["https://librairielebaobab.com/events/ical/", "https://villa-wellentanz.ch/events/ical/",
         "https://effinghamil.com/event/city-council-meeting-2026-10-06/?ical=1",
         "https://win-e-v.de/events/49869-2026-10-08/?ical=1", "https://a.org/events/?ical=1", "https://a.org/calendar.ics"]
check("whole calendars do not: /events/ical/, a ?ical=1 export, a plain file",
      not any(cc._one_event_export(u) for u in whole), [u for u in whole if cc._one_event_export(u)])
# The shape alone is not the rule: feeds.apps.wayne.edu/events/main/ical has
# the per-event shape and 167 VEVENTs. The count is what keeps it (below).
check("a whole calendar can wear the per-event shape", cc._one_event_export("https://feeds.apps.wayne.edu/events/main/ical"))

CAL = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\nBEGIN:VEVENT\r\nUID:1@t\r\nDTSTART:20261215T180000Z\r\n"
       "DTEND:20261215T200000Z\r\nSUMMARY:One night only\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class _R:
    status_code = 200
    url = ""
    headers = {"content-type": "text/calendar; charset=utf-8"}

    def __init__(self, text):
        self.text = text
        self.content = text.encode("utf-8")
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class _S:
    def get(self, url, **_kw):
        r = _R(CAL)
        r.url = url
        return r


os.environ.pop("GOOGLE_CALENDAR_API_KEY", None)
ok, note = cc.verify_ics(_S(), {"url": "https://sprachmobil.ch/events/the-next-one/ical/"})
check("one future VEVENT behind a per-event URL fails verification as a single event's download",
      ok is False and "single event" in str(note) and getattr(note, "status", None) == "fail", note)
ok, note = cc.verify_ics(_S(), {"url": "https://sprachmobil.ch/events/?ical=1"})
check("the same one VEVENT behind the whole-site export still verifies", ok is True and "1 vevents / 1 future" in str(note), note)
# the audit reads the note's own status; a plain '1 vevents / 1 future' is not fail
check("the note names the shape it matched", "/events/the-next-one/ical/" in str(cc.verify_ics(_S(), {"url": "https://x.org/events/the-next-one/ical/"})[1]))

class _Many(_S):
    def get(self, url, **_kw):
        body = CAL.replace("END:VCALENDAR", "".join(
            f"BEGIN:VEVENT\r\nUID:{i}@t\r\nDTSTART:202612{15 + i % 10:02d}T180000Z\r\nSUMMARY:n{i}\r\nEND:VEVENT\r\n" for i in range(2, 168)) + "END:VCALENDAR")
        r = _R(body)
        r.url = url
        return r


ok, note = cc.verify_ics(_Many(), {"url": "https://feeds.apps.wayne.edu/events/main/ical"})
check("...and 167 VEVENTs behind that shape verify as the calendar they are", ok is True and "167 vevents" in str(note), note)

print("\n" + (f"{fails} failed" if fails else "all mirror checks passed"))
sys.exit(1 if fails else 0)
