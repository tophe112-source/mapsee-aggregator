"""Does the KČT adapter place a Czech club hike only where the club put it?

The fixtures under fixtures/kct/ are kct.cz's own markup from the pass of
2026-10-24: the calendar page trimmed to its FullCalendar script and 17 of its
records (plus one made-up record with no page url), and 13 event pages trimmed
to what the adapter reads, with the contact person, phone and email replaced
by made-up ones (this repo is public). Each check is a trap that pass measured:

  * the page explains its own marker: the organiser's start (155 of 237 pages
    within 90 days), the finish of a star hike (58), or the centre of Czechia
    when nothing was entered (24) - never a place;
  * a pin with two decimals, a one-degree slip (České Budějovice's Dům
    kultury pinned 111 km north) and a district nobody typed;
  * the add-to-calendar widget's 05:00-22:00 placeholder is not a clock;
  * a club meeting costs no request; a cancellation is Czech and in the title;
  * the cursor: an unchanged event's page is not read twice, and a deadline
    still writes what the cursor holds.

Pure functions, literal pages and a fake client: no network.
MAPSEE_TODAY is not needed: every date here is passed explicitly.
"""
import copy
import glob
import json
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_kct as K
import offers_0227 as O
from mapsee_ingest import EventStore
from mapsee_supabase_sync import DESCRIPTION_MAX, derive_categories, to_row

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "fixtures", "kct")
fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


TODAY = date(2026, 10, 24)
CFG = json.load(open(os.path.join(HERE, "kct_sources.json"), encoding="utf-8"))
LIST = open(os.path.join(FX, "kalendar-akci.html"), encoding="utf-8").read()
PAGES = {os.path.basename(p)[:-5]: open(p, encoding="utf-8").read()
         for p in glob.glob(os.path.join(FX, "*.html")) if not p.endswith("kalendar-akci.html")}
URL = "https://kct.cz/akce/{}/".format


class FakeClient:
    """kct.cz from the fixtures; Stop after `stop_after` event pages when set."""

    def __init__(self, pages, listing=LIST, stop_after=None):
        self.pages, self.listing, self.asked, self.stop_after = pages, listing, [], stop_after

    def get(self, url):
        if url == K.LIST_URL:
            self.asked.append(url)
            return self.listing
        if self.stop_after is not None and len(self.asked) - 1 >= self.stop_after:
            raise K.Stop("run deadline reached")
        self.asked.append(url)
        return self.pages.get(url.rstrip("/").rsplit("/", 1)[1])


def run(client, today=TODAY, cursor=None, cfg=CFG):
    written, cancelled = [], []
    rep = K.ingest(cfg, client, today, written.append, lambda ev, why: cancelled.append(ev), cursor=cursor)
    return rep, {e.source_id: e for e in written}, cancelled


# --------------------------------------------------------------------------- #
# the list
# --------------------------------------------------------------------------- #
rows, bad = K.parse_list(LIST)
check("the calendar's inline JSON reads: 16 records and 1 malformed (no page url)",
      (len(rows), bad) == (16, 1), (len(rows), bad))
r = next(x for x in rows if x["slug"] == "novorocni-vystup-na-cerchov-3")
check("a record carries its page, dates, district and start place",
      (r["url"], r["first"], r["last"], r["okres"], r["mistos"])
      == (URL("novorocni-vystup-na-cerchov-3"), date(2027, 1, 1), date(2027, 1, 1), "Domažlice", "Capartice"), r)
r = next(x for x in rows if x["slug"].startswith("setkani-sberatelu"))
check("whitespace in a title is collapsed", r["title"] == "Setkání sběratelů turistických vizitek a nálepek", r["title"])
try:
    K.parse_list("<html>no calendar here</html>")
    check("a page with no allEvents array raises, so the run stops instead of reading nothing", False)
except ValueError:
    check("a page with no allEvents array raises, so the run stops instead of reading nothing", True)

# --------------------------------------------------------------------------- #
# one event page
# --------------------------------------------------------------------------- #
det = K.parse_detail(PAGES["novorocni-vystup-na-cerchov-3"])
check("the marker is read as written, as strings (decimals are a rule)",
      (det.get("lat_s"), det.get("lon_s")) == ("49.41269", "12.7893"), det)
