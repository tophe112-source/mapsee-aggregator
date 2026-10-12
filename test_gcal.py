#!/usr/bin/env python3
"""
test_gcal.py - a Google calendar read through the Calendar API keeps its rows.

WHY THIS EXISTS. calendar.google.com/robots.txt refuses the iCal export, so with
GOOGLE_CALENDAR_API_KEY set a Google calendar is read through the Calendar API
and turned back into VCALENDAR text for the ics adapter (mapsee_gcal.py). The
switch is only safe if a row keeps its identity across it. A fingerprint is
(title, date, venue), and the date is the one the ics parser reads off DTSTART:
the UTC date for a `Z` time, the local date for a TZID time. Production says
Google's export writes a one-off event in UTC (221 of 227 rows from 7 Google
calendars, 2026-09-29) and a series in its own zone. Get that wrong and every
evening event in the Americas moves to a new row on the day the key goes in.

The parity test runs the SAME events through the REAL ingest_ics twice: once as
the export writes them, and once through the API route with a stub session. It
compares what reached the store. Everything else here pins the parts that fail
silently: the key must never be in a URL, a series needs a UID per instance
(EventStore re-keys a shared one down to its last instance), a missing key
must change nothing, and discovery must find the embed Google Sites renders.

No network.

    python test_gcal.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.pop("GOOGLE_CALENDAR_API_KEY", None)

import mapsee_gcal as G
import mapsee_ingest_ics as ICS
import catalog_discover_osm as OSM
import catalog_curate as cc

fails = []
KEY = "AIzaTEST-not-a-real-key-000000000000"


def check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(label)


class Resp:
    def __init__(self, code=200, data=None, text=None):
        self.status_code = code
        self._data = data
        self.text = text if text is not None else json.dumps(data or {})
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}
        self.encoding = "utf-8"

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class Session:
    """Answers by URL prefix; records (url, params, headers) of every request."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.asked = []

    def get(self, url, params=None, headers=None, timeout=None, **_kw):
        self.asked.append((url, dict(params or {}), dict(headers or {})))
        for prefix, answer in self.routes:
            if url.startswith(prefix):
                return answer(url, params or {}) if callable(answer) else answer
        return Resp(404, {"error": {"code": 404, "message": "Not Found", "errors": [{"reason": "notFound"}]}})


# --- 1. the identity of a calendar ---------------------------------------------
for cid in ("wprtwatch@gmail.com", "c_abc123@group.calendar.google.com", "en.irish#holiday@group.v.calendar.google.com"):
    check(f"ical_url and calendar_id round-trip {cid}", G.calendar_id(G.ical_url(cid)) == cid)
check("the export URL is spelled the way ics_sources.json spells it (@ as %40)",
      G.ical_url("calendar@erithyachtclub.org.uk")
      == "https://calendar.google.com/calendar/ical/calendar%40erithyachtclub.org.uk/public/basic.ics")
check("the old www.google.com export address is the same calendar (it 302s to calendar.google.com)",
      G.calendar_id("https://www.google.com/calendar/ical/abc%40group.calendar.google.com/public/basic.ics")
      == "abc@group.calendar.google.com")
cfg = json.load(open("ics_sources.json", encoding="utf-8"))
google = [s["url"] for s in cfg if "calendar.google.com" in s["url"]]
check("every configured Google calendar is recognised", google and all(G.calendar_id(u) for u in google),
      [u for u in google if not G.calendar_id(u)][:3])
check("a secret address is told apart", G.is_secret_address(
    "https://calendar.google.com/calendar/ical/x%40gmail.com/private-001e9d/basic.ics"))
check("webcal:// is the same calendar", G.calendar_id("webcal://calendar.google.com/calendar/ical/a%40b.c/public/basic.ics") == "a@b.c")
check("an ordinary feed is not a Google calendar", G.calendar_id("https://fiu.libcal.com/ical_subscribe.php?cid=1") is None)


