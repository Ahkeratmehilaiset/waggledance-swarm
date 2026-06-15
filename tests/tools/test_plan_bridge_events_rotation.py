# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "plan_bridge_events_rotation.py"

sys.path.insert(0, str(ROOT))

from tools.plan_bridge_events_rotation import (  # noqa: E402
    BridgeEventsRotationPlanError,
    plan_bridge_events_rotation,
)


def _event(ts: str, *, task_id: str = "task") -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": "codex-tools-1",
        "type": "message",
        "task_id": task_id,
        "status": "test",
        "message": "synthetic event",
        "payload": {},
    }


def _events_file(path: Path, events: list[object], *, trailing_newline: bool = True) -> Path:
    body = "\n".join(
        json.dumps(event, sort_keys=True) if isinstance(event, dict) else str(event)
        for event in events
    )
    if trailing_newline:
        body += "\n"
    events_path = path / "events.jsonl"
    events_path.write_text(body, encoding="utf-8")
    return events_path


def _now() -> datetime:
    return datetime(2026, 6, 15, 12, tzinfo=timezone.utc)


def test_plan_archives_old_prefix_and_preserves_byte_roundtrip(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old-1"),
            _event("2026-06-02T00:00:00Z", task_id="old-2"),
            _event("2026-06-12T00:00:00Z", task_id="new-1"),
            _event("2026-06-14T00:00:00Z", task_id="new-2"),
        ],
    )

    report = plan_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
    )

    assert report["ok"] is True
    assert report["decision"] == "bridge_events_rotation_plan_ready"
    assert report["mode"] == "read_only_no_writes"
    assert report["eligible_for_rotation"] is True
    assert report["counts"] == {
        "total_lines": 4,
        "archive_lines": 2,
        "recent_lines": 2,
        "blocker_lines": 1,
    }
    assert report["blockers"] == [
        {
            "line": 3,
            "reason": "line_not_older_than_cutoff",
            "ts_utc": "2026-06-12T00:00:00Z",
        }
    ]
    assert report["digests"]["roundtrip_ok"] is True
    assert (
        report["digests"]["source_sha256"]
        == report["digests"]["reconstructed_sha256"]
    )
    assert report["authority"] == {
        "writes_events": False,
        "creates_archive": False,
        "rewrites_bridge": False,
        "merge_or_gate_authority": False,
    }


def test_min_recent_lines_keeps_old_tail_in_recent_file(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old-1"),
            _event("2026-06-02T00:00:00Z", task_id="old-2"),
            _event("2026-06-03T00:00:00Z", task_id="old-3"),
            _event("2026-06-04T00:00:00Z", task_id="old-4"),
            _event("2026-06-05T00:00:00Z", task_id="old-5"),
        ],
    )

    report = plan_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=3,
        now_utc=_now(),
    )

    assert report["counts"]["archive_lines"] == 2
    assert report["counts"]["recent_lines"] == 3
    assert report["blockers"] == [{"line": 3, "reason": "min_recent_lines_floor"}]
    assert report["eligible_for_rotation"] is True


def test_missing_timestamp_blocks_split_before_reordering(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old-1"),
            {"agent": "codex-tools-1", "message": "legacy event without timestamp"},
            _event("2026-06-02T00:00:00Z", task_id="old-2"),
        ],
        trailing_newline=False,
    )

    source_bytes = events_path.read_bytes()
    report = plan_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
    )

    assert report["counts"]["archive_lines"] == 1
    assert report["counts"]["recent_lines"] == 2
    assert report["blockers"] == [
        {"line": 2, "reason": "missing_or_invalid_ts_utc"}
    ]
    assert report["digests"]["roundtrip_ok"] is True
    assert report["digests"]["source_sha256"] == report["digests"]["reconstructed_sha256"]
    assert events_path.read_bytes() == source_bytes


def test_no_archivable_prefix_is_explicit_noop(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-14T00:00:00Z", task_id="new-1"),
            _event("2026-06-14T01:00:00Z", task_id="new-2"),
        ],
    )

    report = plan_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
    )

    assert report["decision"] == "bridge_events_rotation_plan_noop"
    assert report["eligible_for_rotation"] is False
    assert report["counts"]["archive_lines"] == 0
    assert report["digests"]["roundtrip_ok"] is True


def test_cli_json_is_read_only_and_does_not_create_archive_dir(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old-1"),
            _event("2026-06-12T00:00:00Z", task_id="new-1"),
        ],
    )
    archive_dir = tmp_path / "archive"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--archive-dir",
            str(archive_dir),
            "--keep-days",
            "7",
            "--min-recent-lines",
            "1",
            "--now",
            "2026-06-15T12:00:00Z",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    report = json.loads(completed.stdout)
    assert report["decision"] == "bridge_events_rotation_plan_ready"
    assert report["counts"]["archive_lines"] == 1
    assert archive_dir.exists() is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"keep_days": 0}, "keep_days must be positive"),
        ({"min_recent_lines": 0}, "min_recent_lines must be at least 1"),
    ],
)
def test_invalid_retention_config_fails_closed(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    events_path = _events_file(tmp_path, [_event("2026-06-01T00:00:00Z")])

    with pytest.raises(BridgeEventsRotationPlanError) as excinfo:
        plan_bridge_events_rotation(
            events_path=events_path,
            archive_dir=tmp_path / "archive",
            now_utc=_now(),
            **kwargs,
        )

    assert message in str(excinfo.value)
