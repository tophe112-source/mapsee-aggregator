"""Bounded existence/ownership reads. No requests leave this process."""
import json
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import mapsee_supabase_sync as sync
# main() waits out a database that is not answering (wait_for_database); the
# stub here answers 503 on purpose and counts its calls, so the wait is off.
os.environ.setdefault("MAPSEE_DB_WAIT_SECONDS", "0")


class Store:
    def __init__(self, rows, cap=1000, fail_page=None, fail_from=None):
        self.rows = rows
        self.cap = cap
        self.fail_page = fail_page          # this one call answers 503 (a blip)
        self.fail_from = fail_from          # every call from this one on does (an outage)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        q = parse_qs(urlsplit(url).query)
        # If the IN predicate disappears this behaves like a real global
        # catalog, rather than silently making the test's fixture smaller.
        clause = q.get("external_id", [None])[0]
        ids = json.loads("[" + clause[4:-1] + "]") if clause else list(self.rows)
        matched = sorted(set(ids) & self.rows.keys())
        start = int(q.get("offset", [0])[0])
        limit = min(self.cap, int(q.get("limit", [10000])[0]))
        page = [dict(external_id=eid, claimed_at=self.rows[eid]) for eid in matched[start:start+limit]]
        failed = len(self.calls) == self.fail_page or (self.fail_from is not None
                                                        and len(self.calls) >= self.fail_from)

        class Response:
            status_code = 503 if failed else 200
            headers = {"Content-Range": f"{start}-{start+len(page)-1}/{len(matched)}" if page else f"*/{len(matched)}"}
            def json(self):
                return page
        return Response()


class LookupTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch.object(sync, "_patch_sleep")       # the retry's backoff costs nothing here
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def lookup(self, store, ids):
        return sync.fetch_import_state(store, "https://db.example", "unused-test-key", ids)

    def test_catalog_size_does_not_increase_query_or_result_count(self):
        for size in (1000, 1000000):
            # This is a synthetic cost model, not a production DB benchmark.
            store = Store({str(i): None for i in range(size)})
            store.rows["12"] = "2026-09-05T00:00:00Z"
            self.assertEqual(self.lookup(store, ["12", "999", "new", "12"]),
                             {"12": True, "999": False})
            self.assertEqual(len(store.calls), 1)
            query = parse_qs(urlsplit(store.calls[0][0]).query)
            self.assertEqual(query["select"], ["external_id,claimed_at"])
            self.assertEqual(query["external_source"], ["eq.mapsee"])
            self.assertEqual(query["limit"], ["3"])

    def test_server_page_cap_does_not_hide_claimed_or_existing_rows(self):
        store = Store({str(i): None for i in range(7)}, cap=2)
        store.rows["6"] = "claimed"
        state = self.lookup(store, list(store.rows))
        self.assertEqual(len(state), 7)
        self.assertTrue(state["6"])
        self.assertEqual(len(store.calls), 4)

    def test_empty_first_result_with_zero_range_is_valid(self):
        class Empty:
            def get(self, *args, **kwargs):
                class Response:
                    status_code = 200
                    headers = {"Content-Range": "*/0"}
                    def json(self):
                        return []
                return Response()
        self.assertEqual(self.lookup(Empty(), ["missing"]), {})

    def test_premature_empty_page_fails_closed(self):
        class Partial:
            def __init__(self): self.calls = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                class Response:
                    status_code = 200
                    headers = {"Content-Range": "*/2"}
                    def json(self): return []
                return Response()
        store = Partial()
        with self.assertRaises(RuntimeError):
            self.lookup(store, ["a", "b"])

    def test_missing_or_malformed_range_fails_closed(self):
        for header in ({}, {"Content-Range": "0-0/*"}, {"Content-Range": "0-1/2"}):
            class Bad:
                def get(self, *args, **kwargs):
                    class Response:
                        status_code = 200
                        headers = header
                        def json(self):
                            return [dict(external_id="a", claimed_at=None)]
                    return Response()
            with self.assertRaises(RuntimeError):
                self.lookup(Bad(), ["a", "b"])

    def test_changing_total_fails_closed(self):
        class Changing:
            def __init__(self): self.calls = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                total = 2 if self.calls == 1 else 3
                start = 0 if self.calls == 1 else 1
                class Response:
                    status_code = 200
                    headers = {"Content-Range": f"{start}-{start}/{total}"}
                    def json(self):
                        return [dict(external_id="a" if start == 0 else "b", claimed_at=None)]
                return Response()
        with self.assertRaises(RuntimeError):
            self.lookup(Changing(), ["a", "b"])

    def test_request_and_url_budgets(self):
        ids = [f"{i:040d}" for i in range(251)]
        store = Store(dict.fromkeys(ids))
        self.assertEqual(len(self.lookup(store, ids)), 251)
        self.assertEqual(len(store.calls), 3)
        for url, _ in store.calls:
            self.assertLessEqual(len(url), 6000)
        special = ['quote"comma,paren)', "unicode-é" * 40, "slash\\back"]
        store = Store(dict.fromkeys(special))
        self.assertEqual(set(self.lookup(store, special)), set(special))

    def test_empty_feed_reads_nothing(self):
        store = Store({})
        self.assertEqual(self.lookup(store, []), {})
        self.assertEqual(store.calls, [])

    def test_partial_lookup_failure_is_not_permission_to_write(self):
        store = Store({str(i): None for i in range(5)}, cap=2, fail_from=2)
        with self.assertRaisesRegex(RuntimeError, r"HTTP 503, 3 tries\); no events were written"):
            self.lookup(store, list(store.rows))
        # page 1, then page 2 fails; each retry re-reads the chunk from its first page
        self.assertEqual(len(store.calls), 1 + sync.STATE_READ_TRIES)

    def test_a_blip_is_retried_and_the_whole_chunk_read_again(self):
        # 2026-10-09: six jobs lost the day's writes to one failed read each.
        store = Store({str(i): None for i in range(5)}, cap=2, fail_page=2)
        self.assertEqual(self.lookup(store, list(store.rows)), {str(i): False for i in range(5)})
        offsets = [parse_qs(urlsplit(u).query)["offset"][0] for u, _ in store.calls]
        self.assertEqual(offsets, ["0", "2", "0", "2", "4"], "the retry starts the chunk again")

    def test_missing_ownership_field_and_repeated_page_fail_closed(self):
        for rows in ([{"external_id":"a"}], [{"external_id":"a", "claimed_at":None}]):
            class Bad:
                def get(self, *args, **kwargs):
                    class Response:
                        status_code = 200
                        headers = {}
                        def json(self):
                            return rows
                    return Response()
            with self.assertRaises(RuntimeError):
                self.lookup(Bad(), ["a", "b"])

    def test_failure_never_echoes_credentials(self):
        class Broken:
            def get(self, *args, **kwargs):
                raise OSError("unused-test-key sensitive request URL")
        with self.assertRaises(RuntimeError) as error:
            self.lookup(Broken(), ["a"])
        self.assertNotIn("unused-test-key", str(error.exception))

    def test_main_keeps_claimed_edits_and_only_new_semantics(self):
        rows = [dict(external_id=eid) for eid in ("claimed", "existing", "new")]
        for flags, expected in (([], ["existing", "new"]), (["--only-new"], ["new"])):
            store = Store({"claimed":"2026-09-05", "existing":None})
            with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID":"host", "SUPABASE_URL":"https://db.example",
                                        "SUPABASE_SERVICE_ROLE_KEY":"unused-test-key"}), \
                 patch("sys.argv", ["sync"] + flags), patch("requests.Session", return_value=store), \
                 patch.object(sync, "build_rows", return_value=rows), \
                 patch.object(sync, "load_blocklist", return_value=[]), \
                 patch.object(sync, "upsert", return_value=(len(expected), 0, 0)) as write, \
                 redirect_stdout(io.StringIO()):
                sync.main()
                self.assertEqual([row["external_id"] for row in write.call_args.args[0]], expected)
                self.assertEqual(len(store.calls), 1, "one read supplies both guards")

    def test_main_writes_a_kept_row_with_the_categories_of_its_hidden_copies(self):
        # mapsee_dedupe_events hides a copy behind a kept row and gives the kept
        # row the copy's categories, so the event stays on every door. A refresh
        # that wrote the row's own categories would take it off them until the
        # next dedupe run (a median 4.8 h later over 8 runs). The state read
        # brings each stored row's id; one read of the hidden copies finds the
        # kept rows among them; the rows go out merged, before skip-unchanged.
        rows = [dict(external_id="kept", category="music", categories=None),
                dict(external_id="new", category="music", categories=None)]

        own = "Tickets / info: https://seatgeek.com/buena-vista-social-club-the-musical-tickets/broadway/17465311"
        other = "Tickets / info: https://www.pgparks.com/event_list/get-fit-mobile-fitness-on-the-fly/2026-09-24"

        class Kept(Store):
            def __init__(self, rows, copies_status=200, link=own):
                super().__init__(rows)
                self.copies_status, self.link = copies_status, link

            def get(self, url, **kwargs):
                if "select=id,title,place_name,starts_at,description" in url:
                    # the copy's text: only a copy that would add a category is read
                    self.calls.append((url, kwargs))
                    link = self.link

                    class Text:
                        status_code = 200
                        headers = {}

                        def json(self):
                            return [{"id": "c1", "title": "Buena Vista Social Club - The Musical",
                                     "place_name": "Gerald Schoenfeld Theatre",
                                     "starts_at": "2026-10-16T23:00:00+00:00", "description": link}]
                    return Text()
                if "duplicate_of=not.is.null" in url:
                    self.calls.append((url, kwargs))
                    status = self.copies_status

                    class Copies:
                        status_code = status
                        headers = {}

                        def json(self):
                            return [{"id": "c1", "duplicate_of": "k-uuid", "category": "theater", "categories": None}]
                    return Copies()
                resp = super().get(url, **kwargs)
                for r in resp.json():
                    r["id"] = "k-uuid" if r["external_id"] == "kept" else "other-uuid"
                return resp

        # A copy whose link is another event's page (a parks department's
        # fitness class on a show's copy) carries nothing: its categories are
        # that event's, and a misfile an earlier union put on the row comes off.
        for status, link, want, line in (
                (200, own, ["theater"], "Kept rows: 1 carry the categories"),
                (503, own, None, "::warning::Hidden copies' categories could not be read"),
                (200, other, None, "Upserted 2 events")):
            store = Kept({"kept": None}, copies_status=status, link=link)
            out = io.StringIO()
            with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                        "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key"}), \
                 patch("sys.argv", ["sync"]), patch("requests.Session", return_value=store), \
                 patch.object(sync, "build_rows", return_value=[dict(r) for r in rows]), \
                 patch.object(sync, "load_blocklist", return_value=[]), \
                 patch.object(sync, "upsert", return_value=(2, 0, 0)) as write, redirect_stdout(out):
                sync.main()
            sent = {r["external_id"]: r["categories"] for r in write.call_args.args[0]}
            self.assertEqual(sent, {"kept": want, "new": None}, status)
            self.assertIn(line, out.getvalue())
            if link == other:
                self.assertNotIn("Kept rows:", out.getvalue())
            state = parse_qs(urlsplit(store.calls[0][0]).query)["select"][0].split(",")
            self.assertIn("id", state, "the row's id rides the state read")
            self.assertEqual(sum("duplicate_of=not.is.null" in u for u, _ in store.calls), 1)

    def test_only_new_reads_state_before_geocoding_and_refresh_is_unchanged(self):
        # 2026-09-12: Meetup's final sync geocoded 22,276 of 53,871 rows to write
        # 2,480, because --only-new filtered AFTER build_rows. Under --only-new
        # the one state read now happens first and only the new record reaches
        # the geocoder; a refresh day still geocodes everything, then reads.
        import re
        import tempfile
        ids = ("claimed", "existing", "new")
        recs = [dict(fingerprint=eid, name=f"Event {eid}", source="test",
                     start_utc="2026-10-01T18:00:00Z", start_local="2026-10-01T11:00:00",
                     address=f"{i} Main St", city="Seattle", region="WA")
                for i, eid in enumerate(ids, 1)]
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "store.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"events": recs}, fh)
        every = [(f"{i} Main St", "Seattle", "WA") for i in (1, 2, 3)]
        for flags, geocoded, written, order_wanted in (
                (["--only-new"], every[2:], ["new"], ["state", "geocode"]),
                ([], every, ["existing", "new"], ["geocode", "state"])):
            store = Store({"claimed": "2026-09-05", "existing": None})
            order, sent = [], []
            real_get = store.get

            def get(url, _real=real_get, _order=order, **kwargs):
                _order.append("state")
                return _real(url, **kwargs)
            store.get = get

            def geocode(_session, addrs, _order=order, _sent=sent, **_cache):
                _order.append("geocode")
                _sent.extend(addrs)
                return {a: (47.6, -122.3) for a in addrs}
            out = io.StringIO()
            with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                        "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key",
                                        "SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}), \
                 patch("sys.argv", ["sync", "--store", path] + flags), \
                 patch("requests.Session", return_value=store), \
                 patch.object(sync, "batch_geocode", side_effect=geocode), \
                 patch.object(sync, "load_blocklist", return_value=[]), \
                 patch.object(sync, "upsert", return_value=(len(written), 0, 0)) as write, \
                 redirect_stdout(out):
                sync.main()
            label = " ".join(flags) or "refresh"
            self.assertEqual(sorted(sent), geocoded, label)
            self.assertEqual([row["external_id"] for row in write.call_args.args[0]], written, label)
            self.assertEqual(order, order_wanted, label)
            self.assertEqual(len(store.calls), 1, "one read supplies both guards")
            log = out.getvalue()
            for phase in ("Geocoded", "Import state", "Upsert phase"):
                self.assertRegex(log, re.escape(phase) + r".*\d+\.\ds", f"{label}: {phase} timing")

    def test_only_new_failed_read_stops_before_enriching_geocoding_or_writing(self):
        # Moving the read AHEAD of build_rows must keep it fail-closed: a failed
        # ownership read under --only-new stops the sync before the Spotify
        # pass, the geocoder and the write, not after them.
        import tempfile
        rec = dict(fingerprint="new", name="Event new", source="test",
                   start_utc="2026-10-01T18:00:00Z", start_local="2026-10-01T11:00:00",
                   address="1 Main St", city="Seattle", region="WA")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"events": [rec]}, fh)
            store = Store({"new": None}, fail_from=1)
            with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                        "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key"}), \
                 patch("sys.argv", ["sync", "--store", path, "--only-new"]), \
                 patch("requests.Session", return_value=store), \
                 patch.object(sync, "_enrich_music_links") as enrich, \
                 patch.object(sync, "batch_geocode") as geocode, \
                 patch.object(sync, "upsert") as write, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "no events were written"):
                    sync.main()
            self.assertEqual(len(store.calls), sync.STATE_READ_TRIES)
            enrich.assert_not_called()
            geocode.assert_not_called()
            write.assert_not_called()

    def test_main_cannot_write_after_a_failed_ownership_read(self):
        store = Store({"claimed":"2026-09-05"}, fail_from=1)
        with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID":"host", "SUPABASE_URL":"https://db.example",
                                    "SUPABASE_SERVICE_ROLE_KEY":"unused-test-key"}), \
             patch("sys.argv", ["sync"]), patch("requests.Session", return_value=store), \
             patch.object(sync, "build_rows", return_value=[{"external_id":"claimed"}]), \
             patch.object(sync, "upsert") as write, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "no events were written"):
                sync.main()
            write.assert_not_called()

    def test_dry_run_reads_the_store_once_for_rows_and_cancellation_metadata(self):
        import tempfile
        rec = dict(fingerprint="event", name="Sample Library Storytime", source="test",
                   start_utc="2026-10-20T18:00:00Z", latitude=47.6, longitude=-122.3,
                   venue_name="Sample Library", legacy_fingerprints=["old-event"])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"events": [rec],
                           "tombstones": [{"fingerprint": "gone", "source": "ics:library",
                                           "reason": "STATUS:CANCELLED"}]}, fh)
            real_open = open
            store_reads = []
            out = io.StringIO()

            def counted_open(file, *args, **kwargs):
                if os.path.abspath(file) == os.path.abspath(path):
                    store_reads.append(file)
                return real_open(file, *args, **kwargs)

            with patch("sys.argv", ["sync", "--store", path, "--dry-run"]), \
                 patch("builtins.open", side_effect=counted_open), \
                 redirect_stdout(out):
                sync.main()
        self.assertEqual(len(store_reads), 1,
                         "rows, legacy identities, and cancellation metadata share one snapshot")
        self.assertIn('"title": "Sample Library Storytime"', out.getvalue())
        self.assertIn("1 tombstone(s)", out.getvalue())
        self.assertIn("ics:library x1", out.getvalue())

    def test_live_main_shares_legacy_and_tombstone_snapshot_and_reads_once(self):
        import tempfile
        rec = dict(fingerprint="event", name="Sample Library Storytime", source="test",
                   start_utc="2026-10-20T18:00:00Z", latitude=47.6, longitude=-122.3,
                   venue_name="Sample Library", legacy_fingerprints=["old-event"])
        tombstone = {"fingerprint": "gone", "source": "ics:library", "reason": "STATUS:CANCELLED"}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"events": [rec], "tombstones": [tombstone]}, fh)
            real_open = open
            store_reads = []

            def counted_open(file, *args, **kwargs):
                if os.path.abspath(file) == os.path.abspath(path):
                    store_reads.append(file)
                return real_open(file, *args, **kwargs)

            legacy_seen, canceled = [], []
            store = Store({})
            out = io.StringIO()
            with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                        "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key",
                                        "SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}), \
                 patch("sys.argv", ["sync", "--store", path]), \
                 patch("requests.Session", return_value=store), \
                 patch("builtins.open", side_effect=counted_open), \
                 patch.object(sync, "rekey_legacy",
                              side_effect=lambda _session, _url, _key, pairs:
                                  (legacy_seen.append(pairs) or (set(), set()))), \
                 patch.object(sync, "batch_geocode", return_value={}), \
                 patch.object(sync, "load_blocklist", return_value=[]), \
                 patch.object(sync, "merge_copy_categories", return_value=0), \
                 patch.object(sync, "apply_cancellations",
                              side_effect=lambda _session, _url, _key, targets, _now:
                                  (canceled.append(dict(targets)) or (set(), set(), set()))), \
                 patch.object(sync, "upsert", return_value=(1, 0, 0)), \
                 redirect_stdout(out):
                sync.main()
        self.assertEqual(len(store_reads), 1,
                         "live sync previously opened this metadata-bearing store three times")
        self.assertEqual(legacy_seen, [{"event": ["old-event"]}])
        self.assertEqual(canceled, [{"gone": "ics:library"}],
                         "the production cancellation parser still feeds the expected target")
        self.assertIn("Upserted 1 events", out.getvalue())


