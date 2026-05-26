# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

from tools.run_release_gate_readonly_recheck import build_report, main


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


def test_current_release_gate_hold_is_recorded_without_release_mutation() -> None:
    report = build_report(
        checked_at_utc=dt.datetime(2026, 5, 26, 1, 0, tzinfo=dt.UTC),
        today=dt.date(2026, 5, 26),
    )

    assert report["ok"] is True
    assert report["release_gate_decision"] == "hold"
    assert report["blockers"] == [
        "soak_evidence_result_not_pass",
        "soak_evidence_duration_lt_336h",
        "soak_evidence_ended_before_required_soak_end",
    ]
    assert report["release_boundary"] == {
        "tag_creation": False,
        "docker_latest_move": False,
        "stable_release_claim": False,
        "external_effect_authority_change": False,
    }
    assert report["release_gate_effect"] == "none"
    assert report["read_only_invariants"] == {
        "release_gate_effect": "observation_only",
        "no_tag_created": True,
        "no_docker_latest_moved": True,
        "no_stable_release_claim": True,
        "no_external_effect_authority_change": True,
    }
    assert report["gate"]["target_version"] == "v3.12.0"


def test_passing_gate_is_still_read_only(tmp_path) -> None:
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    report = build_report(
        soak_evidence=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, 1, 0, tzinfo=dt.UTC),
        today=dt.date(2026, 5, 24),
    )

    assert report["ok"] is True
    assert report["release_gate_decision"] == "pass"
    assert report["blockers"] == []
    assert all(value is False for value in report["release_boundary"].values())
    assert report["read_only"] is True
    assert report["release_gate_effect"] == "none"


def test_cli_writes_hold_report_without_failing(tmp_path) -> None:
    output = tmp_path / "release_gate_readonly_recheck.json"

    rc = main([
        "--checked-at-utc",
        "2026-05-26T01:00:00Z",
        "--today",
        "2026-05-26",
        "--output",
        str(output),
    ])

    assert rc == 0
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["release_gate_decision"] == "hold"
    assert loaded["release_boundary"]["tag_creation"] is False
    assert loaded["release_boundary"]["docker_latest_move"] is False
    assert loaded["release_boundary"]["stable_release_claim"] is False
    assert loaded["release_boundary"]["external_effect_authority_change"] is False
