from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_pdam_counterfactual_demo.py"
SCHEMA = ROOT / "schemas" / "v3_13_0" / "evaluation_result.v0.json"


def _run_demo() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _report() -> dict:
    result = _run_demo()
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def test_demo_emits_schema_valid_evaluation_results() -> None:
    report = _report()
    validator = _validator()

    validator.validate(report["factual"]["evaluation_result"])
    validator.validate(report["counterfactual"]["evaluation_result"])


def test_counterfactual_changes_decision_and_gate_without_applying_write() -> None:
    report = _report()

    assert report["demo_version"] == "pdam.counterfactual_evaluation.v0"
    assert report["factual"]["action"]["kind"] == "KEEP_WIP"
    assert report["counterfactual"]["action"]["kind"] == "CLOSE_OK"
    assert report["factual"]["evaluation_result"]["actual_gate"] == "review"
    assert report["factual"]["evaluation_result"]["risk_class"] == "internal_memory"
    assert report["counterfactual"]["evaluation_result"]["actual_gate"] == "allow"
    assert report["counterfactual"]["evaluation_result"]["operator_required"] is False
    assert report["writes_applied"] is False


def test_target_digest_binds_evaluation_to_emitted_action_payload() -> None:
    report = _report()

    for side in ("factual", "counterfactual"):
        action = report[side]["action"]
        evaluation = report[side]["evaluation_result"]
        assert evaluation["target_digest"] == sha256_digest(action)


def test_demo_reports_counterfactual_delta() -> None:
    report = _report()

    assert report["delta"] == {
        "kind": ["KEEP_WIP", "CLOSE_OK"],
        "actual_gate": ["review", "allow"],
        "verdict": ["pass", "review"],
    }


def test_counterfactual_reason_codes_capture_mutation_and_gate_drift() -> None:
    report = _report()
    reason_codes = report["counterfactual"]["evaluation_result"]["reason_codes"]

    assert "mutation:subtool_state:DOWNTIME_to_IDLE" in reason_codes
    assert "gate_drift:review_to_allow" in reason_codes


def test_demo_does_not_leak_private_operator_marker() -> None:
    result = _run_demo()

    assert result.returncode == 0, result.stderr
    assert "operator_secret_goal_marker_DO_NOT_LEAK" not in result.stdout + result.stderr


def test_build_evaluation_result_enforces_schema_and_pure_solver_risk_class() -> None:
    base = {
        "case_id": "case:pdam:helper:001",
        "subject_type": "counterfactual",
        "target_payload": {"action": "noop"},
        "risk_class": "internal_memory",
        "expected_gate": "review",
        "actual_gate": "review",
        "verifier_path": ["pdam_close_solver"],
        "solver_selection": ["pdam_close_solver"],
        "policy_version": "policy:pdam_close_solver:v1",
        "charter_version": "charter:v1",
        "domain_threshold_version": "threshold:pdam_close_solver:v1",
        "verdict": "pass",
        "reason_codes": ["pdam:noop"],
        "confidence_score": 1.0,
    }

    assert build_evaluation_result(**base)["operator_required"] is False
    with pytest.raises(ValueError, match="external_effect"):
        build_evaluation_result(**{**base, "risk_class": "external_effect"})
    with pytest.raises(ValueError, match="confidence_score"):
        build_evaluation_result(**{**base, "confidence_score": 1.5})
