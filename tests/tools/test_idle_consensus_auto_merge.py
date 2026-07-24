from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.idle_consensus_auto_merge as idle_merge_tool
from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.idle_consensus_auto_merge import (
    AutoMergeGateError,
    build_parser,
    evaluate_auto_merge_gate,
    main,
    verify_bridge_consensus,
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


def _git_identity_status_fields() -> dict:
    material = github_pr_git_identity_evidence(
        {
            "author": {
                "login": "Ahkeratmehilaiset",
                "name": "",
                "email": "",
            },
            "commits": [
                {
                    "oid": HEAD,
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
        expected_head_sha=HEAD,
    )
    identities = material.pop("identities")
    return {
        "git_identities": identities,
        "git_identity_evidence": material,
    }


def _status(**overrides) -> dict:
    status = {
        "pr_number": 477,
        "head_sha": HEAD,
        "head_ref": "idle-consensus-001",
        "base_sha": BASE,
        "base_ref": "main",
        "base_tip_sha": BASE,
        "title": "Idle consensus follow-up",
        "mergeable": "clean",
        "state": "OPEN",
        "is_draft": False,
        "updated_at": "2026-07-24T09:00:00Z",
        "author_agent": "claude-rco-2",
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {
                "name": "test (3.13)",
                "state": "success",
                "status": "",
                "conclusion": "",
            },
            {
                "name": "unified",
                "state": "success",
                "status": "",
                "conclusion": "",
            },
        ],
        **_git_identity_status_fields(),
    }
    status.update(overrides)
    return status


def _snapshot_gh_payload(status: dict | None = None) -> dict:
    source = _status() if status is None else status
    return {
        "number": source["pr_number"],
        "title": source["title"],
        "headRefOid": source["head_sha"],
        "headRefName": source["head_ref"],
        "baseRefOid": source["base_sha"],
        "baseRefName": source["base_ref"],
        "mergeable": source["mergeable"],
        "state": source["state"],
        "isDraft": source["is_draft"],
        "url": "https://github.example/pull/477",
        "reviewDecision": "",
        "updatedAt": source["updated_at"],
        "changedFiles": 1,
        "statusCheckRollup": source["checks"],
        "author": {
            "login": "Ahkeratmehilaiset",
            "name": "",
            "email": "",
        },
        "commits": [
            {
                "oid": source["head_sha"],
                "authors": [
                    {
                        "name": "Jani",
                        "email": "jani@jkhservice.fi",
                        "login": "",
                    }
                ],
            }
        ],
    }


def _canonical_apply_runner(
    calls: list[list[str]],
    *,
    merge_returncode: int = 0,
    post_state: str = "MERGED",
    status: dict | None = None,
) -> object:
    snapshot_status = _status() if status is None else status
    payload = _snapshot_gh_payload(snapshot_status)

    def runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        calls.append(command)
        if command[:3] == ["gh", "pr", "merge"]:
            return SimpleNamespace(
                returncode=merge_returncode,
                stdout="",
                stderr="Gateway Timeout" if merge_returncode else "",
            )
        if command[:3] == ["gh", "pr", "view"]:
            if (
                "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
                in command
            ):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 477,
                            "state": post_state,
                            "headRefOid": HEAD,
                            "headRefName": "idle-consensus-001",
                            "baseRefName": "main",
                            "mergeCommit": (
                                {"oid": MERGE_SHA}
                                if post_state == "MERGED"
                                else None
                            ),
                        }
                    ),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "ref": "refs/heads/main",
                            "object": {"type": "commit", "sha": BASE},
                        }
                    ),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "filename": "tools/idle_daily_summary.py",
                            "status": "modified",
                        }
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(
                returncode=0,
                stdout=snapshot_status["diff_text"],
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    return runner


def _events_path(
    tmp_path: Path,
    events: list[dict] | None = None,
    *,
    claim_agent: str = "claude-rco-2",
    claim_task: str = "idle-consensus-001",
    inject_claim: bool = True,
) -> Path:
    rows = list(events or [])
    if inject_claim and not any(event.get("type") == "claim" for event in rows):
        rows.insert(0, _claim(claim_agent, claim_task))
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in rows),
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


