from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.idle_consensus_auto_merge import (
    AutoMergeGateError,
    evaluate_auto_merge_gate,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
MERGE_SHA = "abcdef1234567890abcdef1234567890abcdef12"


def _status(**overrides) -> dict:
    status = {
        "pr_number": 477,
        "head_sha": HEAD,
        "title": "Idle consensus follow-up",
        "mergeable": "clean",
        "operator_approved": False,
        "receipt_verified": True,
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
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": "",
        "payload": {},
    }


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


def test_apply_invokes_exact_head_merge_command(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="abcdef\n")

    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
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
        consensus_proposal_id="idle-consensus-001",
        events_path=_events_path(tmp_path),
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
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        apply=True,
        runner=lambda command: calls.append(list(command)),
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert "exact head mismatch" in report["reasons"]
    assert report["external_effect"] is False


def test_daily_rate_limit_blocks_without_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
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
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=_events_path(tmp_path),
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
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path),
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
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=_events_path(tmp_path),
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
            consensus_proposal_id="idle-consensus-001",
            events_path=_events_path(tmp_path),
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
