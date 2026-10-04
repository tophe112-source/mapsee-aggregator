#!/usr/bin/env python3
"""
test_ingest_linkedevents.py - what a Finnish city's event API hands over, and
which of it is something a person can just turn up to.

Every fixture is the SHAPE of a row read live off api.hel.fi/linkedevents/v1
on 2026-10-03 (titles, ids and keyword ids are real; descriptions are cut
down). The expensive cases are the ones that look like ordinary events: a
weekly karaoke that only service-centre card holders may attend, a gym circuit
whose enrolment window has closed, a "Lasten kaupunki" exhibition that runs
from 2001 to 2050, and a row marked free whose wording the offer tagger in
../mapsee must read as free - and a paid one it must not.

    python test_ingest_linkedevents.py
"""
import copy
import json
import os
import re
import sys
import tempfile
from datetime import timedelta

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261003"

import mapsee_ingest_linkedevents as LE  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


API = "https://api.hel.fi/linkedevents/v1"
INST = {"name": "Helsinki", "key": "helsinki", "api": API,
        "event_page": "https://tapahtumat.hel.fi/{lang}/events/{id}",
        "attribution": "Event data: City of Helsinki Linked Events, CC BY 4.0.",
        "timezone": "Europe/Helsinki", "country": "FI", "city": "Helsinki",
        "category": "community", "horizon_days": 90, "crawl_delay": 0, "retry_backoff": 0,
        "drop_data_sources": ["lippupiste"],
        "drop_locations": {"tprek:76754": "its own Tribe calendar is ingested"}}


def ref(kind, i):
    return {"@id": f"{API}/{kind}/{i}/"}


LIBRARY = {"id": "tprek:8184", "name": {"fi": "Etelä-Haagan kirjasto", "sv": "Södra Haga bibliotek"},
           "position": {"type": "Point", "coordinates": [24.888, 60.2167]},
           "street_address": {"fi": "Isonnevantie 16 B", "sv": "Stormossevägen 16 B"},
           "address_locality": {"fi": "Helsinki", "sv": "Helsingfors"}, "postal_code": "00320"}
MYRBACKA = {"id": "tprek:15321", "name": {"fi": "Myyrmäen kirjasto", "sv": "Myrbacka bibliotek"},
            "position": {"type": "Point", "coordinates": [24.8545, 60.2617]},
            "street_address": {"fi": "Paalutori 3", "sv": "Påltorget 3"},
            "address_locality": {"fi": "Vantaa", "sv": "Vanda"}, "postal_code": "01600"}
KAMPPI = {"id": "tprek:1923", "name": {"fi": "Kampin palvelukeskus"},
          "position": {"type": "Point", "coordinates": [24.9325, 60.1675]},
          "street_address": {"fi": "Salomonkatu 21 B"}, "address_locality": {"fi": "Helsinki"},
          "postal_code": "00100"}
OLOHUONE = dict(LIBRARY, id="tprek:76754", name={"fi": "Kalasataman Vapaakaupungin Olohuone"})
PLACES = {p["id"]: p for p in (LIBRARY, MYRBACKA, KAMPPI, OLOHUONE)}


def row(**over):
    base = {
        "id": "helsinki:agp2ol3gfi", "type_id": "General", "data_source": "helsinki",
        "publisher": "ahjo:u4804001010", "super_event_type": None, "event_status": "EventScheduled",
        "deleted": False, "name": {"fi": "Lautapeli-illat aikuisille"},
        "description": {"fi": "<p>Rentoa pelailua hyvässä seurassa!&nbsp;Tervetuloa pelaamaan!</p>"
                              "<p>Peli-illat järjestetään kerran kuussa.</p>"},
        "start_time": "2026-10-15T14:00:00Z", "end_time": "2026-10-15T16:00:00Z",
        "location": ref("place", "tprek:8184"),
        "keywords": [ref("keyword", "yso:p6062"), ref("keyword", "yso:p10727")],
        "audience": [ref("keyword", "yso:p5590")], "in_language": [],
        "offers": [{"is_free": True, "price": None, "info_url": None, "description": None}],
        "enrolment_start_time": None, "enrolment_end_time": None, "registration": None,
        "images": [{"url": "https://api.hel.fi/linkedevents/media/images/etela-haaga.jpg",
                    "license": "event_only", "photographer_name": "Jussi Hellsten"}],
        "info_url": None, "provider": None, "location_extra_info": None,
    }
    base.update(over)
    return base


