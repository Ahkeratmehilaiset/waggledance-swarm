from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.verify_counterfactual_observability_guard import (
    GUARD_SCHEMA_VERSION,
    verify_counterfactual_observability_artifact,
)
from waggledance.core.autonomy_growth.counterfactual_replay import (
    COUNTERFACTUAL_DELTA_SCHEMA,
    COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_counterfactual_observability_guard.py"
CONTRACT = (
    ROOT
    / "docs"
    / "architecture"
    / "COUNTERFACTUAL_OBSERVABILITY_GUARD_CONTRACT.json"
)


def _raw_delta() -> dict[str, object]:
    core: dict[str, object] = {
        "schema_version": COUNTERFACTUAL_DELTA_SCHEMA,
        "candidate_hash": "sha256:" + "a" * 64,
        "incumbent_hash": "sha256:" + "b" * 64,
        "sample_count": 24,
        "candidate_sample_set_digest": "sha256:" + "c" * 64,
        "incumbent_sample_set_digest": "sha256:" + "c" * 64,
        "oracle_kind": "formula_recompute",
        "deterministic": True,
        "divergence_count": 3,
        "improvement_count": 2,
        "regression_count": 1,
        "neutral_divergence_count": 0,
        "oracle_agreement_advantage": 0.25,
        "no_delta": False,
        "per_arm": {
            "candidate": {"results": [{"inputs": {"secret": "do-not-export"}}]},
            "incumbent": {"results": []},
        },
        "divergences": [{"candidate_output": "private", "incumbent_output": "old"}],
    }
    return {**core, "canonical_digest": sha256_digest(core)}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_guard_accepts_raw_delta_and_emits_sanitized_path_free_report() -> None:
    report = verify_counterfactual_observability_artifact(_raw_delta())
    rendered = json.dumps(report, sort_keys=True)

    assert report["schema_version"] == GUARD_SCHEMA_VERSION
    assert report["ok"] is True
    assert report["runtime_measured_claim_safe"] is True
    assert report["source_path_recorded"] is False
    assert report["raw_fields_exported"] is False
    assert report["runtime_authority_granted"] is False
    assert report["observability_summary"]["status"] == "runtime_measured"
    assert report["observability_summary"]["sample_count"] == 24
    assert (
        report["observability_summary"]["net_oracle_agreement_direction"]
        == "net_improvement"
    )
    assert "per_arm" not in rendered
    assert "divergences" not in rendered
    assert "candidate_hash" not in rendered
    assert "do-not-export" not in rendered
    assert _raw_delta()["canonical_digest"] not in rendered


def test_cli_json_output_is_path_free(tmp_path: Path) -> None:
    artifact = tmp_path / "counterfactual-private-path.json"
    artifact.write_text(json.dumps(_raw_delta()), encoding="utf-8")

    result = _run("--artifact-json", str(artifact), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "counterfactual-private-path" not in result.stdout
    assert str(tmp_path) not in result.stdout


def test_guard_rejects_bad_raw_delta_digest() -> None:
    delta = _raw_delta()
    delta["canonical_digest"] = "sha256:" + "0" * 64

    report = verify_counterfactual_observability_artifact(delta)

    assert report["ok"] is False
    assert "canonical_digest_mismatch" in report["blockers"]
    assert report["runtime_measured_claim_safe"] is False


def test_guard_rejects_raw_delta_authority_flags_with_valid_digest() -> None:
    delta = _raw_delta()
    delta.update({
        "controls_present": True,
        "runtime_authority_granted": True,
        "external_writes_applied": True,
        "payload_fields_exported": True,
    })
    core = {key: value for key, value in delta.items() if key != "canonical_digest"}
    delta["canonical_digest"] = sha256_digest(core)

    report = verify_counterfactual_observability_artifact(delta)

    assert report["ok"] is False
    assert "controls_present_must_be_false" in report["blockers"]
    assert "runtime_authority_granted_must_be_false" in report["blockers"]
    assert "external_writes_applied_must_be_false" in report["blockers"]
    assert "payload_fields_exported_must_be_false" in report["blockers"]
    assert report["runtime_measured_claim_safe"] is False


def test_cli_rejects_non_finite_json_without_echoing_path(tmp_path: Path) -> None:
    artifact = tmp_path / "nan-counterfactual.json"
    artifact.write_text(
        '{"schema_version":"magma.counterfactual_delta.v0","sample_count":NaN}',
        encoding="utf-8",
    )

    result = _run("--artifact-json", str(artifact), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == ["artifact_json_invalid_or_non_finite"]
    assert "nan-counterfactual" not in result.stdout


def test_guard_rejects_authority_flags_on_status_summary() -> None:
    status = {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": True,
        "compute_status": "computed",
        "status": "runtime_measured",
        "a3_label": "RUNTIME_MEASURED",
        "sample_count": 24,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "controls_present": False,
        "runtime_authority_granted": True,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }

    report = verify_counterfactual_observability_artifact(status)

    assert report["ok"] is False
    assert "runtime_authority_granted_must_be_false" in report["blockers"]


def test_guard_rejects_authority_flags_on_promotion_summary() -> None:
    summary = {
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": "MEASURED_LOCAL_PARTIAL",
        "sample_count": 20,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest": "sha256:" + "1" * 64,
        "controls_present": True,
        "runtime_authority_granted": True,
        "external_writes_applied": True,
        "payload_fields_exported": True,
    }

    report = verify_counterfactual_observability_artifact(summary)

    assert report["ok"] is False
    assert "controls_present_must_be_false" in report["blockers"]
    assert "runtime_authority_granted_must_be_false" in report["blockers"]
    assert "external_writes_applied_must_be_false" in report["blockers"]
    assert "payload_fields_exported_must_be_false" in report["blockers"]
    assert report["runtime_measured_claim_safe"] is False


def test_guard_bounds_unknown_status_summary_a3_label() -> None:
    status = {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": True,
        "compute_status": "computed",
        "status": "runtime_measured",
        "a3_label": "gpt-4o-secret-raw-label",
        "sample_count": 24,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }

    report = verify_counterfactual_observability_artifact(status)
    rendered = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert "status_summary_unknown_a3_label" in report["blockers"]
    assert report["observability_summary"]["a3_label"] == "INSUFFICIENT"
    assert "gpt-4o-secret-raw-label" not in rendered


def test_guard_bounds_non_string_status_summary_a3_label() -> None:
    status = {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": True,
        "compute_status": "computed",
        "status": "runtime_measured",
        "a3_label": ["RUNTIME_MEASURED"],
        "sample_count": 24,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }

    report = verify_counterfactual_observability_artifact(status)

    assert report["ok"] is False
    assert "status_summary_unknown_a3_label" in report["blockers"]
    assert report["observability_summary"]["a3_label"] == "INSUFFICIENT"


def test_guard_rejects_unknown_status_summary_oracle_direction() -> None:
    status = {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": True,
        "compute_status": "computed",
        "status": "runtime_measured",
        "a3_label": "RUNTIME_MEASURED",
        "sample_count": 24,
        "divergence_count": 3,
        "improvement_count": 2,
        "regression_count": 1,
        "neutral_divergence_count": 0,
        "oracle_agreement_advantage": 0.25,
        "net_oracle_agreement_direction": "operator-secret-direction",
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }

    report = verify_counterfactual_observability_artifact(status)
    rendered = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert "status_summary_unknown_oracle_direction" in report["blockers"]
    assert (
        report["observability_summary"]["net_oracle_agreement_direction"]
        == "unknown"
    )
    assert "operator-secret-direction" not in rendered


def test_guard_rejects_raw_fields_in_promotion_summary() -> None:
    report = verify_counterfactual_observability_artifact({
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": "MEASURED_LOCAL_PARTIAL",
        "sample_count": 5,
        "same_sample_set": True,
        "deterministic": True,
        "divergence_count": 1,
        "no_delta": False,
        "delta_digest": "sha256:" + "1" * 64,
        "per_arm": {"candidate": "private"},
    })

    assert report["ok"] is False
    assert "promotion_summary_contains_raw_fields" in report["blockers"]


def test_contract_pins_guard_invariants() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    invariant_ids = {item["id"] for item in contract["invariants"]}

    assert contract["guard_schema_version"] == GUARD_SCHEMA_VERSION
    assert invariant_ids == {
        "CFOG-001",
        "CFOG-002",
        "CFOG-003",
        "CFOG-004",
        "CFOG-005",
    }
