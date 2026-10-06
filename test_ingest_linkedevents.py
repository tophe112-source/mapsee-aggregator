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
        "drop_locations": {"tprek:76754": "its own Tribe calendar is ingested"},
        "city_level_places": {"matko:732": "the city centre"}}


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
# The city itself, at its centre: by configured id, and by name alone.
CITY = {"id": "matko:732", "name": {"fi": "Helsinki"},
        "position": {"type": "Point", "coordinates": [24.9375, 60.170833]}}
ESPOO_TOWN = {"id": "espooevents:espoo", "name": {"fi": "Espoo"}, "street_address": {"fi": "Espoon kaupunki"},
              "position": {"type": "Point", "coordinates": [24.6559, 60.20549]}}
IDA = {"id": "tprek:8460", "name": {"fi": "Leikkipuisto Ida", "sv": "Lekparken Ida"},
       "position": {"type": "Point", "coordinates": [24.95, 60.20]},
       "street_address": {"fi": "Idankatu 1"}, "address_locality": {"fi": "Helsinki"}}
NOPOINT = {"id": "tprek:9999", "name": {"fi": "Paikka ilman pistettä"}, "position": None}
PLACES = {p["id"]: p for p in (LIBRARY, MYRBACKA, KAMPPI, OLOHUONE, CITY, ESPOO_TOWN, IDA, NOPOINT)}


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
ev, _ = conv(row(name={"fi": "Tanssix"},
                 description={"fi": "Maksullinen, 7 €/ kerta, tutustumiskerta ilmainen."}))
check("is_free, but the text says 'Maksullinen, 7 €/ kerta': not free",
      ev.source_details == {"free": False} and not tagged_free(ev.description)
      and ev.description.startswith("Some admission options are not free."), ev.description[:80])

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
INST_E = dict(INST, key="espoo", event_page={"fi": "https://www.espoo.fi/fi/tapahtumat/{id}",
                                              "sv": "https://www.espoo.fi/sv/evenemang/{id}",
                                              "en": "https://www.espoo.fi/en/events/{id}"})
ev, _ = conv(row(id="espooevents:agqikxguri", name={"en": "Repair workshop"}), INST_E)
check("Espoo: an English row links to the English page",
      ev.ticket_url == "https://www.espoo.fi/en/events/espooevents:agqikxguri", ev.ticket_url)
ev, _ = conv(row(id="espooevents:x", name={"fi": "Kirjastokino"}), INST_E)
check("Espoo: a Finnish row to the Finnish one", "/fi/tapahtumat/" in ev.ticket_url, ev.ticket_url)
ev, _ = conv(row(location_extra_info={"fi": "Pihlajamäen yhteisötalo on kohtaamis- ja tapahtumapaikka kaikenikäisille."}))
check("a sentence about the house is not called a Room",
      "Room:" not in ev.description and "Pihlajamäen yhteisötalo on" in ev.description, ev.description[:120])

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
ev, _ = conv(row(name={"fi": "Musiikkia perheille leikkipuistossa"}, location=ref("place", "tprek:8460"),
                 keywords=[ref("keyword", "yso:p1808")], audience=[]))
check("anything at a staffed playground is kids, the topic an extra",
      ev.category == "kids" and ev.categories == ["music"], (ev.category, ev.categories))
ev, _ = conv(row(name={"fi": "Sirkus Finlandian juhlavuoden näytös"},
                 keywords=[ref("keyword", "yso:p2625"), ref("keyword", "yso:p4354")], audience=[]))
check("'children' filed among the KEYWORDS is still the audience",
      ev.category == "kids" and "theater" in ev.categories, (ev.category, ev.categories))