def _claim(
    agent: str,
    task_id: str,
    *,
    ts: str = "2026-05-18T00:59:00Z",
    write_scope: list[str] | None = None,
) -> dict:
    event = _bridge_event(
        agent=agent, type_="claim", status="active", task_id=task_id, ts=ts
    )
    event["write_scope"] = ["*"] if write_scope is None else write_scope
    return event


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
    assert report["decision"] == "operator_review_required"
    assert report["dry_run"] is True
    assert report["external_effect"] is False
    assert report["would_merge"] is False
    assert "gh" in report["gh_command"]
    assert f"--match-head-commit={HEAD}" in report["gh_command"]
    assert report["receipt_gate"]["verified"] is True
    assert report["path_gate"]["allowed"] is True
    assert report["diff_gate"]["allowed"] is True
    assert report["author_resolution"]["ok"] is False
    assert any(
        "PR author resolution requires operator review" in reason
        for reason in report["reasons"]
    )


def test_apply_invokes_exact_head_merge_command(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    runner = _canonical_apply_runner(calls)

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
    merge_calls = [call for call in calls if call[:3] == ["gh", "pr", "merge"]]
    assert len(merge_calls) == 1
    assert merge_calls[0][:4] == ["gh", "pr", "merge", "477"]
    assert f"--match-head-commit={HEAD}" in merge_calls[0]
    assert "--delete-branch" not in merge_calls[0]
    assert report["decision"] == "auto_merged"
    assert report["external_effect"] is True
    assert report["auto_merge_event_payload"]["auto_merged"] is True
    assert report["auto_merge_event_payload"]["merge_commit_sha"] == MERGE_SHA
    assert report["apply_recheck"]["ok"] is True


def test_apply_accepts_utf8_bom_events_file(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    events_path = _events_path(tmp_path, [_rco_pass()])
    events_path.write_bytes(b"\xef\xbb\xbf" + events_path.read_bytes())
    runner = _canonical_apply_runner(calls)

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

    assert len(
        [call for call in calls if call[:3] == ["gh", "pr", "merge"]]
    ) == 1
    assert report["decision"] == "auto_merged"
    assert report["auto_merge_event_payload"]["merge_commit_sha"] == MERGE_SHA


def test_apply_runs_artifact_hook_before_exact_head_merge(tmp_path: Path) -> None:
    events: list[str] = []
    command_calls: list[list[str]] = []
    canonical_runner = _canonical_apply_runner(command_calls)

    def artifact_writer() -> dict:
        events.append("artifact")
        return {
            "receipt_bundle": {
                "manifest": "docs/receipts/manifest.json",
                "verifier_report": {"ok": True, "receipt_count": 1, "errors": []},
            }
        }

    def runner(command: list[str]) -> SimpleNamespace:
        events.append("merge" if command[:3] == ["gh", "pr", "merge"] else "recheck")
        return canonical_runner(command)

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
    assert events[0] == "artifact"
    assert events.count("merge") == 1
    assert events.index("artifact") < events.index("merge")
    assert report["decision"] == "auto_merged"
    assert report["auto_merge_event_payload"]["receipt_bundle_path"] == (
        "docs/receipts/manifest.json"
    )


def test_apply_rechecks_full_snapshot_before_merge(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    runner = _canonical_apply_runner(
        calls,
        status=_status(updated_at="2026-07-24T09:00:01Z"),
    )

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

    assert report["decision"] == "operator_review_required"
    assert "apply snapshot recheck drifted: updated_at" in report["reasons"]
    assert not [
        call for call in calls if call[:3] == ["gh", "pr", "merge"]
    ]


def test_zero_exit_without_confirmed_merged_state_fails_closed(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    runner = _canonical_apply_runner(calls, post_state="OPEN")

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

    assert excinfo.value.report["decision"] == "post_merge_state_unconfirmed"
    assert excinfo.value.report["gh_merge_attempted"] is True


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


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"state": "CLOSED"}, "PR state snapshot must be OPEN"),
        ({"is_draft": True}, "PR must not be a draft"),
        ({"is_draft": "false"}, "PR must not be a draft"),
    ],
)
def test_closed_or_draft_pr_snapshot_fails_closed(
    tmp_path: Path,
    overrides: dict,
    reason: str,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(**overrides),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
    )

    assert report["decision"] == "operator_review_required"
    assert reason in report["reasons"]


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


def test_daily_rate_limit_blocks_without_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(idle_merge_tool, "_today_utc", lambda: "2026-05-18")
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


@pytest.mark.parametrize(
    "utc_date",
    ["not-a-date", "20260724", "2026-7-24", " 2026-07-24"],
)
def test_quota_date_requires_exact_iso_calendar_date(
    utc_date: str,
) -> None:
    calls: list[list[str]] = []

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            utc_date=utc_date,
            apply=True,
            runner=lambda command: calls.append(list(command)),
        )

    assert calls == []
    assert excinfo.value.report["decision"] == "invalid_input"
    assert "YYYY-MM-DD" in excinfo.value.report["errors"][0]


