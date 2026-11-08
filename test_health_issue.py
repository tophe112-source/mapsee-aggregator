"""Offline regressions for the notifier that failed with 262 findings."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from health_issue import MAX_BODY_BYTES, MISSING_REPORT, issue_body

ROOT = Path(__file__).resolve().parent
RUN_URL = "https://github.com/tophe112-source/mapsee-aggregator/actions/runs/42495413069"
CHECKED_AT = "2026-11-08 15:33 UTC"


class HealthIssueTests(unittest.TestCase):
    def body(self, report, status="1"):
        body = issue_body(report, status, RUN_URL, CHECKED_AT)
        self.assertLessEqual(len(body.encode("utf-8")), MAX_BODY_BYTES)
        self.assertLess(len(body), 65_536)
        self.assertIn(RUN_URL, body)
        self.assertIn(CHECKED_AT, body)
        return body

    def test_small_report_is_preserved(self):
        report = "### 1 source(s) need attention\n\n| FEED_DOWN | City | HTTP 404 |"
        body = self.body(report)
        self.assertIn(report, body)
        self.assertNotIn("Report shortened", body)

    def test_large_report_keeps_totals_complete_rows_and_run_link(self):
        rows = "".join(f"| FEED_DOWN | City {i} | {'failed URL ' * 30}|\n" for i in range(262))
        report = "### 262 source(s) need attention\n\n" + rows
        self.assertGreater(len(report), 65_536)  # the fixture reaches the original failure
        body = self.body(report)
        self.assertIn("262 source(s) need attention", body)
        self.assertIn("Report shortened", body)
        self.assertIn("source-health-report", body)
        visible_rows = [line for line in body.splitlines() if line.startswith("| FEED_DOWN")]
        self.assertGreater(len(visible_rows), 0)
        self.assertLess(len(visible_rows), 262)
        self.assertTrue(all(line.endswith("|") for line in visible_rows))

    def test_multibyte_and_astral_names_respect_the_byte_budget(self):
        body = self.body("### 2000 findings\n" + "| 地域 🎭 café | unavailable |\n" * 2000)
        self.assertIn("Report shortened", body)
        self.assertNotIn("\ufffd", body)

    def test_a_single_oversized_diagnostic_cannot_prevent_an_alert(self):
        body = self.body("x" * 200_000, "2")
        self.assertIn("The health check could not run", body)
        self.assertIn("Report shortened", body)

    def test_empty_report_keeps_missing_report_fallback(self):
        self.assertIn(MISSING_REPORT, self.body("  \n", "2"))

    def test_exact_budget_boundary(self):
        overhead = len(self.body("x").encode("utf-8")) - 1
        body = self.body("x" * (MAX_BODY_BYTES - overhead))
        self.assertEqual(len(body.encode("utf-8")), MAX_BODY_BYTES)
        self.assertNotIn("Report shortened", body)
        self.assertIn("Report shortened", self.body("x" * (MAX_BODY_BYTES - overhead + 1)))

    def test_cli_handles_missing_file_without_modifying_the_full_report(self):
        env = dict(os.environ, GITHUB_SERVER_URL="https://github.com",
                   GITHUB_REPOSITORY="tophe112-source/mapsee-aggregator",
                   GITHUB_RUN_ID="42495413069", PYTHONUTF8="1")
        with tempfile.TemporaryDirectory() as tmp:
            for report in (None, "", "地域 🎭\n" * 20_000):
                path = Path(tmp, "health_report.md")
                if report is not None:
                    path.write_text(report, encoding="utf-8")
                out = subprocess.run([sys.executable, str(ROOT / "health_issue.py"), "--status", "2"],
                                     cwd=tmp, env=env, capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                body = Path(tmp, "issue_body.md").read_text(encoding="utf-8")
                self.assertLessEqual(len(body.encode("utf-8")), MAX_BODY_BYTES)
                if report is not None:
                    self.assertEqual(path.read_text(encoding="utf-8"), report)
                else:
                    self.assertFalse(path.exists())
                self.assertIn(RUN_URL, body)

    def test_workflow_preserves_report_before_notification_without_blocking_it(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/source-health.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["check"]["steps"]
        artifact = next(s for s in steps if s.get("uses") == "actions/upload-artifact@v4")
        notify = next(s for s in steps if s.get("name") == "Open or update the health issue")
        self.assertLess(steps.index(artifact), steps.index(notify))
        self.assertTrue(artifact["continue-on-error"])
        self.assertEqual(artifact["with"]["name"], "source-health-report")
        self.assertEqual(artifact["with"]["path"], "health_report.md")
        self.assertEqual(artifact["with"]["if-no-files-found"], "warn")
        self.assertIn('python health_issue.py --status "$STATUS"', notify["run"])
        self.assertEqual(notify["run"].count("--body-file issue_body.md"), 2)
        gate = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        self.assertIn("python test_health_issue.py", gate)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_checker_crash_routes_to_check_failure_not_feed_failure(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/source-health.yml").read_text(encoding="utf-8"))
        script = next(s["run"] for s in workflow["jobs"]["check"]["steps"] if s.get("id") == "health")
        with tempfile.TemporaryDirectory() as tmp:
            for status, expected in ((0, "0"), (1, "1"), (2, "2"), (7, "2"), (127, "2")):
                env = dict(os.environ, GITHUB_OUTPUT="status.txt")
                out = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                      f'python() {{ return {status}; }}\n' + script],
                                     cwd=tmp, env=env, capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(Path(tmp, "status.txt").read_text().splitlines()[-1], f"status={expected}")

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_real_notifier_script_creates_and_comments_with_bounded_body(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/source-health.yml").read_text(encoding="utf-8"))
        script = next(s["run"] for s in workflow["jobs"]["check"]["steps"]
                      if s.get("name") == "Open or update the health issue")
        # Shell functions fake only the transport. Run the workflow and formatter
        # unchanged, so both createIssue and addComment receive the actual body.
        stub = '''
        gh() {
          if [ "$1 $2" = "issue list" ]; then echo "$EXISTING_TEST";
          elif [ "$1 $2" = "label create" ]; then return 0;
          elif [ "$1 $2" = "issue create" ] || [ "$1 $2" = "issue comment" ]; then
            printf '%s\\n' "$*" > gh_call.txt
            cp issue_body.md sent_body.md
          else return 9; fi
        }
        '''
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(ROOT / "health_issue.py", tmp)
            full = "### 262 source(s) need attention\n" + "| FEED_DOWN | 地域 🎭 | timeout |\n" * 3000
            Path(tmp, "health_report.md").write_text(full, encoding="utf-8")
            # On Windows Git Bash does not automatically prepend the Python venv.
            python = Path(sys.executable).as_posix()
            stub = f'python() {{ "{python}" "$@"; }}\n' + stub
            for existing, expected in (("", "issue create"), ("32", "issue comment 32")):
                env = dict(os.environ, STATUS="1", EXISTING_TEST=existing, PYTHONUTF8="1",
                           GITHUB_SERVER_URL="https://github.com",
                           GITHUB_REPOSITORY="tophe112-source/mapsee-aggregator", GITHUB_RUN_ID="42495413069")
                out = subprocess.run(["bash", "--noprofile", "--norc", "-c", stub + script],
                                     cwd=tmp, env=env, capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertTrue(Path(tmp, "gh_call.txt").read_text().startswith(expected))
                sent = Path(tmp, "sent_body.md").read_text(encoding="utf-8")
                self.assertLessEqual(len(sent.encode("utf-8")), MAX_BODY_BYTES)
                self.assertIn(RUN_URL, sent)
                self.assertEqual(Path(tmp, "health_report.md").read_text(encoding="utf-8"), full)

            # Even a broken/missing Python or formatter must reach gh once.
            failed_formatter = stub + '\npython() { return 127; }\n' + script
            out = subprocess.run(["bash", "--noprofile", "--norc", "-c", failed_formatter],
                                 cwd=tmp, env=dict(env, STATUS="2"), capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            sent = Path(tmp, "sent_body.md").read_text(encoding="utf-8")
            self.assertIn("status 2", sent)
            self.assertIn("formatter failed", sent)
            self.assertIn(RUN_URL, sent)
            self.assertLess(len(sent.encode("utf-8")), MAX_BODY_BYTES)


if __name__ == "__main__":
    unittest.main()
