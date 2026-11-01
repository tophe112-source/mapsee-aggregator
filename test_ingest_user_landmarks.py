#!/usr/bin/env python3
"""
test_ingest_user_landmarks.py — a member's landmark becomes the same kind of
row an OSM fountain does, and the feed's verdict is obeyed and re-checked.

Three things go wrong silently here. A glyph that drifts from
../mapsee/site/js/place-categories.js is a pin the Bathrooms chip can no longer
turn off. A catalog key that drifts from 0250's recipe is a landmark the
database can never find the reports for. And a withdrawn or hidden row that is
upserted instead of cancelled is a landmark somebody took back that stays on
the map for ever, because an upsert cannot delete.

Run: python test_ingest_user_landmarks.py
"""
import os
import re
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import mapsee_ingest_user_landmarks as U

TODAY = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SUPABASE = "https://unit-test.supabase.co"
ID = "11111111-2222-4333-8444-555555555555"
PHOTO = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/ffffffff-1111-4222-8333-444444444444.jpg"

# ../mapsee/site/js/place-categories.js LANDMARK_CATEGORIES, 2026-11-01. The
# product maps a pin's icon back to a Free filter type by this glyph.
PRODUCT_TYPES = {
    "water": "🚰", "bathrooms": "🚻", "art": "🎨", "little_libraries": "📚", "dogs": "🐕",
    "playgrounds": "🛝", "gyms": "🏋️", "repair": "🔧", "food": "🥫", "give": "🎁",
    "markets": "🧺", "parks": "🌳", "benches": "🪑", "other": "📍",
}


def row(**over):
    base = {"id": ID, "external_id": U.external_id(ID), "landmark_type": "bathrooms",
            "title": "Pavilion toilets", "description": "Behind the pavilion, open in daylight.",
            "place_name": "Park gate", "lat": 47.61235, "lon": -122.33123, "image_path": PHOTO,
            "created_at": "2026-11-01T10:00:00+00:00", "updated_at": "2026-11-01T10:00:00+00:00",
            "state": "live", "state_reason": None, "up": 0, "down": 0, "reports": 0}
    base.update(over)
    return base


class FakeStore:
    def __init__(self):
        self.upserts, self.cancels, self.complete = [], [], []

    def upsert(self, ev):
        self.upserts.append(ev); return "added"

    def cancel(self, ev, reason, notice=False):
        self.cancels.append((ev, reason)); return "cancelled"

    def mark_complete(self, *a, **k):
        self.complete.append((a, k))


