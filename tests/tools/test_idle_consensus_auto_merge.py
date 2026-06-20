from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.idle_consensus_auto_merge import (
    AutoMergeGateError,
    evaluate_auto_merge_gate,
    main,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_BASE = "fedcba9876543210fedcba9876543210fedcba98"
MERGE_SHA = "abcdef1234567890abcdef1234567890abcdef12"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _status(**overrides) -> dict:
    status = {
        "pr_number": 477,
        "head_sha": HEAD,
        "base_sha": BASE,
        "title": "Idle consensus follow-up",
        "mergeable": "clean",
        "author_agent": "claude-rco-2",
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {"name": "test (3.13)", "state": "success"},
            {"name": "unified", "state": "success"},
        ],
    }
    status.update(overrides)
    return status


def _events_path(tmp_path: Path, events: list[dict] | None = None) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in (events or [])),
        encoding="utf-8",
    )
    return path


def _bridge_event(
    *,
    agent: str,
    type_: str,
    status: str,
    task_id: str = "idle-consensus-001",
    ts: str = "2026-05-18T01:00:00Z",
) -> dict:
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": "",
        "payload": {},
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _claim(agent: str, task_id: str, *, ts: str = "2026-05-18T00:59:00Z") -> dict:
    return _bridge_event(
        agent=agent, type_="claim", status="active", task_id=task_id, ts=ts
    )


def _rco_pass(
    task_id: str = "idle-consensus-001",
    *,
    pr: int = 477,
    ts: str = "2026-05-18T01:00:00Z",
) -> dict:
    return _bridge_event(
        agent="claude-rco-1",
        type_="decision",
        status="rco_pass",
        task_id=task_id,
        ts=ts,
    ) | {"message": f"RCO_PASS exact head {HEAD}", "payload": {"pr": pr, "head": HEAD}}


def _auto_merge_event(index: int, *, ts: str = "2026-05-18T01:00:00Z") -> dict:
    return {
        "ts_utc": ts,
        "agent": "codex",
        "type": "done",
        "status": "idle_auto_merge_done",
        "task_id": f"idle-auto-merge-{index}",
        "payload": {
            "auto_merged": True,
            "pr_number": 500 + index,
            "consensus_proposal_id": f"idle-consensus-{index}",
        },
    }


def test_dry_run_ready_never_invokes_runner() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="merge-sha")

    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        repo="Ahkeratmehilaiset/waggledance-swarm",
        runner=runner,
    )
    assert calls == []
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["dry_run"] is True
    assert report["external_effect"] is False
    assert report["would_merge"] is True
    assert "gh" in report["gh_command"]
    assert f"--match-head-commit={HEAD}" in report["gh_command"]
    assert report["receipt_gate"]["verified"] is True
    assert report["path_gate"]["allowed"] is True
    assert report["diff_gate"]["allowed"] is True


def test_apply_invokes_exact_head_merge_command(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="abcdef\n")

    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=runner,
    )
    assert len(calls) == 1
    assert calls[0][:4] == ["gh", "pr", "merge", "477"]
    assert f"--match-head-commit={HEAD}" in calls[0]
    assert report["decision"] == "auto_merged"
    assert report["external_effect"] is True
    assert report["auto_merge_event_payload"]["auto_merged"] is True
    assert report["auto_merge_event_payload"]["merge_commit_sha"] == "abcdef"


def test_apply_accepts_utf8_bom_events_file(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    events_path = _events_path(tmp_path, [_rco_pass()])
    events_path.write_bytes(b"\xef\xbb\xbf" + events_path.read_bytes())

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="abcdef\n")

    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=events_path,
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=runner,
    )

    assert len(calls) == 1
    assert report["decision"] == "auto_merged"
    assert report["auto_merge_event_payload"]["merge_commit_sha"] == "abcdef"


def test_apply_runs_artifact_hook_before_exact_head_merge(tmp_path: Path) -> None:
    calls: list[str] = []

    def artifact_writer() -> dict:
        calls.append("artifact")
        return {
            "receipt_bundle": {
                "manifest": "docs/receipts/manifest.json",
                "verifier_report": {"ok": True, "receipt_count": 1, "errors": []},
            }
        }

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append("merge")
        return SimpleNamespace(returncode=0, stdout="abcdef\n")

    report = evaluate_auto_merge_gate(
        pr_status=_status(receipt_verified=False),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=runner,
        artifact_writer=artifact_writer,
    )
    assert calls == ["artifact", "merge"]
    assert report["decision"] == "auto_merged"
    assert report["auto_merge_event_payload"]["receipt_bundle_path"] == (
        "docs/receipts/manifest.json"
    )


def test_head_mismatch_blocks_without_runner() -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(head_sha="abcdefabcdefabcdefabcdefabcdefabcdefabcd"),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "exact head mismatch" in report["reasons"]
    assert report["external_effect"] is False


