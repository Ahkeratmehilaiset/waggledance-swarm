# SPDX-License-Identifier: BUSL-1.1
"""Tests for .agent-bridge/bin/Write-AgentEvent.ps1."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from waggledance.core.bridge_event_schema import validate_event_line

REQUIRES_TASK_ID_CASES = [
    ("claim", ""),
    ("release", ""),
    ("done", ""),
    ("handoff", ""),
    ("blocked", ""),
    ("message", "acknowledged"),
    ("message", "received"),
    ("message", "seen"),
]
MAIN_SHA = "a" * 40
PR_HEAD_SHA = "b" * 40
PROFILE_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "22222222-3333-4444-5555-666666666666"
CODEX_LEAD_UUID = "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
CODEX_TOOLS_UUID = "7a8af68d-20bc-4598-9953-23c5dd98b102"
CLAUDE_RCO1_UUID = "2b2f6ff9-06c2-4ec8-b526-f10071ce7103"
CLAUDE_RCO2_UUID = "76739997-0058-41a2-8514-78ff295537aa"
FABLE_UUID = "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80"


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for Write-AgentEvent smoke tests")
    return executable


def _run_writer(
    root: Path,
    runtime_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return _run_bridge_script(root, runtime_root, "Write-AgentEvent.ps1", *args)


def _run_bridge_script(
    root: Path,
    runtime_root: Path,
    script_name: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in (
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
    ):
        env.pop(name, None)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / ".agent-bridge" / "bin" / script_name),
            *args,
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _grok_freshness_payload(**overrides: object) -> str:
    freshness: dict[str, object] = {
        "freshness_ok": True,
        "remote_main_sha": MAIN_SHA,
        "local_origin_main_sha": MAIN_SHA,
        "worktree_head": MAIN_SHA,
        "pr_head_sha": PR_HEAD_SHA,
    }
    freshness.update(overrides)
    return json.dumps({"freshness": freshness}, sort_keys=True)


def _rco_pass_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "head": MAIN_SHA,
        "canonical_task_id": "codex-lead-1-rco-pass-task-binding-smoke",
    }
    payload.update(overrides)
    return json.dumps(payload, sort_keys=True)


def _write_agent_profile(
    runtime_root: Path,
    *,
    agent: str = "codex",
    agent_uuid: str = PROFILE_UUID,
) -> None:
    agents_dir = runtime_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent}.json").write_text(
        json.dumps({"agent_id": agent, "agent_uuid": agent_uuid}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(("event_type", "status"), REQUIRES_TASK_ID_CASES)
def test_task_id_required_events_fail_before_runtime_write(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    args = [
        "-Agent",
        "codex",
        "-Type",
        event_type,
        "-Message",
        "missing task id",
    ]
    if status:
        args.extend(["-Status", status])

    completed = _run_writer(root, runtime_root, *args)

    assert completed.returncode != 0
    assert "requires non-empty -TaskId" in completed.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "note"),
        ("status", ""),
    ],
)
def test_events_without_task_id_requirement_can_write(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    args = [
        "-Agent",
        "codex",
        "-Type",
        event_type,
        "-Message",
        "task id optional",
    ]
    if status:
        args.extend(["-Status", status])

    completed = _run_writer(root, runtime_root, *args)

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["type"] == event_type
    assert event["task_id"] == ""
    validate_event_line(line)


def test_task_scoped_event_with_task_id_writes_valid_event(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "handoff",
        "-TaskId",
        "bridge-writer-smoke",
        "-To",
        "claude",
        "-Message",
        "valid handoff",
    )

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["task_id"] == "bridge-writer-smoke"
    validate_event_line(line)


def test_null_payload_json_writes_empty_object_payload(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "status",
        "-Status",
        "payload-null-smoke",
        "-Message",
        "payload null should normalize",
        "-PayloadJson",
        "null",
    )

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["payload"] == {}
    validate_event_line(line)


def test_empty_payload_json_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "status",
        "-Status",
        "payload-empty-smoke",
        "-Message",
        "payload empty should fail",
        "-PayloadJson",
        "",
    )

    assert completed.returncode != 0
    assert "payload must be valid JSON" in completed.stderr
    assert not runtime_root.exists()


def test_whitespace_payload_json_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "status",
        "-Status",
        "payload-whitespace-smoke",
        "-Message",
        "payload whitespace should fail",
        "-PayloadJson",
        "   ",
    )

    assert completed.returncode != 0
    assert "payload must be valid JSON" in completed.stderr
    assert not runtime_root.exists()


def test_wake_request_requires_to_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "wake_request",
        "-TaskId",
        "wake-request-missing-to",
        "-Message",
        "missing wake target",
    )

    assert completed.returncode != 0
    assert "type=wake_request requires non-empty -To" in completed.stderr
    assert not runtime_root.exists()


def test_operator_bridge_follow_nudge_duplicate_is_idempotent(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    args = [
        "-Agent",
        "operator",
        "-Type",
        "wake_request",
        "-TaskId",
        "bridge-follow-nudge-20260614",
        "-Status",
        "open",
        "-To",
        "claude-rco-2",
        "-Message",
        "poll the bridge",
    ]

    first = _run_writer(root, runtime_root, *args)
    second = _run_writer(root, runtime_root, *args)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    outbox_path = next((runtime_root / "outbox" / "operator").glob("*.jsonl"))
    event_lines = events_path.read_text(encoding="utf-8").splitlines()
    outbox_lines = outbox_path.read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == 1
    assert len(outbox_lines) == 1
    event = json.loads(event_lines[0])
    assert event["agent"] == "operator"
    assert event["task_id"] == "bridge-follow-nudge-20260614"
    validate_event_line(event_lines[0])


def test_operator_bridge_follow_nudge_writes_after_target_activity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    wake_args = [
        "-Agent",
        "operator",
        "-Type",
        "wake_request",
        "-TaskId",
        "bridge-follow-nudge-20260614",
        "-Status",
        "open",
        "-To",
        "claude-rco-2",
        "-Message",
        "poll the bridge",
    ]

    first = _run_writer(root, runtime_root, *wake_args)
    target_activity = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "claude-rco-2",
        "-Type",
        "message",
        "-TaskId",
        "claude-rco-2-resumed",
        "-Status",
        "active",
        "-Message",
        "target resumed polling",
        "-AgentUuid",
        CLAUDE_RCO2_UUID,
    )
    second = _run_writer(root, runtime_root, *wake_args)

    assert first.returncode == 0, first.stderr
    assert target_activity.returncode == 0, target_activity.stderr
    assert second.returncode == 0, second.stderr
    event_lines = (
        (runtime_root / "shared" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    events = [json.loads(line) for line in event_lines]
    assert [event["type"] for event in events] == [
        "wake_request",
        "message",
        "wake_request",
    ]
    assert events[2]["agent"] == "operator"
    assert events[2]["to"] == "claude-rco-2"


def test_operator_bridge_follow_nudge_multi_target_writes_after_member_activity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    wake_args = [
        "-Agent",
        "operator",
        "-Type",
        "wake_request",
        "-TaskId",
        "bridge-follow-nudge-20260614",
        "-Status",
        "open",
        "-To",
        "codex-lead-1,claude-rco-1",
        "-Message",
        "poll the bridge",
    ]

    first = _run_writer(root, runtime_root, *wake_args)
    target_activity = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-lead-1",
        "-Type",
        "message",
        "-TaskId",
        "codex-lead-1-resumed",
        "-Status",
        "active",
        "-Message",
        "one target resumed polling",
        "-AgentUuid",
        CODEX_LEAD_UUID,
    )
    second = _run_writer(root, runtime_root, *wake_args)

    assert first.returncode == 0, first.stderr
    assert target_activity.returncode == 0, target_activity.stderr
    assert second.returncode == 0, second.stderr
    event_lines = (
        (runtime_root / "shared" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    events = [json.loads(line) for line in event_lines]
    assert [event["type"] for event in events] == [
        "wake_request",
        "message",
        "wake_request",
    ]
    assert events[2]["agent"] == "operator"
    assert events[2]["to"] == "codex-lead-1,claude-rco-1"
    for line in event_lines:
        validate_event_line(line)


def test_regex_agent_id_writes_valid_event_and_outbox(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-2",
        "-Type",
        "message",
        "-To",
        "claude-1",
        "-Message",
        "valid multi-agent bridge message",
    )

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["agent"] == "codex-2"
    assert event["to"] == "claude-1"
    assert (runtime_root / "outbox" / "codex-2").exists()
    validate_event_line(line)


@pytest.mark.parametrize(
    "target",
    [
        "docs/benchmarks/LOCAL_OLLAMA_MODEL_SWEEP_2026.md",
        "Gpt",
    ],
)
def test_invalid_to_agent_id_fails_before_runtime_write(
    tmp_path: Path,
    target: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-To",
        target,
        "-Message",
        "invalid target",
    )

    assert completed.returncode != 0
    # pwsh on Linux may wrap/truncate long throw messages but preserves the
    # rejected token in stderr.
    assert target in completed.stderr
    assert not runtime_root.exists()


def test_separator_only_to_agent_ids_fail_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-To",
        ",,,",
        "-Message",
        "separator only target",
    )

    assert completed.returncode != 0
    assert "to must be empty or comma-separated agents" in completed.stderr
    assert not runtime_root.exists()


def test_comma_separated_to_agent_ids_write_valid_event(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    targets = "codex-lead-1,codex-tools-1,claude-rco-1,operator"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-To",
        targets,
        "-Message",
        "valid multi-target message",
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["to"] == targets
    validate_event_line(line)


def test_github_main_target_ref_writes_valid_event(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "done",
        "-TaskId",
        "bridge-main-merge-observed",
        "-Status",
        "merged_observed",
        "-To",
        "github/main",
        "-Message",
        "main branch merge observed",
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["to"] == "github/main"
    validate_event_line(line)


def test_role_uuid_capability_metadata_is_optional_and_validated(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    agent_uuid = "11111111-2222-3333-4444-555555555555"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-impl-1",
        "-Type",
        "message",
        "-To",
        "claude-rco-1",
        "-Message",
        "metadata smoke",
        "-Role",
        "impl",
        "-AgentUuid",
        agent_uuid,
        "-SessionId",
        "codex-impl-1-20260523T140000Z",
        "-Capabilities",
        "bridge_event,work_queue",
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["role"] == "impl"
    assert event["agent_uuid"] == agent_uuid
    assert event["session_id"] == "codex-impl-1-20260523T140000Z"
    assert event["capabilities"] == ["bridge_event", "work_queue"]
    validate_event_line(line)


def test_grok_response_requires_freshness_payload_before_runtime_write(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "grok-scout-1",
        "-Type",
        "message",
        "-TaskId",
        "grok-dispatch-selftest",
        "-Status",
        "grok_response",
        "-To",
        "codex-lead-1",
        "-Message",
        "missing freshness",
    )

    assert completed.returncode != 0
    assert "grok freshness proof required" in completed.stderr
    assert not runtime_root.exists()


def test_grok_response_with_freshness_payload_writes_valid_event(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "grok-scout-1",
        "-Type",
        "message",
        "-TaskId",
        "grok-dispatch-selftest",
        "-Status",
        "grok_response",
        "-To",
        "codex-lead-1",
        "-Message",
        "fresh response",
        "-PayloadJson",
        _grok_freshness_payload(),
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["payload"]["freshness"]["freshness_ok"] is True
    validate_event_line(line)


def test_rco_pass_accepts_current_style_task_id_only_binding(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "codex-lead-1-rco-pass-task-binding-smoke"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "claude-rco-1",
        "-Type",
        "decision",
        "-TaskId",
        task_id,
        "-Status",
        "rco_pass",
        "-Message",
        f"exact-head pass {MAIN_SHA}",
        "-PayloadJson",
        json.dumps({"head": MAIN_SHA, "operator_gated": True}, sort_keys=True),
        "-AgentUuid",
        CLAUDE_RCO1_UUID,
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["task_id"] == task_id
    assert event["payload"] == {"head": MAIN_SHA, "operator_gated": True}
    validate_event_line(line, agent_uuid_by_id={"claude-rco-1": CLAUDE_RCO1_UUID})


def test_rco_pass_rejects_wrong_task_against_canonical_binding(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "claude-rco-1",
        "-Type",
        "decision",
        "-TaskId",
        "codex-lead-1/bridge-fresh-incoming-20260613",
        "-Status",
        "rco_pass",
        "-Message",
        f"exact-head pass {MAIN_SHA}",
        "-PayloadJson",
        _rco_pass_payload(
            canonical_task_id=(
                "codex-lead-1-bridge-next-action-fresh-incoming-priority-20260613"
            ),
        ),
    )

    assert completed.returncode != 0
    assert "rco_pass task_id does not match canonical task binding" in completed.stderr
    assert not runtime_root.exists()


def test_rco_pass_rejects_old_event_template_task_binding(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "claude-rco-1",
        "-Type",
        "decision",
        "-TaskId",
        "codex-tools-1/operator-feedback-action-bridge-event-template-20260613",
        "-Status",
        "rco_pass",
        "-Message",
        f"exact-head pass {MAIN_SHA}",
        "-PayloadJson",
        _rco_pass_payload(
            branch="codex-tools-1/operator-feedback-action-bridge-template-20260613",
        ),
    )

    assert completed.returncode != 0
    assert "rco_pass task_id does not match canonical task binding" in completed.stderr
    assert not runtime_root.exists()


def test_rco_pass_accepts_canonical_hyphen_task_binding(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "codex-lead-1-rco-pass-task-binding-smoke"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "claude-rco-1",
        "-Type",
        "decision",
        "-TaskId",
        task_id,
        "-Status",
        "rco_pass",
        "-Message",
        f"exact-head pass {MAIN_SHA}",
        "-PayloadJson",
        _rco_pass_payload(canonical_task_id=task_id),
        "-AgentUuid",
        CLAUDE_RCO1_UUID,
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["task_id"] == task_id
    validate_event_line(line, agent_uuid_by_id={"claude-rco-1": CLAUDE_RCO1_UUID})


def test_rco_pass_accepts_deterministic_slash_branch_alias(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "claude-rco-1",
        "-Type",
        "decision",
        "-TaskId",
        "codex-tools-1-operator-feedback-action-bridge-template-20260613",
        "-Status",
        "rco_pass",
        "-Message",
        f"exact-head pass {MAIN_SHA}",
        "-PayloadJson",
        _rco_pass_payload(
            branch="codex-tools-1/operator-feedback-action-bridge-template-20260613",
        ),
        "-AgentUuid",
        CLAUDE_RCO1_UUID,
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert (
        event["task_id"]
        == "codex-tools-1-operator-feedback-action-bridge-template-20260613"
    )
    validate_event_line(line, agent_uuid_by_id={"claude-rco-1": CLAUDE_RCO1_UUID})


def test_non_rco_build_consensus_pass_is_unaffected(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-tools-1",
        "-Type",
        "decision",
        "-TaskId",
        "codex-tools-1-build-consensus-smoke",
        "-Status",
        "build_consensus_pass",
        "-Message",
        "tools build consensus",
        "-PayloadJson",
        json.dumps({"head": MAIN_SHA}, sort_keys=True),
        "-AgentUuid",
        CODEX_TOOLS_UUID,
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["status"] == "build_consensus_pass"
    validate_event_line(line, agent_uuid_by_id={"codex-tools-1": CODEX_TOOLS_UUID})


def test_invalid_payload_json_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "invalid payload json",
        "-PayloadJson",
        "{not-json}",
    )

    assert completed.returncode != 0
    assert "payload must be valid JSON" in completed.stderr
    assert "{not-json}" not in completed.stderr + completed.stdout
    assert not runtime_root.exists()


def test_grok_response_rejects_self_declared_pr_head_worktree_freshness(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "grok-scout-1",
        "-Type",
        "message",
        "-TaskId",
        "grok-dispatch-selftest",
        "-Status",
        "grok_response",
        "-To",
        "codex-lead-1",
        "-Message",
        "stale worktree",
        "-PayloadJson",
        _grok_freshness_payload(worktree_head=PR_HEAD_SHA),
    )

    assert completed.returncode != 0
    assert "grok freshness worktree sha mismatch" in completed.stderr
    assert not runtime_root.exists()


def test_grok_response_accepts_main_worktree_with_pr_head_metadata(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "grok-scout-1",
        "-Type",
        "message",
        "-TaskId",
        "grok-dispatch-selftest",
        "-Status",
        "grok_response",
        "-To",
        "codex-lead-1",
        "-Message",
        "main-bound fresh response",
        "-PayloadJson",
        _grok_freshness_payload(worktree_head=MAIN_SHA),
    )

    assert completed.returncode == 0, completed.stderr
    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["payload"]["freshness"]["worktree_head"] == MAIN_SHA
    validate_event_line(line)


def test_grok_response_rejects_unmatched_worktree_freshness_before_runtime_write(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "grok-scout-1",
        "-Type",
        "message",
        "-TaskId",
        "grok-dispatch-selftest",
        "-Status",
        "grok_response",
        "-To",
        "codex-lead-1",
        "-Message",
        "stale worktree",
        "-PayloadJson",
        _grok_freshness_payload(worktree_head="c" * 40),
    )

    assert completed.returncode != 0
    assert "grok freshness worktree sha mismatch" in completed.stderr
    assert not runtime_root.exists()


def test_invalid_agent_id_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "Codex-2",
        "-Type",
        "message",
        "-Message",
        "invalid uppercase agent id",
    )

    assert completed.returncode != 0
    assert not runtime_root.exists()


def test_invalid_agent_uuid_fails_before_runtime_write(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "invalid uuid",
        "-AgentUuid",
        "not-a-uuid",
    )

    assert completed.returncode != 0
    assert "agent_uuid must be a UUID" in completed.stderr
    assert not runtime_root.exists()


def test_profile_bound_agent_uuid_required_before_runtime_write(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    _write_agent_profile(runtime_root)

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "missing profile-bound uuid",
    )

    assert completed.returncode != 0
    assert "agent_uuid required by bridge agent profile" in completed.stderr
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_profile_bound_agent_uuid_mismatch_fails_before_runtime_write(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    _write_agent_profile(runtime_root)

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "wrong profile-bound uuid",
        "-AgentUuid",
        OTHER_UUID,
    )

    assert completed.returncode != 0
    assert "agent_uuid does not match bridge agent profile" in completed.stderr
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_profile_bound_agent_uuid_match_writes_and_validates(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    _write_agent_profile(runtime_root)

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex",
        "-Type",
        "message",
        "-Message",
        "matching profile-bound uuid",
        "-AgentUuid",
        PROFILE_UUID,
    )

    assert completed.returncode == 0, completed.stderr
    line = (runtime_root / "shared" / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()[0]
    event = json.loads(line)
    assert event["agent_uuid"] == PROFILE_UUID
    validate_event_line(line, agent_uuid_by_id={"codex": PROFILE_UUID})


def test_registry_bound_agent_uuid_required_without_runtime_profile(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-tools-1",
        "-Type",
        "message",
        "-Message",
        "missing registry-bound uuid",
    )

    assert completed.returncode != 0
    assert "agent_uuid required by bridge identity registry" in completed.stderr
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_registry_bound_agent_uuid_mismatch_fails_before_runtime_write(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-tools-1",
        "-Type",
        "message",
        "-Message",
        "wrong registry-bound uuid",
        "-AgentUuid",
        FABLE_UUID,
    )

    assert completed.returncode != 0
    assert "agent_uuid does not match bridge identity registry" in completed.stderr
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_registry_bound_agent_uuid_match_writes_without_runtime_profile(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(
        root,
        runtime_root,
        "-Agent",
        "codex-tools-1",
        "-Type",
        "message",
        "-Message",
        "matching registry-bound uuid",
        "-AgentUuid",
        CODEX_TOOLS_UUID,
    )

    assert completed.returncode == 0, completed.stderr
    line = (runtime_root / "shared" / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()[0]
    event = json.loads(line)
    assert event["agent_uuid"] == CODEX_TOOLS_UUID


@pytest.mark.parametrize(
    ("args", "label"),
    [
        (
            [
                "-Agent",
                "codex",
                "-Type",
                "message",
                "-Message",
                "contains PRIVATE_MARKER",
            ],
            "message",
        ),
        (
            [
                "-Agent",
                "codex",
                "-Type",
                "message",
                "-Message",
                "contains private_marker",
            ],
            "message",
        ),
        (
            [
                "-Agent",
                "codex",
                "-Type",
                "message",
                "-Message",
                "safe public text",
                "-PayloadJson",
                '{"note":"synthetic_secret_DO_NOT_LEAK"}',
            ],
            "payload",
        ),
        (
            [
                "-Agent",
                "codex",
                "-Type",
                "message",
                "-Message",
                "safe public text",
                "-PayloadJson",
                '{"note":"synthetic_secret_\\u005fDO_NOT_LEAK"}',
            ],
            "payload",
        ),
        (
            [
                "-Agent",
                "codex",
                "-Type",
                "message",
                "-Message",
                "safe public text",
                "-To",
                "claude_PRIVATE_MARKER",
            ],
            "to",
        ),
        (
            [
                "-Agent",
                "codex",
                "-Type",
                "message",
                "-Message",
                "safe public text",
                "-TaskId",
                "task_PRIVATE_MARKER",
            ],
            "task_id",
        ),
    ],
)
def test_private_markers_fail_before_runtime_write(
    tmp_path: Path,
    args: list[str],
    label: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    completed = _run_writer(root, runtime_root, *args)

    assert completed.returncode != 0
    assert f"Bridge event {label} contains a private marker" in completed.stderr
    assert "synthetic_secret_DO_NOT_LEAK" not in completed.stderr
    assert not runtime_root.exists()


def test_claim_and_release_accept_regex_agent_id(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"

    claim = _run_bridge_script(
        root,
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex-2",
        "-TaskId",
        "multi-agent-claim-smoke",
        "-Summary",
        "regex agent claim smoke",
    )
    assert claim.returncode == 0, claim.stderr

    release = _run_bridge_script(
        root,
        runtime_root,
        "Release-AgentTask.ps1",
        "-Agent",
        "codex-2",
        "-TaskId",
        "multi-agent-claim-smoke",
        "-Status",
        "done",
        "-Message",
        "regex agent release smoke",
    )
    assert release.returncode == 0, release.stderr

    events_path = runtime_root / "shared" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["agent"] for line in lines} == {"codex-2"}
    for line in lines:
        validate_event_line(line)


def test_claim_records_role_uuid_capabilities_and_lease(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_root = tmp_path / "bridge-runtime"
    agent_uuid = "11111111-2222-3333-4444-555555555555"

    claim = _run_bridge_script(
        root,
        runtime_root,
        "Claim-AgentTask.ps1",
        "-Agent",
        "codex-impl-1",
        "-TaskId",
        "metadata-claim-smoke",
        "-Summary",
        "metadata claim smoke",
        "-Mode",
        "write",
        "-WriteScope",
        "tools/foo.py",
        "-Role",
        "impl",
        "-AgentUuid",
        agent_uuid,
        "-Capabilities",
        "bridge_event,work_queue",
        "-LeaseSeconds",
        "600",
    )
    assert claim.returncode == 0, claim.stderr

    claim_path = runtime_root / "work_queue" / "claims" / "metadata-claim-smoke.json"
    claim_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    assert claim_payload["role"] == "impl"
    assert claim_payload["agent_uuid"] == agent_uuid
    assert claim_payload["capabilities"] == ["bridge_event", "work_queue"]
    assert claim_payload["lease_seconds"] == 600
    assert claim_payload["claim_lease_expires_utc"]

    line = (
        (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").strip()
    )
    event = json.loads(line)
    assert event["role"] == "impl"
    assert event["agent_uuid"] == agent_uuid
    assert event["capabilities"] == ["bridge_event", "work_queue"]
    validate_event_line(line)
