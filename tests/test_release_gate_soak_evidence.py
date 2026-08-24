# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

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


def _verified_report(**_kwargs) -> dict[str, object]:
    return {
        "schema_version": "waggledance.release_soak_verifier.v1",
        "verified": True,
        "blockers": [],
        "mismatched_fields": [],
    }


def test_release_gate_passes_with_valid_soak_evidence_after_window(
    tmp_path, monkeypatch
) -> None:
    # Reproducibility is pinned verified=True here; this test owns the
    # structural-validity contract. Real verifier integration is covered by
    # the reproducibility regressions below.
    calls: list[dict[str, object]] = []

    def _record(**kwargs):
        calls.append(kwargs)
        return _verified_report()

    monkeypatch.setattr(
        "tools.verify_release_soak_evidence.build_report", _record
    )
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
    repro = result["soak_evidence_diagnostics"]["soak_reproducibility"]
    assert repro == {
        "invoked": True,
        "available": True,
        "verified": True,
        "mismatched_field_count": 0,
        "verifier_blockers": [],
    }
    # Canonical defaults are mandatory: the gate passes exactly the
    # evidence and readiness paths, never an artifact-root or release-notes
    # override.
    assert calls
    assert set(calls[0]) == {"soak_evidence", "release_readiness"}


def test_release_gate_treats_commit_as_evidence_subject(
    tmp_path, monkeypatch
) -> None:
    # Evidence subject commit need not equal any storing/current commit;
    # reproducibility is pinned verified=True to isolate that semantic.
    monkeypatch.setattr(
        "tools.verify_release_soak_evidence.build_report",
        lambda **_kwargs: _verified_report(),
    )
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
    assert result["soak_evidence_diagnostics"] == {
        "provided": True,
        "readable": False,
        "object": False,
    }
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
    diagnostics = result["soak_evidence_diagnostics"]
    assert diagnostics["provided"] is True
    assert diagnostics["readable"] is True
    assert diagnostics["object"] is True
    assert diagnostics["duration_hours"] == 12
    assert diagnostics["required_duration_hours"] == 336
    assert diagnostics["ended_at_date"] == "2026-05-11"
    assert diagnostics["required_soak_end"] == "2026-05-24"
    assert diagnostics["silent_failures"] == 1
    assert diagnostics["expected_silent_failures"] == 0
    assert diagnostics["error_log_clean"] is False
    assert diagnostics["expected_error_log_clean"] is True
    assert diagnostics["docker_stable_policy"] == "draft"
    assert diagnostics["expected_docker_stable_policy"] == "finalized"
    assert diagnostics["status_fields"]["axis_b_gate"] == {
        "actual": "hold",
        "expected": "pass",
    }


def test_release_gate_diagnostics_redact_unexpected_status_values(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence["ci_status"] = {"token": "DO_NOT_LEAK"}
    evidence["axis_a_regression"] = "secret-status-DO_NOT_LEAK"
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert result["soak_evidence_diagnostics"]["status_fields"]["ci_status"] == {
        "actual": "<redacted>",
        "expected": "pass",
    }
    assert result["soak_evidence_diagnostics"]["status_fields"][
        "axis_a_regression"
    ] == {
        "actual": "<redacted>",
        "expected": "pass",
    }
    assert "DO_NOT_LEAK" not in encoded


def test_release_gate_holds_when_evidence_not_reproducible(tmp_path) -> None:
    # Real verifier, no mocks: syntactically valid evidence whose fields are
    # not rebuildable from the canonical local artifacts must hold.
    evidence_path = tmp_path / "release_soak_evidence_DO_NOT_LEAK.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert "soak_evidence_not_reproducible" in result["blockers"]
    assert any(
        blocker.startswith("field_mismatch:") for blocker in result["blockers"]
    )
    repro = result["soak_evidence_diagnostics"]["soak_reproducibility"]
    assert repro["invoked"] is True
    assert repro["available"] is True
    assert repro["verified"] is False
    assert str(evidence_path) not in encoded
    assert evidence_path.name not in encoded


def test_release_gate_holds_when_reproducibility_verifier_raises(
    tmp_path, monkeypatch
) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(
        "tools.verify_release_soak_evidence.build_report", _boom
    )
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert (
        "soak_reproducibility_verifier_error:RuntimeError"
        in result["blockers"]
    )
    repro = result["soak_evidence_diagnostics"]["soak_reproducibility"]
    assert repro["verified"] is False


def test_release_gate_holds_when_local_artifacts_unbuildable(
    tmp_path, monkeypatch
) -> None:
    # Verifier reports the expected-evidence rebuild itself failed (e.g.
    # canonical artifacts unreadable): the gate must hold and surface the
    # verifier's path-free blocker vocabulary unchanged.
    monkeypatch.setattr(
        "tools.verify_release_soak_evidence.build_report",
        lambda **_kwargs: {
            "schema_version": "waggledance.release_soak_verifier.v1",
            "verified": False,
            "blockers": ["expected_evidence_unbuildable:OSError"],
            "mismatched_fields": [],
        },
    )
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_not_reproducible" in result["blockers"]
    assert "expected_evidence_unbuildable:OSError" in result["blockers"]
    repro = result["soak_evidence_diagnostics"]["soak_reproducibility"]
    assert repro["verified"] is False
    assert repro["mismatched_field_count"] == 0
    assert repro["verifier_blockers"] == [
        "expected_evidence_unbuildable:OSError"
    ]


def test_release_gate_holds_when_reproducibility_report_malformed(
    tmp_path, monkeypatch
) -> None:
    # A verifier returning a non-dict (None here) must fail closed with a
    # stable redacted blocker instead of crashing the gate.
    monkeypatch.setattr(
        "tools.verify_release_soak_evidence.build_report",
        lambda **_kwargs: None,
    )
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_reproducibility_report_malformed" in result["blockers"]
    repro = result["soak_evidence_diagnostics"]["soak_reproducibility"]
    assert repro["verified"] is False
    assert repro["report_malformed"] is True


def test_release_gate_cli_direct_script_reaches_canonical_verifier() -> None:
    # Direct-script invocation puts tools/ on sys.path (not the repo root);
    # the gate must still import the canonical verifier and surface real
    # field mismatches - never degrade to verifier_unavailable.
    completed = subprocess.run(
        [
            sys.executable,
            str(Path("tools") / "check_release_gate.py"),
            "--release-readiness",
            "docs/release/RELEASE_READINESS.md",
            "--soak-evidence",
            "docs/runs/release_soak_evidence/v3.12.0.json",
            "--today",
            "2026-08-24",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["decision"] == "hold"
    assert (
        "soak_reproducibility_verifier_unavailable"
        not in result["blockers"]
    )
    assert "soak_evidence_not_reproducible" in result["blockers"]
    assert any(
        blocker.startswith("field_mismatch:")
        for blocker in result["blockers"]
    )
    repro = result["soak_evidence_diagnostics"]["soak_reproducibility"]
    assert repro["available"] is True
    assert repro["verified"] is False


def test_release_gate_skips_reproducibility_for_structurally_invalid_paths(
    tmp_path,
) -> None:
    # Unreadable evidence keeps the pre-existing exact diagnostics shape:
    # the reproducibility verifier only runs for readable object evidence.
    evidence_path = tmp_path / "missing_soak.json"

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_reproducibility" not in result["soak_evidence_diagnostics"]
    assert not any(
        blocker.startswith("soak_reproducibility")
        or blocker == "soak_evidence_not_reproducible"
        for blocker in result["blockers"]
    )
