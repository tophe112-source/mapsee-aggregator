#!/usr/bin/env python3
"""
mapsee_prune_cancelled.py — take an event off the map after the source calls it off.

WHY THIS EXISTS. Every cancellation check in this repo runs at INGEST: Dice and
Venuepilot read a status field, Eventbrite reads is_cancelled, OpenActive honours
a `state: deleted` tombstone, the festival and JSON-LD adapters read schema.org's
eventStatus, and mapsee_ingest_meetup now reads Meetup's. All of them protect the
events we have not written yet, and NONE of them can do anything about a row
already in the table — an upsert cannot delete, and `--only-new` (the CI default)
means a scheduled run can only ever ADD. An event that was real when we took it
and is called off a week later stays on the map, with its pin, its RSVP button
and its chat, until mapsee_cleanup reaps it a week after it was supposed to end.

That is the same shape of bug as mapsee_prune_links: not an error anywhere, just
a confident sentence that stopped being true.

HOW BIG IT IS. Measured 2026-09-13 by sampling 2,000 live event pages from
mapsee.me's own sitemaps, taking the 502 whose "Tickets / info" link is a Meetup
event, and re-probing every one at its own source URL:

    scheduled   437
    CANCELLED    47      the source page's own markup says EventCancelled
    HTTP 404     18      the listing has been deleted outright
                ---
                 65      12.9% of the Meetup events on the map are not happening

Meetup is about a quarter of the map's linked rows (502 of the 1,969 sampled rows
carrying a Tickets / info link), so this is not a corner. And the ingest-side
check cannot reach them: the same day, a live eventSearch sweep of Seattle
returned 30 events and ALL 30 were ACTIVE. These rows were live when we took
them. They were cancelled afterwards, which is the only way most events are
cancelled, and this is the only thing that can notice.

WHAT COUNTS AS EVIDENCE. Two things, both unambiguous:

  * schema.org eventStatus of EventCancelled or EventPostponed, in JSON-LD or
    microdata. Postponed counts because the date WE hold is the one that is not
    happening; a new date is a new event and the adapters will import it.
  * HTTP 404 or 410 on the listing. Gone is gone.

WHAT IS NOT EVIDENCE, and the list is the point:

  * A 403, a 429, a 5xx, a timeout, a non-HTML body. The big ticketing hosts
    block scrapers, and treating a refusal as a cancellation would empty the map
    of exactly the sources that guard themselves best. Same rule, same reason, as
    destination_verdict in mapsee_menu_links.
  * PROSE. "This event has been cancelled" is a sentence, and so is "cancellation
    policy", "free cancellation up to 24 hours", "cancelled orders are refunded"
    — which appear on a large share of legitimate ticket pages. There is no text
    rule here and there should not be one; the markup already says it properly.
  * A cancellation marker on a page that is describing SOMEBODY ELSE'S event. A
    "Tickets / info" link sometimes lands on a venue's whole calendar, where one
    cancelled show among twenty would otherwise condemn our row. So a page is
    only read as cancelled when EVERY eventStatus on it says so; a mixed page is
    unknown, and unknown keeps the row.
  * A page its host's ROBOTS.TXT refuses us, because that page is not read at
    all. The owner's line (2026-09-30): robots.txt binds a publisher's own pages
    and feeds, and a "Tickets / info" link is the publisher's own page. The
    verdict is `robots`, which keeps the row as `unknown` does. It went in the
    day 76 feeds were retired for their robots.txt. The rows those feeds had
    already imported stay on the map until their dates pass, and this sweep was
    still visiting their event pages. Santa Fe Public Library alone had 1,109
    upcoming programs there, on a site whose robots.txt says `Disallow: /` to
    every crawler it does not name. Measured that day: meetup.com and
    eventbrite.com event pages are allowed, so the sweep's reason to exist is
    untouched.
  * A whole HOST answering dead at once. Restaurants close independently and so
    do gigs; twenty listings on one domain dying on the same afternoon is a
    statement about us, not about them. Lifted wholesale from mapsee_prune_links,
    where it was learned by nearly deleting every Uber Eats link on the map.

HIDE, NOT DELETE. hidden_at comes off the map immediately, is one PATCH to undo,
and does not cascade through event_messages, event_rsvps, invites and cohosts —
which matters most for exactly these rows, because somebody may have RSVP'd to
the thing that got cancelled. mapsee_cleanup deletes it a week after it would
have ended, as it does every other imported row, so nothing accumulates. And
hiding is durable against re-import: fetch_import_state does not filter on
hidden_at, so a hidden row still counts as existing and `--only-new` will not
put it back.

HIDDEN ONLY, NEVER cancelled_at, though the sync's own cancellations set both
(2026-10-05). cancelled_at on an unclaimed import is the sync's marker for "WE
cancelled this, so a live listing may lift it": mapsee_supabase_sync un-cancels
a row a source that read its whole timetable lists live again (both stamps
still equal). This sweep's evidence is a PAGE, and a feed
lags its pages: a Meetup group's feed still lists an event its own page calls
cancelled (the 30-for-30 ACTIVE sweep in docs/agents/cancelled-events.md). Set
cancelled_at here and the next sync would lift every such hide, and this sweep
would put it back, twice a day. A 404 is not a cancellation either, and
../mapsee labels a cancelled_at row "Cancelled" to the people who RSVP'd.

HOW BIG THE SWEEP ACTUALLY IS, measured on the first live run 2026-09-13:
**88,128 rows and 25,311 distinct source URLs inside a three-day window.** At
roughly one probe a second that is seven hours of work, so every run is capped
and every run leaves most of the map unexamined. Which part it examines is
therefore the whole design, and it is decided by two things: `--days`/`--back`
choose the slice, and the soonest-first ordering decides what inside that slice
gets the budget. `--back` defaults to 0 for exactly this reason — see its note.

Env:  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Run:  python mapsee_prune_cancelled.py                    # dry run, the default
      python mapsee_prune_cancelled.py --apply
      python mapsee_prune_cancelled.py --apply --max-seconds 900
      python mapsee_prune_cancelled.py --apply --write-url-index prune_url_index.json   # the full sweep
      python mapsee_prune_cancelled.py --apply --days 2 --url-index prune_url_index.json
"""
import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import robots_txt   # stdlib only, like this file

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PAGE = 500

