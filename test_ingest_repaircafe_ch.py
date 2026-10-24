"""repair-cafe.ch: the date is on the page, the place is checked against the
town, and a page is read once per lastmod.

WHY THIS EXISTS. Every case below is a way the 2026-10-24 build could come out
well-formed and wrong, or cost the publisher hundreds of requests a day:

  * THE PIN. The Tüftelwerk Luzern page carries (85.0511, -180), the map
    tiles' corner, 4,596 km from its café. A pin is believed only within
    max_km of the session's own postcode and town; so is the café's pin and so
    is a geocoded street ("Photon answers every question"). Le Vilebrequin's
    Delémont page says Rue Emile-Boéchat 34 and carries LARC's pin (Passage des
    Ponts 2, 0.42 km off): a page pin that is ANOTHER café's, on a page whose
    Ort is its own café's address, gives way to the café's pin.
  * THE TOWN NODE. Photon, asked for "Kuhstall, 7503 Samedan", answers with
    Samedan itself; a "street" on the town's point is no street, and in a
    city the town's own node ("Luzern") is apart from the postcode's answer.
  * THE ADDRESS. "Bernstrasse 9  Nouvo" is the café's "Bernstrasse 9";
    "Therwilerstrasse 18" is NOT the café's "Therwilerstrasse 16"; an empty Ort
    is the café's own place, a street-less Ort in another town is nowhere.
  * THE NAME. Two cafés' bare "Repair Café" on one day in one town are two
    rows, and so are one café's sessions in two towns on one day (FRC Genève
    in Meyrin and in Genève); "flicken" and "am Standort Badenerstrasse 658"
    become their café. A post edited to another date carries its old
    fingerprint, so the sync moves the row instead of adding one; when the new
    date is not written this run (past, beyond the horizon, unplaced), the old
    date's row is tombstoned instead, every run until that day is past. A key
    another post writes live is never sent as a legacy one: through the sync's
    rekey_legacy it hid a double posting's real session from the third run on.
  * THE LIFT. A run that read every changed page is whole for lifting only
    (absence False): a session the café puts back on is lifted by the sync, a
    run that left a page unread records no whole read, and none is absence.
  * THE CURSOR. 1,866 sitemap URLs, mostly history: a page is fetched once per
    lastmod, never when its lastmod is older than the floor, newest first, and
    the walk resumes where a budget or a deadline stopped it.
  * THE CONTACT BOX. A volunteer's mobile number and e-mail are never copied,
    nor the name left beside them once they are cut.
  * THE CONDUCT. A bot challenge or a 403 ends the run with no row; five 5xx
    in a row stop the walk; a redirect off the site is not followed.

No network: the pages are the real markup of repair-cafe.ch (2026-10-24),
trimmed to the event section and its map script, with the volunteers' names,
numbers and addresses replaced by placeholders (event_delemont_vilebrequin_
template_pin.html is the 2026-12-05 page as served on 2026-10-24, pin and
all). event_murten_abgesagt.html is
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
check("a pin that is no number ('47.19.') is no pin, not an exception",
      R.parse_event_page(fx("event_grenchen_pin.html").replace("L.marker([", "L.marker([47.19., 7.39], {}); //[")).get("pin")
      is None)
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
check("the café map: 8 cafés, a /standorte/ site among them",
      len(BOOK) == 8 and "https://www.repair-cafe.ch/standorte/repair-cafe-in-der-stad-zug/" in BOOK, sorted(BOOK))
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
# Delémont (2026-10-24): the Vilebrequin page for 5 Dec 2026 says "Le
# Vilebrequin, Rue Emile-Boéchat 34" and carries [47.36297, 7.34710], which is
# the café map's pin for LARC (Passage des Ponts 2). Photon puts no. 34 at
# [47.36019, 7.35050], 30 m from the Vilebrequin café's pin and 402 m from
# the page's. The page's pin is 0.25 km from the town point: the town guard
# alone passes it.
VILEBREQUIN = "https://www.repair-cafe.ch/repaircafe/repair-cafe-au-vilebrequin-a-delemont/"
LARC = "https://www.repair-cafe.ch/repaircafe/repair-cafe-delemont/"
vil = R.parse_event_page(fx("event_delemont_vilebrequin_template_pin.html"))
check("the Vilebrequin page is read: its own café, its Ort, LARC's pin",
      (vil.get("cafe_url"), vil.get("line1"), vil.get("date")) == (VILEBREQUIN, "Le Vilebrequin, Rue Emile-Boéchat 34",
                                                                  "2026-12-05")
      and R.km(tuple(vil["pin"]), tuple(BOOK[LARC]["pin"])) < 0.001, vil)
notes = {}
pt, how = R.place(vil, BOOK.get(VILEBREQUIN), fake_geocode(), 8.0, 3.0, notes, others=BOOK.values())
check("a page pin that is another café's (LARC's), on a page whose Ort is its own café's address, gives way to "
      "the café's pin", how == "café pin" and pt == (47.3602508, 7.3508929)
      and notes.get("event page's pin is another café's, its Ort its own café's (not used)") == 1, (pt, how, notes))
pt, how = R.place(vil, BOOK.get(VILEBREQUIN), fake_geocode(), 8.0, 3.0)
check("(without the café map's other pins the page's pin would have been believed)", how == "event pin", (pt, how))
larc_page = R.parse_event_page(fx("event_delemont_inscription.html"))
pt, how = R.place(larc_page, BOOK.get(LARC), fake_geocode(), 8.0, 3.0, others=BOOK.values())
check("LARC's own page carries LARC's pin: its own café's pin is not another's", how == "event pin", (pt, how))
pt, how = R.place(dict(vil, line1="LARC, Passage des Ponts 2"), BOOK.get(VILEBREQUIN), fake_geocode(), 8.0, 3.0,
                  others=BOOK.values())
check("a Vilebrequin session held AT LARC (its Ort says so) keeps LARC's pin", how == "event pin", (pt, how))
# Hinwil (2026-10-24, five sessions): page and café pins 0.37 km apart and
# neither is another café's; Photon's Untere Bahnhofstrasse 17 is 25 m from
# the page's pin and 353 m from the café's. A disagreement alone settles nothing.
# What the rule does NOT touch. Two cafés at one address: a page pin that is
# the other's, with the Ort naming that address too, is the right pin.
shared = {"pin": [47.3640, 7.3520], "venue": "Le Vilebrequin, Rue Emile-Boéchat 34", "postcode": "2800",
          "city": "Delémont"}
pt, how = R.place(dict(vil, pin=shared["pin"]), BOOK.get(VILEBREQUIN), fake_geocode(), 8.0, 3.0,
                  others=list(BOOK.values()) + [shared])
check("a page pin that is another café's AT THE SAME ADDRESS (a shared building) is kept", how == "event pin"
      and pt == (47.3640, 7.3520), (pt, how))
near_other = {"pin": [47.36110, 7.35060], "venue": "Rue de la Maltière 10", "postcode": "2800", "city": "Delémont"}
pt, how = R.place(dict(vil, pin=near_other["pin"]), BOOK.get(VILEBREQUIN), fake_geocode(), 8.0, 3.0,
                  others=list(BOOK.values()) + [near_other])
check("page and café pins within 0.2 km agree: nothing to settle, even on another café's pin", how == "event pin"
      and pt == (47.36110, 7.35060), (pt, how))
pt, how = R.place(dict(vil, line1="Halle des expositions, Route de Bâle 20"), BOOK.get(VILEBREQUIN), fake_geocode(),
                  8.0, 3.0, others=BOOK.values())
check("the rule speaks only when the Ort is the session's own café's address (here it names neither café)",
      how == "event pin", (pt, how))
pt, how = R.place({"line1": "Salle communale", "postcode": "1284", "town": "Chancy", "pin": [46.1500, 5.8800]}, None,
                  lambda q, s: (46.1500, 5.9710) if q == "1284 Chancy" else None, 8.0, 3.0)
check("a pin 7 km from its town but across the border (west of Switzerland's box) is not used",
      pt is None and how == "street not found in its town", (pt, how))
hinwil_cafe = {"pin": [47.2993227, 8.8427233], "venue": "Pfarreisaal Kath. Kirche, Untere Bahnhofstrasse 17",
               "postcode": "8340", "city": "Hinwil"}
hinwil = {"line1": "Pfarreisaal der Katholischen Kirche, Untere Bahnhofstrasse 17", "postcode": "8340",
          "town": "Hinwil", "pin": [47.3017239, 8.839365]}
pt, how = R.place(hinwil, hinwil_cafe, lambda q, s: (47.3004302, 8.8414124) if q == "8340 Hinwil" else None,
                  8.0, 3.0, others=[hinwil_cafe] + list(BOOK.values()))
check("Hinwil: page and café pins 0.37 km apart, neither another café's -> the page's pin, as before",
      how == "event pin" and pt == (47.3017239, 8.839365), (pt, how))

# Samedan (2026-10-24): "Kuhstall, 7503 Samedan, Schweiz" comes back from
# Photon as osm place=village "Samedan", byte-identical to "7503 Samedan".
SAMEDAN = (46.5332403, 9.874586)
engiadin = {"pin": [46.529166, 9.8664647], "venue": "San Bastiaun 52", "postcode": "7503", "city": "Samedan"}
kuh = {"date": "2026-11-25", "line1": "Kuhstall", "postcode": "7503", "town": "Samedan", "pin": None}
notes = {}
pt, how = R.place(kuh, engiadin, lambda q, s: SAMEDAN, 8.0, 3.0, notes, others=[engiadin])
check("Photon's answer for a venue it does not know is the town's own point: refused, the session unplaced",
      pt is None and how == "street not found in its town"
      and notes.get("the geocoder answered with the town itself (not used)") == 1, (pt, how, notes))
pt, how = R.place(dict(kuh, line1="Kuhstall, Cho d'Punt 10"), engiadin,
                  lambda q, s: SAMEDAN if q in ("7503 Samedan", "Samedan", "Kuhstall, Cho d'Punt 10") else (46.5355, 9.8790),
                  8.0, 3.0)
check("the town's point for the whole line, then the street itself is tried and kept",
      how == "geocoded street" and pt == (46.5355, 9.8790), (pt, how))
# Gams: Photon knows "Oberstufenzentrum Widem" (Schulhaus Widem), 0.14 km from
# the village node: a street-less venue that IS found is kept.
pt, how = R.place({"line1": "Oberstufenzentrum Widem", "postcode": "9473", "town": "Gams", "pin": None}, None,
                  lambda q, s: (47.2054106, 9.4421264) if q in ("9473 Gams", "Gams") else (47.2052217, 9.4403015), 8.0, 3.0)
check("a venue Photon does find, 0.14 km from the town's point, is kept", how == "geocoded street", (pt, how))
# Luzern (Photon, 2026-10-24): "Luzern, Schweiz" is place=city at
# (47.052144, 8.3058123), 0.48 km from "6003 Luzern"; Genève's city node is
# 0.34 km from "1205 Genève". A venue Photon does not know, answered with the
# CITY's node, is no nearer the venue than the postcode's answer is.
LUZ_PC, LUZ_CITY = (47.0504373, 8.3116665), (47.052144, 8.3058123)
log = []


def luzern(answer):
    def g(q, s):
        log.append(q)
        return {"6003 Luzern": LUZ_PC, "Luzern": LUZ_CITY}.get(q, answer)
    return g


notes = {}
pt, how = R.place({"line1": "Kuhstall", "postcode": "6003", "town": "Luzern", "pin": None}, None, luzern(LUZ_CITY),
                  8.0, 3.0, notes)
check("a venue answered with the city's own node (0.48 km from the postcode's answer) is the town: refused",
      pt is None and how == "street not found in its town" and log.count("Luzern") == 1
      and notes.get("the geocoder answered with the town itself (not used)") == 1, (pt, how, notes, log))
log.clear()
pt, how = R.place({"line1": "Bireggstrasse 36", "postcode": "6003", "town": "Luzern", "pin": None}, None,
                  luzern((47.0432, 8.3122)), 8.0, 3.0)
check("a street 1.1 km from the city's node is kept (the 16 placed on 2026-10-24 lie 0.14-1.2 km from theirs)",
      how == "geocoded street" and pt == (47.0432, 8.3122), (pt, how))
log.clear()
pt, how = R.place({"line1": "Bireggstrasse 36", "postcode": "6003", "town": "Luzern", "pin": None}, None,
                  luzern((47.30, 8.20)), 8.0, 3.0)
check("the town's own node is asked only for an answer that would be kept", pt is None and "Luzern" not in log, log)
pt, how = R.place({"line1": "Kuhstall", "postcode": "6003", "town": "Luzern", "pin": None}, None,
                  lambda q, s: LUZ_PC if q == "6003 Luzern" else None if q == "Luzern" else LUZ_CITY, 8.0, 3.0)
check("a town Photon cannot find alone leaves the postcode's answer as the town's point", how == "geocoded street",
      (pt, how))

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
check("nor the volunteer's name left beside the number ('Nur auf Voranmeldung: 079 000 00 00 Anna Muster')",
      "Muster" not in (ev.description or "") and (ev.description or "").endswith("Nur auf Voranmeldung"), ev.description)
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
# FRC Genève runs sessions at Minoteries (Genève), Chambésy and Meyrin under
# one organiser link; make_fingerprint reads its city argument only when the
# place is empty, so the town must be IN the place.
frc = dict(bott, cafe_name="Repair Café FRC Genève", date="2026-11-07")
gm = R.build_event(EV + "frc-meyrin/", dict(frc, line1="Aula de l'école des Boudines", postcode="1217", town="Meyrin"),
                   None, (46.23, 6.08), SITE)
gg = R.build_event(EV + "frc-minoteries/", dict(frc, line1="Rue des Minoteries 3", postcode="1205", town="Genève"),
                   None, (46.19, 6.14), SITE)
check("one café, one day, two towns: one name, two fingerprints",
      gm.name == gg.name == "Repair Café FRC Genève" and gm.fingerprint != gg.fingerprint, (gm.fingerprint, gg.fingerprint))
check("the fingerprint is (title, day, café and town)", gm.fingerprint == R.make_fingerprint(
    "Repair Café FRC Genève", "2026-11-07", "Repair Café FRC Genève Meyrin", "Meyrin"), gm.fingerprint)
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
       "Bitte vorgängig anmelden": True, "Kommen Sie einfach vorbei.": False,
       "Keine Voranmeldung nötig, einfach vorbeikommen.": False}
for text, want in reg.items():
    check(f"registration_required({text!r}) is {want}", R.registration_required(text) == want)
check("clean_text cuts +41, 0xx and e-mail forms",
      R.clean_text("Infos: +41 79 123 45 67 oder 044/437 90 20, mail an a.b@c-d.ch. Bis bald!", 500)
      == "Infos: oder, mail an. Bis bald!", R.clean_text("Infos: +41 79 123 45 67 oder 044/437 90 20, mail an a.b@c-d.ch. Bis bald!", 500))
check("clean_text cuts a spaced or (at) address (and the label left with nothing after it)",
      R.clean_text("Anmeldung: a.muster @ epost.ch oder b(at)example.ch\nBis bald!", 500) == "Bis bald!",
      R.clean_text("Anmeldung: a.muster @ epost.ch oder b(at)example.ch\nBis bald!", 500))
# The three shapes a volunteer's name took on 2026-10-24 once the number was
# cut (26 contact lines on 321 upcoming pages): the name goes with it.
for line, want in {"Nur auf Voranmeldung: 079 000 00 00 Anna Muster": "Nur auf Voranmeldung",
                   "Anna Muster: 079 000 00 00": "", "Anna Muster, 071 000 00 00 oder 079 000 00 00": "",
                   "Email: info@example.ch": "",
                   "Ou par courriel info@example.ch": "Ou par courriel",
                   "Sur inscription préalable, uniquement par tél. au 078 000 00 00 (places limitées)":
                       "Sur inscription préalable, uniquement par tél. au (places limitées)"}.items():
    check(f"clean_text({line!r}) -> {want!r}", R.clean_text(line, 500) == want, R.clean_text(line, 500))
check("a line that held no contact keeps its names ('Jegliche Haftung', 'Repair Café Murten')",
      R.clean_text("Repair Café Murten\nJegliche Haftung ist ausgeschlossen.", 500)
      == "Repair Café Murten\nJegliche Haftung ist ausgeschlossen.")
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


def run(pages, cursor, max_pages=None, fetch_until=None, robots=None, deadline=None, geo=None, save=None, today=TODAY):
    s = FakeSession(pages)
    clock = Clock()
    client = R.Client(s, 1.0, deadline, robots=robots, clock=clock, sleep=clock.sleep)
    out, offs = [], []
    rep = R.read_site(SITE, client, cursor, today, geo or fake_geocode(), out.append,
                      lambda e, why, notice: offs.append((e, why, notice)), max_pages=max_pages,
                      fetch_until=fetch_until, save=save)
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
check("a run that left pages for the next is not a whole read", rep["complete"] is False, rep["complete"])
rep, out, offs, asked, _ = run(SITEMAP_PAGES, cur)
check("the next run fetches only the 4 not yet read, newest first", asked[4:] == [
    EV + "am-standort-badenerstrasse-658-31/", EV + "repair-cafe-stans-reparieren-statt-fortwerfen/",
    EV + "frc-jura-jura-bernois-et-larc-a-delemont-caritas-2/", EV + "repair-aktion-im-parktheater-2/"], asked)
check("and writes all 3 upcoming sessions again from the cursor (Loogarten's 27 Jan 2027 is inside 180 days)",
      sorted(e.source_id for e in out) == ["repairevent/am-standort-badenerstrasse-658-31",
                                           "repairevent/repair-cafe-murten-morat", "repairevent/repair-cafe-murten-morat-4"],
      [e.source_id for e in out])
check("one that fetched every new or changed page is, from today to the horizon",
      rep["complete"] is True and rep["window"] == (TODAY, date(2027, 4, 22)), (rep["complete"], rep["window"]))
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
check("  ...and the run is not whole", rep["complete"] is False)
miss = dict(SITEMAP_PAGES)
del miss[EV + "repair-cafe-stans-reparieren-statt-fortwerfen/"]
rep, out, offs, asked, _ = run(miss, {"version": 1, "pages": {}, "cafes": {}})
check("a session page not read (a 404) leaves the run not whole, though every other row is written",
      rep["complete"] is False and rep["notes"].get("session page not read (retried next run)") == 1 and len(out) == 3,
      (rep["complete"], rep["notes"], len(out)))
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
check("  ...and that run is not whole", rep["complete"] is False)


class NoRobots:
    def check(self, url):
        return {"allowed": False, "status": "ok", "rule": "Disallow: /", "crawl_delay": None}


rep, out, offs, asked, _ = run(SITEMAP_PAGES, json.loads(json.dumps(cur)), robots=NoRobots())
check("robots.txt refusing: no request, no row, the run says REFUSED", not asked and not out
      and (rep["stopped"] or "").startswith("REFUSED"), (asked, rep["stopped"]))
check("  ...and is not whole", rep["complete"] is False)
forbidden = dict(SITEMAP_PAGES)
forbidden["https://www.repair-cafe.ch/sitemap_index.xml"] = Resp(403, "Forbidden")
rep, out, offs, asked, _ = run(forbidden, json.loads(json.dumps(cur)))
check("a 403 is a refusal: one request, no row, never retried", len(asked) == 1 and not out
      and "HTTP 403" in (rep["stopped"] or ""), (asked, rep["stopped"]))


print("== conduct on a fake host")
WARM = json.dumps(cur)                       # a warm cursor: three rows to write
challenge = dict(SITEMAP_PAGES)
challenge["https://www.repair-cafe.ch/sitemap_index.xml"] = (
    "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>")
rep, out, offs, asked, _ = run(challenge, json.loads(WARM))
check("a 200 that is a bot challenge is a refusal: REFUSED, one request, no row (not even from the cursor)",
      len(asked) == 1 and not out and (rep["stopped"] or "").startswith("REFUSED")
      and "bot challenge" in rep["stopped"], (asked, rep["stopped"], len(out)))
challenge_page = dict(SITEMAP_PAGES)
challenge_page[EV + "repair-cafe-murten-morat-4/"] = Resp(200, '<html><script src="/cdn-cgi/challenge-platform/x.js">'
                                                               '</script></html>')
rep, out, offs, asked, _ = run(challenge_page, {"version": 1, "pages": {}, "cafes": {}})
check("a challenge on a session page ends the walk there: nothing after it is asked, no row",
      asked[-1] == EV + "repair-cafe-murten-morat-4/" and len(asked) == 5 and not out
      and (rep["stopped"] or "").startswith("REFUSED"), (asked, rep["stopped"]))
down = dict(SITEMAP_PAGES)
for u in list(down):
    if "/repairevent/" in u:
        down[u] = Resp(503, "Service Unavailable")
rep, out, offs, asked, _ = run(down, {"version": 1, "pages": {}, "cafes": {}})
check("five 5xx in a row stop the walk: 4 sitemap/map requests + 5 pages, the 6th never asked",
      len(asked) == 9 and "5 requests in a row failed" in (rep["stopped"] or "")
      and EV + "repair-aktion-im-parktheater-2/" not in asked
      and rep["notes"].get("new or changed pages left for the next run (the host stopped answering)") == 2,
      (len(asked), rep["stopped"], rep["notes"]))
flaky = dict(SITEMAP_PAGES)
flaky[EV + "repair-cafe-murten-morat-4/"] = Resp(503, "busy")
rep, out, offs, asked, _ = run(flaky, {"version": 1, "pages": {}, "cafes": {}})
check("one 5xx costs one page, retried next run; the walk goes on",
      len(asked) == 10 and rep["fetched"] == 5 and rep["stopped"] is None and rep["notes"].get(
          "session page not read (retried next run)") == 1, (len(asked), rep["stopped"], rep["notes"]))
away = dict(SITEMAP_PAGES)
away[EV + "repair-cafe-murten-morat-4/"] = Resp(301, "", {"Location": "https://elsewhere.example/repairevent/x/"})
away[EV + "repair-cafe-murten-morat/"] = Resp(301, "", {"Location": "/repairevent/repair-cafe-murten-morat-neu/"})
away[EV + "repair-cafe-murten-morat-neu/"] = fx("event_murten_no_street.html")
cur_away = {"version": 1, "pages": {}, "cafes": {}}
rep, out, offs, asked, _ = run(away, cur_away)
check("a redirect off the site is not followed (and the page is retried next run); one on the site is",
      not any("elsewhere.example" in u for u in asked) and EV + "repair-cafe-murten-morat-neu/" in asked
      and EV + "repair-cafe-murten-morat-4/" not in cur_away["pages"] and EV + "repair-cafe-murten-morat/" in cur_away["pages"],
      asked)

print("== what a run writes")
far_page = dict(SITEMAP_PAGES)
far_page[EV + "repair-cafe-murten-morat-4/"] = fx("event_murten_voranmeldung.html").replace("Sa, 24.10.2026", "Mi, 12.05.2027")
rep, out, offs, asked, _ = run(far_page, {"version": 1, "pages": {}, "cafes": {}})
check("a session 200 days out (past the 180-day horizon) is remembered, counted, and not written",
      not any(e.source_id.endswith("murten-morat-4") for e in out) and len(out) == 2
      and rep["notes"].get("upcoming, beyond the horizon (written when it comes inside)") == 1, (len(out), rep["notes"]))
no_ort = dict(SITEMAP_PAGES)
no_ort[EV + "repair-cafe-murten-morat-4/"] = fx("event_murten_voranmeldung.html").replace(
    "<br/>Bernstrasse 9  Nouvo</div>", "<br/></div>").replace(">3280 Murten", ">")
check("(the fixture has an empty Ort)", (lambda q: (q.get("line1"), q.get("postcode"), q.get("town")) == ("", "", ""))(
    R.parse_event_page(no_ort[EV + "repair-cafe-murten-morat-4/"])))
rep, out, offs, asked, _ = run(no_ort, {"version": 1, "pages": {}, "cafes": {}})
e_no = next((e for e in out if e.source_id.endswith("murten-morat-4")), None)
check("an empty Ort is the café's own address: written on the café's pin, in its town",
      e_no is not None and (e_no.latitude, e_no.longitude, e_no.city, e_no.postal_code) == (46.930214, 7.1165429, "Murten", "3280")
      and rep["notes"].get("no Ort on the page: the café's own address") == 1, (e_no, rep["notes"]))
twice = dict(SITEMAP_PAGES)
twice["https://www.repair-cafe.ch/repairevent-sitemap2.xml"] = fx("repairevent-sitemap2.xml").replace(
    "</urlset>", "<url><loc>" + EV + "repair-cafe-murten-morat-4b/</loc><lastmod>2026-10-23T09:40:00+00:00</lastmod></url>\n</urlset>")
twice[EV + "repair-cafe-murten-morat-4b/"] = fx("event_murten_voranmeldung.html")
rep, out, offs, asked, _ = run(twice, {"version": 1, "pages": {}, "cafes": {}})
check("two posts, one café, one day, one town (Weesen's double posting): written once",
      EV + "repair-cafe-murten-morat-4b/" in asked and len(out) == 3
      and len([e for e in out if e.source_id.startswith("repairevent/repair-cafe-murten-morat-4")]) == 1
      and rep["notes"].get("two posts, one café, one day (the first is kept)") == 1, ([e.source_id for e in out], rep["notes"]))
two_towns = dict(twice)
two_towns[EV + "repair-cafe-murten-morat-4b/"] = fx("event_murten_voranmeldung.html").replace(
    "<br/>Bernstrasse 9  Nouvo</div>", "<br/>Bundesgasse 3</div>").replace(">3280 Murten", ">3011 Bern")
rep, out, offs, asked, _ = run(two_towns, {"version": 1, "pages": {}, "cafes": {}},
                               geo=lambda q, sfx: (46.948, 7.447) if q == "3011 Bern" else
                               (46.9467, 7.4440) if q == "Bundesgasse 3" else fake_geocode()(q, sfx))
check("one café, one day, two towns (Murten and Bern): both written",
      len([e for e in out if e.source_id.startswith("repairevent/repair-cafe-murten-morat-4")]) == 2
      and not rep["notes"].get("two posts, one café, one day (the first is kept)"), ([e.source_id for e in out], rep["notes"]))
saves = []
_every = R.SAVE_EVERY
R.SAVE_EVERY = 2
try:
    rep, out, offs, asked, _ = run(SITEMAP_PAGES, {"version": 1, "pages": {}, "cafes": {}}, save=lambda: saves.append(1))
finally:
    R.SAVE_EVERY = _every
check("the cursor is saved every SAVE_EVERY pages during the walk (6 pages, every 2: 3 saves)",
      rep["fetched"] == 6 and len(saves) == 3, (rep["fetched"], len(saves)))

print("== a post edited to another date moves its row")
cur_d = {"version": 1, "pages": {}, "cafes": {}}
rep, out, offs, asked, _ = run(SITEMAP_PAGES, cur_d)
fp_24 = next(e.fingerprint for e in out if e.source_id.endswith("murten-morat-4"))
check("the cursor records the key each row was written under, with its day and name",
      cur_d["pages"][EV + "repair-cafe-murten-morat-4/"].get("keys") == [[fp_24, "2026-10-24", "Repair Café Murten-Morat"]]
      and not any(e.legacy_fingerprints for e in out), cur_d["pages"][EV + "repair-cafe-murten-morat-4/"])
redated = dict(SITEMAP_PAGES)
redated["https://www.repair-cafe.ch/repairevent-sitemap2.xml"] = fx("repairevent-sitemap2.xml").replace(
    "2026-10-23T09:35:33+00:00", "2026-10-24T07:00:00+00:00")
redated[EV + "repair-cafe-murten-morat-4/"] = fx("event_murten_voranmeldung.html").replace("Sa, 24.10.2026", "Sa, 31.10.2026")
rep, out, offs, asked, _ = run(redated, cur_d)
e31 = next(e for e in out if e.source_id.endswith("murten-morat-4"))
check("re-dated 24.10 -> 31.10: read again, a new key, and the old key carried as a legacy fingerprint",
      EV + "repair-cafe-murten-morat-4/" in asked and e31.start_local.startswith("2026-10-31")
      and e31.fingerprint != fp_24 and e31.legacy_fingerprints == [fp_24]
      and rep["notes"].get("re-dated or re-titled since its row was written: legacy fingerprint sent (the sync moves the row)") == 1,
      (e31.start_local, e31.legacy_fingerprints))
rep, out, offs, asked, _ = run(redated, cur_d)
e31b = next(e for e in out if e.source_id.endswith("murten-morat-4"))
check("the next run (nothing fetched) still carries it, so a failed move is tried again",
      len(asked) == 4 and e31b.fingerprint == e31.fingerprint and e31b.legacy_fingerprints == [fp_24], e31b.legacy_fingerprints)
check("the other rows carry none", all(not e.legacy_fingerprints for e in out if not e.source_id.endswith("murten-morat-4")))
retitled = dict(redated)
retitled["https://www.repair-cafe.ch/repairevent-sitemap2.xml"] = fx("repairevent-sitemap2.xml").replace(
    "2026-10-23T09:35:33+00:00", "2026-10-25T07:00:00+00:00")
retitled[EV + "repair-cafe-murten-morat-4/"] = redated[EV + "repair-cafe-murten-morat-4/"].replace(
    '!important;">Repair Café Murten-Morat</h2>', '!important;">Repair Café Murten-Morat mit Velo-Flickstation</h2>')
rep, out, offs, asked, _ = run(retitled, cur_d)
e31t = next(e for e in out if e.source_id.endswith("murten-morat-4"))
check("then re-titled: both earlier keys are carried",
      e31t.name == "Repair Café Murten-Morat mit Velo-Flickstation"
      and e31t.legacy_fingerprints == [fp_24, e31.fingerprint], (e31t.name, e31t.legacy_fingerprints))
off_d = dict(retitled)
off_d["https://www.repair-cafe.ch/repairevent-sitemap2.xml"] = fx("repairevent-sitemap2.xml").replace(
    "2026-10-23T09:35:33+00:00", "2026-10-26T07:00:00+00:00")
off_d[EV + "repair-cafe-murten-morat-4/"] = retitled[EV + "repair-cafe-murten-morat-4/"].replace(
    '!important;">Repair Café Murten-Morat mit', '!important;">ABGESAGT: Repair Café Murten-Morat mit')
rep, out, offs, asked, _ = run(off_d, cur_d)
check("then called off: the tombstone names the live key and every earlier one",
      len(offs) == 1 and offs[0][0].fingerprint == e31t.fingerprint
      and offs[0][0].legacy_fingerprints == [fp_24, e31.fingerprint], [(o[0].fingerprint, o[0].legacy_fingerprints) for o in offs])


print("== a post edited to a date not written this run calls its old date off")
SM2 = "https://www.repair-cafe.ch/repairevent-sitemap2.xml"
M4 = EV + "repair-cafe-murten-morat-4/"


def edited(base, n, **repl):
    """`base` with the Murten post's lastmod moved to the n-th edit and its
    page's text replaced."""
    out = dict(base)
    out[SM2] = fx("repairevent-sitemap2.xml").replace("2026-10-23T09:35:33+00:00", f"2026-10-24T0{n}:00:00+00:00")
    page = fx("event_murten_voranmeldung.html")
    for a, b in repl.items():
        page = page.replace(a.replace("_", " "), b)
    out[M4] = page
    return out


