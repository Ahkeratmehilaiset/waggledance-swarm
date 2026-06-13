from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.idle_consensus_to_pr import (
    ELIGIBLE_DECISION,
    OPERATOR_REVIEW_DECISION,
    ConsensusToPrGateError,
    evaluate_consensus_to_pr_gate,
    main,
)


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
                "task_id": f"idle-test-{index}",
                "payload": payload,
            },
            sort_keys=True,
        )
        for index, payload in enumerate(payloads, 1)
    ]
    events_path.write_text("\n".join(lines), encoding="utf-8")
    return events_path


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


def _write_events_with_extras(
    tmp_path: Path,
    payloads: list[dict],
    extras: list[dict],
) -> Path:
    events_path = _write_events(tmp_path, payloads)
    existing = events_path.read_text(encoding="utf-8")
    extra_lines = [json.dumps(event, sort_keys=True) for event in extras]
    events_path.write_text(
        existing + "\n" + "\n".join(extra_lines),
        encoding="utf-8",
    )
    return events_path


def test_soft_consensus_allowlisted_candidate_is_gate_eligible(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["tools/idle_daily_summary.py", "tests/unit/test_idle_daily_summary.py"],
        diff_text="+ def helper():\n+     return 1\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == ELIGIBLE_DECISION
    assert report["eligible"] is True
    assert report["dry_run"] is True
    assert report["would_create_pr"] is False
    assert report["would_merge"] is False
    assert report["external_effect"] is False
    assert report["convergence"]["status"] == "soft_convergence"
    assert report["rate_gate"] == {
        "allowed": True,
        "utc_date": "2026-05-18",
        "quota_used": 0,
        "quota_total": 5,
    }


def test_hard_consensus_allowlisted_candidate_is_gate_eligible(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("hard_convergence.json"))
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["tools/idle_protocol_session.py"],
        diff_text="+ def helper():\n+     return 1\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == ELIGIBLE_DECISION
    assert report["eligible"] is True
    assert report["convergence"]["status"] == "hard_convergence"
    assert report["would_create_pr"] is False


def test_denylisted_path_requires_operator_review(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["CLAUDE.md"],
        diff_text="+ documentation update\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == OPERATOR_REVIEW_DECISION
    assert report["eligible"] is False
    assert report["operator_review_required"] is True
    assert report["path_gate"]["blocked_paths"] == ["CLAUDE.md"]


def test_unmatched_path_requires_operator_review(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["random/off_allowlist.py"],
        diff_text="+ pass\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == OPERATOR_REVIEW_DECISION
    assert report["eligible"] is False
    assert report["path_gate"]["unmatched_paths"] == ["random/off_allowlist.py"]


def test_code_pattern_hit_requires_operator_review(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["tools/idle_protocol_activate.py"],
        diff_text="+ operator_gate_required=True\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == OPERATOR_REVIEW_DECISION
    assert report["eligible"] is False
    assert report["diff_gate"]["code_pattern_hits"]


def test_daily_rate_limit_refuses_gate_eligibility(tmp_path: Path) -> None:
    events_path = _write_events_with_extras(
        tmp_path,
        _fixture_payloads("soft_convergence.json"),
        [_auto_merge_event(index) for index in range(1, 6)],
    )
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["tools/idle_daily_summary.py"],
        diff_text="+ pass\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == "rate_limited"
    assert report["eligible"] is False
    assert report["operator_review_required"] is True
    assert report["rate_gate"] == {
        "allowed": False,
        "utc_date": "2026-05-18",
        "quota_used": 5,
        "quota_total": 5,
    }
    assert report["would_merge"] is False


def test_no_consensus_reports_no_consensus_without_effect(tmp_path: Path) -> None:
    payloads = _fixture_payloads("soft_convergence.json")[:2]
    events_path = _write_events(tmp_path, payloads)
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["tools/example.py"],
        diff_text="+ pass\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == "no_consensus"
    assert report["would_create_pr"] is False
    assert report["would_merge"] is False


def test_charter_violation_stays_operator_required(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("charter_violation.json"))
    report = evaluate_consensus_to_pr_gate(
        events_path=events_path,
        changed_paths=["tools/example.py"],
        diff_text="+ pass\n",
        utc_date="2026-05-18",
    )
    assert report["decision"] == "charter_violation"
    assert report["operator_review_required"] is True
    assert report["would_merge"] is False


def test_private_marker_in_diff_refused_before_gate(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    with pytest.raises(ConsensusToPrGateError) as exc_info:
        evaluate_consensus_to_pr_gate(
            events_path=events_path,
            changed_paths=["tools/example.py"],
            diff_text="+ PRIVATE_MARKER\n",
        )
    assert exc_info.value.report["decision"] == "privacy_marker_refused"


def test_invalid_event_json_refused_without_raw_echo(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("not valid json\n", encoding="utf-8")
    with pytest.raises(ConsensusToPrGateError) as exc_info:
        evaluate_consensus_to_pr_gate(
            events_path=events_path,
            changed_paths=["tools/example.py"],
            diff_text="+ pass\n",
        )
    report = exc_info.value.report
    assert report["decision"] == "invalid_events"
    assert "not valid json" not in " ".join(report["errors"])


def test_cli_defaults_events_to_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_events = _write_events(tmp_path, _fixture_payloads("soft_convergence.json"))
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    runtime_events.write_text(source_events.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))

    exit_code = main(
        [
            "--changed-path",
            "tools/idle_daily_summary.py",
            "--utc-date",
            "2026-05-18",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == ELIGIBLE_DECISION
    assert report["eligible"] is True