UA = ("MapseeAggregator/1.0 (+https://mapsee.me; events@mapsee.me) "
      "cancellation-check")

# The line the sync writes from ticket_url — the source's own page for this
# event, and the only pointer a stored row keeps back to where it came from.
TICKETS_LINE = re.compile(r"^\s*Tickets\s*/\s*info:\s*(\S+)\s*$", re.I | re.M)

# schema.org eventStatus, in the two ways a page can state it. JSON-LD is by far
# the commoner; the microdata form is matched in both attribute orders because
# half the CMSs that emit it put content= first.
_LD_STATUS = re.compile(r'"eventStatus"\s*:\s*"([^"]*)"', re.I)
_MICRO_STATUS = re.compile(
    r'<meta[^>]*\bitemprop\s*=\s*["\']eventStatus["\'][^>]*\bcontent\s*=\s*["\']([^"\']*)["\']'
    r'|<meta[^>]*\bcontent\s*=\s*["\']([^"\']*)["\'][^>]*\bitemprop\s*=\s*["\']eventStatus["\']',
    re.I)

_OFF = ("eventcancelled", "eventpostponed")

# Hosts proven to refuse a plain HTTP client, so their pages are never evidence.
# Kept deliberately short: a host belongs here only once a run has shown it
# answers everything the same way. The per-run host guard below catches the rest.
UNVERIFIABLE_HOSTS = re.compile(r"(^|\.)(ticketmaster\.[a-z.]+|livenation\.[a-z.]+)$", re.I)


def sb(path, method="GET", body=None, prefer=""):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", SERVICE_KEY)
    req.add_header("authorization", f"Bearer {SERVICE_KEY}")
    req.add_header("content-type", "application/json")
    if prefer:
        req.add_header("prefer", prefer)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None


def page_verdict(html: str) -> str:
    """"cancelled" | "live" | "unknown", from a page we actually fetched.

    Split out from the fetch so the decision is testable without a network, and
    so there is exactly one place that decides what markup means.

    A page with no eventStatus at all is "live", not "unknown": most event pages
    do not carry the property, and calling all of them unknown would be the same
    as not running. Absence of a cancellation is the normal state of the world;
    only a POSITIVE marker acts, and that is what keeps this fail-open.
    """
    found = [m for m in _LD_STATUS.findall(html or "")]
    found += [(a or b) for a, b in _MICRO_STATUS.findall(html or "")]
    found = [f.strip().rsplit("/", 1)[-1].lower() for f in found if f and f.strip()]
    if not found:
        return "live"
    off = [f for f in found if f in _OFF]
    if not off:
        return "live"
    if len(off) == len(found):
        return "cancelled"
    # A calendar page listing many events, one of them cancelled. We cannot tell
    # which one is ours, so we say so.
    return "unknown"


