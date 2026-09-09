#!/usr/bin/env python3
"""Offline contract checks for embedded event agendas."""
import json
import tempfile
from unittest.mock import patch
from pathlib import Path

from mapsee_ingest import EventStore, NormalizedEvent
from mapsee_supabase_sync import _norm_cmp, to_row, upsert


def event(agenda=None, agenda_tz=None):
    return NormalizedEvent(
        source="festival", source_id="show-1", name="Festival", fingerprint="fp-1",
        start_utc="2026-09-08T10:00:00Z", end_utc="2026-09-08T20:00:00Z",
        latitude=47.6, longitude=-122.3, agenda=agenda, agenda_tz=agenda_tz,
    )


def item(at="2026-09-08T11:00:00Z"):
    return {"title": "Opening", "at": at, "place": "Main stage"}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(str(Path(tmp) / "events.json"))
        first = event([item()], "UTC")
        assert store.upsert(first) == "added"
        assert store.records["fp-1"]["agenda"][0]["at"] == "2026-09-08T11:00:00Z"
        row = to_row(first.as_record("now"), "host")
        assert row["agenda"] == first.agenda and row["agenda_tz"] == "UTC"

        # An adapter with no agenda leaves the source's programme untouched.
        store.upsert(event())
        assert len(store.records["fp-1"]["agenda"]) == 1
        # An explicit empty refresh removes it.
        store.upsert(event([], "UTC"))
        assert store.records["fp-1"]["agenda"] == []
        cleared = to_row(event([], "UTC").as_record("now"), "host")
        assert cleared["agenda"] is None and cleared["agenda_tz"] is None
        assert to_row(event().as_record("now"), "host").get("agenda") is None

    # Offset and UTC spellings compare equal for skip-unchanged.
    assert _norm_cmp("agenda", [{"id": "x", "title": "T", "at": "2026-09-08T11:00:00Z"}]) == _norm_cmp(
        "agenda", [{"id": "x", "title": "T", "at": "2026-09-08T04:00:00-07:00"}])
    # The fallback identity survives a corrected time, and nullable optional
    # keys remain omitted so the DB response has the same JSON shape.
    a = event([item("2026-09-08T11:00:00Z")], "UTC").agenda[0]
    b = event([item("2026-09-08T12:00:00Z")], "UTC").agenda[0]
    assert a["id"] == b["id"]
    assert a["until"] is None and a["emoji"] is None and a["url"] is None
    assert _norm_cmp("agenda", [a]) == _norm_cmp("agenda", json.loads(json.dumps([a])))
    equal_end = event([dict(item(), until="2026-09-08T11:00:00Z")], "UTC").agenda[0]
    assert equal_end["until"] is None
    ordered = event([dict(item("2026-09-08T12:00:00Z"), id="late"),
                     dict(item("2026-09-08T11:00:00Z"), id="early")], "UTC").agenda
    assert [x["at"] for x in ordered] == ["2026-09-08T11:00:00Z", "2026-09-08T12:00:00Z"]

    # Every requests payload in a mixed batch has one shape, and omitted agenda
    # is never serialized as JSON null by the batching layer.
    class Response:
        status_code = 201
        text = ""
        def json(self): return []
    payloads = []
    def post(_endpoint, **kwargs):
        payloads.append(json.loads(kwargs["data"]))
        return Response()
    plain = to_row(event().as_record("now"), "host")
    with patch("requests.post", side_effect=post):
        assert upsert([plain, to_row(event([item()], "UTC").as_record("now"), "host")], "https://db", "k") == (2, 0, 0)
    assert len(payloads) == 2
    assert all(("agenda" not in row) or row["agenda"] is not None for batch in payloads for row in batch)
    for bad in ([item()] * 61, [dict(item(), at="2026-09-08T09:00:00Z")],
                [dict(item(), url="javascript:alert(1)")],
                [dict(item(), title="x" * 121)]):
        try:
            event(bad, "UTC")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid agenda accepted")
    print("agenda sync: 5 checks passed")


if __name__ == "__main__":
    main()