cur_m = {"version": 1, "pages": {}, "cafes": {}}
rep, out, offs, asked, _ = run(SITEMAP_PAGES, cur_m)
fp_24 = next(e.fingerprint for e in out if e.source_id.endswith("murten-morat-4"))
far = edited(SITEMAP_PAGES, 1, **{"Sa,_24.10.2026": "Mi, 12.05.2027"})
rep, out, offs, asked, _ = run(far, cur_m)
check("re-dated 24.10.2026 -> 12.05.2027 (past the horizon): no row, and the old date's row is tombstoned "
      "as the page's own evidence (not a title: notice False)",
      not any(e.source_id.endswith("murten-morat-4") for e in out) and len(offs) == 1
      and offs[0][0].fingerprint == fp_24 and offs[0][1] == R.MOVED_REASON and offs[0][2] is False
      and (offs[0][0].source_id, offs[0][0].ticket_url, offs[0][0].start_local, offs[0][0].name)
      == ("repairevent/repair-cafe-murten-morat-4", M4, "2026-10-24", "Repair Café Murten-Morat")
      and rep["moved_off"] == 1, [(o[0].fingerprint, o[1], o[2]) for o in offs])
check("the cursor holds it as dead, not as a key to move", cur_m["pages"][M4].get("dead") == [
    [fp_24, "2026-10-24", "Repair Café Murten-Morat"]] and not cur_m["pages"][M4].get("keys"), cur_m["pages"][M4])
