# SPDX-License-Identifier: BUSL-1.1
"""Export a payload-free MAGMA share manifest from a verified receipt bundle."""
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
    PRODUCER_ROLES,
    PURPOSES,
    write_magma_share_manifest_export,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="New output directory. It must not already exist.",
    )
    parser.add_argument(
        "--operator-approval-id",
        required=True,
        help="Operator approval reference required for this local export.",
    )
    parser.add_argument("--share-id", required=True)
    parser.add_argument("--producer-agent", required=True)
    parser.add_argument(
        "--producer-role",
        required=True,
        choices=sorted(PRODUCER_ROLES),
    )
    parser.add_argument("--bridge-event-ref", required=True)
    parser.add_argument("--purpose", required=True, choices=sorted(PURPOSES))
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-28T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = write_magma_share_manifest_export(
            source_manifest_path=args.source_manifest,
            out_dir=args.out_dir,
            operator_approval_id=args.operator_approval_id,
            verify_source_manifest=verify_manifest,
            share_id=args.share_id,
            producer_agent_id=args.producer_agent,
            producer_role=args.producer_role,
            bridge_event_ref=args.bridge_event_ref,
            purpose=args.purpose,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"magma share manifest export FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"magma share manifest export OK: {report['share_manifest']}")
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