def test_apply_rejects_historical_quota_date_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(idle_merge_tool, "_today_utc", lambda: "2026-07-24")
    calls: list[list[str]] = []

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=_events_path(
                tmp_path,
                [
                    _auto_merge_event(index, ts="2026-07-24T01:00:00Z")
                    for index in range(1, 6)
                ],
            ),
            utc_date="2026-07-23",
            apply=True,
            runner=lambda command: calls.append(list(command)),
        )

    assert calls == []
    assert excinfo.value.report["decision"] == "invalid_input"
    assert "current UTC date" in excinfo.value.report["errors"][0]


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
    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["ok"] is False
    assert report["rco_pass_gate"]["decision"] == (
        "not_checked_operator_review_required"
    )


def test_operator_merge_required_rco_status_does_not_satisfy_merge_gate(
    tmp_path: Path,
) -> None:
    # rco_pass_operator_merge_required is RETIRED (RCOs post plain rco_pass and
    # convey operator-merge in the message). This regression is deliberately kept
    # to verify a stray variant fails toward STUCK -- does not satisfy the merge
    # gate -- never fail-open.
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
    # rco_pass_operator_merge_required is RETIRED; this regression is kept to
    # verify the retired variant is rejected as a qualifying RCO pass
    # (fail-toward-stuck-safe), never silently accepted by consensus.
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


# --- 9b standing-consensus-sign wiring (end-to-end through the merge gate) ---
def _dual_rco_events() -> list[dict]:
    """lead+tools build + DUAL-RCO rco_pass, all head-bound (best-possible-consensus).
    Includes a fable-5 author claim so the author resolves to a non-reviewer agent
    (both recognized RCOs stay eligible)."""
    return [
        _claim("fable-5", "idle-consensus-001", ts="2026-06-07T17:30:00Z"),
        _bridge_event(
            agent="codex-lead-1", type_="decision",
            status="build_consensus_pass", ts="2026-06-07T17:34:11Z",
        ) | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="codex-tools-1", type_="decision",
            status="build_consensus_pass", ts="2026-06-07T17:38:40Z",
        ) | {"payload": {"exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-1", type_="decision",
            status="rco_pass", ts="2026-06-07T17:39:47Z",
        ) | {"payload": {"pr": 477, "exact_head": HEAD}},
        _bridge_event(
            agent="claude-rco-2", type_="decision",
            status="rco_pass", ts="2026-06-07T17:40:10Z",
        ) | {"payload": {"pr": 477, "exact_head": HEAD}},
    ]


def _standing_status(**overrides) -> dict:
    # off-allowlist (path not on the allowlist) + (b)-class + non-RCO author so
    # both recognized RCOs are eligible reviewers.
    base = dict(
        author_agent="fable-5",
        changed_paths=["tools/run_hex_readiness_proof.py", "docs/runs/board.md"],
    )
    base.update(overrides)
    return _status(**base)


def _standing_gate(tmp_path: Path, *, events: list[dict] | None = None, **over):
    kw = dict(
        pr_status=_standing_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _dual_rco_events() if events is None else events),
        bridge_task_id="idle-consensus-001",
        require_bridge_consensus=True,
    )
    kw.update(over)
    return evaluate_auto_merge_gate(**kw)


def test_standing_sign_admits_b_class_offallowlist_with_dual_rco(tmp_path: Path) -> None:
    report = _standing_gate(tmp_path, standing_consensus_sign=True)
    assert report["path_gate"]["allowed"] is False          # off-allowlist
    assert report["standing_consensus_sign"]["admitted"] is True
    assert report["standing_consensus_sign"]["ab_class"] == "b"
    assert report["decision"] == "auto_merge_plan_ready"     # path block dropped


def test_standing_sign_default_off_keeps_offallowlist_blocked(tmp_path: Path) -> None:
    report = _standing_gate(tmp_path)                         # standing_consensus_sign defaults False
    assert report["standing_consensus_sign"]["admitted"] is False
    assert report["decision"] == "operator_review_required"
    assert any("path gate failed" in r for r in report["reasons"])


def test_standing_sign_refuses_a_class_even_when_enabled(tmp_path: Path) -> None:
    report = _standing_gate(
        tmp_path,
        pr_status=_standing_status(changed_paths=["tools/idle_consensus_auto_merge.py"]),
        standing_consensus_sign=True,
    )
    assert report["standing_consensus_sign"]["ab_class"] == "a"
    assert report["standing_consensus_sign"]["admitted"] is False
    assert report["decision"] == "operator_review_required"


