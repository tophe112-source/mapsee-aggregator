"""Bounded feed runs advance through the catalog and resume a partial source."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapsee_ingest_ics as ics
import mapsee_ingest_jsonld as jsonld


class Store:
    def __init__(self, _path):
        self.records = {}
        self.saves = 0

    def save(self):
        self.saves += 1


def run_case(module, sources, key, config_name, ingest_name, cache_patches):
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp, config_name)
        config.write_text(json.dumps(sources), encoding="utf-8")
        cursor = Path(tmp, "cursor.json")
        store = Store("unused")
        clock = [0]
        seen = []

        def ingest(_store, _session, src, **kwargs):
            seen.append((key(src), kwargs["start_offset"], kwargs.get("start_kept", 0)))
            clock[0] += 4  # three-second budget: one source per invocation
            return 1

        with patch.object(module, "EventStore", return_value=store), \
             patch.object(module.requests, "Session", create=True), \
             patch.object(module.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(module, ingest_name, side_effect=ingest), \
             cache_patches():
            for expected in range(3):
                clock[0] = 0
                assert module.main(["--config", str(config), "--store", str(Path(tmp, "events.json")),
                                    "--cursor", str(cursor), "--max-minutes", "0.05"]) == 0
                assert seen[-1] == (key(sources[expected] if isinstance(sources, list)
                                         else sources["sites"][expected]), 0, 0)
            assert store.saves >= 3

            # A long source stops after a partial read. Its event/page offset is
            # retained and the NEXT run starts there, not at the first row again.
            first = sources[0] if isinstance(sources, list) else sources["sites"][0]
            module._save_cursor(str(cursor), key(first))
            with patch.object(module, ingest_name, side_effect=module.BudgetExpired(7)):
                clock[0] = 0
                assert module.main(["--config", str(config), "--store", str(Path(tmp, "events.json")),
                                    "--cursor", str(cursor), "--max-minutes", "0.05"]) == 0
            assert json.loads(cursor.read_text(encoding="utf-8"))["offset"] == 7
            clock[0] = 0
            assert module.main(["--config", str(config), "--store", str(Path(tmp, "events.json")),
                                "--cursor", str(cursor), "--max-minutes", "0.05"]) == 0
            assert seen[-1] == (key(first), 7, 0)

            # A removed catalog source must not donate its saved offset or kept
            # count to the first current source.
            module._save_cursor(str(cursor), "removed-source", 7)
            clock[0] = 0
            assert module.main(["--config", str(config), "--store", str(Path(tmp, "events.json")),
                                "--cursor", str(cursor), "--max-minutes", "0.05"]) == 0
            assert seen[-1] == (key(first), 0, 0)

            # Legacy/manual unbounded runs still walk the full catalog from its
            # first source and leave the budget cursor untouched.
            second = sources[1] if isinstance(sources, list) else sources["sites"][1]
            module._save_cursor(str(cursor), key(second), 7)
            before = cursor.read_text(encoding="utf-8")
            clock[0] = 0
            assert module.main(["--config", str(config), "--store", str(Path(tmp, "events.json")),
                                "--cursor", str(cursor)]) == 0
            current = sources if isinstance(sources, list) else sources["sites"]
            assert seen[-3:] == [(key(src), 0, 0) for src in current]
            assert cursor.read_text(encoding="utf-8") == before


from contextlib import ExitStack


def ics_caches():
    stack = ExitStack()
    stack.enter_context(patch.object(ics, "_save_geo_cache"))
    stack.enter_context(patch.object(ics, "_save_feed_cache"))
    return stack


def jsonld_caches():
    stack = ExitStack()
    stack.enter_context(patch.object(jsonld, "_save_geo_cache"))
    return stack


run_case(ics,
         [{"name": n, "url": f"https://example.test/{n}.ics"} for n in "abc"],
         lambda src: src["url"], "ics.json", "ingest_ics", ics_caches)
run_case(jsonld,
         {"sites": [{"name": n, "listing": [f"https://example.test/{n}"]} for n in "abc"]},
         jsonld._site_key, "jsonld.json", "ingest_site", jsonld_caches)

# Exercise the actual inner loops: a source can exhaust the budget after some
# events/pages, so its offset must identify the first item not yet attempted.
with patch.object(ics, "_fetch_ics", return_value=("", "200")), \
     patch.object(ics, "parse_ics", return_value=[{}, {}, {}]), \
     patch.object(ics.time, "monotonic", side_effect=[0, 4]):
    try:
        ics.ingest_ics(Store("unused"), None, {"name": "a", "url": "x"}, deadline=3)
        raise AssertionError("ICS source did not stop at the budget")
    except ics.BudgetExpired as exc:
        assert exc.offset == 1


class EventStore(Store):
    def __init__(self, path):
        super().__init__(path)
        self.rows = []

    def upsert(self, event):
        self.rows.append(event)


rows = EventStore("unused")
event = {"SUMMARY": ("Future gathering", {}), "DTSTART": ("20990101", {"VALUE": "DATE"}),
         "GEO": ("47.6;-122.3", {}), "UID": ("one", {})}
future_events = [event, dict(event, UID=("two", {})), dict(event, UID=("three", {}))]
with patch.object(ics, "_fetch_ics", return_value=("", "200")), \
     patch.object(ics, "parse_ics", return_value=future_events), \
     patch.object(ics.time, "monotonic", side_effect=[0, 4]):
    try:
        ics.ingest_ics(rows, None, {"name": "a", "url": "x", "limit": 2}, deadline=3)
        raise AssertionError("ICS source did not stop with one event kept")
    except ics.BudgetExpired as exc:
        assert (exc.offset, exc.kept) == (1, 1)
with patch.object(ics, "_fetch_ics", return_value=("", "200")), \
     patch.object(ics, "parse_ics", return_value=future_events):
    assert ics.ingest_ics(rows, None, {"name": "a", "url": "x", "limit": 2},
                          start_offset=1, start_kept=1) == 1
assert len(rows.rows) == 2


class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class Session:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return Response('<a href="/one">one</a><a href="/two">two</a>' if url.endswith('/listing') else '')


site = {"name": "venue", "listing": ["https://example.test/listing"],
        "link_pattern": '/one|/two'}
session = Session()
with patch.object(jsonld.time, "monotonic", side_effect=[0, 0, 4]), \
     patch.object(jsonld.time, "sleep"):
    try:
        jsonld.ingest_site(Store("unused"), session, site, deadline=3)
        raise AssertionError("JSON-LD site did not stop at the budget")
    except jsonld.BudgetExpired as exc:
        assert exc.offset == 1
assert session.urls == ["https://example.test/listing", "https://example.test/one"]
session = Session()
with patch.object(jsonld.time, "sleep"):
    jsonld.ingest_site(Store("unused"), session, site, start_offset=1)
assert session.urls == ["https://example.test/listing", "https://example.test/two"]


class InlineSession(Session):
    def get(self, url, timeout):
        self.urls.append(url)
        return Response('<script type="application/ld+json">' +
                        json.dumps([{"@type": "Event", "name": n} for n in "abc"]) +
                        '</script>')


inline = InlineSession()
inline_rows = EventStore("unused")
clock = [0]


def make_event(*_args):
    clock[0] = 4
    return object()


with patch.object(jsonld.time, "monotonic", side_effect=lambda: clock[0]), \
     patch.object(jsonld.time, "sleep"), \
     patch.object(jsonld, "to_event", side_effect=make_event):
    try:
        jsonld.ingest_site(inline_rows, inline, {"name": "inline", "listing": ["https://example.test/inline"]},
                           deadline=3)
        raise AssertionError("inline JSON-LD events did not stop at the budget")
    except jsonld.BudgetExpired as exc:
        assert (exc.offset, exc.inline_offset) == (0, 1)
with patch.object(jsonld.time, "sleep"), patch.object(jsonld, "to_event", return_value=object()):
    jsonld.ingest_site(inline_rows, inline, {"name": "inline", "listing": ["https://example.test/inline"]},
                       start_inline=1)
assert len(inline_rows.rows) == 3
print("bounded ICS and JSON-LD resume checks passed")