# --- 2. the text the ics adapter will read ---------------------------------------
today = datetime.now(timezone.utc).date()
D = today + timedelta(days=12)                # far enough ahead that no zone makes it past
d = D.isoformat()
d1 = (D + timedelta(days=1)).isoformat()
d8 = (D + timedelta(days=8)).isoformat()
d15 = (D + timedelta(days=15)).isoformat()
d3 = (D + timedelta(days=3)).isoformat()
d5 = (D + timedelta(days=5)).isoformat()
META = {"summary": "Judkins Park Neighbors", "timeZone": "America/Los_Angeles"}
ITEMS = [
    {"id": "bbq1", "iCalUID": "bbq1@google.com", "status": "confirmed", "summary": "Fall BBQ, burgers; drinks",
     "location": "Judkins Park Picnic Shelter, Seattle", "description": "Bring a side <b>to share</b>",
     "start": {"dateTime": f"{d}T14:00:00-07:00"}, "end": {"dateTime": f"{d}T17:00:00-07:00"}},
    # 9:30pm in Edmonton is 03:30 UTC the NEXT day: the fingerprint's date is UTC's.
    {"id": "late1", "iCalUID": "late1@google.com", "status": "confirmed", "summary": "Midnight Movie",
     "location": "Garneau Theatre, Edmonton", "start": {"dateTime": f"{d}T21:30:00-06:00"},
     "end": {"dateTime": f"{d}T23:30:00-06:00"}},
    {"id": "fair1", "iCalUID": "fair1@google.com", "status": "confirmed", "summary": "Harvest Fair",
     "location": "Judkins Park", "start": {"date": d3}, "end": {"date": d5}},
    # A weekly series: the API expands it; the export has one VEVENT with an RRULE.
    *[{"id": f"walk_{x.replace('-', '')}", "iCalUID": "walk@google.com", "recurringEventId": "walk",
       "status": "confirmed", "summary": "Park Cleanup", "location": "Judkins Park",
       "originalStartTime": {"dateTime": f"{x}T10:00:00-07:00", "timeZone": "America/Los_Angeles"},
       "start": {"dateTime": f"{x}T10:00:00-07:00", "timeZone": "America/Los_Angeles"},
       "end": {"dateTime": f"{x}T12:00:00-07:00", "timeZone": "America/Los_Angeles"}} for x in (d1, d8, d15)],
    {"id": "priv", "iCalUID": "priv@google.com", "status": "confirmed", "visibility": "private",
     "start": {"dateTime": f"{d}T09:00:00-07:00"}, "end": {"dateTime": f"{d}T10:00:00-07:00"}},
    {"id": "gone", "iCalUID": "gone@google.com", "status": "cancelled", "summary": "Called off",
     "location": "Judkins Park", "start": {"dateTime": f"{d}T09:00:00-07:00"}},
    {"id": "bday", "iCalUID": "bday@google.com", "status": "confirmed", "eventType": "birthday",
     "summary": "Sam's birthday", "start": {"date": d}, "end": {"date": d1}},
]
text = G.to_ics(META, ITEMS)
check("a one-off event is written in UTC, as the export writes it",
      f"DTSTART:{d.replace('-', '')}T210000Z" in text, text[:400])
check("an instance of a series is written in its own zone",
      f"DTSTART;TZID=America/Los_Angeles:{d1.replace('-', '')}T100000" in text)
check("an all-day event is a DATE, and its end stays exclusive",
      f"DTSTART;VALUE=DATE:{d3.replace('-', '')}" in text and f"DTEND;VALUE=DATE:{d5.replace('-', '')}" in text)
evs = ICS.parse_ics(text)
titles = [ICS._summary(e) for e in evs]
check("private, cancelled and birthday items are left out", sorted(set(titles)) ==
      sorted({"Fall BBQ, burgers; drinks", "Midnight Movie", "Harvest Fair", "Park Cleanup"}), titles)
check("commas and semicolons survive the round trip", "Fall BBQ, burgers; drinks" in titles)
uids = [e["UID"][0] for e in evs]
check("a one-off event keeps the export's UID exactly", "bbq1@google.com" in uids)
walk = [u for u in uids if u.startswith("walk@google.com")]
check("each instance of a series has a UID of its own", len(walk) == 3 and len(set(walk)) == 3, walk)
check("...and the same UID on the next run", walk == [e["UID"][0] for e in ICS.parse_ics(G.to_ics(META, ITEMS))
                                                  if e["UID"][0].startswith("walk@google.com")])