rep, out, offs, asked, _ = run(far, cur_m)
check("the next run (nothing fetched) tombstones it again, in case that sync failed",
      len(asked) == 4 and [(o[0].fingerprint, o[2]) for o in offs] == [(fp_24, False)], (asked, offs))
back = edited(SITEMAP_PAGES, 2, **{"Sa,_24.10.2026": "Sa, 31.10.2026"})
rep, out, offs, asked, _ = run(back, cur_m)
e31m = next((e for e in out if e.source_id.endswith("murten-morat-4")), None)
check("edited again, to 31.10 (inside): written with NO legacy key (moving the cancelled row onto the live key "
      "would hide the session for good), and the 24.10 row is still called off",
      e31m is not None and e31m.legacy_fingerprints == [] and [o[0].fingerprint for o in offs] == [fp_24],
      (e31m and e31m.legacy_fingerprints, offs))
rep, out, offs, asked, _ = run(back, cur_m, today=date(2026, 10, 25))
check("once the old day is past it is forgotten: no tombstone, nothing dead in the cursor",
      not offs and "dead" not in cur_m["pages"][M4] and rep["moved_off"] == 0, (offs, cur_m["pages"][M4]))

cur_p = {"version": 1, "pages": {}, "cafes": {}}
run(SITEMAP_PAGES, cur_p)
rep, out, offs, asked, _ = run(edited(SITEMAP_PAGES, 1, **{"Sa,_24.10.2026": "Di, 20.10.2026"}), cur_p)
check("re-dated into the past (a corrected date): the 24.10 row is called off, the page kept by its date only",
      [(o[0].fingerprint, o[1]) for o in offs] == [(fp_24, R.MOVED_REASON)]
      and cur_p["pages"][M4].get("date") == "2026-10-20" and "page" not in cur_p["pages"][M4]
      and "keys" not in cur_p["pages"][M4], (offs, cur_p["pages"][M4]))

