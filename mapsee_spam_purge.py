#!/usr/bin/env python3
"""
mapsee_spam_purge.py — remove the advertisements that got in before the gate did.

    python mapsee_spam_purge.py                     # report only, the default
    python mapsee_spam_purge.py --apply
    python mapsee_spam_purge.py --apply --state spam_purge_state.json   # the schedule:
                                    # rows created since the last complete run; the
                                    # whole table weekly and when the rules change

mapsee_spam.py stops new ones at ingest. It reaches nothing already stored, for
the reason CLAUDE.md gives about classifier fixes: `--only-new` is the default in
CI, so a scheduled run can only ADD, and Wednesday's full run re-reads the
SOURCES rather than re-applying our own rules to rows we already hold. A row that
arrived in August is in the table until something goes and gets it.

So this is the backfill half, and it is the same shape as mapsee_reclassify.py:
one predicate, imported from the same module the ingest uses, applied to rows
that predate it. Not a second copy of the rules — a second copy would drift, and
the drift would be invisible because both would look right in isolation.

TWO ACTIONS, NOT ONE, and they follow the split in mapsee_spam.py exactly:

  * A row spam_reason() refuses is DELETED. It is an advertisement.
  * A row whose only problem is an end date years out has that END CLEARED, and
    keeps its place. That is not a verdict about the row (see the 487-day
    Extinction Rebellion listing in the module header), and clearing the end is
    what lets mapsee_cleanup.py reach it at all — its filter is `starts_at <
    cutoff AND (ends_at IS NULL OR ends_at < cutoff)`, so an end in 2036 is a pin
    nothing in this pipeline can ever remove. Most of what this touches then
    disappears on the next cleanup run without this script deleting anything.

WHY IT PAGES BY starts_at AND FILTERS IN PYTHON. The obvious implementation is a
`description=ilike.*…*` filter and letting Postgres do the work. Measured: that
is a sequential scan over the whole events table and it does not finish inside
the statement timeout — every attempt comes back 57014. The spam predicate is
also not expressible as a `like` anyway (it is three regexes and a date
subtraction). So the walk is over the INDEX migration 0113 already provides
(external_source, starts_at), a window at a time, and the judging happens here.

SAFETY, in three layers, because this deletes on a CONTENT judgement.

  * Scoped to `external_source = 'mapsee'` on every request, like
    mapsee_cleanup.py. Community events (external_source IS NULL) cannot be
    matched and therefore cannot be deleted.
  * `--apply` is required to write anything. Without it this reports and exits.
  * `--max-share` refuses to delete when the matched fraction is implausibly
    high. This is the one that makes it safe to SCHEDULE: the failure to fear is
    not a spam wave, it is a widened regex, and a widened regex looks exactly
    like a very successful run.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs 'requests'.  Install it with:  pip install requests")

from mapsee_spam import implausible_end, spam_reason

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# Same reasoning as mapsee_cleanup.BATCH_DEFAULT: ids ride back inside
# `id=in.(…)` in the URL, and a UUID plus its separator is ~37 bytes.
BATCH = 100
PAGE = 500                    # rows read per window step
TIMEOUT_CODE = "57014"


def _timed_out(resp) -> bool:
    return resp.status_code >= 500 and TIMEOUT_CODE in (resp.text or "")


def too_many(matched: int, seen: int, max_share: float, min_sample: int) -> bool:
    """Is this run deleting an implausible share of what it read?

    A free function so it can be graded without a database — the tripwire is the
    part that has to work on the day something else has already gone wrong, and
    logic reachable only through a live PostgREST call is logic nobody tests.
    """
    if seen < min_sample:
        return False           # a share off a handful of rows is noise
    return (matched / seen) > max_share if seen else False


# THE EVENTS TABLE HAS NO SOURCE COLUMN: external_source is 'mapsee' for every
# adapter. What every imported row does carry is the line
# mapsee_supabase_sync.to_row writes into its description, "Tickets / info:
# <url>", and that URL's host is the nearest thing to a source there is - the
# Mobilizon instance, the ticketing site, the library gateway. It is what a
# person needs to see before allowing a delete: forty rows from one spammed
# instance is the purge catching up; forty spread across ticketing sites is a
# rule that has started matching ordinary listings.
_INFO_URL = re.compile(r"Tickets / info: (https?://\S+)")


def source_host(description) -> str:
    """The host of a row's own "Tickets / info:" link, or "(no link)"."""
    m = _INFO_URL.search(str(description or ""))
    host = urllib.parse.urlsplit(m.group(1)).netloc.lower() if m else ""
    if host.startswith("www."):
        host = host[4:]
    return host or "(no link)"


