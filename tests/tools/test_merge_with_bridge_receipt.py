# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


@pytest.mark.parametrize("failure", [None, "expired_at_apply", "ci", "veto", "missing_tools"])
def test_operator_exception_preserves_real_gates_and_apply_recheck(tmp_path, monkeypatch, failure):
    from waggledance.core.magma.canonical import sha256_digest
    path = "ops/windows/reboot/wd_supervisor.ps1"
    diff = f"diff --git a/{path} b/{path}\n"
    calls, original_runner = _runner()
    events = _full_consensus()
    events[0]["write_scope"] = [path]
    if failure == "veto":
        events.append(_event("claude-rco-1", "changes_requested", "2026-06-14T06:13:00Z"))
    if failure == "missing_tools":
        events = [e for e in events if e["agent"] != "codex-tools-1"]
    grant = dict(schema="wd.operator-path-exception.v1", repo="example/repo",
                 pr_number=1174, head=HEAD, base=BASE, diff_digest=sha256_digest(diff),
                 paths=[path], approval_reference="operator-test-instruction",
                 issued_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=1)).isoformat())

    def runner(command):
        result = original_runner(command)
        if command[:3] == ["gh", "pr", "diff"]:
            result.stdout = diff
        elif command[:2] == ["gh", "api"] and "/git/ref/heads/" not in command[4]:
            result.stdout = json.dumps([dict(filename=path, status="modified")])
        elif command[:3] == ["gh", "pr", "view"] and failure == "ci":
            payload = json.loads(result.stdout)
            if "statusCheckRollup" in payload:
                payload["statusCheckRollup"][0]["conclusion"] = "FAILURE"
                result.stdout = json.dumps(payload)
        return result

    ticks = iter([NOW, NOW + timedelta(minutes=1)])
    if failure == "expired_at_apply":
        monkeypatch.setattr(merge_tool, "_utc_now", lambda: next(ticks))
    kwargs = dict(pr_number=1174, repo="example/repo",
                  events_path=_events_path(tmp_path, events), out_dir=tmp_path / "out",
                  expected_head=HEAD, expected_base_sha=BASE, consensus_proposal_id=TASK,
                  from_agent="codex-lead-1", bridge_task_id=TASK, apply=True,
                  now_utc=NOW, runner=runner, operator_path_exception=grant)
    if failure:
        with pytest.raises(ValueError):
            merge_with_bridge_receipt(**kwargs)
        assert not _merge_calls(calls)
    else:
        report = merge_with_bridge_receipt(**kwargs)
        assert report["ok"] is True
        assert report["merge_executed"] is True
        assert len(_merge_calls(calls)) == 1
        assert report["fresh_gate"]["path_gate"]["allowed"] is False


@pytest.fixture(autouse=True)
def _trusted_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_tool, "_utc_now", lambda: NOW)


def _main_args(tmp_path: Path) -> list[str]:
    return [
        "1174",
        "--repo",
        "example/repo",
        "--out-dir",
        str(tmp_path / "out"),
        "--expected-head",
        HEAD,
        "--expected-base-sha",
        BASE,
        "--consensus-proposal-id",
        TASK,
        "--json",
    ]


