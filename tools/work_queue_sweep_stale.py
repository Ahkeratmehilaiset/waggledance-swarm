# SPDX-License-Identifier: BUSL-1.1
"""Sweep stale work-queue claims so multi-agent operation never wedges.

Dry-run by default. With ``--apply`` the script archives stale claim files
to ``.agent-bridge/work_queue/done/<task>.<stamp>.stale_lease.json`` and
removes the active claim, mirroring
``.agent-bridge/bin/Invoke-StaleClaimSweep.ps1``.

This is a thin Python parity wrapper around
``waggledance.core.work_queue.archive_stale_claims``. It is intentionally
side-effect-free under the default (no ``--apply``) so a curious agent or
operator can run it to inspect the sweep plan.

Exit codes:
    0 - sweep ran (apply or dry-run); zero or more claims listed
    1 - argument or I/O error
    2 - bridge root not found
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from waggledance.core.work_queue import (
    DEFAULT_BRIDGE_ROOT,
    ArchivedClaim,
    WorkQueueError,
    archive_stale_claims,
)


CLI_DEFAULT_MAX_AGE_SECONDS = 300


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="work_queue_sweep_stale",
        description=(
            "Archive stale agent-bridge claims whose heartbeat is older "
            "than --max-age-seconds. Dry-run unless --apply is passed."
        ),
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help="Path to .agent-bridge directory (default: repo-local).",
    )
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=CLI_DEFAULT_MAX_AGE_SECONDS,
        help=(
            "Lease threshold in seconds (default: "
            f"{CLI_DEFAULT_MAX_AGE_SECONDS}s). Claims whose "
            "last_heartbeat_utc is older than this are swept."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually archive and unlink stale claims. Default is dry-run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable output.",
    )
    return parser.parse_args(argv)


def _serialize(record: ArchivedClaim) -> dict[str, object]:
    return {
        "agent": record.claim.agent,
        "task_id": record.claim.task_id,
        "summary": record.claim.summary,
        "age_seconds": record.age_seconds,
        "release_reason": record.release_reason,
        "archived_path": str(record.archived_path),
        "applied": record.applied,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bridge_root = args.bridge_root or DEFAULT_BRIDGE_ROOT
    if not bridge_root.exists():
        sys.stderr.write(f"bridge root not found: {bridge_root}\n")
        return 2

    now = datetime.now(timezone.utc)
    try:
        archived = archive_stale_claims(
            bridge_root=bridge_root,
            now_utc=now,
            max_age_seconds=args.max_age_seconds,
            apply=args.apply,
        )
    except WorkQueueError as exc:
        sys.stderr.write(f"sweep refused: {exc}\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"sweep failed: {exc}\n")
        return 1

    if args.json:
        payload = {
            "applied": args.apply,
            "max_age_seconds": args.max_age_seconds,
            "now_utc": now.isoformat().replace("+00:00", "Z"),
            "archived": [_serialize(record) for record in archived],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    label = "ARCHIVED" if args.apply else "WOULD ARCHIVE"
    if not archived:
        print(f"no stale claims (threshold {args.max_age_seconds}s)")
        return 0
    for record in archived:
        print(
            f"{label}: {record.claim.task_id} "
            f"agent={record.claim.agent} "
            f"age={record.age_seconds}s "
            f"-> {record.archived_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