# --- 3. parity: the same events, through the export and through the API ----------
def dt_utc(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


EXPORT = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "X-WR-TIMEZONE:America/Los_Angeles",
    "BEGIN:VEVENT", f"DTSTART:{dt_utc(d + 'T14:00:00-07:00')}", f"DTEND:{dt_utc(d + 'T17:00:00-07:00')}",
    "UID:bbq1@google.com", "SUMMARY:Fall BBQ\\, burgers\\; drinks", "LOCATION:Judkins Park Picnic Shelter\\, Seattle",
    "STATUS:CONFIRMED", "END:VEVENT",
    "BEGIN:VEVENT", f"DTSTART:{dt_utc(d + 'T21:30:00-06:00')}", f"DTEND:{dt_utc(d + 'T23:30:00-06:00')}",
    "UID:late1@google.com", "SUMMARY:Midnight Movie", "LOCATION:Garneau Theatre\\, Edmonton",
    "STATUS:CONFIRMED", "END:VEVENT",
    "BEGIN:VEVENT", f"DTSTART;VALUE=DATE:{d3.replace('-', '')}", f"DTEND;VALUE=DATE:{d5.replace('-', '')}",
    "UID:fair1@google.com", "SUMMARY:Harvest Fair", "LOCATION:Judkins Park", "STATUS:CONFIRMED", "END:VEVENT",
    "BEGIN:VEVENT", f"DTSTART;TZID=America/Los_Angeles:{d1.replace('-', '')}T100000",
    f"DTEND;TZID=America/Los_Angeles:{d1.replace('-', '')}T120000", "RRULE:FREQ=WEEKLY",
    "UID:walk@google.com", "SUMMARY:Park Cleanup", "LOCATION:Judkins Park", "STATUS:CONFIRMED", "END:VEVENT",
    "END:VCALENDAR", ""])


class Store:
    def __init__(self):
        self.events = []

    def upsert(self, ev):
        self.events.append(ev)
        return "new"


def _geocoder(_session, _suffix):
    return lambda _loc: (47.59, -122.30)


SRC = {"name": "Judkins Park Neighbors", "category": "community",
       "url": "https://calendar.google.com/calendar/ical/jpn%40gmail.com/public/basic.ics"}
old_store, api_store = Store(), Store()
with patch.object(ICS, "_fetch_ics", return_value=(EXPORT, "200", None)), \
     patch.object(ICS, "make_location_geocoder", side_effect=_geocoder):
    ICS.ingest_ics(old_store, None, SRC)
api = Session([("https://www.googleapis.com/calendar/v3/", Resp(200, dict(META, items=ITEMS)))])
with patch.dict(os.environ, {"GOOGLE_CALENDAR_API_KEY": KEY}), \
     patch.object(ICS, "make_location_geocoder", side_effect=_geocoder):
    ICS.ingest_ics(api_store, api, SRC)
old_fp = {e.fingerprint: e for e in old_store.events}
new_fp = {e.fingerprint: e for e in api_store.events}
check("every row the export made is made again, under the same fingerprint",
      set(old_fp) <= set(new_fp), sorted(e.name for f, e in old_fp.items() if f not in new_fp))
late = [e for e in api_store.events if e.name == "Midnight Movie"]
check("...including the evening event whose UTC date is the next day",
      late and late[0].start_utc.startswith(d1), late and late[0].start_utc)
check("a one-off event keeps its source_id (the export's UID)",
      {e.source_id for e in old_store.events if e.name == "Fall BBQ, burgers; drinks"}
      == {e.source_id for e in api_store.events if e.name == "Fall BBQ, burgers; drinks"})
cleanups = [e for e in api_store.events if e.name == "Park Cleanup"]
check("the series brings every instance, where the export's first VEVENT brought one",
      len(cleanups) == 3 and len([e for e in old_store.events if e.name == "Park Cleanup"]) == 1,
      f"{len(cleanups)} vs {len([e for e in old_store.events if e.name == 'Park Cleanup'])}")


