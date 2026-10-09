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

With ``--apply`` it first takes the v2 runtime-root mutex, the same one the
work-queue CLI writers take (``tools/work_queue.py`` ``_root_mutex``; fable-5
foreman call 2026-10-01, inventory option a). A busy, abandoned or unusable
mutex, including a root the v2 canonical-root rule refuses, refuses the sweep
before any claim is archived. The dry run only reads and takes no lock.

Exit codes:
    0 - sweep ran (apply or dry-run); zero or more claims listed
    1 - argument or I/O error, or the runtime-root mutex refused --apply
        (nothing was archived)
    2 - bridge root not found
    4 - reconcile: --apply archived its claims, then releasing or closing the
        runtime-root mutex failed (outcome + mutex_cleanup_error, QB); or an
        I/O error inside --apply left some claims archived, a rollback failed,
        or the effect is unknown (outcome io_error_*, the completed archives
        listed, QB-L1). Nothing is retried. An I/O error that proves nothing
        was left behind, and any dry-run I/O error, stay 1.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import (  # noqa: E402
    ArchivedClaim,
    WorkQueueError,
    archive_stale_claims,
    resolve_bridge_root,
)
from tools.bridge_v2_queue_transactions import QueueTransactionError  # noqa: E402
from tools.work_queue import (  # noqa: E402
    IO_ERROR_NOTHING_APPLIED,
    MUTEX_CLEANUP_EXIT_CODE,
    MUTEX_CLEANUP_OUTCOME,
    _root_mutex,
    io_error_fields,
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
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
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


def _io_error(args: argparse.Namespace, now: datetime | None, exc: OSError) -> int:
    """An OSError raised inside archive_stale_claims (QB-L1): report what it says was completed and its outcome
    (io_error_fields) instead of a bare "sweep failed". A dry run writes nothing, so its I/O error is nothing-applied."""
    fields, code = io_error_fields(exc)
    if not args.apply:
        fields.update(applied=False, outcome=IO_ERROR_NOTHING_APPLIED)
        code = 1
    completed = [record for record in getattr(exc, "completed", None) or [] if isinstance(record, ArchivedClaim)]
    if args.json:
        payload = {
            "applied": args.apply,
            "max_age_seconds": args.max_age_seconds,
            "now_utc": None if now is None else now.isoformat().replace("+00:00", "Z"),
            "archived": [_serialize(record) for record in completed],
            "effects_applied": fields.pop("applied"),
            **fields,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return code
    for record in completed:
        print(f"ARCHIVED: {record.claim.task_id} agent={record.claim.agent} age={record.age_seconds}s "
              f"-> {record.archived_path}")
    if code == 1:
        sys.stderr.write(f"sweep failed: {exc}\n")
        return code
    sys.stderr.write(f"sweep failed with outcome {fields['outcome']}; reconcile before the next sweep: {exc}\n")
    for line in fields["rollback_errors"] + fields["residual"]:
        sys.stderr.write(f"- {line}\n")
    return code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    if not bridge_root.exists():
        sys.stderr.write(f"bridge root not found: {bridge_root}\n")
        return 2

    now = None
    archived = None
    cleanup_error = None
    try:
        # --apply archives and unlinks claims, so it takes the CLI writers' runtime-root mutex first; the dry run
        # reads only and takes no lock.
        with _root_mutex(bridge_root) if args.apply else contextlib.nullcontext():
            now = datetime.now(timezone.utc)   # read under the lock: a lease renewed during the wait is not stale
            archived = archive_stale_claims(
                bridge_root=bridge_root,
                now_utc=now,
                max_age_seconds=args.max_age_seconds,
                apply=args.apply,
            )
    except QueueTransactionError as exc:   # the mutex refused (busy, abandoned, unusable root): nothing archived
        sys.stderr.write(f"sweep refused: runtime-root mutex: {exc}\n")
        return 1
    except WorkQueueError as exc:
        sys.stderr.write(f"sweep refused: {exc}\n")
        return 1
    except OSError as exc:
        if archived is None:
            return _io_error(args, now, exc)
        # The archive WAS applied; only releasing or closing the runtime-root mutex after it failed (QB).
        cleanup_error = f"{type(exc).__name__}: {exc}"
    code = 0 if cleanup_error is None else MUTEX_CLEANUP_EXIT_CODE

    if args.json:
        payload = {
            "applied": args.apply,
            "max_age_seconds": args.max_age_seconds,
            "now_utc": now.isoformat().replace("+00:00", "Z"),
            "archived": [_serialize(record) for record in archived],
        }
        if cleanup_error is not None:
            payload["outcome"] = MUTEX_CLEANUP_OUTCOME
            payload["mutex_cleanup_error"] = cleanup_error
        print(json.dumps(payload, indent=2, sort_keys=True))
        return code

    if cleanup_error is not None:
        sys.stderr.write(f"sweep applied, then the runtime-root mutex cleanup failed: {cleanup_error}\n")
    label = "ARCHIVED" if args.apply else "WOULD ARCHIVE"
    if not archived:
        print(f"no stale claims (threshold {args.max_age_seconds}s)")
        return code
    for record in archived:
        print(
            f"{label}: {record.claim.task_id} "
            f"agent={record.claim.agent} "
            f"age={record.age_seconds}s "
            f"-> {record.archived_path}"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