class _RobotsSession:
    """The one call robots_txt.Robots makes of a session, over urllib, so the
    twice-daily run keeps no dependency to break. An HTTP error is an answer
    (its code and body are what RFC 9309 and the challenge check read); a
    network failure raises, which Robots records as unreachable."""

    class _Answer:
        def __init__(self, status_code, content):
            self.status_code, self.content = status_code, content

    def get(self, url, timeout=15, allow_redirects=True):
        req = urllib.request.Request(url, headers={"user-agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return self._Answer(r.status, r.read(robots_txt.MAX_BYTES))
        except urllib.error.HTTPError as e:
            try:
                body = e.read(robots_txt.MAX_BYTES) or b""
            except Exception:           # an error with no body is still its code
                body = b""
            return self._Answer(e.code, body)


def cancellation_verdict(url: str, timeout: int = 20, robots=None) -> str:
    """"cancelled" | "gone" | "live" | "unknown" | "robots". Only the first two
    act. `robots` (a robots_txt.Robots) is asked first when given: a page it
    does not plainly allow is never fetched."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return "unknown"
    if host and UNVERIFIABLE_HOSTS.search(host):
        return "unknown"
    if robots is not None and robots.check(url)["allowed"] is not True:
        return "robots"
    try:
        req = urllib.request.Request(url, headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = (r.headers.get("content-type") or "").lower()
            if "html" not in ct and "json" not in ct and "text" not in ct:
                return "unknown"
            return page_verdict(r.read(600_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # The listing is gone. Everything else — 403 bot-block, 429, 5xx — is us
        # being refused, which is not the same as the event being off.
        return "gone" if e.code in (404, 410) else "unknown"
    except Exception:
        return "unknown"


def probe_all(order, robots, delay, max_seconds, began, verdict=None,
              clock=time.time, sleep=time.sleep):
    """Probe each URL once, soonest first. Returns (verdicts, budget_hit, paced).

    A HOST'S CRAWL-DELAY IS A FLOOR, NOT A SUGGESTION. Until 2026-10-05 every
    probe slept the same --delay (0.35 s) whatever the host's robots.txt asked:
    Barcelona's guia.barcelona.cat asks 10 s and was about to carry 1,237
    upcoming rows' links. Sleeping 10 s per row would spend the whole budget on
    one host, so a URL whose host is not due yet is skipped this run (counted in
    `paced`) and the walk moves on; it comes round again tomorrow, nearer the top.
    """
    verdict = verdict or cancellation_verdict
    verdicts, budget_hit, paced = {}, 0, collections.Counter()
    next_ok = {}
    for i, url in enumerate(order, 1):
        if max_seconds and clock() - began > max_seconds:
            budget_hit = len(order) - i + 1
            break
        host = _host(url)
        if clock() < next_ok.get(host, 0.0):
            paced[host] += 1
            continue
        verdicts[url] = verdict(url, robots=robots)
        if verdicts[url] != "robots":                  # nothing was fetched to pace
            ans = robots.check(url) if robots is not None else {}
            next_ok[host] = clock() + float(ans.get("crawl_delay") or 0)
            sleep(delay)
        if i % 50 == 0:
            print(f"    probed {i}/{len(order)}", flush=True)
    return verdicts, budget_hit, paced


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


# The filters every read of a candidate row carries, and every hide too: an
# import (a community event is never ours to hide), public, not hidden already.
# One constant, so the collect, the read-back of a far row and the PATCH can
# never drift apart.
LISTED = "&external_source=eq.mapsee&is_private=eq.false&hidden_at=is.null"

# THE CLAIMED SKIP, AS A FILTER. The collect skips a row with claimed_by; the
# write also asks claimed_at, because claimed_by is `on delete set null`
# (../mapsee 0043): a claimer who deletes their profile leaves claimed_at set
# and claimed_by empty, and the venue's listing is still the venue's.
UNCLAIMED = "&claimed_by=is.null&claimed_at=is.null"


def _ts(value) -> str:
    """A row's starts_at as a filter value: UTC, microseconds, a 'Z', quoted,
    like mapsee_dedupe_events.Z. PostgREST answers '2026-10-27T19:00:00+00:00',
    and a bare '+' in a query string arrives as a space."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return urllib.parse.quote(dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), safe="")


def collect(args):
    """Upcoming aggregator rows, grouped by the source URL that would prove them off."""
    now = time.time()
    start = now - args.back * 86400
    step = 86400 * 2
    t, pages = start, 0
    rows_by_url = collections.defaultdict(list)
    scanned = 0
    skipped_windows = []

    # TWO-DAY WINDOWS, EACH READ BY KEYSET, NEVER BY OFFSET. The windows came
    # from mapsee_reclassify and mapsee_prune_links, where deep OFFSET paging
    # died under the statement timeout; inside a window this still paged by
    # OFFSET, and it still died: every run 2026-10-20..10-26, the 16:55 --days 2
    # ones alone included, lost its first window to HTTP 500 at OFFSET
    # 37.5k-49.5k after 3 tries, so a near run read 41.5k-46.5k rows of a
    # window that holds more, and a full sweep lost 3-4 windows. The cursor is
    # mapsee_dedupe_events._list_range's: starts_at >= s AND (starts_at > s OR
    # id > last), ordered starts_at, id. The bare or() climbed 0.81 -> 2.03 s
    # over five pages there, 0.45-1.01 s over six with the gte beside it: the
    # index gets a lower bound, and no page walks the rows before it again
    # (OFFSET 45,000 walks 45,000 rows to return 500, about 2 M for a 45k
    # window of 90 pages). A window is still the unit a failure skips, so one
    # bad page costs the rest of two days, not the run. No count=: on this
    # table it is the statement that times out.
    while t < now + args.days * 86400 and pages < args.max_pages:
        w_a = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        w_b = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(min(t + step, now + args.days * 86400)))
        base = ("events?select=id,title,description,claimed_by,starts_at" + LISTED
                + f"&order=starts_at.asc,id.asc&limit={PAGE}")
        lower, n = f"starts_at.gte.{w_a}", 0
        while True:
            batch = None
            n += 1
            for attempt in range(3):
                try:
                    batch = sb(base + f"&and=({lower},starts_at.lt.{w_b})") or []
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  window {w_a[:10]} page {n} failed 3x ({e})", file=sys.stderr)
                        skipped_windows.append(w_a[:10])
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if not batch:
                break
            for row in batch:
                scanned += 1
                if row.get("claimed_by"):
                    continue                       # the venue owns this listing
                m = TICKETS_LINE.search(row.get("description") or "")
                if m:
                    rows_by_url[m.group(1)].append(row)
            if len(batch) < PAGE:
                break
            last = batch[-1]
            try:
                s = _ts(last.get("starts_at"))
            except (TypeError, ValueError):
                print(f"  window {w_a[:10]} page {n}: no cursor in {last.get('starts_at')!r}", file=sys.stderr)
                skipped_windows.append(w_a[:10])
                break
            nxt = f"starts_at.gte.{s},or(starts_at.gt.{s},id.gt.{last.get('id')})"
            if nxt == lower:
                # A page that does not move the cursor is a server ignoring the
                # filter, and the loop would read it forever. Same rule as
                # mapsee_dedupe_events.read_rsvp_ids: a failed read, not a loop.
                print(f"  window {w_a[:10]} page {n} did not advance the cursor", file=sys.stderr)
                skipped_windows.append(w_a[:10])
                break
            lower = nxt
        pages += 1
        t += step
    return scanned, rows_by_url, skipped_windows


# --------------------------------------------------------------------------- #
# FAR ROWS WITHOUT THE 120-DAY READ
# --------------------------------------------------------------------------- #
# A listing found off hides every collected row that shares its URL, and on a
# full sweep that reach was most of the point: at least 143 of the 360 hides
# 2026-10-20..10-26 were LATER rows of a listing found off today (Tenby
# leisure centre's timetable 10-20, Pembroke's 10-22, a multi-day stage 10-26).
# Since six mornings a week read --days 2 (about 44k rows, not about 674k), a
# listing found off on a weekday hid only its next 48 hours; the rest waited
# until they came within 48 hours themselves, one or two days of them a run.
#
# So the Sunday sweep, which reads the 120 days anyway, leaves behind a small
# file: {listing: [the ids of its rows beyond 48 hours]}. A near run that finds
# a listing off by today's evidence (EventCancelled or a 404 at that very URL,
# never prose, a 403 or a page robots.txt refuses, and never on a host that
# answers dead wholesale) also hides the ids the file holds for it. The file
# decides NOTHING: the near run's own probe is the verdict, and every id is
# read back first and hidden only if, NOW, it is still unhidden, unclaimed,
# public, ours, not yet started and its own Tickets / info line is the URL that
# was found off. A row re-linked since Sunday, claimed, hidden by anybody for
# any reason, or deleted is left exactly as it is. Missing, unreadable, from
# another version or older than URL_INDEX_MAX_AGE_DAYS, the file is ignored,
# the run says so, and the run is what it was before.
URL_INDEX_VERSION = 1
# Ids inside the first 2 days of the sweep are left out: every near run reads
# that window itself. It is the near runs' --days, by design.
URL_INDEX_NEAR_DAYS = 2
# Sunday to Sunday is 7 days; the schedule ran 2.3-7.1 h late in the measured
# week, so 8 covers one late sweep and never two missed ones.
URL_INDEX_MAX_AGE_DAYS = 8
# A LISTING WITH MORE THAN 200 LATER ROWS IS NOT AN EVENT'S PAGE, and one 404
# there is a site moving its calendar, not 4,000 events called off. Measured
# on the four feed-group stores of 2026-10-26 (187,009 rows inside 120 days,
# 131,328 links): every one of the 45 listings over 200 far rows was a
# calendar, a directory or a homepage (mdpls.libnet.info/events 4,829,
# grownyc.org/greenmarket 1,026, loisirs.montreal.ca/ 744,
# spl.org/event-calendar 696), and together they were 45,815 of the 52,928
# far ids. The 13 between 101 and 200 were the same kind and nothing else (a
# data.gov.hk dataset page 137, thealamo.org/ 117); 200, not 100, because the
# measured case the index is for, Tenby LC's timetable (at least 91 later
# rows on 10-20), deserves room to grow. Under it also sit the dates of one
# event (500 of the 676 listings carry a single title). Such a listing is
# left out, not split: its later rows wait for the Sunday sweep, exactly as
# they did before the index.
URL_INDEX_MAX_FAR = 200
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _url_key(url: str) -> str:
    """The URL a probe actually asks for, so two spellings of one request share
    an entry: scheme and host lower-cased, the fragment dropped (it never
    leaves the browser). Path and query are kept EXACTLY: a path is
    case-sensitive on half the booking platforms on the map, and a looser key
    could let a dead page hide rows that point at a live one."""
    u = (url or "").strip()
    try:
        p = urllib.parse.urlsplit(u)
        return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ""))
    except ValueError:
        return u


