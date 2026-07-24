# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

import tools.merge_with_bridge_receipt as merge_tool
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


@pytest.fixture(autouse=True)
def _trusted_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_tool, "_utc_now", lambda: NOW)


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
    calls, runner = _runner(merge_returncode=1, post_state="OPEN")

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
    assert report["merge_executed"] is None
    assert report["dry_run"] is False
    assert report["external_effect"] is None
    assert report["external_effect_unknown"] is True
    assert report["post_merge"]["decision"] == "post_merge_not_confirmed"
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
    assert report["merge_executed"] is None
    assert report["dry_run"] is False
    assert report["external_effect"] is None
    assert report["external_effect_unknown"] is True
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
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True
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


@pytest.mark.parametrize(
    "now_utc",
    [False, 0, 7, "2026-06-14T06:20:00Z", datetime(2026, 6, 14)],
)
def test_now_utc_requires_timezone_aware_datetime_before_effects(
    tmp_path: Path,
    now_utc: object,
) -> None:
    calls, runner = _runner()

    with pytest.raises(ValueError, match="now_utc"):
        merge_with_bridge_receipt(
            pr_number=1174,
            repo="example/repo",
            events_path=_events_path(tmp_path, _full_consensus()),
            out_dir=tmp_path / "out",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            apply=True,
            now_utc=now_utc,  # type: ignore[arg-type]
            runner=runner,
        )

    assert calls == []
    assert not (tmp_path / "out").exists()


def test_apply_rejects_historical_now_even_with_full_historical_quota(
    tmp_path: Path,
) -> None:
    calls, runner = _runner()
    historical = NOW.replace(day=13)
    quota_events = [
        {
            "ts_utc": f"2026-06-14T0{index}:00:00Z",
            "agent": "codex-lead-1",
            "type": "done",
            "status": "idle_auto_merge_done",
            "task_id": f"merged-{index}",
            "payload": {"auto_merged": True},
        }
        for index in range(1, 6)
    ]

    with pytest.raises(ValueError, match="current UTC date"):
        merge_with_bridge_receipt(
            pr_number=1174,
            repo="example/repo",
            events_path=_events_path(
                tmp_path,
                [*_full_consensus(), *quota_events],
            ),
            out_dir=tmp_path / "out",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            apply=True,
            now_utc=historical,
            runner=runner,
        )

    assert calls == []
    assert not _merge_calls(calls)


