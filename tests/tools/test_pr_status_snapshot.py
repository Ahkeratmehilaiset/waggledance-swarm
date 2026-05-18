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


def _gh_payload(**overrides) -> dict:
    payload = {
        "number": 479,
        "title": "feat(idle): add dry-run auto-merge gate",
        "headRefOid": HEAD,
        "headRefName": "codex/idle-consensus-auto-merge-v1-20260518",
        "mergeable": "MERGEABLE",
        "isDraft": False,
        "url": "https://github.example/pr/479",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [
            {"name": "test (3.13)", "state": "SUCCESS"},
            {"name": "unified", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    }
    payload.update(overrides)
    return payload


def test_snapshot_uses_structured_gh_json_fields() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload()))

    snapshot = build_pr_status_snapshot(
        pr_number=479,
        repo="Ahkeratmehilaiset/waggledance-swarm",
        operator_approved=True,
        receipt_verified=True,
        runner=runner,
    )
    assert calls == [
        [
            "gh",
            "pr",
            "view",
            "479",
            "--json",
            GH_JSON_FIELDS,
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ]
    ]
    assert snapshot["pr_number"] == 479
    assert snapshot["head_sha"] == HEAD
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


def test_gh_failure_does_not_echo_stderr() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_view_failed"
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


def test_cli_writes_snapshot_file(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    out_path = tmp_path / "pr-status.json"

    def fake_run(command: list[str]) -> SimpleNamespace:
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