def _epoch(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def build_url_index(rows_by_url, now: float) -> dict:
    """The far ids of every listing a near run can still reach while the file
    is fresh. A listing is worth an entry only when a NEARER row of it can bring
    it into a near run first: so at least two rows, the soonest starting inside
    MAX_AGE + NEAR days (a near run 8 days on reads 2 days ahead), and at least
    one beyond the first NEAR days (the near run reads those itself). A listing
    that starts further out is in next Sunday's file instead; one with more
    than URL_INDEX_MAX_FAR far rows is counted in `left_out` and not written."""
    near_cut = now + URL_INDEX_NEAR_DAYS * 86400
    reach = now + (URL_INDEX_MAX_AGE_DAYS + URL_INDEX_NEAR_DAYS) * 86400
    by_key = collections.defaultdict(list)
    for url, rows in rows_by_url.items():
        by_key[_url_key(url)].extend(rows)
    urls, n, out, out_rows = {}, 0, 0, 0
    for key, rows in by_key.items():
        if len(rows) < 2:
            continue
        starts = [(_epoch(r.get("starts_at")), str(r.get("id") or "")) for r in rows]
        starts = [(s, i) for s, i in starts if s is not None and _UUID.match(i)]
        if not starts or min(s for s, _ in starts) >= reach:
            continue
        far = sorted({i for s, i in starts if s > near_cut})
        if len(far) > URL_INDEX_MAX_FAR:
            out, out_rows = out + 1, out_rows + len(far)
        elif far:
            urls[key] = far
            n += len(far)
    return {"version": URL_INDEX_VERSION,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "near_days": URL_INDEX_NEAR_DAYS, "max_far": URL_INDEX_MAX_FAR, "rows": n,
            "left_out": {"listings": out, "rows": out_rows}, "urls": urls}


def write_url_index(path: str, rows_by_url, now: float, skipped=()) -> str:
    """Write the file a near run reads (atomically: a run cut off mid-write
    must leave last week's file or none, never half of one). -> the log line."""
    doc = build_url_index(rows_by_url, now)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)
    size = os.path.getsize(path)
    note = (f"  URL INDEX: wrote {doc['rows']} far row(s) of {len(doc['urls'])} listing(s) "
            f"to {path} ({size / 1024:.0f} KB); left out {doc['left_out']['listings']} listing(s) "
            f"with more than {URL_INDEX_MAX_FAR} far rows ({doc['left_out']['rows']} rows)")
    if skipped:
        note += f"; {len(skipped)} window(s) errored and their rows are not in it"
    return note


