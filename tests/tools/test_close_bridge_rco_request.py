# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/close_bridge_rco_request.py."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "close_bridge_rco_request.py"

sys.path.insert(0, str(ROOT))

import waggledance.core.bridge_event_writer as event_writer  # noqa: E402
from tools.close_bridge_rco_request import (  # noqa: E402
    CloseRcoError,
    close_bridge_rco_request,
    _read_events,
)
from tools.idle_check import evaluate_idle_state  # noqa: E402
from waggledance.core.bridge_event_writer import (  # noqa: E402
    BridgeEventProjectionWarning,
)


def _seed_bridge(tmp_path: Path, events: list[dict]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    claims = bridge_root / "work_queue" / "claims"
    shared.mkdir(parents=True)
    claims.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return bridge_root


def _seed_bridge_raw(tmp_path: Path, lines: list[str]) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    claims = bridge_root / "work_queue" / "claims"
    shared.mkdir(parents=True)
    claims.mkdir(parents=True)
    (shared / "events.jsonl").write_text("".join(lines), encoding="utf-8")
    return bridge_root


def test_read_events_skips_bare_null_event_line(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(
        tmp_path,
        [
            "null\n",
            json.dumps(_opening_handoff("task-1", "2026-05-20T18:00:00Z")) + "\n",
        ],
    )

    assert _read_events(bridge_root / "shared" / "events.jsonl") == [
        _opening_handoff("task-1", "2026-05-20T18:00:00Z")
    ]


def _opening_handoff(task_id: str, ts: str, agent: str = "claude") -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "handoff",
        "task_id": task_id,
        "status": "rco_requested",
        "severity": "",
        "to": "codex",
        "message": f"Please RCO PR #999 task {task_id}",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }


def test_close_emits_event_that_clears_open_rco(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-1", "2026-05-20T18:00:00Z")],
    )

    # Idle gate should report open_rco before close
    idle_before = evaluate_idle_state(
        events_path=bridge_root / "shared" / "events.jsonl",
        claims_dir=bridge_root / "work_queue" / "claims",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )
    assert idle_before["criteria"]["open_rco_requests"]["task_ids"] == ["task-1"]

    result = close_bridge_rco_request(
        task_id="task-1",
        pr_number=999,
        from_agent="claude",
        bridge_root=bridge_root,
        merge_commit="abcdef0123",
        merged_at="2026-05-20T19:00:00Z",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        emit=True,
    )
    assert result["ok"] is True
    assert result["emitted"] is True
    assert result["decision"] == "closed"
    assert result["proposed_event"]["type"] == "decision"
    assert result["proposed_event"]["status"] == "rco_closed_postmerge"
    assert result["proposed_event"]["task_id"] == "task-1"
    assert result["proposed_event"]["to"] == "codex,claude"
    assert "operator" not in result["proposed_event"]["to"]

    idle_after = evaluate_idle_state(
        events_path=bridge_root / "shared" / "events.jsonl",
        claims_dir=bridge_root / "work_queue" / "claims",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )
    assert idle_after["criteria"]["open_rco_requests"]["task_ids"] == []


def test_close_does_not_extend_recent_merge_window(tmp_path: Path) -> None:
    """The close event must NOT match _is_merge_event."""
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-2", "2026-05-20T18:00:00Z")],
    )
    close_bridge_rco_request(
        task_id="task-2",
        pr_number=42,
        from_agent="claude",
        bridge_root=bridge_root,
        merge_commit="cafe1234",
        merged_at="2026-05-20T19:00:00Z",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        emit=True,
    )

    # Idle gate query at the SAME instant as the close emit. If the close
    # had been classified as a merge event, recent_merge.ok would be False
    # (event ts within the 60-min window). The whole point of using
    # type=decision status=rco_closed_postmerge is that it must not.
    idle_state = evaluate_idle_state(
        events_path=bridge_root / "shared" / "events.jsonl",
        claims_dir=bridge_root / "work_queue" / "claims",
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )
    # The bridge had no prior merge event AND the close must not register as one
    assert idle_state["criteria"]["recent_merge"]["ok"] is True
    assert idle_state["criteria"]["recent_merge"].get("latest_utc") is None


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-3", "2026-05-20T18:00:00Z")],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_text(encoding="utf-8")

    result = close_bridge_rco_request(
        task_id="task-3",
        pr_number=7,
        from_agent="claude",
        bridge_root=bridge_root,
        now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
        emit=False,
    )
    assert result["emitted"] is False
    assert result["decision"] == "ready"
    assert events_path.read_text(encoding="utf-8") == before


def test_refuses_when_no_open_rco_for_task(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-A", "2026-05-20T18:00:00Z")],
    )
    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="other-task",
            pr_number=1,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )
    assert excinfo.value.decision == "no_open_rco"
    assert excinfo.value.exit_code == 3


def test_refuses_malformed_events_file(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(tmp_path, ["{not-json}\n"])

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-malformed",
            pr_number=1,
            from_agent="codex",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=False,
        )

    assert excinfo.value.decision == "invalid_events_file"
    assert excinfo.value.exit_code == 2
    assert "line 1" in str(excinfo.value)


def test_refuses_non_object_event_line(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(tmp_path, ["[]\n"])

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-non-object",
            pr_number=1,
            from_agent="codex",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=False,
        )

    assert excinfo.value.decision == "invalid_events_file"
    assert excinfo.value.exit_code == 2
    assert "line 1" in str(excinfo.value)


