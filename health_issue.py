#!/usr/bin/env python3
"""Keep source-health notifications below GitHub's issue/comment body limit.

The full report stays in the workflow log, summary and report artifact. Only
the notification is shortened, and both create and comment use the same file.
Stdlib only: an alarm should not need another dependency to format its message.
"""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path


# GitHub accepts 65,536 characters. A UTF-8 byte budget is deliberately stricter
# and also covers clients that count UTF-16 units rather than Unicode points.
MAX_BODY_BYTES = 60_000
MISSING_REPORT = (
    "**The check exited non-zero without writing a report.**\n\n"
    "That is a fault in the check itself, not a finding about any source. "
    "See the run log for the cause."
)


def issue_body(report, status, run_url, checked_at):
    """Return one bounded body, reserving space for its run link and notice."""
    if status == "2":
        intro = (
            "**The health check could not run.** It did not evaluate the sources, "
            "so this is a fault in the check, not evidence of an ingest outage."
        )
    else:
        intro = (
            "The health check found sources that need attention. "
            "Individual feed findings do not mean the database is down."
        )
    report = report.strip() or MISSING_REPORT
    footer = (
        f"\n\n---\n_Checked by [source-health]({run_url}) on {checked_at}. "
        "This issue closes itself once the condition clears._\n"
    )
    prefix = intro + "\n\n"
    full = prefix + report + footer
    if len(full.encode("utf-8")) <= MAX_BODY_BYTES:
        return full

    notice = (
        "\n\n**Report shortened to fit GitHub's issue/comment limit.** "
        f"The complete report is in the [workflow summary and logs]({run_url}); "
        "the `source-health-report` artifact is also available there if its upload succeeded."
    )
    budget = MAX_BODY_BYTES - len((prefix + notice + footer).encode("utf-8"))
    # Run metadata is short and controlled by Actions, not by a publisher.
    if budget < 0:
        raise ValueError("Workflow metadata exceeds the notification budget")
    preview = report.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    # Health rows are one line each. Do not publish a chopped URL/table row.
    # A single unreasonably long diagnostic still gets a bounded preview.
    if "\n" in preview:
        preview = preview.rsplit("\n", 1)[0]
    return prefix + preview.rstrip() + notice + footer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="health_report.md")
    parser.add_argument("--output", default="issue_body.md")
    parser.add_argument("--status", choices=("1", "2"), required=True)
    args = parser.parse_args(argv)
    try:
        report = Path(args.report).read_text(encoding="utf-8", errors="replace")
    except OSError:
        report = MISSING_REPORT
    run_url = (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
               f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = issue_body(report, args.status, run_url, checked_at)
    Path(args.output).write_text(body, encoding="utf-8", newline="\n")
    print(f"Notification: {len(body.encode('utf-8')):,} bytes; full report retained separately")


if __name__ == "__main__":
    main()
