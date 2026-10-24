"""repair-cafe.ch: the date is on the page, the place is checked against the
town, and a page is read once per lastmod.

WHY THIS EXISTS. Every case below is a way the 2026-10-24 build could come out
well-formed and wrong, or cost the publisher hundreds of requests a day:

  * THE PIN. The Tüftelwerk Luzern page carries (85.0511, -180), the map
    tiles' corner, 4,596 km from its café. A pin is believed only within
    max_km of the session's own postcode and town; so is the café's pin and so
    is a geocoded street ("Photon answers every question").
  * THE ADDRESS. "Bernstrasse 9  Nouvo" is the café's "Bernstrasse 9";
    "Therwilerstrasse 18" is NOT the café's "Therwilerstrasse 16"; an empty Ort
    is the café's own place, a street-less Ort in another town is nowhere.
  * THE NAME. Two cafés' bare "Repair Café" on one day in one town are two
    rows; "flicken" and "am Standort Badenerstrasse 658" become their café.
  * THE CURSOR. 1,866 sitemap URLs, mostly history: a page is fetched once per
    lastmod, never when its lastmod is older than the floor, newest first, and
    the walk resumes where a budget or a deadline stopped it.
  * THE CONTACT BOX. A volunteer's mobile number and e-mail are never copied.

No network: the pages are the real markup of repair-cafe.ch (2026-10-24),
trimmed to the event section and its map script, with the volunteers' names,
numbers and addresses replaced by placeholders. event_murten_abgesagt.html is
event_murten_voranmeldung.html with its title changed to "ABGESAGT: ...", and
the sitemap fixtures carry a few lastmods moved so that each side of the floor
is exercised.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="repaircafe_ch_test_")
# Before mapsee_ingest_ics is imported: it reads the cache path at import.
os.environ["GEOCODE_CACHE"] = os.path.join(TMP, "geocode_cache.json")
os.environ["GEOCODE_BUDGET_FILE"] = os.path.join(TMP, "geo_budget.json")
os.environ["MAPSEE_TODAY"] = "20261024"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from datetime import date

import mapsee_ingest_repaircafe_ch as R

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "repaircafe_ch")
CFG = json.load(open(os.path.join(HERE, "repaircafe_ch_sources.json"), encoding="utf-8"))
SITE = CFG["sites"][0]
TODAY = date(2026, 10, 24)
EV = "https://www.repair-cafe.ch/repairevent/"


def fx(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


BOOK = R.parse_cafe_book(fx("cafe_map.html"))
MURTEN = "https://www.repair-cafe.ch/repaircafe/repair-cafe-murten-morat/"
LUZERN = "https://www.repair-cafe.ch/repaircafe/tueftelwerk-luzern/"
LOOGARTEN = "https://www.repair-cafe.ch/repaircafe/repair-cafe-gz-loogarten/"

# Photon as measured: the postcode-and-town answers (each within a km of the
# town), nothing else unless a case adds it.
TOWNS = {"3280 Murten": (46.9283, 7.1167), "6005 Luzern": (47.0444, 8.3075),
         "8048 Zürich": (47.3870, 8.4900), "2540 Grenchen": (47.1920, 7.3960),
         "4103 Bottmingen": (47.5226, 7.5733), "6370 Stans": (46.9580, 8.3660),
         "2800 Delémont": (47.3650, 7.3440)}


def fake_geocode(extra=None, log=None):
    extra = extra or {}

    def g(query, suffix):
        if log is not None:
            log.append((query, suffix))
        if suffix in (", Schweiz", ", Liechtenstein"):
            return TOWNS.get(query)
        return extra.get(query)
    return g


# --------------------------------------------------------------------------- #
print("== parsing a session page")
p = R.parse_event_page(fx("event_murten_voranmeldung.html"))
check("Datum, Uhrzeit and Ort are read (Sa, 24.10.2026; 10:00 - 12:15 Uhr; Bernstrasse 9  Nouvo / 3280 Murten)",
      (p.get("date"), p.get("start"), p.get("end"), p.get("line1"), p.get("postcode"), p.get("town"))
      == ("2026-10-24", "10:00", "12:15", "Bernstrasse 9 Nouvo", "3280", "Murten"), p)
check("the café is the organiser link, without the page's ?date=",
      p.get("cafe_url") == MURTEN and p.get("cafe_name") == "Repair Café Murten-Morat", p.get("cafe_url"))
check("a page with no map carries no pin", p.get("pin") is None, p.get("pin"))
g = R.parse_event_page(fx("event_grenchen_pin.html"))
check("the page's own map pin is read (Parktheater Grenchen)",
      g.get("pin") and abs(g["pin"][0] - 47.1911) < 1e-3 and abs(g["pin"][1] - 7.3905) < 1e-3, g.get("pin"))
lz = R.parse_event_page(fx("event_luzern_corner_pin.html"))
check("the corner pin is read as written (and refused later, not here)", lz.get("pin") == [85.051128779807, -180.0],
      lz.get("pin"))
st = R.parse_event_page(fx("event_stans_no_street.html"))
check("an Ort with no street keeps its postcode and town", (st.get("line1"), st.get("postcode"), st.get("town"))
      == ("", "6370", "Stans"), st)
check("not a session page -> a reason, not an exception",
      R.parse_event_page("<html><body>Seite nicht gefunden</body></html>").get("why", "").startswith("no event section"))
check("a Datum naming two days is refused",
      R.parse_event_page(fx("event_murten_voranmeldung.html").replace("Sa, 24.10.2026", "Sa, 24.10.2026 - So, 25.10.2026"))
      .get("why") == "Datum spans several days")
check("parse_time: '10:00 - 12:15 Uhr', '14.30 Uhr', '' and an end before the start",
      [R.parse_time("10:00 - 12:15 Uhr"), R.parse_time("14.30 Uhr"), R.parse_time(""), R.parse_time("18:00 - 09:00")]
      == [((10, 0), (12, 15)), ((14, 30), None), (None, None), ((18, 0), None)])

print("== sitemaps and the café map")
idx = R.parse_sitemap_index(fx("sitemap_index.xml"))
check("the index yields only the repairevent sitemaps", idx == [
    "https://www.repair-cafe.ch/repairevent-sitemap.xml", "https://www.repair-cafe.ch/repairevent-sitemap2.xml"], idx)
sm2 = R.parse_sitemap(fx("repairevent-sitemap2.xml"))
check("a sitemap yields (url, lastmod); hreflang alternates are not URLs", sm2[0] == (
    EV + "am-standort-badenerstrasse-658-31/", "2025-10-24T12:32:44+00:00") and len(sm2) == 3, sm2)
check("the café map: 7 cafés, a /standorte/ site among them",
      len(BOOK) == 7 and "https://www.repair-cafe.ch/standorte/repair-cafe-in-der-stad-zug/" in BOOK, sorted(BOOK))
m = BOOK.get(MURTEN, {})
check("a café carries its pin, address, postcode and next dates",
      m.get("pin") == [46.930214, 7.1165429] and m.get("venue") == "Bernstrasse 9" and m.get("postcode") == "3280"
      and m.get("city") == "Murten" and m.get("next") and all(len(d) == 10 for d in m["next"]), m)
check("canon_url drops ?date= and adds the slash",
      R.canon_url(EV + "repair-cafe-murten-morat-4/?date=24.10.2026") == EV + "repair-cafe-murten-morat-4/"
      and R.canon_url("https://WWW.repair-cafe.ch/repairevent/x") == EV + "x/")

print("== addresses")
cases = {
    "Parktheater Grenchen, Lindenstrasse 41": ("Parktheater Grenchen", "Lindenstrasse 41"),
    "Bernstrasse 9 Nouvo": ("Nouvo", "Bernstrasse 9"),
    "Im Lokal von RusWärch / Grindel 8": ("Im Lokal von RusWärch", "Grindel 8"),
    "Therwilerstrasse 16/18": (None, "Therwilerstrasse 16/18"),
    "LARC, Passage des Ponts 2": ("LARC", "Passage des Ponts 2"),
    "Werkraum Primarschulhaus": ("Werkraum Primarschulhaus", None),
    "Salle communale, 12 rue du Lac": ("Salle communale", "12 rue du Lac"),
    "Ida-Sträuli-Strasse 71a": (None, "Ida-Sträuli-Strasse 71a"),
}
for line, want in cases.items():
    check(f"split_line({line!r})", R.split_line(line) == want, R.split_line(line))
check("same_place: 'Bernstrasse 9 Nouvo' is the café's 'Bernstrasse 9'", R.same_place("Bernstrasse 9 Nouvo", "Bernstrasse 9"))
check("same_place: 'Therwilerstrasse 18' is not the café's 'Therwilerstrasse 16'",
      not R.same_place("Therwilerstrasse 18", "Therwilerstrasse 16"))
check("same_place: 'Werkraum Primarschulhaus, Wolhuserstrasse 3a' is the café's 'Werkraum Primarschulhaus'",
      R.same_place("Werkraum Primarschulhaus, Wolhuserstrasse 3a", "Werkraum Primarschulhaus"))
check("same_place: 'Spritzenhaus' is not the café's 'Foyer Schulzentrum Turmatt'",
      not R.same_place("Spritzenhaus", "Foyer Schulzentrum Turmatt"))
check("same_place: 'Gäuggelistr. 10' is 'Gäuggelistrasse 10'", R.same_place("Gäuggelistr. 10", "Gäuggelistrasse 10"))
check("same_place: 'Gemeindestube, Therwilerstrasse 16/18' is the café's 'Therwilerstrasse 16'",
      R.same_place("Gemeindestube, Therwilerstrasse 16/18", "Therwilerstrasse 16"))
check("a Liechtenstein postcode is LI, a Swiss one CH", (R.country_of("9490"), R.country_of("3280")) == ("LI", "CH"))

print("== placement: the town is the guard")
notes = {}
pt, how = R.place(lz, BOOK.get(LUZERN), fake_geocode(), 8.0, 3.0, notes)
check("the (85.05, -180) corner pin is refused and the café's pin, same address, places the session",
      how == "café pin" and pt == (47.042935, 8.3138767) and notes.get("event page's own pin is not in its town (not used)") == 1,
      (pt, how, notes))
pt, how = R.place(g, BOOK.get("https://www.repair-cafe.ch/repaircafe/repair-cafe-grenchen/"), fake_geocode(), 8.0, 3.0)
check("a page pin inside its town is used first", how == "event pin", (pt, how))
pt, how = R.place(p, BOOK.get(MURTEN), fake_geocode(), 8.0, 3.0)
check("no page pin, the café's address -> the café's pin", how == "café pin" and pt == (46.930214, 7.1165429), (pt, how))
nost = R.parse_event_page(fx("event_murten_no_street.html"))
pt, how = R.place(nost, BOOK.get(MURTEN), fake_geocode(), 8.0, 3.0)
check("no street, the café's own postcode -> the café's pin", how == "café pin", (pt, how))
pt, how = R.place(dict(nost, postcode="3011", town="Bern"), BOOK.get(MURTEN),
                  lambda q, s: (46.948, 7.447) if q == "3011 Bern" else None, 8.0, 3.0)
check("no street in ANOTHER town -> unplaced, never the café's pin", pt is None and how == "no street, and not at its café's address",
      (pt, how))
far = dict(BOOK.get(MURTEN), pin=[47.20, 7.10])                  # 30 km north: a pin that is wrong
notes = {}
pt, how = R.place(p, far, fake_geocode({"Bernstrasse 9 Nouvo": (46.9300, 7.1170)}), 8.0, 3.0, notes)
check("a café pin 30 km from its town is not used; the street is geocoded in its town instead",
      how == "geocoded street" and notes.get("café pin is not in its town (not used)") == 1, (pt, how, notes))
bott = {"date": "2026-11-21", "line1": "Therwilerstrasse 18", "postcode": "4103", "town": "Bottmingen", "pin": None,
        "cafe_url": None, "title": "Repair Café", "cafe_name": "Repair Café Binningen-Bottmingen", "text": ""}
cafe16 = {"pin": [47.5226, 7.5733], "venue": "Therwilerstrasse 16", "postcode": "4103", "city": "Bottmingen"}
log = []
pt, how = R.place(bott, cafe16, fake_geocode({"Therwilerstrasse 18": (47.5230, 7.5710)}, log), 8.0, 3.0)
check("another house number than the café's -> the street is geocoded, with its postcode and town",
      how == "geocoded street" and ("Therwilerstrasse 18", ", 4103 Bottmingen, Schweiz") in log, (how, log))
notes = {}
pt, how = R.place(bott, None, fake_geocode({"Therwilerstrasse 18": (47.3, 8.2)}), 8.0, 3.0, notes)
check("Photon's answer 50 km off (a same-named street elsewhere) is refused",
      pt is None and how == "street not found in its town"
      and notes.get("the geocoder answered outside the town (not used)") == 1, (pt, how, notes))
pt, how = R.place(dict(bott, postcode="5400", town="Baden"), None,
                  lambda q, s: (48.7606, 8.2398) if q == "5400 Baden" else (47.47, 8.30), 8.0, 3.0)
check("a town Photon puts outside Switzerland (Baden-Baden for Baden) places nothing",
      pt is None and how == "the geocoder put the town outside Switzerland", (pt, how))
pt, how = R.place(bott, None, lambda q, s: None, 8.0, 3.0)
check("a town Photon does not find places nothing", pt is None and how == "town not found by the geocoder", (pt, how))

# Eschenbach SG (2026-10-24): Photon puts "8733 Eschenbach" 6.6 km from the
# village, where the merged municipality's point is; the page's pin and the
# café's pin agree to the metre on Rickenstrasse 29. A 5 km guard refused both.
esch = {"date": "2026-11-07", "line1": "Rickenstrasse 29", "postcode": "8733", "town": "Eschenbach",
        "pin": [47.2414855, 8.9259191], "cafe_url": None, "title": "Repair Café", "cafe_name": "Repair Café Eschenbach",
        "text": ""}
esch_geo = lambda q, s: (47.2742383, 8.9931626) if q == "8733 Eschenbach" else None
pt, how = R.place(esch, None, esch_geo, 8.0, 3.0)
check("a publisher's pin 6.1 km from Photon's town point (a merged municipality) is kept", how == "event pin", (pt, how))
notes = {}
pt, how = R.place(dict(esch, pin=None), None, lambda q, s: esch_geo(q, s) or (47.2414855, 8.9259191), 8.0, 3.0, notes)
check("but a STREET Photon puts 6.1 km away is not (one village over is its failure)",
      pt is None and notes.get("the geocoder answered outside the town (not used)"), (pt, how, notes))
dbl = R.parse_event_page(fx("event_murten_voranmeldung.html").replace(">3280 Murten", ">3280 3280 Murten"))
dash = R.parse_event_page(fx("event_murten_voranmeldung.html").replace(">3280 Murten", ">3280 - Murten"))
check("a postcode typed twice ('9642 9642 Ebnat-Kappel') or with a dash ('1880 - Bex') leaves the town alone",
      (dbl.get("postcode"), dbl.get("town"), dash.get("town")) == ("3280", "Murten", "Murten"), (dbl, dash))

print("== rows")
ev = R.build_event(EV + "repair-cafe-murten-morat-4/", p, BOOK.get(MURTEN), (46.930214, 7.1165429), SITE)
check("10:00 on 24 Oct 2026 in Zurich is 08:00Z (CEST); the row is Europe/Zurich, CH, coords_exact",
      (ev.start_local, ev.start_utc, ev.end_utc, ev.timezone, ev.country, ev.coords_exact)
      == ("2026-10-24T10:00:00+02:00", "2026-10-24T08:00:00Z", "2026-10-24T10:15:00Z", "Europe/Zurich", "CH", True),
      (ev.start_local, ev.start_utc, ev.end_utc))
check("venue 'Nouvo', street 'Bernstrasse 9', postcode, town, promoter = the café, link = the session page",
      (ev.venue_name, ev.address, ev.postal_code, ev.city, ev.promoter, ev.ticket_url, ev.source_id)
      == ("Nouvo", "Bernstrasse 9", "3280", "Murten", "Repair Café Murten-Morat",
          EV + "repair-cafe-murten-morat-4/", "repairevent/repair-cafe-murten-morat-4"), ev)
check("'Nur auf Voranmeldung' puts 'Registration required' first",
      (ev.description or "").startswith("Registration required: see the event page."), ev.description)
check("the volunteer's number and e-mail are not copied (the placeholder 079 000 00 00 is cut from the café's text)",
      "079" not in (ev.description or "") and "@" not in (ev.description or "") and "example.ch" not in (ev.description or ""),
      ev.description)
ev12 = R.build_event(EV + "repair-cafe-murten-morat/", nost, BOOK.get(MURTEN), (46.930214, 7.1165429), SITE)
check("10:00 on 12 Dec 2026 is 09:00Z (CET)", ev12.start_utc == "2026-12-12T09:00:00Z", ev12.start_utc)
check("category community", ev.category == "community")
k1 = dict(bott, cafe_name="Repair Café Wollishofen", town="Zürich", postcode="8038")
k2 = dict(bott, cafe_name="Repair Café Altstetten", town="Zürich", postcode="8048")
e1, e2 = (R.build_event(EV + "a/", k, None, (47.37, 8.53), SITE) for k in (k1, k2))
check("two cafés' bare 'Repair Café' on one day in Zürich: named for their café, two fingerprints",
      e1.name == "Repair Café Wollishofen" and e2.name == "Repair Café Altstetten" and e1.fingerprint != e2.fingerprint,
      (e1.name, e2.name))
moved = R.build_event(EV + "a/", dict(k1, line1="Seestrasse 1"), None, (47.37, 8.53), SITE)
check("a corrected street keeps the fingerprint (title, day, café, town)", moved.fingerprint == e1.fingerprint)
names = {("flicken", "Tüftelwerk Luzern"): "Tüftelwerk Luzern",
         ("am Standort Badenerstrasse 658", "Repair Café GZ Loogarten"): "Repair Café GZ Loogarten",
         ("Repair Café Aarau mit Kleider-Tauschbörse «Share the Schrank»", "Repair Café Aarau"):
             "Repair Café Aarau mit Kleider-Tauschbörse «Share the Schrank»",
         ("FRC Jura-Jura bernois et LARC à Delémont (Caritas)", "Repair Café à LARC à Delémont"):
             "Repair Café à LARC à Delémont",
         ("Repair-Café", "Repair Café Altendorf"): "Repair Café Altendorf",
         ("Caffè Riparazione ACSI", "Caffè Riparazione ACSI Locarno"): "Caffè Riparazione ACSI Locarno",
         ("Repair Aktion im Parktheater", "Repair Café Grenchen"): "Repair Café Grenchen",
         ("Reparieren statt wegwerfen! 13. Februar 2027", "Repair Café Flawil"): "Repair Café Flawil",
         ("11. Repair Café Fraubrunnen", "Repair Café Fraubrunnen"): "11. Repair Café Fraubrunnen",
         ("Répare Café d’Épalinges d’automne", "Répare Café d'Épalinges"): "Répare Café d’Épalinges d’automne",
         ("Repair Café", ""): "Repair Café"}
for (t, c), want in names.items():
    check(f"event_name({t!r})", R.event_name(t, c) == want, R.event_name(t, c))
lo = R.parse_event_page(fx("event_loogarten_no_pin.html"))
evl = R.build_event(EV + "am-standort-badenerstrasse-658-31/", lo, BOOK.get(LOOGARTEN), (47.3875171, 8.4879502), SITE)
check("a title that is only a place is quoted in the description, under the café's name",
      evl.name == "Repair Café GZ Loogarten" and "«am Standort Badenerstrasse 658»" in (evl.description or ""),
      (evl.name, evl.description))
reg = {"Nur auf Voranmeldung: Anna Muster": True, "Voranmeldung ist nicht nötig.": False,
       "Il faut s’inscrire au préalable 10 jours avant": True, "Entrée libre, sans inscription.": False,
       "Iscrizione obbligatoria allo 091 000 00 00": True, "Ohne Anmeldung, einfach vorbeikommen": False,
       "Bitte vorgängig anmelden": True, "Kommen Sie einfach vorbei.": False}
for text, want in reg.items():
    check(f"registration_required({text!r}) is {want}", R.registration_required(text) == want)
check("clean_text cuts +41, 0xx and e-mail forms",
      R.clean_text("Infos: +41 79 123 45 67 oder 044/437 90 20, mail an a.b@c-d.ch. Bis bald!", 500)
      == "Infos: oder, mail an. Bis bald!", R.clean_text("Infos: +41 79 123 45 67 oder 044/437 90 20, mail an a.b@c-d.ch. Bis bald!", 500))
check("clean_text cuts a spaced or (at) address", R.clean_text("Anmeldung: a.muster @ epost.ch oder b(at)example.ch", 500)
      == "Anmeldung: oder", R.clean_text("Anmeldung: a.muster @ epost.ch oder b(at)example.ch", 500))
check("clean_text keeps dates and times", R.clean_text("Samstag, 08.11.2025, 10.00-13.00 Uhr", 500)
      == "Samstag, 08.11.2025, 10.00-13.00 Uhr")

print("== the cursor: what is fetched")
rows = R.parse_sitemap(fx("repairevent-sitemap.xml")) + sm2
todo, counts = R.select_pages(rows, {}, TODAY, 548, 30)
check("cold: the two URLs last modified before the floor (18 months) are never fetched",
      counts["lastmod before the floor, never fetched"] == 2 and EV + "flicken-10/" not in todo
      and EV + "repaircafe-bex-10eme-edition/" not in todo, counts)
check("cold: the rest newest lastmod first", todo[:3] == [EV + "repair-cafe-murten-morat-4/", EV + "repair-cafe-murten-morat/",
                                                          EV + "am-standort-badenerstrasse-658-31/"], todo)
pages = {}
R.remember(pages, EV + "repair-cafe-murten-morat-4/", "2026-10-23T09:35:33+00:00", p, TODAY)
R.remember(pages, EV + "repair-aktion-im-parktheater-2/", "2025-06-10T12:30:44+00:00", g, TODAY)
R.remember(pages, EV + "repair-cafe-murten-morat/", "2026-07-20T20:29:48+00:00", {"why": "no Datum"}, TODAY)
check("a past session keeps its date only; an upcoming one its page",
      "page" not in pages[EV + "repair-aktion-im-parktheater-2/"] and pages[EV + "repair-aktion-im-parktheater-2/"]["date"]
      == "2024-05-25" and pages[EV + "repair-cafe-murten-morat-4/"]["page"]["town"] == "Murten")
todo, counts = R.select_pages(rows, pages, TODAY, 548, 30)
check("warm: a known, unchanged page is not fetched again, past or upcoming",
      EV + "repair-cafe-murten-morat-4/" not in todo and EV + "repair-aktion-im-parktheater-2/" not in todo
      and counts["known, unchanged"] == 3, (todo, counts))
rows2 = [(u, "2026-10-24T08:00:00+00:00" if u.endswith("murten-morat-4/") else lm) for u, lm in rows]
todo, _ = R.select_pages(rows2, pages, TODAY, 548, 30)
check("a changed lastmod is fetched again, first", todo[0] == EV + "repair-cafe-murten-morat-4/", todo)
old = json.loads(json.dumps(pages))
old[EV + "repair-cafe-murten-morat-4/"]["v"] = R.PARSER_VERSION - 1
old[EV + "repair-aktion-im-parktheater-2/"]["v"] = R.PARSER_VERSION - 1
todo, counts = R.select_pages(rows, old, TODAY, 548, 30)
check("an upcoming session read by an older parser is fetched again; a past one is not",
      EV + "repair-cafe-murten-morat-4/" in todo and EV + "repair-aktion-im-parktheater-2/" not in todo
      and counts["upcoming, read by an older parser"] == 1, (todo, counts))
todo, _ = R.select_pages(rows, pages, date(2026, 11, 30), 548, 30)
check("a page that would not parse is asked again after 30 days, not before",
      EV + "repair-cafe-murten-morat/" in todo
      and EV + "repair-cafe-murten-morat/" not in R.select_pages(rows, pages, TODAY, 548, 30)[0])


# --------------------------------------------------------------------------- #
# A whole run over a fake site.

class Resp:
    def __init__(self, code, text="", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}


class FakeSession:
    def __init__(self, pages):
        self.pages, self.asked, self.headers = pages, [], {}

    def get(self, url, timeout=None, allow_redirects=True, **_k):
        self.asked.append(url)
        if url not in self.pages:
            return Resp(404, "<html>nicht gefunden</html>")
        got = self.pages[url]
        return got if isinstance(got, Resp) else Resp(200, got)


SITEMAP_PAGES = {
    "https://www.repair-cafe.ch/sitemap_index.xml": fx("sitemap_index.xml"),
    "https://www.repair-cafe.ch/repairevent-sitemap.xml": fx("repairevent-sitemap.xml"),
    "https://www.repair-cafe.ch/repairevent-sitemap2.xml": fx("repairevent-sitemap2.xml"),
    "https://www.repair-cafe.ch/reparieren/": fx("cafe_map.html"),
    EV + "repair-cafe-murten-morat-4/": fx("event_murten_voranmeldung.html"),
    EV + "repair-cafe-murten-morat/": fx("event_murten_no_street.html"),
    EV + "am-standort-badenerstrasse-658-31/": fx("event_loogarten_no_pin.html"),
    EV + "repair-cafe-stans-reparieren-statt-fortwerfen/": fx("event_stans_no_street.html"),
    EV + "frc-jura-jura-bernois-et-larc-a-delemont-caritas-2/": fx("event_delemont_inscription.html"),
    EV + "repair-aktion-im-parktheater-2/": fx("event_grenchen_pin.html"),
}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(pages, cursor, max_pages=None, fetch_until=None, robots=None, deadline=None):
    s = FakeSession(pages)
    clock = Clock()
    client = R.Client(s, 1.0, deadline, robots=robots, clock=clock, sleep=clock.sleep)
    out, offs = [], []
    rep = R.read_site(SITE, client, cursor, TODAY, fake_geocode(), out.append,
                      lambda e, why: offs.append((e, why)), max_pages=max_pages, fetch_until=fetch_until)
    return rep, out, offs, s.asked, clock


print("== a whole run")
cur = {"version": 1, "pages": {}, "cafes": {}}
rep, out, offs, asked, clock = run(SITEMAP_PAGES, cur, max_pages=2)
check("cold, max 2 pages: index, 2 sitemaps, the café map and the 2 newest pages - 6 requests",
      len(asked) == 6 and asked[-2:] == [EV + "repair-cafe-murten-morat-4/", EV + "repair-cafe-murten-morat/"], asked)
check("never faster than one request a second", clock.t >= 5.0, clock.t)
check("both Murten sessions are written, on the café's pin",
      sorted(e.source_id for e in out) == ["repairevent/repair-cafe-murten-morat", "repairevent/repair-cafe-murten-morat-4"]
      and rep["placed_by"] == {"café pin": 2}, (rep["placed_by"], [e.source_id for e in out]))
check("the rest is left for the next run and counted",
      rep["notes"].get("new or changed pages left for the next run (max pages)") == 4, rep["notes"])
rep, out, offs, asked, _ = run(SITEMAP_PAGES, cur)
check("the next run fetches only the 4 not yet read, newest first", asked[4:] == [
    EV + "am-standort-badenerstrasse-658-31/", EV + "repair-cafe-stans-reparieren-statt-fortwerfen/",
    EV + "frc-jura-jura-bernois-et-larc-a-delemont-caritas-2/", EV + "repair-aktion-im-parktheater-2/"], asked)
check("and writes all 3 upcoming sessions again from the cursor (Loogarten's 27 Jan 2027 is inside 180 days)",
      sorted(e.source_id for e in out) == ["repairevent/am-standort-badenerstrasse-658-31",
                                           "repairevent/repair-cafe-murten-morat", "repairevent/repair-cafe-murten-morat-4"],
      [e.source_id for e in out])
check("past sessions are remembered by date, not written",
      cur["pages"][EV + "repair-aktion-im-parktheater-2/"].get("date") == "2024-05-25"
      and "page" not in cur["pages"][EV + "repair-aktion-im-parktheater-2/"])
rep, out, offs, asked, _ = run(SITEMAP_PAGES, cur)
check("warm and unchanged: 4 requests (index, sitemaps, café map) and the same 3 rows", len(asked) == 4 and len(out) == 3,
      (asked, len(out)))
check("the café map's next dates are compared with what was written", rep["map_dates"] is not None, rep["map_dates"])
gone = dict(SITEMAP_PAGES)
gone["https://www.repair-cafe.ch/repairevent-sitemap2.xml"] = fx("repairevent-sitemap2.xml").replace(
    "repair-cafe-murten-morat-4/", "repair-cafe-murten-morat-x/")
gone[EV + "repair-cafe-murten-morat-x/"] = "<html>nicht gefunden</html>"
cur2 = json.loads(json.dumps(cur))
rep, out, offs, asked, _ = run(gone, cur2)
check("a post gone from the sitemap leaves the cursor and is not written; nothing is cancelled for it",
      EV + "repair-cafe-murten-morat-4/" not in cur2["pages"] and "repairevent/repair-cafe-murten-morat-4" not in
      [e.source_id for e in out] and not offs
      and rep["notes"].get("upcoming post gone from the sitemap (prune_cancelled re-probes its row)") == 1, rep["notes"])
part = dict(SITEMAP_PAGES)
del part["https://www.repair-cafe.ch/repairevent-sitemap.xml"]
cur3 = json.loads(json.dumps(cur))
rep, out, offs, asked, _ = run(part, cur3)
check("a sitemap not read drops nothing from the cursor", set(cur3["pages"]) == set(cur["pages"]) and len(out) == 3,
      rep["notes"])
off = dict(SITEMAP_PAGES)
off["https://www.repair-cafe.ch/repairevent-sitemap2.xml"] = fx("repairevent-sitemap2.xml").replace(
    "2026-10-23T09:35:33+00:00", "2026-10-24T07:00:00+00:00")
off[EV + "repair-cafe-murten-morat-4/"] = fx("event_murten_abgesagt.html")
cur4 = json.loads(json.dumps(cur))
live_fp = next(e.fingerprint for e in run(SITEMAP_PAGES, json.loads(json.dumps(cur)))[1]
               if e.source_id.endswith("murten-morat-4"))
rep, out, offs, asked, _ = run(off, cur4)
check("'ABGESAGT: Repair Café Murten-Morat' (lastmod changed, page fetched again) is a tombstone for the live row",
      len(offs) == 1 and offs[0][0].fingerprint == live_fp and offs[0][0].source_id == "repairevent/repair-cafe-murten-morat-4"
      and offs[0][1].startswith("ABGESAGT") and not any(e.source_id.endswith("murten-morat-4") for e in out),
      (offs, [e.source_id for e in out]))
cur5 = {"version": 1, "pages": {}, "cafes": {}}
rep, out, offs, asked, clock = run(SITEMAP_PAGES, cur5, fetch_until=3.5)
check("a fetch deadline stops the page walk (the sitemaps and map are read first) and the rest waits",
      rep["fetched"] == 0 and rep["notes"].get("new or changed pages left for the next run (deadline)") == 6
      and cur5["pages"] == {}, (rep["fetched"], rep["notes"]))


class NoRobots:
    def check(self, url):
        return {"allowed": False, "status": "ok", "rule": "Disallow: /", "crawl_delay": None}


rep, out, offs, asked, _ = run(SITEMAP_PAGES, json.loads(json.dumps(cur)), robots=NoRobots())
check("robots.txt refusing: no request, no row, the run says REFUSED", not asked and not out
      and (rep["stopped"] or "").startswith("REFUSED"), (asked, rep["stopped"]))
forbidden = dict(SITEMAP_PAGES)
forbidden["https://www.repair-cafe.ch/sitemap_index.xml"] = Resp(403, "Forbidden")
rep, out, offs, asked, _ = run(forbidden, json.loads(json.dumps(cur)))
check("a 403 is a refusal: one request, no row, never retried", len(asked) == 1 and not out
      and "HTTP 403" in (rep["stopped"] or ""), (asked, rep["stopped"]))


print("== the geocoder's budget and deadline")
import mapsee_ingest_ics as ics
calls = []
saved = ics.make_location_geocoder
ics._GEO_CACHE["3280 murten|, schweiz"] = (46.9283, 7.1167)


def fake_maker(session, suffix):
    def g(loc):
        calls.append((loc, suffix))
        return (46.9, 7.1)
    return g


ics.make_location_geocoder = fake_maker
try:
    t = [0.0]
    geo, unasked = R.make_geocode(None, deadline=10.0, clock=lambda: t[0])
    first = geo("Bernstrasse 9", ", 3280 Murten, Schweiz")
    t[0] = 11.0
    cached = geo("3280 Murten", ", Schweiz")
    skipped = geo("Pelzgasse 17", ", 5000 Aarau, Schweiz")
finally:
    ics.make_location_geocoder = saved
check("before the deadline a lookup goes to the shared geocoder", first == (46.9, 7.1) and calls[0][0] == "Bernstrasse 9",
      calls)
check("after it, a cached answer is still given, and an uncached one is not asked",
      cached is not None and skipped is None and len([c for c in calls if c[0] == "Pelzgasse 17"]) == 0 and unasked(),
      (cached, skipped, calls))
cur6 = {"version": 1, "pages": {}, "cafes": {}}
s = FakeSession(SITEMAP_PAGES)
client = R.Client(s, 1.0, None, clock=Clock(), sleep=lambda x: None)
rep = R.read_site(SITE, client, cur6, TODAY, lambda q, sfx: None, lambda e: None, None,
                  geocode_unasked=lambda: True)
check("a session not placed because the geocoder was not asked says it will be retried",
      rep["unplaced"].get("not geocoded this run (budget or deadline): retried next run") == 3, rep["unplaced"])


print("== main(): a scratch store and cursor, and a dry run writes nothing")
import requests as _requests
import robots_txt as _robots_txt


class AllowAll:
    def __init__(self, *a, **k):
        pass

    def check(self, url):
        return {"allowed": True, "status": "ok", "rule": None, "crawl_delay": None}


class MainSession(FakeSession):
    def __init__(self):
        super().__init__(SITEMAP_PAGES)


cfg_path = os.path.join(TMP, "cfg.json")
json.dump({"sites": [dict(SITE, pause=0)]}, open(cfg_path, "w", encoding="utf-8"))
store_path, cursor_path = os.path.join(TMP, "store.json"), os.path.join(TMP, "cursor.json")
_saved = (_requests.Session, _robots_txt.Robots, ics.make_location_geocoder, R.time.sleep)
_requests.Session, _robots_txt.Robots = MainSession, AllowAll
ics.make_location_geocoder = lambda session, suffix: (
    lambda loc: TOWNS.get(loc, (None, None)) if suffix in (", Schweiz", ", Liechtenstein") else (None, None))
R.time.sleep = lambda s: None
try:
    rc = R.main(["--config", cfg_path, "--store", store_path, "--cursor", cursor_path, "--max-minutes", "0"])
    saved_store = json.load(open(store_path, encoding="utf-8"))
    saved_cursor = json.load(open(cursor_path, encoding="utf-8"))
    dry_store, dry_cursor = os.path.join(TMP, "dry_store.json"), os.path.join(TMP, "dry_cursor.json")
    rc2 = R.main(["--config", cfg_path, "--store", dry_store, "--cursor", dry_cursor, "--max-minutes", "0", "--dry-run"])
finally:
    _requests.Session, _robots_txt.Robots, ics.make_location_geocoder, R.time.sleep = _saved
recs = saved_store.get("events") or []
check("main writes the 3 upcoming sessions to the store and the 6 pages to the cursor",
      rc == 0 and len(recs) == 3 and len(saved_cursor["pages"]) == 6, (rc, len(recs), len(saved_cursor.get("pages", {}))))
check("every stored row is placed, exact, and links its session page",
      all(r.get("latitude") and r.get("coords_exact") and (r.get("sources") or [{}])[0].get("url", "").startswith(EV)
          for r in recs), recs[:1])
check("a dry run writes neither store nor cursor", rc2 == 0 and not os.path.exists(dry_store)
      and not os.path.exists(dry_cursor))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
