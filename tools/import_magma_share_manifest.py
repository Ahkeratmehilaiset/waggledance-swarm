# SPDX-License-Identifier: BUSL-1.1
"""Verify a MAGMA share manifest as no-authority replay metadata."""
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

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.share_manifest import (  # noqa: E402
    DEFAULT_IMPORT_MAX_AGE_HOURS,
    IMPORT_HANDOFF_DECISIONS,
    PURPOSES,
    build_magma_share_manifest_import_report,
    write_magma_share_import_peer_review_handoff,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share-manifest", required=True, type=Path)
    parser.add_argument(
        "--source-manifest",
        required=True,
        type=Path,
        help=(
            "Local receipt-bundle manifest used only to verify digest/context "
            "references. Payload files are not copied or imported."
        ),
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=DEFAULT_IMPORT_MAX_AGE_HOURS,
        help="Reject share manifests older than this age.",
    )
    parser.add_argument(
        "--expected-share-id",
        default=None,
        help="Optional exact share id expected by the receiving review context.",
    )
    parser.add_argument(
        "--expected-purpose",
        default=None,
        choices=sorted(PURPOSES),
        help="Optional exact share purpose expected by the receiving review context.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-28T09:00:00Z.",
    )
    parser.add_argument(
        "--peer-review-handoff-out",
        type=Path,
        default=None,
        help=(
            "Optional local JSON artifact recording an operator-owned "
            "peer-review import decision. No runtime authority is granted."
        ),
    )
    parser.add_argument(
        "--operator-decision-id",
        default=None,
        help=(
            "Required with --peer-review-handoff-out. Checked for ref shape "
            "but redacted from the handoff artifact."
        ),
    )
    parser.add_argument(
        "--operator-agent",
        default=None,
        help="Required with --peer-review-handoff-out.",
    )
    parser.add_argument(
        "--bridge-event-ref",
        default=None,
        help="Required with --peer-review-handoff-out.",
    )
    parser.add_argument(
        "--import-decision",
        choices=sorted(IMPORT_HANDOFF_DECISIONS),
        default="accepted_for_peer_review",
        help="Operator import decision recorded in the peer-review handoff.",
    )
    parser.add_argument(
        "--decision-reason-ref",
        default="reason:operator_peer_review_handoff",
        help="Stable non-sensitive reason ref recorded in the handoff.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.peer_review_handoff_out is not None:
        missing = [
            flag
            for flag, value in (
                ("--operator-decision-id", args.operator_decision_id),
                ("--operator-agent", args.operator_agent),
                ("--bridge-event-ref", args.bridge_event_ref),
            )
            if not value
        ]
        if missing:
            parser.error(
                "--peer-review-handoff-out requires "
                + ", ".join(missing)
            )

    now_utc = _parse_utc(args.now) if args.now else None
    try:
        report = build_magma_share_manifest_import_report(
            share_manifest_path=args.share_manifest,
            source_manifest_path=args.source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=now_utc,
            max_age_hours=args.max_age_hours,
            expected_share_id=args.expected_share_id,
            expected_purpose=args.expected_purpose,
        )
        handoff = None
        if args.peer_review_handoff_out is not None:
            handoff = write_magma_share_import_peer_review_handoff(
                import_report=report,
                out_path=args.peer_review_handoff_out,
                operator_decision_id=args.operator_decision_id,
                operator_agent_id=args.operator_agent,
                bridge_event_ref=args.bridge_event_ref,
                import_decision=args.import_decision,
                decision_reason_ref=args.decision_reason_ref,
                now_utc=now_utc,
            )
    except (OSError, ValueError) as exc:
        print(f"magma share manifest import FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        if handoff is not None:
            report = dict(report)
            report["peer_review_handoff"] = handoff
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        suffix = ""
        if handoff is not None:
            suffix = "; peer-review handoff recorded"
        print(
            "magma share manifest import OK: "
            f"{report['replay_plan']['entry_count']} replay metadata entries"
            f"{suffix}"
        )
    return 0


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise ValueError("--now must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid --now timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now must be in UTC")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
