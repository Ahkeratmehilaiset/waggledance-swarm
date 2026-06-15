# SPDX-License-Identifier: BUSL-1.1
"""Plan a safe, read-only rotation split for bridge events.jsonl.

The tool does not mutate the bridge. It computes the prefix that could be
archived while keeping a recent suffix in place, then proves that
``archive_bytes + recent_bytes`` reconstructs the original file exactly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402


DEFAULT_KEEP_DAYS = 7.0
DEFAULT_MIN_RECENT_LINES = 50000
PLAN_VERSION = "bridge-events-rotation-plan.v1"


class BridgeEventsRotationPlanError(ValueError):
    """Raised when a rotation plan cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read bridge events.jsonl and report a safe archive/recent split. "
            "This is read-only and never rewrites the bridge."
        )
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
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
        "--archive-dir",
        type=Path,
        default=None,
        help=(
            "Planned archive directory. Defaults to <bridge-root>/shared/archive. "
            "The directory is reported only; it is not created."
        ),
    )
    parser.add_argument(
        "--keep-days",
        type=float,
        default=DEFAULT_KEEP_DAYS,
        help="Only events older than this many days are eligible for archival.",
    )
    parser.add_argument(
        "--min-recent-lines",
        type=int,
        default=DEFAULT_MIN_RECENT_LINES,
        help="Always keep at least this many physical lines in events.jsonl.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time for cutoff evaluation.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    archive_dir = args.archive_dir or bridge_root / "shared" / "archive"
    try:
        report = plan_bridge_events_rotation(
            events_path=events_path,
            archive_dir=archive_dir,
            keep_days=args.keep_days,
            min_recent_lines=args.min_recent_lines,
            now_utc=_parse_now(args.now),
        )
    except BridgeEventsRotationPlanError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "bridge_events_rotation_plan_error",
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


def plan_bridge_events_rotation(
    *,
    events_path: Path,
    archive_dir: Path,
    keep_days: float = DEFAULT_KEEP_DAYS,
    min_recent_lines: int = DEFAULT_MIN_RECENT_LINES,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a read-only archive/recent split plan for a bridge JSONL file."""
    if not math.isfinite(keep_days) or keep_days <= 0:
        raise BridgeEventsRotationPlanError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_plan_error",
                "errors": ["keep_days must be a finite positive number"],
            }
        )
    if min_recent_lines < 1:
        raise BridgeEventsRotationPlanError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_plan_error",
                "errors": ["min_recent_lines must be at least 1"],
            }
        )
    if not events_path.exists():
        raise BridgeEventsRotationPlanError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_plan_error",
                "errors": [f"events file not found: {events_path}"],
            },
            exit_code=1,
        )

    raw = events_path.read_bytes()
    physical_lines = raw.splitlines(keepends=True)
    effective_now = now_utc or _utc_now()
    cutoff = effective_now - timedelta(days=keep_days)

    split_index, blockers = _safe_prefix_split(
        physical_lines=physical_lines,
        cutoff_utc=cutoff,
        min_recent_lines=min_recent_lines,
    )
    archive_bytes = b"".join(physical_lines[:split_index])
    recent_bytes = b"".join(physical_lines[split_index:])
    reconstructed = archive_bytes + recent_bytes

    source_sha = _sha256(raw)
    reconstructed_sha = _sha256(reconstructed)
    roundtrip_ok = reconstructed == raw and reconstructed_sha == source_sha
    archive_name = _archive_name(cutoff, archive_bytes)
    archive_path = archive_dir / archive_name
    manifest_path = archive_dir / "manifest.json"

    archive_line_count = split_index
    recent_line_count = len(physical_lines) - split_index
    decision = (
        "bridge_events_rotation_plan_ready"
        if archive_line_count > 0 and roundtrip_ok
        else "bridge_events_rotation_plan_noop"
    )
    return {
        "ok": roundtrip_ok,
        "decision": decision,
        "plan_version": PLAN_VERSION,
        "mode": "read_only_no_writes",
        "authority": {
            "writes_events": False,
            "creates_archive": False,
            "rewrites_bridge": False,
            "merge_or_gate_authority": False,
        },
        "events_path": str(events_path),
        "archive_dir": str(archive_dir),
        "planned_archive_path": str(archive_path),
        "planned_manifest_path": str(manifest_path),
        "retention": {
            "keep_days": keep_days,
            "cutoff_ts_utc": _format_utc(cutoff),
            "min_recent_lines": min_recent_lines,
            "effective_now_utc": _format_utc(effective_now),
            "split_policy": (
                "contiguous_prefix_only; stop before the recent-line floor "
                "or the first line that is not older than the cutoff"
            ),
        },
        "counts": {
            "total_lines": len(physical_lines),
            "archive_lines": archive_line_count,
            "recent_lines": recent_line_count,
            "blocker_lines": len(blockers),
        },
        "bytes": {
            "source": len(raw),
            "archive": len(archive_bytes),
            "recent": len(recent_bytes),
        },
        "digests": {
            "source_sha256": source_sha,
            "archive_sha256": _sha256(archive_bytes),
            "recent_sha256": _sha256(recent_bytes),
            "reconstructed_sha256": reconstructed_sha,
            "roundtrip_ok": roundtrip_ok,
        },
        "eligible_for_rotation": archive_line_count > 0 and roundtrip_ok,
        "blockers": blockers[:10],
        "safety_notes": [
            "This tool is read-only and does not create archive files.",
            "A future mutating compactor must use temp files plus atomic rename.",
            "A future mutating compactor must keep archive+recent byte reconstruction equal to source.",
        ],
    }


def _safe_prefix_split(
    *,
    physical_lines: Sequence[bytes],
    cutoff_utc: datetime,
    min_recent_lines: int,
) -> tuple[int, list[dict[str, Any]]]:
    split_limit = max(0, len(physical_lines) - min_recent_lines)
    blockers: list[dict[str, Any]] = []
    split_index = 0
    for index, raw_line in enumerate(physical_lines):
        line_no = index + 1
        if index >= split_limit:
            if raw_line.strip():
                blockers.append(
                    {
                        "line": line_no,
                        "reason": "min_recent_lines_floor",
                    }
                )
            break
        event_ts = _event_timestamp(raw_line)
        if event_ts is None:
            blockers.append(
                {
                    "line": line_no,
                    "reason": "missing_or_invalid_ts_utc",
                }
            )
            break
        if event_ts >= cutoff_utc:
            blockers.append(
                {
                    "line": line_no,
                    "reason": "line_not_older_than_cutoff",
                    "ts_utc": _format_utc(event_ts),
                }
            )
            break
        split_index = index + 1
    return split_index, blockers


def _event_timestamp(raw_line: bytes) -> datetime | None:
    stripped = raw_line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    value = event.get("ts_utc")
    if not isinstance(value, str):
        return None
    return _parse_utc(value)


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise BridgeEventsRotationPlanError(
            {
                "ok": False,
                "decision": "bridge_events_rotation_plan_error",
                "errors": ["now must be an ISO-8601 timestamp"],
            }
        )
    return parsed


def _parse_utc(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _archive_name(cutoff_utc: datetime, archive_bytes: bytes) -> str:
    stamp = cutoff_utc.strftime("%Y%m%dT%H%M%SZ")
    digest = _sha256(archive_bytes)[:12]
    return f"events-before-{stamp}-{digest}.jsonl"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _print_human(report: dict[str, Any]) -> None:
    if not report.get("ok"):
        print("bridge events rotation plan: FAILED")
        for error in report.get("errors", []):
            print(f"- {error}")
        return
    counts = report["counts"]
    digests = report["digests"]
    print(f"bridge events rotation plan: {report['decision']}")
    print(f"- mode: {report['mode']}")
    print(f"- archive lines: {counts['archive_lines']}")
    print(f"- recent lines: {counts['recent_lines']}")
    print(f"- roundtrip ok: {digests['roundtrip_ok']}")
    print(f"- planned archive: {report['planned_archive_path']}")


if __name__ == "__main__":
    raise SystemExit(main())
