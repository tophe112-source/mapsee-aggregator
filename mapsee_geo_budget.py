"""Per-RUN cap on NEW Photon geocodes, shared across the feed adapters.

All feed ingesters share geocode_cache.json, so a warm cache is nearly free. But
a cold cache (or a big new batch of coord-less venues) can need thousands of
1.1s Photon lookups - more than one GitHub Actions job's timeout. When the job
dies at the timeout, actions/cache never uploads the partially-warmed cache, so
the NEXT run re-does the same work and times out again: a cache that can never
catch up.

This caps NEW lookups per RUN (env GEOCODE_MAX_NEW). Adapters skip venues past
the cap THIS run (leaving them un-geocoded, NOT cached as unfindable, so they're
retried next run), the job finishes comfortably, actions/cache DOES upload the
freshly-warmed cache, and the backlog drains over the next few runs.

The counter lives in a small file so it's shared across the separate adapter
processes in a job. It's NOT in the cache path, so every run starts at 0.
GEOCODE_MAX_NEW unset/0 = unlimited (local dev, or once the cache is warm).
"""
import json
import os

_MAX = int(os.environ.get("GEOCODE_MAX_NEW", "0") or "0")     # 0 = unlimited
_FILE = os.environ.get("GEOCODE_BUDGET_FILE", "geocode_budget.json")


def geocode_allowed() -> bool:
    """True if another NEW Photon lookup fits this run's budget (and counts it).
    Call ONLY on a cache miss, right before the network hit."""
    if _MAX <= 0:
        return True
    try:
        n = int(json.load(open(_FILE)).get("n", 0))
    except Exception:
        n = 0
    if n >= _MAX:
        return False
    try:
        json.dump({"n": n + 1}, open(_FILE, "w"))
    except Exception:
        pass
    return True
