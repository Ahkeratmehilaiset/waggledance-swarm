# SPDX-License-Identifier: BUSL-1.1
"""CLI wrapper for the bridge work-queue Python API."""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import (  # noqa: E402
    WorkQueueError,
    check_scope_overlap,
    claim_task,
    detect_stale_claims,
    heartbeat,
    list_claims,
    release_task,
    resolve_bridge_root,
)
from tools.bridge_v2_queue_transactions import (  # noqa: E402
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    QueueTransactionError,
    mutex_name,
)

# RCO2 S2 (Lead 0b33855f): the writer commands take the v2 runtime-root mutex first, the same Windows named
# mutex (mutex_name(root), NamedMutexPort) every v2 writer takes first, so a v2 participant holding it excludes
# them: the command waits LOCK_TIMEOUT_SECONDS and then refuses before any claim or done file is touched.
# tools/work_queue_sweep_stale.py --apply takes the same _root_mutex. The legacy PowerShell writers take the same
# kernel object through Enter-BridgeQueueRootMutex (.agent-bridge/bin/ClaimLeaseHeartbeat.ps1, which loads the twin
# BridgeV2QueueMutex.ps1) in the S2 PowerShell half (fable-5 1764254b), composed with this one. A static inventory
# (2026-10-01) found no other production caller of the core writers. Reads (list, stale, check-overlap) take no
# lock. The mutex exists only on Windows; elsewhere nothing is excluded. A refusal exits by its source (fable-5 B1):
# 2 when the --bridge-root is unusable (the v2 canonical-root rule), 1 for every other refusal.
WRITER_COMMANDS = ("claim", "release", "heartbeat")
LOCK_TIMEOUT_SECONDS = DEFAULT_LOCK_TIMEOUT_SECONDS
MUTEX_REFUSAL = "runtime-root mutex: "   # the prefix of every refusal: _exit_code_for_error reads it, not the words


def _root_mutex(bridge_root: Path) -> Any:
    """Hold ``bridge_root``'s v2 runtime-root mutex around one writer command (Windows only). A busy, abandoned or
    unusable mutex raises QueueTransactionError before the command runs; the port releases it on every path and
    re-raises a primary error from the command unchanged."""
    if os.name != "nt":
        return contextlib.nullcontext()
    from tools.bridge_v2_queue_ports_windows import NamedMutexPort
    return NamedMutexPort().hold(mutex_name(bridge_root), LOCK_TIMEOUT_SECONDS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge work-queue CLI.")
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to the runtime .agent-bridge directory. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT / AGENT_BRIDGE_ROOT when set, then "
            "repo-local .agent-bridge."
        ),
    )
    parser.add_argument("--json", action="store_true")
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
        exit_code = _exit_code_for_error(str(exc))
    else:
        exit_code = int(report.pop("exit_code", 0))
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    bridge_root = resolve_bridge_root(args.bridge_root)
    if args.command not in WRITER_COMMANDS:
        return _run(args, bridge_root)
    try:
        with _root_mutex(bridge_root):
            return _run(args, bridge_root)
    except QueueTransactionError as exc:     # the mutex refused (busy, abandoned, unusable root): nothing was written
        raise WorkQueueError(MUTEX_REFUSAL + str(exc)) from exc


def _run(args: argparse.Namespace, bridge_root: Path) -> dict[str, Any]:
    if args.command == "claim":
        claim = claim_task(
            agent=args.agent,
            task_id=args.task_id,
            summary=args.summary,
            mode=args.mode,
            write_scope=_normalize_write_scope(args.write_scope),
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
        stale_claims = [
            _to_jsonable(claim)
            for claim in detect_stale_claims(
                bridge_root=bridge_root,
                max_age_seconds=args.max_age_seconds,
            )
        ]
        return {
            "ok": True,
            "decision": "stale_claims",
            "claims": stale_claims,
            "exit_code": 3 if stale_claims else 0,
        }
    if args.command == "check-overlap":
        return {
            "ok": True,
            "decision": "scope_overlap",
            "claims": [
                _to_jsonable(claim)
                for claim in check_scope_overlap(
                    bridge_root=bridge_root,
                    write_scope=_normalize_write_scope(args.write_scope),
                )
            ],
        }
    raise WorkQueueError(f"unsupported command: {args.command}")


def _normalize_write_scope(values: Sequence[str]) -> list[str]:
    scope: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in str(value).split(","):
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                scope.append(normalized)
    return scope


def _to_jsonable(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        data = asdict(value)
        return {
            key: list(item) if isinstance(item, tuple) else item
            for key, item in data.items()
        }
    return value


def _exit_code_for_error(message: str) -> int:
    lowered = message.lower()
    # A runtime-root mutex refusal exits by its source, never by its words (fable-5 B1): an unusable --bridge-root
    # (the v2 canonical-root rule, "runtime root: ...") is an input error, 2; every other refusal (busy, abandoned, a
    # creation failure that may quote a policy literal such as "... is required") is 1.
    if lowered.startswith(MUTEX_REFUSAL + "runtime root:"):
        return 2
    if lowered.startswith(MUTEX_REFUSAL):
        return 1
    invalid_markers = (
        "invalid",
        "require",
        "required",
        "must ",
        "positive",
        "does not produce",
    )
    if any(marker in lowered for marker in invalid_markers):
        return 2
    return 1


def _print_human(report: dict[str, Any]) -> None:
    print(report.get("decision", "unknown"))
    if not report.get("ok", False):
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    claims = report.get("claims")
    if isinstance(claims, list):
        print(f"claims: {len(claims)}")
    claim = report.get("claim")
    if isinstance(claim, dict):
        print(f"task_id: {claim.get('task_id', '')}")
    release = report.get("release")
    if isinstance(release, dict):
        print(f"task_id: {release.get('task_id', '')}")


if __name__ == "__main__":
    raise SystemExit(main())
