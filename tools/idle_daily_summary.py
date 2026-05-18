# SPDX-License-Identifier: BUSL-1.1
"""Daily summary CLI for autonomous idle-protocol merges.

Reads ``.agent-bridge/shared/events.jsonl`` and renders a markdown handoff to
``docs/handoffs/idle_auto_merges_{YYYY-MM-DD}.md`` describing all autonomous
merges of the requested UTC day, plus pending DRAFT pull requests held for
operator review.

This tool is read-only with respect to the bridge stream and git history.
``--emit`` is required to write the handoff file; dry-run prints to stdout
instead.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.idle_daily_summary import (  # noqa: E402
    DEFAULT_DAILY_QUOTA,
    DEFAULT_EVENTS_PATH,
    DEFAULT_HANDOFF_DIR,
    SummaryPrivacyError,
    build_daily_summary,
    read_bridge_events,
    render_summary_markdown,
    today_utc_iso,
    write_summary_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the autonomous-merge daily summary handoff.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument(
        "--utc-date",
        default=None,
        help="UTC calendar date to summarise, e.g. 2026-05-18. Defaults to today.",
    )
    parser.add_argument(
        "--handoff-dir",
        type=Path,
        default=DEFAULT_HANDOFF_DIR,
        help="Directory under which to write the markdown summary.",
    )
    parser.add_argument(
        "--daily-quota",
        type=int,
        default=DEFAULT_DAILY_QUOTA,
        help="Daily auto-merge quota (rate-limit ceiling).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--emit",
        "--apply",
        dest="emit",
        action="store_true",
        help="Write the summary file. Default is dry-run that prints to stdout.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Render to stdout without writing the handoff file. This is the default.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    utc_date = args.utc_date or today_utc_iso()

    if not _validate_utc_date(utc_date):
        print(f"daily summary FAILED: invalid UTC date {utc_date!r}", file=sys.stderr)
        return 2

    events = read_bridge_events(args.events)
    try:
        summary = build_daily_summary(
            utc_date=utc_date,
            events=events,
            daily_quota=args.daily_quota,
        )
        markdown = render_summary_markdown(summary)
    except SummaryPrivacyError as exc:
        report = {
            "decision": "privacy_marker_refused",
            "error": str(exc),
            "exit_code": 2,
        }
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"daily summary FAILED: {exc}", file=sys.stderr)
        return 2

    if args.emit:
        out_path = write_summary_file(summary, args.handoff_dir)
        report = {
            "decision": "emitted",
            "utc_date": utc_date,
            "auto_merge_count": summary.quota_used,
            "pending_draft_count": len(summary.pending_drafts),
            "handoff_path": str(out_path),
        }
    else:
        report = {
            "decision": "dry_run",
            "utc_date": utc_date,
            "auto_merge_count": summary.quota_used,
            "pending_draft_count": len(summary.pending_drafts),
            "handoff_path": None,
            "preview_markdown": markdown,
        }

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        print(markdown)
    return 0


def _validate_utc_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return True
    except (ValueError, TypeError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