cur_t = {"version": 1, "pages": {}, "cafes": {}}
run(SITEMAP_PAGES, cur_t)
retitled_t = edited(SITEMAP_PAGES, 1, **{'!important;">Repair_Café_Murten-Morat</h2>':
                                         '!important;">Repair Café Murten-Morat mit Velo-Flickstation</h2>'})
rep, out, offs, asked, _ = run(retitled_t, cur_t, geo=lambda q, sfx: None)
check("re-TITLED on the same day and not placed this run: nothing is called off (the session is still on)",
      not offs and not out and cur_t["pages"][M4].get("keys") == [[fp_24, "2026-10-24", "Repair Café Murten-Morat"]],
      (offs, cur_t["pages"][M4].get("keys")))
rep, out, offs, asked, _ = run(retitled_t, cur_t)
e_t = next(e for e in out if e.source_id.endswith("murten-morat-4"))
check("placed the next run, it carries the old key, so the row moves", e_t.legacy_fingerprints == [fp_24] and not offs,
      (e_t.legacy_fingerprints, offs))

cur_u = {"version": 1, "pages": {}, "cafes": {}}
run(SITEMAP_PAGES, cur_u)
rep, out, offs, asked, _ = run(edited(SITEMAP_PAGES, 1, **{"Sa,_24.10.2026": "Sa, 31.10.2026"}), cur_u,
                               geo=lambda q, sfx: None)