def load_url_index(path: str, now: float):
    """-> ({url key: [far ids]}, the log line). ADVISORY: whatever is wrong with
    the file, the answer is an empty index and a sentence saying why, never an
    exception, because a run without it is exactly the run before it existed."""
    without = "far rows of a listing found off wait until they come within reach (as before the index)"
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {}, f"  URL INDEX: none at {path}; {without}"
    except (OSError, ValueError) as e:
        return {}, f"  URL INDEX: unreadable ({type(e).__name__}); ignored, {without}"
    if not isinstance(doc, dict) or doc.get("version") != URL_INDEX_VERSION:
        return {}, f"  URL INDEX: not version {URL_INDEX_VERSION}; ignored, {without}"
    written = _epoch(doc.get("written_at"))
    if written is None or written > now + 3600:
        return {}, f"  URL INDEX: no believable written_at ({doc.get('written_at')!r}); ignored, {without}"
    age = (now - written) / 86400
    if age > URL_INDEX_MAX_AGE_DAYS:
        return {}, (f"  URL INDEX: written {doc['written_at']}, {age:.1f} days ago, older than "
                    f"{URL_INDEX_MAX_AGE_DAYS}; ignored, {without}")
    raw = doc.get("urls")
    if not isinstance(raw, dict):
        return {}, f"  URL INDEX: no listings in it; ignored, {without}"
    # Every id goes into a PostgREST filter, so one that is not a uuid is
    # dropped rather than trusted: the file is ours, but it is still a file.
    # ...and an entry over the cap is dropped whoever wrote it.
    urls = {k: [i for i in v if isinstance(i, str) and _UUID.match(i)]
            for k, v in raw.items() if isinstance(k, str) and isinstance(v, list)}
    urls = {k: v for k, v in urls.items() if 0 < len(v) <= URL_INDEX_MAX_FAR}
    n = sum(len(v) for v in urls.values())
    return urls, (f"  URL INDEX: {n} far row(s) of {len(urls)} listing(s), "
                  f"written {doc['written_at']} ({age:.1f} days ago)")


