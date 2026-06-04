from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.report_open_pr_stale_base_queue import (
    GH_PR_LIST_FIELDS,
    OpenPrStaleBaseReportError,
    build_open_pr_stale_base_report,
    main,
)


BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_BASE = "fedcba9876543210fedcba9876543210fedcba98"
HEAD_ONE = "1234567890abcdef1234567890abcdef12345678"
HEAD_TWO = "234567890abcdef1234567890abcdef123456789"


def _pr_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "number": 879,
        "title": "add latency producer",
        "headRefName": "codex-tools-1/future-scale-latency-producer",
        "headRefOid": HEAD_ONE,
        "baseRefOid": BASE,
        "isDraft": True,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "",
        "url": "https://github.example/pr/879",
        "statusCheckRollup": [
            {"name": "test", "state": "SUCCESS"},
            {"name": "unified", "status": "IN_PROGRESS"},
            {"name": "security", "conclusion": "FAILURE"},
            {"name": "lock"},
        ],
    }
    payload.update(overrides)
    return payload


def _runner(payload: object) -> tuple[list[list[str]], object]:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    return calls, runner


def test_report_partitions_current_and_stale_open_prs() -> None:
    payload = [
        _pr_payload(number=879, baseRefOid=BASE, headRefOid=HEAD_ONE),
        _pr_payload(number=870, baseRefOid=OTHER_BASE, headRefOid=HEAD_TWO),
    ]
    calls, runner = _runner(payload)

    report = build_open_pr_stale_base_report(
        expected_base_sha=BASE,
        repo="Ahkeratmehilaiset/waggledance-swarm",
        limit=20,
        runner=runner,
    )

    assert calls == [
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            GH_PR_LIST_FIELDS,
            "--limit",
            "20",
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ]
    ]
    assert report["decision"] == "stale_base_refs_present"
    assert report["queue_clear"] is False
    assert report["open_pr_count"] == 2
    assert report["current_base_count"] == 1
    assert report["stale_base_count"] == 1
    assert report["stale_pr_numbers"] == [870]
    assert report["stale_prs"][0]["base_status"] == "stale"
    assert report["prs"][0]["check_summary"] == {
        "total": 4,
        "success": 1,
        "pending": 1,
        "failure": 1,
        "other": 1,
    }


def test_report_marks_empty_or_all_current_queue_clear() -> None:
    calls, runner = _runner([_pr_payload(baseRefOid=BASE)])

    report = build_open_pr_stale_base_report(
        expected_base_sha=BASE,
        runner=runner,
    )

    assert len(calls) == 1
    assert report["decision"] == "all_open_prs_current_base"
    assert report["queue_clear"] is True
    assert report["stale_base_count"] == 0


def test_invalid_expected_base_refused_before_gh_call() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        raise AssertionError(f"runner should not be called: {command}")

    with pytest.raises(OpenPrStaleBaseReportError) as excinfo:
        build_open_pr_stale_base_report(
            expected_base_sha="abc123",
            runner=runner,
        )
    assert excinfo.value.report["decision"] == "invalid_expected_base_sha"


def test_invalid_repo_refused_before_gh_call() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        raise AssertionError(f"runner should not be called: {command}")

    with pytest.raises(OpenPrStaleBaseReportError) as excinfo:
        build_open_pr_stale_base_report(
            expected_base_sha=BASE,
            repo="../bad",
            runner=runner,
        )
    assert excinfo.value.report["decision"] == "invalid_repo"


def test_gh_failure_does_not_echo_stderr() -> None:
    guarded_marker = "PRIVATE_" + "MARKER"

    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout="", stderr=guarded_marker)

    with pytest.raises(OpenPrStaleBaseReportError) as excinfo:
        build_open_pr_stale_base_report(expected_base_sha=BASE, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_list_failed"
    assert guarded_marker not in " ".join(report["errors"])


def test_invalid_json_refused_without_raw_echo() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="not json")

    with pytest.raises(OpenPrStaleBaseReportError) as excinfo:
        build_open_pr_stale_base_report(expected_base_sha=BASE, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "invalid_gh_json"
    assert "not json" not in " ".join(report["errors"])


def test_private_marker_in_structured_json_is_refused() -> None:
    guarded_marker = "PRIVATE_" + "MARKER"
    calls, runner = _runner([_pr_payload(title=guarded_marker)])

    with pytest.raises(OpenPrStaleBaseReportError) as excinfo:
        build_open_pr_stale_base_report(expected_base_sha=BASE, runner=runner)
    assert len(calls) == 1
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_invalid_pr_shape_is_refused() -> None:
    calls, runner = _runner([_pr_payload(number="879")])

    with pytest.raises(OpenPrStaleBaseReportError) as excinfo:
        build_open_pr_stale_base_report(expected_base_sha=BASE, runner=runner)
    assert len(calls) == 1
    assert excinfo.value.report["decision"] == "invalid_gh_json"


def test_cli_json_can_fail_on_stale(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [_pr_payload(number=870, baseRefOid=OTHER_BASE)]

    def fake_build_open_pr_stale_base_report(**kwargs: object) -> dict[str, object]:
        assert kwargs["expected_base_sha"] == BASE
        return {
            "decision": "stale_base_refs_present",
            "ok": True,
            "queue_clear": False,
            "expected_base_sha": BASE,
            "open_pr_count": 1,
            "current_base_count": 0,
            "stale_base_count": 1,
            "stale_pr_numbers": [870],
            "prs": payload,
            "stale_prs": payload,
        }

    monkeypatch.setattr(
        "tools.report_open_pr_stale_base_queue.build_open_pr_stale_base_report",
        fake_build_open_pr_stale_base_report,
    )
    exit_code = main(["--expected-base-sha", BASE, "--fail-on-stale", "--json"])
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert out["exit_code"] == 1
    assert out["stale_base_count"] == 1
