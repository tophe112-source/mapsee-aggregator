#!/usr/bin/env python3
"""
mapsee_standing.py — the one way a walker reads the listed standing rows.

WHY. A standing row (recurring_hours set, not a pin: a shop, a centre, a
weekly class) used to be reachable by a walker windowed on starts_at only
because ../mapsee's roller kept rewriting its stored window to the current
one, about 125-150k events UPDATEs a day and the database's largest writer
(3.2 GB of WAL a day, measured 2026-10-21..10-27). R1 stops that: from
../mapsee 0248 the roller writes only events_map, and a standing row's stored
starts_at/ends_at stay at the last window the sync wrote (its ANCHOR), days or
weeks behind. A walker that finds rows by `starts_at=gte.<now>` then never
sees a standing row again.

So every walker that must see standing rows reads them here, by id:

    events?select=<cols>&recurring_hours=not.is.null&hidden_at=is.null
          &pin_only=is.false<scope>&order=id.asc&limit=<page>
          [&id=gte.<lo> on the first page | &id=gt.<last id> after it][&id=lt.<hi>]

That predicate is ../mapsee 0247's events_standing_walk_idx, spelled the way
PostgREST must send it for the planner to prove the partial index:
`pin_only=is.false` (the index says `pin_only is false`), never
`pin_only=eq.false`, and `hidden_at=is.null`. Measured in the R1 capacity
verdict: about 1,005 buffers per 1,000 rows on that index against 8,845-9,014
without it, so a full pass over ~188k listed standing rows is ~189k buffers:
fine for a dispatch job or a 1/14 slice a run, never a daily full pass. And
re-measured 2026-10-28 for every walker's own request as PostgREST renders it
(`NOT recurring_hours IS NULL`, `IS NULL`, `IS false`, every value a
parameter, a generic plan forced; PG16, 150k rows, 41,250 listed standing):
each full walk an Index Scan on events_standing_walk_idx at 1,106-1,152
buffers per 1,000 rows, a 1/14 slice and a 100-row week slice a Bitmap Index
Scan on it. With `pin_only=eq.false` (a parameter, so a generic plan cannot
prove the partial index) the same page walked events_pkey: 4,275 buffers for
1,000 rows against 1,152.

NEVER OFFSET, NEVER starts_at. OFFSET re-reads every row before it on every
page (the prune collect died at OFFSET 37.5k-49.5k every run 2026-10-20..26).
starts_at is the anchor, which is exactly what stopped meaning "now". A
hidden row is never in the walk (the predicate says hidden_at is null): no
walker reaches a take-down, a cancellation or an organizer's removal here.

Stdlib only, like the walkers that import it.
"""
import time
import uuid

STANDING = "recurring_hours=not.is.null&hidden_at=is.null&pin_only=is.false"   # == events_standing_walk_idx

RETRY_PAUSE = 1.5        # seconds before the second ask of a page, twice that before the third

# A scope narrows the walk; it may not window it, page it or re-state the walk's
# own predicate (a duplicate `hidden_at=` is how a `hidden_at=not.is.null` could
# ride in and walk the hidden rows).
_SCOPE_REFUSED = {"select", "order", "limit", "offset", "id", "starts_at", "ends_at",
                  "recurring_hours", "hidden_at", "pin_only"}


class StandingWalkError(RuntimeError):
    """A page failed after its retries, the cursor stopped moving, or max_pages
    ran out. `rows` is how many rows were yielded before it: a walker reports
    its read as incomplete rather than as the whole set."""

    def __init__(self, message, rows=0):
        super().__init__(message)
        self.rows = rows


def uuid_bound(k: int, n: int) -> str:
    """Lower id bound of slice k of n over the uuid space ('00000000-...' for k=0)."""
    if not (isinstance(k, int) and isinstance(n, int) and n >= 1 and 0 <= k < n):
        raise ValueError(f"slice {k!r} of {n!r}: need 0 <= k < n")
    return str(uuid.UUID(int=(k * (1 << 128)) // n))


def slice_bounds(k: int, n: int):
    """(lo, hi) for slice k of n: lo inclusive, hi exclusive, hi None for the
    last slice (so slice n-1 reaches ffffffff-... with no id=lt.)."""
    return uuid_bound(k, n), (uuid_bound(k + 1, n) if k + 1 < n else None)


def parse_slice(text):
    """'K/N' -> (K, N) with 0 <= K < N. For argparse's type=."""
    try:
        a, b = str(text).split("/")
        k, n = int(a), int(b)
        uuid_bound(k, n)
    except (ValueError, TypeError):
        raise ValueError(f"expected K/N with 0 <= K < N, got {text!r}") from None
    return k, n


def _check_scope(scope: str):
    if not scope:
        return
    if not scope.startswith("&"):
        raise ValueError(f"scope must begin with '&': {scope!r}")
    for part in scope[1:].split("&"):
        key, _, value = part.partition("=")
        if not key or not value:
            raise ValueError(f"scope filter is not key=value: {part!r}")
        if key in _SCOPE_REFUSED or "starts_at" in value or "ends_at" in value:
            raise ValueError(f"scope may not page, window or re-state the walk: {part!r}")


def standing_pages(sb, select: str, scope: str = "", page: int = 1000,
                   lo: str | None = None, hi: str | None = None, max_pages: int = 400,
                   *, attempts: int = 3, sleep=None):
    """Yield pages of listed standing rows in id order: a keyset over ../mapsee
    0247's events_standing_walk_idx. Never OFFSET, never starts_at. `scope` is
    the walker's own filters, each beginning with '&'. lo is inclusive
    (id=gte.), hi exclusive (id=lt.); continuation is id=gt.<last>.

    `sb(path)` is the walker's own REST call. A page that raises is asked again
    (`attempts` in all, backing off RETRY_PAUSE, then twice it); after that, or when the cursor
    does not move, or after `max_pages` full pages, StandingWalkError says how
    far the walk got. A short page ends the walk."""
    cols = [c.strip() for c in select.split(",")]
    if "id" not in cols:
        raise ValueError("select must include id: it is the cursor")
    if page < 1 or max_pages < 1:
        raise ValueError("page and max_pages must be positive")
    _check_scope(scope)
    base = f"events?select={select}&{STANDING}{scope}&order=id.asc&limit={page}"
    upper = f"&id=lt.{hi}" if hi else ""
    lower = f"&id=gte.{lo}" if lo else ""
    last, yielded = None, 0
    for n in range(1, max_pages + 1):
        url = base + lower + upper
        batch = None
        for attempt in range(1, attempts + 1):
            try:
                batch = sb(url)
                break
            except Exception as e:                                  # noqa: BLE001
                if attempt == attempts:
                    raise StandingWalkError(
                        f"standing page {n} failed {attempts}x ({type(e).__name__}: {str(e)[:160]})",
                        yielded) from e
                (sleep or time.sleep)(RETRY_PAUSE * attempt)
        if batch is None:
            batch = []
        if not isinstance(batch, list):
            raise StandingWalkError(f"standing page {n}: not a list ({type(batch).__name__})", yielded)
        if not batch:
            return
        yielded += len(batch)
        yield batch
        if len(batch) < page:
            return
        nxt = batch[-1].get("id")
        if not nxt or nxt == last:
            # A server ignoring the cursor would serve this page for ever.
            raise StandingWalkError(f"standing page {n} did not advance the cursor", yielded)
        last = nxt
        lower = f"&id=gt.{nxt}"
    raise StandingWalkError(f"standing walk stopped at max_pages={max_pages} ({yielded} rows)", yielded)