check("the page's sentence about its marker is read",
      det.get("pin_note") == "Značka je v místě souřadnic startu akce.", det.get("pin_note"))
check("start: date, clock, place and district",
      (det.get("start_date"), det.get("start_clock"), det.get("start_place"), det.get("start_okres"))
      == ("2027-01-01", "10:00", "Capartice", "Domažlice"), det)
check("finish: clock and place", (det.get("end_clock"), det.get("finish_place")) == ("12:00", "Čerchov"), det)
check("the routes with their lengths",
      det.get("types") == ["Novoroční čtyřlístek", "Pěší turistika (8 km)", "Lyžařská turistika (8 km)"],
      det.get("types"))
check("organiser and edition", (det.get("organiser"), det.get("edition")) == ("KČT – odbor Domažlice", 29), det)
check("the contact person, phone and email are never read",
      not any(x in json.dumps(det, ensure_ascii=False) for x in ("example.org", "555010100", "EXAMPLE")), det)
det = K.parse_detail(PAGES["novorocni-ctyrlistek-okolo-vresovic-3"])
check("no clock in the start row is no clock, whatever the widget's 05:00 says",
      det.get("start_clock") is None and det.get("end_clock") == "15:00", det)

# --------------------------------------------------------------------------- #
# a run over the list
# --------------------------------------------------------------------------- #
client = FakeClient(PAGES)
rep, ev, cancelled = run(client)
asked = client.asked
check("one list read, then one page per in-window event (13)", len(asked) == 14 and asked[0] == K.LIST_URL, asked)
check("no request for a club meeting, a past event, one beyond 90 days or a record with no page",
      not any(s in u for u in asked for s in ("programova-rada", "podzimni-stezka", "den-ceskeho-andela")), asked)
check("in date order, so a deadline leaves the farthest events",
      asked[1] == URL("s-trilobitem-do-holyne-bez-holinek-3")
      and asked[-1] == URL("setkani-sberatelu-turistickych-vizitek-a-nalepek"), (asked[1], asked[-1]))
check("nine rows are written", rep["kept"] == 9 and len(ev) == 9, (rep, sorted(ev)))
check("the meeting is counted as skipped", rep["skipped"].get("club meeting or training") == 1, rep["skipped"])

e = ev["novorocni-vystup-na-cerchov-3"]
check("Čerchov starts at 10:00 Prague time (09:00Z) and ends at the finish's 12:00",
      (e.start_local, e.start_utc, e.end_local, e.end_utc)
      == ("2027-01-01T10:00:00", "2027-01-01T09:00:00Z", "2027-01-01T12:00:00", "2027-01-01T11:00:00Z"),
      (e.start_local, e.start_utc, e.end_local, e.end_utc))
check("...on the organiser's pin, kept exact", (e.latitude, e.longitude, e.coords_exact) == (49.41269, 12.7893, True),
      (e.latitude, e.longitude, e.coords_exact))
check("...named by its start place, in Czechia, on Prague's clock",
      (e.venue_name, e.country, e.timezone) == ("Capartice", "CZ", "Europe/Prague"), (e.venue_name, e.country))
check("...linked to its own page, keyed by its slug",
      (e.ticket_url, e.source_id, e.source) == (URL("novorocni-vystup-na-cerchov-3"), "novorocni-vystup-na-cerchov-3", "kct"))
check("...outdoors, reaching the movement door through fitness",
      (e.category, e.categories) == ("outdoors", ["fitness"]), (e.category, e.categories))
check("...the routes in English and the start as the club wrote it",
      e.description.startswith("New Year's hike (Novoroční čtyřlístek), Hike (8 km), Ski tour (8 km).")
      and "Start: 10:00, Capartice (okres Domažlice)." in e.description
      and "Finish: 12:00, Čerchov." in e.description, e.description)
check("...and no contact of any person", "@" not in e.description and "555" not in e.description, e.description)