check("re-DATED and not placed this run: the old date is called off all the same (the page says it is not then)",
      not out and [o[0].fingerprint for o in offs] == [fp_24], (out, offs))

cur_w = {"version": 1, "pages": {}, "cafes": {}}
run(SITEMAP_PAGES, cur_w)
both = edited(SITEMAP_PAGES, 1, **{"Sa,_24.10.2026": "Mi, 12.05.2027"})
both[SM2] = both[SM2].replace("</urlset>", "<url><loc>" + EV + "repair-cafe-murten-morat-4b/</loc>"
                              "<lastmod>2026-10-24T02:00:00+00:00</lastmod></url>\n</urlset>")
both[EV + "repair-cafe-murten-morat-4b/"] = fx("event_murten_voranmeldung.html")
rep, out, offs, asked, _ = run(both, cur_w)
check("one post moved off 24.10 while another post lists 24.10: that key is written live, never tombstoned",
      not offs and any(e.fingerprint == fp_24 and e.source_id.endswith("morat-4b") for e in out),
      ([e.source_id for e in out], offs))
check("retire_moved keeps a same-day key and moves only another day's",
      (lambda en: (R.retire_moved(en, "2026-11-07"), en))({"keys": [["a", "2026-10-31", "x"], ["b", "2026-11-07", "y"]],
                                                         "dead": [["c", "2026-11-01", "z"]]})
      == (1, {"keys": [["b", "2026-11-07", "y"]], "dead": [["c", "2026-11-01", "z"], ["a", "2026-10-31", "x"]]}))


