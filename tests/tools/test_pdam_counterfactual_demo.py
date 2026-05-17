from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import jsonschema

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
    assert report["counterfactual"]["evaluation_result"]["actual_gate"] == "require_approval"
    assert report["counterfactual"]["evaluation_result"]["operator_required"] is True
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
        "actual_gate": ["review", "require_approval"],
        "verdict": ["review", "pass"],
    }