e = ev["predsilvestrovsky-vystup-na-brdo-3"]
check("a star hike pinned at its finish is named after the finish, and says so",
      e.venue_name == "rozhledna Brdo – Chřiby" and "The pin is the finish" in e.description,
      (e.venue_name, e.description))
check("no start clock is an all-day row, with no end", (e.start_local, e.end_local, e.start_utc) == ("2026-12-30", None, None),
      (e.start_local, e.end_local))
e = ev["novorocni-ctyrlistek-okolo-vresovic-3"]
check("a finish clock with no start clock stays in the text, not the row",
      e.start_local == "2027-01-02" and e.end_local is None and "Finish: 15:00." in e.description,
      (e.start_local, e.end_local, e.description))
e = ev["zimni-taboreni-na-zvicine-671-m-memorial-milana-polaka-3"]
check("a weekend camp runs Friday 16:00 to Sunday 12:00",
      (e.start_local, e.end_local, e.start_utc) == ("2027-01-08T16:00:00", "2027-01-10T12:00:00", "2027-01-08T15:00:00Z"),
      (e.start_local, e.end_local))
e = ev["mikulasska-prochazka-3"]
check("no district typed, but 'Chrudim, žst' names a district town: placed", e.latitude == 49.9482982, e.latitude)
e = ev["novorocni-ctyrlistek-v-hradeckych-lesich-3"]
check("'HK' is Hradec Králové (an alias): placed, and a family route reaches kids",
      e.latitude == 50.19137 and e.categories == ["kids", "fitness"], (e.latitude, e.categories))
e = ev["s-trilobitem-do-holyne-bez-holinek-3"]
check("a march with a 50 km running route reaches running", e.categories == ["running", "fitness"], e.categories)
e = ev["novorocni-sprintduatlon-3"]
check("a page that names no route is community; a duathlon in the title is running",
      (e.category, e.categories) == ("community", ["running"]), (e.category, e.categories))
e = ev["zimni-prahou-2026-pzp-42-2"]
check("'Praha 8, Praha-východ' is two districts, and the pin is in Prague",
      e.latitude == 50.126816666667, e.latitude)

check("the centre-of-Czechia pin is not a place",
      "novorocni-vystup-na-spicak-3" not in ev
      and rep["skipped"].get("no place entered (the pin is the centre of Czechia)") == 1, rep["skipped"])
check("a pin with two decimals is not believed",
      "vanocni-prosicka-2" not in ev and rep["skipped"].get("pin too coarse (under 3 decimals)") == 1)
check("České Budějovice's Dům kultury pinned 111 km north is not believed",
      "setkani-sberatelu-turistickych-vizitek-a-nalepek" not in ev
      and rep["skipped"].get("pin over 50 km from its district") == 1, rep["skipped"])
check("a pin with no district and no district town to check it against is not written",
      "drakiada-v-prostredni-lhote" not in ev
      and rep["skipped"].get("no district to check the pin against") == 1, rep["skipped"])
check("pins counted by what the page says they are",
      rep["pins"] == {"start": 10, "finish": 2, "centre of Czechia": 1}, rep["pins"])

# --------------------------------------------------------------------------- #
# the placement guard, case by case
# --------------------------------------------------------------------------- #
row = {"okres": "Domažlice", "mistos": "Capartice"}
base = {"lat_s": "49.41269", "lon_s": "12.7893", "pin_note": "Značka je v místě souřadnic startu akce.",
        "start_okres": "Domažlice", "start_place": "Capartice"}
pin, why = K.place_pin(base, row, CFG)
check("the guard passes a precise pin in its district", pin and pin["kind"] == "start" and pin["district"] == "domazlice",
      (pin, why))
pin, why = K.place_pin(dict(base, pin_note="Značka je v místě obce."), row, CFG)
check("a sentence the adapter does not know is not believed", pin is None and why == "pin of unknown meaning", why)
pin, why = K.place_pin(dict(base, lat_s="49.743756", lon_s="15.3360752", pin_note="Značka je v místě souřadnic startu akce."),
                       row, CFG)
check("the centre of Czechia is refused even under a start sentence", pin is None and "centre" in why, why)
pin, why = K.place_pin(dict(base, lat_s="48.5154", lon_s="14.172"), {"okres": "Český Krumlov", "mistos": "Kleť"},
                       dict(CFG))