tz = LE._tz("Europe/Helsinki")
NOW = LE._now(tz)
HEND = LE._today() + timedelta(days=90)


def conv(r, inst=INST):
    pid = LE._ref_id(r.get("location"))
    return LE.to_event(r, PLACES.get(pid), inst, NOW, HEND)


# --------------------------------------------------------------- 1. a row
print("an ordinary free session")
ev, why = conv(row())
check("kept", ev is not None and why is None, why)
check("times: UTC instant kept, local clock derived (UTC+3 in October)",
      ev.start_utc == "2026-10-15T14:00:00Z" and ev.start_local == "2026-10-15T17:00:00", (ev.start_utc, ev.start_local))
check("the city's point is the pin, and is marked exact",
      (ev.latitude, ev.longitude, ev.coords_exact) == (60.2167, 24.888, True))
check("address, town and postcode come from the place",
      (ev.address, ev.city, ev.postal_code, ev.country) == ("Isonnevantie 16 B", "Helsinki", "00320", "FI"))
check("HTML becomes paragraphs, entities decoded",
      "Rentoa pelailua hyvässä seurassa! Tervetuloa pelaamaan!\n\nPeli-illat" in ev.description, ev.description)
check("the city's public page for the event is the link",
      ev.ticket_url == "https://tapahtumat.hel.fi/fi/events/helsinki:agp2ol3gfi", ev.ticket_url)
e2, _ = conv(row(info_url={"fi": "https://helmet.finna.fi/"}))
check("...even when the publisher typed a catalogue's front page as info_url",
      e2.ticket_url.startswith("https://tapahtumat.hel.fi/"), e2.ticket_url)
e2, _ = conv(row(info_url={"fi": "https://www.hel.fi/fi/saunabaari"}), dict(INST, event_page=None))
check("with no event page configured, the info_url", e2.ticket_url == "https://www.hel.fi/fi/saunabaari", e2.ticket_url)
check("games + participation, adults: community, not the nightlife door",
      ev.category == "community" and "party" not in ev.categories, (ev.category, ev.categories))
check("source and id namespace the instance", (ev.source, ev.source_id) == ("linkedevents:helsinki", "helsinki:agp2ol3gfi"))

