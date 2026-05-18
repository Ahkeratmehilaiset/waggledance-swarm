# SPDX-License-Identifier: BUSL-1.1
"""CLI wrapper for the bridge work-queue Python API."""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import (  # noqa: E402
    DEFAULT_BRIDGE_ROOT,
    WorkQueueError,
    check_scope_overlap,
    claim_task,
    detect_stale_claims,
    heartbeat,
    list_claims,
    release_task,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge work-queue CLI.")
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim")
    claim.add_argument("--agent", required=True)
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--summary", required=True)
    claim.add_argument("--mode", choices=["read-only", "write"], default="read-only")
    claim.add_argument("--write-scope", action="append", default=[])
    claim.add_argument("--run-id", default="")
    claim.add_argument("--lease-seconds", type=int, default=900)
    claim.add_argument("--force", action="store_true")

    release = sub.add_parser("release")
    release.add_argument("--agent", required=True)
    release.add_argument("--task-id", required=True)
    release.add_argument(
        "--status",
        choices=["done", "blocked", "abandoned", "handoff"],
        default="done",
    )
    release.add_argument("--message", default="")

    beat = sub.add_parser("heartbeat")
    beat.add_argument("--agent", required=True)
    beat.add_argument("--task-id", required=True)
    beat.add_argument("--lease-seconds", type=int, default=None)

    sub.add_parser("list")

    stale = sub.add_parser("stale")
    stale.add_argument("--max-age-seconds", type=int, default=12 * 60 * 60)

    overlap = sub.add_parser("check-overlap")
    overlap.add_argument("--write-scope", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _dispatch(args)
    except WorkQueueError as exc:
        report = {
            "ok": False,
            "decision": "work_queue_error",
            "errors": [str(exc)],
        }
        exit_code = 2
    else:
        exit_code = 0
    print(json.dumps(report, sort_keys=True))
    return exit_code


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    bridge_root = Path(args.bridge_root)
    if args.command == "claim":
        claim = claim_task(
            agent=args.agent,
            task_id=args.task_id,
            summary=args.summary,
            mode=args.mode,
            write_scope=args.write_scope,
            run_id=args.run_id,
            lease_seconds=args.lease_seconds,
            bridge_root=bridge_root,
            force=args.force,
        )
        return {"ok": True, "decision": "claimed", "claim": _to_jsonable(claim)}
    if args.command == "release":
        record = release_task(
            agent=args.agent,
            task_id=args.task_id,
            release_status=args.status,
            release_message=args.message,
            bridge_root=bridge_root,
        )
        return {"ok": True, "decision": "released", "release": _to_jsonable(record)}
    if args.command == "heartbeat":
        claim = heartbeat(
            agent=args.agent,
            task_id=args.task_id,
            bridge_root=bridge_root,
            lease_seconds=args.lease_seconds,
        )
        return {"ok": True, "decision": "heartbeat", "claim": _to_jsonable(claim)}
    if args.command == "list":
        return {
            "ok": True,
            "decision": "listed",
            "claims": [_to_jsonable(claim) for claim in list_claims(bridge_root=bridge_root)],
        }
    if args.command == "stale":
        return {
            "ok": True,
            "decision": "stale_claims",
            "claims": [
                _to_jsonable(claim)
                for claim in detect_stale_claims(
                    bridge_root=bridge_root,
                    max_age_seconds=args.max_age_seconds,
                )
            ],
        }
    if args.command == "check-overlap":
        return {
            "ok": True,
            "decision": "scope_overlap",
            "claims": [
                _to_jsonable(claim)
                for claim in check_scope_overlap(
                    bridge_root=bridge_root,
                    write_scope=args.write_scope,
                )
            ],
        }
    raise WorkQueueError(f"unsupported command: {args.command}")


def _to_jsonable(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        data = asdict(value)
        return {
            key: list(item) if isinstance(item, tuple) else item
            for key, item in data.items()
        }
    return value


if __name__ == "__main__":
    raise SystemExit(main())