HERE = os.path.dirname(os.path.abspath(__file__))

# THE TABLE STORES NO ADAPTER, so the purge infers the source spam_reason asks
# for from the row's link, and says so in its report. Three answers:
#   * a host from mobilizon_sources.json or gancio_sources.json, _not_included
#     entries too (the retired spam hosts are recorded there) -> that source;
#   * meetup.com or openagenda.com -> "meetup" / "ods", the two platforms where
#     a number in the title was MEASURED to be an organiser's contact line on
#     real events (24 and 3 rows, 2026-09-14);
#   * anything else, or no link at all -> None, UNKNOWN, which spam_reason
#     treats like an open-registration source: 55 of the 205 advertisements
#     that rule caught link only to the advertiser's own page or to nothing.
# The ingest gate needs none of this: every adapter names itself.
_MEASURED_CLOSED_HOSTS = {"meetup.com": "meetup", "openagenda.com": "ods"}


def instance_hosts(here: str = HERE) -> dict:
    """host -> "mobilizon" | "gancio", from the two open-registration configs."""
    out = {}
    for family, fname in (("mobilizon", "mobilizon_sources.json"),
                          ("gancio", "gancio_sources.json")):
        try:
            with open(os.path.join(here, fname), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            continue
        urls = [s.get("base_url") or s.get("url") or "" for s in cfg.get("sites") or []]
        urls += [k for k in (cfg.get("_not_included") or {}) if str(k).startswith("http")]
        for u in urls:
            host = urllib.parse.urlsplit(str(u)).netloc.lower()
            host = host[4:] if host.startswith("www.") else host
            if host:
                out[host] = family
    return out


def row_source(description, instances: dict):
    """The source a stored row came through, as far as its link can tell; None = unknown."""
    host = source_host(description)
    if host == "(no link)":
        return None
    if host in instances:
        return instances[host]
    for known, family in _MEASURED_CLOSED_HOSTS.items():
        if host == known or host.endswith("." + known):
            return family
    return None


def _select(key: str) -> str:
    """The five columns the predicate reads, plus the walk's key when it is not one of them."""
    return "id,title,description,starts_at,ends_at" + ("" if key == "starts_at" else f",{key}")


def window_query(root: str, scope: str, cursor: str, page: int, op: str = "gte",
                 key: str = "starts_at") -> str:
    """The URL for one window of the walk, with the cursor QUOTED.

    PostgREST hands starts_at back as "2026-08-21T08:00:00+00:00", and in a query
    string a bare "+" is a space. The first run of this script against the live
    table (report only, 2026-09-14) read one page of 500 rows and died on the
    second request: 400, 22007, invalid input syntax for "2026-08-21T08:00:00
    00:00". The first cursor is built here with a "Z" and passes, which is why
    only the SECOND window fails - and why a table smaller than one page never
    shows it.

    Ordered by id as well as starts_at, so the rows a window holds at an instant
    are that instant's LOWEST ids - which is what lets walk() read the rest of
    an instant by id when it holds more than a page.
    """
    return (f"{root}?{scope}&{key}={op}.{urllib.parse.quote(cursor, safe='')}"
            f"&select={_select(key)}"
            f"&order={key}.asc,id.asc&limit={page}")


def instant_query(root: str, scope: str, at: str, after_id: str, page: int,
                  key: str = "starts_at") -> str:
    """The rest of ONE instant, by id, for an instant that holds more than a page."""
    return (f"{root}?{scope}&{key}=eq.{urllib.parse.quote(at, safe='')}"
            f"&id=gt.{urllib.parse.quote(after_id, safe='')}"
            f"&select={_select(key)}"
            f"&order=id.asc&limit={page}")


def walk(fetch_window, fetch_instant, cursor: str, page: int, out_of_time, visit,
         key: str = "starts_at"):
    """Visit every row from `cursor` forward in `key` order (starts_at for the
    full walk, created_at for the daily one); say how it ended.

    fetch_window(op, at) returns up to `page` rows with starts_at `op` ("gte" or
    "gt") `at`, ordered by (starts_at, id). fetch_instant(at, after_id) returns
    up to `page` rows AT `at` with a larger id, ordered by id. visit(row) may
    see a row more than once and must skip repeats: a "gte" window re-reads
    the instant it starts on, which is what stops an instant that straddles
    two windows losing its tail.

    AN INSTANT CAN HOLD MORE THAN A PAGE, and the first version of this walk
    stopped there for good. Measured 2026-09-14: 2,285 rows in, 500+ rows
    shared starts_at 2026-09-05T23:00:00+00:00, a "gte" window on that instant
    returns the same page for ever, and every row after it - every upcoming
    event in the table - was never judged. So a window that is ONE instant
    from end to end has that instant read to its end by id, and the walk then
    steps strictly past it.

    A free function so test_spam.py can drive it over a fake table: logic
    reachable only through a live PostgREST call is logic nobody tests.
    Returns (how the walk ended, the starts_at it ended at).
    """
    op = "gte"
    while not out_of_time():
        rows = fetch_window(op, cursor)
        for row in rows:
            visit(row)
        if len(rows) < page:
            return "end of the table", cursor
        last = rows[-1][key]
        if rows[0][key] != last:
            # A WINDOW, NOT AN OFFSET. offset= re-walks everything before it on
            # each step, which is what turns a long table into a timeout.
            op, cursor = "gte", last
            continue
        after = rows[-1]["id"]
        while True:
            if out_of_time():
                return (f"the time budget ran out inside {key} {last}; "
                        "rows after it were NOT examined"), last
            more = fetch_instant(last, after)
            for row in more:
                visit(row)
            if len(more) < page:
                break
            after = more[-1]["id"]
        op, cursor = "gt", last
    return (f"the time budget ran out at {key} {cursor}; "
            "rows after it were NOT examined"), cursor


# --------------------------------------------------------------------------- #
# THE DAILY RUN READS WHAT ARRIVED SINCE THE LAST ONE; THE WHOLE TABLE IS WEEKLY
# --------------------------------------------------------------------------- #
# A full walk read 744,093 rows in 1,261 s (2026-09-14) to find 227 deletes, and
# once the switch is on it runs every day. Almost all of it re-judges rows the
# same rules already passed the day before: the gate in EventStore.upsert
# (mapsee_ingest) applies this same spam_reason and implausible_end to every
# record before it is stored, so what the purge still finds is (a) rows older
# than the gate or than a rule, and (b) new rows the gate let through because
# it KNEW their adapter while the purge has to infer one from the link.
#
# (b) is only ever a NEW row. (a) needs the whole table only when the rules
# change. So with --state PATH (the scheduled run; kept in the Actions cache):
#
#   * FULL walk, exactly the one above, when there is no believable state, when
#     the rules changed (predicate_hash: this file and mapsee_spam.py), when the
#     last complete full walk is FULL_EVERY_DAYS old, or with --full;
#   * otherwise only rows CREATED since the last complete run began, less
#     OVERLAP_HOURS, by created_at.
#
# The daily read asks hidden_at=is.null: that is the predicate of ../mapsee
# 0111's events_created_at_idx ((created_at desc) where hidden_at is null), the
# only index on created_at, and the one mapsee_indexnow's walk of the same shape
# already uses. A new advert that is already hidden is off the map and waits for
# the weekly walk. It also asks starts_at >= the full walk's own floor
# (--from-days-ago), so the daily run can never reach a row the full walk
# cannot: what it may delete is a SUBSET of what the full walk may.
#
# WHAT WAITS FOR THE WEEKLY WALK, and nothing else does: a stored row whose
# description a refresh rewrote into something only the purge's inferred source
# refuses (the title is in the fingerprint, so a new title is a new row), a row
# a re-key moved, and hidden rows. A state that is missing, unreadable, from the
# future or written under other rules means FULL: the default is today's walk.
#
# THE SHARE CEILING GETS TIGHTER, NEVER LOOSER: 5% of a day's arrivals is far
# fewer rows than 5% of the table. Under --min-sample a share is still noise and
# is not evaluated, which a 744k-row walk never met and a quiet day can; the
# case the ceiling exists for, a widened rule, changes predicate_hash and so
# always meets the full walk and the full sample first.
#
# The state is written only by an --apply run whose walk reached the end of the
# table and whose writes all landed. A report, a refusal, a budget that ran out
# or a failed write leaves it as it was, and the next run starts from the same
# place. Without --state nothing here applies: a manual run is the full walk.
STATE_VERSION = 1
FULL_EVERY_DAYS = 7
OVERLAP_HOURS = 3
# A run is "a week later" on the same weekday even when the schedule starts it
# hours early or late (this one has started 7 h late): half a day of slack.
FULL_SLACK_HOURS = 12
_PREDICATE_FILES = ("mapsee_spam.py", "mapsee_spam_purge.py")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(value):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else None


def predicate_hash(here: str = HERE) -> str:
    """sha256 of the two files that decide what is deleted, line endings
    normalised (this checkout is CRLF on Windows, LF on the runner). The source
    configs are left out on purpose: a host added to mobilizon_sources.json
    moves a row from UNKNOWN to an open-registration source, and phone_counts
    answers the same for both."""
    h = hashlib.sha256()
    for name in _PREDICATE_FILES:
        with open(os.path.join(here, name), "rb") as fh:
            h.update(name.encode() + b"\0" + fh.read().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def load_state(path: str):
    """(state, None) or (None, why it is not believed). Never raises: whatever
    is wrong with the file, the answer is the full walk."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, f"no state at {path}"
    except (OSError, ValueError) as ex:
        return None, f"state unreadable ({type(ex).__name__})"
    if not isinstance(doc, dict) or doc.get("version") != STATE_VERSION:
        return None, f"state is not version {STATE_VERSION}"
    if not isinstance(doc.get("predicate"), str) or not doc["predicate"]:
        return None, "state names no rules"
    if _when(doc.get("full_at")) is None or _when(doc.get("watermark")) is None:
        return None, "state has no believable full_at/watermark"
    return doc, None


def plan_walk(state, why_not, predicate: str, now: datetime, force_full: bool = False,
              full_every_days: float = FULL_EVERY_DAYS, overlap_hours: float = OVERLAP_HOURS):
    """("full", why, None) or ("incremental", why, the created_at to start from)."""
    if force_full:
        return "full", "--full was passed", None
    if state is None:
        return "full", why_not or "no state", None
    if state["predicate"] != predicate:
        return "full", "the rules changed since the last complete full walk (mapsee_spam.py or this file)", None
    full_at, mark = _when(state["full_at"]), _when(state["watermark"])
    if full_at > now + timedelta(hours=1) or mark > now + timedelta(hours=1):
        return "full", "the state is stamped in the future", None
    if mark < full_at:
        return "full", "the state's watermark is older than its full walk", None
    age = now - full_at
    if age >= timedelta(days=full_every_days) - timedelta(hours=FULL_SLACK_HOURS):
        return "full", (f"the last complete full walk began {state['full_at']}, "
                        f"{age.total_seconds() / 86400:.1f} days ago (every {full_every_days:g})"), None
    since = mark - timedelta(hours=overlap_hours)
    return "incremental", (f"rows created since {_stamp(since)} (the last complete run began "
                           f"{state['watermark']}, less {overlap_hours:g} h); the last full walk "
                           f"began {state['full_at']}"), _stamp(since)


def save_state(path: str, predicate: str, began: datetime, full: bool, previous) -> None:
    doc = {"version": STATE_VERSION, "predicate": predicate,
           "full_at": _stamp(began) if full or previous is None else previous["full_at"],
           "watermark": _stamp(began)}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


class ReadTimedOut(RuntimeError):
    """A 57014 on a windowed read, or no answer inside the client's timeout."""


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove advertisements already in the events table.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete and clear. Without it, nothing is written.")
    # FIVE YEARS BACK, NOT THIRTY DAYS, and the reported row is why. "Shatru
    # Nashak…" STARTED on 2026-04-11 — five months before anyone noticed it —
    # and was still on the map because its end was 2036 and mapsee_cleanup can
    # only delete a row whose end has also passed. The rows this script most
    # needs to reach are, by construction, the ones with a past start that
    # cleanup could not touch, so a window that begins near today misses exactly
    # the population it was written for. It is cheap: cleanup runs weekly, so
    # almost nothing else is left back there to walk past.
    ap.add_argument("--from-days-ago", type=int, default=1825,
                    help="Start the walk this far back (default 1825 = 5 years). "
                         "The forever-pins have a PAST start; that is why they are "
                         "still here.")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--show", type=int, default=40, help="Titles to print (default 40).")
    # THE GUARD THAT MAKES THIS SAFE TO SCHEDULE, and it is a SHARE rather than a
    # count on purpose. An absolute cap has to be re-tuned every time the
    # catalogue grows and is wrong in both directions while you are guessing at
    # it; a share is self-calibrating and expresses the actual invariant, which
    # is that spam is a MINORITY of what this pipeline imports.
    #
    # The failure it exists for is not "spam spiked". It is "somebody widened a
    # regex and it now matches ordinary titles" — the exact mistake made while
    # writing mapsee_spam.py, where rejecting on span alone turned a real 487-day
    # Extinction Rebellion listing into an advertisement. That was caught by
    # reading an audit by hand. Scheduled and unattended, it would have deleted
    # real events every night and the only symptom would have been the map
    # getting quieter.
    #
    # Measured 2026-09-03: 11.8% of the MOBILIZON supply, which is a small
    # fraction of a catalogue also carrying Ticketmaster, OSM, Eventbrite and
    # forty other adapters. 5% of everything read is comfortably above the real
    # number and far below "the predicate has stopped meaning what it meant".
    ap.add_argument("--max-share", type=float, default=0.05,
                    help="Refuse to write if more than this share of rows read are "
                         "matched (default 0.05). A high share means the predicate "
                         "changed meaning, not that spam did.")
    ap.add_argument("--min-sample", type=int, default=200,
                    help="Below this many rows read, --max-share is not evaluated: a "
                         "share off a handful of rows is noise (default 200).")
    ap.add_argument("--state", default=None, metavar="PATH",
                    help="Remember the last complete --apply run here (the Actions cache "
                         "keeps it) and read only rows created since, with the full walk "
                         f"every {FULL_EVERY_DAYS} days and whenever the rules change. "
                         "Without it every run is the full walk.")
    ap.add_argument("--full", action="store_true",
                    help="With --state: walk the whole table this run whatever the state says.")
    a = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (server-side secrets).")
    auth = {"apikey": key, "Authorization": f"Bearer {key}"}
    root = url.rstrip("/") + "/rest/v1/events"

    # SAFETY: on every request, without exception. Community rows carry
    # external_source IS NULL and can never match this.
    SCOPE = "external_source=eq.mapsee"
    assert "external_source=eq.mapsee" == SCOPE, "refusing an unscoped walk"

    began = _now()
    floor = _stamp(began - timedelta(days=max(0, a.from_days_ago)))
    started = time.monotonic()
    seen = 0
    seen_ids: set[str] = set()
    doomed: list[tuple[str, str, str, str]] = []     # (id, reason, title, host)
    unbounded: list[tuple[str, int, str, str]] = []  # (id, span, title, host)
    drained: dict[str, int] = {}                     # instant -> rows read past its first page
    instances = instance_hosts()                     # host -> "mobilizon" | "gancio"

    # WHICH WALK (see plan_walk). No --state: the full walk, as it always was.
    state, predicate, mode, since = None, None, "full", None
    if a.state:
        predicate = predicate_hash()
        state, why_not = load_state(a.state)
        mode, why, since = plan_walk(state, why_not, predicate, began, a.full)
        print(f"Walk: {mode.upper()} - {why}.")

    def fetch(q: str) -> list:
        try:
            r = requests.get(q, headers=auth, timeout=120)
        except Exception as ex:                       # noqa: BLE001
            # No answer inside our own 120 s is the same condition as a 57014
            # (the statement is still running); anything else is raised as it was.
            if type(ex).__name__ == "ReadTimeout":
                raise ReadTimedOut() from None
            raise
        if _timed_out(r):
            raise ReadTimedOut()
        if r.status_code >= 300:
            sys.exit(f"Read failed [{r.status_code}]: {r.text[:300]}")
        return r.json() or []

    def visit(row) -> None:
        # A "gte" window re-reads the instant it starts on, hence the set.
        # Judging a row twice would be harmless; COUNTING it twice would quietly
        # make the report wrong, and the report is the thing a person reads
        # before allowing a content-based delete.
        nonlocal seen
        if row["id"] in seen_ids:
            return
        seen_ids.add(row["id"])
        seen += 1
        source = row_source(row.get("description"), instances)
        label = f"{source_host(row.get('description'))} [{source or 'unknown source'}]"
        why = spam_reason(row.get("title"), row.get("description"),
                          row.get("starts_at"), row.get("ends_at"), source=source)
        if why:
            doomed.append((row["id"], why, (row.get("title") or "")[:70], label))
            return
        span = implausible_end(row.get("starts_at"), row.get("ends_at"))
        if span is not None:
            unbounded.append((row["id"], span, (row.get("title") or "")[:70], label))

    def run_walk(scope: str, key: str, cursor: str):
        def fetch_instant(at: str, after: str) -> list:
            rows = fetch(instant_query(root, scope, at, after, PAGE, key))
            drained[at] = drained.get(at, 0) + len(rows)
            return rows
        return walk(lambda op, at: fetch(window_query(root, scope, at, PAGE, op, key)),
                    fetch_instant, cursor, PAGE,
                    lambda: time.monotonic() - started >= a.max_seconds, visit, key)

    ended = None
    if mode == "incremental":
        # hidden_at=is.null is events_created_at_idx's own predicate; the
        # starts_at floor keeps this inside what the full walk can reach.
        daily = (f"{SCOPE}&hidden_at=is.null"
                 f"&starts_at=gte.{urllib.parse.quote(floor, safe='')}")
        assert daily.startswith(SCOPE + "&"), "refusing an unscoped walk"
        print(f"Walking aggregator events created since {since}…")
        try:
            ended, cursor = run_walk(daily, "created_at", since)
        except ReadTimedOut:
            # THE WORST CASE IS THE OLD ONE. If the planner will not take the
            # created_at index, the daily read is no cheaper than the walk it
            # replaces, so the run does that walk instead and says why. Nothing
            # judged so far is kept: the full walk sees those rows again.
            print("::warning::Spam purge: the created_at read timed out, so this run "
                  "walks the whole table instead. Check ../mapsee 0111's events_created_at_idx "
                  "exists and is valid; until it is, pass no --state.", flush=True)
            # The clock is NOT restarted: --max-seconds is paired with the job's
            # timeout-minutes, and the full walk gets what is left of it.
            mode = "full"
            seen = 0
            seen_ids.clear(); doomed.clear(); unbounded.clear(); drained.clear()
    if mode == "full":
        print(f"Walking aggregator events from {floor} forward…")
        # SAID OUT LOUD, because a walk that runs out of budget used to end exactly
        # like one that reached the end of the table - and it starts five years back
        # every time, so an unfinished walk never reaches the NEWEST rows at all.
        try:
            ended, cursor = run_walk(SCOPE, "starts_at", floor)
        except ReadTimedOut:
            sys.exit("The windowed read timed out. Check migration 0113 "
                     "(events_aggregator_cleanup_idx) is applied — it is what makes "
                     "this walk index-backed.")
    complete = ended == "end of the table"
    ended = ended.replace("the time budget", f"the --max-seconds {a.max_seconds} budget")
    # Checked on DELETIONS only. Clearing an end date is reversible in the sense
    # that matters — the row stays, the sync refills it from the source on the
    # next full refresh — so it does not deserve a tripwire that stops the run.
    # Printed in a REPORT as well: the report is what somebody reads before
    # allowing deletes, and "would --apply have refused?" is its first question.
    share = (len(doomed) / seen) if seen else 0.0
    refuse = too_many(len(doomed), seen, a.max_share, a.min_sample)

    print(f"read {seen} row(s) ({mode} walk); walk ended: {ended}")
    if drained:
        big = sorted(drained.items(), key=lambda kv: -kv[1])[:5]
        print(f"  {len(drained)} instant(s) held a full page or more; largest: "
              + ", ".join(f"{at} ({PAGE + n}+ rows)" for at, n in big))
    print(f"  {len(doomed)} advertisement(s) to delete: {share:.2%} of rows read, "
          f"against the {a.max_share:.0%} ceiling"
          + (" - OVER IT, so --apply would refuse" if refuse else ""))
    print(f"  {len(unbounded)} row(s) whose end date is not a fact")
    for label, items in (("advertisements", doomed), ("end dates to clear", unbounded)):
        if items:
            print(f"  {label} by source (host of each row's Tickets / info link [source inferred from it]):")
            for host, n in collections.Counter(x[3] for x in items).most_common(15):
                print(f"    {n:>6}  {host}")
    for _id, why, title, host in doomed[:a.show]:
        print(f"    DELETE [{why}] {title}  ({host})")
    if len(doomed) > a.show:
        print(f"    … and {len(doomed) - a.show} more")
    for _id, span, title, host in unbounded[:a.show]:
        print(f"    CLEAR END [{span} days] {title}  ({host})")
    if len(unbounded) > a.show:
        print(f"    … and {len(unbounded) - a.show} more")

    if not a.apply:
        print()
        print("Report only. Re-run with --apply to delete and clear.")
        return 0

    if refuse:
        print()
        print(f"REFUSING TO WRITE: {len(doomed)} of {seen} rows ({share:.1%}) matched, "
              f"over the {a.max_share:.0%} ceiling.")
        print("That is not what a spam rate looks like. Read the titles above before "
              "anything is deleted — the likely cause is a rule in mapsee_spam.py that "
              "now matches ordinary listings, not a change at the sources.")
        print("If the titles really are all advertisements, re-run with a higher "
              "--max-share and say so in the run's notes.")
        return 1

    deleted = 0
    for i in range(0, len(doomed), BATCH):
        ids = [d[0] for d in doomed[i:i + BATCH]]
        resp = requests.delete(f"{root}?{SCOPE}&id=in.({','.join(ids)})",
                               headers=dict(auth, Prefer="return=minimal,count=exact"),
                               timeout=300)
        if resp.status_code >= 300:
            sys.exit(f"Delete failed [{resp.status_code}]: {resp.text[:300]}")
        try:
            deleted += int(resp.headers.get("Content-Range", "*/0").split("/")[-1])
        except ValueError:
            deleted += len(ids)
        print(f"  deleted {deleted}…", flush=True)

    cleared = 0
    for i in range(0, len(unbounded), BATCH):
        ids = [u[0] for u in unbounded[i:i + BATCH]]
        resp = requests.patch(f"{root}?{SCOPE}&id=in.({','.join(ids)})",
                              headers=dict(auth, **{"Content-Type": "application/json",
                                                    "Prefer": "return=minimal,count=exact"}),
                              json={"ends_at": None}, timeout=300)
        if resp.status_code >= 300:
            sys.exit(f"Clearing ends_at failed [{resp.status_code}]: {resp.text[:300]}")
        try:
            cleared += int(resp.headers.get("Content-Range", "*/0").split("/")[-1])
        except ValueError:
            cleared += len(ids)
        print(f"  cleared {cleared}…", flush=True)

    print(f"Deleted {deleted} advertisement(s); cleared the end date on {cleared} row(s). "
          f"The cleared ones leave on mapsee_cleanup.py's next run once their start is past.")
    # LAST, and only here: every return and exit above leaves the state as it
    # was, so a refusal, a failed write or a walk the budget cut short is
    # repeated from the same place.
    if a.state and complete:
        save_state(a.state, predicate, began, mode == "full", state)
        print(f"State: {a.state} now says this {mode} walk began {_stamp(began)}"
              + ("" if mode == "full" else f" (the last full walk: {state['full_at']})") + ".")
    elif a.state:
        print(f"State: NOT updated (the walk did not reach the end); the next run starts "
              f"where this one did.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
