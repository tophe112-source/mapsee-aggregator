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
run stalls; do not retry the 503 and a provider blip is a red build.

THEN WHAT THE JOB MAY TOUCH. No standing row goes by age, hidden or not: a live
one is the roller's to bring back, and a hidden one is a tombstone whose delete
lets the next sync re-insert it visible. The same `recurring_hours=is.null` is
what keeps the candidate fetch off the 465,926 pins 0239 froze, which stopped
the 2026-10-26 run at 20,410 deletions with "Even 10 ids time out on fetch".
The last case runs the job against a small table and lets the URL's own filter
decide what goes. Run:

    python test_cleanup.py
"""
import sys
import types
from contextlib import redirect_stdout
from io import StringIO

import mapsee_cleanup as C

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


class Resp:
    def __init__(self, code, text="", *, headers=None, body=None):
        self.status_code, self.text = code, text
        self.headers = headers or {}
        self.body = body

    def json(self):
        return self.body


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


def run_main(dry_run=False, fetch_timeouts=0, count_failure=False):
    """Run main with scripted PostgREST replies and return calls/output/result."""
    calls = []
    timeouts_left = fetch_timeouts
    pages = [
        Resp(200, headers={}, body=[{"id": "event-a"}, {"id": "event-b"}]),
        Resp(200, headers={}, body=[]),
    ]

    def request(method, url, **kw):
        nonlocal timeouts_left
        calls.append((method, url, kw))
        if method == "DELETE":
            return Resp(204, headers={"Content-Range": "0-1/2"})
        if dry_run:
            if count_failure:
                return Resp(503, "count service unavailable")
            return Resp(200, headers={"Content-Range": "0-0/estimate"}, body=[])
        if timeouts_left:
            timeouts_left -= 1
            return Resp(500, TIMEOUT_BODY)
        return pages.pop(0)

    return drive_main(request, calls, dry_run)


def drive_main(request, calls, dry_run=False):
    """Run main with `request` standing in for _req; returns the `calls` that
    request recorded, main's stdout, and its result (or the exception)."""
    real_req, real_time = C._req, C.time
    real_env = dict(C.os.environ)
    real_argv = sys.argv
    C._req = request
    C.time = types.SimpleNamespace(monotonic=lambda: 0, sleep=lambda _s: None)
    C.os.environ["SUPABASE_URL"] = "https://example.test"
    C.os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
    sys.argv = ["mapsee_cleanup.py"] + (["--dry-run"] if dry_run else [])
    out = StringIO()
    try:
        with redirect_stdout(out):
            try:
                result = C.main()
            except BaseException as ex:
                result = ex
        return calls, out.getvalue(), result
    finally:
        C._req, C.time, sys.argv = real_req, real_time, real_argv
        C.os.environ.clear()
        C.os.environ.update(real_env)


calls, output, result = run_main()
get_calls = [call for call in calls if call[0] == "GET"]
check("apply starts with bounded candidate fetches, without a count request",
      result == 0 and len(calls) == 3 and len(get_calls) == 2
      and all("Range-Unit" not in call[2].get("headers", {}) for call in calls), calls)
candidate_url = get_calls[0][1]
delete_url = next(call[1] for call in calls if call[0] == "DELETE")
# ORDERED BY starts_at ALONE, BEFORE THE LIMIT. Without an ORDER a LIMIT may be
# answered by a sequential scan (a local model of the table did: ~193k buffers);
# an id tiebreak brings back the Incremental Sort the 2026-10-10 live EXPLAIN
# measured. And `&limit=` stays last, because the halving case below reads the
# batch size off the end of the URL.
check("candidate pages are ordered by starts_at alone, before a bounded limit",
      "&order=starts_at.asc&limit=100" in candidate_url and candidate_url.count("order=") == 1
      and "id.asc" not in candidate_url, candidate_url)
check("delete rechecks source, start cutoff and end-time eligibility",
      all(part in delete_url for part in (
          "external_source=eq.mapsee", "starts_at=lt.",
          "ends_at.is.null", "ends_at.lt.", "id=in.(event-a,event-b)")), delete_url)
check("candidate reads and deletes spare EVERY standing row (a plain recurring_hours=is.null)",
      all("&recurring_hours=is.null" in u for u in (candidate_url, delete_url)),
      (candidate_url, delete_url))
check("...with no hidden_at arm: a hidden standing row is a tombstone, not a candidate",
      all("hidden_at" not in u for u in (candidate_url, delete_url)),
      (candidate_url, delete_url))
check("apply reports actual deletes without an eligible-count claim",
      "Deleted 2 past aggregator events." in output and "eligible" not in output, output)

calls, output, result = run_main(dry_run=True)
check("dry run uses a planned estimate and returns without listing or deleting",
      result == 0 and len(calls) == 1 and calls[0][0] == "GET"
      and calls[0][2]["headers"]["Prefer"] == "count=planned"
      and "~estimate" in output and "nothing deleted" in output.lower(), (calls, output))

calls, output, result = run_main(dry_run=True, count_failure=True)
check("a failed dry-run count exits visibly instead of reporting a successful dry run",
      isinstance(result, SystemExit) and "Couldn't count matching events [503]" in str(result)
      and "Dry run" not in output and len(calls) == 1, (result, output, calls))

calls, output, result = run_main(fetch_timeouts=5)
limits = [call[1].split("&limit=")[-1] for call in calls]
check("fetch timeouts halve the limit to the minimum and remain a visible failure",
      isinstance(result, SystemExit) and limits == ["100", "50", "25", "12", "10"]
      and "exists and is valid" in str(result) and "apply migration" not in str(result),
      (limits, result))
