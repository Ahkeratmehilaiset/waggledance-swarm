from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

from tools.run_pdam_counterfactual_demo import (
    build_demo_report,
    build_variant_matrix_report,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_pdam_counterfactual_demo.py"
SCHEMA = ROOT / "schemas" / "v3_13_0" / "evaluation_result.v0.json"
FIXED_NOW = datetime(2026, 5, 18, 1, 2, 3, tzinfo=timezone.utc)


def _run_demo(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *args],
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


def test_opt_in_receipt_bundle_verifies_and_binds_counterfactual_chain(tmp_path: Path) -> None:
    out_dir = tmp_path / "pdam-receipts"

    report = build_demo_report(out_dir=out_dir, now_utc=FIXED_NOW)

    bundle = report["receipt_bundle"]
    assert report["writes_applied"] is False
    assert bundle["receipt_count"] == 2
    assert bundle["verifier_report"]["ok"] is True
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True

    first_receipt = json.loads((out_dir / "receipt-001-factual.json").read_text(encoding="utf-8"))
    second_receipt = json.loads((out_dir / "receipt-002-counterfactual.json").read_text(encoding="utf-8"))
    assert first_receipt["ts_utc"] == "2026-05-18T01:02:04Z"
    assert second_receipt["ts_utc"] == "2026-05-18T01:02:05Z"
    assert first_receipt["prev_receipt_hash"] is None
    assert second_receipt["prev_receipt_hash"] == sha256_digest(first_receipt)

    for index, label in ((1, "factual"), (2, "counterfactual")):
        payload = json.loads((out_dir / f"payload-{index:03d}-{label}.json").read_text(encoding="utf-8"))
        evaluation = json.loads((out_dir / f"evaluation-{index:03d}-{label}.json").read_text(encoding="utf-8"))
        receipt = json.loads((out_dir / f"receipt-{index:03d}-{label}.json").read_text(encoding="utf-8"))
        assert evaluation["target_digest"] == sha256_digest(payload)
        assert receipt["canonical_payload_digest"] == sha256_digest(payload)
        assert receipt["evaluation_result_digest"] == sha256_digest(evaluation)
        assert receipt["risk_class"] == "internal_memory"
        assert receipt["operator_gate_required"] is False
        assert receipt["approval_id"] is None


def test_cli_emits_receipt_bundle_only_when_out_dir_is_requested(tmp_path: Path) -> None:
    out_dir = tmp_path / "pdam-receipts"

    result = _run_demo(
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-18T01:02:03Z",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    first_receipt = json.loads((out_dir / "receipt-001-factual.json").read_text(encoding="utf-8"))
    assert first_receipt["ts_utc"] == "2026-05-18T01:02:04Z"
    assert (out_dir / "manifest.json").exists()


def test_cli_accepts_now_with_explicit_zero_offset(tmp_path: Path) -> None:
    out_dir = tmp_path / "pdam-receipts"

    result = _run_demo(
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-18T01:02:03+00:00",
    )

    assert result.returncode == 0, result.stderr
    first_receipt = json.loads((out_dir / "receipt-001-factual.json").read_text(encoding="utf-8"))
    assert first_receipt["ts_utc"] == "2026-05-18T01:02:04Z"


@pytest.mark.parametrize(
    "now_value",
    [
        "2026-05-18T01:02:03",
        "2026-05-18T03:02:03+02:00",
        "not-a-timestamp",
    ],
)
def test_cli_rejects_non_utc_now_values(tmp_path: Path, now_value: str) -> None:
    out_dir = tmp_path / f"pdam-receipts-{len(now_value)}"

    result = _run_demo("--out-dir", str(out_dir), "--now", now_value)

    assert result.returncode == 1
    assert "PDAM counterfactual demo FAILED" in result.stderr
    assert not out_dir.exists()


def test_fixed_now_reproduces_receipt_bytes(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    build_demo_report(out_dir=first_dir, now_utc=FIXED_NOW)
    build_demo_report(out_dir=second_dir, now_utc=FIXED_NOW)

    first_receipt = json.loads((first_dir / "receipt-001-factual.json").read_text(encoding="utf-8"))
    second_receipt = json.loads((second_dir / "receipt-001-factual.json").read_text(encoding="utf-8"))
    assert first_receipt == second_receipt
    assert sha256_digest(first_receipt) == sha256_digest(second_receipt)


def test_variant_matrix_emits_four_counterfactual_rows_without_writes() -> None:
    report = build_variant_matrix_report()

    assert report["demo_version"] == "pdam.counterfactual_evaluation_matrix.v0"
    assert report["writes_applied"] is False
    assert report["variant_count"] == 4
    assert [variant["variant_id"] for variant in report["variants"]] == [
        "limited_to_idle",
        "active_wip_to_clean_close",
        "review_to_clean_close",
        "clean_close_to_blocked",
    ]
    deltas = {variant["variant_id"]: variant["delta"] for variant in report["variants"]}
    assert deltas["limited_to_idle"]["kind"] == ["KEEP_WIP", "CLOSE_OK"]
    assert deltas["active_wip_to_clean_close"]["kind"] == ["KEEP_WIP", "CLOSE_OK"]
    assert deltas["review_to_clean_close"]["kind"] == ["REVIEW", "CLOSE_OK"]
    assert deltas["limited_to_idle"]["actual_gate"] == ["review", "allow"]
    assert deltas["active_wip_to_clean_close"]["actual_gate"] == ["review", "allow"]
    assert deltas["review_to_clean_close"]["actual_gate"] == ["review", "allow"]
    active_wip = report["variants"][1]["counterfactual"]["evaluation_result"]
    assert "gate_drift:review_to_allow" in active_wip["reason_codes"]
    assert "gate_stable:allow" not in active_wip["reason_codes"]
    # Fourth variant: mutation in the REVERSED direction (healthy -> blocked),
    # the only variant whose counterfactual gate is stricter than factual.
    assert deltas["clean_close_to_blocked"]["kind"] == ["CLOSE_OK", "KEEP_WIP"]
    assert deltas["clean_close_to_blocked"]["actual_gate"] == ["allow", "review"]
    reversed_cf = report["variants"][3]["counterfactual"]["evaluation_result"]
    assert "mutation:subtool_state:IDLE_to_DOWNTIME" in reversed_cf["reason_codes"]
    assert "gate_drift:allow_to_review" in reversed_cf["reason_codes"]


def test_variant_matrix_receipt_bundle_verifies_all_eight_scenarios(tmp_path: Path) -> None:
    out_dir = tmp_path / "pdam-matrix-receipts"

    report = build_variant_matrix_report(out_dir=out_dir, now_utc=FIXED_NOW)

    assert report["receipt_bundle"]["receipt_count"] == 8
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 8
    last_receipt = json.loads(
        (out_dir / "receipt-008-clean_close_to_blocked-counterfactual.json").read_text(
            encoding="utf-8"
        )
    )
    assert last_receipt["event_id"].endswith("clean_close_to_blocked:counterfactual")


def test_receipt_bundle_refuses_non_empty_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "pdam-receipts"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("keep\n", encoding="utf-8")

    result = _run_demo("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_receipt_bundle_does_not_leak_private_operator_marker(tmp_path: Path) -> None:
    out_dir = tmp_path / "pdam-receipts"

    result = _run_demo("--out-dir", str(out_dir))

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for path in out_dir.glob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "operator_secret_goal_marker_DO_NOT_LEAK" not in combined


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