# --- 4. the key: in a header, never in a URL; and no key changes nothing --------
url, params, headers = api.asked[0]
check("the key travels in X-Goog-Api-Key", headers.get("X-Goog-Api-Key") == KEY, headers)
check("...and in neither the URL nor the query", KEY not in url and KEY not in json.dumps(params), (url, params))
check("the read asks for instances, in order, from today",
      params.get("singleEvents") == "true" and params.get("orderBy") == "startTime"
      and str(params.get("timeMin", "")).startswith(today.isoformat()), params)
check("...and 180 days ahead by default, the catalog's usual window",
      str(params.get("timeMax", "")).startswith((today + timedelta(days=G.DAYS_AHEAD)).isoformat())
      and G.DAYS_AHEAD == 180, params.get("timeMax"))
near = Session([("https://www.googleapis.com/calendar/v3/", Resp(200, dict(META, items=ITEMS)))])
with patch.dict(os.environ, {"GOOGLE_CALENDAR_API_KEY": KEY}), \
     patch.object(ICS, "make_location_geocoder", side_effect=_geocoder):
    ICS.ingest_ics(Store(), near, dict(SRC, within_days=30))
check("a source's within_days sets how far the API reads",
      str(near.asked[0][1].get("timeMax", "")).startswith((today + timedelta(days=30)).isoformat()),
      near.asked[0][1].get("timeMax"))
plain = Session([("https://calendar.google.com/", Resp(200, None, text=EXPORT))])
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("GOOGLE_CALENDAR_API_KEY", None)
    got = ICS._fetch_ics(plain, SRC["url"])
check("without the key a Google calendar is fetched exactly as before",
      plain.asked and plain.asked[0][0] == SRC["url"] and got[1] == "200", plain.asked)
with patch.dict(os.environ, {"GOOGLE_CALENDAR_API_KEY": KEY}):
    route = ICS._fetch_ics(api, SRC["url"])
check("with the key the fetch says so (`api`)", route[1] == "api")

denied = Session([("https://www.googleapis.com/", Resp(404, {"error": {"code": 404, "message": "Not Found",
                                                                       "errors": [{"reason": "notFound"}]}}))])
try:
    G.fetch_as_ics(denied, SRC["url"], KEY)
    msg = ""
except G.GcalError as ex:
    msg = str(ex)
check("a calendar the API will not give us is an error that names why", "404" in msg and "notFound" in msg, msg)
check("...and the message cannot carry the key", KEY not in msg)
secret = Session([])
try:
    G.fetch_as_ics(secret, "https://calendar.google.com/calendar/ical/x%40gmail.com/private-001e9d/basic.ics", KEY)
    msg = ""
except G.GcalError as ex:
    msg = str(ex)
check("a secret-address calendar is refused without a request", "secret-address" in msg and not secret.asked, msg)

pages = {"n": 0}


def paged(_url, params):
    pages["n"] += 1
    if not params.get("pageToken"):
        return Resp(200, dict(META, items=ITEMS[:2], nextPageToken="p2"))
    return Resp(200, dict(META, items=ITEMS[2:4]))


meta, items = G.fetch_events(Session([("https://www.googleapis.com/", paged)]), "jpn@gmail.com", KEY)
check("a second page is followed", pages["n"] == 2 and len(items) == 4, (pages, len(items)))
pages["n"] = 0
meta, items = G.fetch_events(Session([("https://www.googleapis.com/", paged)]), "jpn@gmail.com", KEY, max_items=2)
check("...and not once max_items is reached", pages["n"] == 1 and len(items) == 2, (pages, len(items)))


# --- 5. finding the calendar a page embeds -------------------------------------
SITES_BLOCK = ('<iframe jsname="L5Fo6c" frameborder="0" aria-label="Calendar, wprtwatch@gmail.com" '
               'src="https://www.google.com/calendar/embed?color=%239fe1e7&amp;embed_style=WyJhdDplbWI&amp;'
               'eopt=2&amp;mode=month&amp;showCalendars=1&amp;src=wprtwatch@gmail.com" allowfullscreen></iframe>')
