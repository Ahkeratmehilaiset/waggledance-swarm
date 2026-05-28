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
    PURPOSES,
    build_magma_share_manifest_import_report,
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
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_magma_share_manifest_import_report(
            share_manifest_path=args.share_manifest,
            source_manifest_path=args.source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=_parse_utc(args.now) if args.now else None,
            max_age_hours=args.max_age_hours,
            expected_share_id=args.expected_share_id,
            expected_purpose=args.expected_purpose,
        )
    except (OSError, ValueError) as exc:
        print(f"magma share manifest import FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "magma share manifest import OK: "
            f"{report['replay_plan']['entry_count']} replay metadata entries"
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