PATCH_IDS = 100          # ids per request: the filter travels in the URL (~37 bytes each)


def read_far(far, not_before: float):
    """{id: the URL found off} -> {id: (row, that URL)}, for the far ids that
    may be hidden NOW. Read back, never trusted from the file: the row must
    still pass the collect's filters (LISTED), not have started, be unclaimed,
    and carry that same URL in its own Tickets / info line. A chunk that cannot
    be read keeps its rows; the index can only ever add a hide, never force one."""
    keep = {}
    ids = sorted(far)
    since = urllib.parse.quote(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(not_before)), safe="")
    for i in range(0, len(ids), PATCH_IDS):
        chunk = ids[i:i + PATCH_IDS]
        try:
            rows = sb("events?select=id,title,description,claimed_by,starts_at"
                      f"&id=in.({','.join(chunk)}){LISTED}{UNCLAIMED}&starts_at=gte.{since}") or []
        except Exception as e:
            print(f"    read of {len(chunk)} far row(s) failed ({e}); they are kept", file=sys.stderr)
            continue
        for row in rows:
            url = far.get(row.get("id"))
            if url is None or row.get("claimed_by"):
                continue
            m = TICKETS_LINE.search(row.get("description") or "")
            if m and _url_key(m.group(1)) == _url_key(url):
                keep[row["id"]] = (row, url)
    return keep


def hide(ids, stamp: str):
    """hidden_at = stamp on these ids, still carrying the collect's filters and
    the claimed skip, so an id that has since been hidden (by anybody: the sync,
    an organizer's take-down, a dedupe), claimed or deleted is a no-op and its
    stamp is never overwritten. -> (ids actually hidden, ids whose request failed)."""
    ids = list(ids)
    hidden, failed = [], 0
    for i in range(0, len(ids), PATCH_IDS):
        chunk = ids[i:i + PATCH_IDS]
        try:
            got = sb(f"events?id=in.({','.join(chunk)}){LISTED}{UNCLAIMED}&select=id",
                     "PATCH", {"hidden_at": stamp}, prefer="return=representation") or []
            hidden += [r.get("id") for r in got]
        except Exception as e:
            failed += len(chunk)
            print(f"    hide of {len(chunk)} failed: {e}", file=sys.stderr)
    return hidden, failed