def test_refuses_when_rco_already_closed(tmp_path: Path) -> None:
    """Calling twice does not produce duplicate close events."""
    bridge_root = _seed_bridge(
        tmp_path,
        [
            _opening_handoff("task-B", "2026-05-20T18:00:00Z"),
            {
                "ts_utc": "2026-05-20T18:30:00Z",
                "agent": "claude",
                "type": "decision",
                "task_id": "task-B",
                "status": "rco_closed_postmerge",
                "severity": "",
                "to": "codex",
                "message": "already closed",
                "paths": [],
                "write_scope": [],
                "run_id": "",
                "pid": 0,
                "cwd": "",
            },
        ],
    )
    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-B",
            pr_number=2,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )
    assert excinfo.value.decision == "no_open_rco"


def test_concurrent_close_rechecks_under_append_mutex_and_emits_exactly_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-race", "2026-05-20T18:00:00Z")],
    )
    import tools.close_bridge_rco_request as closer

    original_read = closer._read_events
    first_read_barrier = threading.Barrier(2)
    thread_state = threading.local()

    def synchronize_first_read(events_path: Path) -> list[dict[str, object]]:
        events = original_read(events_path)
        calls = int(getattr(thread_state, "calls", 0)) + 1
        thread_state.calls = calls
        if calls == 1:
            first_read_barrier.wait(timeout=5)
        return events

    monkeypatch.setattr(closer, "_read_events", synchronize_first_read)

    def close_once() -> dict[str, object]:
        return close_bridge_rco_request(
            task_id="task-race",
            pr_number=404,
            from_agent="codex",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )

    successes: list[dict[str, object]] = []
    failures: list[CloseRcoError] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(close_once) for _ in range(2)]
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except CloseRcoError as exc:
                failures.append(exc)

    assert [result["decision"] for result in successes] == ["closed"]
    assert [error.decision for error in failures] == ["no_open_rco"]
    close_events = [
        event
        for event in _read_events(bridge_root / "shared" / "events.jsonl")
        if event.get("task_id") == "task-race"
        and event.get("status") == "rco_closed_postmerge"
    ]
    assert len(close_events) == 1


def test_close_event_is_schema_validated_before_mutation(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-schema", "2026-05-20T18:00:00Z")],
    )
    events_path = bridge_root / "shared" / "events.jsonl"
    before = events_path.read_bytes()

    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="task-schema",
            pr_number=405,
            from_agent="Mallory",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )

    assert excinfo.value.decision == "invalid_close_event"
    assert events_path.read_bytes() == before
    assert not (bridge_root / "outbox").exists()


def test_close_reports_projection_warning_without_failing_committed_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-projection", "2026-05-20T18:00:00Z")],
    )

    def fail_outbox(path: Path, payload: bytes) -> None:
        raise PermissionError(f"simulated outbox failure at {path}")

    monkeypatch.setattr(event_writer, "_append_projection", fail_outbox)
    with pytest.warns(BridgeEventProjectionWarning, match="outbox projection"):
        result = close_bridge_rco_request(
            task_id="task-projection",
            pr_number=406,
            from_agent="codex",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )

    assert result["decision"] == "closed"
    assert len(result["projection_warnings"]) == 1
    close_events = [
        event
        for event in _read_events(bridge_root / "shared" / "events.jsonl")
        if event.get("status") == "rco_closed_postmerge"
    ]
    assert len(close_events) == 1


def test_empty_task_id_rejected(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-x", "2026-05-20T18:00:00Z")],
    )
    with pytest.raises(CloseRcoError) as excinfo:
        close_bridge_rco_request(
            task_id="",
            pr_number=1,
            from_agent="claude",
            bridge_root=bridge_root,
            now_utc=datetime(2026, 5, 21, 6, 0, tzinfo=timezone.utc),
            emit=True,
        )
    assert excinfo.value.decision == "invalid_args"


def test_cli_reports_malformed_events_file(tmp_path: Path) -> None:
    bridge_root = _seed_bridge_raw(tmp_path, ["{not-json}\n"])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "task-cli-malformed",
            "--pr",
            "1234",
            "--from-agent",
            "codex",
            "--bridge-root",
            str(bridge_root),
            "--now",
            "2026-05-21T06:00:00Z",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["decision"] == "invalid_events_file"


def test_cli_default_bridge_root_uses_runtime_env_from_other_cwd(
    tmp_path: Path,
) -> None:
    bridge_root = _seed_bridge(
        tmp_path / "runtime",
        [_opening_handoff("task-cli-env", "2026-05-20T18:00:00Z")],
    )
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "task-cli-env",
            "--pr",
            "1234",
            "--from-agent",
            "codex",
            "--now",
            "2026-05-21T06:00:00Z",
            "--dry-run",
            "--json",
        ],
        cwd=str(other_cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "ready"
    assert payload["emitted"] is False


def test_cli_smoke(tmp_path: Path) -> None:
    bridge_root = _seed_bridge(
        tmp_path,
        [_opening_handoff("task-cli", "2026-05-20T18:00:00Z")],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            "task-cli",
            "--pr",
            "1234",
            "--from-agent",
            "claude",
            "--bridge-root",
            str(bridge_root),
            "--merge-commit",
            "deadbeef",
            "--merged-at",
            "2026-05-20T19:00:00Z",
            "--now",
            "2026-05-21T06:00:00Z",
            "--apply",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "closed"
    assert payload["proposed_event"]["status"] == "rco_closed_postmerge"
    assert payload["proposed_event"]["to"] == "codex,claude"
    assert "operator" not in payload["proposed_event"]["to"]