def test_apply_requires_expected_base_sha_before_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "expected_base_sha is required before merge" in report["reasons"]
    assert report["base_gate"]["required"] is True
    assert report["base_gate"]["configured"] is False


def test_expected_base_mismatch_blocks_without_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=OTHER_BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "base sha mismatch" in report["reasons"]
    assert report["base_gate"]["snapshot_base_sha"] == BASE
    assert report["base_gate"]["expected_base_sha"] == OTHER_BASE


def test_expected_base_requires_snapshot_base_sha(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    status = _status()
    status.pop("base_sha")
    report = evaluate_auto_merge_gate(
        pr_status=status,
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "base_sha snapshot is required before merge" in report["reasons"]


def test_invalid_expected_base_sha_refused() -> None:
    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha="abc123",
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
        )
    assert excinfo.value.report["decision"] == "invalid_sha"
    assert (
        "expected_base_sha must be a 40-char lowercase sha"
        in excinfo.value.report["errors"]
    )


def test_denylisted_changed_path_blocks_without_runner() -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(changed_paths=["CLAUDE.md"]),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "path gate failed: denylist hit" in report["reasons"]
    assert report["path_gate"]["blocked_paths"] == ["CLAUDE.md"]
    assert report["would_merge"] is False


def test_code_pattern_denylist_blocks_without_runner() -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            changed_paths=["tools/idle_protocol_activate.py"],
            diff_text="+ operator_gate_required=True\n",
        ),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "diff gate failed: code pattern denylist hit" in report["reasons"]
    assert report["diff_gate"]["code_pattern_hits"]
    assert report["would_merge"] is False


def test_missing_changed_paths_snapshot_fails_closed() -> None:
    status = _status()
    status.pop("changed_paths")
    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=status,
            expected_head=HEAD,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
        )
    assert excinfo.value.report["decision"] == "invalid_pr_status"
    assert "changed_paths must be a list" in excinfo.value.report["errors"]


def test_missing_diff_text_snapshot_fails_closed() -> None:
    status = _status()
    status.pop("diff_text")
    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=status,
            expected_head=HEAD,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
        )
    assert excinfo.value.report["decision"] == "invalid_pr_status"
    assert "diff_text must be a string" in excinfo.value.report["errors"]


def test_daily_rate_limit_blocks_without_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            [_auto_merge_event(index) for index in range(1, 6)],
        ),
        utc_date="2026-05-18",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "rate_limited"
    assert report["rate_gate"] == {
        "allowed": False,
        "utc_date": "2026-05-18",
        "quota_used": 5,
        "quota_total": 5,
    }
    assert "daily rate limit exceeded: 5/5 for 2026-05-18" in report["reasons"]


def test_pending_check_blocks_merge() -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(checks=[{"name": "unified", "state": "pending"}]),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "operator_review_required"
    assert report["would_merge"] is False
    assert "status checks not green: unified" in report["reasons"]


def test_bridge_peer_block_blocks_automerge_without_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            [
                _bridge_event(
                    agent="claude",
                    type_="handoff",
                    status="rco_requested",
                    task_id="rule9-task",
                ),
                _bridge_event(
                    agent="codex",
                    type_="decision",
                    status="changes_requested",
                    task_id="rule9-task",
                    ts="2026-05-18T01:05:00Z",
                ),
            ],
        ),
        from_agent="claude",
        bridge_task_id="rule9-task",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert report["bridge_peer_gate"]["clear_to_merge"] is False
    assert "unresolved peer bridge block: agent=codex status=changes_requested" in (
        report["reasons"]
    )


def test_exact_head_rco_pass_required_when_bridge_events_checked(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["has_qualifying_rco_pass_at_head"] is False
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


def test_rco_gate_not_checked_defaults_fail_closed_report() -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["rco_pass_gate"]["ok"] is False
    assert report["rco_pass_gate"]["decision"] == (
        "not_checked_operator_review_required"
    )


def test_operator_merge_required_rco_status_does_not_satisfy_merge_gate(
    tmp_path: Path,
) -> None:
    rco = _bridge_event(
        agent="claude-rco-1",
        type_="decision",
        status="rco_pass_operator_merge_required",
    ) | {
        "message": f"RCO_PASS_OPERATOR_MERGE_REQUIRED exact head {HEAD}",
        "payload": {"head": HEAD},
    }
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [rco]),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=lambda command: pytest.fail(f"merge runner called: {command}"),
    )
    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["ok"] is False
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


def test_pending_ci_rco_status_with_non_green_ci_refuses_merge_gate(
    tmp_path: Path,
) -> None:
    rco = _bridge_event(
        agent="claude-rco-1",
        type_="decision",
        status="rco_pass_pending_ci",
    ) | {
        "message": f"RCO_PASS_PENDING_CI exact head {HEAD}",
        "payload": {"head": HEAD},
    }
    report = evaluate_auto_merge_gate(
        pr_status=_status(checks=[{"name": "unified", "state": "pending"}]),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [rco]),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=lambda command: pytest.fail(f"merge runner called: {command}"),
    )
    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["ok"] is False
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )
    assert "status checks not green: unified" in report["reasons"]


