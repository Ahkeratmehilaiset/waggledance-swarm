# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from tools.merge_with_bridge_receipt import merge_with_bridge_receipt


HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BASE = "fedcba0987654321fedcba0987654321fedcba09"
OTHER_BASE = "1111111111111111111111111111111111111111"
MERGE_SHA = "2222222222222222222222222222222222222222"
AUTHOR = "claude-rco-2"
TASK = f"{AUTHOR}/bridge-consensus-receipt-test"
NOW = datetime(2026, 6, 14, 6, 20, tzinfo=timezone.utc)
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}


def test_dry_run_writes_receipt_without_attempting_merge(tmp_path: Path) -> None:
    calls, runner = _runner()

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "merge_receipt_ready"
    assert report["ok"] is True
    assert report["gh_merge_attempted"] is False
    assert report["merge_executed"] is False
    assert not _merge_calls(calls)
    assert (tmp_path / "out" / "pr-status.json").exists()
    assert Path(report["receipt_bundle_path"]).exists()


def test_apply_runs_merge_only_after_receipt_passes(tmp_path: Path) -> None:
    calls, runner = _runner()

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "merge_executed_after_receipt"
    assert report["ok"] is True
    merge_calls = _merge_calls(calls)
    assert len(merge_calls) == 1
    assert f"--match-head-commit={HEAD}" in merge_calls[0]
    assert "--squash" in merge_calls[0]
    assert Path(report["receipt_bundle_path"]).exists()
    assert report["post_merge"]["decision"] == "post_merge_confirmed"
    assert report["merge_commit_sha"] == MERGE_SHA


def test_receipt_failure_blocks_merge_attempt(tmp_path: Path) -> None:
    calls, runner = _runner()

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()[:-1]),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "receipt_preflight_failed"
    assert report["ok"] is False
    assert report["stage"] == "receipt"
    assert report["gh_merge_attempted"] is False
    assert report["merge_executed"] is False
    assert not _merge_calls(calls)
    assert not (tmp_path / "out" / "receipt").exists()


def test_stale_base_snapshot_blocks_before_receipt_or_merge(tmp_path: Path) -> None:
    calls, runner = _runner(payload=_gh_payload(baseRefOid=OTHER_BASE))

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "pr_status_snapshot_failed"
    assert report["stage"] == "snapshot"
    assert report["gh_merge_attempted"] is False
    assert not _merge_calls(calls)
    assert not (tmp_path / "out").exists()


def test_unexpected_head_blocks_before_receipt_or_merge(tmp_path: Path) -> None:
    calls, runner = _runner(payload=_gh_payload(headRefOid=OTHER_HEAD))

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "unexpected_head"
    assert report["stage"] == "snapshot"
    assert report["gh_merge_attempted"] is False
    assert not _merge_calls(calls)
    assert not (tmp_path / "out").exists()


def test_gh_merge_failure_is_reported_after_receipt(tmp_path: Path) -> None:
    calls, runner = _runner(merge_returncode=1)

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "gh_merge_failed"
    assert report["stage"] == "merge"
    assert report["gh_merge_attempted"] is True
    assert report["merge_executed"] is False
    assert len(_merge_calls(calls)) == 1
    assert Path(report["receipt_bundle_path"]).exists()


def test_zero_exit_without_confirmed_merged_state_fails_closed(
    tmp_path: Path,
) -> None:
    calls, runner = _runner(post_state="OPEN")

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "post_merge_state_unconfirmed"
    assert report["gh_merge_attempted"] is True
    assert report["merge_executed"] is False
    assert len(_merge_calls(calls)) == 1


def test_post_merge_head_ref_identity_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    calls, runner = _runner(post_head_ref="other/task")

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "post_merge_state_unconfirmed"
    assert report["post_merge"]["decision"] == "post_merge_not_confirmed"
    assert report["merge_executed"] is False
    assert len(_merge_calls(calls)) == 1


def test_apply_recheck_stabilizes_full_safety_snapshot(
    tmp_path: Path,
) -> None:
    calls, runner = _runner(
        apply_payload=_gh_payload(updatedAt="2026-07-24T09:00:01Z")
    )

    report = merge_with_bridge_receipt(
        pr_number=1174,
        repo="example/repo",
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        apply=True,
        now_utc=NOW,
        runner=runner,
    )

    assert report["decision"] == "apply_snapshot_recheck_failed"
    assert "updated_at" in report["errors"][0]
    assert not _merge_calls(calls)