# ------------------------------------------------- 2. the wording contract
print()
print("free means the publisher said free, in words ../mapsee's tagger reads")
# Mirrors the two alternatives of FREE in ../mapsee/tools/measure_deals.py
# (migration 0227's `free`) that these descriptions can reach, and FREE_NEG.
FREE = re.compile(r"(?<![-\w/])free\s+(?:admission|entry|to\s+attend)\b|"
                  r"\b(?:admission|entry|cost|price)\s*(?:is|:|-)?\s*free\b|\bis\s+free\b|"
                  r"\bat\s+no\s+cost\b|\bfree\s*!", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b", re.I)


def tagged_free(text):
    return bool(FREE.search(text)) and not FREE_NEG.search(text)


check("is_free: says 'Free to attend.' first, and the tagger reads it",
      ev.description.startswith("Free to attend.") and tagged_free(ev.description), ev.description[:60])
check("is_free: structured facts say free", ev.source_details.get("free") is True and ev.admission_checked)
ev, _ = conv(row(offers=[{"is_free": False, "price": {"fi": "5 €"}, "info_url": None}]))
check("one price: written as an amount, never free",
      "Admission: EUR 5." in ev.description and not tagged_free(ev.description)
      and (ev.source_details["offer"]["price"], ev.source_details["offer"]["currency"]) == ("5", "EUR"),
      ev.description[:90])
ev, _ = conv(row(offers=[{"is_free": False, "price": {"fi": "12€/20€"}, "info_url": None}]))
check("two prices: quoted as written, never free",
      "Price: 12€/20€." in ev.description and not tagged_free(ev.description), ev.description[:90])
ev, _ = conv(row(offers=[{"is_free": False, "price": {"fi": "0€"}, "info_url": None}]))
check("'0€' against is_free false is not a free claim",
      ev.source_details == {"free": False} and not tagged_free(ev.description), ev.source_details)
ev, _ = conv(row(offers=[{"is_free": False, "price": None, "info_url": None}]))
check("paid with no price text says only that it is not free",
      ev.description.startswith("Some admission options are not free.") and not tagged_free(ev.description))
ev, _ = conv(row(offers=[]))
check("no offers at all: no admission claim either way", ev.source_details is None
      and not tagged_free(ev.description))

# ------------------------------------------------- 3. the language
print()
print("the language a row is shown in")
sv = row(id="helsinki:agphexxazi", name={"fi": "Ruotsia kirjastossa", "sv": "Svenska på bibban"},
         description={"fi": "Keskustelua ruotsiksi.", "sv": "Välkommen till Svenska på bibban."},
         in_language=[ref("language", "sv")], location=ref("place", "tprek:15321"),
         keywords=[ref("keyword", "helsinki:agjffu7tgq")], location_extra_info={"sv": "2. våning"})
ev, _ = conv(sv)
check("held in Swedish: the Swedish title, text and venue",
      (ev.name, ev.venue_name) == ("Svenska på bibban", "Myrbacka bibliotek")
      and "Välkommen" in ev.description, (ev.name, ev.venue_name))
check("...but the town in Finnish, the name other sources join on", ev.city == "Vantaa", ev.city)
check("the room goes in the description", "Room: 2. våning" in ev.description)
check("the event page in the same language", "/sv/events/" in ev.ticket_url, ev.ticket_url)
check("a language café is a learning row", ev.category == "learning", ev.category)
ev, _ = conv(row(name={"fi": "Satutunti", "en": "Story hour"}, in_language=[ref("language", "fi"), ref("language", "en")]))
check("two languages held, Finnish first", ev.name == "Satutunti", ev.name)
ev, _ = conv(row(name={"en": "Open mic night"}))
check("only English: English", ev.name == "Open mic night")

# ------------------------------------------------- 4. categories
print()
print("categories from the row's own keywords and audiences")
kids = row(name={"fi": "Vauvojen värikylpy"}, keywords=[ref("keyword", "yso:p2739")],
           audience=[ref("keyword", "yso:p20513"), ref("keyword", "yso:p4354")])
ev, _ = conv(kids)
check("every audience young: kids first, the topic kept as an extra",
      ev.category == "kids" and ev.categories == ["arts"], (ev.category, ev.categories))
ev, _ = conv(dict(kids, audience=[ref("keyword", "yso:p4354"), ref("keyword", "yso:p5590")]))
check("children AND adults: the topic's door, kids as an extra",
      ev.category == "arts" and "kids" in ev.categories, (ev.category, ev.categories))
ev, _ = conv(row(keywords=[ref("keyword", "yso:p8105")], audience=[]))
check("a staffed playground (leikkipuistot) is kids", ev.category == "kids", ev.category)
ev, _ = conv(row(keywords=[ref("keyword", "yso:p1808"), ref("keyword", "yso:p10727")], audience=[]))
check("music outranks the participation tag on a third of all rows", ev.category == "music", ev.category)
ev, _ = conv(row(keywords=[ref("keyword", "yso:p999999")], audience=[]))
check("an unknown keyword falls back to the config's category", ev.category == "community")

# --------------------------------------------- 5. what is left out, and why
print()
print("what is not something you can turn up to")
CASES = [
    ("lippupiste (ticket resale)", row(data_source="lippupiste"), "data source lippupiste"),
    ("a recurring parent", row(super_event_type="recurring"), "super event (series or festival parent)"),
    ("EventCancelled", row(event_status="EventCancelled"), "cancelled (event_status)"),
    ("PERUUTETTU in the title", row(name={"fi": "PERUUTETTU: Kansalaisvaikuttamisen ja demokratian kurssi"}),
     "cancelled (title)"),
    ("INSTÄLLT in the title", row(name={"sv": "INSTÄLLT ! Le 11 novembre en France"}), "cancelled (title)"),
    ("Internet as the place", row(location=ref("place", "helsinki:internet")), "online (location Internet)"),
    ("an enrolment window", row(name={"fi": "Kuntopiiri"}, enrolment_start_time="2026-11-03T08:05:00+02:00",
                                enrolment_end_time="2026-11-13T12:00:00+02:00"), "registration required (enrolment)"),
    ("a Linked Registrations sign-up", row(registration=ref("registration", "2003")),
     "registration required (enrolment)"),
    ("Espoo: a sign-up form", row(event_registration_link="https://link.webropolsurveys.com/S/C86B6DDBB34D2CEC"),
     "registration required (sign-up link)"),
    ("Espoo: a hobby catalogue group",
     row(name={"fi": "JUMPPI-Voimistelu-7-9v"}, hobby_categories=[{"@id": "https://espooevents.example/hobbycategory/1/"}]),
     "hobby group (Espoo hobby catalogue)"),
    ("the service-centre card audience",
     row(name={"fi": "Karaoke"}, location=ref("place", "tprek:1923"),
         audience=[ref("keyword", "helsinki:aflfbat76e")]), "card holders only (service-centre card)"),
    ("the card in the text only",
     row(name={"fi": "Bingo"}, location=ref("place", "tprek:1923"), audience=[],
         description={"fi": "Toimintaan osallistuminen edellyttää maksutonta palvelukeskuskorttia."}),
     "card holders only (service-centre card)"),
    ("a closed memory group", row(name={"fi": "Suljettu muistiryhmä"}), "closed group (title)"),
    ("a venue whose own calendar is ingested", row(location=ref("place", "tprek:76754")),
     "venue's own calendar already ingested"),
    ("a permanent exhibition, 2001-2050",
     row(name={"fi": "Lasten kaupunki"}, start_time="2001-01-01T09:00:00Z", end_time="2050-12-31T16:00:00Z"),
     "long run (span over the limit)"),
    ("over before today", row(start_time="2026-10-01T14:00:00Z", end_time="2026-10-01T16:00:00Z"), "past"),
    ("no location at all", row(location=None), "no location"),
    ("a place that has no point", row(location=ref("place", "tprek:8426")), "location not fetched"),
    ("a Zoom call by its own words", row(name={"en": "Coffee chat on Zoom"}), "online (text)"),
]
for label, r, want in CASES:
    ev, why = conv(r)
    check(f"{label}: {want}", ev is None and why == want, why)

ev, why = conv(row(name={"fi": "Karaoke"}, location=ref("place", "tprek:1923"),
                   audience=[ref("keyword", "helsinki:aflfbat76e")]), dict(INST, keep_card_holder_rows=True))
check("card rows kept on request say who may come, and are never 'free' to the public",
      ev is not None and "service-centre card" in ev.description
      and ev.source_details == {"free": False, "restricted": True} and not tagged_free(ev.description),
      (why, ev and ev.description[:120]))
ev, why = conv(row(start_time="2026-10-12", end_time="2026-10-14", name={"fi": "Syysleiri"}))
check("a bare date stays a day, and the span is three days not two",
      ev is not None and (ev.start_local, ev.end_local, ev.start_utc) == ("2026-10-12", "2026-10-14", None), why)
ev, why = conv(row(start_time="2026-09-20T09:00:00Z", end_time="2026-10-30T15:00:00Z",
                   name={"fi": "Me ollaan Sankareita!"}, keywords=[ref("keyword", "yso:p5121")]))
check("an exhibition already open and running into the window is kept", ev is not None and ev.category == "arts", why)
ev, why = conv(row(start_time="2027-02-01T10:00:00Z", end_time="2027-02-01T12:00:00Z"))
check("beyond the 90-day horizon", ev is None and why == "beyond horizon", why)

# ------------------------------------------------- 6. the licence line
print()
print("CC BY 4.0 is a condition, and the condition is the attribution")
ev, _ = conv(row(description={"fi": "Pitkä kuvaus. " * 200}))
last = ev.description.rsplit("\n\n", 1)[-1]
check("the attribution is the final paragraph and names the image's photographer",
      last == "Event data: City of Helsinki Linked Events, CC BY 4.0. Image: Jussi Hellsten.", last)
try:
    import mapsee_supabase_sync as SYNC
    capped = SYNC._cap_prose(ev.description)
    check("short enough that the sync's 800-character cap keeps it",
          len(capped) <= SYNC.DESCRIPTION_MAX and capped.endswith(last), capped[-120:])
    rec = {"name": "Vauvojen värikylpy", "description": "Free to attend.", "category": "kids",
           "categories": ["arts"], "sources": [{"source": "linkedevents:helsinki"}]}
    check("the sync keeps a kids row on kids", SYNC.derive_categories(rec)[0] == "kids",
          SYNC.derive_categories(rec))
except ImportError as exc:                                         # pragma: no cover
    check("the sync imports offline", False, exc)
ev, _ = conv(row(images=[{"url": "https://x.example/a.jpg", "license": "all_rights_reserved"}]))
check("an image under any other licence is left out",
      ev.poster_image_url is None and "Image:" not in ev.description)


# ------------------------------------------------- 7. the walk
print()
print("paging, places and refusals, against a fake API")


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


class Fake:
    """Two event pages joined by meta.next, one place page, one place fetched
    by id. `status` overrides the answer for a URL substring."""

    def __init__(self, status=None, place_count=2):
        self.calls, self.status, self.place_count = [], status or {}, place_count
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for needle, code in self.status.items():
            if needle in url:
                code = (code.pop(0) if code else 200) if isinstance(code, list) else code
                if code != 200:
                    return Resp(code)
        if url.endswith("/place/"):
            return Resp(200, {"meta": {"count": self.place_count, "next": None},
                              "data": [LIBRARY, KAMPPI]})
        if "/place/tprek:15321/" in url:
            return Resp(200, MYRBACKA)
        if url.endswith("/event/"):
            return Resp(200, {"meta": {"count": 3, "next": f"{API}/event/?page=2&sort=start_time"},
                              "data": [row(), row(id="helsinki:b", name={"fi": "Kuvataidepaja"},
                                              keywords=[ref("keyword", "yso:p2739")])]})
        if "page=2" in url:
            return Resp(200, {"meta": {"count": 3, "next": None},
                              "data": [copy.deepcopy(sv)]})
        return Resp(404)


def run(fake, inst=INST):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        store = LE.EventStore(path)
        out = LE.ingest_instance(store, fake, dict(inst))
        store.save()
        again = LE.EventStore(path)
        out2 = LE.ingest_instance(again, Fake(), dict(inst))
        return out, store, again, out2


fake = Fake()
out, store, again, out2 = run(fake)
ev_params = next(p for u, p in fake.calls if u.endswith("/event/"))
check("the event list asks for leaves, in start order, inside the window",
      ev_params.get("super_event_type") == "none" and ev_params.get("sort") == "start_time"
      and ev_params.get("start") == "2026-10-03" and ev_params.get("end") == "2027-01-02"
      and ev_params.get("page_size") == 100 and "include" not in ev_params, ev_params)
check("meta.next is followed to the second page", any("page=2" in u for u, _ in fake.calls))
check("places come from the place list, and a place it missed is fetched by id",
      any(u.endswith("/place/") for u, _ in fake.calls) and any("/place/tprek:15321/" in u for u, _ in fake.calls))
check("all three rows kept", out["kept"] == 3 and len(store.records) == 3, out)
check("A RE-RUN WRITES NO NEW ROW: same fingerprints, nothing rekeyed",
      out2["kept"] == 3 and len(again.records) == 3 and again.stats["rekeyed"] == 0
      and again.stats["added"] == 0, (len(again.records), again.stats))



class Twice(Fake):
    """The same playground session entered twice, standalone and in a series."""
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if url.endswith("/event/"):
            r._body["data"].append(row(id="helsinki:aaa-copy"))
        return r


fake = Twice()
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, fake, dict(INST))
ids = sorted(s["source_id"] for rec in st.records.values() for s in rec["sources"])
check("the same session under two ids is one row, and the smaller id keeps it",
      out["kept"] == 3 and "helsinki:aaa-copy" in ids and "helsinki:agp2ol3gfi" not in ids
      and out["reasons"].get("same session published twice") == 1, (ids, out["reasons"]))

