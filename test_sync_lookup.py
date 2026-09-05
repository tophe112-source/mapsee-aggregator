"""Bounded existence/ownership reads. No requests leave this process."""
import json
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import mapsee_supabase_sync as sync


class Store:
    def __init__(self, rows, cap=1000, fail_page=None):
        self.rows = rows
        self.cap = cap
        self.fail_page = fail_page
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
        failed = len(self.calls) == self.fail_page

        class Response:
            status_code = 503 if failed else 200
            headers = {"Content-Range": f"{start}-{start+len(page)-1}/{len(matched)}" if page else f"*/{len(matched)}"}
            def json(self):
                return page
        return Response()


class LookupTests(unittest.TestCase):
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
        store = Store({str(i): None for i in range(5)}, cap=2, fail_page=2)
        with self.assertRaisesRegex(RuntimeError, "no events were written"):
            self.lookup(store, list(store.rows))
        self.assertEqual(len(store.calls), 2)

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

    def test_main_cannot_write_after_a_failed_ownership_read(self):
        store = Store({"claimed":"2026-09-05"}, fail_page=1)
        with patch.dict(os.environ, {"MAPSEE_HOST_PROFILE_ID":"host", "SUPABASE_URL":"https://db.example",
                                    "SUPABASE_SERVICE_ROLE_KEY":"unused-test-key"}), \
             patch("sys.argv", ["sync"]), patch("requests.Session", return_value=store), \
             patch.object(sync, "build_rows", return_value=[{"external_id":"claimed"}]), \
             patch.object(sync, "upsert") as write, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "no events were written"):
                sync.main()
            write.assert_not_called()


def run():
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LookupTests))
    return len(result.failures) + len(result.errors)


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