ev, _ = conv(row(name={"fi": "Sunnuntaikirpputori"}, keywords=[], audience=[]))
check("no keyword, a flea market by its title", ev.category == "market", ev.category)
ev, _ = conv(row(name={"fi": "Tuolijumppa"}, keywords=[], audience=[]))
check("no keyword, chair exercise by its title", ev.category == "fitness", ev.category)
ev, _ = conv(row(name={"fi": "Tuolijumppa"}, keywords=[ref("keyword", "yso:p6062")], audience=[]))
check("...but a keyword, when there is one, still decides", ev.category == "community", ev.category)

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
    ("a place the walk could not fetch", row(location=ref("place", "tprek:8426")), "location not fetched"),
    ("a place that has no point", row(location=ref("place", "tprek:9999")), "location without coordinates"),
    ("a Zoom call by its own words", row(name={"en": "Coffee chat"},
                                         description={"en": "This is an online event. Join on Zoom."}),
     "online (text)"),
    # --- the review of 2026-10-04: each of these was KEPT before it
    ("the card in Swedish (servicecentralSkort)",
     row(name={"sv": "Svenska klubben"}, audience=[], location=ref("place", "tprek:1923"),
         description={"sv": "För att delta behöver du ett servicecentralskort."}),
     "card holders only (service-centre card)"),
    ("by appointment in the title", row(name={"fi": "Digiopastusta ajanvarauksella"}), "by appointment (title)"),
    ("a bookable sewing machine", row(name={"fi": "Ompelukone varattavissa Kontulan yhteisötalolla"}),
     "by appointment (title)"),
    ("book your own slot, in the text",
     row(name={"fi": "Lukukoira Tuisku"},
         description={"fi": "Yksi lukuhetki kestää 15 minuuttia. Varaa oma aikasi kirjaston asiakaspalvelusta."}),
     "by appointment (text)"),
    ("kulke's stock 'requires registration'",
     row(name={"fi": "Luovan kirjoittamisen työpaja"}, description={"fi": "Maksuton, vaatii ilmoittautumisen."}),
     "registration required (text)"),
    ("'registration is mandatory'",
     row(name={"fi": "Naapuriäidit kokkikerho"}, description={"fi": "HUOM! Ilmoittautuminen on pakollinen."}),
     "registration required (text)"),
    ("a course, by its title", row(name={"fi": "Sirkuskurssi senioreille, Ryhmä 1"}), "course (title)"),
    ("cancelled at the END of the title", row(name={"fi": "Killer (PERUTTU)"}), "cancelled (title)"),
    ("cancelled at the end, no bracket", row(name={"fi": "Mosaiikin kielikahvila PERUTTU"}), "cancelled (title)"),
    ("online in Finnish, at a real library", row(name={"fi": "Puhutaan suomea -kielikahvila Zoomissa"}),
     "online (title)"),
    ("a council's sitting", row(name={"fi": "Espoon kaupunginvaltuuston kokous"}), "governance meeting (title)"),
    ("the city itself, by configured id", row(location=ref("place", "matko:732")), "location is a whole town"),
    ("the city itself, by name", row(location=ref("place", "espooevents:espoo")), "location is a whole town"),
    ("a weekly baby morning written as ONE row, 12 weeks long",
     row(name={"fi": "Vauva-aamu"}, start_time="2026-09-21T07:00:00Z", end_time="2026-12-14T09:00:00Z"),
     "series written as one row (weeks long, short daily hours)"),
    ("a theatre run that ends on a matinee's clock",
     row(name={"fi": "Mirdja"}, start_time="2026-11-23T16:30:00Z", end_time="2026-12-19T14:00:00Z"),
     "series written as one row (weeks long, short daily hours)"),
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
check("a bare date stays a day",
      ev is not None and (ev.start_local, ev.end_local, ev.start_utc) == ("2026-10-12", "2026-10-14", None), why)
ev, why = conv(row(start_time="2026-10-01", end_time="2026-10-03", name={"fi": "Syysleiri"}))
check("...and a bare END date runs to its end: a camp ending today is not past", ev is not None, why)
ev, why = conv(row(start_time="2026-09-20T09:00:00Z", end_time="2026-10-30T15:00:00Z",
                   name={"fi": "Me ollaan Sankareita!"}, keywords=[ref("keyword", "yso:p5121")]))
check("an exhibition already open and running into the window is kept", ev is not None and ev.category == "arts", why)
ev, why = conv(row(start_time="2027-02-01T10:00:00Z", end_time="2027-02-01T12:00:00Z"))
check("beyond the 90-day horizon", ev is None and why == "beyond horizon", why)