def test_apply_receipt_uses_trusted_clock_not_same_day_override(
    tmp_path: Path,
) -> None:
    calls, runner = _runner()
    supplied = NOW.replace(hour=0, minute=1)

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
        now_utc=supplied,
        runner=runner,
    )

    assert report["merge_executed"] is True
    payload = json.loads(
        (tmp_path / "out" / "receipt" / "payload-001-merge.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["created_at_utc"] == "2026-06-14T06:20:00Z"
    assert len(_merge_calls(calls)) == 1


@pytest.mark.parametrize("returncode", [None, False, "0", 0.0])
def test_merge_result_requires_exact_integer_returncode(
    tmp_path: Path,
    returncode: object,
) -> None:
    calls, runner = _runner(
        merge_returncode=returncode,  # type: ignore[arg-type]
        post_state="OPEN",
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

    assert report["decision"] == "invalid_merge_result"
    assert report["gh_merge_attempted"] is True
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True
    assert report["post_merge"]["decision"] == "post_merge_not_confirmed"
    assert len(_merge_calls(calls)) == 1


@pytest.mark.parametrize("returncode", [None, False, "0", 0.0])
def test_post_merge_query_requires_exact_integer_returncode(
    tmp_path: Path,
    returncode: object,
) -> None:
    calls, delegate = _runner()

    def runner(command: Sequence[str]) -> SimpleNamespace:
        result = delegate(command)
        if (
            list(command)[:3] == ["gh", "pr", "view"]
            and "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
            in command
        ):
            result.returncode = returncode
        return result

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
    assert report["post_merge"]["decision"] == "post_merge_query_invalid"
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True


@pytest.mark.parametrize("merge_returncode", [None, 1])
def test_nonstandard_merge_result_recovers_confirmed_exact_merge(
    tmp_path: Path,
    merge_returncode: object,
) -> None:
    calls, runner = _runner(
        merge_returncode=merge_returncode,  # type: ignore[arg-type]
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

    assert report["decision"] == "merge_executed_after_receipt"
    assert report["merge_executed"] is True
    assert report["dry_run"] is False
    assert report["external_effect"] is True
    assert report["external_effect_unknown"] is False
    assert report["post_merge"]["decision"] == "post_merge_confirmed"
    assert report["merge_commit_sha"] == MERGE_SHA


def test_merge_runner_exception_after_dispatch_is_indeterminate(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner(post_state="OPEN")

    def runner(command: Sequence[str]) -> SimpleNamespace:
        result = delegate(command)
        if list(command)[:3] == ["gh", "pr", "merge"]:
            raise RuntimeError("PRIVATE_MARKER")
        return result

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

    assert report["decision"] == "gh_merge_runner_exception"
    assert report["gh_merge_attempted"] is True
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True
    assert report["post_merge"]["decision"] == "post_merge_not_confirmed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_merge_returncode_property_exception_is_indeterminate(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner(post_state="OPEN")

    class RaisingReturncode:
        stdout = ""

        @property
        def returncode(self):
            raise RuntimeError("PRIVATE_MARKER")

    def runner(command: Sequence[str]) -> SimpleNamespace:
        result = delegate(command)
        if list(command)[:3] == ["gh", "pr", "merge"]:
            return RaisingReturncode()  # type: ignore[return-value]
        return result

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

    assert report["decision"] == "gh_merge_runner_exception"
    assert report["post_merge"]["decision"] == "post_merge_not_confirmed"
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_merge_runner_exception_recovers_confirmed_exact_merge(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner()

    def runner(command: Sequence[str]) -> SimpleNamespace:
        result = delegate(command)
        if list(command)[:3] == ["gh", "pr", "merge"]:
            raise RuntimeError("timeout")
        return result

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
    assert report["merge_executed"] is True
    assert report["external_effect_unknown"] is False
    assert report["post_merge"]["decision"] == "post_merge_confirmed"


def test_post_merge_query_runner_exception_is_controlled(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner()

    def runner(command: Sequence[str]) -> SimpleNamespace:
        if (
            list(command)[:3] == ["gh", "pr", "view"]
            and "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
            in command
        ):
            calls.append(list(command))
            raise RuntimeError("PRIVATE_MARKER")
        return delegate(command)

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
    assert report["post_merge"]["decision"] == "post_merge_query_exception"
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_post_merge_query_returncode_property_exception_is_controlled(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner()

    class RaisingReturncode:
        stdout = ""

        @property
        def returncode(self):
            raise RuntimeError("PRIVATE_MARKER")

    def runner(command: Sequence[str]) -> SimpleNamespace:
        if (
            list(command)[:3] == ["gh", "pr", "view"]
            and "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
            in command
        ):
            calls.append(list(command))
            return RaisingReturncode()  # type: ignore[return-value]
        return delegate(command)

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
    assert report["post_merge"]["decision"] == "post_merge_query_exception"
    assert report["merge_executed"] is None
    assert report["external_effect_unknown"] is True
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_post_merge_query_stdout_property_exception_is_controlled(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner()

    class RaisingStdout:
        returncode = 0

        @property
        def stdout(self):
            raise RuntimeError("PRIVATE_MARKER")

    def runner(command: Sequence[str]) -> SimpleNamespace:
        if (
            list(command)[:3] == ["gh", "pr", "view"]
            and "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
            in command
        ):
            calls.append(list(command))
            return RaisingStdout()  # type: ignore[return-value]
        return delegate(command)

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

    serialized = json.dumps(report, sort_keys=True)
    assert report["decision"] == "post_merge_state_unconfirmed"
    assert report["post_merge"]["decision"] == "post_merge_query_invalid"
    assert report["merge_executed"] is None
    assert report["dry_run"] is False
    assert report["external_effect"] is None
    assert report["external_effect_unknown"] is True
    assert "PRIVATE_MARKER" not in serialized


def test_post_merge_query_deep_json_is_controlled(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner()
    deep_json = ("[" * 5000) + "0" + ("]" * 5000)

    def runner(command: Sequence[str]) -> SimpleNamespace:
        if (
            list(command)[:3] == ["gh", "pr", "view"]
            and "number,state,mergeCommit,headRefOid,headRefName,baseRefName"
            in command
        ):
            calls.append(list(command))
            return SimpleNamespace(returncode=0, stdout=deep_json)
        return delegate(command)

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
    assert report["post_merge"]["decision"] == "post_merge_query_invalid"
    assert report["merge_executed"] is None
    assert report["dry_run"] is False
    assert report["external_effect"] is None
    assert report["external_effect_unknown"] is True


def test_falsey_callable_runner_is_used_without_live_fallback(
    tmp_path: Path,
) -> None:
    calls, delegate = _runner()

    class FalseyRunner:
        def __bool__(self) -> bool:
            return False

        def __call__(self, command: Sequence[str]) -> SimpleNamespace:
            return delegate(command)

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
        apply=False,
        now_utc=NOW,
        runner=FalseyRunner(),
    )

    assert report["decision"] == "merge_receipt_ready"
    assert calls


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