def test_consensus_rejects_operator_merge_required_rco_status(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(agent="codex-lead-1", type_="decision", status="approved")
        | {"payload": {"head": HEAD}},
        _bridge_event(agent="codex-tools-1", type_="decision", status="approved")
        | {"payload": {"head": HEAD}},
        _bridge_event(
            agent="claude-rco-1",
            type_="decision",
            status="rco_pass_operator_merge_required",
        )
        | {
            "message": f"operator must merge exact head {HEAD}",
            "payload": {"head": HEAD},
        },
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "operator_review_required"
    assert report["bridge_consensus"]["ok"] is False
    assert any(
        "rco (recognized non-author RCO): no head-bound approval" in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_bridge_consensus_accepts_exact_head_payload_alias(tmp_path: Path) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-1",
            type_="decision",
            status="rco_pass",
            ts="2026-06-07T17:39:47Z",
        )
        | {"payload": {"pr": 477, "exact_head": HEAD}},
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_consensus"]["ok"] is True
    assert report["bridge_consensus"]["rco_pass_ref"]["agent"] == "claude-rco-1"


@pytest.mark.parametrize(
    ("author_agent", "role"),
    [
        ("codex-lead-1", "build_lead"),
        ("codex-tools-1", "build_tools"),
    ],
)
def test_bridge_consensus_rejects_build_author_self_review(
    tmp_path: Path,
    author_agent: str,
    role: str,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-1",
            type_="decision",
            status="rco_pass",
            ts="2026-06-07T17:39:47Z",
        )
        | {"payload": {"pr": 477, "exact_head": HEAD}},
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(author_agent=author_agent),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    identity = report["bridge_consensus"]["identities"][role]
    assert report["decision"] == "operator_review_required"
    assert report["bridge_consensus"]["ok"] is False
    assert identity["eligible"] is False
    assert identity["approved"] is False
    assert identity["self_approval_ignored"] is True
    assert any(
        "author_agent cannot satisfy its own reviewer slot" in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_bridge_consensus_accepts_exact_head_alias_with_stale_legacy_head_key(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"payload": {"head": OTHER_BASE, "exact_head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"payload": {"head": OTHER_BASE, "exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-1",
            type_="decision",
            status="rco_pass",
            ts="2026-06-07T17:39:47Z",
        )
        | {"payload": {"pr": 477, "head": OTHER_BASE, "exact_head": HEAD}},
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_consensus"]["ok"] is True


def test_bridge_consensus_rejects_stale_exact_head_payload_alias(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"payload": {"exact_head": OTHER_BASE}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-1",
            type_="decision",
            status="rco_pass",
            ts="2026-06-07T17:39:47Z",
        )
        | {"payload": {"pr": 477, "exact_head": HEAD}},
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "operator_review_required"
    assert report["bridge_consensus"]["ok"] is False
    assert any(
        f"build_lead (codex-lead-1): no head-bound approval at {HEAD}" in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_bridge_consensus_ignores_non_string_exact_head_payload_alias(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"payload": {"exact_head": {"sha": HEAD}}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-1",
            type_="decision",
            status="rco_pass",
            ts="2026-06-07T17:39:47Z",
        )
        | {"payload": {"pr": 477, "exact_head": HEAD}},
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "operator_review_required"
    assert report["bridge_consensus"]["ok"] is False
    assert any(
        f"build_lead (codex-lead-1): no head-bound approval at {HEAD}" in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_bridge_consensus_allows_clear_preflight_status_with_block_context(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(),
        _bridge_event(
            agent="codex-tools-1",
            type_="test",
            status="peer_block_preflight_clear_after_tools_build_consensus",
            ts="2026-06-07T17:39:47Z",
        ),
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_consensus"]["ok"] is True


def test_bridge_consensus_rejects_build_pass_with_noncanonical_task_id(
    tmp_path: Path,
) -> None:
    canonical_task = "codex-lead-1/v12-solver-growth-coverage-summary-20260608"
    wrong_task = "codex-lead-1-v12-solver-growth-coverage-summary-20260608"
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            task_id=wrong_task,
            ts="2026-06-08T02:18:40Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            task_id=canonical_task,
            ts="2026-06-08T02:22:11Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(task_id=canonical_task),
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=canonical_task,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id=canonical_task,
        require_bridge_consensus=True,
    )

    assert report["decision"] == "operator_review_required"
    assert report["bridge_consensus"]["ok"] is False
    assert (
        report["bridge_consensus"]["identities"]["build_lead"]["task_id_mismatch"]
        == wrong_task
    )
    assert any(
        f"non-canonical task_id {wrong_task!r}; expected {canonical_task!r}"
        in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_bridge_consensus_allows_no_changes_requested_status(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(),
        _bridge_event(
            agent="codex-tools-1",
            type_="test",
            status="no_changes_requested",
            ts="2026-06-07T17:39:47Z",
        ),
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_consensus"]["ok"] is True


def test_bridge_consensus_allows_no_changes_requested_approved_status(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(),
        _bridge_event(
            agent="codex-tools-1",
            type_="test",
            status="no_changes_requested_approved",
            ts="2026-06-07T17:39:47Z",
        ),
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_consensus"]["ok"] is True


def test_bridge_consensus_allows_changes_requested_resolved_status(
    tmp_path: Path,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(),
        _bridge_event(
            agent="codex-tools-1",
            type_="done",
            status="changes_requested_resolved_ci_pending",
            ts="2026-06-07T17:39:47Z",
        ),
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_consensus"]["ok"] is True


def test_bridge_consensus_no_changes_requested_text_does_not_clear_real_block(
    tmp_path: Path,
) -> None:
    for status in [
        "no_changes_requested_but_blocked",
        "no_changes_requested_rco_blocked",
        "no_changes_requested_block_requested",
        "no_changes_requested_changes_requested",
    ]:
        events = [
            _bridge_event(
                agent="codex-lead-1",
                type_="decision",
                status="build_consensus_pass",
                ts="2026-06-07T17:34:11Z",
            )
            | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
            _bridge_event(
                agent="codex-tools-1",
                type_="decision",
                status="build_consensus_pass",
                ts="2026-06-07T17:38:40Z",
            )
            | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
            _rco_pass(),
            _bridge_event(
                agent="codex-tools-1",
                type_="test",
                status=status,
                ts="2026-06-07T17:39:47Z",
            ),
        ]
        report = evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=_events_path(tmp_path, events),
            bridge_task_id="idle-consensus-001",
            require_bridge_consensus=True,
        )

        assert report["decision"] == "operator_review_required"
        assert report["bridge_peer_gate"]["clear_to_merge"] is False
        assert report["bridge_peer_gate"]["latest_blocking_event"]["status"] == status
        assert report["bridge_consensus"]["ok"] is False


def _lead_stall_failover_events(*, task_id: str = "idle-consensus-001") -> list[dict]:
    return [
        _bridge_event(
            agent="codex-lead-1",
            type_="message",
            status="vision_response",
            task_id=task_id,
            ts="2026-05-18T00:00:00Z",
        ),
        _bridge_event(
            agent="codex-lead-1",
            type_="heartbeat",
            status="alive",
            task_id=task_id,
            ts="2026-05-18T02:30:00Z",
        ),
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            task_id=task_id,
            ts="2026-05-18T03:00:00Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(task_id=task_id, ts="2026-05-18T03:01:00Z"),
    ]


def test_lead_stall_failover_is_default_off(tmp_path: Path) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _lead_stall_failover_events()),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "operator_review_required"
    assert report["bridge_consensus"]["ok"] is False
    assert report["bridge_consensus"]["lead_stall_failover"]["enabled"] is False
    assert any(
        "build_lead (codex-lead-1): no head-bound approval" in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_lead_stall_failover_engages_from_durable_idle_proof(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _lead_stall_failover_events()),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_consensus"]["ok"] is True
    assert report["bridge_consensus"]["identities"]["build_lead"]["approved"] is True
    assert (
        report["bridge_consensus"]["identities"]["build_lead"]["direct_approval"]
        is False
    )
    assert (
        report["bridge_consensus"]["identities"]["build_lead"]["failover_engaged"]
        is True
    )
    assert failover["engaged"] is True
    assert failover["clock_source"] == "durable_bridge_event_log_latest_ts"
    assert failover["lead_last_substantive_ts"] == "2026-05-18T00:00:00Z"
    assert failover["lead_idle_seconds"] >= 90 * 60
    assert failover["charter_clean"] is True
    assert failover["path_gate"]["allowed"] is True
    assert failover["diff_gate"]["allowed"] is True


def test_lead_stall_failover_refuses_tools_authored_pr(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(author_agent="codex-tools-1"),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _lead_stall_failover_events()),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert "tools-authored PRs cannot use tools-only lead failover" in (
        failover["reasons"]
    )
    assert any(
        "lead-stall failover refused: tools-authored PRs cannot use tools-only"
        in reason
        for reason in report["bridge_consensus"]["reasons"]
    )


def test_lead_stall_failover_refuses_gate_self_modification(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(changed_paths=["tools/idle_consensus_auto_merge.py"]),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _lead_stall_failover_events()),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["charter_clean"] is False
    assert failover["path_gate"]["reason"] == "denylist hit"
    assert "charter path gate failed: denylist hit" in failover["reasons"]


def test_lead_stall_failover_refuses_lead_veto(
    tmp_path: Path,
) -> None:
    events = _lead_stall_failover_events()
    events.append(
        _bridge_event(
            agent="codex-lead-1",
            type_="finding",
            status="changes_requested",
            ts="2026-05-18T00:30:00Z",
        )
        | {"message": f"lead veto exact head {HEAD}", "payload": {"head": HEAD}}
    )
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["lead_block_present"] is True
    assert "lead block/change request at this scope hard-blocks failover" in (
        failover["reasons"]
    )


def test_lead_stall_failover_refuses_recent_substantive_lead_event(
    tmp_path: Path,
) -> None:
    events = _lead_stall_failover_events()
    events[0]["ts_utc"] = "2026-05-18T02:50:00Z"
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["lead_idle_seconds"] < 90 * 60
    assert any(
        reason.startswith("lead idle duration below threshold:")
        for reason in failover["reasons"]
    )


def test_lead_stall_failover_refuses_missing_lead_uuid_idle_evidence(
    tmp_path: Path,
) -> None:
    events = _lead_stall_failover_events()
    events[0].pop("agent_uuid")
    events[1].pop("agent_uuid")
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert "no substantive lead event found in durable bridge log" in (
        failover["reasons"]
    )
    assert any(
        event["agent"] == "codex-lead-1"
        and event["identity_binding_status"] == "missing_uuid"
        for event in report["bridge_consensus"]["ignored_identity_mismatch_events"]
    )


def test_lead_stall_failover_refuses_stale_tools_head(
    tmp_path: Path,
) -> None:
    events = _lead_stall_failover_events()
    events[2]["payload"] = {"head": OTHER_BASE}
    events[2]["message"] = f"tools pass stale head {OTHER_BASE}"
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["tools_build_consensus_at_head"] is False
    assert "tools build_consensus at exact head is required" in failover["reasons"]


def test_lead_stall_failover_refuses_rco_veto(tmp_path: Path) -> None:
    events = _lead_stall_failover_events()
    events.append(
        _bridge_event(
            agent="claude-rco-1",
            type_="finding",
            status="changes_requested",
            ts="2026-05-18T03:02:00Z",
        )
        | {"message": f"RCO veto exact head {HEAD}", "payload": {"head": HEAD}}
    )
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["rco_pass_at_head"] is False
    assert failover["blocking_rco_agents"] == ["claude-rco-1"]
    assert (
        "recognized RCO veto blocks failover: claude-rco-1"
        in failover["reasons"]
    )


def test_lead_stall_failover_refuses_future_observation_timestamp(
    tmp_path: Path,
) -> None:
    """Anti-tamper clock clamp: a forged future timestamp in the durable
    log could otherwise manufacture lead idle time, so an observation
    newer than system clock + 5 minutes refuses the failover."""
    future = datetime.now(timezone.utc) + timedelta(minutes=10)

    def _ts(minutes_before: int) -> str:
        stamp = future - timedelta(minutes=minutes_before)
        return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")

    events = _lead_stall_failover_events()
    # Preserve the fixture's relative spacing (lead idle >= 90min) while
    # shifting the newest observation ~10min past the real clock.
    events[0]["ts_utc"] = _ts(200)
    events[1]["ts_utc"] = _ts(30)
    events[2]["ts_utc"] = _ts(1)
    events[3]["ts_utc"] = _ts(0)
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["lead_idle_seconds"] >= 90 * 60
    assert (
        "durable event observation timestamp is in the future"
        in failover["reasons"]
    )


def test_lead_stall_failover_refuses_denylisted_diff_content(
    tmp_path: Path,
) -> None:
    """The diff-content gate is load-bearing on its own: changed paths
    stay allowlist-clean, only the diff text carries a charter
    code-pattern-denylisted line."""
    denylisted_line = "+    retry_policy.gate_" + "skip=True\n"
    report = evaluate_auto_merge_gate(
        pr_status=_status(diff_text=denylisted_line),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _lead_stall_failover_events()),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert report["decision"] == "operator_review_required"
    assert failover["engaged"] is False
    assert failover["charter_clean"] is False
    assert failover["path_gate"]["allowed"] is True
    assert failover["diff_gate"]["allowed"] is False
    assert any(
        reason.startswith("charter diff gate failed:")
        for reason in failover["reasons"]
    )


def test_lead_stall_failover_refuses_when_lead_directly_approved(
    tmp_path: Path,
) -> None:
    """When the lead build slot is already directly satisfied, the
    failover must refuse instead of double-engaging (the slot is filled
    by the direct approval; the report must say so)."""
    events = _lead_stall_failover_events()
    events.append(
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-05-18T03:02:00Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}}
    )
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    failover = report["bridge_consensus"]["lead_stall_failover"]
    assert failover["engaged"] is False
    assert (
        "lead direct approval already satisfies the build slot"
        in failover["reasons"]
    )
    # The gate still passes -- via the direct approval, not the failover.
    assert report["bridge_consensus"]["ok"] is True
    assert report["bridge_consensus"]["identities"]["build_lead"]["approved"] is True
    assert (
        report["bridge_consensus"]["identities"]["build_lead"]["direct_approval"]
        is True
    )
    assert (
        report["bridge_consensus"]["identities"]["build_lead"]["failover_engaged"]
        is False
    )


def _failover_report(
    tmp_path: Path,
    *,
    status_overrides: dict | None = None,
    events_mutator=None,
) -> dict:
    """Run the gate on the positive-engage failover scenario, optionally
    flipping exactly one precondition via status overrides or an events
    mutation, and return the lead_stall_failover sub-report."""
    events = _lead_stall_failover_events()
    if events_mutator is not None:
        events_mutator(events)
    report = evaluate_auto_merge_gate(
        pr_status=_status(**(status_overrides or {})),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
        allow_lead_stall_failover=True,
    )
    return report["bridge_consensus"]["lead_stall_failover"]


def test_failover_mutation_guard_baseline_engages(tmp_path: Path) -> None:
    """Anchor for the single-flip matrix below: the unmodified scenario
    must engage, so each flip case isolates exactly one guard."""
    failover = _failover_report(tmp_path)
    assert failover["engaged"] is True
    assert failover["decision"] == "engaged"
    assert failover["reasons"] == []


def _flip_drop_tools_pass(events: list[dict]) -> None:
    del events[2]


def _flip_drop_rco_pass(events: list[dict]) -> None:
    del events[3]


def _flip_add_rco_veto(events: list[dict]) -> None:
    events.append(
        _bridge_event(
            agent="claude-rco-1",
            type_="finding",
            status="changes_requested",
            ts="2026-05-18T03:02:00Z",
        )
        | {"message": f"RCO veto exact head {HEAD}", "payload": {"head": HEAD}}
    )


def _flip_add_lead_block(events: list[dict]) -> None:
    events.append(
        _bridge_event(
            agent="codex-lead-1",
            type_="finding",
            status="changes_requested",
            ts="2026-05-18T00:30:00Z",
        )
        | {"message": f"lead veto exact head {HEAD}", "payload": {"head": HEAD}}
    )


def _flip_lead_recently_active(events: list[dict]) -> None:
    events[0]["ts_utc"] = "2026-05-18T02:50:00Z"


def _flip_future_observation(events: list[dict]) -> None:
    future = datetime.now(timezone.utc) + timedelta(minutes=10)

    def _ts(minutes_before: int) -> str:
        stamp = future - timedelta(minutes=minutes_before)
        return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")

    events[0]["ts_utc"] = _ts(200)
    events[1]["ts_utc"] = _ts(30)
    events[2]["ts_utc"] = _ts(1)
    events[3]["ts_utc"] = _ts(0)


@pytest.mark.parametrize(
    ("status_overrides", "events_mutator", "expected_reason"),
    [
        pytest.param(
            {"changed_paths": ["tools/idle_consensus_auto_merge.py"]},
            None,
            "charter path gate failed",
            id="flip-path-gate",
        ),
        pytest.param(
            # Assembled at runtime so this file's own diff stays clean.
            {"diff_text": "+    retry_policy.gate_" + "skip=True\n"},
            None,
            "charter diff gate failed",
            id="flip-diff-gate",
        ),
        pytest.param(
            {"author_agent": "codex-tools-1"},
            None,
            "tools-authored PRs cannot use tools-only lead failover",
            id="flip-tools-author",
        ),
        pytest.param(
            None,
            _flip_drop_tools_pass,
            "tools build_consensus at exact head is required",
            id="flip-tools-consensus-missing",
        ),
        pytest.param(
            None,
            _flip_drop_rco_pass,
            "recognized non-author RCO_PASS at exact head is required",
            id="flip-rco-pass-missing",
        ),
        pytest.param(
            None,
            _flip_add_rco_veto,
            "recognized RCO veto blocks failover",
            id="flip-rco-veto",
        ),
        pytest.param(
            None,
            _flip_add_lead_block,
            "lead block/change request at this scope hard-blocks failover",
            id="flip-lead-block",
        ),
        pytest.param(
            None,
            _flip_lead_recently_active,
            "lead idle duration below threshold",
            id="flip-lead-idle-below-threshold",
        ),
        pytest.param(
            None,
            _flip_future_observation,
            "durable event observation timestamp is in the future",
            id="flip-future-observation",
        ),
    ],
)
def test_failover_mutation_guard_single_flip_refuses(
    tmp_path: Path,
    status_overrides: dict | None,
    events_mutator,
    expected_reason: str,
) -> None:
    """Mutation-completeness: flipping exactly one engage precondition
    must refuse with the matching reason, so removing any single guard
    from _evaluate_lead_stall_failover makes at least one case fail."""
    failover = _failover_report(
        tmp_path,
        status_overrides=status_overrides,
        events_mutator=events_mutator,
    )
    assert failover["engaged"] is False
    assert failover["decision"] == "refused"
    assert any(
        expected_reason in reason for reason in failover["reasons"]
    ), f"expected {expected_reason!r} in {failover['reasons']!r}"


@pytest.mark.parametrize(
    "status",
    [
        "changes_requested_do_not_merge",
        "blocked_no_fix_yet",
        "block_without_fix",
        "rco_block_cleared",
    ],
)
def test_bridge_consensus_veto_statuses_with_negation_words_still_block(
    tmp_path: Path,
    status: str,
) -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
        )
        | {"message": f"lead pass exact head {HEAD}", "payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:38:40Z",
        )
        | {"message": f"tools pass exact head {HEAD}", "payload": {"head": HEAD}},
        _rco_pass(),
        _bridge_event(
            agent="codex-tools-1",
            type_="test",
            status=status,
            ts="2026-06-07T17:39:47Z",
        ),
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    assert report["decision"] == "operator_review_required"
    assert report["bridge_peer_gate"]["clear_to_merge"] is False
    assert report["bridge_consensus"]["ok"] is False


def test_bridge_peer_block_runs_before_artifact_writer(tmp_path: Path) -> None:
    calls: list[str] = []

    def artifact_writer() -> dict:
        calls.append("artifact")
        return {
            "receipt_bundle": {
                "manifest": "docs/receipts/manifest.json",
                "verifier_report": {"ok": True, "receipt_count": 1, "errors": []},
            }
        }

    report = evaluate_auto_merge_gate(
        pr_status=_status(receipt_verified=False),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        events_path=_events_path(
            tmp_path,
            [
                _bridge_event(
                    agent="codex",
                    type_="decision",
                    status="changes_requested",
                    task_id="rule9-task",
                ),
            ],
        ),
        from_agent="claude",
        bridge_task_id="rule9-task",
        apply=True,
        runner=lambda command: calls.append("merge"),
        artifact_writer=artifact_writer,
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert report["bridge_peer_gate"]["clear_to_merge"] is False


def test_bridge_peer_block_matches_pr_number_when_task_id_differs(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    event = _bridge_event(
        agent="codex",
        type_="finding",
        status="confirmed_bug_blocks_merge",
        task_id="pr477-idle-consensus-readonly-review",
        ts="2026-05-18T01:05:00Z",
    )
    event["message"] = "Lead BLOCK PR #477 exact head abc123."

    report = evaluate_auto_merge_gate(
        pr_status=_status(pr_number=477),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [event]),
        from_agent="claude",
        bridge_task_id="implementation-task-without-pr-number",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert report["bridge_peer_gate"]["clear_to_merge"] is False


def test_apply_requires_bridge_events_path() -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "bridge events path is required before merge" in report["reasons"]
    assert "bridge task id is required before merge" in report["reasons"]


def test_apply_requires_explicit_bridge_task_id(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "bridge task id is required before merge" in report["reasons"]


def test_bridge_peer_approval_clears_same_peer_block(tmp_path: Path) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            [
                _bridge_event(
                    agent="codex",
                    type_="decision",
                    status="changes_requested",
                    ts="2026-05-18T01:05:00Z",
                ),
                _rco_pass(),
                _bridge_event(
                    agent="codex",
                    type_="decision",
                    status="rco_pass_pr531",
                    ts="2026-05-18T01:20:00Z",
                ),
            ],
        ),
        from_agent="claude",
        bridge_task_id="idle-consensus-001",
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_peer_gate"]["latest_approval_event"]["status"] == (
        "rco_pass_pr531"
    )


def test_author_resolves_from_bridge_claim_not_operator_github_login(
    tmp_path: Path,
) -> None:
    task = "wd/rco/rule9a-backup-rco"
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            author_agent=None,
            author_login="Ahkeratmehilaiset",
            author={"login": "Ahkeratmehilaiset"},
        ),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=task,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            [
                _claim("claude-rco-1", task),
                _rco_pass(task_id=task),
            ],
        ),
        bridge_task_id=task,
    )

    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["author_agent"] == "claude-rco-1"
    assert report["rco_pass_gate"]["eligible_rco_agents"] == ["claude-rco-2"]
    assert report["rco_pass_gate"]["ok"] is False
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


def test_unresolvable_author_fails_closed_instead_of_guessing_task_prefix(
    tmp_path: Path,
) -> None:
    task = "wd/rco/rule9a-backup-rco"
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            author_agent=None,
            author_login="Ahkeratmehilaiset",
            author={"login": "Ahkeratmehilaiset"},
        ),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=task,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass(task_id=task)]),
        bridge_task_id=task,
    )

    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["author_agent"] == ""
    assert report["rco_pass_gate"]["decision"] == "invalid_author_agent"
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