def test_standing_sign_refuses_single_rco(tmp_path: Path) -> None:
    # drop rco-2 -> only single RCO (claim, lead, tools, rco-1) -> dual-RCO
    # incomplete -> not admitted
    report = _standing_gate(
        tmp_path, events=_dual_rco_events()[:4], standing_consensus_sign=True,
    )
    assert report["standing_consensus_sign"]["admitted"] is False
    assert report["decision"] == "operator_review_required"


def test_cli_exposes_default_off_standing_consensus_sign_flag() -> None:
    args = build_parser().parse_args(
        [
            "--pr-status-file",
            "status.json",
            "--expected-head",
            HEAD,
            "--consensus-proposal-id",
            "idle-consensus-001",
            "--standing-consensus-sign",
        ]
    )
    assert args.standing_consensus_sign is True


@pytest.mark.parametrize(
    ("author_agent", "waived_role", "peer_role"),
    [
        ("codex-lead-1", "build_lead", "build_tools"),
        ("codex-tools-1", "build_tools", "build_lead"),
    ],
)
def test_bridge_consensus_waives_build_author_slot_with_independent_peer(
    tmp_path: Path,
    author_agent: str,
    waived_role: str,
    peer_role: str,
) -> None:
    events = [
        _claim(author_agent, "idle-consensus-001"),
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
    consensus = report["bridge_consensus"]
    waived = consensus["identities"][waived_role]
    peer = consensus["identities"][peer_role]
    assert report["decision"] == "auto_merge_plan_ready"
    assert consensus["ok"] is True
    assert consensus["build_author_slot_waivers"] == [author_agent]
    assert waived["build_author_slot_waived"] is True
    assert waived["eligible"] is False
    assert waived["approved"] is True
    assert waived["direct_approval"] is False
    assert waived["self_approval_ignored"] is True
    assert peer["build_author_slot_waived"] is False
    assert peer["approved"] is True
    assert peer["direct_approval"] is True
    assert not any(
        "author_agent cannot satisfy its own reviewer slot" in reason
        for reason in consensus["reasons"]
    )


@pytest.mark.parametrize(
    ("author_agent", "waived_role", "peer_role", "peer_agent"),
    [
        ("codex-lead-1", "build_lead", "build_tools", "codex-tools-1"),
        ("codex-tools-1", "build_tools", "build_lead", "codex-lead-1"),
    ],
)
def test_bridge_consensus_build_author_waiver_still_requires_peer_build_slot(
    tmp_path: Path,
    author_agent: str,
    waived_role: str,
    peer_role: str,
    peer_agent: str,
) -> None:
    events = [
        _claim(author_agent, "idle-consensus-001"),
        _bridge_event(
            agent=author_agent,
            type_="decision",
            status="build_consensus_pass",
            ts="2026-06-07T17:34:11Z",
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
    consensus = report["bridge_consensus"]
    waived = consensus["identities"][waived_role]
    peer = consensus["identities"][peer_role]
    assert report["decision"] == "operator_review_required"
    assert consensus["ok"] is False
    assert consensus["build_author_slot_waivers"] == [author_agent]
    assert waived["build_author_slot_waived"] is True
    assert waived["approved"] is True
    assert waived["direct_approval"] is False
    assert waived["self_approval_ignored"] is True
    assert peer["approved"] is False
    assert any(
        f"{peer_role} ({peer_agent}): no head-bound approval at {HEAD}" in reason
        for reason in consensus["reasons"]
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
    canonical_task = "fable-5/v12-solver-growth-coverage-summary-20260608"
    wrong_task = "fable-5-v12-solver-growth-coverage-summary-20260608"
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
        pr_status=_status(
            head_ref=canonical_task,
            author_agent="fable-5",
        ),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=canonical_task,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            events,
            claim_agent="fable-5",
            claim_task=canonical_task,
        ),
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


def test_bridge_consensus_resolved_status_resets_prior_same_agent_block(
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
            status="changes_requested",
            ts="2026-06-07T17:38:40Z",
        ),
        _bridge_event(
            agent="codex-tools-1",
            type_="done",
            status="changes_requested_resolved_ci_pending",
            ts="2026-06-07T17:39:47Z",
        ),
        _rco_pass(),
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
    build_tools = report["bridge_consensus"]["identities"]["build_tools"]

    assert report["decision"] == "operator_review_required"
    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_peer_gate"]["latest_blocking_event"] is None
    assert report["bridge_consensus"]["ok"] is False
    assert build_tools["approved"] is False
    assert build_tools["approval_index"] is None
    assert build_tools["block_index"] is None


def test_bridge_consensus_cleared_status_resets_prior_same_agent_block(
    tmp_path: Path,
) -> None:
    # Mirror of the resolved-status reset test for changes_requested_cleared:
    # "cleared" is a natural veto-lift suffix and must reset the prior same-agent
    # build/RCO block in the consensus loop (without granting an approval).
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
            status="changes_requested",
            ts="2026-06-07T17:38:40Z",
        ),
        _bridge_event(
            agent="codex-tools-1",
            type_="done",
            status="changes_requested_cleared",
            ts="2026-06-07T17:39:47Z",
        ),
        _rco_pass(),
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
    build_tools = report["bridge_consensus"]["identities"]["build_tools"]

    assert report["bridge_peer_gate"]["clear_to_merge"] is True
    assert report["bridge_peer_gate"]["latest_blocking_event"] is None
    # cleared resets the prior block but is NOT an approval.
    assert build_tools["block_index"] is None
    assert build_tools["approved"] is False


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
        events_path=_events_path(
            tmp_path,
            _lead_stall_failover_events(),
            claim_agent="codex-tools-1",
        ),
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
        events_path=_events_path(
            tmp_path,
            events,
            claim_agent=str(
                (status_overrides or {}).get("author_agent", "claude-rco-2")
            ),
        ),
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
    status = _status(
        author_login="Ahkeratmehilaiset",
        author={"login": "Ahkeratmehilaiset"},
        head_ref=task,
    )
    status.pop("author_agent")
    report = evaluate_auto_merge_gate(
        pr_status=status,
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
            head_ref=task,
        ),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=task,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(
            tmp_path,
            [_rco_pass(task_id=task)],
            inject_claim=False,
        ),
        bridge_task_id=task,
    )

    assert report["decision"] == "operator_review_required"
    assert report["rco_pass_gate"]["author_agent"] == ""
    assert report["rco_pass_gate"]["decision"] == "invalid_author_agent"
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


@pytest.mark.parametrize(
    "mode",
    ["absent_evidence", "empty_identities", "incomplete_evidence"],
)
def test_missing_or_incomplete_identity_evidence_blocks_with_bridge_events(
    tmp_path: Path,
    mode: str,
) -> None:
    status = _status()
    if mode == "absent_evidence":
        status.pop("git_identity_evidence")
    elif mode == "empty_identities":
        status["git_identities"] = []
        status["git_identity_evidence"] = {
            **status["git_identity_evidence"],
            "identity_count": 0,
            "commit_author_count": 0,
        }
    else:
        status["git_identity_evidence"] = dict(
            status["git_identity_evidence"]
        )
        status["git_identity_evidence"].pop("commit_oids")

    report = evaluate_auto_merge_gate(
        pr_status=status,
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
    )

    assert report["decision"] == "operator_review_required"
    assert report["author_resolution"]["ok"] is False
    assert any(
        "PR author resolution requires operator review" in reason
        for reason in report["reasons"]
    )


def test_non_string_author_assertion_blocks_instead_of_coercing(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(author_agent=["claude-rco-2"]),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
    )

    assert report["decision"] == "operator_review_required"
    assert report["author_resolution"]["decision"] == "invalid_author_evidence"
    assert "must be a string" in report["author_resolution"]["reasons"][0]


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


def test_status_check_rollup_is_supported(tmp_path: Path) -> None:
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
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
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


def test_operator_approval_snapshot_metadata_is_not_required(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(operator_approved=False),
        expected_head=HEAD,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
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
    calls: list[list[str]] = []
    canonical_runner = _canonical_apply_runner(
        calls,
        merge_returncode=7,
        post_state="OPEN",
    )

    def runner(command: list[str]) -> SimpleNamespace:
        result = canonical_runner(command)
        if command[:3] == ["gh", "pr", "merge"]:
            return SimpleNamespace(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr="PRIVATE_MARKER",
            )
        return result

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
    assert report["merge_recovery"]["decision"] == "pr_not_merged"


@pytest.mark.parametrize("returncode", [None, False, "0", 0.0])
def test_idle_merge_result_requires_exact_integer_returncode(
    tmp_path: Path,
    returncode: object,
) -> None:
    calls: list[list[str]] = []
    runner = _canonical_apply_runner(
        calls,
        merge_returncode=returncode,  # type: ignore[arg-type]
    )

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
    assert report["decision"] == "auto_merge_invalid_result"
    assert report.get("auto_merge_event_payload") is None


def test_falsey_callable_idle_runner_is_used_without_default_fallback(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    delegate = _canonical_apply_runner(calls)

    class FalseyRunner:
        def __bool__(self) -> bool:
            return False

        def __call__(self, command: list[str]) -> SimpleNamespace:
            return delegate(command)

    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
        apply=True,
        runner=FalseyRunner(),
    )

    assert report["decision"] == "auto_merged"
    assert calls


def test_runner_failure_recovers_when_pr_view_confirms_merge(
    tmp_path: Path,
) -> None:
    verifier_calls: list[tuple[int, str, str]] = []
    calls: list[list[str]] = []
    runner = _canonical_apply_runner(calls, merge_returncode=1)

    def verifier(pr_number: int, expected_head: str, repo: str) -> dict:
        verifier_calls.append((pr_number, expected_head, repo))
        return {
            "number": 477,
            "state": "MERGED",
            "headRefOid": expected_head,
            "headRefName": "idle-consensus-001",
            "baseRefName": "main",
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


def test_unregistered_rco_cannot_satisfy_bridge_consensus() -> None:
    events = [
        _bridge_event(
            agent="codex-lead-1",
            type_="decision",
            status="build_consensus_pass",
        )
        | {"payload": {"head": HEAD}},
        _bridge_event(
            agent="codex-tools-1",
            type_="decision",
            status="build_consensus_pass",
        )
        | {"payload": {"head": HEAD}},
        {
            "agent": "evil-rco",
            "type": "decision",
            "status": "rco_pass",
            "task_id": "idle-consensus-001",
            "payload": {"head": HEAD, "pr": 477},
        },
    ]

    report = verify_bridge_consensus(
        events=events,
        task_id="idle-consensus-001",
        head_sha=HEAD,
        pr_number=477,
        rco_agent="evil-rco",
        author_agent="fable-5",
    )

    assert report["ok"] is False
    assert report["decision"] == "invalid_consensus_config"
    assert "not registered" in report["reasons"][0]


def test_injected_registry_cannot_register_an_attacker_rco() -> None:
    injected = {
        **AGENT_UUIDS,
        "evil-rco": "11111111-1111-4111-8111-111111111111",
    }

    report = verify_bridge_consensus(
        events=[],
        task_id="idle-consensus-001",
        head_sha=HEAD,
        pr_number=477,
        rco_agent="evil-rco",
        author_agent="fable-5",
        identity_registry=injected,
    )

    assert report["ok"] is False
    assert report["decision"] == "invalid_identity_registry"
    assert "canonical registry" in report["reasons"][0]


@pytest.mark.parametrize(
    ("field", "value", "decision"),
    [
        ("task_id", False, "invalid_consensus_config"),
        ("task_id", " idle-consensus-001", "invalid_consensus_config"),
        ("head_sha", False, "invalid_consensus_head"),
        ("head_sha", 7, "invalid_consensus_head"),
        ("pr_number", False, "invalid_consensus_config"),
        ("allow_lead_stall_failover", 1, "invalid_consensus_config"),
        (
            "lead_stall_failover_threshold_seconds",
            False,
            "invalid_consensus_config",
        ),
        (
            "lead_stall_failover_changed_paths",
            "tools/a.py",
            "invalid_consensus_config",
        ),
        (
            "lead_stall_failover_diff_text",
            False,
            "invalid_consensus_config",
        ),
        (
            "lead_stall_failover_charter_path",
            False,
            "invalid_consensus_config",
        ),
        ("lead_agent", " codex-lead-1", "invalid_consensus_config"),
        ("tools_agent", " codex-tools-1", "invalid_consensus_config"),
        ("author_agent", " fable-5", "invalid_consensus_config"),
        ("rco_agent", [1], "invalid_consensus_config"),
    ],
)
def test_bridge_consensus_public_inputs_fail_closed_without_type_leaks(
    field: str,
    value: object,
    decision: str,
) -> None:
    kwargs: dict[str, object] = {
        "events": [],
        "task_id": "idle-consensus-001",
        "head_sha": HEAD,
        "pr_number": 477,
        "author_agent": "fable-5",
    }
    kwargs[field] = value

    report = verify_bridge_consensus(**kwargs)  # type: ignore[arg-type]

    assert report["ok"] is False
    assert report["decision"] == decision
    assert report["rco_pass_ref"] is None


def test_runner_failure_still_fails_when_merge_verifier_disagrees(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    runner = _canonical_apply_runner(calls, merge_returncode=1)

    def verifier(pr_number: int, expected_head: str, repo: str) -> dict:
        return {
            "number": 477,
            "state": "OPEN",
            "headRefOid": expected_head,
            "headRefName": "idle-consensus-001",
            "baseRefName": "main",
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


@pytest.mark.parametrize("ok_value", [False, 0, "false", None])
def test_merge_recovery_rejects_every_present_nontrue_ok_value(
    ok_value: object,
) -> None:
    state = {
        "number": 477,
        "state": "MERGED",
        "headRefOid": HEAD,
        "headRefName": "idle-consensus-001",
        "baseRefName": "main",
        "mergeCommit": {"oid": MERGE_SHA},
        "ok": ok_value,
    }

    report = idle_merge_tool._recover_merge_state_after_failure(
        pr_number=477,
        expected_head=HEAD,
        expected_head_ref="idle-consensus-001",
        expected_base_ref="main",
        repo="example/repo",
        return_code=1,
        verifier=lambda *_args: state,
    )

    assert report["merged"] is False
    assert report["decision"] == "verifier_refused_merge_state"


def test_merge_recovery_rejects_wrong_pr_even_when_other_fields_match() -> None:
    report = idle_merge_tool._recover_merge_state_after_failure(
        pr_number=477,
        expected_head=HEAD,
        expected_head_ref="idle-consensus-001",
        expected_base_ref="main",
        repo="example/repo",
        return_code=1,
        verifier=lambda *_args: {
            "number": 9999,
            "state": "MERGED",
            "headRefOid": HEAD,
            "headRefName": "idle-consensus-001",
            "baseRefName": "main",
            "mergeCommit": {"oid": MERGE_SHA},
        },
    )

    assert report["merged"] is False
    assert report["decision"] == "merged_pr_mismatch"


@pytest.mark.parametrize(
    "manifest",
    [7, False, ["manifest"], {"manifest": "x"}, "", " ", " path.json"],
)
def test_artifact_manifest_requires_an_exact_nonempty_string(
    manifest: object,
) -> None:
    with pytest.raises(AutoMergeGateError) as excinfo:
        idle_merge_tool._verified_artifact_manifest(
            {
                "receipt_bundle": {
                    "manifest": manifest,
                    "verifier_report": {"ok": True},
                }
            }
        )

    assert excinfo.value.report["decision"] == "artifact_receipt_failed"


@pytest.mark.parametrize("returncode", [None, False, "0", 0.0])
def test_merge_state_query_requires_exact_integer_returncode(
    returncode: object,
) -> None:
    result = SimpleNamespace(stdout="{}", stderr="")
    if returncode is not None:
        result.returncode = returncode

    report = idle_merge_tool._query_pr_merge_state(
        477,
        HEAD,
        "example/repo",
        runner=lambda _command: result,
    )

    assert report["ok"] is False
    assert report["decision"] == "merge_state_query_invalid_result"


def test_falsey_callable_merge_state_runner_is_not_replaced() -> None:
    calls: list[list[str]] = []

    class FalseyRunner:
        def __bool__(self) -> bool:
            return False

        def __call__(self, command: list[str]) -> SimpleNamespace:
            calls.append(list(command))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "number": 477,
                        "state": "MERGED",
                        "headRefOid": HEAD,
                        "headRefName": "idle-consensus-001",
                        "baseRefName": "main",
                        "mergeCommit": {"oid": MERGE_SHA},
                    }
                ),
                stderr="",
            )

    report = idle_merge_tool._query_pr_merge_state(
        477,
        HEAD,
        "example/repo",
        runner=FalseyRunner(),
    )

    assert calls
    assert report["number"] == 477


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
    runtime_events.write_text(
        "\n".join(
            json.dumps(event, sort_keys=True)
            for event in [
                _claim("claude-rco-2", "idle-consensus-001"),
                _rco_pass(),
            ]
        ),
        encoding="utf-8",
    )
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


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_receipt_verified_requires_exact_boolean(
    tmp_path: Path,
    value: object,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(receipt_verified=value),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, [_rco_pass()]),
        bridge_task_id="idle-consensus-001",
    )

    assert report["decision"] == "operator_review_required"
    assert report["receipt_gate"]["verified"] is False
    assert "receipt_verified must be a boolean" in report["reasons"]


@pytest.mark.parametrize(
    ("field", "value", "decision"),
    [
        ("pr_status", None, "invalid_pr_status"),
        ("pr_status", [], "invalid_pr_status"),
        ("pr_status", "status", "invalid_pr_status"),
        ("pr_status", 7, "invalid_pr_status"),
        ("pr_status", True, "invalid_pr_status"),
        ("expected_head", 0, "invalid_input"),
        ("expected_base_sha", False, "invalid_input"),
        ("consensus_proposal_id", [], "invalid_input"),
        ("consensus_proposal_id", " idle-consensus-001", "invalid_input"),
        ("receipt_bundle_path", None, "invalid_input"),
        ("repo", 7, "invalid_input"),
        ("from_agent", False, "invalid_input"),
        ("from_agent", " codex-lead-1", "invalid_input"),
        ("bridge_task_id", {}, "invalid_input"),
        ("bridge_task_id", " idle-consensus-001", "invalid_input"),
        ("utc_date", 20260724, "invalid_input"),
        ("events_path", "events.jsonl", "invalid_input"),
        ("charter_path", "charter.toml", "invalid_input"),
        ("lead_stall_failover_threshold_seconds", 1.5, "invalid_input"),
        ("runner", False, "invalid_input"),
        ("merge_verifier", 0, "invalid_input"),
        ("artifact_writer", [], "invalid_input"),
    ],
)
def test_malformed_public_inputs_raise_controlled_gate_error_before_effects(
    field: str,
    value: object,
    decision: str,
) -> None:
    calls: list[list[str]] = []
    kwargs: dict[str, object] = {
        "pr_status": _status(),
        "expected_head": HEAD,
        "expected_base_sha": BASE,
        "consensus_proposal_id": "idle-consensus-001",
        "receipt_bundle_path": "docs/receipts/manifest.json",
        "runner": lambda command: calls.append(list(command)),
    }
    kwargs[field] = value

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(**kwargs)  # type: ignore[arg-type]

    assert calls == []
    assert excinfo.value.report["decision"] == decision
    assert excinfo.value.report["external_effect"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("apply", "false"),
        ("apply", 0),
        ("apply", 1),
        ("require_bridge_consensus", "false"),
        ("require_bridge_consensus", 0),
        ("standing_consensus_sign", "true"),
        ("standing_consensus_sign", 1),
        ("allow_lead_stall_failover", []),
        ("allow_lead_stall_failover", None),
    ],
)
def test_effect_and_control_flags_require_exact_booleans(
    field: str,
    value: object,
) -> None:
    calls: list[list[str]] = []
    kwargs: dict[str, object] = {
        "pr_status": _status(),
        "expected_head": HEAD,
        "expected_base_sha": BASE,
        "consensus_proposal_id": "idle-consensus-001",
        "receipt_bundle_path": "docs/receipts/manifest.json",
        "runner": lambda command: calls.append(list(command)),
    }
    kwargs[field] = value

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(**kwargs)  # type: ignore[arg-type]

    assert calls == []
    assert excinfo.value.report["decision"] == "invalid_input"
    assert f"{field} must be a boolean" in excinfo.value.report["errors"][0]