check("...and the message names the index this read needs (0241) and the one it falls back to (0113)",
      "events_cleanup_dated_idx" in str(result) and "0241" in str(result)
      and "events_aggregator_cleanup_idx" in str(result), result)


# --------------------------------------------------------------------------- #
# THE FILTER, EVALUATED. The checks above read the URL's text; this one runs the
# job against a small table and lets the URL's own filter decide what goes, so
# a clause that reads right and selects wrong is caught too. Supports exactly
# the PostgREST syntax the job sends and refuses anything else, so a filter
# this cannot read fails here instead of passing by default.
def _split_top(s):
    """Split on commas that are not inside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    out.append(cur)
    return out


def _cond(row, col, op):
    """One PostgREST condition `col` + `op` (`is.null`, `lt.X`, `in.(a,b)` ...)."""
    if op.startswith("not."):
        return not _cond(row, col, op[4:])
    v = row.get(col)
    if op == "is.null":
        return v is None
    if op.startswith("eq."):
        return v == op[3:]
    if op.startswith("lt."):
        return v is not None and v < op[3:]
    if op.startswith("in.(") and op.endswith(")"):
        return v in op[4:-1].split(",")
    raise AssertionError(f"the table fake cannot read {col}={op}")


def _logic(row, kind, body):
    """`or(a,b)` / `and(a,b)` bodies, nested ones included."""
    assert body.startswith("(") and body.endswith(")"), body
    parts = []
    for p in _split_top(body[1:-1]):
        if p.startswith(("or(", "and(")):
            k, rest = p.split("(", 1)
            parts.append(_logic(row, k, "(" + rest))
        else:
            col, op = p.split(".", 1)
            parts.append(_cond(row, col, op))
    return any(parts) if kind == "or" else all(parts)


def _matches(row, query):
    for param in query.split("&"):
        key, val = param.split("=", 1)
        if key in ("select", "order", "limit"):
            continue
        if key in ("or", "and"):
            if not _logic(row, key, val):
                return False
        elif not _cond(row, key, val):
            return False
    return True


def run_table(rows):
    """Run main against `rows`, honouring each request's own filter, order and limit."""
    table = [dict(r) for r in rows]
    calls = []

    def request(method, url, **kw):
        calls.append((method, url, kw))
        query = url.split("?", 1)[1]
        hit = [r for r in table if _matches(r, query)]
        if method == "DELETE":
            for r in hit:
                table.remove(r)
            return Resp(204, headers={"Content-Range": f"*/{len(hit)}"})
        if "order=starts_at.asc" in query:
            hit.sort(key=lambda r: r["starts_at"])
        limit = int(query.split("&limit=")[-1]) if "&limit=" in query else len(hit)
        return Resp(200, headers={}, body=[{"id": r["id"]} for r in hit[:limit]])

    calls, output, result = drive_main(request, calls)
    return {r["id"] for r in table}, calls, output, result


PAST, PAST_END, FUTURE = "2020-01-01T10:00:00Z", "2020-01-01T12:00:00Z", "2999-01-01T00:00:00Z"
HOURS = {"mon": [["09:00", "17:00"]]}
HIDDEN = "2026-10-01T00:00:00Z"
rows = [
    # ids say what each row is; recurring_hours set = a standing row
    dict(id="past-dated", external_source="mapsee", starts_at=PAST, ends_at=PAST_END,
         recurring_hours=None, hidden_at=None),
    dict(id="past-dated-hidden", external_source="mapsee", starts_at=PAST, ends_at=PAST_END,
         recurring_hours=None, hidden_at=HIDDEN),
    dict(id="standing-live-stale", external_source="mapsee", starts_at=PAST, ends_at=PAST_END,
         recurring_hours=HOURS, hidden_at=None),
    dict(id="standing-hidden-stale", external_source="mapsee", starts_at=PAST, ends_at=PAST_END,
         recurring_hours=HOURS, hidden_at=HIDDEN),
    dict(id="still-running", external_source="mapsee", starts_at=PAST, ends_at=FUTURE,
         recurring_hours=None, hidden_at=None),
    dict(id="community-past", external_source=None, starts_at=PAST, ends_at=PAST_END,
         recurring_hours=None, hidden_at=None),
]
left, calls, output, result = run_table(rows)
deletes = [call[1] for call in calls if call[0] == "DELETE"]
check("a hidden standing row (recurring_hours AND hidden_at set) is never in a delete's scope",
      "standing-hidden-stale" in left
      and all("standing-hidden-stale" not in u for u in deletes), (left, deletes))
check("...nor is a live standing row the roller has not reached yet",
      "standing-live-stale" in left, left)
check("...while a past dated row goes, hidden or not (the retire scripts' promise)",
      result == 0 and "past-dated" not in left and "past-dated-hidden" not in left
      and "Nothing left to delete." in output, (left, result, output))
check("...and a running event and a community event are untouched",
      {"still-running", "community-past"} <= left, left)

# The safety rail this file must never lose: the delete is scoped, always.
import inspect
src = inspect.getsource(C.main)
check("the delete is still refused unless it is scoped to imported, past events",
      'assert "external_source=eq.mapsee" in flt and "starts_at=lt." in flt' in src)
check("the delete is still refused if it could reach a standing row",
      'assert "&recurring_hours=is.null" in flt' in src)
check("the delete is still refused if a hidden_at arm comes back (a hidden standing row is a tombstone)",
      'assert "hidden_at" not in flt' in src)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