print()
print("...and what the new rules must NOT take")
KEEP = [
    ("'Ei ajanvarausta' (NO appointment) is a drop-in",
     row(name={"fi": "Digiopastusta seniorilta seniorille - Enter ry"},
         description={"fi": "Ei ajanvarausta, kävijöitä opastetaan saapumisjärjestyksessä."})),
    ("an /ajanvaraus/ inside a link beside 'tule käymään'",
     row(name={"fi": "Digitukea Oulunkylän Seurahuoneella"},
         description={"fi": "Voit myös halutessasi varata ajan digitukeen osoitteessa: "
                            "digituki.hel.fi/ajanvaraus/. Tule käymään!"})),
    ("a gallery's hours 'ma suljettu / ajanvarauksella' in the text",
     row(name={"fi": "Hannu Väisänen: Derrière les portes"},
         description={"fi": "Ti–pe 11.00–18.00, ma suljettu / ajanvarauksella. Vapaa pääsy."})),
    ("registration negated in three languages",
     row(name={"fi": "Språkkafé på svenska"},
         description={"fi": "Vapaa pääsy, ei ennakkoilmoittautumista.",
                      "sv": "Fritt inträde, ingen förhandsanmälan krävs.",
                      "en": "Free entry, no prior registration required."})),
    ("'SOME sessions need registration' is not this one",
     row(name={"fi": "Vauvatreffit"},
         description={"fi": "Osa tuokioista (esimerkiksi vauvasirkus) vaatii ennakkoilmoittautumisen."})),
    ("a course's PERFORMANCE is a show", row(name={"fi": "Improvisoitu näytelmä kurssi esittää: Elämän makuista"})),
    ("an exhibition open for weeks on short hours",
     row(name={"fi": "Taidenäyttely - Masa"}, keywords=[ref("keyword", "yso:p5121")],
         start_time="2026-09-14T13:00:00Z", end_time="2026-10-30T15:00:00Z")),
    ("a two-day event on a short window is not a series",
     row(name={"fi": "Halliween 2026"}, start_time="2026-10-23T13:00:00Z", end_time="2026-10-24T15:00:00Z")),
    ("Espoo's hobby catalogue, but 'ei tarvitse ilmoittautua'",
     row(name={"fi": "Töpinät"}, hobby_categories=[{"@id": "https://espooevents.example/hobbycategory/1/"}],
         description={"fi": "Töpinöihin ei tarvitse ilmoittautua, osallistuminen on maksutonta."})),
]
for label, r in KEEP:
    ev, why = conv(r)
    check(f"kept: {label}", ev is not None, why)

ev, why = conv(row(name={"fi": "Luke Jerram: Helios"}, keywords=[ref("keyword", "yso:p5121")],
                   start_time="2026-09-30T21:00:00Z", end_time="2026-10-31T22:00:00Z"))
check("00:00 to 00:00 is a DATE range: all-day, ending the day before",
      ev is not None and (ev.start_local, ev.end_local, ev.start_utc, ev.end_utc)
      == ("2026-10-01", "2026-10-31", None, None), (why, ev and (ev.start_local, ev.end_local)))
ev, why = conv(row(name={"fi": "Koiramessut"}, start_time="2026-12-05T22:01:00Z", end_time="2026-12-06T21:59:00Z"))
check("00:01 to 23:59 on one day is that day, all-day",
      ev is not None and (ev.start_local, ev.end_local, ev.start_utc) == ("2026-12-06", None, None),
      (why, ev and (ev.start_local, ev.end_local)))
ev, why = conv(row(name={"fi": "Iltakonsertti"}, start_time="2026-10-15T21:00:00Z", end_time="2026-10-15T22:30:00Z"))
check("a real midnight start with a real end keeps its clock",
      ev is not None and ev.start_local == "2026-10-16T00:00:00" and ev.start_utc, (why, ev and ev.start_local))

# ------------------------------------------------- 6. the licence line
print()
print("CC BY 4.0 is a condition, and the condition is the attribution")
ev, _ = conv(row(description={"fi": "Pitkä kuvaus. " * 200}))
last = ev.description.rsplit("\n\n", 1)[-1]
check("event_only is OFF unless the config allows it: no picture, no image credit",
      ev.poster_image_url is None and last == "Event data: City of Helsinki Linked Events, CC BY 4.0.", last)
ON = dict(INST, event_only_images=True)
ev, _ = conv(row(description={"fi": "Pitkä kuvaus. " * 200}), ON)
last = ev.description.rsplit("\n\n", 1)[-1]
check("allowed, it is the final paragraph and names the photographer",
      ev.poster_image_url and last == "Event data: City of Helsinki Linked Events, CC BY 4.0. Image: Jussi Hellsten.",
      last)
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
for who in ("", None, ".", "-", "m", "Tuntematon", "N/A", "Linked events"):
    ev, _ = conv(row(images=[{"url": "https://x.example/e.jpg", "license": "event_only",
                              "photographer_name": who}]), ON)
    check(f"event_only with photographer {who!r}: names nobody, so no image",
          ev.poster_image_url is None and "Image:" not in ev.description, ev.poster_image_url)
