# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/bridge_loop_tick.py.

All bridge state is synthetic and the PR-status query is an injected fake, so
no real GitHub call fires and the live repo is never touched.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.bridge_loop_tick import (
    MERGE_DRIVER_COMMAND,
    WAKEUP_ACT_NOW,
    WAKEUP_IN_FLIGHT,
    WAKEUP_QUIET,
    _git_ref_sha,
    build_loop_tick,
    build_pr_status_snapshot_fn,
    emit_peer_activation_event,
    evaluate_merge_ready,
    main,
    materialize_peer_activation_event,
    my_unmerged_rco_passes,
    peer_activation_recommendation,
    peer_has_active_pr_producing_claim,
)
from tools.bridge_event_writer import BridgeEventWriteError, _PortableTestBackend
from waggledance.core.work_queue import claim_task

NOW = datetime(2026, 5, 22, 14, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
BASE = "c" * 40
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


@pytest.fixture(autouse=True)
def _valid_work_queue_owner_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_RUN_ID", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "pytest-session")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "a" * 64)
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_PID", str(os.getpid()))
    monkeypatch.setenv(
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
        "2026-07-28T00:00:00Z",
    )
    for name in (
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
    ):
        monkeypatch.delenv(name, raising=False)


def _with_agent_uuid(event: dict) -> dict:
    agent = str(event.get("agent", ""))
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _rco_request(task: str, *, frm: str = "codex", to: str = "claude", ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": frm,
        "to": to,
        "type": "handoff",
        "task_id": task,
        "status": "rco_requested",
        "message": "ready for RCO",
    }


def _rco_pass(task: str, *, pr: int, head: str, ts: str, frm: str = "claude") -> dict:
    return _with_agent_uuid({
        "ts_utc": ts,
        "agent": frm,
        "to": "codex,operator",
        "type": "decision",
        "task_id": task,
        "status": "rco_pass",
        "message": "PASS",
        "payload": {"pr": pr, "head": head},
    })


def _claim(agent: str, task: str, *, ts: str = "2026-05-22T12:59:00Z") -> dict:
    return _with_agent_uuid({
        "ts_utc": ts,
        "agent": agent,
        "type": "claim",
        "task_id": task,
        "status": "active",
        "message": "claimed",
        "write_scope": ["*"],
        "payload": {},
    })


def _rco_gate_pass(task: str, *, pr: int, head: str, ts: str) -> dict:
    event = _rco_pass(task, pr=pr, head=head, ts=ts, frm="claude-rco-1")
    event["message"] = f"RCO_PASS exact head {head}"
    return event


def _build_consensus(
    task: str,
    *,
    head: str,
    agent: str,
    ts: str,
) -> dict:
    return _with_agent_uuid({
        "ts_utc": ts,
        "agent": agent,
        "to": "claude-rco-1,operator",
        "type": "decision",
        "task_id": task,
        "status": "build_consensus_pass",
        "message": f"build consensus at exact head {head}",
        "payload": {},
    })


def _three_identity_consensus(task: str, *, pr: int, head: str) -> list[dict]:
    return [
        _claim("fable-5", task),
        _build_consensus(
            task,
            head=head,
            agent="codex-lead-1",
            ts="2026-05-22T13:29:00Z",
        ),
        _build_consensus(
            task,
            head=head,
            agent="codex-tools-1",
            ts="2026-05-22T13:30:00Z",
        ),
        _rco_gate_pass(task, pr=pr, head=head, ts="2026-05-22T13:31:00Z"),
    ]


def _done_merged(task: str, *, pr: int, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": "claude",
        "type": "done",
        "task_id": task,
        "status": "merged",
        "payload": {"pr": pr},
    }


def _heartbeat(agent: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "heartbeat",
        "task_id": f"{agent}-heartbeat",
        "status": "active",
        "message": f"{agent} heartbeat",
    }


def _finding(agent: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "finding",
        "task_id": f"{agent}-finding",
        "status": "reported",
        "message": "substantive work",
    }


def _peer_activation_sent(agent: str, peer: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": peer,
        "type": "handoff",
        "task_id": f"peer-activation-{peer}-wd-advantage-scout-2026-05-22-13-59",
        "status": "scout_requested",
        "message": "please scout",
    }


def _events_file(bridge_root: Path, events: list[dict]) -> Path:
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def _format_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _green_snapshot(pr: int, head: str = HEAD, task: str = "t1") -> dict:
    material = github_pr_git_identity_evidence(
        {
            "author": {
                "login": "Ahkeratmehilaiset",
                "name": "",
                "email": "",
            },
            "commits": [
                {
                    "oid": head,
                    "authors": [
                        {
                            "name": "Jani",
                            "email": "jani@jkhservice.fi",
                            "login": "",
                        }
                    ],
                }
            ],
        },
        expected_head_sha=head,
    )
    identities = material.pop("identities")
    return {
        "pr_number": pr,
        "head_sha": head,
        "head_ref": task,
        "base_sha": BASE,
        "mergeable": "MERGEABLE",
        "state": "OPEN",
        "is_draft": False,
        "author_agent": "fable-5",
        "changed_paths": ["tools/idle_daily_summary.py"],
        "git_identities": identities,
        "git_identity_evidence": material,
        "checks": [
            {
                "name": "test",
                "state": "",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            }
        ],
    }


# --- stale-base checked PR snapshot function -------------------------------


def test_git_ref_sha_reads_origin_main_sha():
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=f"{BASE}\n")

    assert _git_ref_sha(runner=runner) == BASE
    assert calls == [["git", "rev-parse", "--verify", "origin/main"]]