MULTI = ('<iframe src="https://calendar.google.com/calendar/embed?height=600&amp;ctz=America%2FChicago'
         '&amp;src=30a14f41%40group.calendar.google.com&amp;src=3ccc3902%40group.calendar.google.com'
         '&amp;src=en.usa%23holiday%40group.v.calendar.google.com"></iframe>')
LINK = '<a href="https://www.google.com/calendar/embed?src=0j1667ft5gdld6esbsctjt6eg0@group.calendar.google.com">Open</a>'
B64 = ('<iframe src="https://calendar.google.com/calendar/u/0/embed?src=c3NiY0Bzd29yZHNzYWlsaW5nLmll'
       '&amp;src=ZW4uaXJpc2gjaG9saWRheUBncm91cC52LmNhbGVuZGFyLmdvb2dsZS5jb20"></iframe>')
check("a Google Sites Calendar block (www.google.com, &amp;)", G.embed_ids(SITES_BLOCK) == ["wprtwatch@gmail.com"])
check("an embed with several calendars, Google's holidays left out",
      G.embed_ids(MULTI) == ["30a14f41@group.calendar.google.com", "3ccc3902@group.calendar.google.com"], G.embed_ids(MULTI))
check("the 'open in a new window' link", G.embed_ids(LINK) == ["0j1667ft5gdld6esbsctjt6eg0@group.calendar.google.com"])
check("a base64 id is decoded, and a base64 holiday calendar still dropped",
      G.embed_ids(B64) == ["ssbc@swordssailing.ie"], G.embed_ids(B64))
check("the same calendar twice is one", G.embed_ids(SITES_BLOCK + LINK.replace("0j1667ft5gdld6esbsctjt6eg0@group.calendar.google.com", "wprtwatch@gmail.com")) == ["wprtwatch@gmail.com"])
check("a page with no calendar has none", G.embed_ids('<iframe src="https://www.google.com/maps/embed?pb=1"></iframe>') == [])

labels, ics = OSM.fingerprint("<html><body>" + SITES_BLOCK + "</body></html>")
check("discovery reads the embed as a calendar, under its export URL",
      labels == ["gcal-embed"] and ics == G.ical_url("wprtwatch@gmail.com"), (labels, ics))
check("...and hands it to the ics adapter", OSM.adapter_for(labels) == "ics")
labels, ics = OSM.fingerprint('<a href="/events.ics">Subscribe</a>' + SITES_BLOCK)
check("a page that links its own .ics keeps that link", ics == "/events.ics" and "gcal-embed" not in labels, (labels, ics))
check("the embed outranks the page's own Event block", OSM.adapter_for(["gcal-embed", "jsonld-event"]) == "ics")
check("...and a real events platform outranks the embed", OSM.adapter_for(["tribe", "gcal-embed"]) == "tribe")


