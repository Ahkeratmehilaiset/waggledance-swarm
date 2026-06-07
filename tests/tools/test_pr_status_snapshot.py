from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import tools.pr_status_snapshot as snapshot_tool
from tools.pr_status_snapshot import (
    GH_JSON_FIELDS,
    PrStatusSnapshotError,
    build_pr_status_snapshot,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_BASE = "fedcba9876543210fedcba9876543210fedcba98"


def _gh_payload(**overrides) -> dict:
    payload = {
        "number": 479,
        "title": "feat(idle): add dry-run auto-merge gate",
        "headRefOid": HEAD,
        "headRefName": "codex/idle-consensus-auto-merge-v1-20260518",
        "baseRefOid": BASE,
        "mergeable": "MERGEABLE",
        "state": "OPEN",
        "isDraft": False,
        "url": "https://github.example/pr/479",
        "reviewDecision": "APPROVED",
        "files": [
            {"path": "tools/idle_daily_summary.py"},
            {"path": "tests/tools/test_idle_consensus_auto_merge.py"},
        ],
        "statusCheckRollup": [
            {"name": "test (3.13)", "state": "SUCCESS"},
            {"name": "unified", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    }
    payload.update(overrides)
    return payload


def _runner(
    payload: dict | None = None,
    diff_text: str = "+ def helper():\n",
    recheck_payload: dict | None = None,
) -> tuple[list[list[str]], object]:
    initial_payload = payload or _gh_payload()
    followup_payload = recheck_payload if recheck_payload is not None else initial_payload
    payloads = [initial_payload, followup_payload]
    view_call = {"index": 0}

    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout=diff_text)
        index = view_call["index"]
        payload_to_use = payloads[min(index, len(payloads) - 1)]
        view_call["index"] += 1
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload_to_use))

    return calls, runner


def test_snapshot_uses_structured_gh_json_fields() -> None:
    calls, runner = _runner()
    expected_json_fields = f"{GH_JSON_FIELDS},state"

    snapshot = build_pr_status_snapshot(
        pr_number=479,
        repo="Ahkeratmehilaiset/waggledance-swarm",
        operator_approved=True,
        receipt_verified=True,
        expected_base_sha=BASE,
        runner=runner,
    )
    assert calls == [
        [
            "gh",
            "pr",
            "view",
            "479",
            "--json",
            expected_json_fields,
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ],
        [
            "gh",
            "pr",
            "diff",
            "479",
            "--patch",
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ],
        [
            "gh",
            "pr",
            "view",
            "479",
            "--json",
            expected_json_fields,
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ],
    ]
    assert snapshot["pr_number"] == 479
    assert snapshot["head_sha"] == HEAD
    assert snapshot["base_sha"] == BASE
    assert snapshot["state"] == "OPEN"
    assert snapshot["operator_approved"] is True
    assert snapshot["receipt_verified"] is True
    assert snapshot["checks"] == [
        {
            "name": "test (3.13)",
            "state": "SUCCESS",
            "status": "",
            "conclusion": "",
        },
        {
            "name": "unified",
            "state": "",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        },
    ]
    assert snapshot["changed_paths"] == [
        "tools/idle_daily_summary.py",
        "tests/tools/test_idle_consensus_auto_merge.py",
    ]
    assert snapshot["diff_text"] == "+ def helper():\n"


def test_gh_failure_does_not_echo_stderr() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_view_failed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_diff_failure_does_not_echo_stderr() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload()))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_diff_failed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_invalid_json_refused_without_raw_echo() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="not json")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "invalid_gh_json"
    assert "not json" not in " ".join(report["errors"])


def test_private_marker_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_gh_payload(title="PRIVATE_MARKER")),
        )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_private_marker_in_diff_refused() -> None:
    calls, runner = _runner(diff_text="+ PRIVATE_MARKER\n")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert len(calls) == 2
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_pr_head_changed_during_snapshot_is_rejected() -> None:
    calls, runner = _runner(
        payload=_gh_payload(),
        recheck_payload=_gh_payload(headRefOid="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_diff_head_drift"
    assert len(calls) == 3


def test_pr_base_changed_during_snapshot_is_rejected() -> None:
    calls, runner = _runner(
        payload=_gh_payload(),
        recheck_payload=_gh_payload(baseRefOid=OTHER_BASE),
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_diff_base_drift"
    assert len(calls) == 3


def test_expected_base_mismatch_is_rejected() -> None:
    calls, runner = _runner(payload=_gh_payload(baseRefOid=BASE))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            expected_base_sha=OTHER_BASE,
            runner=runner,
        )
    report = excinfo.value.report
    assert report["decision"] == "stale_base_ref"
    assert len(calls) == 3


def test_invalid_expected_base_refused_before_gh_call() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        raise AssertionError(f"runner should not be called: {command}")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            expected_base_sha="abc123",
            runner=runner,
        )
    assert excinfo.value.report["decision"] == "invalid_expected_base_sha"


def test_missing_full_base_sha_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_gh_payload(baseRefOid="abc1234")),
        )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "invalid_base_sha"


def test_recheck_view_failure_is_reported() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout="+ def helper():\n")
        if command[:3] == ["gh", "pr", "view"] and len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload()))
        return SimpleNamespace(returncode=13, stdout="", stderr="fetch failed")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_view_recheck_failed"
    assert len(calls) == 3


def test_missing_full_head_sha_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload(headRefOid="abc1234")))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "invalid_head_sha"


def test_pr_number_mismatch_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload(number=480)))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "pr_number_mismatch"


def test_invalid_files_refused() -> None:
    calls, runner = _runner(payload=_gh_payload(files=[{"path": ""}]))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert len(calls) == 3
    assert excinfo.value.report["decision"] == "invalid_files"


def test_cli_writes_snapshot_file(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    out_path = tmp_path / "pr-status.json"

    def fake_run(command: list[str]) -> SimpleNamespace:
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout="+ def helper():\n")
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload()))

    monkeypatch.setattr(snapshot_tool, "_run_command", fake_run)
    exit_code = snapshot_tool.main(
        [
            "479",
            "--operator-approved",
            "--receipt-verified",
            "--out",
            str(out_path),
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "written"
    assert out_path.exists()
    snapshot = json.loads(out_path.read_text(encoding="utf-8"))
    assert snapshot["head_sha"] == HEAD
    assert snapshot["receipt_verified"] is True
    assert snapshot["changed_paths"] == [
        "tools/idle_daily_summary.py",
        "tests/tools/test_idle_consensus_auto_merge.py",
    ]
    assert snapshot["diff_text"] == "+ def helper():\n"