check("a pin south of Czechia (Kleť's, in Austria) is refused", pin is None and why == "pin outside Czechia", why)
pin, why = K.place_pin(dict(base, lat_s="49.996", lon_s="14.149", start_okres="Prachatice", start_place="Lhenice – náves"),
                       {"okres": "Prachatice", "mistos": "Lhenice – náves"}, CFG)
check("Lhenice's one-degree slip (48.996 typed 49.996) is refused", pin is None and "over 50 km" in why, why)
pin, why = K.place_pin(dict(base, pin_note="Značka je v místě souřadnic cíle. Souřadnice startu akce nebyly zadány."),
                       row, CFG)
check("a finish sentence is a finish pin", pin and pin["kind"] == "finish", (pin, why))
pin, why = K.place_pin(dict(base, lat_s=None), row, CFG)
check("no marker is no place", pin is None and why == "no pin on the page", why)

D, A = CFG["districts"], CFG["district_aliases"]
check("'Ústí n.Orlicí' is Ústí nad Orlicí", K._district_keys_from_text("Ústí n.Orlicí", D, A) == ["usti-nad-orlici"])
check("'Praha 8, Praha-východ' is Prague and Praha-východ",
      K._district_keys_from_text("Praha 8, Praha-východ", D, A) == ["hlavni-mesto-praha", "praha-vychod"])
check("'Chomutov, ČR', 'Plzeň – město' and 'Frýdek Místek' read",
      [K._district_keys_from_text(x, D, A) for x in ("Chomutov, ČR", "Plzeň – město", "Frýdek Místek")]
      == [["chomutov"], ["plzen-mesto"], ["frydek-mistek"]])
check("'Děčínsko' and 'Ostrava' are aliases",
      [K._district_keys_from_text(x, D, A) for x in ("Děčínsko", "Ostrava ")] == [["decin"], ["ostrava-mesto"]])
check("'Vyškov, Hodonín' is two districts", K._district_keys_from_text("Vyškov, Hodonín", D, A) == ["vyskov", "hodonin"])
check("'Slovensko' and 'dle propozic' are no district",
      [K._district_keys_from_text(x, D, A) for x in ("Slovensko", "dle propozic", "")] == [[], [], []])
keys, how = K.districts_for({"start_place": "Most přes Ohři"}, {"okres": "", "mistos": "Most přes Ohři"}, CFG)
check("'Most' in a start place is a bridge, not the district of Most", keys == [], keys)

# --------------------------------------------------------------------------- #
# contacts, meetings, cancellations
# --------------------------------------------------------------------------- #
check("a note's sentence with an email or a phone is dropped, the rest kept",
      K.without_contacts("Sraz na vrcholu. Přihlášky: jana@example.org. Info na tel. 555 010 199. Občerstvení zajištěno.")
      == "Sraz na vrcholu. Občerstvení zajištěno.")
page = PAGES["novorocni-vystup-na-spicak-3"].replace(
    "Sraz na vrcholu Špičáku,", "Přihlášky na jana@example.org nebo 555 010 199. Sraz na vrcholu Špičáku,")
det = K.parse_detail(page)
check("...and the page's own note carries the sentence before the cut",
      "jana@example.org" in (det.get("note") or "") and "@" not in (K.without_contacts(det["note"]) or ""), det.get("note"))
for t in ("Programová rada a porada předsedů odborů KČT, oblast Moravskoslezská", "Oblastní konference KČT",
          "Školení a zkoušky vedoucích a cvičitelů PT", "Podzimní seminář odborů"):
    check(f"a meeting: {t}", K.is_meeting(t))
for t in ("Předpuchýřovský pochod – památka Viléma Kurze", "Novoroční výstup na Čerchov", "Zimní táboření na Zvičině"):
    check(f"not a meeting: {t}", not K.is_meeting(t))
