"""Focused regression checks for the ICS importer's durable checkpoints."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mapsee_ingest_ics as ICS


fails = []


def check(label, condition, detail=""):
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else '   ' + str(detail)}")
    if not condition:
        fails.append(label)


class Store:
    def __init__(self, _path):
        self.records = {}
        self.saves = 0

    def save(self):
        self.saves += 1


sources = [
    {"name": "good", "url": "https://example.test/good.ics"},
    {"name": "bad", "url": "https://example.test/bad.ics"},
]
store = Store("unused.json")
cache_saves = []


def ingest(_store, _session, src):
    if src["name"] == "bad":
        raise RuntimeError("feed refused")
    return 3


with patch.object(ICS.json, "loads", return_value=sources), \
     patch.object(ICS.requests, "Session"), \
     patch.object(ICS, "EventStore", return_value=store), \
     patch.object(ICS, "ingest_ics", side_effect=ingest), \
     patch.object(ICS, "_save_geo_cache", side_effect=lambda _cache: cache_saves.append("geo")), \
     patch.object(ICS, "_save_feed_cache", side_effect=lambda _cache: cache_saves.append("feed")), \
     patch("builtins.open"):
    rc = ICS.main(["--config", "unused.json", "--store", "unused-store.json"])

check("main succeeds when one source fails", rc == 0, rc)
check("event store checkpoints after every attempted source", store.saves == 2, store.saves)
check("both caches checkpoint after every attempted source",
      cache_saves == ["geo", "feed", "geo", "feed"], cache_saves)


if fails:
    raise SystemExit(f"{len(fails)} ICS test(s) failed: {', '.join(fails)}")
print("all ICS tests passed")