def test_git_ref_sha_rejects_unresolved_or_malformed_ref():
    def unresolved(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal")

    with pytest.raises(RuntimeError, match="could not resolve origin/main"):
        _git_ref_sha(runner=unresolved)

    def malformed(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="abc123\n")

    with pytest.raises(RuntimeError, match="40-char lowercase sha"):
        _git_ref_sha(runner=malformed)


def test_pr_status_snapshot_fn_passes_resolved_expected_base_sha():
    calls: list[dict] = []

    def ref_sha_fn(ref: str) -> str:
        assert ref == "origin/main"
        return BASE

    def snapshot_builder(**kwargs) -> dict:
        calls.append(kwargs)
        return _green_snapshot(kwargs["pr_number"])

    snapshot_fn = build_pr_status_snapshot_fn(
        repo="Ahkeratmehilaiset/waggledance-swarm",
        ref_sha_fn=ref_sha_fn,
        snapshot_builder=snapshot_builder,
    )

    assert snapshot_fn(900)["pr_number"] == 900
    assert calls == [
        {
            "pr_number": 900,
            "repo": "Ahkeratmehilaiset/waggledance-swarm",
            "expected_base_sha": BASE,
        }
    ]


def test_cli_defaults_to_runtime_bridge_root_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    _events_file(runtime_bridge, [_heartbeat("codex-tools-1", "2026-05-22T13:55:00Z")])
    claim_task(
        agent="codex-tools-1",
        task_id="runtime-loop-claim",
        summary="runtime bridge loop claim",
        mode="read-only",
        bridge_root=runtime_bridge,
    )
    inbox = tmp_path / "operator_inbox"
    inbox.mkdir()

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    exit_code = main(
        [
            "--agent",
            "codex-tools-1",
            "--inbox-dir",
            str(inbox),
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["next_action"] == "continue_claim"
    assert report["next_action_detail"]["task_id"] == "runtime-loop-claim"


@pytest.mark.skipif(os.name != "nt", reason="production bridge writes are Windows-only")
def test_cli_emit_peer_activation_uses_runtime_bridge_root_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    now = datetime.now(timezone.utc)
    events_path = _events_file(
        runtime_bridge,
        [
            _finding("codex-lead-1", _format_z(now - timedelta(minutes=40))),
            _heartbeat("codex-lead-1", _format_z(now - timedelta(minutes=5))),
        ],
    )
    inbox = tmp_path / "operator_inbox"
    inbox.mkdir()

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    exit_code = main(
        [
            "--agent",
            "codex-tools-1",
            "--inbox-dir",
            str(inbox),
            "--emit-peer-activation",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["peer_activation"]["emitted"] is True
    assert str(runtime_bridge) in report["peer_activation"]["emitted_path"]
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events[-1]["agent"] == "codex-tools-1"
    assert events[-1]["to"] == "codex-lead-1"
    assert events[-1]["status"] == "scout_requested"


def test_cli_custom_events_write_failure_preserves_typed_decision(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / "bridge-never-created"
    custom_events = tmp_path / "custom-events.jsonl"
    now = datetime.now(timezone.utc)
    custom_events.write_text(
        "\n".join(
            json.dumps(event, sort_keys=True)
            for event in [
                _finding("codex-lead-1", _format_z(now - timedelta(minutes=40))),
                _heartbeat("codex-lead-1", _format_z(now - timedelta(minutes=5))),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    inbox = tmp_path / "operator_inbox"
    inbox.mkdir()

    exit_code = main(
        [
            "--agent",
            "codex-tools-1",
            "--bridge-root",
            str(bridge_root),
            "--events",
            str(custom_events),
            "--inbox-dir",
            str(inbox),
            "--emit-peer-activation",
            "--json",
        ]
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "bridge_write_failed"
    assert "non-canonical" in report["errors"][0]
    assert not bridge_root.exists()


def test_cli_peer_activation_default_is_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    now = datetime.now(timezone.utc)
    events_path = _events_file(
        runtime_bridge,
        [
            _finding("codex-lead-1", _format_z(now - timedelta(minutes=40))),
            _heartbeat("codex-lead-1", _format_z(now - timedelta(minutes=5))),
        ],
    )
    original_events = events_path.read_text(encoding="utf-8")
    inbox = tmp_path / "operator_inbox"
    inbox.mkdir()

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    exit_code = main(
        [
            "--agent",
            "codex-tools-1",
            "--inbox-dir",
            str(inbox),
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["peer_activation"]["needed"] is True
    assert "emitted" not in report["peer_activation"]
    assert events_path.read_text(encoding="utf-8") == original_events
    assert not (runtime_bridge / "outbox" / "codex-tools-1").exists()
    assert not (runtime_bridge / "shared" / "last_codex-tools-1.json").exists()


def test_pr_status_snapshot_fn_prefers_explicit_expected_base_sha():
    calls: list[dict] = []

    def ref_sha_fn(ref: str) -> str:
        raise AssertionError("explicit expected_base_sha should skip ref lookup")

    def snapshot_builder(**kwargs) -> dict:
        calls.append(kwargs)
        return _green_snapshot(kwargs["pr_number"])

    snapshot_fn = build_pr_status_snapshot_fn(
        repo="Ahkeratmehilaiset/waggledance-swarm",
        expected_base_sha=BASE,
        ref_sha_fn=ref_sha_fn,
        snapshot_builder=snapshot_builder,
    )

    assert snapshot_fn(901)["pr_number"] == 901
    assert calls == [
        {
            "pr_number": 901,
            "repo": "Ahkeratmehilaiset/waggledance-swarm",
            "expected_base_sha": BASE,
        }
    ]


# --- my_unmerged_rco_passes -------------------------------------------------


def test_unmerged_rco_pass_detected():
    events = [
        _rco_request("t1", ts="2026-05-22T13:00:00Z"),
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
    ]
    out = my_unmerged_rco_passes(events, agent="claude", now_utc=NOW)
    assert len(out) == 1
    assert out[0]["pr"] == 900 and out[0]["approved_head"] == HEAD


def test_merged_rco_pass_excluded():
    events = [
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        _done_merged("t1", pr=900, ts="2026-05-22T13:40:00Z"),
    ]
    assert my_unmerged_rco_passes(events, agent="claude", now_utc=NOW) == []


def test_stale_rco_pass_excluded_by_age():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-18T10:00:00Z")]
    # >48h before NOW
    assert my_unmerged_rco_passes(events, agent="claude", now_utc=NOW) == []


def test_merged_by_pr_number_excluded_despite_task_id_mismatch():
    # rco_pass under one task id; done/merged for the SAME pr under a slightly
    # different task id (20260522 vs 2026-05-22). The PR must clear anyway.
    events = [
        _rco_pass("t-20260522", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        _done_merged("t-2026-05-22", pr=900, ts="2026-05-22T13:40:00Z"),
    ]
    assert my_unmerged_rco_passes(events, agent="claude", now_utc=NOW) == []


def test_pass_without_pr_skipped():
    ev = _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")
    ev["payload"] = {"head": HEAD}  # no pr
    assert my_unmerged_rco_passes([ev], agent="claude", now_utc=NOW) == []


# --- evaluate_merge_ready ---------------------------------------------------


def _candidate(pr=900, head=HEAD, task="t1"):
    return {"task_id": task, "pr": pr, "approved_head": head}


def test_merge_ready_when_green_clean_headmatch():
    events = [
        _rco_request("t1", ts="2026-05-22T13:00:00Z"),
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        *_three_identity_consensus("t1", pr=900, head=HEAD),
    ]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is True
    assert r["merge_command"] == MERGE_DRIVER_COMMAND
    assert r["merge_driver_command"] == MERGE_DRIVER_COMMAND
    assert not r["merge_command"].startswith("gh pr merge")
    assert r["merge_target"] == {
        "pr": 900,
        "head_sha": HEAD,
        "canonical_task_id": "t1",
    }


def test_merge_ready_with_short_approved_head_prefix():
    # Real bridge rco_pass payloads carry a short head; gh returns the full sha.
    short = "862d34bd"
    full = "862d34bd27c15b870242faf333f7961629137cb8"
    events = [
        _rco_pass("t1", pr=566, head=short, ts="2026-05-22T13:30:00Z"),
        *_three_identity_consensus("t1", pr=566, head=full),
    ]
    r = evaluate_merge_ready(
        {"task_id": "t1", "pr": 566, "approved_head": short},
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr, head=full),
    )
    assert r["ready"] is True
    assert r["merge_command"] == MERGE_DRIVER_COMMAND
    # The driver rechecks and pins the full snapshot sha from GitHub.
    assert r["merge_target"]["head_sha"] == full


def test_not_ready_without_full_bridge_consensus():
    events = [
        _claim("fable-5", "t1"),
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        _rco_gate_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:31:00Z"),
    ]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is False
    assert "bridge_consensus_incomplete" in r["blockers"]
    reasons = r["bridge_consensus_gate"]["reasons"]
    assert any("build_lead" in reason for reason in reasons)
    assert any("build_tools" in reason for reason in reasons)


def test_not_ready_when_head_moved():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        _candidate(head=HEAD),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr, head=OTHER_HEAD),
    )
    assert r["ready"] is False
    assert "head_moved_since_rco_pass" in r["blockers"]


def test_not_ready_when_checks_red():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    snap = _green_snapshot(900)
    snap["checks"] = [
        {"name": "test", "state": "", "status": "COMPLETED", "conclusion": "FAILURE"}
    ]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: snap,
    )
    assert r["ready"] is False and "checks_not_green" in r["blockers"]


def test_not_ready_when_mergeable_dirty():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    snap = _green_snapshot(900)
    snap["mergeable"] = "CONFLICTING"
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: snap,
    )
    assert r["ready"] is False
    assert any(b.startswith("mergeable_not_clean") for b in r["blockers"])


def test_not_ready_when_peer_block_standing():
    # Codex (peer) posted a later changes_requested -> preflight not clear.
    events = [
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        {
            "ts_utc": "2026-05-22T13:45:00Z",
            "agent": "codex",
            "type": "decision",
            "task_id": "t1",
            "status": "changes_requested",
            "message": "blocker",
        },
    ]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is False
    assert "peer_block_or_changes_requested" in r["blockers"]


def test_not_ready_when_peer_block_mentions_same_pr_under_different_task_id():
    events = [
        _rco_pass(
            "fix-bridge-next-action-stale-ack-requester-close-2026-05-27",
            pr=701,
            head=HEAD,
            ts="2026-05-27T07:30:00Z",
        ),
        {
            "ts_utc": "2026-05-27T07:31:39Z",
            "agent": "codex-lead-1",
            "agent_uuid": AGENT_UUIDS["codex-lead-1"],
            "type": "finding",
            "task_id": "pr701-bridge-stale-ack-close-readonly-review-2026-05-27",
            "status": "confirmed_bug_blocks_merge",
            "message": "Lead BLOCK PR #701 exact head abc123.",
        },
    ]
    r = evaluate_merge_ready(
        _candidate(
            pr=701,
            task="fix-bridge-next-action-stale-ack-requester-close-2026-05-27",
        ),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is False
    assert "peer_block_or_changes_requested" in r["blockers"]


def test_no_snapshot_fn_is_unchecked_not_ready():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=None,
    )
    assert r["ready"] is False and "pr_status_unchecked" in r["blockers"]


def test_not_ready_without_exact_head_rco_pass():
    events = [
        _claim("fable-5", "t1"),
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
    ]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is False
    assert "rco_pass_missing_or_stale" in r["blockers"]
    assert r["rco_pass_gate"]["decision"] == "no_rco_events_for_task"


def test_merge_ready_resolves_author_from_claim_and_rejects_rco_self_pass():
    task = "wd/rco/rule9a-backup-rco"
    events = [
        _claim("claude-rco-1", task),
        _build_consensus(
            task,
            head=HEAD,
            agent="codex-lead-1",
            ts="2026-05-22T13:29:00Z",
        ),
        _build_consensus(
            task,
            head=HEAD,
            agent="codex-tools-1",
            ts="2026-05-22T13:30:00Z",
        ),
        _rco_gate_pass(task, pr=900, head=HEAD, ts="2026-05-22T13:31:00Z"),
    ]
    snapshot = _green_snapshot(900, task=task)
    snapshot.pop("author_agent")
    snapshot["author"] = {"login": "Ahkeratmehilaiset"}

    r = evaluate_merge_ready(
        _candidate(task=task),
        events=events,
        agent="codex-tools-1",
        snapshot_fn=lambda pr: snapshot,
    )

    assert r["ready"] is False
    assert r["author_agent"] == "claude-rco-1"
    assert r["rco_pass_gate"]["eligible_rco_agents"] == ["claude-rco-2"]
    assert "rco_pass_missing_or_stale" in r["blockers"]


def test_merge_ready_fails_closed_when_author_unresolvable():
    task = "wd/rco/rule9a-backup-rco"
    events = [
        *_three_identity_consensus(task, pr=900, head=HEAD),
    ]
    snapshot = _green_snapshot(900)
    snapshot.pop("author_agent")
    snapshot["author_login"] = "Ahkeratmehilaiset"

    r = evaluate_merge_ready(
        _candidate(task=task),
        events=events,
        agent="codex-tools-1",
        snapshot_fn=lambda pr: snapshot,
    )

    assert r["ready"] is False
    assert r["author_agent"] == ""
    assert r["rco_pass_gate"]["decision"] == "invalid_author_agent"
    assert "rco_pass_missing_or_stale" in r["blockers"]


def test_snapshot_error_decision_is_reported_as_blocker():
    class SnapshotError(Exception):
        report = {
            "decision": "stale_base_ref",
            "ok": False,
            "errors": ["base moved"],
        }

    def snapshot_fn(pr: int) -> dict:
        raise SnapshotError()

    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        _candidate(),
        events=events,
        agent="claude",
        snapshot_fn=snapshot_fn,
    )
    assert r["ready"] is False
    assert "pr_status_error:stale_base_ref" in r["blockers"]
    assert r["pr_status_error"]["decision"] == "stale_base_ref"


# --- build_loop_tick + adaptive wakeup -------------------------------------


def test_loop_tick_merge_ready_short_wakeup(tmp_path):
    events = [
        _rco_request("t1", ts="2026-05-22T13:00:00Z"),
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        *_three_identity_consensus("t1", pr=900, head=HEAD),
    ]
    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert report["ok"] is True
    ready = [m for m in report["merge_ready"] if m["ready"]]
    assert len(ready) == 1 and ready[0]["pr"] == 900
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW


def test_loop_tick_caps_pr_snapshot_checks_to_recent_candidates(tmp_path):
    events = [
        _rco_pass("old", pr=100, head=HEAD, ts="2026-05-22T13:00:00Z"),
        _rco_pass("new", pr=102, head=HEAD, ts="2026-05-22T13:50:00Z"),
        _rco_pass("mid", pr=101, head=HEAD, ts="2026-05-22T13:30:00Z"),
    ]
    calls: list[int] = []

    def snapshot_fn(pr: int) -> dict:
        calls.append(pr)
        return _green_snapshot(pr)

    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=snapshot_fn,
        max_merge_ready_checks=2,
    )

    assert calls == [102, 101]
    assert report["merge_ready_checked_count"] == 2
    assert report["merge_ready_deferred_count"] == 1
    assert report["merge_ready_deferred"] == [
        {
            "task_id": "old",
            "pr": 100,
            "approved_head": HEAD,
            "passed_at_utc": "2026-05-22T13:00:00Z",
        }
    ]


def test_loop_tick_genuinely_quiet_uses_long_wakeup(tmp_path):
    report = build_loop_tick(
        agent="claude",
        events=[],
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )
    assert report["next_action"] == "claim_unblocked_work"
    assert report["merge_ready"] == []
    assert report["recommended_wakeup_seconds"] == WAKEUP_QUIET
    assert report["wakeup_reason"] == "quiet; no pending bridge work"


def test_loop_tick_open_operator_pack_does_not_park_unblocked_work(tmp_path):
    (tmp_path / "pack.yaml").write_text(
        """\
schema_version: waggledance.operator_decision_pack.v1
decision_id: torch-cuda-vs-cpu
category: dependency_security
created_utc: 2026-05-22T14:00:00Z
author_agent: claude
options:
  - id: A1_cpu_only
  - id: A2_cu126
operator_signoff:
  signed_by: ""
  chosen_option: ""
""",
        encoding="utf-8",
    )

    report = build_loop_tick(
        agent="claude",
        events=[],
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert [p["decision_id"] for p in report["open_operator_packs"]] == [
        "torch-cuda-vs-cpu"
    ]
    assert report["next_action"] == "claim_unblocked_work"
    assert report["recommended_wakeup_seconds"] == WAKEUP_IN_FLIGHT
    assert report["wakeup_reason"] == "operator pack open; check unblocked work soon"


def test_loop_tick_open_peer_rco_is_answer_incoming(tmp_path):
    events = [_rco_request("t2", ts="2026-05-22T13:50:00Z")]
    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )
    assert report["next_action"] == "answer_incoming"
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW


# --- peer activation --------------------------------------------------------


def test_peer_activation_needed_for_heartbeat_only_peer():
    events = [
        _finding("claude", "2026-05-22T13:00:00Z"),
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is True
    assert rec["peer"] == "claude"
    assert rec["reason"] == "peer_heartbeat_only_without_recent_substantive_work"
    assert rec["bridge_event"]["to"] == "claude"
    assert rec["bridge_event"]["status"] == "scout_requested"
    assert "highest-value PR candidate" in rec["bridge_event"]["message"]


def test_peer_activation_uses_operator_pack_safe_scout_when_packs_open():
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    packs = [{"decision_id": "torch-cuda-vs-cpu", "category": "dependency_security"}]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=packs,
        now_utc=NOW,
    )

    assert rec["needed"] is True
    assert "torch-cuda-vs-cpu" in rec["bridge_event"]["message"]
    assert "must stay fail-closed" in rec["bridge_event"]["message"]


def test_peer_activation_not_needed_when_peer_has_recent_substantive_work():
    events = [
        _finding("claude", "2026-05-22T13:45:00Z"),
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is False
    assert rec["reason"] == "peer_recently_substantive"


def test_peer_activation_not_needed_when_peer_has_claim():
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    claims = [{"agent": "claude", "task_id": "active"}]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=claims,
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is False
    assert rec["reason"] == "peer_has_active_claim"


def test_peer_activation_not_duplicated_when_recent_handoff_exists():
    events = [
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
        _peer_activation_sent("codex", "claude", "2026-05-22T13:59:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is False
    assert rec["reason"] == "peer_activation_recently_sent"
    assert rec["last_activation_sent_at_utc"] == "2026-05-22T13:59:00Z"


def test_emit_peer_activation_writes_valid_bridge_event(tmp_path):
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    events_path = emit_peer_activation_event(
        bridge_root=tmp_path,
        agent="codex",
        event_spec=rec["bridge_event"],
        now_utc=NOW,
        writer_backend=_PortableTestBackend(),
    )

    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["agent"] == "codex"
    assert event["to"] == "claude"
    assert event["type"] == "handoff"
    assert event["status"] == "scout_requested"
    assert event["payload"]["source"] == "bridge_loop_tick.peer_activation"
    assert (tmp_path / "outbox" / "codex" / "2026-05-22.jsonl").exists()
    assert (
        json.loads((tmp_path / "shared" / "last_codex.json").read_text())["task_id"]
        == event["task_id"]
    )


def test_emit_peer_activation_refuses_custom_events_target(tmp_path: Path) -> None:
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    with pytest.raises(BridgeEventWriteError, match="non-canonical"):
        emit_peer_activation_event(
            bridge_root=tmp_path / "bridge",
            events_path=tmp_path / "custom-events.jsonl",
            agent="codex",
            event_spec=rec["bridge_event"],
            now_utc=NOW,
            writer_backend=_PortableTestBackend(),
        )

    assert not (tmp_path / "bridge").exists()


def test_peer_activation_event_is_handoff_only_without_authority():
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    event = materialize_peer_activation_event(
        agent="codex",
        event_spec=rec["bridge_event"],
        now_utc=NOW,
    )

    assert event["type"] == "handoff"
    assert event["status"] == "scout_requested"
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["run_id"] == ""
    assert event["payload"] == {
        "summary": rec["bridge_event"]["summary"],
        "source": "bridge_loop_tick.peer_activation",
    }
    assert "operator_gate_required" not in event
    assert "auto_execute" not in event
    assert "claim" not in event
    assert "merge" not in event


def test_loop_tick_short_wakeup_when_peer_activation_needed(tmp_path):
    events = [
        _finding("claude", "2026-05-22T13:00:00Z"),
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
    ]
    report = build_loop_tick(
        agent="codex",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_activation"]["needed"] is True
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW
    assert report["wakeup_reason"] == "peer activation needed"


@pytest.mark.parametrize(
    ("agent", "peer"),
    [
        ("codex-tools-1", "codex-lead-1"),
        ("codex-lead-1", "codex-tools-1"),
    ],
)
def test_peer_activation_supports_current_production_agent_ids(agent, peer):
    events = [
        _finding(peer, "2026-05-22T13:00:00Z"),
        _heartbeat(peer, "2026-05-22T13:55:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent=agent,
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is True
    assert rec["peer"] == peer
    assert rec["reason"] == "peer_heartbeat_only_without_recent_substantive_work"
    assert rec["bridge_event"]["to"] == peer
    assert rec["bridge_event"]["task_id"].startswith(f"peer-activation-{peer}-")


def test_loop_tick_short_wakeup_for_current_production_peer_activation(tmp_path):
    events = [
        _finding("codex-lead-1", "2026-05-22T13:00:00Z"),
        _heartbeat("codex-lead-1", "2026-05-22T13:55:00Z"),
    ]

    report = build_loop_tick(
        agent="codex-tools-1",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_activation"]["needed"] is True
    assert report["peer_activation"]["peer"] == "codex-lead-1"
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW
    assert report["wakeup_reason"] == "peer activation needed"


# --- peer active PR-producing claim ----------------------------------------


def _claim_active(agent: str, task: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "claim",
        "task_id": task,
        "status": "active",
        "message": f"{agent} working on {task}",
    }


def test_peer_active_claim_detected_when_recent_and_unclosed():
    events = [_claim_active("codex", "magma-slice-1", ts="2026-05-22T13:55:00Z")]

    result = peer_has_active_pr_producing_claim(events, agent="claude", now_utc=NOW)

    assert result["active"] is True
    assert result["peer"] == "codex"
    assert result["task_id"] == "magma-slice-1"
    assert result["event_type"] == "claim"
    assert result["event_status"] == "active"
    assert result["reason"] == "peer_has_active_pr_producing_claim"


@pytest.mark.parametrize(
    ("agent", "peer"),
    [
        ("codex-tools-1", "codex-lead-1"),
        ("codex-lead-1", "codex-tools-1"),
    ],
)
def test_peer_active_claim_detects_current_production_agent_ids(agent, peer):
    events = [_claim_active(peer, "current-agent-slice", ts="2026-05-22T13:55:00Z")]

    result = peer_has_active_pr_producing_claim(events, agent=agent, now_utc=NOW)

    assert result["active"] is True
    assert result["peer"] == peer
    assert result["task_id"] == "current-agent-slice"
    assert result["reason"] == "peer_has_active_pr_producing_claim"


def test_peer_active_claim_cleared_by_done_event():
    events = [
        _claim_active("codex", "magma-slice-1", ts="2026-05-22T13:55:00Z"),
        {
            "ts_utc": "2026-05-22T13:58:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": "magma-slice-1",
            "status": "merged",
            "payload": {"pr": 700},
        },
    ]

    result = peer_has_active_pr_producing_claim(events, agent="claude", now_utc=NOW)

    assert result["active"] is False
    assert result["reason"] == "peer_claim_closed_by_done"


def test_peer_active_claim_survives_intervening_non_terminal_event():
    """Per Codex RCO on PR #592: a later decision/clarification/finding from
    the same peer MUST NOT clear an active PR-producing claim. Only a
    terminal close (``type=done`` or status in PEER_TERMINAL_STATUSES) on
    the same task_id clears it."""
    events = [
        _claim_active("codex", "magma-slice-1", ts="2026-05-22T13:55:00Z"),
        {
            "ts_utc": "2026-05-22T13:57:00Z",
            "agent": "codex",
            "type": "decision",
            "task_id": "magma-slice-1",
            "status": "clarification",
            "message": "interim note, claim still open",
        },
        # An unrelated finding on a different task_id from the same peer
        # must also not clear the claim.
        _finding("codex", "2026-05-22T13:58:00Z"),
    ]

    result = peer_has_active_pr_producing_claim(events, agent="claude", now_utc=NOW)

    assert result["active"] is True
    assert result["task_id"] == "magma-slice-1"
    assert result["reason"] == "peer_has_active_pr_producing_claim"


def test_peer_active_claim_too_old_does_not_anticipate():
    events = [_claim_active("codex", "magma-slice-1", ts="2026-05-22T13:20:00Z")]

    result = peer_has_active_pr_producing_claim(events, agent="claude", now_utc=NOW)

    assert result["active"] is False
    assert result["reason"] == "peer_claim_event_too_old"


def test_loop_tick_in_flight_when_peer_has_active_pr_producing_claim(tmp_path):
    events = [_claim_active("codex", "magma-slice-2", ts="2026-05-22T13:55:00Z")]

    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_active_claim"]["active"] is True
    assert report["recommended_wakeup_seconds"] == WAKEUP_IN_FLIGHT
    assert report["wakeup_reason"] == "peer has active PR-producing claim; anticipate"


def test_loop_tick_quiet_when_peer_claim_already_done(tmp_path):
    events = [
        _claim_active("codex", "magma-slice-2", ts="2026-05-22T13:55:00Z"),
        {
            "ts_utc": "2026-05-22T13:58:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": "magma-slice-2",
            "status": "merged",
            "payload": {"pr": 701},
        },
    ]

    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_active_claim"]["active"] is False
    assert report["recommended_wakeup_seconds"] == WAKEUP_QUIET
