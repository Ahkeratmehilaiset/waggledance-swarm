# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import (
    agent_identity,
    agent_next_task,
    bridge_loop_tick,
    bridge_next_action,
    work_queue,
)
from tools.bridge_session_identity import cli_identity_mismatch, emit_identity_mismatch
from waggledance.core.work_queue import claim_task


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_LOOP_TICK = ROOT / "tools" / "bridge_loop_tick.py"
SESSION_AGENT = "codex-tools-1"
REQUESTED_AGENT = "codex-lead-1"
EXPECTED_MISMATCH = {
    "ok": False,
    "decision": "identity_mismatch",
    "safe_mode": "read-only",
    "requested_agent": REQUESTED_AGENT,
    "session_agent": SESSION_AGENT,
    "errors": [
        (
            "--agent 'codex-lead-1' does not match "
            "AGENT_BRIDGE_AGENT 'codex-tools-1'"
        )
    ],
}


def _unexpected_state_access(*args: object, **kwargs: object) -> object:
    raise AssertionError("identity guard must run before bridge state access")


def _snapshot_files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _core_identity_cli_cases(
    base: Path,
    requested_agent: str,
) -> list[tuple[object, list[str]]]:
    return [
        (
            bridge_next_action.main,
            [
                "--agent",
                requested_agent,
                "--bridge-root",
                str(base / "bridge-next"),
                "--json",
            ],
        ),
        (
            agent_next_task.main,
            [
                "--agent",
                requested_agent,
                "--bridge-root",
                str(base / "agent-next"),
                "--json",
            ],
        ),
        (
            bridge_loop_tick.main,
            [
                "--agent",
                requested_agent,
                "--bridge-root",
                str(base / "loop-tick"),
                "--json",
            ],
        ),
        (
            work_queue.main,
            [
                "--bridge-root",
                str(base / "queue"),
                "--json",
                "claim",
                "--agent",
                requested_agent,
                "--task-id",
                "identity-boundary",
                "--summary",
                "must not touch state",
            ],
        ),
        (
            agent_identity.main,
            [
                "--bridge-root",
                str(base / "profiles"),
                "--json",
                "register",
                "--agent",
                requested_agent,
                "--kind",
                "codex",
            ],
        ),
    ]


def _assert_json_mismatch(capsys: pytest.CaptureFixture[str]) -> None:
    captured = capsys.readouterr()
    assert json.loads(captured.out) == EXPECTED_MISMATCH
    assert captured.err == ""