print("== a double posting whose kept post is re-dated: the other post keeps the old day's row")
# Through the sync's real rekey_legacy over a fake events table. Before the fix
# post -4 sent the 24.10 key as a legacy one while -4b wrote it live, and from
# the third run the sync hid -4b's real session (hidden_at alone: never lifted).
import mapsee_supabase_sync as S

M4B = EV + "repair-cafe-murten-morat-4b/"
TABLE = {}                      # external_id -> hidden?


def with_4b(pages):
    out = dict(pages)
    out[SM2] = out[SM2].replace("</urlset>", "<url><loc>" + M4B + "</loc>"
                                "<lastmod>2026-10-23T09:40:00+00:00</lastmod></url>\n</urlset>")
    out[M4B] = fx("event_murten_voranmeldung.html")
    return out


class TablePatch:
    def patch(self, url, headers=None, data=None, timeout=None):
        from urllib.parse import parse_qs, urlsplit
        old, body = parse_qs(urlsplit(url).query)["external_id"][0][3:], json.loads(data)
        if "external_id" in body:
            TABLE[body["external_id"]] = TABLE.pop(old)
        else:
            TABLE[old] = True
        return Resp(204)


cur_dp = {"version": 1, "pages": {}, "cafes": {}}
dp = []
_fis = S.fetch_import_state
S.fetch_import_state = lambda session, url, key, ids, fields=(), detail=None: {i: None for i in ids if i in TABLE}
try:
    moved_31 = with_4b(edited(SITEMAP_PAGES, 1, **{"Sa,_24.10.2026": "Sa, 31.10.2026"}))
    for pages in (with_4b(SITEMAP_PAGES), moved_31, moved_31, moved_31):
        rep, out, offs, asked, _ = run(pages, cur_dp)
        S.rekey_legacy(TablePatch(), "https://x.supabase.co", "k",
                       {e.fingerprint: list(e.legacy_fingerprints) for e in out if e.legacy_fingerprints})
        for e in out:                                    # --only-new: insert what is not there
            TABLE.setdefault(e.fingerprint, False)
        dp.append((rep, {e.source_id.rsplit("/", 1)[-1]: e for e in out}, offs, dict(TABLE)))
