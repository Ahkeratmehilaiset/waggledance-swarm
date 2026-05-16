# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/validate_bridge_event.py."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys


def _good_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": "2026-05-16T05:39:57.9995496Z",
        "agent": "codex",
        "type": "message",
        "task_id": "bridge-schema-smoke",
        "status": "answered",
        "severity": "",
        "to": "claude",
        "message": "validator smoke",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": {},
    }
    event.update(overrides)
    return event


def _write_jsonl(path: Path, events: list[dict[str, object] | str]) -> None:
    lines = [
        item if isinstance(item, str) else json.dumps(item, sort_keys=True)
        for item in events
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_returns_zero_and_json_summary_for_valid_file(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event()])

    rc = mod.main(["--events", str(events_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["valid"] == 1
    assert payload["invalid"] == 0


def test_cli_returns_one_for_invalid_file_and_reports_issue(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            _good_event(),
            _good_event(type="wake_request", to=""),
        ],
    )

    rc = mod.main(["--events", str(events_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["valid"] == 1
    assert payload["invalid"] == 1
    assert payload["issues"][0]["line_no"] == 2


def test_cli_tail_can_validate_recent_clean_lines_only(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, ["{not-json}", _good_event()])

    rc = mod.main(["--events", str(events_path), "--tail", "1", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["checked"] == 1
    assert payload["ok"] is True


def test_cli_missing_events_file_is_a_nonzero_schema_failure(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    missing_path = tmp_path / "missing.jsonl"

    rc = mod.main(["--events", str(missing_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["missing_path"] == str(missing_path)


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event()])

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "validate_bridge_event.py"),
            "--events",
            str(events_path),
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