# --- 5b. every other form a page keeps a calendar id in (page_ids) --------------
# polishhome.org/calendar/ (a Seattle community centre), 2026-10-12, trimmed:
# Pretty Google Calendar draws the calendar itself, so there is no iframe. The
# site's own key sits beside the id as "google_api"; the real one is replaced
# by FAKE_SITE_KEY, and nothing may ever hand it back.
PH_ID = "d83853455e5cebb55a95bc30f0719941cc219c65f48a1cd6228d3f1ed34a7e09@group.calendar.google.com"
FAKE_SITE_KEY = "AIzaSyFAKE-site-owners-key-not-real-0000"
POLISH_HOME_CAL = """<html><head><title>Calendar - Polish Home Association</title>
<script id="pgcal_helpers-js" src="https://polishhome.org/wp-content/plugins/pretty-google-calendar/public/js/helpers.js?ver=2.2.1"></script>
</head><body><h1 class="entry-title">Calendar</h1><div class="entry-content">
 <p class="wp-block-paragraph">
 <div id='pgcalendar-0b319af336f45404d0e731b9f8f3c058bda1fd75a1190e981aa1aad7acf93aca' class='pgcal-container'>loading...</div>
 <div class='pgcal-branding'>Powered by <a href='https://wordpress.org/plugins/pretty-google-calendar/'>Pretty Google Calendar</a></div>
 </p></div>
<script id="pgcal_loader-js" src="https://polishhome.org/wp-content/plugins/pretty-google-calendar/public/js/pgcal.js?ver=2.2.1"></script>
<script id="pgcal_loader-js-after">
document.addEventListener('DOMContentLoaded', function() {
      function pgcal_inlineScript(settings) {
        var ajaxurl = 'https://polishhome.org/wp-admin/admin-ajax.php';
        var pgcal_ajax_nonce = '9e5a276a62';
        pgcal_render_calendar(settings, ajaxurl, pgcal_ajax_nonce);
      }

      pgcal_inlineScript({"gcal":"%s","cal_ids":"","locale":"en","list_type":"listCustom","custom_list_button":"list","custom_days":"28","views":"dayGridMonth, listCustom","initial_view":"dayGridMonth","enforce_listview_on_mobile":"true","show_today_button":"true","show_title":"true","id_hash":"0b319af336f45404d0e731b9f8f3c058bda1fd75a1190e981aa1aad7acf93aca","use_tooltip":"true","no_link":"false","hide_past":"false","fc_args":"{}","google_api":"%s"});
    });
</script></body></html>""" % (PH_ID, FAKE_SITE_KEY)
# A Simple Calendar page (the google-calendar-events plugin), trimmed from
# artisticdynamicassociation.eu/calendar/ada-is-programm/ (2026-10-12). It
# renders server-side and keeps the id on the server; each event links to
# Google with eid=base64("<event id> <calendar>"), the domain shortened to @m.
# The calendar id is replaced by a made-up one: this repository is public.
SIMCAL_EID = ("XzYwcjMyZTlrODkyMzZiOWo4ZDJqYWI5azZwMTM2YjlwNzUyamFiYTY3NHJqMmdobDY0czM2aGk1"
              "OGsgZXhhbXBsZWFydHNhc3NvY2lhdGlvbkBt")
SIMCAL_PAGE = """<html><head>
<link rel='stylesheet' href='https://example.eu/wp-content/plugins/google-calendar-events/assets/generated/default-calendar-grid.min.css?ver=4.3.1'>
</head><body><div class="simcal-calendar simcal-default-calendar simcal-default-calendar-grid" data-calendar-id="1723" data-timezone="Europe/Vienna">
<td class="simcal-day-2 simcal-weekday-5 simcal-day simcal-day-has-events" data-events-count="1"><div>
<ul class="simcal-events"><li class="simcal-event simcal-events-calendar-1723 simcal-tooltip">
<span class="simcal-event-title">Open studio evening</span>
<div class="simcal-event-details simcal-tooltip-content" style="display: none;">
<p><span class="simcal-event-start simcal-event-start-date" itemprop="startDate" content="2026-10-02T18:00:00+02:00">02/10/2026</span></p>
<p> <a href="https://www.google.com/calendar/event?eid=%s&#038;ctz=Europe/Vienna" target="_blank" >Weitere Details anzeigen</a></p></div>
</li></ul></div></td></div>
<script src='https://example.eu/wp-content/plugins/google-calendar-events/assets/generated/default-calendar.min.js?ver=4.3.1'></script>
</body></html>""" % SIMCAL_EID
FC_PAGE = ("<script>new FullCalendar.Calendar(el, {plugins: [googleCalendarPlugin], "
           "googleCalendarApiKey: '" + FAKE_SITE_KEY + "', "
           "events: {googleCalendarId: 'a1b2c3@group.calendar.google.com'}});</script>")


def _eid(text):
    import base64
    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii").rstrip("=")


check("Pretty Google Calendar's \"gcal\" is read (Polish Home)", G.page_ids(POLISH_HOME_CAL) == [PH_ID],
      G.page_ids(POLISH_HOME_CAL))
check("...where the iframe reader finds nothing", G.embed_ids(POLISH_HOME_CAL) == [])
check("...and \"cal_ids\" too, a comma list, after \"gcal\"",
      G.page_ids('pgcal_inlineScript({"gcal":"g1@group.calendar.google.com","cal_ids":'
                 '"g2@group.calendar.google.com, g3@group.calendar.google.com","google_api":"%s"})' % FAKE_SITE_KEY)
      == ["g1@group.calendar.google.com", "g2@group.calendar.google.com", "g3@group.calendar.google.com"])