ev, _ = conv(row(images=[{"url": "https://x.example/e.jpg", "license": "event_only", "photographer_name": "-"},
                         {"url": "https://x.example/c.jpg", "license": "cc_by", "photographer_name": ""}]), ON)
check("...and the next image is tried: cc_by with no creator credits the licence",
      ev.poster_image_url == "https://x.example/c.jpg"
      and ev.description.endswith("CC BY 4.0. Image: CC BY 4.0, from the same source."), ev.description[-90:])
ev, _ = conv(row(images=[{"url": "https://x.example/c.jpg", "license": "cc_by", "photographer_name": "Lille Santanen"}]))
check("cc_by is taken with the switch off, its photographer named",
      ev.poster_image_url == "https://x.example/c.jpg" and ev.description.endswith("Image: Lille Santanen, CC BY 4.0."),
      ev.description[-60:])


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

class SeriesCopy(Fake):
    """One session, and a copy of it with a SMALLER id that runs five days to
    the series' last date (the Äänimaljarentoutus pair of 2026-10-04)."""
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if url.endswith("/event/"):
            r._body["data"].append(row(id="helsinki:aaa-long", end_time="2026-10-20T16:00:00Z"))
        return r


fake = SeriesCopy()
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, fake, dict(INST))
ids = sorted(s["source_id"] for rec in st.records.values() for s in rec["sources"])
check("the same session twice: the SHORTER row is kept, whatever its id",
      "helsinki:agp2ol3gfi" in ids and "helsinki:aaa-long" not in ids, ids)

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

# --------------------------------------------- 7b. --max-minutes
print()
print("a deadline no request starts after, and the save inside it")


class Clocked(Fake):
    """Every request takes a minute of a fake clock."""
    def get(self, url, params=None, timeout=None):
        self.now = getattr(self, "now", 0.0) + 60.0
        return super().get(url, params, timeout)


with tempfile.TemporaryDirectory() as d:
    cfg_path, store_path = os.path.join(d, "cfg.json"), os.path.join(d, "store.json")
    second = dict(INST, name="Espoo", key="espoo", api="https://espoo.example/v1")
    json.dump({"instances": [dict(INST), second]}, open(cfg_path, "w", encoding="utf-8"))
    fake = Clocked()
    rc = LE.main(["--config", cfg_path, "--store", store_path, "--max-minutes", "2.5"],
                 session=fake, clock=lambda: getattr(fake, "now", 0.0))
    saved = json.load(open(store_path, encoding="utf-8"))["events"] if os.path.exists(store_path) else None
    check("cut after the third request: the two placed rows read are saved, exit 0",
          rc == 0 and saved is not None and len(saved) == 2, (rc, saved and len(saved)))
    check("no request started after the deadline (the place lookup was not asked)",
          not any("/place/tprek:15321/" in u for u, _ in fake.calls) and len(fake.calls) == 3,
          [u for u, _ in fake.calls])
    check("the second instance is not read at all", not any("espoo.example" in u for u, _ in fake.calls))

# ------------------------------------- 7c. called off: a tombstone, not a gap
print()
print("a cancelled row is a TOMBSTONE keyed exactly as its live row was")


def conv_cancel(r, inst=INST):
    pid = LE._ref_id(r.get("location"))
    return LE.cancellation(r, PLACES.get(pid), inst, NOW, HEND)


live, _ = conv(row())
for label, r, why in [
    ("EventCancelled", row(event_status="EventCancelled"), "event_status EventCancelled"),
    ("EventPostponed (no new date)", row(event_status="EventPostponed"), "event_status EventPostponed"),
    ("PERUTTU: at the start of the title", row(name={"fi": "PERUTTU: Lautapeli-illat aikuisille"}),
     "cancelled in the title"),
    ("(PERUTTU) at the end", row(name={"fi": "Lautapeli-illat aikuisille (PERUTTU)"}), "cancelled in the title"),
]:
    tomb, cwhy = conv_cancel(r)
    check(f"{label}: tombstone with the live row's fingerprint, source and id",
          tomb is not None and cwhy == why and tomb.fingerprint == live.fingerprint
          and (tomb.source, tomb.source_id) == (live.source, live.source_id), (cwhy, tomb and tomb.fingerprint))
check("a scheduled row is not called off", conv_cancel(row()) == (None, None))
tomb, cwhy = conv_cancel(row(event_status="EventCancelled", location=ref("place", "helsinki:internet")))
check("called off, but no rule would ever list it (online): no tombstone, the reason kept",
      tomb is None and cwhy == "event_status EventCancelled", (tomb, cwhy))