for t, want in (("ZRUŠENO! – Podzimní stezka Polabím", "Podzimní stezka Polabím"),
                ("Zrušeno!!! První jarní pochod – memoriál Arnošta Valenty", "První jarní pochod – memoriál Arnošta Valenty"),
                ("Šternberský vandr – AKCE ZRUŠENA – Putování Nízkým Jeseníkem", "Šternberský vandr – Putování Nízkým Jeseníkem"),
                ("ZRUŠENO !! Technické důvody !! Turistický pochod Po stopách rytíře Zbyhoně",
                 "Turistický pochod Po stopách rytíře Zbyhoně"),
                ("Cvilínské šlápoty – ZRUŠENO z důvodu povodní", "Cvilínské šlápoty"),
                ("Zimní seminář VHT v Krkonoších !!! Seminář zrušen !!!", "Zimní seminář VHT v Krkonoších"),
                ("ODLOŽENO !!! Oblastní konference KČT", "Oblastní konference KČT"),
                ("Akce se nekoná – Zimní pochod", "Zimní pochod")):
    check(f"cancelled: {t[:40]}", K.cancelled_title(t) == (True, want), K.cancelled_title(t))
for t in ("Nekonečná stezka", "Zimní výstup – změna termínu", "Pochod Zrušenka"):
    check(f"not cancelled: {t}", K.cancelled_title(t) == (False, t), K.cancelled_title(t))

live = ev["novorocni-vystup-na-cerchov-3"]
off_list = LIST.replace("Novoro\\u010dn\\u00ed v\\u00fdstup na \\u010cerchov",
                        "ZRU\\u0160ENO! \\u2013 Novoro\\u010dn\\u00ed v\\u00fdstup na \\u010cerchov")
check("(the fixture edit took)", off_list != LIST)
rep2, ev2, cancelled2 = run(FakeClient(PAGES, listing=off_list))
check("a title that says ZRUŠENO writes no live row and one tombstone",
      "novorocni-vystup-na-cerchov-3" not in ev2 and rep2["cancelled"] == 1 and len(cancelled2) == 1, rep2)
check("...whose fingerprint is the live row's, so the sync can find that row",
      cancelled2 and cancelled2[0].fingerprint == live.fingerprint and cancelled2[0].name == live.name,
      cancelled2 and (cancelled2[0].name, cancelled2[0].fingerprint, live.fingerprint))
with tempfile.TemporaryDirectory() as d:
    st = EventStore(os.path.join(d, "s.json"))
    st.upsert(live)
    st.cancel(cancelled2[0], "the KČT calendar marks it cancelled")
    check("...and in the store the tombstone takes the live row off", live.fingerprint not in st.records
          and live.fingerprint in st.tombstones, list(st.records))

# --------------------------------------------------------------------------- #
# the cursor
# --------------------------------------------------------------------------- #
cur = {"version": K.CURSOR_VERSION, "pages": {}}
c1 = FakeClient(PAGES)
rep_a, ev_a, _ = run(c1, cursor=cur)
check("a first run fills the cursor with every page it read",
      len(cur["pages"]) == 13 and rep_a["reads"] == {"new": 13}, (len(cur["pages"]), rep_a["reads"]))
cur["pages"]["https://kct.cz/akce/no-longer-listed/"] = {"sig": "x", "read": "2026-10-20", "det": {}}
c2 = FakeClient(PAGES)
rep_b, ev_b, _ = run(c2, cursor=cur)
check("the same day, an unchanged list costs one request and writes the same rows",
      c2.asked == [K.LIST_URL] and rep_b["from_cursor"] == 13 and sorted(ev_b) == sorted(ev_a), (c2.asked, rep_b))
check("a page no longer listed leaves the cursor", "https://kct.cz/akce/no-longer-listed/" not in cur["pages"])
check("rows rebuilt from the cursor are the rows read from the pages",
      all(ev_b[k].fingerprint == ev_a[k].fingerprint and ev_b[k].description == ev_a[k].description for k in ev_a))
moved = LIST.replace('"mistos":"Capartice"', '"mistos":"Capartice, n\\u00e1ves"')
check("(the fixture edit took)", moved != LIST)
c3 = FakeClient(PAGES, listing=moved)
rep_c, _, _ = run(c3, cursor=copy.deepcopy(cur))
check("a list row that changed is read again, and only it",
      c3.asked[1:] == [URL("novorocni-vystup-na-cerchov-3")] and rep_c["reads"] == {"changed": 1}, (c3.asked, rep_c["reads"]))