finally:
    S.fetch_import_state = _fis
fp_24 = dp[0][1]["repair-cafe-murten-morat-4"].fingerprint
check("run 1: -4 and -4b both on 24.10, written once (-4 sorts first)",
      "repair-cafe-murten-morat-4b" not in dp[0][1] and dp[0][3] == {e.fingerprint: False for e in dp[0][1].values()},
      list(dp[0][1]))
r2 = dp[1][1]
check("run 2, -4 re-dated to 31.10: -4b writes 24.10 live, and -4 does NOT send 24.10 as a legacy key",
      r2["repair-cafe-murten-morat-4b"].fingerprint == fp_24
      and r2["repair-cafe-murten-morat-4"].start_local.startswith("2026-10-31")
      and r2["repair-cafe-murten-morat-4"].legacy_fingerprints == [] and not dp[1][2]
      and dp[1][0]["notes"].get("an old key another post is written under this run: left to that post") == 1,
      (r2["repair-cafe-murten-morat-4"].legacy_fingerprints, dp[1][0]["notes"]))
check("  ...and the key leaves -4's cursor entry for good (it is -4b's row now)",
      [k[0] for k in cur_dp["pages"][M4]["keys"]] == [r2["repair-cafe-murten-morat-4"].fingerprint]
      and [k[0] for k in cur_dp["pages"][M4B]["keys"]] == [fp_24], (cur_dp["pages"][M4]["keys"], cur_dp["pages"][M4B]))
check("no run sends a legacy key some row of that run is written under",
      all(not (set(e.legacy_fingerprints) & {x.fingerprint for x in rows.values()})
          for _rep, rows, _o, _t in dp for e in rows.values()))
check("runs 2-4: the 24.10 and the 31.10 rows are both in the table and neither is hidden",
      all(t.get(fp_24) is False and t.get(rows["repair-cafe-murten-morat-4"].fingerprint) is False
          for _rep, rows, _o, t in dp[1:]), [{k[:8]: v for k, v in t.items()} for *_x, t in dp])


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
    PAGES = SITEMAP_PAGES

    def __init__(self):
        super().__init__(self.PAGES)


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
    # The next day's run, the Murten post re-dated: a fresh store, as in CI.
    MainSession.PAGES = redated
    store2 = os.path.join(TMP, "store2.json")
    rc3 = R.main(["--config", cfg_path, "--store", store2, "--cursor", cursor_path, "--max-minutes", "0"])
    saved_store2 = json.load(open(store2, encoding="utf-8"))
    # The day after, the post moved again, past the horizon: the 31.10 row is
    # called off in the store, as the sync reads it.
    MainSession.PAGES = edited(SITEMAP_PAGES, 3, **{"Sa,_24.10.2026": "Mi, 12.05.2027"})
    store3 = os.path.join(TMP, "store3.json")
    rc4 = R.main(["--config", cfg_path, "--store", store3, "--cursor", cursor_path, "--max-minutes", "0"])
    saved_store3 = json.load(open(store3, encoding="utf-8"))
    # A crash after pages were fetched: the cursor keeps them all the same.
    MainSession.PAGES = SITEMAP_PAGES
    crash_cursor = os.path.join(TMP, "crash_cursor.json")
    _read_site = R.read_site

    def crashing(site, client, cursor, *a, **k):
        cursor["pages"][EV + "fetched-before-the-crash/"] = {"lastmod": "x", "checked": "2026-10-24", "date": "2026-11-01"}
        raise ValueError("boom")
    R.read_site = crashing
    try:
        rc5 = R.main(["--config", cfg_path, "--store", os.path.join(TMP, "store5.json"), "--cursor", crash_cursor,
                      "--max-minutes", "0"])
    finally:
        R.read_site = _read_site
    crash_saved = json.load(open(crash_cursor, encoding="utf-8")) if os.path.exists(crash_cursor) else {}
finally:
    MainSession.PAGES = SITEMAP_PAGES
    _requests.Session, _robots_txt.Robots, ics.make_location_geocoder, R.time.sleep = _saved
recs = saved_store.get("events") or []
check("main writes the 3 upcoming sessions to the store and the 6 pages to the cursor",
      rc == 0 and len(recs) == 3 and len(saved_cursor["pages"]) == 6, (rc, len(recs), len(saved_cursor.get("pages", {}))))
check("every stored row is placed, exact, and links its session page",
      all(r.get("latitude") and r.get("coords_exact") and (r.get("sources") or [{}])[0].get("url", "").startswith(EV)
          for r in recs), recs[:1])
check("a dry run writes neither store nor cursor", rc2 == 0 and not os.path.exists(dry_store)
      and not os.path.exists(dry_cursor))