# A majority, not unanimity — mapsee_prune_links learned that demanding 100%
# disarms the guard on the very host it was written for, because one live
# listing among fifteen dead ones is enough to break a unanimity test.
REFUSAL_SHARE = 0.6
REFUSAL_MIN = 5           # higher than prune_links' 3: a venue really can cancel
                          # three nights in a row, and this one HIDES rows.


def refusing_hosts(verdicts):
    """Hosts whose links are dead at a rate that says more about us than them."""
    by_host = collections.defaultdict(list)
    for u, v in verdicts.items():
        by_host[_host(u)].append(v)
    out = set()
    for host, vs in by_host.items():
        n_off = sum(1 for v in vs if v in ("cancelled", "gone"))
        if len(vs) >= REFUSAL_MIN and n_off >= max(REFUSAL_MIN, int(len(vs) * REFUSAL_SHARE)):
            out.add(host)
            print(f"  HOST REFUSING: {host} — {n_off} of {len(vs)} listings read as off. "
                  f"Treating as unknown, not hiding them.")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Hide aggregator events the source has since cancelled or deleted.")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--days", type=int, default=120, help="how far ahead to sweep")
    # ZERO, AND THE FIRST LIVE RUN IS WHY. It was 1, on the reasoning that a
    # multi-day event cancelled mid-run is worth catching. True, and irrelevant
    # next to what it costs: the queue is sorted soonest-first, so a day of
    # ALREADY-STARTED events sits at the front of it, and a capped run spends
    # its entire budget there. Measured 2026-09-13 on the first production dry
    # run — 88,128 rows and 25,311 distinct URLs inside a three-day window, of
    # which the run reached 894, and every single one of the 47 rows it found
    # had started YESTERDAY. It never got as far as today. An event that has
    # already begun is the least useful thing this can hide; cleanup deletes it
    # a week later regardless. Pass --back 1 deliberately when that is the job.
    ap.add_argument("--back", type=int, default=0,
                    help="days BEFORE now to include (default 0: only what has not started)")
    ap.add_argument("--max-pages", type=int, default=90)
    # THE BUDGET IS THE REAL LIMITER, and this is only politeness. It was 1200,
    # chosen against an estimate of "a few hundred URLs in a two-day window"
    # that turned out to be off by a factor of twenty — so the cap bit long
    # before --max-seconds did and the run stopped early for no good reason.
    # Sized now so that --max-seconds is what ends a sweep.
    ap.add_argument("--max-checks", type=int, default=4000,
                    help="distinct source URLs to probe per run; we are a guest on these servers")
    ap.add_argument("--max-seconds", type=int, default=0,
                    help="stop probing after this long and act on what we have (0 = no budget)")
    ap.add_argument("--delay", type=float, default=0.35, help="seconds between probes")
    # THE FAR-ROW INDEX (see FAR ROWS WITHOUT THE 120-DAY READ). One run writes
    # it, the others read it, never both: prune-cancelled.yml gives the write
    # to the runs that read the whole horizon (Sunday's, a blank dispatch).
    idx = ap.add_mutually_exclusive_group()
    idx.add_argument("--write-url-index", metavar="FILE",
                     help="after the collect, write every reachable listing's far row ids to FILE")
    idx.add_argument("--url-index", metavar="FILE",
                     help="a listing found off also hides the far rows FILE holds for it")
    args = ap.parse_args()

    if not SUPABASE_URL or not SERVICE_KEY:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    began = time.time()
    scanned, rows_by_url, skipped_windows = collect(args)
    print(f"\n  scanned {scanned} aggregator rows · "
          f"{len(rows_by_url)} distinct source URLs")
    if skipped_windows:
        print(f"  INCOMPLETE: {len(skipped_windows)} window(s) errored and were not read to their end: "
              f"{', '.join(skipped_windows[:6])}")
    # Written BEFORE the probing, which is what can run into the job's timeout:
    # the cache save after this step runs always(), and saves what is here.
    if args.write_url_index:
        print(write_url_index(args.write_url_index, rows_by_url, began, skipped_windows))
    index = {}
    if args.url_index:
        index, note = load_url_index(args.url_index, began)
        print(note)
    if not rows_by_url:
        print("  nothing to check.")
        return 0

    # ONE probe per distinct URL. A weekly series shares one listing, so the
    # naive loop would ask the same server the same question every week.
    #
    # SOONEST FIRST, and that ordering is the whole budget strategy. There are
    # far more upcoming rows than one run can politely probe, so something has to
    # be left out — and the run must not leave out the SAME things every day.
    # mapsee_prune_links sorts by how many rows share a URL, which is right for
    # it (a restaurant's link is one fact about seven rows) and wrong here: it
    # would re-probe the same popular listings daily and never reach the tail.
    # By start date the cap truncates the FAR end, which is both the least
    # harmful place for a stale row and the part that will be probed anyway as it
    # comes closer. A cancelled gig tomorrow is a wasted evening; a cancelled gig
    # in November is a row that has four months of runs to be caught in.
    first_start = {u: min(str(r.get("starts_at") or "9") for r in rows)
                   for u, rows in rows_by_url.items()}
    order = sorted(rows_by_url, key=lambda u: (first_start[u], u))
    if len(order) > args.max_checks:
        print(f"  CAPPED: probing the {args.max_checks} soonest of {len(order)} URLs "
              f"(through {first_start[order[args.max_checks - 1]][:10]}); the remaining "
              f"{len(order) - args.max_checks} are further out and wait for a later run")
        order = order[:args.max_checks]

    robots = robots_txt.Robots(_RobotsSession())      # one robots.txt per host per run
    verdicts, budget_hit, paced = probe_all(order, robots, args.delay, args.max_seconds, began)
    if budget_hit:
        print(f"  BUDGET: stopped after {args.max_seconds}s with {budget_hit} URL(s) "
              f"unprobed; they are left for the next run")
    if paced:
        print(f"  PACED: {sum(paced.values())} URL(s) not asked because their host's "
              f"Crawl-delay had not elapsed; left for a later run: "
              + ", ".join(f"{h} {n}" for h, n in paced.most_common(6)))

    tally = collections.Counter(verdicts.values())
    print("\n  verdicts: " + " · ".join(f"{k}={v}" for k, v in tally.most_common()))
    refused_by = collections.Counter(_host(u) for u, v in verdicts.items() if v == "robots")
    if refused_by:
        print(f"  NOT READ: {sum(refused_by.values())} page(s) on {len(refused_by)} host(s) "
              "whose robots.txt refuses us (their rows are kept): "
              + ", ".join(f"{h} {n}" for h, n in refused_by.most_common(8)))

    refusing = refusing_hosts(verdicts)
    off = {u: v for u, v in verdicts.items()
           if v in ("cancelled", "gone") and _host(u) not in refusing}
    if not off:
        print("  nothing to hide.")
        return 0

    affected = {}
    for u in off:
        for row in rows_by_url[u]:
            affected[row["id"]] = (row, off[u], u)

    # The far rows the index holds for each listing found off, read back now.
    far_ids = {}
    for u in off:
        for rid in index.get(_url_key(u), ()):
            if rid not in affected:
                far_ids.setdefault(rid, u)
    far = read_far(far_ids, began - args.back * 86400) if far_ids else {}

    print(f"\n  CALLED OFF at the source ({len(off)} listings, {len(affected)} rows):")
    for rid, (row, verdict, u) in list(affected.items())[:30]:
        print(f"    {verdict:<10} {str(row.get('starts_at'))[:10]}  "
              f"{(row.get('title') or '?')[:46]:<48} {u[:58]}")
    if len(affected) > 30:
        print(f"    … and {len(affected) - 30} more")
    if far_ids:
        print(f"\n  FROM THE URL INDEX: {len(far)} later row(s) of {len({u for _, u in far.values()})} "
              f"of those listings still carry the dead link ({len(far_ids) - len(far)} of the "
              f"{len(far_ids)} it held are gone, hidden, claimed, started or re-linked):")
        for rid, (row, u) in list(far.items())[:10]:
            print(f"    {off[u]:<10} {str(row.get('starts_at'))[:10]}  "
                  f"{(row.get('title') or '?')[:46]:<48} {u[:58]}")
        if len(far) > 10:
            print(f"    … and {len(far) - 10} more")

    if not args.apply:
        if args.url_index:
            print(f"\n  far rows the URL index would hide: {len(far)}")
        print("\n  DRY RUN — nothing written. Read the table above, then re-run with --apply.")
        return 0

    # ONE hide path for both: batched, and carrying the collect's filters, so a
    # row hidden or claimed since it was read keeps the state it has now.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hidden, failed = hide(list(affected), stamp)
    print(f"\n  hidden {len(hidden)} row(s) (hidden_at set; not deleted, and reversible)"
          + (f" · {failed} failed" if failed else ""))
    if args.url_index:
        done = set(hidden)
        far_hidden, far_failed = hide([rid for rid in far if rid not in done], stamp)
        print(f"  far rows hidden from the URL index: {len(far_hidden)}"
              + (f" · {far_failed} failed" if far_failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
