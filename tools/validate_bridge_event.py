# SPDX-License-Identifier: BUSL-1.1
"""Validate agent bridge events.jsonl against the v1 bridge event schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import (
    BridgeEventValidationResult,
    validate_event_file,
)


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate agent bridge JSONL events.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS_PATH,
        help="Path to bridge events.jsonl.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Validate only the last N non-empty physical lines.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=20,
        help="Maximum validation issues to include in output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.events.exists():
        result = BridgeEventValidationResult(
            schema_version="agent-bridge-event.v1",
            checked=0,
            valid=0,
            invalid=1,
            issues=(),
        )
        _print_result(result, json_output=args.json, missing_path=args.events)
        return 1

    result = validate_event_file(
        args.events,
        tail=args.tail,
        max_errors=args.max_errors,
    )
    _print_result(result, json_output=args.json)
    return 0 if result.ok else 1


def _print_result(
    result: BridgeEventValidationResult,
    *,
    json_output: bool,
    missing_path: Path | None = None,
) -> None:
    payload = result.to_dict()
    if missing_path is not None:
        payload["missing_path"] = str(missing_path)
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return

    if result.ok:
        print(
            f"bridge event schema OK: {result.valid}/{result.checked} valid "
            f"({result.schema_version})"
        )
        return

    if missing_path is not None:
        print(f"bridge event schema FAILED: missing {missing_path}", file=sys.stderr)
        return

    print(
        f"bridge event schema FAILED: {result.invalid}/{result.checked} invalid "
        f"({result.schema_version})",
        file=sys.stderr,
    )
    for issue in result.issues:
        print(f"line {issue.line_no}: {issue.error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