fake = Fake(place_count=99999)
out = LE.ingest_instance(LE.EventStore(os.devnull + ".none"), fake, dict(INST))
ev_params = next(p for u, p in fake.calls if u.endswith("/event/"))
check("a place list that is not filtered: places are asked for inline instead",
      ev_params.get("include") == "location", ev_params)

fake = Fake(status={"/event/": 403})
try:
    LE.ingest_instance(LE.EventStore(os.devnull + ".none"), fake, dict(INST))
    refused = False
except LE.Refused:
    refused = True
check("a 403 is a refusal: raised, and asked exactly once",
      refused and sum(1 for u, _ in fake.calls if u.endswith("/event/")) == 1, fake.calls)

fake = Fake(status={"/event/": [502, 200]})
out = LE.ingest_instance(LE.EventStore(os.devnull + ".none"), fake, dict(INST))
check("a 502 is a bad moment: asked again, and the walk goes on",
      out["kept"] == 3 and sum(1 for u, _ in fake.calls if u.endswith("/event/")) == 2, out)


class OtherHost(Fake):
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if url.endswith("/event/"):
            r._body["meta"]["next"] = "https://elsewhere.example/v1/event/?page=2"
        return r


fake = OtherHost()
out = LE.ingest_instance(LE.EventStore(os.devnull + ".none"), fake, dict(INST))
check("a next page on another host is not followed",
      not any("elsewhere.example" in u for u, _ in fake.calls) and out["kept"] == 2, out)

# ------------------------------------------------- 8. the config itself
print()
print("the shipped config")
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "linkedevents_sources.json"), encoding="utf-8"))
insts = cfg["instances"]
keys = [i["key"] for i in insts]
check("keys are unique - they namespace the fingerprint", len(keys) == len(set(keys)), keys)
check("every instance carries the CC BY 4.0 attribution",
      all("CC BY 4.0" in i.get("attribution", "") for i in insts))
check("every instance has a horizon and a timezone",
      all(i.get("horizon_days") and i.get("timezone") for i in insts))
check("Helsinki drops the ticket reseller", "lippupiste" in next(
    i for i in insts if i["key"] == "helsinki").get("drop_data_sources", []))
check("every dropped location says why",
      all(isinstance(v, str) and len(v) > 20 for i in insts for v in (i.get("drop_locations") or {}).values()))
check("the unreachable instances are recorded, not configured",
      all(k.startswith("http") and len(v) > 20 for k, v in cfg.get("_unverified", {}).items())
      and not any(i["api"].rstrip("/") in cfg.get("_unverified", {}) for i in insts))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