@pytest.mark.parametrize(
    "malformed_bound",
    ["*", "Codex-Tools-1", " codex-tools-1", "codex-tools-1\nforged"],
)
def test_core_clis_reject_malformed_bound_identity_before_state_access(
    tmp_path: Path,
    malformed_bound: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", malformed_bound)

    for main, arguments in _core_identity_cli_cases(tmp_path, REQUESTED_AGENT):
        assert main(arguments) == 2
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert captured.err == ""
        assert report["decision"] == "identity_mismatch"
        assert report["requested_agent"] == REQUESTED_AGENT
        assert report["session_agent"] == malformed_bound
        assert report["errors"] == [
            f"AGENT_BRIDGE_AGENT {malformed_bound!r} is malformed"
        ]
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "malformed_agent",
    ["*", "Codex-Lead-1", " codex-lead-1", "codex-lead-1\nforged"],
)
def test_malformed_requested_agent_is_rejected_even_when_unbound(
    tmp_path: Path,
    malformed_agent: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)

    exit_code = work_queue.main(
        [
            "--bridge-root",
            str(tmp_path / "queue"),
            "--json",
            "claim",
            "--agent",
            malformed_agent,
            "--task-id",
            "malformed-requested-agent",
            "--summary",
            "must not touch state",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "identity_mismatch"
    assert report["errors"] == [f"--agent {malformed_agent!r} is malformed"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reserved_agent", ["operator", "system"])
@pytest.mark.parametrize("ambient", [None, "", "   "])
def test_unbound_reserved_agents_are_rejected_by_every_core_cli(
    tmp_path: Path,
    reserved_agent: str,
    ambient: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if ambient is None:
        monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", ambient)

    for main, arguments in _core_identity_cli_cases(tmp_path, reserved_agent):
        assert main(arguments) == 2
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert captured.err == ""
        assert report["decision"] == "identity_mismatch"
        assert report["requested_agent"] == reserved_agent
        assert "requires a verified bound or internal caller" in report["errors"][0]
        assert list(tmp_path.iterdir()) == []


def test_bound_operator_is_allowed_but_system_has_no_public_python_authority() -> None:
    assert (
        cli_identity_mismatch(
            "operator",
            environ={"AGENT_BRIDGE_AGENT": "operator"},
        )
        is None
    )
    report = cli_identity_mismatch(
        "system",
        environ={"AGENT_BRIDGE_AGENT": "system"},
    )
    assert report is not None
    assert report["decision"] == "identity_mismatch"
    assert report["errors"] == [
        "reserved --agent 'system' has no public Python CLI authority"
    ]


def test_malformed_identity_human_output_cannot_inject_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    malformed_bound = "codex-tools-1\nforged: yes"
    report = cli_identity_mismatch(
        REQUESTED_AGENT,
        environ={"AGENT_BRIDGE_AGENT": malformed_bound},
    )
    assert report is not None

    emit_identity_mismatch(report, as_json=False)

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "identity_mismatch",
        "safe_mode: read-only",
        f"requested_agent: {REQUESTED_AGENT}",
        r"session_agent: 'codex-tools-1\nforged: yes'",
    ]
    assert captured.err.splitlines() == [
        r"- AGENT_BRIDGE_AGENT 'codex-tools-1\nforged: yes' is malformed"
    ]


def test_core_bridge_clis_fail_before_bridge_state_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", SESSION_AGENT)
    monkeypatch.setattr(
        bridge_next_action,
        "resolve_bridge_root",
        _unexpected_state_access,
    )
    monkeypatch.setattr(
        agent_next_task,
        "_bridge_root_for_args",
        _unexpected_state_access,
    )
    monkeypatch.setattr(
        bridge_loop_tick,
        "resolve_bridge_root",
        _unexpected_state_access,
    )
    monkeypatch.setattr(work_queue, "_dispatch", _unexpected_state_access)

    exit_code = bridge_next_action.main(
        [
            "--agent",
            REQUESTED_AGENT,
            "--bridge-root",
            str(tmp_path / "bridge-next"),
            "--json",
        ]
    )
    assert exit_code == 2
    _assert_json_mismatch(capsys)

    exit_code = agent_next_task.main(
        [
            "--agent",
            REQUESTED_AGENT,
            "--bridge-root",
            str(tmp_path / "agent-next"),
            "--json",
        ]
    )
    assert exit_code == 2
    _assert_json_mismatch(capsys)

    exit_code = bridge_loop_tick.main(
        [
            "--agent",
            REQUESTED_AGENT,
            "--bridge-root",
            str(tmp_path / "loop-tick"),
            "--json",
        ]
    )
    assert exit_code == 2
    _assert_json_mismatch(capsys)

    exit_code = work_queue.main(
        [
            "--bridge-root",
            str(tmp_path / "queue"),
            "--json",
            "claim",
            "--agent",
            REQUESTED_AGENT,
            "--task-id",
            "must-not-exist",
            "--summary",
            "must not touch the queue",
        ]
    )
    assert exit_code == 2
    _assert_json_mismatch(capsys)


@pytest.mark.parametrize(
    ("main", "arguments"),
    [
        (
            bridge_next_action.main,
            ["--agent", REQUESTED_AGENT],
        ),
        (
            agent_next_task.main,
            ["--agent", REQUESTED_AGENT],
        ),
        (
            bridge_loop_tick.main,
            ["--agent", REQUESTED_AGENT],
        ),
        (
            work_queue.main,
            [
                "heartbeat",
                "--agent",
                REQUESTED_AGENT,
                "--task-id",
                "must-not-exist",
            ],
        ),
    ],
)
def test_core_bridge_clis_emit_same_human_mismatch(
    main,
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", SESSION_AGENT)

    assert main(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == (
        "identity_mismatch\n"
        "safe_mode: read-only\n"
        "requested_agent: codex-lead-1\n"
        "session_agent: codex-tools-1\n"
    )
    assert captured.err == (
        "- --agent 'codex-lead-1' does not match "
        "AGENT_BRIDGE_AGENT 'codex-tools-1'\n"
    )


def test_bridge_loop_tick_mismatch_subprocess_is_byte_for_byte_read_only(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        json.dumps(
            {
                "ts_utc": "2026-07-28T00:00:00Z",
                "agent": SESSION_AGENT,
                "type": "heartbeat",
                "task_id": "peer-heartbeat",
                "status": "active",
                "message": "peer heartbeat",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sentinel_path = bridge_root / "work_queue" / "claims" / "sentinel.bin"
    sentinel_path.parent.mkdir(parents=True)
    sentinel_path.write_bytes(b"\x00\xffidentity-boundary\x10")
    original_entries = sorted(
        path.relative_to(bridge_root).as_posix()
        for path in bridge_root.rglob("*")
    )
    original_state = _snapshot_files(bridge_root)

    env = os.environ.copy()
    env["AGENT_BRIDGE_AGENT"] = SESSION_AGENT
    completed = subprocess.run(
        [
            sys.executable,
            str(BRIDGE_LOOP_TICK),
            "--agent",
            REQUESTED_AGENT,
            "--bridge-root",
            str(bridge_root),
            "--inbox-dir",
            str(tmp_path / "operator-inbox"),
            "--emit-peer-activation",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == json.dumps(EXPECTED_MISMATCH, sort_keys=True) + "\n"
    assert completed.stderr == ""
    assert sorted(
        path.relative_to(bridge_root).as_posix()
        for path in bridge_root.rglob("*")
    ) == original_entries
    assert _snapshot_files(bridge_root) == original_state


def test_mismatched_queue_claim_release_and_heartbeat_leave_state_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    claim_task(
        agent=REQUESTED_AGENT,
        task_id="existing-task",
        summary="must remain claimed",
        bridge_root=bridge_root,
        now_utc=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    original_state = _snapshot_files(bridge_root)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", SESSION_AGENT)

    commands = [
        [
            "claim",
            "--agent",
            REQUESTED_AGENT,
            "--task-id",
            "new-task",
            "--summary",
            "must not be created",
        ],
        [
            "release",
            "--agent",
            REQUESTED_AGENT,
            "--task-id",
            "existing-task",
        ],
        [
            "heartbeat",
            "--agent",
            REQUESTED_AGENT,
            "--task-id",
            "existing-task",
        ],
    ]
    for command in commands:
        exit_code = work_queue.main(
            ["--bridge-root", str(bridge_root), "--json", *command]
        )
        assert exit_code == 2
        _assert_json_mismatch(capsys)
        assert _snapshot_files(bridge_root) == original_state


def test_queue_commands_without_agent_ignore_session_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", SESSION_AGENT)

    cases = [
        (["list"], "listed"),
        (["stale", "--max-age-seconds", "1"], "stale_claims"),
        (["check-overlap", "--write-scope", "tools/example.py"], "scope_overlap"),
    ]
    for command, expected_decision in cases:
        exit_code = work_queue.main(
            ["--bridge-root", str(bridge_root), "--json", *command]
        )
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert captured.err == ""
        assert exit_code == 0
        assert report["decision"] == expected_decision


@pytest.mark.parametrize("ambient", [None, "", " \t "])
def test_unset_or_blank_session_identity_keeps_legacy_cli_behavior(
    tmp_path: Path,
    ambient: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if ambient is None:
        monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", ambient)

    bridge_root = tmp_path / ".agent-bridge"
    exit_code = work_queue.main(
        [
            "--bridge-root",
            str(bridge_root),
            "--json",
            "claim",
            "--agent",
            REQUESTED_AGENT,
            "--task-id",
            "legacy-task",
            "--summary",
            "legacy-compatible claim",
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert exit_code == 0
    assert report["decision"] == "claimed"


@pytest.mark.parametrize("ambient", [None, "", " \t "])
def test_bridge_loop_tick_unset_or_blank_identity_keeps_legacy_behavior(
    tmp_path: Path,
    ambient: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if ambient is None:
        monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", ambient)

    exit_code = bridge_loop_tick.main(
        [
            "--agent",
            REQUESTED_AGENT,
            "--bridge-root",
            str(tmp_path / ".agent-bridge"),
            "--inbox-dir",
            str(tmp_path / "operator-inbox"),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert exit_code == 0
    assert report["ok"] is True
    assert report["agent"] == REQUESTED_AGENT


def test_matching_session_identity_is_allowed() -> None:
    assert (
        cli_identity_mismatch(
            SESSION_AGENT,
            environ={"AGENT_BRIDGE_AGENT": SESSION_AGENT},
        )
        is None
    )


def test_nonblank_session_identity_requires_an_exact_match() -> None:
    report = cli_identity_mismatch(
        SESSION_AGENT,
        environ={"AGENT_BRIDGE_AGENT": f" {SESSION_AGENT} "},
    )

    assert report is not None
    assert report["decision"] == "identity_mismatch"
    assert report["safe_mode"] == "read-only"


def test_library_recommend_and_evaluate_ignore_ambient_session_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", SESSION_AGENT)
    now_utc = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"

    recommendation = bridge_next_action.recommend_next_action(
        agent=REQUESTED_AGENT,
        events=[],
        claims=[],
        bridge_root=bridge_root,
        now_utc=now_utc,
    )
    assert recommendation["decision"] == "bridge_next_action"
    assert recommendation["agent"] == REQUESTED_AGENT

    evaluation = agent_next_task.evaluate_agent_next_task(
        agent=REQUESTED_AGENT,
        events_path=events_path,
        bridge_root=bridge_root,
        now_utc=now_utc,
    )
    assert evaluation["decision"] != "identity_mismatch"
    assert evaluation.get("exit_code", 0) == 0
