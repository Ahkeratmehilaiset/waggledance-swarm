# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

from tools.check_release_gate import evaluate_release_gate


def _valid_evidence() -> dict[str, object]:
    return {
        "schema_version": "waggledance.release_soak.v1",
        "target_version": "v3.12.0",
        "commit": "4f49564bea93df5432238661e1daf21530915a16",
        "started_at_utc": "2026-05-10T00:00:00Z",
        "ended_at_utc": "2026-05-24T00:00:00Z",
        "duration_hours": 336,
        "result": "pass",
        "silent_failures": 0,
        "error_log_clean": True,
        "ci_status": "pass",
        "profile_s_smoke": "pass",
        "security_privacy_gate": "pass",
        "axis_a_regression": "pass",
        "axis_b_gate": "pass",
        "docker_stable_policy": "finalized",
        "release_notes_anti_claims": "pass",
    }


def test_current_release_readiness_holds_until_soak_end() -> None:
    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        today=dt.date(2026, 5, 12),
    )

    assert result["decision"] == "hold"
    assert "before_no_earlier_than_date" in result["blockers"]
    assert "soak_window_incomplete" in result["blockers"]
    assert "soak_evidence_missing" in result["blockers"]
    assert result["target_version"] == "v3.12.0"


def test_release_gate_passes_with_valid_soak_evidence_after_window(tmp_path) -> None:
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "pass"
    assert result["blockers"] == []
    assert result["soak_window"]["required_hours"] == 336


def test_release_gate_treats_commit_as_evidence_subject(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence["commit"] = "1111111111111111111111111111111111111111"
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "pass"
    assert result["blockers"] == []


def test_release_gate_redacts_unreadable_release_readiness_path(tmp_path) -> None:
    readiness_path = tmp_path / "missing_readiness_DO_NOT_LEAK.md"

    result = evaluate_release_gate(readiness_path=readiness_path)
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert result["blockers"] == [
        "release_readiness_unreadable:FileNotFoundError"
    ]
    assert str(readiness_path) not in encoded
    assert readiness_path.name not in encoded


def test_release_gate_redacts_unreadable_soak_evidence_path(tmp_path) -> None:
    evidence_path = tmp_path / "missing_soak_DO_NOT_LEAK.json"

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert "soak_evidence_unreadable:FileNotFoundError" in result["blockers"]
    assert str(evidence_path) not in encoded
    assert evidence_path.name not in encoded


def test_release_gate_rejects_partial_or_dirty_soak_evidence(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence.update({
        "duration_hours": 12,
        "ended_at_utc": "2026-05-11T12:00:00Z",
        "silent_failures": 1,
        "error_log_clean": False,
        "docker_stable_policy": "draft",
        "axis_b_gate": "hold",
    })
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_duration_lt_336h" in result["blockers"]
    assert "soak_evidence_ended_before_required_soak_end" in result["blockers"]
    assert "soak_evidence_silent_failures_nonzero" in result["blockers"]
    assert "soak_evidence_error_log_not_clean" in result["blockers"]
    assert "soak_evidence_docker_policy_not_finalized" in result["blockers"]
    assert "soak_evidence_axis_b_gate_not_pass" in result["blockers"]