check("to_event still refuses it (its contract is unchanged)",
      conv(row(event_status="EventCancelled")) == (None, "cancelled (event_status)"))

# Through the store, the way two runs see it: yesterday's live write, today's
# cancel. The tombstone's key IS the stored row's fingerprint.
with tempfile.TemporaryDirectory() as d:
    yday, today_st = LE.EventStore(os.path.join(d, "a.json")), LE.EventStore(os.path.join(d, "b.json"))
    yday.upsert(conv(row())[0])
    res = today_st.cancel(conv_cancel(row(event_status="EventCancelled",
                                          name={"fi": "PERUTTU: Lautapeli-illat aikuisille"}))[0], "x")
    check("store.cancel's key == store.upsert's key, across a retitled cancellation",
          res == "cancelled" and list(today_st.tombstones) == list(yday.records), (res, list(today_st.tombstones)))


class Cancels(Fake):
    """The walk with one of its rows called off and one sold as the city's own
    duplicate (both are real shapes of 2026-10-05)."""
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if url.endswith("/event/"):
            r._body["data"].append(row(id="helsinki:agp77uujbu", event_status="EventCancelled",
                                       name={"fi": "Sävelten siivin - toivemusiikkituokio"}))
            r._body["meta"]["count"] = 4
        if "page=2" in url:
            r._body["meta"]["count"] = 4
        return r


fake = Cancels()
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, fake, dict(INST))
check("the called-off row is a tombstone, the three live rows are kept, and the log counts it",
      out["kept"] == 3 and out["cancelled"] == 1 and len(st.tombstones) == 1 and len(st.records) == 3
      and st.cancelled_by_source.get("linkedevents:helsinki") == 1, (out, st.stats))
check("a whole read is marked complete, for the instance's own source name",
      out["complete"] and list(st.complete_reads) == ["linkedevents:helsinki"], (out.get("incomplete"), st.complete_reads))
win = st.complete_reads.get("linkedevents:helsinki") or {}
check("...over the window asked for: Helsinki midnight today to the end of the horizon's last day",
      (win.get("from"), win.get("to")) == ("2026-10-02T21:00:00Z", "2027-01-01T22:00:00Z"), win)

st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, OtherHost(), dict(INST))
check("a next page on another host is NOT a complete read", not out["complete"] and not st.complete_reads, out)


class OtherHostUndercounted(OtherHost):
    """...even when the first page's count says the rows read are all of them."""
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if url.endswith("/event/"):
            r._body["meta"]["count"] = 2
        return r


st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, OtherHostUndercounted(), dict(INST))
check("...even when the count agrees with what was read: an unfollowed page is never a whole read",
      not out["complete"] and not st.complete_reads and "another host" in (out["incomplete"] or ""), out)
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, Fake(status={"/place/tprek:15321/": 404}), dict(INST))
check("a place that could not be fetched is NOT a complete read (its rows would look gone)",
      not out["complete"] and not st.complete_reads and "place" in (out["incomplete"] or ""), out)


class Undercount(Fake):
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if url.endswith("/event/") or "page=2" in url:
            r._body["meta"]["count"] = 5
        return r


st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, Undercount(), dict(INST))
check("fewer distinct rows than the API counted is NOT a complete read",
      not out["complete"] and not st.complete_reads, out)
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, Fake(), dict(INST, max_pages=1))
check("the page cap is NOT a complete read", not out["complete"] and not st.complete_reads, out)
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, Clocked(), dict(INST), deadline=150.0, clock=lambda: 0.0)
check("a read inside the deadline is complete", out["complete"], out)
fake = Clocked()
st = LE.EventStore(os.devnull + ".none")
out = LE.ingest_instance(st, fake, dict(INST), deadline=150.0, clock=lambda: getattr(fake, "now", 0.0))
check("a read cut by the deadline is NOT", not out["complete"] and not st.complete_reads, out)

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
check("no instance takes event_only images while ../mapsee reuses posters off the event's page",
      all(i.get("event_only_images") is False for i in insts))
check("every instance names its whole-town place",
      all(isinstance(i.get("city_level_places"), dict) and i["city_level_places"] for i in insts))
check("the unreachable instances are recorded, not configured",
      all(k.startswith("http") and len(v) > 20 for k, v in cfg.get("_unverified", {}).items())
      and not any(i["api"].rstrip("/") in cfg.get("_unverified", {}) for i in insts))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