def test_missing_from_agent_treats_all_bridge_decisions_as_peer_signals(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            [
                _bridge_event(
                    agent="claude",
                    type_="decision",
                    status="changes_requested",
                ),
            ],
        ),
    )
    assert report["decision"] == "operator_review_required"
    assert report["bridge_peer_gate"]["latest_blocking_event"]["agent"] == "claude"


def test_status_check_rollup_is_supported() -> None:
    status = _status()
    status.pop("checks")
    status["statusCheckRollup"] = [
        {"name": "unified", "conclusion": "SUCCESS", "status": "COMPLETED"},
    ]
    report = evaluate_auto_merge_gate(
        pr_status=status,
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "auto_merge_plan_ready"


def test_empty_status_check_snapshot_blocks_merge() -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(checks=[]),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "operator_review_required"
    assert "status checks snapshot is required before merge" in report["reasons"]


def test_operator_approval_snapshot_metadata_is_not_required() -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(operator_approved=False),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["operator_review_required"] is False
    assert "operator_approved is required before merge" not in report["reasons"]


def test_receipt_verification_required() -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(receipt_verified=False),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "operator_review_required"
    assert "receipt bundle verification is required before merge" in report["reasons"]


def test_receipt_bundle_required() -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
    )
    assert report["decision"] == "operator_review_required"
    assert "receipt_bundle_path is required before merge" in report["reasons"]