@pytest.mark.parametrize("apply", ["false", 0, 1, None, [], {}])
def test_apply_requires_exact_boolean_without_invoking_runner(
    tmp_path: Path,
    apply: object,
) -> None:
    calls, runner = _runner()

    with pytest.raises(ValueError, match="apply must be a boolean"):
        merge_with_bridge_receipt(
            pr_number=1174,
            repo="example/repo",
            events_path=_events_path(tmp_path, _full_consensus()),
            out_dir=tmp_path / "out",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            apply=apply,  # type: ignore[arg-type]
            now_utc=NOW,
            runner=runner,
        )

    assert calls == []
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("sha", [HEAD.upper(), f" {HEAD}", f"{HEAD} "])
def test_expected_sha_is_not_normalized_before_effects(
    tmp_path: Path,
    sha: str,
) -> None:
    calls, runner = _runner()

    with pytest.raises(ValueError, match="40-char lowercase sha"):
        merge_with_bridge_receipt(
            pr_number=1174,
            repo="example/repo",
            events_path=_events_path(tmp_path, _full_consensus()),
            out_dir=tmp_path / "out",
            expected_head=sha,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            apply=True,
            now_utc=NOW,
            runner=runner,
        )

    assert calls == []
    assert not (tmp_path / "out").exists()


def _runner(
    *,
    payload: dict[str, Any] | None = None,
    apply_payload: dict[str, Any] | None = None,
    merge_returncode: int = 0,
    post_state: str = "MERGED",
    post_head: str = HEAD,
    post_head_ref: str = TASK,
    post_base_ref: str = "main",
) -> tuple[list[list[str]], Any]:
    calls: list[list[str]] = []
    payload_to_use = payload or _gh_payload()
    apply_payload_to_use = apply_payload or payload_to_use
    snapshot_view_count = 0

    def run(command: Sequence[str]) -> SimpleNamespace:
        nonlocal snapshot_view_count
        call = list(command)
        calls.append(call)
        if call[:3] == ["gh", "pr", "view"]:
            if (
                "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
                in call
            ):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 1174,
                            "state": post_state,
                            "headRefOid": post_head,
                            "headRefName": post_head_ref,
                            "baseRefName": post_base_ref,
                            "mergeCommit": (
                                {"oid": MERGE_SHA}
                                if post_state == "MERGED"
                                else None
                            ),
                        }
                    ),
                    stderr="",
                )
            selected_payload = (
                payload_to_use
                if snapshot_view_count < 2
                else apply_payload_to_use
            )
            snapshot_view_count += 1
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(selected_payload),
                stderr="",
            )
        if call[:2] == ["gh", "api"] and "/git/ref/heads/" in call[4]:
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
        if call[:2] == ["gh", "api"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "filename": "tools/agent_next_task.py",
                            "status": "modified",
                        }
                    ]
                ),
                stderr="",
            )
        if call[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(
                returncode=0,
                stdout="diff --git a/tools/agent_next_task.py b/tools/agent_next_task.py\n",
            )
        if call[:3] == ["gh", "pr", "merge"]:
            return SimpleNamespace(returncode=merge_returncode, stdout="")
        raise AssertionError(f"unexpected command: {call}")

    return calls, run


def _merge_calls(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[:3] == ["gh", "pr", "merge"]]


def _gh_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "number": 1174,
        "title": "fix(bridge): test receipt writer",
        "headRefOid": HEAD,
        "headRefName": TASK,
        "baseRefOid": BASE,
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "state": "OPEN",
        "isDraft": False,
        "url": "https://github.com/example/repo/pull/1174",
        "reviewDecision": "",
        "updatedAt": "2026-07-24T09:00:00Z",
        "changedFiles": 1,
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
        "statusCheckRollup": [
            {"name": "unified", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"},
        ],
    }
    payload.update(overrides)
    if "headRefOid" in overrides and "commits" not in overrides:
        payload["commits"][0]["oid"] = overrides["headRefOid"]  # type: ignore[index]
    return payload


def _events_path(tmp_path: Path, events: list[dict[str, object]]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def _full_consensus() -> list[dict[str, object]]:
    return [
        {
            "ts_utc": "2026-06-14T06:09:00Z",
            "agent": AUTHOR,
            "agent_uuid": AGENT_UUIDS[AUTHOR],
            "type": "claim",
            "status": "active",
            "task_id": TASK,
            "write_scope": ["tools/agent_next_task.py"],
            "message": f"claim at head {HEAD}",
            "payload": {"head": HEAD, "pr": 1174},
        },
        _event("codex-lead-1", "build_consensus_pass", "2026-06-14T06:10:00Z"),
        _event("codex-tools-1", "build_consensus_pass", "2026-06-14T06:11:00Z"),
        _event("claude-rco-1", "rco_pass", "2026-06-14T06:12:00Z"),
    ]


def _event(agent: str, status: str, ts_utc: str) -> dict[str, object]:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "agent_uuid": AGENT_UUIDS[agent],
        "type": "decision",
        "status": status,
        "task_id": TASK,
        "message": f"{status} at head {HEAD}",
        "payload": {"head": HEAD, "pr": 1174},
    }
