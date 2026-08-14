#!/usr/bin/env python3
"""
test_cleanup.py — two 5xx arrive at the janitor and they want opposite things.

A STATEMENT TIMEOUT (Postgres 57014) means we asked for too much. A dozen tables
carry `on delete cascade` on events, so a batch that is too big cannot be waited
out — the fix is a SMALLER bite, and the caller halves the batch. Retrying the
identical request would burn the run's time budget doing the thing that already
failed, and at BATCH_MIN it would hide the message that says to go and look at
the server.

AN UPSTREAM 503 means we asked for nothing at all. "upstream connect error or
disconnect/reset before headers" is Envoy failing to reach Postgres; the same
request works once the edge recovers. On 2026-08-14 an hour-long Supabase outage
took this job out with "Couldn't count matching events [503]" and exit 1, on
work that is idempotent, scheduled daily and would have succeeded seconds later.

Both statuses are >= 500, so telling them apart is the whole job of _req, and
getting it backwards is silent in either direction: retry the timeout and the
run stalls; do not retry the 503 and a provider blip is a red build. Run:

    python test_cleanup.py
"""
import sys
import types

import mapsee_cleanup as C

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


class Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


TIMEOUT_BODY = '{"code":"57014","message":"canceling statement due to statement timeout"}'
OUTAGE_BODY = "upstream connect error or disconnect/reset before headers"


def run(responses):
    """Drive _req with a scripted transport; returns (response|exc, call count)."""
    calls = []

    def transport(method, url, **kw):
        calls.append(method)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    real_requests, real_time = C.requests, C.time
    C.requests = types.SimpleNamespace(request=transport, RequestException=Exception)
    C.time = types.SimpleNamespace(sleep=lambda _s: None)          # no real waiting
    try:
        try:
            return C._req("GET", "https://example.test/rest/v1/events"), len(calls)
        except BaseException as ex:                                 # SystemExit included
            return ex, len(calls)
    finally:
        C.requests, C.time = real_requests, real_time


# --------------------------------------------------------------------------- #
r, n = run([Resp(503, OUTAGE_BODY), Resp(503, OUTAGE_BODY), Resp(200, "[]")])
check("a transient 503 is retried until it answers",
      getattr(r, "status_code", None) == 200 and n == 3, (r, n))

r, n = run([Resp(500, TIMEOUT_BODY)])
check("a statement timeout comes straight back, so the caller can halve the batch",
      getattr(r, "status_code", None) == 500 and n == 1, (r, n))
check("...and it is still recognised as a timeout downstream", C._timed_out(Resp(500, TIMEOUT_BODY)))

r, n = run([Resp(503, OUTAGE_BODY)])
check(f"a sustained outage returns the last response after {C.ATTEMPTS} tries, "
      f"for the caller to report",
      getattr(r, "status_code", None) == 503 and n == C.ATTEMPTS, (r, n))

r, n = run([Resp(404, "no such table")])
check("a 4xx is a settled answer and is not retried",
      getattr(r, "status_code", None) == 404 and n == 1, (r, n))

# A connection that never completes has no response to hand back, so this is the
# one path that ends the run itself — with a sentence, not a traceback.
r, n = run([ConnectionError("connection reset by peer")])
check("a transport that never answers exits with an explanation",
      isinstance(r, SystemExit) and "unreachable" in str(r) and n == C.ATTEMPTS, (r, n))
check("...and says the work is safe to retry", isinstance(r, SystemExit) and "Nothing was deleted" in str(r), r)

# The safety rail this file must never lose: the delete is scoped, always.
import inspect
src = inspect.getsource(C.main)
check("the delete is still refused unless it is scoped to imported, past events",
      'assert "external_source=eq.mapsee" in flt and "starts_at=lt." in flt' in src)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
