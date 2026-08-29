#!/usr/bin/env python3
"""
test_sync_unknown_column.py — a column the database has not got yet must cost
one feature, not the whole night.

THE FAILURE THIS PREVENTS. to_row writes every column for every adapter, so a
repo whose migration CODE has merged before the migration has been run against
Supabase does not lose the feature that needed the column — it loses every row
from all thirty-seven adapters. A 400 is not retryable, so each batch of 50
falls straight through to row-by-row isolation, every one of those rows fails
identically, and the run writes ZERO having made fifty times the requests.

The ordering that causes it is between two systems nothing coordinates: a git
merge and a hand-run `supabase db push`.

Run: python test_sync_unknown_column.py
"""
import json
import sys
import types

import mapsee_supabase_sync as S


class Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


PGRST204 = {"code": "PGRST204",
            "message": "Could not find the 'pin_only' column of 'events' in the schema cache"}


def fake_requests(known_columns):
    """A PostgREST that rejects any row naming a column it does not have."""
    calls = {"posts": 0, "rows": 0, "seen": []}

    def post(url, headers=None, data=None, timeout=None):
        calls["posts"] += 1
        batch = json.loads(data)
        calls["rows"] += len(batch)
        calls["seen"].append(batch)
        for row in batch:
            for column in row:
                if column not in known_columns:
                    return Resp(400, dict(PGRST204,
                                          message=f"Could not find the '{column}' column "
                                                  f"of 'events' in the schema cache"))
        return Resp(201)

    mod = types.ModuleType("requests")
    mod.post = post
    return mod, calls


def main():
    checks = []
    rows = [{"title": f"row {i}", "lat": 1.0, "lon": 2.0, "pin_only": False}
            for i in range(120)]

    # ---- 1. THE OLD DATABASE. pin_only does not exist yet.
    known = {"title", "lat", "lon"}
    fake, calls = fake_requests(known)
    sys.modules["requests"] = fake
    sent, skipped, _lost = S.upsert([dict(r) for r in rows], "https://x.test", "k")
    checks.append((sent == 120 and skipped == 0,
                   "every row still lands when a column is missing "
                   f"(sent={sent}, skipped={skipped})"))
    # 3 batches of 50/50/20 plus ONE rejected probe = 4. The old code would have
    # made 3 + 120 requests and written nothing.
    checks.append((calls["posts"] <= 6,
                   f"...and it costs one wasted request, not 120 ({calls['posts']} posts)"))
    checks.append((all("pin_only" not in row for batch in calls["seen"][1:] for row in batch),
                   "...the unknown column is dropped from every LATER batch, not re-probed"))

    # ---- 2. THE MIGRATED DATABASE. Nothing is dropped, nothing is wasted.
    fake, calls = fake_requests(known | {"pin_only"})
    sys.modules["requests"] = fake
    sent, skipped, _lost = S.upsert([dict(r) for r in rows], "https://x.test", "k")
    checks.append((sent == 120 and skipped == 0 and calls["posts"] == 3,
                   f"a database that HAS the column pays nothing for this "
                   f"({calls['posts']} posts)"))
    checks.append((all("pin_only" in row for batch in calls["seen"] for row in batch),
                   "...and the column is actually written"))

    # ---- 3. A REAL BAD ROW still gets isolated, and is not mistaken for this.
    # The whole point of the row-by-row fallback is one poisoned row among 50;
    # dropping a column must not swallow that.
    def post_blocked(url, headers=None, data=None, timeout=None):
        batch = json.loads(data)
        if any("BLOCKED" in (r.get("title") or "") for r in batch):
            return Resp(400, {"code": "P0001", "message": "content_blocked"})
        return Resp(201)
    sys.modules["requests"] = types.SimpleNamespace(post=post_blocked)
    mixed = [{"title": "ok"}, {"title": "BLOCKED"}, {"title": "fine"}]
    sent, skipped, _lost = S.upsert(mixed, "https://x.test", "k")
    checks.append((sent == 2 and skipped == 1,
                   f"a genuinely bad row is still isolated and skipped "
                   f"(sent={sent}, skipped={skipped})"))

    # ---- 4. The parser reads PostgREST, not a guess.
    checks.append((S._unknown_column(Resp(400, PGRST204)) == "pin_only",
                   "the missing column name is read out of PostgREST's own message"))
    checks.append((S._unknown_column(Resp(400, {"message": "content_blocked"})) is None,
                   "...and an unrelated 400 names no column"))
    checks.append((S._unknown_column(Resp(500, {})) is None,
                   "...and neither does an empty body"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
