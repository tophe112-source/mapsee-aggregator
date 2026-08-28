#!/usr/bin/env python3
"""
test_ingest_mapasculturais.py — the ways a Mapas Culturais instance lies to a reader.

Every case here was bought with a live measurement on 2026-08-28 across eight
Brazilian instances, and the expensive ones are the three that are
indistinguishable from a healthy read: a date filter that is accepted and
ignored, a coordinate that is present and zero, and a placeholder that is
present and the word "undefined".

Run: python test_ingest_mapasculturais.py
"""
import json
import sys

import mapsee_ingest_mapasculturais as MC

TODAY = "2026-08-28"
SITE = {"name": "Test", "slug": "ce", "category": "community",
        "default_region": "CE", "default_country": "Brazil"}


def _occ(oid, starts_on, starts_at="19:00", ends_at="21:00", freq="once",
         until=None, name="Show", lat="-3.72", lon="-38.52", terms=None,
         endereco="Rua X, 10, Centro, 60000-000, Fortaleza, CE", _starts_on="skip"):
    rule = {"startsOn": starts_on, "startsAt": starts_at, "endsAt": ends_at,
            "frequency": freq}
    if until is not None:
        rule["until"] = until
    occ = {"id": oid, "rule": rule, "frequency": freq, "timezoneName": "Etc/UTC",
           "space": {"id": 1, "name": "Teatro", "endereco": endereco,
                     "location": ({"latitude": lat, "longitude": lon}
                                  if lat is not None else None)}}
    if name is not None:
        occ["event"] = {"id": 99, "name": name, "shortDescription": "d",
                        "singleUrl": "https://x.test/evento/99/",
                        "terms": {"linguagem": terms or ["Teatro"]}}
    # `_startsOn` defaults to ABSENT on purpose: that is the live shape on the
    # instances where the server filter silently fails.
    if _starts_on != "skip":
        occ["_startsOn"] = _starts_on
    return occ


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


