#!/usr/bin/env python3
"""Exact publisher-labelled hours; no country clock or generic first-time match."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mapsee_ingest_jsonld as jl
from mapsee_ingest import EventStore

URL = "https://muetterzentrum-dortmund.de/events/stricken-mit-ursel/"
LISTING = "https://muetterzentrum-dortmund.de/events/"
VENUE = {"name": "M\u00fctterzentrum Dortmund", "lat": 51.5096268, "lon": 7.4198657,
         "city": "Dortmund", "country": "DE", "postal_code": "44149"}
# Publisher JSON-LD captured 2026-11-04. Dates roll to the next Wednesday for
# to_event's past-date gate; the description and exact place remain verbatim.
DESCRIPTION = (
    "&lt;p&gt;Jeden Mittwoch steht &lt;strong&gt;Ursula Enderling&lt;/strong&gt; "
    "f\u00fcr alle eure Fragen zur Verf\u00fcgung.&lt;/p&gt; "
    "&lt;p&gt;&lt;strong&gt;Wann:&lt;/strong&gt; Mittwochs von 10:00 \u2013 16:00 Uhr "
    "&#8211; ihr k\u00f6nnt zu jeder Uhrzeit dazusto\u00dfen!&lt;/p&gt; "
    "&lt;p&gt;&lt;strong&gt;Wo:&lt;/strong&gt; Im Caf\u00e9 des M\u00fctterzentrums&lt;/p&gt; "
    "&lt;p&gt;Das Angebot ist kostenlos, eine Anmeldung ist nicht n\u00f6tig \u2013 "
    "bitte rufen Sie aber am besten vorher kurz an, ob Frau Enderling wirklich "
    "an diesem Tag kommt: 0231 997 89 60&lt;/p&gt;"
)


def fixture():
    today = datetime.now(timezone.utc).date()
    day = today + timedelta(days=(2 - today.weekday()) % 7)
    return {"@type": "Event", "name": "Stricken mit Ursel", "url": URL,
            "startDate": day.isoformat(), "endDate": day.isoformat(),
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "description": DESCRIPTION,
            "location": {"@type": "Place", "name": "Caf\u00e9",
                         "address": "Hospitalstra\u00dfe 6, 44149 Dortmund"}}


def page(item):
    return '<script type="application/ld+json">' + json.dumps(item) + '</script>'


class DortmundHours(unittest.TestCase):
    def setUp(self):
        self.item = fixture()
        self.day = self.item["startDate"]

    def event(self, item=None, flag="dortmund_schedule"):
        return jl.to_event(item or self.item, URL, "community", None, VENUE,
                           description_hours=flag)

    def test_production_page_parser_and_normalizer(self):
        item, opts = jl._prepared(page(self.item), URL, detail=True)[0]
        event = self.event(item)
        self.assertEqual((event.start_local, event.end_local),
                         (self.day + "T10:00:00", self.day + "T16:00:00"))
        self.assertIn("bitte rufen Sie aber am besten vorher kurz an", event.description)
        self.assertIn("0231 997 89 60", event.description)
        self.assertEqual(event.recurring_days, None)
        self.assertEqual(event.category, "community")

    def test_opt_in_and_identity(self):
        raw, fixed = self.event(flag=None), self.event()
        self.assertEqual(raw.start_local, self.day)
        self.assertEqual(raw.end_local, self.day)
        self.assertEqual((raw.fingerprint, raw.source_id, raw.latitude, raw.longitude),
                         (fixed.fingerprint, fixed.source_id, fixed.latitude, fixed.longitude))
        self.assertEqual(self.event(flag="center_when").start_local, self.day)

    def test_current_prose_changes_clock(self):
        self.item["description"] = DESCRIPTION.replace("10:00", "09:30").replace("16:00", "15:45")
        self.assertEqual(jl._dortmund_schedule_hours(self.item, URL),
                         (self.day + "T09:30:00", self.day + "T15:45:00"))

    def test_source_timed_stamps_win(self):
        self.item.update(startDate=self.day + "T11:00:00", endDate=self.day + "T17:00:00")
        self.assertIsNone(jl._dortmund_schedule_hours(self.item, URL))
        event = self.event()
        self.assertEqual((event.start_local, event.end_local),
                         (self.day + "T11:00:00", self.day + "T17:00:00"))

    def test_wrong_or_ambiguous_inputs_are_unresolved(self):
        mutations = [
            {"name": "Another class"},
            {"url": "https://muetterzentrum-dortmund.de/events/another-class/"},
            {"url": "https://[invalid"},
            {"startDate": "2026-11-05", "endDate": "2026-11-05"},
            {"startDate": "2026-02-30", "endDate": "2026-02-30"},
            {"endDate": (date.fromisoformat(self.day) + timedelta(days=1)).isoformat()},
            {"endDate": self.day + "T16:00:00"},
            {"location": {"@type": "VirtualLocation", "name": "Caf\u00e9"}},
            {"location": {"@type": "Place", "name": "Other cafe", "address": "Hospitalstra\u00dfe 6, 44149 Dortmund"}},
            {"location": {"@type": "Place", "name": "Caf\u00e9", "address": "Another street, Dortmund"}},
            {"eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode"},
            {"description": "Wann: Mittwochs von 10:00 - 16:00 Uhr Wo: Online"},
            {"description": DESCRIPTION.replace("Mittwochs", "Donnerstags")},
            {"description": DESCRIPTION.replace("10:00", "25:00")},
            {"description": DESCRIPTION.replace("16:00", "09:00")},
            {"description": DESCRIPTION.replace("16:00", "10:00")},
            {"description": DESCRIPTION.replace("16:00 Uhr", "16:00 Uhr oder 18:00 - 20:00 Uhr")},
            {"description": DESCRIPTION + " Wann: Mittwochs von 10:00 - 16:00 Uhr Wo: Im Cafe"},
            {"description": "B\u00fcrozeiten Montag-Freitag 09:00-14:30. Telefon 0231 997 89 60"},
            {"description": "Wann: Mittwoch 2026-11-04 von 10:00 - 16:00 Uhr Wo: Im Cafe"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertIsNone(jl._dortmund_schedule_hours(dict(self.item, **mutation), URL))
        self.assertIsNone(jl._dortmund_schedule_hours(self.item, URL.replace(".de", ".example")))
        self.assertIsNone(jl._dortmund_schedule_hours(self.item, LISTING + "another-class/"))

    def test_label_case_and_unrelated_phone_or_office_hours(self):
        self.item["description"] = DESCRIPTION.replace("Wann:", "WANN:").replace("Mittwochs", "MITTWOCHS")
        self.item["description"] += " B\u00fcrozeiten Montag - Freitag 09:00 - 14:30 Uhr. Telefon 0231 997 89 60."
        self.assertEqual(jl._dortmund_schedule_hours(self.item, URL),
                         (self.day + "T10:00:00", self.day + "T16:00:00"))

    def test_cancelled_and_virtual_remain_cancelled_or_virtual(self):
        self.assertIsNone(self.event(dict(self.item, eventStatus="https://schema.org/EventCancelled")))
        self.assertIsNone(self.event(dict(self.item, eventAttendanceMode="https://schema.org/OnlineEventAttendanceMode")))

    def test_cache_refresh_is_only_for_sole_publisher_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            for mixed in (False, True):
                with self.subTest(mixed=mixed):
                    store = EventStore(str(Path(folder) / "events.json"))
                    raw = self.event(flag=None)
                    store.upsert(raw)
                    if mixed:
                        store.records[raw.fingerprint]["sources"].append({"source": "ics", "source_id": "other"})
                    jl.store_item(store, self.item, URL, "community", None, VENUE, None,
                                  {"description_hours": "dortmund_schedule"}, {})
                    cached = store.records[raw.fingerprint]
                    self.assertEqual(cached["start_local"], self.day if mixed else self.day + "T10:00:00")
                    self.assertEqual(cached["end_local"], self.day if mixed else self.day + "T16:00:00")

    def test_source_flag_reaches_listing_and_detail(self):
        config = json.loads(Path("jsonld_sources.json").read_text(encoding="utf-8"))
        site = next(s for s in config["sites"] if s.get("listing") == [LISTING])
        self.assertEqual(site["description_hours"], "dortmund_schedule")
        for detail in (False, True):
            with self.subTest(detail=detail), tempfile.TemporaryDirectory() as folder:
                store = EventStore(str(Path(folder) / "events.json"))
                body = ('<a href="' + URL + '">Stricken</a>') if detail else page(self.item)
                responses = [SimpleNamespace(text=body, raise_for_status=lambda: None)]
                if detail:
                    responses.append(SimpleNamespace(text=page(self.item), raise_for_status=lambda: None))
                else:
                    responses.append(SimpleNamespace(text="", raise_for_status=lambda: None))
                with patch.object(jl, "_ask", side_effect=responses):
                    jl.ingest_site(store, None, site)
                self.assertEqual(len(store.records), 1)
                record = next(iter(store.records.values()))
                self.assertEqual((record["start_local"], record["end_local"]),
                                 (self.day + "T10:00:00", self.day + "T16:00:00"))

    def test_later_explicit_publisher_clocks_replace_previous_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(str(Path(folder) / "events.json"))
            jl.store_item(store, self.item, URL, "community", None, VENUE, None,
                          {"description_hours": "dortmund_schedule"}, {})
            self.item.update(startDate=self.day + "T11:00:00", endDate=self.day + "T17:00:00")
            jl.store_item(store, self.item, URL, "community", None, VENUE, None,
                          {"description_hours": "dortmund_schedule"}, {})
            cached = next(iter(store.records.values()))
            self.assertEqual((cached["start_local"], cached["end_local"]),
                             (self.day + "T11:00:00", self.day + "T17:00:00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
