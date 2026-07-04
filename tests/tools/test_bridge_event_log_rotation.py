# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_event_log_rotation.py"

sys.path.insert(0, str(ROOT))

from tools.bridge_event_log_rotation import (  # noqa: E402
    BridgeEventLogRotationError,
    stage_bridge_events_rotation,
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


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    events_path = path / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def _now() -> datetime:
    return datetime(2026, 6, 15, 12, tzinfo=timezone.utc)


def test_dry_run_reports_ready_without_writes(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    archive_dir = tmp_path / "archive"

    report = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=archive_dir,
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
    )

    assert report["ok"] is True
    assert report["decision"] == "bridge_events_rotation_stage_ready"
    assert report["mode"] == "dry_run_no_writes"
    assert report["authority"]["writes_archive"] is False
    assert archive_dir.exists() is False


def test_dry_run_protected_task_id_uses_planner_guard(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="sealed-old"),
            _event("2026-06-02T00:00:00Z", task_id="open-task"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )

    report = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
        protected_task_ids=["open-task"],
    )

    assert report["ok"] is True
    assert report["decision"] == "bridge_events_rotation_stage_ready"
    assert report["plan"]["counts"]["archive_lines"] == 1
    assert report["plan"]["blockers"] == [
        {
            "line": 2,
            "reason": "protected_gate_reference",
            "ts_utc": "2026-06-02T00:00:00Z",
            "protected_references": [
                {"kind": "task_id", "value": "open-task"},
            ],
        }
    ]
    assert (tmp_path / "archive").exists() is False


def test_apply_stages_archive_and_receipt_without_rewriting_events(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old-1"),
            _event("2026-06-02T00:00:00Z", task_id="old-2"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    source = events_path.read_bytes()

    report = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
        apply=True,
    )

    assert report["ok"] is True
    assert report["decision"] == "bridge_events_rotation_archive_staged"
    assert report["events_rewritten"] is False
    assert report["source_preserved"] is True
    assert events_path.read_bytes() == source
    archive_path = Path(report["archive"]["path"])
    receipt_path = Path(report["receipt"]["path"])
    assert archive_path.exists()
    assert receipt_path.exists()
    assert report["archive"]["action"] == "written"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["events_rewritten"] is False
    assert receipt["source_preserved"] is True
    assert receipt["events_after_sha256"] == report["events_after_sha256"]
    assert receipt["concurrent_append_bytes_observed"] == 0
    assert receipt["digests"]["archive_sha256"] == report["archive"]["sha256"]
    assert "append-race proof or cooperative writer lock" in receipt[
        "future_truncate_required_controls"
    ]


def test_apply_reuses_matching_archive_idempotently(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    first = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
        apply=True,
    )
    second = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
        apply=True,
    )

    assert Path(first["archive"]["path"]) == Path(second["archive"]["path"])
    assert second["archive"]["action"] == "already_present_verified"


def test_apply_refuses_conflicting_archive_path(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    first = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
        apply=True,
    )
    Path(first["archive"]["path"]).write_text("corrupt\n", encoding="utf-8")

    with pytest.raises(BridgeEventLogRotationError) as excinfo:
        stage_bridge_events_rotation(
            events_path=events_path,
            archive_dir=tmp_path / "archive",
            keep_days=7,
            min_recent_lines=1,
            now_utc=_now(),
            apply=True,
        )

    assert "archive path already exists with different bytes" in str(excinfo.value)
    assert events_path.read_text(encoding="utf-8").count("\n") == 2


def test_apply_skips_receipt_when_source_mutates_by_more_than_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    mutated = json.dumps(_event("2026-06-16T00:00:00Z", task_id="tampered")).encode(
        "utf-8"
    ) + b"\n"
    original_read_bytes = Path.read_bytes
    events_read_count = 0

    def read_bytes_with_non_append_race(path: Path) -> bytes:
        nonlocal events_read_count
        if path == events_path:
            events_read_count += 1
            if events_read_count >= 3:
                return mutated
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes_with_non_append_race)

    report = stage_bridge_events_rotation(
        events_path=events_path,
        archive_dir=tmp_path / "archive",
        keep_days=7,
        min_recent_lines=1,
        now_utc=_now(),
        apply=True,
    )

    assert report["ok"] is False
    assert report["decision"] == "bridge_events_rotation_stage_source_changed"
    assert report["source_preserved"] is False
    assert report["events_rewritten"] is False
    assert report["receipt"]["write"]["action"] == "skipped_source_changed"
    assert Path(report["archive"]["path"]).exists()
    assert Path(report["receipt"]["path"]).exists() is False


def test_cli_apply_returns_nonzero_when_source_mutates_by_more_than_append(
    tmp_path: Path,
) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    archive_dir = tmp_path / "archive"
    mutated_event = _event("2026-06-16T00:00:00Z", task_id="tampered")
    code = f"""
import json
from pathlib import Path
from tools import bridge_event_log_rotation as tool

events_path = Path({str(events_path)!r})
archive_dir = Path({str(archive_dir)!r})
mutated = (json.dumps({mutated_event!r}) + "\\n").encode("utf-8")
original_read_bytes = Path.read_bytes
state = {{"events_read_count": 0}}

def read_bytes_with_non_append_race(path):
    if path == events_path:
        state["events_read_count"] += 1
        if state["events_read_count"] >= 3:
            return mutated
    return original_read_bytes(path)

Path.read_bytes = read_bytes_with_non_append_race
raise SystemExit(tool.main([
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
    "--apply",
    "--json",
]))
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["decision"] == "bridge_events_rotation_stage_source_changed"
    assert report["receipt"]["write"]["action"] == "skipped_source_changed"


def test_cli_apply_json_writes_archive_and_preserves_events(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _event("2026-06-01T00:00:00Z", task_id="old"),
            _event("2026-06-14T00:00:00Z", task_id="new"),
        ],
    )
    source = events_path.read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--archive-dir",
            str(tmp_path / "archive"),
            "--keep-days",
            "7",
            "--min-recent-lines",
            "1",
            "--now",
            "2026-06-15T12:00:00Z",
            "--apply",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    report = json.loads(completed.stdout)
    assert report["decision"] == "bridge_events_rotation_archive_staged"
    assert Path(report["archive"]["path"]).exists()
    assert Path(report["receipt"]["path"]).exists()
    assert events_path.read_bytes() == source
