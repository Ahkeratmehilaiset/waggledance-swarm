# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

from tools.check_release_gate import evaluate_release_gate
from tools.collect_soak_evidence import build_soak_evidence, main


def test_collector_draft_is_fail_closed_until_evidence_is_explicit() -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 16, 12, 0, tzinfo=dt.UTC),
    )

    assert evidence["schema_version"] == "waggledance.release_soak.v1"
    assert evidence["target_version"] == "v3.12.0"
    assert evidence["commit"] == "dc76e81cd8c804608bfaedf951220e46ff1baffa"
    assert evidence["started_at_utc"] == "2026-05-10T00:00:00Z"
    assert evidence["ended_at_utc"] == "2026-05-16T12:00:00Z"
    assert evidence["duration_hours"] == 156
    assert evidence["result"] == "hold"
    assert evidence["ci_status"] == "unknown"
    assert evidence["profile_s_smoke"] == "unknown"
    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False
    assert evidence["docker_stable_policy"] == "draft"


def test_collector_output_still_uses_release_gate_as_source_of_truth(tmp_path) -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 16, 12, 0, tzinfo=dt.UTC),
    )
    evidence_path = tmp_path / "v3.12.0.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        "docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 16),
    )

    assert result["decision"] == "hold"
    assert "soak_window_incomplete" in result["blockers"]
    assert "soak_evidence_result_not_pass" in result["blockers"]
    assert "soak_evidence_ci_status_not_pass" in result["blockers"]
    assert "soak_evidence_docker_policy_not_finalized" in result["blockers"]


def test_collector_can_emit_valid_pass_when_all_evidence_is_explicit(tmp_path) -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 24, 0, 0, tzinfo=dt.UTC),
        status_overrides={
            "ci_status": "pass",
            "profile_s_smoke": "pass",
            "security_privacy_gate": "pass",
            "axis_a_regression": "pass",
            "axis_b_gate": "pass",
            "release_notes_anti_claims": "pass",
        },
        silent_failures=0,
        error_log_clean=True,
        docker_stable_policy="finalized",
    )
    evidence_path = tmp_path / "v3.12.0.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        "docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert evidence["result"] == "pass"
    assert result["decision"] == "pass"
    assert result["blockers"] == []


def test_collector_cli_writes_output_and_history(tmp_path) -> None:
    output = tmp_path / "evidence" / "v3.12.0.json"
    history = tmp_path / "history.jsonl"

    rc = main([
        "--release-readiness",
        "docs/release/RELEASE_READINESS.md",
        "--output",
        str(output),
        "--history",
        str(history),
        "--commit",
        "dc76e81cd8c804608bfaedf951220e46ff1baffa",
        "--ended-at-utc",
        "2026-05-16T12:00:00Z",
    ])

    assert rc == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    history_lines = history.read_text(encoding="utf-8").splitlines()
    assert evidence["result"] == "hold"
    assert len(history_lines) == 1
    assert json.loads(history_lines[0])["target_version"] == "v3.12.0"
