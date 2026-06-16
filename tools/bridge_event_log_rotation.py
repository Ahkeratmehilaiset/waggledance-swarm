# SPDX-License-Identifier: BUSL-1.1
"""Stage a verified archive for bridge events.jsonl rotation.

This is the first mutating step after the read-only planner. It can materialize
the archived prefix and a receipt, but it deliberately does not rewrite or
truncate the live events.jsonl file.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.plan_bridge_events_rotation import (
    DEFAULT_KEEP_DAYS,
    DEFAULT_MIN_RECENT_LINES,
    PLAN_VERSION,
    BridgeEventsRotationPlanError,
    _parse_now,
    _sha256,
    plan_bridge_events_rotation,
)
from waggledance.core.work_queue import resolve_bridge_root


STAGE_VERSION = "bridge-events-rotation-stage.v1"


class BridgeEventLogRotationError(ValueError):
    """Raised when archive staging cannot complete safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage a verified archive for bridge events.jsonl rotation. "
            "Dry-run by default; --apply writes archive+receipt only and never "
            "rewrites the live events file."
        )
    )
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--bridge-root", type=Path, default=None)
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--keep-days", type=float, default=DEFAULT_KEEP_DAYS)
    parser.add_argument("--min-recent-lines", type=int, default=DEFAULT_MIN_RECENT_LINES)
    parser.add_argument("--now", default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write archive and receipt. The live events file is still preserved.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    archive_dir = args.archive_dir or bridge_root / "shared" / "archive"
    try:
        report = stage_bridge_events_rotation(
            events_path=events_path,
            archive_dir=archive_dir,
            keep_days=args.keep_days,
            min_recent_lines=args.min_recent_lines,
            now_utc=_parse_now(args.now),
            apply=args.apply,
        )
    except (BridgeEventsRotationPlanError, BridgeEventLogRotationError) as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "bridge_events_rotation_stage_error",
            "errors": [exc.__class__.__name__],
        }
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def stage_bridge_events_rotation(
    *,
    events_path: Path,
    archive_dir: Path,
    keep_days: float = DEFAULT_KEEP_DAYS,
    min_recent_lines: int = DEFAULT_MIN_RECENT_LINES,
    now_utc: datetime | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Return a staging report and optionally write archive+receipt."""

    plan = plan_bridge_events_rotation(
        events_path=events_path,
        archive_dir=archive_dir,
        keep_days=keep_days,
        min_recent_lines=min_recent_lines,
        now_utc=now_utc,
    )
    report = _base_report(plan=plan, apply=apply)
    if not plan.get("eligible_for_rotation"):
        report["decision"] = "bridge_events_rotation_stage_noop"
        report["ok"] = True
        return report
    if not apply:
        report["decision"] = "bridge_events_rotation_stage_ready"
        report["ok"] = True
        return report

    raw = events_path.read_bytes()
    archive_line_count = int(plan["counts"]["archive_lines"])
    physical_lines = raw.splitlines(keepends=True)
    archive_bytes = b"".join(physical_lines[:archive_line_count])
    recent_bytes = b"".join(physical_lines[archive_line_count:])
    _verify_plan_bytes(plan=plan, raw=raw, archive_bytes=archive_bytes, recent_bytes=recent_bytes)

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = Path(str(plan["planned_archive_path"]))
    receipt_path = _receipt_path(archive_path)
    _assert_child_path(parent=archive_dir, child=archive_path)
    _assert_child_path(parent=archive_dir, child=receipt_path)

    archive_write = _write_verified_once(
        path=archive_path,
        data=archive_bytes,
        expected_sha256=str(plan["digests"]["archive_sha256"]),
    )
    after_bytes = events_path.read_bytes()
    source_preserved = after_bytes == raw or after_bytes.startswith(raw)
    concurrent_append_bytes_observed = (
        len(after_bytes) - len(raw) if after_bytes.startswith(raw) else 0
    )
    events_after_sha256 = _sha256(after_bytes)
    if not source_preserved:
        report.update(
            {
                "ok": False,
                "decision": "bridge_events_rotation_stage_source_changed",
                "archive": archive_write,
                "receipt": {
                    "path": str(receipt_path),
                    "write": {"action": "skipped_source_changed"},
                },
                "source_preserved": False,
                "events_rewritten": False,
                "events_after_sha256": events_after_sha256,
                "concurrent_append_bytes_observed": concurrent_append_bytes_observed,
            }
        )
        report.setdefault("errors", []).append(
            "events file changed by more than append while archive was staged"
        )
        return report

    receipt = _build_receipt(
        plan=plan,
        stage_report=report,
        archive_path=archive_path,
        receipt_path=receipt_path,
        source_preserved=source_preserved,
        events_after_sha256=events_after_sha256,
        concurrent_append_bytes_observed=concurrent_append_bytes_observed,
    )
    receipt_bytes = _json_bytes(receipt)
    receipt_write = _write_atomic_replace(path=receipt_path, data=receipt_bytes)

    report.update(
        {
            "ok": True,
            "decision": "bridge_events_rotation_archive_staged",
            "archive": archive_write,
            "receipt": {
                "path": str(receipt_path),
                "sha256": _sha256(receipt_bytes),
                "write": receipt_write,
            },
            "source_preserved": source_preserved,
            "events_rewritten": False,
            "events_after_sha256": events_after_sha256,
            "concurrent_append_bytes_observed": concurrent_append_bytes_observed,
        }
    )
    return report


def _base_report(*, plan: dict[str, Any], apply: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": "bridge_events_rotation_stage_pending",
        "stage_version": STAGE_VERSION,
        "plan_version": PLAN_VERSION,
        "mode": "apply_archive_and_receipt" if apply else "dry_run_no_writes",
        "apply": apply,
        "authority": {
            "writes_archive": apply,
            "writes_receipt": apply,
            "rewrites_events": False,
            "truncates_events": False,
            "merge_or_gate_authority": False,
        },
        "plan": plan,
        "safety_notes": [
            "Default mode is dry-run and writes nothing.",
            "--apply writes only the planned archive and receipt.",
            "The live events.jsonl file is preserved; truncate/rewrite remains a separate operator-gated step.",
        ],
    }


def _verify_plan_bytes(
    *,
    plan: dict[str, Any],
    raw: bytes,
    archive_bytes: bytes,
    recent_bytes: bytes,
) -> None:
    reconstructed = archive_bytes + recent_bytes
    if reconstructed != raw:
        raise BridgeEventLogRotationError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_stage_error",
                "errors": ["archive+recent bytes do not reconstruct source"],
            }
        )
    expected = plan["digests"]
    actual = {
        "source_sha256": _sha256(raw),
        "archive_sha256": _sha256(archive_bytes),
        "recent_sha256": _sha256(recent_bytes),
        "reconstructed_sha256": _sha256(reconstructed),
    }
    mismatches = [
        name for name, value in actual.items() if value != str(expected.get(name))
    ]
    if mismatches:
        raise BridgeEventLogRotationError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_stage_error",
                "errors": [f"plan digest mismatch: {', '.join(mismatches)}"],
            }
        )


def _write_verified_once(
    *,
    path: Path,
    data: bytes,
    expected_sha256: str,
) -> dict[str, Any]:
    if path.exists():
        existing = path.read_bytes()
        if _sha256(existing) != expected_sha256 or existing != data:
            raise BridgeEventLogRotationError(
                {
                    "ok": False,
                    "decision": "bridge_events_rotation_stage_error",
                    "errors": [f"archive path already exists with different bytes: {path}"],
                }
            )
        return {
            "path": str(path),
            "sha256": expected_sha256,
            "bytes": len(data),
            "action": "already_present_verified",
        }
    write = _write_atomic_replace(path=path, data=data)
    verified = path.read_bytes()
    if verified != data or _sha256(verified) != expected_sha256:
        raise BridgeEventLogRotationError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_stage_error",
                "errors": [f"archive verification failed after replace: {path}"],
            }
        )
    return {
        "path": str(path),
        "sha256": expected_sha256,
        "bytes": len(data),
        "action": "written",
        "write": write,
    }


def _write_atomic_replace(*, path: Path, data: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp_path.read_bytes() != data:
            raise BridgeEventLogRotationError(
                {
                    "ok": False,
                    "decision": "bridge_events_rotation_stage_error",
                    "errors": [f"temp write verification failed: {tmp_path}"],
                }
            )
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return {"method": "tempfile_then_os_replace", "path": str(path)}


def _build_receipt(
    *,
    plan: dict[str, Any],
    stage_report: dict[str, Any],
    archive_path: Path,
    receipt_path: Path,
    source_preserved: bool,
    events_after_sha256: str,
    concurrent_append_bytes_observed: int,
) -> dict[str, Any]:
    return {
        "receipt_version": "bridge-events-rotation-receipt.v1",
        "stage_version": stage_report["stage_version"],
        "plan_version": plan["plan_version"],
        "events_path": plan["events_path"],
        "archive_path": str(archive_path),
        "receipt_path": str(receipt_path),
        "counts": plan["counts"],
        "bytes": plan["bytes"],
        "digests": plan["digests"],
        "retention": plan["retention"],
        "events_rewritten": False,
        "source_preserved": source_preserved,
        "events_after_sha256": events_after_sha256,
        "concurrent_append_bytes_observed": concurrent_append_bytes_observed,
        "future_truncate_required_controls": [
            "operator-gated explicit apply flag",
            "append-race proof or cooperative writer lock",
            "archive verified before any events rewrite",
            "gate-reader window exceeds open-PR lifetime",
            "head-bound bridge receipt after successful rewrite",
        ],
    }


def _receipt_path(archive_path: Path) -> Path:
    return archive_path.with_name(f"{archive_path.name}.receipt.json")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _assert_child_path(*, parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if parent_resolved != child_resolved and parent_resolved not in child_resolved.parents:
        raise BridgeEventLogRotationError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_stage_error",
                "errors": [f"path escapes archive directory: {child}"],
            }
        )


def _print_human(report: dict[str, Any]) -> None:
    print(f"bridge event log rotation stage: {report.get('decision')}")
    print(f"- mode: {report.get('mode')}")
    print(f"- ok: {report.get('ok')}")
    archive = report.get("archive")
    if isinstance(archive, dict):
        print(f"- archive: {archive.get('path')}")
    receipt = report.get("receipt")
    if isinstance(receipt, dict):
        print(f"- receipt: {receipt.get('path')}")


if __name__ == "__main__":
    raise SystemExit(main())