def main():
    checks = []

    # ------------------------------------------------- 1. the filter that lies
    #
    # THE COUNT IS THE ONLY TELL, AND IT IS FREE. Espírito Santo answered a
    # future-filtered query with 1,575 rows — its ENTIRE archive, back to 1911 —
    # because it accepts `_startsOn=GTE()` and ignores it. Ceará applies the
    # same parameter. Both answer 200. Comparing the filtered count against the
    # unfiltered one is what separates them.
    class S:
        def __init__(self, total, filtered):
            self.t, self.f = total, filtered

        def get(self, url, timeout=None):
            return _Resp(str(self.f if "_startsOn" in url else self.t))

    checks.append((MC.filter_honoured(S(16587, 329), "b", TODAY) is True,
                   "a filtered count that differs from the total means the filter APPLIED"))
    checks.append((MC.filter_honoured(S(1575, 1575), "b", TODAY) is False,
                   "equal counts mean the server ACCEPTED the filter and ignored it"))

    class SBad:
        def get(self, url, timeout=None):
            return _Resp("<html>nope</html>", 500)
    checks.append((MC.filter_honoured(SBad(), "b", TODAY) is None,
                   "a count that cannot be read is 'unknown', which is not 'ignored'"))

    # ------------------------------------------- 2. rule.startsOn is the date
    #
    # The structured column is NULL on whole instances (all 1,575 of Espírito
    # Santo's, all 34 of João Pessoa's) while `rule` carries the real value
    # everywhere. That is WHY the server filter can pass a 2023 event: it tests
    # an empty column. Both spellings were compared on records where they
    # disagree before this was chosen.
    ancient = _occ(1, "1911-06-01", _starts_on=None)
    checks.append((MC.occurrence_dates(ancient, TODAY, 42) == [],
                   "a 1911 event survives the server's future filter and is dropped here"))
    checks.append((MC.occurrence_dates(_occ(2, "2023-09-12"), TODAY, 42) == [],
                   "so is a 2023 one — every row is re-checked against rule.startsOn"))

    # A populated `_startsOn` must not be able to override the rule.
    disagree = _occ(3, "2026-09-05", _starts_on={"date": "2020-01-01 00:00:00.000000"})
    checks.append((MC.occurrence_dates(disagree, TODAY, 42) == ["2026-09-05"],
                   "when the two spellings disagree, rule.startsOn wins"))

    # ------------------------------------------------------- 3. null island
    #
    # 55 of the 345 genuinely-future occurrences sit at 0,0 — and it arrives as
    # the STRING "0", so `if loc.get("latitude")` counts every one as placed.
    # The first pass of this audit reported Ceará at 328/329; the truth is 281.
    checks.append((MC.parse_location({"location": {"latitude": "0", "longitude": "0"}})
                   == (None, None),
                   "0,0 is a space nobody placed, not a place off West Africa"))
    checks.append((MC.parse_location({"location": {"latitude": "-3.72", "longitude": "-38.52"}})
                   == (-3.72, -38.52),
                   "a real point is read out of STRINGS"))
    checks.append((MC.parse_location({"location": None}) == (None, None),
                   "a space with no location at all is not placeable"))
    checks.append((MC.parse_location({"location": {"latitude": "abc", "longitude": "x"}})
                   == (None, None),
                   "an unparseable pair is refused rather than raising"))

    # ------------------------------------------- 4. 'undefined' is a real word
    #
    # The platform interpolates a missing field's JS value into the address.
    # 167 of Ceará's 329 live rows carry one. It is TRUTHY, so it survives every
    # `if not x` and would reach the row as street text.
    a = MC.clean_address("Rua Dragão do Mar, , Praia de Iracema, Fortaleza, undefined, CE, 60060-390")
    checks.append((a == "Rua Dragão do Mar",
                   "'undefined' and the empty comma-slots are stripped, street kept"))
    checks.append((MC.clean_address("Rua Domingos Façanha, , Centro, value, undefined, CE, 61940-140")
                   == "Rua Domingos Façanha",
                   "'value' is the platform's OTHER placeholder and is stripped too"))
    checks.append((MC.clean_address("Rua Doutor João Moreira, 540 , Centro, 60030-000, Fortaleza, CE")
                   == "Rua Doutor João Moreira, 540",
                   "a bare house number belongs with the street"))
    checks.append((MC.clean_address("undefined, undefined") is None,
                   "an address that is nothing but placeholders is no address"))

    # ---------------------------------------- 5. two address layouts, one shape
    #
    # Both are live on the SAME instance, so a comma-counting parser gets one of
    # them wrong. Anchor on the UF, which is unmistakable, and read outward.
    city, uf, cep = MC.split_address(
        "Rua Desembargador Lauro Nogueira, 1500, , Papicu, Fortaleza, undefined, CE, 60175-055")
    checks.append(((city, uf, cep) == ("Fortaleza", "CE", "60175-055"),
                   "layout A (UF second-last, CEP last) reads correctly"))
    city, uf, cep = MC.split_address(
        "Rua Doutor João Moreira, 540 , Centro, 60030-000, Fortaleza, CE")
    checks.append(((city, uf, cep) == ("Fortaleza", "CE", "60030-000"),
                   "layout B (UF last, CEP mid-string) reads correctly too"))
    checks.append((MC.split_address("Rua Dragão do Mar , 81 , Praia de Iracema, "
                                    "60060-390, FORTALEZA, CE")[0] == "Fortaleza",
                   "a SHOUTED city is title-cased, not passed through"))
    checks.append((MC.split_address("somewhere with no state") == (None, None, None),
                   "no UF anchor means no guess — a wrong city moves the label"))

    # ------------------------------------------- 6. recurrence with no end date
    #
    # 145 of Espírito Santo's live rows recur with no `until`. Unbounded, that
    # projects pins for ever and cleanup can never remove them: it deletes the
    # PAST, and a 2050 Tuesday is not past.
    weekly = _occ(4, "2026-08-29", freq="weekly", until="")
    days = MC.occurrence_dates(weekly, TODAY, 42)
    checks.append((len(days) == 6 and days[0] == "2026-08-29" and days[-1] <= "2026-10-09",
                   "a weekly with a BLANK until is bounded by the horizon, not by luck"))
    checks.append((MC.occurrence_dates(_occ(5, "2026-08-29", freq="weekly"), TODAY, 42) == days,
                   "a MISSING until behaves the same as a blank one"))
    bounded = MC.occurrence_dates(_occ(6, "2026-08-29", freq="weekly", until="2026-09-12"),
                                  TODAY, 42)
    checks.append((bounded == ["2026-08-29", "2026-09-05", "2026-09-12"],
                   "a real until is honoured and INCLUSIVE"))

    # A long-running class contributes its next few dates, not its whole history.
    old = MC.occurrence_dates(_occ(7, "2021-01-04", freq="weekly"), TODAY, 42)
    checks.append((old and all(d >= TODAY for d in old) and len(old) <= 7,
                   "a weekly registered in 2021 yields upcoming dates only, never 800 dead ones"))

    # An unrecognised cadence is one date, never an invented series.
    checks.append((MC.occurrence_dates(_occ(8, "2026-09-01", freq="fortnightly-ish"),
                                       TODAY, 42) == ["2026-09-01"],
                   "a frequency word we have not seen is treated as a single date"))

    # ------------------------------------------------- 7. one id, many dates
    #
    # EventStore keys on (source, source_id) and POPS the stored record when the
    # fingerprint moves, so a bare occurrence id makes each date DELETE the one
    # before it. BikeReg paid 121 rows to learn this.
    e1 = MC.to_event(weekly, "2026-08-29", SITE)
    e2 = MC.to_event(weekly, "2026-09-05", SITE)
    checks.append((e1.source_id != e2.source_id,
                   "two dates of ONE occurrence get different source_ids"))
    checks.append((e1.fingerprint != e2.fingerprint,
                   "and different fingerprints, so neither evicts the other"))

    # ---------------------------------------------------- 8. the time is local
    #
    # `timezoneName` is "Etc/UTC" on all 1,938 occurrences read, and it is not
    # true — the times are naive local clock times. Stamping them UTC serves a
    # 19:40 show at 16:40. The sync turns the coordinates into a zone instead.
    ev = MC.to_event(_occ(9, "2026-09-05", starts_at="19:40", ends_at="21:32"), "2026-09-05", SITE)
    checks.append((ev.start_local == "2026-09-05T19:40:00" and ev.start_utc is None,
                   "the clock time is emitted as LOCAL, never as an instant"))
    checks.append((ev.end_local == "2026-09-05T21:32:00", "and so is the end"))
    checks.append((ev.coords_exact is True,
                   "the instance's own point is exact — a Brazilian street in a US geocoder moves the pin"))

    # No clock time at all is an all-day date, not midnight.
    allday = MC.to_event(_occ(10, "2026-09-05", starts_at="", ends_at=""), "2026-09-05", SITE)
    checks.append((allday.start_local == "2026-09-05",
                   "no start time means an all-day row, not a 00:00 one"))
    # An end before the start is a typo, not a negative event.
    back = MC.to_event(_occ(11, "2026-09-05", starts_at="20:00", ends_at="19:00"), "2026-09-05", SITE)
    checks.append((back.end_local is None, "an end earlier than the start is dropped"))

    # ------------------------------------------------------- 9. the taxonomy
    for terms, want in ((["Teatro"], "theater"), (["Música Popular"], "music"),
                        (["Cinema"], "arts"), (["Curso ou Oficina"], "learning"),
                        (["Cultura Tradicional"], "community"), (["Jogos"], "party")):
        got = MC.to_event(_occ(12, "2026-09-05", terms=terms), "2026-09-05", SITE).category
        checks.append((got == want, f"linguagem {terms[0]!r} -> {want}"))
    unknown = MC.to_event(_occ(13, "2026-09-05", terms=["Outros"]), "2026-09-05", SITE)
    checks.append((unknown.category == "community",
                   "'Outros' means the organiser declined to say — fall back to the config"))

    # ------------------------------------------------ 10. orphans and no-name
    checks.append((MC.to_event(_occ(14, "2026-09-05", name=None), "2026-09-05", SITE) is None,
                   "an occurrence whose event is missing yields no row"))

    # --------------------------------------------- 11. main(), against a stub
    #
    # BOTH of mapsee_ingest_osm_amenities' production failures were in main(),
    # and nothing ran main(). So this drives the real one end to end.
    pages = {1: [_occ(20, "2026-09-01"), _occ(21, "1911-06-01"),
                 _occ(22, "2026-09-02", lat="0", lon="0"),
                 _occ(23, "2026-09-03", name=None)]}

    class Sess:
        headers = {}

        def get(self, url, timeout=None):
            if "@count=1" in url:
                return _Resp("4" if "_startsOn" in url else "99")
            page = int(url.split("@page=")[1].split("&")[0])
            return _Resp(pages.get(page, []))

    import tempfile, os
    MC.requests = type("R", (), {"Session": staticmethod(lambda: Sess())})
    cfg = {"sites": [dict(SITE, base_url="https://x.test", horizon_days=42)]}
    with tempfile.TemporaryDirectory() as d:
        cpath, spath = os.path.join(d, "c.json"), os.path.join(d, "s.json")
        open(cpath, "w").write(json.dumps(cfg))
        rc = MC.main(["--config", cpath, "--store", spath])
        stored = json.load(open(spath))["events"]
    checks.append((rc == 0, "main() returns 0 on a clean run"))
    checks.append((len(stored) == 1 and stored[0]["start_local"].startswith("2026-09-01"),
                   "main() keeps the future placeable row and drops the 1911, the 0,0 and the orphan"))

    # ------------------------------------- 12. the config is loadable, not just valid JSON
    #
    # osm_amenity_sources.json shipped as valid JSON that the code could not
    # load, and parkrun_sources.json was never committed at all while the job
    # printed a friendly skip. So read the real file with the real helpers.
    cfg = json.load(open("mapasculturais_sources.json", encoding="utf-8"))
    sites = cfg.get("sites") or []
    checks.append((bool(sites), "mapasculturais_sources.json exists and has sites"))
    declined = set(cfg.get("_not_included") or {})
    for s in sites:
        base = (s.get("base_url") or "").rstrip("/")
        checks.append((base.startswith("https://"), f"{s.get('name')}: has an https base_url"))
        checks.append((bool(s.get("slug")), f"{s.get('name')}: has a slug for its source_ids"))
        checks.append((int(s.get("horizon_days", 0)) > 0,
                       f"{s.get('name')}: carries a horizon — an unbounded recurrence pins for ever"))
        checks.append((base not in declined and base + "/" not in declined,
                       f"{s.get('name')}: is not also in _not_included"))
    slugs = [s.get("slug") for s in sites]
    checks.append((len(slugs) == len(set(slugs)),
                   "slugs are unique — they namespace source_id across instances"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