check("a \"gcal\" that is not an id is not one", G.page_ids('{"gcal":"1","cal_ids":"","google_api":"x"}') == [])
check("Simple Calendar's event link names its calendar (@m is gmail.com)",
      G.page_ids(SIMCAL_PAGE) == ["exampleartsassociation@gmail.com"], G.page_ids(SIMCAL_PAGE))
check("...and that one is a personal address", G.personal_address(G.page_ids(SIMCAL_PAGE)[0]))
check("...a group calendar's is not", not G.personal_address(PH_ID))
check("...Google's other personal domain is one, in any case",
      G.personal_address("someone@googlemail.com") and G.personal_address("Someone@GMail.com"))
check("...a domain that only ends like one is not", not G.personal_address("someone@notgmail.com"))
check("@g is a group calendar",
      G.page_ids('<a href="https://calendar.google.com/calendar/event?eid=%s">x</a>' % _eid("ev1 5f2a9c41e0b7d3a8@g"))
      == ["5f2a9c41e0b7d3a8@group.calendar.google.com"])
check("a full domain in an eid is kept",
      G.page_ids('<a href="https://www.google.com/calendar/event?eid=%s">x</a>' % _eid("ev1 events@hall.example.org"))
      == ["events@hall.example.org"])
check("an abbreviation that is not @m or @g is not guessed at",
      G.page_ids('<a href="https://www.google.com/calendar/event?eid=%s">x</a>' % _eid("ev1 hall@x")) == [])
check("Google's own calendars stay out, as an eid (@v) and as \"gcal\"",
      G.page_ids('<a href="https://www.google.com/calendar/event?eid=%s">x</a>' % _eid("ev1 en.usa#holiday@v")
                 + '{"gcal":"en.usa#holiday@group.v.calendar.google.com"}') == [])
check("FullCalendar's googleCalendarId is read, its googleCalendarApiKey is not",
      G.page_ids(FC_PAGE) == ["a1b2c3@group.calendar.google.com"], G.page_ids(FC_PAGE))
CID_FORMS = {
    "a subscribe link, plain": '<a href="https://calendar.google.com/calendar/r?cid=s1@group.calendar.google.com">+</a>',
    "...under /u/0/": '<a href="https://calendar.google.com/calendar/u/0/r?cid=s1%40group.calendar.google.com">+</a>',
    "...as render?cid=, base64": '<a href="https://calendar.google.com/calendar/render?cid=' + base64.b64encode(
        b"s1@group.calendar.google.com").decode() + '">+</a>',
    "...as ?cid= the export URL itself": '<a href="https://calendar.google.com/calendar/?cid=webcal://calendar.google.com'
                                         '/calendar/ical/s1%40group.calendar.google.com/public/basic.ics">+</a>',
    "...with the cid after another parameter (&amp;)": '<a href="https://calendar.google.com/calendar/r?pli=1&amp;cid=s1@group.calendar.google.com">+</a>',
}
for label, page in CID_FORMS.items():
    check(label, G.page_ids(page) == ["s1@group.calendar.google.com"], G.page_ids(page))
check("a cid= that is somebody else's feed is not a Google calendar id",
      G.page_ids('<a href="https://calendar.google.com/calendar/r?cid=https://www.trumba.com/calendars/x.ics">+</a>') == [])
check("the iframe forms are still read, through page_ids", G.page_ids(MULTI) == G.embed_ids(MULTI)
      and G.page_ids(B64) == ["ssbc@swordssailing.ie"])
check("page order across forms, once each",
      G.page_ids(LINK + FC_PAGE + LINK) == ["0j1667ft5gdld6esbsctjt6eg0@group.calendar.google.com",
                                            "a1b2c3@group.calendar.google.com"])
check("no id on a page with none", G.page_ids("<html><body><a href='/events'>Events</a></body></html>") == [])
check("the quick test in front of the patterns hides no form: an id in an HTML attribute",
      G.page_ids("<div data-settings='{&quot;gcal&quot;:&quot;h1@group.calendar.google.com&quot;}'></div>")
      == ["h1@group.calendar.google.com"])