def test_cli_default_events_uses_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    captured: dict[str, Path] = {}

    def fake_merge(**kwargs):
        captured["events_path"] = kwargs["events_path"]
        return {"ok": True, "decision": "merge_receipt_ready", "exit_code": 0}

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))
    monkeypatch.setattr(merge_tool, "merge_with_bridge_receipt", fake_merge)

    exit_code = merge_tool.main(_main_args(tmp_path))

    assert exit_code == 0
    assert captured["events_path"] == bridge_root / "shared" / "events.jsonl"
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_mismatched_events_fails_before_merge_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    outside = tmp_path / "snapshot" / "events.jsonl"
    outside.parent.mkdir()
    outside.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        merge_tool,
        "merge_with_bridge_receipt",
        lambda **kwargs: pytest.fail("merge runner must not be called"),
    )

    exit_code = merge_tool.main(
        _main_args(tmp_path)
        + ["--bridge-root", str(bridge_root), "--events", str(outside)]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "invalid_input"
    assert report["gh_merge_attempted"] is False


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


# --- Rule 14a: the receipt binds the lane tuple; a changed fresh gate never merges ---
MIND_FILES = ["sandbox/mind/reader.py", "tools/wd_mind_reader.py"]


def _mind_task(author: str) -> str:
    return f"{author}/mind-lane-receipt-test"


def _mind_event(agent: str, status: str, minute: int, author: str, type_: str = "decision") -> dict[str, object]:
    from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
    event = _event("codex-lead-1", status, f"2026-06-14T06:{minute:02d}:00Z")
    event.update(agent=agent, agent_uuid=load_bridge_identity_registry()[agent], type=type_, task_id=_mind_task(author))
    if type_ == "claim":
        event["write_scope"] = ["*"]
    return event


def _mind_runner(
    paths: list[str], author: str, *, events_file: Path | None = None, late: tuple = (),
    line: str = "", line_path: str = "",
):
    """gh stub for a Mind-class PR; ``late`` events land between the receipt and the fresh gate. ``line`` is one
    more added line in the file ``line_path`` of the diff; the hunk header counts it."""
    payload = _gh_payload(headRefName=_mind_task(author), changedFiles=len(paths))
    calls, inner = _runner(payload=payload, post_head_ref=_mind_task(author))
    views = 0
    diff = "".join(
        f"diff --git a/{p} b/{p}\nindex 1111111..2222222 100644\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n"
        for p in paths
    )
    if line:
        clean, head = diff, f"+++ b/{line_path}\n"
        diff = clean.replace(head + "@@ -1 +1 @@\n-old\n+new\n", head + "@@ -1 +1,2 @@\n-old\n+new\n+" + line + "\n")
        assert diff != clean

    def run(command: Sequence[str]) -> SimpleNamespace:
        nonlocal views
        call = list(command)
        if call[:3] == ["gh", "pr", "view"] and not any("mergeCommit" in part for part in call):
            views += 1
            if views == 3 and events_file is not None:      # first read of the apply recheck
                with events_file.open("a", encoding="utf-8") as handle:
                    handle.writelines("\n" + json.dumps(event, sort_keys=True) for event in late)
        if call[:2] == ["gh", "api"] and "/git/ref/heads/" not in call[4]:
            calls.append(call)
            records = [{"filename": path, "status": "modified"} for path in paths]
            return SimpleNamespace(returncode=0, stdout=json.dumps(records), stderr="")
        if call[:3] == ["gh", "pr", "diff"]:
            calls.append(call)
            return SimpleNamespace(returncode=0, stdout=diff)
        return inner(command)

    return calls, run


def _mind_merge(tmp_path: Path, name: str, events_file: Path, runner, author: str, *, apply: bool = True):
    return merge_with_bridge_receipt(
        pr_number=1174, repo="example/repo", events_path=events_file, out_dir=tmp_path / name,
        expected_head=HEAD, expected_base_sha=BASE, consensus_proposal_id=_mind_task(author),
        from_agent="codex-lead-1", bridge_task_id=_mind_task(author), apply=apply, now_utc=NOW, runner=runner,
    )


def _receipt_payload(report: dict) -> dict:
    manifest = Path(report["receipt_bundle_path"])
    return json.loads((manifest.parent / "payload-001-merge.json").read_text(encoding="utf-8"))


def _mind_votes(author: str, *voters: tuple[str, str]) -> list[dict[str, object]]:
    rows = [_mind_event(author, "active", 9, author, type_="claim")]
    rows.extend(_mind_event(agent, status, 10 + i, author) for i, (agent, status) in enumerate(voters))
    return rows


def test_mind_lane_merges_when_the_fresh_gate_reproduces_the_receipt_tuple(tmp_path: Path) -> None:
    author = "codex-tools-1"
    events = _events_path(tmp_path, _mind_votes(author, ("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass")))
    calls, runner = _mind_runner(MIND_FILES, author)
    report = _mind_merge(tmp_path, "out", events, runner, author)
    assert report["decision"] == "merge_executed_after_receipt", report
    assert len(_merge_calls(calls)) == 1
    payload = _receipt_payload(report)
    assert payload["consensus_lane"] == "rule14a_mind_class"
    assert [row["agent"] for row in payload["mind_class_lane_evidence"]["required_identities"]] == [
        "codex-lead-1", "claude-rco-1",
    ]


def test_mind_lane_new_rco_pass_after_the_receipt_refuses_merge_and_the_old_receipt(tmp_path: Path) -> None:
    # T1: the receipt binds Lead + RCO1. T2: an RCO2 pass lands before the fresh gate, which
    # would now pass on Lead + RCO2. Both approvals stand, but the receipt would describe
    # another decision, so nothing is merged with it.
    author = "codex-tools-1"
    votes = _mind_votes(author, ("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass"))
    late = _mind_event("claude-rco-2", "rco_pass", 15, author)
    events = _events_path(tmp_path, votes)
    calls, runner = _mind_runner(MIND_FILES, author, events_file=events, late=(late,))
    report = _mind_merge(tmp_path, "out", events, runner, author)
    assert report["decision"] == "apply_gate_recheck_failed", report
    assert report["ok"] is False and report["stage"] == "apply_recheck"
    assert report["errors"] == ["fresh gate Rule 14a Mind-class lane evidence differs from the receipt"]
    assert _merge_calls(calls) == []                                 # GitHub was never asked to merge
    fresh = report["fresh_gate"]
    assert fresh["ok"] is True                                       # the fresh gate alone would pass
    old, new = report["receipt_mind_class_lane_evidence"], fresh["mind_class_lane_evidence"]
    assert old["rco_pass_ref"]["agent"] == "claude-rco-1"
    assert new["rco_pass_ref"]["agent"] == "claude-rco-2"
    assert old["approver_ref"] == new["approver_ref"]
    assert _receipt_payload(report)["mind_class_lane_evidence"] == old
    # A new run writes its own receipt for the decision that now holds, and merges on that one.
    calls_again, runner_again = _mind_runner(MIND_FILES, author)
    again = _mind_merge(tmp_path, "again", events, runner_again, author)
    assert again["decision"] == "merge_executed_after_receipt", again
    assert len(_merge_calls(calls_again)) == 1
    assert again["receipt_bundle_path"] != report["receipt_bundle_path"]
    assert _receipt_payload(again)["mind_class_lane_evidence"] == new


def test_three_identity_receipt_is_not_used_once_the_mind_lane_engages(tmp_path: Path) -> None:
    # T1: Lead-authored, Tools + RCO1 on an allowlisted Mind path: the unchanged three-identity
    # verdict. T2: a pool reviewer's approval engages the Rule 14a lane. The lane changed, so
    # the three-identity receipt is not used for the merge.
    author = "codex-lead-1"
    votes = _mind_votes(author, ("codex-tools-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass"))
    late = _mind_event("fable-5", "build_consensus_pass", 15, author)
    events = _events_path(tmp_path, votes)
    calls, runner = _mind_runner(MIND_FILES[1:], author, events_file=events, late=(late,))
    report = _mind_merge(tmp_path, "out", events, runner, author)
    assert report["decision"] == "apply_gate_recheck_failed", report
    assert _merge_calls(calls) == []
    assert report["receipt_mind_class_lane_evidence"] is None
    assert "consensus_lane" not in _receipt_payload(report)
    assert report["fresh_gate"]["mind_class_lane_evidence"]["approver_ref"]["agent"] == "fable-5"


def test_mind_lane_receipt_is_not_used_once_the_lane_no_longer_holds(tmp_path: Path) -> None:
    # T1: the lane holds (Lead + RCO1). T2: Lead blocks. Nothing is merged.
    author = "codex-tools-1"
    votes = _mind_votes(author, ("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass"))
    late = _mind_event("codex-lead-1", "changes_requested", 15, author)
    events = _events_path(tmp_path, votes)
    calls, runner = _mind_runner(MIND_FILES, author, events_file=events, late=(late,))
    report = _mind_merge(tmp_path, "out", events, runner, author)
    assert report["decision"] == "apply_gate_recheck_failed", report
    assert _merge_calls(calls) == []


def test_unrelated_late_event_does_not_disturb_a_mind_lane_merge(tmp_path: Path) -> None:
    author = "codex-tools-1"
    votes = _mind_votes(author, ("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass"))
    late = _mind_event("codex-lead-1", "info", 15, author, type_="message")
    events = _events_path(tmp_path, votes)
    calls, runner = _mind_runner(MIND_FILES, author, events_file=events, late=(late,))
    report = _mind_merge(tmp_path, "out", events, runner, author)
    assert report["decision"] == "merge_executed_after_receipt", report
    assert len(_merge_calls(calls)) == 1


_MERGE_CLAIM_KEYS = {"merge_commit_sha", "post_merge", "merge_command_result", "external_effect"}


@pytest.mark.parametrize(
    ("author", "voters", "files", "late"),
    [
        # the reviewer lane takes its own approval back
        ("codex-tools-1", (("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass")), MIND_FILES,
         ("claude-rco-1", "changes_requested", "finding")),
        # the other recognized reviewer lane objects
        ("codex-tools-1", (("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass")), MIND_FILES,
         ("claude-rco-2", "changes_requested", "finding")),
        # the pool reviewer standing in for Lead takes its approval back, with a Tools approval present
        ("codex-lead-1", (("fable-5", "build_consensus_pass"), ("claude-rco-1", "rco_pass"),
                          ("codex-tools-1", "build_consensus_pass")), MIND_FILES[1:],
         ("fable-5", "changes_requested", "decision")),
        # a build lane the two-identity lane does not need objects
        ("codex-lead-1", (("fable-5", "build_consensus_pass"), ("claude-rco-1", "rco_pass")), MIND_FILES,
         ("codex-tools-1", "changes_requested", "decision")),
    ],
)
def test_mind_lane_approval_taken_back_after_the_receipt_never_merges(
    tmp_path: Path, author: str, voters: tuple, files: list[str], late: tuple[str, str, str],
) -> None:
    events = _events_path(tmp_path, _mind_votes(author, *voters))
    late_event = _mind_event(late[0], late[1], 15, author, type_=late[2])
    calls, runner = _mind_runner(files, author, events_file=events, late=(late_event,))
    report = _mind_merge(tmp_path, "out", events, runner, author)
    assert report["decision"] == "apply_gate_recheck_failed", report
    assert report["fresh_gate"]["ok"] is False
    assert _merge_calls(calls) == []
    assert report["ok"] is False and report["merge_executed"] is False and report["gh_merge_attempted"] is False
    assert not _MERGE_CLAIM_KEYS & set(report)
    # the receipt written at T1 named the lane; it stays on disk as the record of that evaluation only
    assert _receipt_payload(report)["consensus_lane"] == "rule14a_mind_class"


@pytest.mark.parametrize("path", MIND_FILES)
def test_mind_lane_denied_diff_content_never_reaches_a_merge(tmp_path: Path, path: str) -> None:
    """Tools build review P2, at executor level: valid Mind paths and both lane approvals, but the diff text
    carries a line the charter denies, in either file. GitHub is never asked to merge. The control with a harmless added line
    in the same two-line hunk merges, so the refusal comes from the content of the line."""
    author = "codex-tools-1"
    votes = _mind_votes(author, ("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass"))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    control_calls, control_runner = _mind_runner(MIND_FILES, author, line="    retry_policy.attempts=3", line_path=path)
    control = _mind_merge(tmp_path, "control", _events_path(tmp_path / "a", votes), control_runner, author)
    assert control["decision"] == "merge_executed_after_receipt", control
    assert len(_merge_calls(control_calls)) == 1

    calls, runner = _mind_runner(MIND_FILES, author, line="    retry_policy.gate_" + "skip=True", line_path=path)
    refused = _mind_merge(tmp_path, "denied", _events_path(tmp_path / "b", votes), runner, author)
    assert _merge_calls(calls) == []                                 # GitHub was never asked to merge
    assert refused["decision"] == "receipt_preflight_failed", refused
    assert refused["errors"] == ["diff gate failed: code pattern denylist hit"]
    assert refused["gh_merge_attempted"] is False
    assert not _MERGE_CLAIM_KEYS & set(refused)                      # the report claims no merge
    assert refused["merge_executed"] is False and refused["ok"] is False
    assert list((tmp_path / "control").rglob("payload-*.json"))      # the merged control wrote a receipt payload
    assert not list((tmp_path / "denied").rglob("payload-*.json"))   # the refused run wrote none
    assert not refused.get("receipt_bundle_path"), sorted(refused)   # and reports no receipt bundle


def test_mind_lane_receipt_of_a_refused_run_is_no_proof_of_a_merge(tmp_path: Path) -> None:
    """The receipt is written before the merge. It records one gate evaluation and nothing after it:
    whether a merge followed is stated only by the executor's report, with GitHub's confirmation."""
    from tools.verify_magma_receipt import verify_manifest

    author = "codex-tools-1"
    votes = _mind_votes(author, ("codex-lead-1", "build_consensus_pass"), ("claude-rco-1", "rco_pass"))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    refused_events = _events_path(tmp_path / "a", votes)
    late = _mind_event("claude-rco-2", "rco_pass", 15, author)
    calls, runner = _mind_runner(MIND_FILES, author, events_file=refused_events, late=(late,))
    refused = _mind_merge(tmp_path, "refused", refused_events, runner, author)
    merged_events = _events_path(tmp_path / "b", votes)
    merged_calls, merged_runner = _mind_runner(MIND_FILES, author)
    merged = _mind_merge(tmp_path, "merged", merged_events, merged_runner, author)

    assert refused["decision"] == "apply_gate_recheck_failed" and _merge_calls(calls) == []
    assert refused["merge_executed"] is False and not _MERGE_CLAIM_KEYS & set(refused)
    assert merged["decision"] == "merge_executed_after_receipt" and len(_merge_calls(merged_calls)) == 1
    assert merged["merge_executed"] is True and merged["post_merge"]["merged"] is True
    assert merged["merge_commit_sha"] == merged["post_merge"]["merge_commit_sha"]
    refused_payload, merged_payload = _receipt_payload(refused), _receipt_payload(merged)
    assert merged["fresh_gate"]["mind_class_lane_evidence"] == merged_payload["mind_class_lane_evidence"]
    assert refused["fresh_gate"]["mind_class_lane_evidence"] != refused_payload["mind_class_lane_evidence"]
    for payload in (refused_payload, merged_payload):
        assert payload["gate_decision"] == "auto_merge_plan_ready"      # a plan, written before the merge
        assert not {"merge_executed", *_MERGE_CLAIM_KEYS} & set(payload)
    # The bundle is whole either way, and nothing inside it tells the two runs apart: integrity
    # of a receipt is never evidence that its merge happened.
    assert verify_manifest(Path(refused["receipt_bundle_path"]))["ok"] is True
    assert set(refused_payload) == set(merged_payload)
    # The folder of the refused run cannot be reused: a new decision needs its own receipt.
    again_calls, again_runner = _mind_runner(MIND_FILES, author)
    with pytest.raises(ValueError, match="out_dir must not exist"):
        _mind_merge(tmp_path, "refused", refused_events, again_runner, author)
    assert _merge_calls(again_calls) == []