old_key = next(r["fingerprint"] for r in recs if r["sources"][0]["url"].endswith("murten-morat-4/"))
new_rec = next(r for r in saved_store2.get("events") or [] if r["sources"][0]["url"].endswith("murten-morat-4/"))
check("the store hands the sync the re-dated row's old key (legacy_fingerprints), the others none",
      rc3 == 0 and new_rec["start_local"].startswith("2026-10-31") and new_rec["fingerprint"] != old_key
      and new_rec.get("legacy_fingerprints") == [old_key]
      and all("legacy_fingerprints" not in r for r in saved_store2["events"] if r is not new_rec),
      (new_rec.get("start_local"), new_rec.get("legacy_fingerprints")))
tombs = saved_store3.get("tombstones") or []
check("moved past the horizon: the store holds a tombstone for the 31.10 row (and the 24.10 one), no live row, "
      "and neither is a title notice",
      rc4 == 0 and sorted(t["fingerprint"] for t in tombs) == sorted([new_rec["fingerprint"], old_key])
      and all(t.get("reason") == R.MOVED_REASON and not t.get("notice") for t in tombs)
      and not any(r["sources"][0]["url"].endswith("murten-morat-4/") for r in saved_store3.get("events") or []),
      tombs)
check("a crash inside the run still saves the cursor it had (rc 1: the only site failed)",
      rc5 == 1 and EV + "fetched-before-the-crash/" in (crash_saved.get("pages") or {}), (rc5, list(crash_saved)))


print("== a whole read may lift what this adapter called off, and is never absence")
# The sync lifts a cancellation (cancelled_at == hidden_at, ours) only for a row
# a source read WHOLE this run writes live and this store does not tombstone
# (mapsee_supabase_sync main, test_cancellations.py). Read the store as it does.
from datetime import datetime, timezone
from mapsee_ingest import EventStore


def liftable(path, fp):
    inputs = S.cancellation_inputs(path)
    live = set().union(*inputs["units"].values()) if inputs["units"] else set()
    return fp in live and fp not in inputs["tombstones"]


def lastmod_to(stamp, page):
    out = dict(SITEMAP_PAGES)
    out[SM2] = fx("repairevent-sitemap2.xml").replace("2026-10-23T09:35:33+00:00", stamp)
    out[M4] = page
    return out


def main_run(pages, store, cursor, *extra):
    MainSession.PAGES = pages
    path = os.path.join(TMP, store)
    code = R.main(["--config", cfg_path, "--store", path, "--cursor", os.path.join(TMP, cursor),
                   "--max-minutes", "0", *extra])
    return code, path, json.load(open(path, encoding="utf-8"))


stale = os.path.join(TMP, "lift_stale.json")
st = EventStore(stale)
st.mark_complete(R.SOURCE, TODAY, date(2027, 4, 22), absence=False)
st.save()
_requests.Session, _robots_txt.Robots = MainSession, AllowAll
ics.make_location_geocoder = lambda session, suffix: (
    lambda loc: TOWNS.get(loc, (None, None)) if suffix in (", Schweiz", ", Liechtenstein") else (None, None))
R.time.sleep = lambda s: None
try:
    l1 = main_run(SITEMAP_PAGES, "lift1.json", "lift_cursor.json")
    l2 = main_run(lastmod_to("2026-10-24T05:00:00+00:00", fx("event_murten_abgesagt.html")), "lift2.json", "lift_cursor.json")
    l3 = main_run(lastmod_to("2026-10-24T06:00:00+00:00", fx("event_murten_voranmeldung.html")), "lift3.json",
                  "lift_cursor.json")
    # The main() sequence above left its cursor with the post moved past the
    # horizon (24.10 and 31.10 both tombstoned); the café corrects it to 24.10.
    l4 = main_run(edited(SITEMAP_PAGES, 4), "lift4.json", os.path.basename(cursor_path))
    lp = main_run(SITEMAP_PAGES, os.path.basename(stale), "lift_cold_cursor.json", "--max-pages", "1")
finally:
    MainSession.PAGES = SITEMAP_PAGES
    _requests.Session, _robots_txt.Robots, ics.make_location_geocoder, R.time.sleep = _saved
k_live = next(r["fingerprint"] for r in l1[2]["events"] if r["sources"][0]["url"] == M4)
read = (l1[2].get("complete_reads") or {}).get(R.SOURCE) or {}
check("a whole run is recorded as a complete read for LIFTING only (absence False), from today to the horizon",
      l1[0] == 0 and read.get("absence") is False and read.get("from", "").startswith("2026-10-24")
      and read.get("to", "").startswith("2027-04-2"), read)
check("  ...so the sync counts its live rows as listed by a source read whole",
      all(liftable(l1[1], r["fingerprint"]) for r in l1[2]["events"]) and len(l1[2]["events"]) == 3)
inputs = S.cancellation_inputs(l1[1])
prev = {R.SOURCE: {"source": R.SOURCE, "id_prefix": "", "from": read.get("from"), "to": read.get("to"),
                   "starts": {"gone": "2026-11-21T09:00:00Z"}}}
_at = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
check("  ...and never as an absence baseline: a session the last read had and this one has not is not absent",
      S.absent_fingerprints(prev, inputs, now=_at)[0] == {}, S.absent_fingerprints(prev, inputs, now=_at)[0])
check("ABGESAGT: the session is tombstoned by its title and the store holds no live row of it, so nothing lifts it",
      [t["fingerprint"] for t in l2[2].get("tombstones") or []] == [k_live] and not liftable(l2[1], k_live)
      and (l2[2].get("complete_reads") or {}).get(R.SOURCE), l2[2].get("tombstones"))
check("the café takes ABGESAGT out again: the same key is written live by a whole read, so the sync lifts it "
      "(read in passing, it stayed hidden)",
      l3[0] == 0 and liftable(l3[1], k_live) and not l3[2].get("tombstones"), (l3[2].get("tombstones"), l3[1]))
l4_tombs = sorted(t["fingerprint"] for t in l4[2].get("tombstones") or [])
check("a post moved past the horizon and corrected back to 24.10: 24.10 is live and liftable, 31.10 stays called off",
      l4[0] == 0 and liftable(l4[1], old_key) and l4_tombs == [new_rec["fingerprint"]]
      and not liftable(l4[1], new_rec["fingerprint"]), (l4_tombs, old_key))
check("a run that is not whole (max 1 page) records no complete read, and drops one an earlier run left in its store",
      lp[0] == 0 and R.SOURCE not in (lp[2].get("complete_reads") or {}), lp[2].get("complete_reads"))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
