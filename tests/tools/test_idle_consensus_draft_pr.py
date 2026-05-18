from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.idle_consensus_draft_pr import DraftPrPlanError, build_draft_pr_plan


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "idle_protocol"


def _fixture_payloads(name: str) -> list[dict]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return list(data["events"])


def _write_events(tmp_path: Path, payloads: list[dict]) -> Path:
    events_path = tmp_path / "events.jsonl"
    lines = [
        json.dumps(
            {
                "ts_utc": f"2026-05-18T00:00:{index:02d}Z",
                "agent": "codex",
                "type": "message",
                "status": payload["event_type"],
                "task_id": f"idle-draft-pr-{index}",
                "payload": payload,
            },
            sort_keys=True,
        )
        for index, payload in enumerate(payloads, 1)
    ]
    events_path.write_text("\n".join(lines), encoding="utf-8")
    return events_path


def test_eligible_gate_builds_draft_pr_plan_without_effect(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    report = build_draft_pr_plan(
        events_path=events_path,
        changed_paths=["tools/idle_daily_summary.py"],
        diff_text="+ pass\n",
        utc_date="2026-05-18",
        head="codex/idle-consensus-example-branch",
        repo="Ahkeratmehilaiset/waggledance-swarm",
        artifact_path="docs/architecture/consensus_artifacts/example.json",
        receipt_manifest="docs/architecture/consensus_artifacts/receipt/manifest.json",
    )
    assert report["decision"] == "draft_pr_plan_ready"
    assert report["dry_run"] is True
    assert report["external_effect"] is False
    assert report["would_create_pr"] is True
    assert report["operator_review_required"] is True
    assert report["would_merge"] is False
    assert report["auto_execute"] is False
    command = report["draft_pr"]["gh_command"]
    assert command[:4] == ["gh", "pr", "create", "--draft"]
    assert "merge" not in command
    assert "--head" in command
    assert report["draft_pr"]["body"].count("required before merge") == 0
    assert "## Gate Report" in report["draft_pr"]["body"]
    assert "## Transcript" in report["draft_pr"]["body"]
    assert "idle-soft-001" in report["draft_pr"]["body"]
    assert report["draft_pr"]["consensus_artifact_path"] == (
        "docs/architecture/consensus_artifacts/example.json"
    )
    assert report["draft_pr"]["artifact_receipt_path"] == (
        "docs/architecture/consensus_artifacts/receipt/manifest.json"
    )


def test_gate_denial_does_not_build_draft_pr_command(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    report = build_draft_pr_plan(
        events_path=events_path,
        changed_paths=["CLAUDE.md"],
        diff_text="+ docs\n",
        utc_date="2026-05-18",
        head="codex/idle-consensus-example-branch",
    )
    assert report["decision"] == "operator_review_required"
    assert report["operator_review_required"] is True
    assert report["would_create_pr"] is False
    assert "draft_pr" not in report


def test_apply_requires_existing_head_ref(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    with pytest.raises(DraftPrPlanError) as excinfo:
        build_draft_pr_plan(
            events_path=events_path,
            changed_paths=["tools/idle_daily_summary.py"],
            diff_text="+ pass\n",
            utc_date="2026-05-18",
            apply=True,
        )
    assert excinfo.value.report["decision"] == "missing_head_ref"
    assert excinfo.value.report["external_effect"] is False


def test_apply_invokes_runner_once_for_draft_pr(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="https://example.test/pull/1\n")

    report = build_draft_pr_plan(
        events_path=events_path,
        changed_paths=["tools/idle_daily_summary.py"],
        diff_text="+ pass\n",
        utc_date="2026-05-18",
        head="codex/idle-consensus-example-branch",
        apply=True,
        runner=fake_runner,
    )
    assert len(calls) == 1
    assert calls[0][:4] == ["gh", "pr", "create", "--draft"]
    assert "merge" not in calls[0]
    assert report["decision"] == "draft_pr_created"
    assert report["external_effect"] is True
    assert report["operator_review_required"] is True
    assert report["created_pr_url"] == "https://example.test/pull/1"
    assert report["would_merge"] is False


def test_apply_refuses_when_gate_not_eligible(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="unexpected")

    report = build_draft_pr_plan(
        events_path=events_path,
        changed_paths=["CLAUDE.md"],
        diff_text="+ docs\n",
        utc_date="2026-05-18",
        head="codex/idle-consensus-example-branch",
        apply=True,
        runner=fake_runner,
    )
    assert calls == []
    assert report["decision"] == "operator_review_required"
    assert report["external_effect"] is False
    assert report["would_create_pr"] is False


def test_private_marker_in_body_is_refused_before_runner(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    with pytest.raises(DraftPrPlanError) as excinfo:
        build_draft_pr_plan(
            events_path=events_path,
            changed_paths=["tools/idle_daily_summary.py"],
            diff_text="+ pass\n",
            utc_date="2026-05-18",
            head="codex/idle-consensus-example-branch",
            body="PRIVATE_MARKER",
        )
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_runner_failure_fails_closed_without_stderr_echo(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))

    def fake_runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")

    with pytest.raises(DraftPrPlanError) as excinfo:
        build_draft_pr_plan(
            events_path=events_path,
            changed_paths=["tools/idle_daily_summary.py"],
            diff_text="+ pass\n",
            utc_date="2026-05-18",
            head="codex/idle-consensus-example-branch",
            apply=True,
            runner=fake_runner,
        )
    report = excinfo.value.report
    assert report["decision"] == "draft_pr_create_failed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])
    assert report["external_effect"] is False


def test_arbitrary_head_namespace_is_refused(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    with pytest.raises(DraftPrPlanError) as excinfo:
        build_draft_pr_plan(
            events_path=events_path,
            changed_paths=["tools/idle_daily_summary.py"],
            diff_text="+ pass\n",
            utc_date="2026-05-18",
            head="codex/random-branch",
        )
    assert excinfo.value.report["decision"] == "invalid_head_ref"