c4 = FakeClient(PAGES)
rep_d, _, _ = run(c4, today=TODAY + timedelta(days=1), cursor=copy.deepcopy(cur))
check("the next day, only the event starting within 7 days is read again (Trilobit, Oct 31)",
      c4.asked[1:] == [URL("s-trilobitem-do-holyne-bez-holinek-3")] and rep_d["reads"] == {"soon": 1},
      (c4.asked, rep_d["reads"]))
c5 = FakeClient(PAGES)
rep_e, _, _ = run(c5, today=TODAY + timedelta(days=14), cursor=copy.deepcopy(cur))
check("after refresh_days (14) every page is read again",
      rep_e["read"] == rep_e["in_window"] - 1 and set(rep_e["reads"]) <= {"stale", "soon"}, rep_e)

cur9 = {"version": K.CURSOR_VERSION, "pages": {}}
broken = dict(PAGES, **{"novorocni-vystup-na-cerchov-3": "<html><title>Údržba</title>Probíhá údržba webu.</html>"})
rep_i, ev_i, _ = run(FakeClient(broken), cursor=cur9)
check("a page this parser does not know is counted and not cached, so the next run reads it again",
      rep_i["skipped"].get("page did not parse") == 1 and URL("novorocni-vystup-na-cerchov-3") not in cur9["pages"]
      and "novorocni-vystup-na-cerchov-3" not in ev_i, rep_i["skipped"])

with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "kct_cursor.json")
    K.save_cursor(p, cur)
    back = K.load_cursor(p)
    check("the cursor survives a save and a load", back == json.loads(json.dumps(cur)))
    open(p, "w").write(json.dumps({"version": 0, "pages": {"x": {}}}))
    check("a cursor of another version starts fresh", K.load_cursor(p) == {"version": K.CURSOR_VERSION, "pages": {}})
    open(p, "w").write("{not json")
    check("an unreadable cursor starts fresh", K.load_cursor(p)["pages"] == {})
    check("no cursor path is no cursor", K.load_cursor(None)["pages"] == {} and K.save_cursor(None, cur) is None)

# --------------------------------------------------------------------------- #
# the budget
# --------------------------------------------------------------------------- #
cur = {"version": K.CURSOR_VERSION, "pages": {}}
c6 = FakeClient(PAGES, stop_after=4)
rep_f, ev_f, _ = run(c6, cursor=cur)
check("no request starts after the deadline: the list and 4 pages",
      len(c6.asked) == 5 and rep_f["stopped"] and rep_f["skipped"].get("deadline: page not read") == 9,
      (len(c6.asked), rep_f["stopped"], rep_f["skipped"]))
check("what was read before it is written (2 of the 4 place) and kept in the cursor",
      rep_f["kept"] == 2 and len(cur["pages"]) == 4,
      (rep_f["kept"], len(cur["pages"])))
c7 = FakeClient(PAGES)
rep_g, _, _ = run(c7, cursor=cur)
check("the next run reads only the 9 pages the deadline left", len(c7.asked) == 10 and rep_g["reads"] == {"new": 9},
      (len(c7.asked), rep_g["reads"]))
c8 = FakeClient(PAGES, stop_after=0)
rep_h, ev_h, _ = run(c8, today=TODAY + timedelta(days=14), cursor=copy.deepcopy(cur))
check("past refresh_days with no time left, the cursor's rows are still written (Trilobit is past by then)",
      len(c8.asked) == 1 and rep_h["kept"] == 8 and rep_h["from_cursor"] == 12, (len(c8.asked), rep_h))


class Resp:
    def __init__(self, code, text="", url=None):
        self.status_code, self.text, self.url = code, text, url


class Session:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def get(self, url, timeout=None, allow_redirects=True):
        self.asked.append(url)
        return self.answers.pop(0)


class Robots:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def check(self, url):
        return {"allowed": self.allowed, "status": "ok", "rule": None if self.allowed else "Disallow: /akce/",
                "crawl_delay": None}