def main():
    checks = []
    ok = lambda cond, why: checks.append((bool(cond), why))

    # ------------------------------------------------- the row it becomes
    ev = U.to_event(row(), SUPABASE, TODAY)
    ok(ev is not None, "a live bathroom row becomes an event")
    ok(ev.source == "user-landmark" and ev.source_id == ID, "one source name, the landmark id as source_id")
    ok(ev.fingerprint == f"user-landmark:{ID}", "the fingerprint IS 0250's catalog key, user-landmark:<id>")
    ok(ev.pin_only is True, "a landmark is furniture: drawn, never a Nearby listing")
    ok(ev.coords_exact is True and ev.latitude == 47.61235 and ev.longitude == -122.33123, "the member's point is the truth, never geocoded over")
    ok(ev.city is None and ev.region is None and ev.country is None, "no invented town")
    ok(ev.icon == "🚻" and ev.category == "outdoors", "a toilet draws 🚻 under outdoors, as the OSM adapter's does")
    ok(ev.recurring_days == U.ALWAYS and ev.start_local == "2026-11-01T00:00:00" and ev.end_local == "2026-11-01T23:59:00",
       "an always-open week, dated today, so the sync writes a standing row")
    ok(ev.venue_name == "Park gate", "the place name rides as the venue")
    ok(ev.poster_image_url == f"{SUPABASE}/storage/v1/object/public/things-images/{PHOTO}", "the photo is the bucket's public URL")
    ok(ev.description.startswith("Pavilion toilets — public toilet.\n\n"), "the opener is the OSM adapter's shape, title then noun")
    ok("Behind the pavilion, open in daylight." in ev.description, "the member's words are the body")
    ok(ev.description.rstrip().endswith(U.ATTRIBUTION), "the attribution line is last")
    ok(U.ATTRIBUTION.startswith("Shared by a mapsee member"), "...and begins with the words ../mapsee parseImportedDesc keys on")
    ok("OpenStreetMap" not in ev.description, "a member's landmark never credits OpenStreetMap")
    ok(U.to_event(row(image_path=None), SUPABASE, TODAY).poster_image_url is None, "no photo, no poster")
    ok(U.to_event(row(image_path="../../etc/passwd"), SUPABASE, TODAY).poster_image_url is None, "a path the bucket policy would refuse is not turned into a link")
    bare = U.to_event(row(title="Public toilet", description=""), SUPABASE, TODAY)
    ok(bare.description == "Public toilet — public toilet.\n\n" + U.ATTRIBUTION or bare.description.startswith("Public toilet"),
       "a title that is the noun does not read 'toilet — toilet'")
    ok(not bare.description.lower().startswith("public toilet — public toilet"), "...measured: the doubled noun is gone")

    # ------------------------------------------------- every type, and the product's glyphs
    ok(set(U.KINDS) == set(PRODUCT_TYPES), f"the 14 types are the product's 14: {sorted(set(U.KINDS) ^ set(PRODUCT_TYPES)) or 'no difference'}")
    for key, glyph in PRODUCT_TYPES.items():
        e = U.to_event(row(landmark_type=key), SUPABASE, TODAY)
        ok(e is not None and e.icon == glyph, f"{key} draws {glyph} (got {getattr(e, 'icon', None)})")
    glyphs = [k.glyph for k in U.KINDS.values()]
    ok(len(set(glyphs)) == len(glyphs), "no two types share a glyph")
    ok(all(k.category in {"running", "fitness", "sports", "music", "food", "community", "party", "market",
                          "outdoors", "arts", "theater", "kids", "learning", "volunteer", "other"} for k in U.KINDS.values()),
       "every type lands on a category key a lens opens onto")
    product = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mapsee", "site", "js", "place-categories.js")
    if os.path.exists(product):
        src = open(product, encoding="utf-8").read()
        live = dict(re.findall(r'key: "([a-z_]+)", label: "[^"]+", emoji: "([^"]+)"', src))
        ok(live == PRODUCT_TYPES, f"the sibling checkout's place-categories.js agrees with this test's table ({len(live)} types)")
    else:
        print("note  ../mapsee not checked out beside this repo; the glyph table is the committed copy")

    # ------------------------------------------------- refusals: the shapes a script writes
    ok(U.refusal(row()) is None, "a plain row is not refused")
    ok(U.refusal(row(landmark_type="statue")) == "unknown landmark type", "an unknown type is refused")
    ok(U.refusal(row(title="x")) == "title too short", "a one-letter title is refused")
    ok(U.refusal(row(title="Toilets https://spam.example")) == "link in title", "a link in the title is refused")
    ok(U.refusal(row(title="Toilets www.spam.example")) == "link in title", "...www counts as a link")
    ok(U.refusal(row(title="Call +91 9965500027 now")) is not None, "a phone number in the title is refused")
    ok(U.refusal(row(title="Toilets 0022991113322")) is not None, "...an unbroken run of digits too")
    ok(U.refusal(row(title="Bench 12 by gate 3")) is None, "a bench numbered 12 is not a phone number")
    ok(U.refusal(row(description="see http://a http://b http://c")) == "description is mostly links", "three links in a description is an advert")
    ok(U.refusal(row(description="see http://a and http://b")) is None, "two links are a description")
    ok(U.refusal(row(title="Marabout retour affectif")) == "scam phrase in title", "mapsee_spam's phrases apply with the source unknown")
    ok(U.refusal(row(lat=None)) == "no usable coordinate", "a missing coordinate is refused")
    ok(U.refusal(row(lat=91)) == "no usable coordinate", "a latitude off the earth is refused")
    ok(U.refusal(row(lat=0, lon=0)) == "coordinate is null island", "0,0 is refused")
    ok(U.refusal(row(external_id="user-landmark:someone-else")) == "catalog key differs from the recipe", "a feed key that is not the recipe is a contract drift, refused")
    ok(U.refusal(row(external_id=None)) is None, "a feed without the key column still works from the recipe")
    ok(U.to_event(row(title="x"), SUPABASE, TODAY) is None, "to_event returns None for a refused row")

    # ------------------------------------------------- the verdict, and its re-check
    ok(U.verdict(row()) == ("upsert", None), "live with no votes is upserted")
    ok(U.verdict(row(state="withdrawn", state_reason="withdrawn")) == ("cancel", "withdrawn"), "withdrawn is cancelled")
    ok(U.verdict(row(state="hidden", state_reason="consensus")) == ("cancel", "consensus"), "hidden is cancelled with the database's reason")
    ok(U.verdict(row(state="banned", state_reason="banned")) == ("cancel", "banned"), "a banned member's landmark is cancelled")
    ok(U.verdict(row(state="published")) == ("cancel", "unknown state 'published'"), "an unknown state is never upserted")
    ok(U.verdict(row(up=0, down=3)) == ("cancel", "consensus (re-checked)"), "three down and no up is cancelled even if the feed said live")
    ok(U.verdict(row(up=3, down=3)) == ("upsert", None), "three down against three up stays")
    ok(U.verdict(row(up=0, down=2)) == ("upsert", None), "two down is not a consensus")
    ok(U.verdict(row(up="x", down="y")) == ("upsert", None), "unreadable counts do not cancel a live row")

    # ------------------------------------------------- the run: upserts, tombstones, refusals, counts
    store = FakeStore()
    rows = [row(), row(id="22222222-2222-4333-8444-555555555555", external_id=None, state="withdrawn", state_reason="withdrawn"),
            row(id="33333333-2222-4333-8444-555555555555", external_id=None, title="Toilets https://spam.example"),
            row(id="44444444-2222-4333-8444-555555555555", external_id=None, landmark_type="water", up=0, down=4)]
    logs = []
    counts = U.ingest(iter(rows), store, SUPABASE, log=logs.append, today=TODAY)
    ok(counts["read"] == 4 and counts["upserted"] == 1 and counts["cancelled"] == 2 and counts["refused"] == 1, f"counts: {counts}")
    ok(len(store.upserts) == 1 and store.upserts[0].fingerprint == f"user-landmark:{ID}", "the live row is upserted")
    ok([c[1] for c in store.cancels] == ["withdrawn", "consensus (re-checked)"], f"the withdrawn and the outvoted rows are tombstoned: {[c[1] for c in store.cancels]}")
    ok(store.cancels[0][0].fingerprint == "user-landmark:22222222-2222-4333-8444-555555555555", "a tombstone carries the live row's own fingerprint")
    ok(counts["refusals"] == {"link in title": 1} and any("refused" in line for line in logs), "a refusal is counted and logged, never written")
    ok(counts["cancel_results"] == {"cancelled": 2}, "tombstone results are counted")
    line = U.summary(counts, False)
    ok("4 row(s) read: 1 upserted, 2 cancelled, 1 refused" in line and "live 2" in line and "withdrawn 1" in line, f"the summary says what happened: {line}")
    dry_logs = []
    dry = U.ingest(iter(rows), None, SUPABASE, log=dry_logs.append, today=TODAY)
    ok(dry["upserted"] == 1 and any("would upsert" in l for l in dry_logs) and any("would cancel" in l for l in dry_logs), "a dry run prints what it would do")

    # ------------------------------------------------- the real store, once: a tombstone is trusted
    import tempfile
    from mapsee_ingest import EventStore
    with tempfile.TemporaryDirectory() as d:
        real = EventStore(os.path.join(d, "store.json"))
        real.upsert(U.to_event(row(), SUPABASE, TODAY))
        result = real.cancel(U.to_event(row(), SUPABASE, TODAY), "withdrawn")
        ok(result == "cancelled", f"EventStore trusts a user-landmark cancellation (got {result!r}; an unknown family's would be 'untrusted')")
        ok(f"user-landmark:{ID}" in real.tombstones and f"user-landmark:{ID}" not in real.records, "the tombstone replaces the live record")
        real.mark_complete(U.SOURCE, TODAY, TODAY.replace(day=8))
        ok(U.SOURCE in real.complete_reads, "mark_complete records the one unit")

    # ------------------------------------------------- paging the feed
    pages = {None: [row(id=f"0000000{i}-2222-4333-8444-555555555555") for i in range(1, 4)],
             "00000003-2222-4333-8444-555555555555": [row(id="00000004-2222-4333-8444-555555555555")]}
    asked = []

    def fetch(after, limit):
        asked.append((after, limit)); return pages.get(after, [])
    got = [r["id"] for chunk in U.feed_pages(fetch, page=3) for r in chunk]
    ok(len(got) == 4 and asked == [(None, 3), ("00000003-2222-4333-8444-555555555555", 3)], f"keyset paging follows the last id and stops on a short page: {asked}")
    ok(list(U.feed_pages(lambda a, l: [], page=3)) == [], "an empty feed is one empty read")
    try:
        list(U.feed_pages(lambda a, l: [row(id="")] * 3, page=3)); ok(False, "a page that cannot advance is an error")
    except U.FeedError:
        ok(True, "a page that cannot advance the cursor is a FeedError, not a loop")
    try:
        list(U.feed_pages(lambda a, l: {"error": "x"}, page=3)); ok(False, "a non-list answer is an error")
    except U.FeedError:
        ok(True, "a non-list answer is a FeedError")

    # ------------------------------------------------- the HTTP shape, with a fake session
    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, str(body)

        def json(self):
            return self._body

    class Session:
        def __init__(self, answers):
            self.answers, self.calls = list(answers), []

        def post(self, url, json=None, headers=None, timeout=None):
            self.calls.append((url, json, headers)); return self.answers.pop(0)
    sess = Session([Resp(500, "boom"), Resp(200, [row()])])
    slept = []
    fetch = U.postgrest_fetch(SUPABASE, "service-key", session=sess, sleep=slept.append)
    got = fetch(None, 500)
    ok(len(got) == 1 and len(sess.calls) == 2 and slept == [2], "a 5xx is retried after a pause, then the page is read")
    url, body, headers = sess.calls[0]
    ok(url == f"{SUPABASE}/rest/v1/rpc/landmarks_catalog_feed" and body == {"p_after": None, "p_limit": 500}, f"the RPC is called by name with keyset arguments: {url} {body}")
    ok(headers["apikey"] == "service-key" and headers["Authorization"] == "Bearer service-key", "the service role key is the credential, both ways PostgREST wants it")
    sess = Session([Resp(404, "not found")])
    try:
        U.postgrest_fetch(SUPABASE, "k", session=sess, sleep=slept.append)(None, 10); ok(False, "a 404 is final")
    except U.FeedError as exc:
        ok("404" in str(exc) and len(sess.calls) == 1, "a 4xx (the migration not applied yet) fails at once and says so")

    # ------------------------------------------------- the entry point refuses to run blind
    env = {k: os.environ.pop(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if k in os.environ}
    try:
        ok(U.main(["--dry-run"]) == 2, "no credentials is a non-zero exit, not a friendly skip")
    finally:
        os.environ.update(env)

    failed = 0
    for passed, why in checks:
        failed += 0 if passed else 1
        print(f"{'ok  ' if passed else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