@pytest.mark.parametrize(
    "contents",
    [
        b'{"type":"claim","type":"decision"}\n',
        b'{"type":"claim","payload":{"weight":NaN}}\n',
        b'{"type":"claim","payload":{"weight":Infinity}}\n',
        b"\xff\xfe\x00",
    ],
)
def test_bridge_events_use_strict_json_and_utf8(
    tmp_path: Path,
    contents: bytes,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(contents)
    calls: list[list[str]] = []

    with pytest.raises(AutoMergeGateError) as excinfo:
        evaluate_auto_merge_gate(
            pr_status=_status(),
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id="idle-consensus-001",
            receipt_bundle_path="docs/receipts/manifest.json",
            events_path=events_path,
            runner=lambda command: calls.append(list(command)),
        )

    assert calls == []
    assert excinfo.value.report["decision"] == "invalid_events"


@pytest.mark.parametrize(
    "contents",
    [
        b'{"pr_number":477,"pr_number":478}',
        b'{"pr_number":NaN}',
        b'{"pr_number":Infinity}',
        b"\xff\xfe\x00",
    ],
)
def test_cli_pr_status_uses_strict_json_and_utf8(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: bytes,
) -> None:
    pr_status = tmp_path / "pr-status.json"
    pr_status.write_bytes(contents)

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
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "invalid_pr_status"
    assert report["external_effect"] is False