def test_runner_failure_fails_closed_without_stderr_echo(tmp_path: Path) -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=_events_path(tmp_path, [_rco_pass()]),
            bridge_task_id="idle-consensus-001",
            apply=True,
            runner=runner,
        )
    report = excinfo.value.report
    assert report["decision"] == "auto_merge_failed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])
    assert report["merge_recovery"]["decision"] == "not_checked"


def test_runner_failure_recovers_when_pr_view_confirms_merge(
    tmp_path: Path,
) -> None:
    verifier_calls: list[tuple[int, str, str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="Gateway Timeout")

    def verifier(pr_number: int, expected_head: str, repo: str) -> dict:
        verifier_calls.append((pr_number, expected_head, repo))
        return {
            "state": "MERGED",
            "headRefOid": expected_head,
            "mergeCommit": {"oid": MERGE_SHA},
        }

    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
        repo="Ahkeratmehilaiset/waggledance-swarm",
        apply=True,
        runner=runner,
        merge_verifier=verifier,
    )
    assert verifier_calls == [(477, HEAD, "Ahkeratmehilaiset/waggledance-swarm")]
    assert report["decision"] == "auto_merged"
    assert report["merge_recovery"]["decision"] == (
        "merged_after_merge_command_failure"
    )
    assert report["auto_merge_event_payload"]["merge_commit_sha"] == MERGE_SHA