check("...nor an embed written in capitals",
      G.page_ids('<iframe src="HTTPS://CALENDAR.GOOGLE.COM/calendar/embed?src=h2%40group.calendar.google.com">')
      == ["h2@group.calendar.google.com"])

labels, ics = OSM.fingerprint(POLISH_HOME_CAL)
check("discovery reads Polish Home as a Google calendar, under its export URL",
      labels == ["gcal-embed"] and ics == G.ical_url(PH_ID), (labels, ics))
labels, ics = OSM.fingerprint(SIMCAL_PAGE)
check("...and a Simple Calendar page the same way", labels == ["gcal-embed"]
      and G.calendar_id(ics) == "exampleartsassociation@gmail.com", (labels, ics))
# THE SITE'S KEY NEVER COMES BACK, from any reader, in any output.
outs = [G.page_ids(POLISH_HOME_CAL), G.page_ids(FC_PAGE), OSM.fingerprint(POLISH_HOME_CAL), OSM.fingerprint(FC_PAGE),
        G.page_ids(POLISH_HOME_CAL.replace('"gcal":"' + PH_ID + '"', '"gcal":""'))]
check("the site owner's key is in none of what the readers return",
      FAKE_SITE_KEY not in json.dumps(outs) and "AIza" not in json.dumps(outs), json.dumps(outs)[:200])
check("...and a page whose only id field is empty returns nothing at all, not the key",
      G.page_ids(POLISH_HOME_CAL.replace('"gcal":"' + PH_ID + '"', '"gcal":""')) == [])


# --- 6. verify: skipped without the key, read through the API with it ----------
check("without the key the robots gate would be asked about the export URL",
      cc._ingest_requests("ics", {"url": SRC["url"]}) == [("GET", SRC["url"])])
with patch.dict(os.environ, {"GOOGLE_CALENDAR_API_KEY": KEY}):
    check("with it, about the API request", cc._ingest_requests("ics", {"url": SRC["url"]})
          == [("GET", "https://www.googleapis.com/calendar/v3/calendars/jpn%40gmail.com/events")])


def run_verify(env):
    tmp = tempfile.mkdtemp(prefix="gcal-verify-")
    keep = (cc.HERE, cc.LEDGER_FILE, cc._session)
    sess = Session([("https://www.googleapis.com/robots.txt", Resp(404, None, text="Not Found")),
                    ("https://www.googleapis.com/calendar/v3/", Resp(200, dict(META, items=ITEMS)))])
    cc.HERE, cc.LEDGER_FILE = tmp, os.path.join(tmp, "curation_ledger.json")
    cc._session = lambda: sess
    path = os.path.join(tmp, "cand.json")
    json.dump([dict(SRC, type="ics")], open(path, "w", encoding="utf-8"))
    try:
        with patch.dict(os.environ, env):
            if "GOOGLE_CALENDAR_API_KEY" not in env:
                os.environ.pop("GOOGLE_CALENDAR_API_KEY", None)
            cc.cmd_verify(path)
        verified = json.load(open(os.path.join(tmp, "cand.verified.json"), encoding="utf-8"))
        led = json.load(open(cc.LEDGER_FILE, encoding="utf-8")) if os.path.exists(cc.LEDGER_FILE) else {}
    finally:
        cc.HERE, cc.LEDGER_FILE, cc._session = keep
    return verified, led, sess


verified, led, sess = run_verify({})
check("without the key a Google candidate is skipped", verified == [])
check("...without a ledger row, so the key is not a 90-day verdict", led == {}, led)
check("...and without a single request", sess.asked == [], sess.asked)
verified, led, sess = run_verify({"GOOGLE_CALENDAR_API_KEY": KEY})
check("with the key it verifies through the API", [v["name"] for v in verified] == [SRC["name"]], verified)
check("...having asked googleapis.com's robots.txt, not calendar.google.com's",
      [u for u, _p, _h in sess.asked][:1] == ["https://www.googleapis.com/robots.txt"], [u for u, _p, _h in sess.asked])
row = led.get(cc._canon(SRC["url"])) or {}
check("...and the ledger keeps the calendar under its export URL", row.get("status") == "ok", row)

if fails:
    print(f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