clock = [100.0]
slept = []
cl = K.Client(Session([Resp(200, "a"), Resp(200, "b")]), 1.0, None, robots=Robots(),
              clock=lambda: clock[0], sleep=lambda s: slept.append(s))
cl.get(K.LIST_URL)
clock[0] += 0.25
cl.get(URL("x"))
check("one request a second: the second waits out the pause", slept == [0.75], slept)
for code in (403, 429):
    try:
        K.Client(Session([Resp(code)]), 1.0, None).get(K.LIST_URL)
        check(f"HTTP {code} is a refusal", False)
    except K.Refused:
        check(f"HTTP {code} is a refusal", True)
try:
    K.Client(Session([Resp(200, "ok")]), 1.0, None, robots=Robots(False)).get(URL("x"))
    check("robots.txt refusing a page ends the run before the request", False)
except K.Refused:
    check("robots.txt refusing a page ends the run before the request", True)
try:
    K.Client(Session([]), 1.0, deadline=5.0, clock=lambda: 6.0).get(K.LIST_URL)
    check("past the deadline no request starts", False)
except K.Stop:
    check("past the deadline no request starts", True)
check("a 404 skips the page, it does not end the run",
      K.Client(Session([Resp(404, "gone")]), 1.0, None).get(URL("x")) is None)
check("a redirect off kct.cz is not read",
      K.Client(Session([Resp(200, "x", url="https://example.org/")]), 1.0, None).get(URL("x")) is None)

# --------------------------------------------------------------------------- #
# what the sync makes of a row
# --------------------------------------------------------------------------- #


def synced(e):
    with tempfile.TemporaryDirectory() as d:
        st = EventStore(os.path.join(d, "s.json"))
        st.upsert(e)
        rec = next(iter(st.records.values()))
    return rec, to_row(rec, "00000000-0000-0000-0000-000000000000")


for sid, want in (("novorocni-vystup-na-cerchov-3", ("outdoors", "fitness")),
                  ("novorocni-ctyrlistek-v-hradeckych-lesich-3", ("outdoors", "kids")),
                  ("s-trilobitem-do-holyne-bez-holinek-3", ("outdoors", "running"))):
    rec, row = synced(ev[sid])
    prim, extras = derive_categories(rec)
    check(f"the sync keeps {sid[:30]} on {want[0]} with {want[1]}",
          prim == want[0] and want[1] in (extras or []), (prim, extras))
rec, row = synced(ev["novorocni-vystup-na-cerchov-3"])
check("the sync stores the Prague instant the adapter computed", row["starts_at"] == "2027-01-01T09:00:00Z", row["starts_at"])
check("the pin is the row's point", (row["lat"], row["lon"]) == (49.41269, 12.7893))
check("every description fits the sync's cap", all(len(e.description) <= DESCRIPTION_MAX for e in ev.values()),
      max(len(e.description) for e in ev.values()))
tags = {sid: O.classify({"title": synced(e)[1]["title"], "description": synced(e)[1]["description"]})[0]
        for sid, e in ev.items()}
check("no row claims an offer (free, discount, ...) the club did not make", not any(tags.values()), tags)

# --------------------------------------------------------------------------- #
# the config the job runs
# --------------------------------------------------------------------------- #
check("kct_sources.json names the calendar the adapter reads", CFG.get("list_url") == K.LIST_URL)
check("one request a second", float(CFG.get("pause", 0)) >= 1.0)
check("90 days, Wednesday and Sunday", (CFG.get("within_days"), CFG.get("run_weekdays")) == (90, [2, 6]))
check("it records where it was found and what was measured",
      str(CFG.get("_found", "")).startswith("manual:2026-10-24") and "_measured" in CFG)
check("77 district points (76 okresy and Prague), every one inside Czechia",
      len(CFG["districts"]) == 77 and all(K.CZ_BOX[0] <= la <= K.CZ_BOX[1] and K.CZ_BOX[2] <= lo <= K.CZ_BOX[3]
                                          for la, lo in CFG["districts"].values()))
check("every alias names a district", all(v in CFG["districts"] for v in CFG["district_aliases"].values()))

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all KČT checks passed")
