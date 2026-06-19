#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a path-free hex shadow-subdivision template-index verifier summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hex_shadow_subdivision_replay import (  # noqa: E402
    _bridge_template_index_entry_verification_summary_error_report,
    _load_bridge_template_index_entry_verification_report,
    _parse_utc,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-entry-verification-json",
        "--verification-json",
        "--summary-bridge-event-template-index-entry-verification-json",
        dest="index_entry_verification_json",
        required=True,
        type=Path,
        help="Local verifier report JSON to summarize without appending it.",
    )
    parser.add_argument(
        "--reviewer-agent",
        default="codex-tools-1",
        help="Reviewer agent id to record as context only.",
    )
    parser.add_argument(
        "--handoff-ref",
        default="hex-shadow-subdivision-replay-verifier-summary",
        help="Safe handoff reference to include as context only.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-31T13:15:00Z.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON. Present for explicitness; JSON is the only output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    verification_report, failure = _load_bridge_template_index_entry_verification_report(
        args.index_entry_verification_json
    )
    if failure is not None:
        summary = _bridge_template_index_entry_verification_summary_error_report(
            failure
        )
    else:
        try:
            now_utc = _parse_utc(args.now) if args.now else None
        except ValueError:
            summary = _bridge_template_index_entry_verification_summary_error_report(
                "now_utc_invalid"
            )
        else:
            summary = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary(
                verification_report or {},
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
                now_utc=now_utc,
            )

    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return 0 if summary.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