class Writable(Store):
    """Store that also answers PATCH, moving or hiding a row as PostgREST would."""
    def __init__(self, rows, fail_patch=False, **kw):
        super().__init__(rows, **kw)
        self.patches, self.hidden, self.fail_patch = [], set(), fail_patch

    def patch(self, url, headers=None, data=None, timeout=None):
        q = parse_qs(urlsplit(url).query)
        old, body = q["external_id"][0][len("eq."):], json.loads(data)
        self.patches.append((old, body))
        failed = self.fail_patch
        if not failed and "external_id" in body:
            self.rows[body["external_id"]] = self.rows.pop(old)
        if not failed and "hidden_at" in body:
            self.hidden.add(old)

        class Response:
            status_code = 503 if failed else 204
        return Response()


class LegacyRekeyTests(unittest.TestCase):
    """A row garbled by the old ics reader is MOVED to its new key, never duplicated."""
    def setUp(self):
        sleeper = patch.object(sync, "_patch_sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def rekey(self, store, pairs):
        return sync.rekey_legacy(store, "https://db.example", "unused-test-key", pairs)

    def quiet(self, *a):
        with redirect_stdout(io.StringIO()):
            return self.rekey(*a)

    def test_a_legacy_row_moves_to_its_new_key(self):
        store = Writable({"old": None, "other": None})
        self.assertEqual(self.quiet(store, {"new": ["old"]}), ({"new"}, set()))
        self.assertEqual(store.patches, [("old", {"external_id": "new"})])
        self.assertEqual(set(store.rows), {"new", "other"})

    def test_a_claimed_row_moves_too_and_keeps_its_claim(self):
        store = Writable({"old": "2026-09-05"})
        self.assertEqual(self.quiet(store, {"new": ["old"]}), ({"new"}, set()))
        self.assertEqual(store.rows, {"new": "2026-09-05"})

    def test_a_taken_new_key_hides_the_legacy_copy_instead(self):
        store = Writable({"old": None, "new": None})
        self.assertEqual(self.quiet(store, {"new": ["old"]}), (set(), set()))
        self.assertEqual([(o, sorted(b)) for o, b in store.patches], [("old", ["hidden_at"])])
        self.assertEqual(store.hidden, {"old"})

    def test_a_claimed_duplicate_is_left_to_its_owner(self):
        store = Writable({"old": "2026-09-05", "new": None})
        self.assertEqual(self.quiet(store, {"new": ["old"]}), (set(), set()))
        self.assertEqual(store.patches, [])

    def test_two_old_keys_for_one_event_move_one_and_hide_the_other(self):
        store = Writable({"old1": None, "old2": None})
        moved, held = self.quiet(store, {"new": ["old1", "old2"]})
        self.assertEqual((moved, held), ({"new"}, set()))
        self.assertEqual(store.hidden, {"old2"})

    def test_a_failed_move_is_held_back_not_inserted(self):
        store = Writable({"old": None}, fail_patch=True)
        self.assertEqual(self.quiet(store, {"new": ["old"]}), (set(), {"new"}))

    def test_a_failed_lookup_holds_every_record_that_has_an_old_key(self):
        store = Writable({"old": None}, fail_from=1)
        self.assertEqual(self.quiet(store, {"new": ["old"], "new2": ["old2"]}), (set(), {"new", "new2"}))
        self.assertEqual(store.patches, [])

    def test_a_lookup_blip_is_ridden_out_and_the_row_still_moves(self):
        store = Writable({"old": None}, fail_page=1)
        self.assertEqual(self.quiet(store, {"new": ["old"]}), ({"new"}, set()))

    def test_nothing_to_move_costs_nothing(self):
        store = Writable({})
        self.assertEqual(self.rekey(store, {}), (set(), set()))
        self.assertEqual(store.calls, [])
        self.assertEqual(self.quiet(store, {"new": ["old"]}), (set(), set()))
        self.assertEqual(len(store.calls), 1)
        self.assertEqual(store.patches, [])

    def test_only_new_rewrites_a_moved_row_and_never_inserts_a_held_one(self):
        rows = [dict(external_id=eid) for eid in ("new", "existing", "fresh")]
        for fail, expected in ((False, ["new", "fresh"]), (True, ["fresh"])):
            store = Writable({"old": None, "existing": None}, fail_patch=fail)
            with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID":"host", "SUPABASE_URL":"https://db.example",
                                        "SUPABASE_SERVICE_ROLE_KEY":"unused-test-key"}), \
                 patch("sys.argv", ["sync", "--only-new"]), patch("requests.Session", return_value=store), \
                 patch.object(sync, "legacy_pairs", return_value={"new": ["old"]}), \
                 patch.object(sync, "build_rows", return_value=[dict(r) for r in rows]), \
                 patch.object(sync, "load_blocklist", return_value=[]), \
                 patch.object(sync, "upsert", return_value=(len(expected), 0, 0)) as write, \
                 redirect_stdout(io.StringIO()):
                sync.main()
            self.assertEqual([r["external_id"] for r in write.call_args.args[0]], expected)