def test_runner_failure_still_fails_when_merge_verifier_disagrees(
    tmp_path: Path,
) -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="Gateway Timeout")

    def verifier(pr_number: int, expected_head: str, repo: str) -> dict:
        return {
            "state": "OPEN",
            "headRefOid": expected_head,
            "mergeCommit": None,
        }

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=_events_path(tmp_path, [_rco_pass()]),
            bridge_task_id="idle-consensus-001",
            apply=True,
            runner=runner,
            merge_verifier=verifier,
        )
    report = excinfo.value.report
    assert report["decision"] == "auto_merge_failed"
    assert report["merge_recovery"]["decision"] == "pr_not_merged"


def test_artifact_hook_failure_blocks_merge_without_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def artifact_writer() -> dict:
        return {
            "receipt_bundle": {
                "manifest": "docs/receipts/manifest.json",
                "verifier_report": {"ok": False, "receipt_count": 1, "errors": []},
            }
        }

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(receipt_verified=False),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            events_path=_events_path(tmp_path, [_rco_pass()]),
            bridge_task_id="idle-consensus-001",
            apply=True,
            runner=lambda command: calls.append(list(command)),
            artifact_writer=artifact_writer,
        )
    assert calls == []
    assert excinfo.value.report["decision"] == "artifact_receipt_failed"


def test_private_marker_refused() -> None:
    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(title="PRIVATE_MARKER"),
            expected_head=HEAD,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
        )
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_cli_defaults_events_to_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    runtime_events.write_text(json.dumps(_rco_pass(), sort_keys=True), encoding="utf-8")
    pr_status = tmp_path / "pr_status.json"
    pr_status.write_text(json.dumps(_status(), sort_keys=True), encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))

    exit_code = main(
        [
            "--pr-status-file",
            str(pr_status),
            "--expected-head",
            HEAD,
            "--expected-base-sha",
            BASE,
            "--consensus-proposal-id",
            "idle-consensus-001",
            "--receipt-bundle-path",
            "docs/receipts/manifest.json",
            "--bridge-task-id",
            "idle-consensus-001",
            "--utc-date",
            "2026-05-18",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["rco_pass_gate"]["ok"] is True