class Clock:
    """A monotonic clock the fake Census moves: a timeout costs its timeout,
    an answer 22 ms an address (civic's measured rate)."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Census:
    """The Census batch endpoint: Match for an address in `places`, No_Match
    for the rest, rows answered in reverse (Census does not keep the order).
    `fail(call, addrs, timeout)` makes that request time out instead, or
    answer the HTTP status it returns. A field ending in a backslash is a
    400 for the whole request, as measured live 2026-10-24."""
    def __init__(self, places=None, fail=None, clock=None):
        self.places = places or {}
        self.fail = fail or (lambda call, addrs, timeout: False)
        self.clock = clock or Clock()
        self.sent, self.timeouts, self.bodies = [], [], []

    def post(self, url, files=None, data=None, timeout=None):
        import csv
        body = files["addressFile"][1]
        rows = list(csv.reader(io.StringIO(body)))
        addrs = [tuple(r[1:4]) for r in rows]
        self.sent.append(addrs)
        self.timeouts.append(timeout)
        self.bodies.append(body)

        class Response:
            status_code = 200
            text = ""
        failed = self.fail(len(self.sent), addrs, timeout)
        if any(f.endswith("\\") for a in addrs for f in a):
            failed = 400
        if failed is True:
            self.clock.t += timeout
            raise TimeoutError("read timed out")     # requests.ReadTimeout in production
        if failed:
            Response.status_code, Response.text = failed, "<p>Malformed input file"
            return Response()
        self.clock.t += 0.022 * len(addrs)
        out = io.StringIO()
        writer = csv.writer(out)
        for r, addr in reversed(list(zip(rows, addrs))):
            if addr in self.places:
                lat, lon = self.places[addr]
                writer.writerow([r[0], ", ".join(addr), "Match", "Exact", "X", f"{lon},{lat}", "1", "L"])
            else:
                writer.writerow([r[0], ", ".join(addr), "No_Match"])
        Response.text = out.getvalue()
        return Response()


def street(i):
    return (f"{i} Main St", "Seattle", "WA")


def at(i, **rec):
    return dict(rec, **dict(zip(("address", "city", "region"), street(i))))


class CensusTests(unittest.TestCase):
    """The Census batch: what is sent, in what chunks, and what a failure moves."""
    NOW = 1_792_800_000                                 # 2026-10-24
    HOUR = 3600

    def setUp(self):
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.cache = os.path.join(folder.name, "census_cache.json")
        path = patch.object(sync, "CENSUS_CACHE_PATH", self.cache)
        path.start()
        self.addCleanup(path.stop)

    def geocode(self, census, addrs, now=None):
        out = io.StringIO()
        with redirect_stdout(out):
            got = sync.batch_geocode(census, addrs, cache_path=self.cache,
                                     now=self.NOW if now is None else now, clock=census.clock)
        return got, out.getvalue()

    def stored(self):
        with open(self.cache, encoding="utf-8") as fh:
            return json.load(fh)

    def seed(self, entries):
        with open(self.cache, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)

    def build(self, census, recs, now=None, held=None):
        """build_rows over `recs`, its Census answered in reverse order (so a
        test knows which chunk an address lands in) at `now`."""
        path = os.path.join(self.folder, "store.json")
        base = dict(source="test", start_utc="2026-11-01T18:00:00Z", start_local="2026-11-01T11:00:00")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"events": [dict(base, **r) for r in recs]}, fh)
        when = self.NOW if now is None else now
        out = io.StringIO()
        with patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}), \
             patch.object(sync, "batch_geocode",
                          side_effect=lambda s, addrs, **kw: self.real(s, sorted(addrs, reverse=True),
                                                                       now=when, clock=census.clock, **kw)), \
             redirect_stdout(out):
            rows = sync.build_rows(path, "host", geo_session=census, census_cache=self.cache,
                                   census_held=held)
        return {r["external_id"]: (r["lat"], r["lon"]) for r in rows}, out.getvalue()

    real = staticmethod(sync.batch_geocode)

    def test_ten_thousand_addresses_go_in_four_chunks_of_at_most_2500(self):
        census = Census()
        got, log = self.geocode(census, [street(i) for i in range(10000)])
        self.assertEqual([len(s) for s in census.sent], [2500] * 4)
        self.assertEqual(census.timeouts, [sync.CENSUS_TIMEOUT] * 4)
        self.assertEqual(sum(len(set(s)) for s in census.sent), 10000, "every address once")
        self.assertEqual(got, {})
        self.assertEqual(log.count("Census chunk "), 4)
        self.assertRegex(log, r"Census chunk 1 \(2500 of 10000\): 2500 addresses, HTTP 200, "
                              r"0 matched in \d+\.\ds")
        self.assertNotIn("::warning::", log)

    def test_a_record_abroad_is_never_sent_and_keeps_its_source_pin(self):
        census = Census(places={("3 Mincing Lane", "London", ""): (37.13, -84.08),     # London, KY
                                street(1): (47.61, -122.33), street(2): (47.62, -122.34),
                                street(3): (47.63, -122.35), street(4): (47.64, -122.36)})
        recs = [dict(fingerprint="gb", name="Tech talk", address="3 Mincing Lane", city="London",
                     country="gb", latitude=51.51, longitude=-0.08),
                dict(fingerprint="au", name="Board games", address="1 George St", city="Sydney",
                     region="NSW", country="AU"),                              # no pin of its own: dropped
                at(1, fingerprint="us", name="Choir", country="us", latitude=47.0, longitude=-122.0),
                at(2, fingerprint="blank", name="Swing"),
                at(3, fingerprint="usa", name="Quiz", country="U.S.A."),
                at(4, fingerprint="named", name="Book club", country="United States")]
        rows, log = self.build(census, recs)
        sent = [a for s in census.sent for a in s]
        self.assertEqual(sorted(sent), [street(i) for i in (1, 2, 3, 4)])
        self.assertEqual(rows["gb"], (51.51, -0.08), "not moved to London, KY")
        self.assertNotIn("au", rows)
        self.assertEqual([rows[k] for k in ("us", "blank", "usa", "named")],
                         [(47.61, -122.33), (47.62, -122.34), (47.63, -122.35), (47.64, -122.36)])
        self.assertIn("Kept 2 non-US rows off the Census batch", log)

    def test_an_answered_address_is_not_sent_again_until_its_ttl(self):
        census = Census(places={street(1): (47.61, -122.33)})
        first, _ = self.geocode(census, [street(1), street(2)])
        self.assertEqual(self.stored(), {"1 Main St|Seattle|WA": [47.61, -122.33, self.NOW],
                                         "2 Main St|Seattle|WA": ["NO_MATCH", self.NOW]})
        again, log = self.geocode(census, [street(1), street(2)], now=self.NOW + 29 * 86400)
        self.assertEqual(len(census.sent), 1, "both answers came from the cache")
        self.assertEqual(again, first)
        self.assertEqual(again, {street(1): (47.61, -122.33)})
        self.assertIn(f"2 answered from {self.cache}", log)
        # A miss is asked again after 30 days; a match is not.
        self.geocode(census, [street(1), street(2)], now=self.NOW + 31 * 86400)
        self.assertEqual(census.sent[1:], [[street(2)]])

    def test_an_entry_nobody_asked_for_in_twice_its_ttl_is_dropped(self):
        old = self.NOW - 61 * 86400
        self.seed({"9 Gone St|Seattle|WA": ["NO_MATCH", old],
                   "7 Held St|Seattle|WA": ["PENDING", old],
                   "8 Kept St|Seattle|WA": [47.6, -122.3, old],
                   "junk": "not an entry"})
        self.geocode(Census(), [street(1)])
        self.assertEqual(set(self.stored()), {"8 Kept St|Seattle|WA", "1 Main St|Seattle|WA"})

    def test_an_unreadable_cache_is_asked_again_and_replaced(self):
        with open(self.cache, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        census = Census(places={street(1): (47.61, -122.33)})
        got, _ = self.geocode(census, [street(1)])
        self.assertEqual(got, {street(1): (47.61, -122.33)})
        self.assertEqual(self.stored(), {"1 Main St|Seattle|WA": [47.61, -122.33, self.NOW]})

    def test_a_failed_chunk_is_retried_once_as_two_halves(self):
        census = Census(fail=lambda call, addrs, timeout: call == 1)
        got, log = self.geocode(census, [street(i) for i in range(2500)])
        self.assertEqual([len(s) for s in census.sent], [2500, 1250, 1250])
        self.assertEqual(census.timeouts, [sync.CENSUS_TIMEOUT, sync.CENSUS_TIMEOUT / 2, sync.CENSUS_TIMEOUT / 2])
        self.assertEqual(len(self.stored()), 2500, "the halves' answers are kept")
        self.assertIn("Census chunk 1 (2500 of 2500): 2500 addresses, TimeoutError, 0 matched in 180.0s", log)
        self.assertNotIn("::warning::", log)

    def test_a_slow_census_goes_on_in_half_chunks(self):
        # Census times out at 2,500 and answers 1,250: once a chunk's halves
        # both answer, the chunks after it are halves too, with half the wait.
        census = Census(fail=lambda call, addrs, timeout: len(addrs) > 1250)
        got, log = self.geocode(census, [street(i) for i in range(10000)])
        self.assertEqual([len(s) for s in census.sent], [2500, 1250, 1250] + [1250] * 6)
        self.assertEqual(census.timeouts[3:], [sync.CENSUS_TIMEOUT / 2] * 6)
        self.assertEqual(len(self.stored()), 10000, "every address answered")
        self.assertEqual(got.held, frozenset())
        self.assertNotIn("::warning::", log)

    def test_failed_requests_spend_at_most_the_budget(self):
        # 2026-10-24's fake: every 2,500 times out, the first half answers and
        # the second times out. Without a budget, 14,500 addresses took 18
        # requests and ~1,780 s; the 9,000 chunks' worst was 2 x 180 s.
        census = Census(fail=lambda call, addrs, timeout: len(addrs) > 1250 or call % 3 == 0)
        got, log = self.geocode(census, [street(i) for i in range(14500)])
        self.assertEqual([len(s) for s in census.sent], [2500, 1250, 1250], "then nothing fits the budget")
        self.assertAlmostEqual(census.clock.t, 180 + 0.022 * 1250 + 90)
        self.assertLessEqual(180 + 90, sync.CENSUS_FAIL_BUDGET)
        self.assertEqual(len(got.held), 14500 - 1250, "unsent or unanswered, and Census answered some")
        self.assertIn("12000 address(es) not sent (failed requests took 270s of 360s", log)

    def test_a_census_that_is_down_costs_one_chunk_and_its_halves(self):
        census = Census(fail=lambda call, addrs, timeout: True)
        got, log = self.geocode(census, [street(i) for i in range(14500)])
        self.assertEqual([len(s) for s in census.sent], [2500, 1250, 1250])
        self.assertEqual(census.clock.t, 2 * sync.CENSUS_TIMEOUT)

    def test_a_failed_chunk_keeps_last_answers_writes_no_answer_and_holds_the_unknown(self):
        stale = self.NOW - 400 * 86400                   # due to be asked again
        self.seed({"1 Main St|Seattle|WA": [47.61, -122.33, stale],
                   "2 Main St|Seattle|WA": ["NO_MATCH", self.NOW - 40 * 86400]})
        before = self.stored()
        # Chunk 1 (4, 5, 6) is answered; chunk 2 (1, 2, 3) and both its halves time out.
        census = Census(places={street(i): (47.0 + i / 100, -122.0) for i in range(1, 7)},
                        fail=lambda call, addrs, timeout: street(1) in addrs or street(3) in addrs)
        recs = [at(i, fingerprint=f"e{i}", name=f"Event {i}", latitude=40.0, longitude=-100.0)
                for i in (4, 5, 6, 1, 2, 3)]
        held = set()
        with patch.object(sync, "CENSUS_CHUNK", 3):
            rows, log = self.build(census, recs, held=held)
        self.assertEqual([len(s) for s in census.sent], [3, 3, 2, 1])
        self.assertEqual(rows["e1"], (47.61, -122.33), "the cached match, not the source's pin")
        self.assertEqual(rows["e2"], (40.0, -100.0), "the cached miss: the source's pin, as stored")
        self.assertEqual(rows["e3"], (40.0, -100.0), "built on the source's pin...")
        self.assertEqual(held, {"e3"}, "...and handed to main(), which holds it if it is stored")
        self.assertEqual([rows[f"e{i}"] for i in (4, 5, 6)], [(47.04, -122.0), (47.05, -122.0), (47.06, -122.0)])
        after = self.stored()
        for key in before:
            self.assertEqual(after[key], before[key], "a failed chunk writes no answer")
        self.assertEqual(after["3 Main St|Seattle|WA"], ["PENDING", self.NOW], "only the hold's mark")
        self.assertIn("::warning::Census geocoder: 1 chunk(s) failed", log)
        self.assertIn("1 keep their cached match, 1 their cached miss, 1 never answered are held", log)
        self.assertIn("1 row(s) at an address whose Census chunk failed", log)

    def test_a_later_sync_on_the_same_file_holds_until_the_hold_lapses(self):
        # 2026-10-24: the US leg held e1..e3; the final sync, on the same file,
        # sent only those three, Census failed them again and "answered nothing
        # this run", so they were written on the source's pin after all.
        places = {street(i): (47.0 + i / 100, -122.0) for i in range(1, 7)}
        recs = [at(i, fingerprint=f"e{i}", name=f"Event {i}", latitude=40.0, longitude=-100.0)
                for i in range(1, 7)]
        first_three = lambda call, addrs, timeout: any(street(i) in addrs for i in (1, 2, 3))
        held = set()
        with patch.object(sync, "CENSUS_CHUNK", 3):
            self.build(Census(places, first_three), recs, held=held)
        self.assertEqual(held, {"e1", "e2", "e3"})
        for hours, still in ((3, {"e1", "e2", "e3"}), (sync.CENSUS_HOLD_HOURS + 1, set())):
            census, held = Census(places, lambda call, addrs, timeout: True), set()
            with patch.object(sync, "CENSUS_CHUNK", 3):
                rows, log = self.build(census, recs, now=self.NOW + hours * self.HOUR, held=held)
            self.assertEqual(census.sent[0], [street(3), street(2), street(1)], f"{hours} h: only those sent")
            self.assertEqual(len(census.sent), 3, "their chunk and its halves")
            self.assertEqual(held, still, f"{hours} h later")
            self.assertEqual([rows[f"e{i}"] for i in (4, 5, 6)], [places[street(i)] for i in (4, 5, 6)])
        self.assertIn(f"3 held for {sync.CENSUS_HOLD_HOURS} h already", log)
        self.assertEqual(self.stored()["1 Main St|Seattle|WA"], ["PENDING", self.NOW], "the first failure")

    def test_a_field_ending_in_a_backslash_is_sent_without_it(self):
        # Measured live 2026-10-24: '400 Broad St\' is HTTP 400 "Malformed input
        # file" for the whole request, and it used to cost its half a hold every run.
        poison = ("400 Broad St\\", "Seattle\\", "WA")
        census = Census(places={("400 Broad St", "Seattle", "WA"): (47.62, -122.35)})
        got, log = self.geocode(census, [poison] + [street(i) for i in range(3000)])
        self.assertEqual([len(s) for s in census.sent], [2500, 501])
        self.assertNotIn("\\", "".join(census.bodies))
        self.assertEqual(got[poison], (47.62, -122.35))
        self.assertIn("400 Broad St\\|Seattle\\|WA", self.stored(), "kept under the address it was asked for")
        self.assertNotIn("::warning::", log)

    def test_a_request_census_refuses_holds_nothing(self):
        # A 4xx is the same answer next time: its addresses keep their last
        # answer or the source's pin, and are never held.
        bad = street(7)
        census = Census(places={street(i): (47.0, -122.0) for i in range(8)},
                        fail=lambda call, addrs, timeout: 400 if bad in addrs else False)
        got, log = self.geocode(census, [street(i) for i in range(8)])
        self.assertEqual([len(s) for s in census.sent], [8, 4, 4])
        self.assertEqual(got.held, frozenset())
        self.assertEqual(set(got), {street(i) for i in range(4)}, "the half without it is answered")
        stored = self.stored()
        self.assertEqual(set(stored), {f"{i} Main St|Seattle|WA" for i in range(4)},
                         "the refused half writes no answer and no hold's mark")
        self.assertIn("4 take their source pin (4 in a request Census refused)", log)

    def test_census_answering_nothing_on_a_cold_cache_holds_nothing(self):
        census = Census(fail=lambda call, addrs, timeout: True)
        recs = [at(i, fingerprint=f"e{i}", name=f"Event {i}", latitude=40.0, longitude=-100.0)
                for i in range(5)]
        held = set()
        with patch.object(sync, "CENSUS_CHUNK", 2):
            rows, log = self.build(census, recs, held=held)
        self.assertEqual([len(s) for s in census.sent], [2, 1, 1], "one chunk and its halves, then stop")
        self.assertEqual(rows, {f"e{i}": (40.0, -100.0) for i in range(5)})
        self.assertEqual(held, set())
        self.assertIn("3 address(es) not sent", log)
        self.assertIn("5 because Census answered nothing and the cache knows nothing", log)
        self.assertFalse(os.path.exists(self.cache), "nothing answered, nothing written")

    def test_nothing_to_send_reads_and_writes_nothing(self):
        census = Census()
        got, log = self.geocode(census, [])
        self.assertEqual((got, log, census.sent), ({}, "", []))
        self.assertFalse(os.path.exists(self.cache))

    def sync_main(self, recs, stored, flags=(), answers=None):
        """main() over `recs` with `stored` rows in the database; batch_geocode
        answers `answers`. Returns the rows written and the log."""
        path = os.path.join(self.folder, "store.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"events": recs}, fh)
        out = io.StringIO()
        with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID": "host", "SUPABASE_URL": "https://db.example",
                                    "SUPABASE_SERVICE_ROLE_KEY": "unused-test-key",
                                    "SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}), \
             patch("sys.argv", ["sync", "--store", path, *flags]), \
             patch("requests.Session", return_value=Store(stored)), \
             patch.object(sync, "batch_geocode", return_value=answers) as geocode, \
             patch.object(sync, "load_blocklist", return_value=[]), \
             patch.object(sync, "merge_copy_categories", return_value=0), \
             patch.object(sync, "upsert", return_value=(1, 0, 0)) as write, \
             redirect_stdout(out):
            sync.main()
        written = {r["external_id"]: (r["lat"], r["lon"]) for r in write.call_args.args[0]}
        return written, out.getvalue(), geocode

    def test_only_a_stored_row_waits_for_census_and_a_new_one_is_written(self):
        # A new row has no pin to keep: holding it kept a same-day Meetup event
        # off the map until the next morning (2026-10-24: 5,000 of 7,500 new
        # rows held when Census failed after its first request).
        answers = sync.CensusAnswers()
        answers.held = frozenset({street(1), street(2)})
        base = dict(source="test", start_utc="2026-11-01T18:00:00Z", latitude=40.0, longitude=-100.0)
        recs = [at(1, fingerprint="stored", name="Stored", **base),
                at(2, fingerprint="new", name="New", **base)]
        for flags in ((), ("--only-new",)):
            if flags:                                   # a stored row an --only-new day still writes
                recs[0]["source_details"] = {"price": "free"}
            written, log, _ = self.sync_main(recs, {"stored": None}, flags, answers)
            self.assertEqual(written, {"new": (40.0, -100.0)}, flags)
            self.assertIn("Census hold: 1 stored row(s) held back; 1 new or moved row(s) written", log)

    def test_only_the_sync_run_keeps_answers(self):
        # A direct call (test_ics_venue_location's fake Census) keeps no file,
        # or its control would be answered from the last run's cache.
        census = Census(places={street(1): (47.61, -122.33)})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(sync.batch_geocode(census, [street(1)]), {street(1): (47.61, -122.33)})
        self.assertFalse(os.path.exists(self.cache))
        # main() hands build_rows the cache path.
        rec = at(1, fingerprint="new", name="Event new", source="test", start_utc="2026-11-01T18:00:00Z")
        written, _, geocode = self.sync_main([rec], {}, answers={street(1): (47.61, -122.33)})
        self.assertEqual(geocode.call_args.kwargs, {"cache_path": self.cache})
        self.assertEqual(written, {"new": (47.61, -122.33)})


def run():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(LookupTests),
                                loader.loadTestsFromTestCase(LegacyRekeyTests),
                                loader.loadTestsFromTestCase(CensusTests)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return len(result.failures) + len(result.errors)


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
