from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_a3_counterfactual_axis_proof import (
    build_a3_counterfactual_axis_proof,
    render_markdown,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_a3_counterfactual_axis_proof.py"
FIXED_NOW = datetime(2026, 5, 20, 19, 50, tzinfo=timezone.utc)


def _run_a3(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_a3_axis_proof_reports_counterfactual_delta_without_writes() -> None:
    report = build_a3_counterfactual_axis_proof(now_utc=FIXED_NOW)
    replay = report["stored_consensus_replay"]
    runtime_smoke = report["runtime_condition_replay_smoke"]

    assert report["report_version"] == "wd.v12.a3_counterfactual_axis_proof.v0"
    assert report["ok"] is True
    assert report["axis_id"] == "A3"
    assert report["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert report["evaluation_result_version"] == "magma.evaluation_result.v1"
    assert report["writes_applied"] is False
    assert report["counterfactual_delta_proven"] is True
    assert report["variant_count"] == 3
    assert report["variants_with_kind_delta"] == 3
    assert report["variants_with_gate_delta"] == 2
    assert report["delta"] == {
        "actual_gate": ["review", "allow"],
        "kind": ["KEEP_WIP", "CLOSE_OK"],
        "verdict": ["pass", "review"],
    }
    assert [variant["variant_id"] for variant in report["variants"]] == [
        "limited_to_idle",
        "duplicate_to_clean_close",
        "review_to_clean_close",
    ]
    assert set(report["delta_fields"]) == {"actual_gate", "kind", "verdict"}
    assert report["receipt_chain_verified"] is False
    assert report["stored_consensus_replay_verified"] is True
    assert report["receipt_bound_stored_consensus_replay"] is False
    assert replay["replay_version"] == "wd.v12.a3_stored_consensus_replay.v0"
    assert replay["decision"] == "candidate_diff_charter_passed"
    assert replay["candidate_diff_charter_allowed"] is True
    assert replay["receipt_bound"] is False
    assert replay["receipt_chain_id"] is None
    assert replay["satisfied_gates"] == []
    assert "forensic_artifact_receipt" in replay["next_required_gates"]
    assert replay["admission_report_version"] == (
        "idle_consensus_candidate_diff_replay_admission.v0"
    )
    assert replay["replay_seed"]["seed_version"] == "idle_consensus_replay_seed.v0"
    assert replay["replay_seed"]["digest"].startswith("sha256:")
    assert replay["stored_consensus"]["artifact_version"] == (
        "idle_consensus_operator_review.v1"
    )
    assert replay["stored_consensus"]["status"] == "soft_convergence"
    assert replay["stored_consensus"]["replay_seed_digest"] == replay["replay_seed"][
        "digest"
    ]
    assert replay["stored_consensus"]["transcript_digest"] == replay["replay_seed"][
        "transcript_digest"
    ]
    assert replay["stored_consensus"]["convergence_digest"] == replay["replay_seed"][
        "convergence_digest"
    ]
    assert replay["candidate_diff"]["changed_paths"] == [
        "docs/architecture/consensus_artifacts/a3_counterfactual_delta_replay.md"
    ]
    assert replay["candidate_diff"]["diff_text_included"] is False
    assert "diff_text" not in replay["candidate_diff"]
    assert replay["counterfactual_eval"]["provided"] is True
    assert replay["counterfactual_eval"]["satisfies_replay_gate"] is True
    assert replay["counterfactual_eval"]["receipt_payload_included"] is False
    assert replay["counterfactual_eval"]["observability"]["status"] == (
        "measured_local_partial"
    )
    assert replay["draft_pr_gate_blockers"] == [
        "operator_review_gate_required",
        "live_rate_gate_not_evaluated",
    ]
    assert replay["path_gate"]["allowed"] is True
    assert replay["diff_gate"]["allowed"] is True
    assert replay["eligible_for_draft_pr_gate"] is False
    assert replay["external_effect"] is False
    assert replay["would_create_task"] is False
    assert replay["would_create_branch"] is False
    assert replay["would_create_pr"] is False
    assert replay["would_merge"] is False
    assert report["factual"]["evaluation_version"] == "magma.evaluation_result.v1"
    assert report["factual"]["competitor_axis_reference"] == "A3"
    assert report["factual"]["confidence_basis"] == {
        "method": "point_estimate",
        "sample_count": 1,
        "methodology_reference": "tools/run_v12_a3_counterfactual_axis_proof.py",
    }
    assert report["factual"]["sanitization_audit"] == {
        "applied": ["locale_normalization"],
        "redaction_count": 0,
    }
    assert report["factual"]["subject_payload_size_bytes"] > 0
    assert runtime_smoke == {
        "schema_version": "wd.v12.a3_runtime_condition_replay_smoke.v0",
        "ok": True,
        "sample_family": "scalar_unit_conversion_24_same_sample_set",
        "min_samples": 20,
        "sample_count": 24,
        "compute_status": "computed",
        "observability_status": "measured_local_partial",
        "claim_label": "MEASURED_LOCAL_PARTIAL",
        "runtime_conditions_met": True,
        "divergence_count": 24,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "source_available": True,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "required_runtime_evidence_present": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
        "raw_fields_exported": False,
        "privacy_canary_absent": True,
        "emitted_text_passes_leak_policy": True,
        "claim_boundary": "runtime_condition_smoke_only_not_axis_claim_upgrade",
    }
    assert report["no_overclaim_guardrails"][
        "runtime_smoke_is_not_axis_claim_upgrade"
    ] is True
    assert "per_arm" not in runtime_smoke
    assert "divergences" not in runtime_smoke
    assert "candidate_hash" not in runtime_smoke
    assert "incumbent_hash" not in runtime_smoke


def test_a3_axis_proof_writes_verified_receipt_chain(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    report = build_a3_counterfactual_axis_proof(
        receipt_out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["receipt_chain_verified"] is True
    assert report["receipt_bound_stored_consensus_replay"] is True
    assert report["stored_consensus_replay"]["receipt_bound"] is True
    assert report["stored_consensus_replay"]["receipt_chain_id"] == (
        "magma:v12_a3_counterfactual_axis:v1"
    )
    assert report["stored_consensus_replay"]["satisfied_gates"] == [
        "forensic_artifact_receipt"
    ]
    assert (
        "forensic_artifact_receipt"
        not in report["stored_consensus_replay"]["next_required_gates"]
    )
    assert report["receipt_bundle"]["available"] is True
    assert report["receipt_bundle"]["receipt_count"] == 6
    assert report["receipt_bundle"]["manifest"].endswith("manifest.json")
    assert report["receipt_chain_id"] == "magma:v12_a3_counterfactual_axis:v1"
    assert (out_dir / "manifest.json").exists()
    first_receipt = json.loads(
        (out_dir / "receipt-001-limited_to_idle-factual.json").read_text(
            encoding="utf-8"
        )
    )
    second_receipt = json.loads(
        (out_dir / "receipt-002-limited_to_idle-counterfactual.json").read_text(
            encoding="utf-8"
        )
    )
    sixth_receipt = json.loads(
        (out_dir / "receipt-006-review_to_clean_close-counterfactual.json").read_text(
            encoding="utf-8"
        )
    )
    assert second_receipt["prev_receipt_hash"] == sha256_digest(first_receipt)
    assert sixth_receipt["event_id"].endswith("review_to_clean_close:counterfactual")
    first_evaluation = json.loads(
        (out_dir / "evaluation-001-limited_to_idle-factual.json").read_text(
            encoding="utf-8"
        )
    )
    assert first_evaluation["evaluation_version"] == "magma.evaluation_result.v1"
    assert first_evaluation["competitor_axis_reference"] == "A3"
    assert first_evaluation["confidence_basis"]["method"] == "point_estimate"
    assert "axis:A3_counterfactual_evaluation_delta" in first_evaluation["reason_codes"]
    assert (
        "stored_consensus_replay:candidate_diff_charter_gate"
        in first_evaluation["reason_codes"]
    )
    replay_binding = _receipt_replay_binding(report)
    assert first_receipt["rco_decision_digest"] == sha256_digest({
        "actual_gate": first_evaluation["actual_gate"],
        "case_id": first_evaluation["case_id"],
        "competitor_axis_reference": first_evaluation["competitor_axis_reference"],
        "stored_consensus_replay": replay_binding,
        "verdict": first_evaluation["verdict"],
    })
    assert first_receipt["world_snapshot_digest"] == sha256_digest({
        "case_id": report["variants"][0]["case_id"],
        "scenario": "factual",
        "stored_consensus_replay": replay_binding,
        "subtool_state": report["variants"][0]["factual"]["subtool_state"],
    })
    assert first_receipt["solver_contract_digest"] == sha256_digest({
        "solver_selection": first_evaluation["solver_selection"],
        "policy_version": first_evaluation["policy_version"],
        "stored_consensus_replay_version": report["stored_consensus_replay"][
            "replay_version"
        ],
    })


def test_a3_markdown_preserves_no_rival_benchmark_guardrail(tmp_path: Path) -> None:
    report = build_a3_counterfactual_axis_proof(
        receipt_out_dir=tmp_path / "a3-receipts",
        now_utc=FIXED_NOW,
    )
    markdown = render_markdown(report)

    assert "V12 A3 Counterfactual Axis Proof" in markdown
    assert "counterfactual_delta_proven: `true`" in markdown
    assert "evaluation_result_version: `magma.evaluation_result.v1`" in markdown
    assert "variant_count: `3`" in markdown
    assert "`review_to_clean_close`" in markdown
    assert "receipt_chain_verified: `true`" in markdown
    assert "stored_consensus_replay_verified: `true`" in markdown
    assert "stored_consensus_replay_receipt_bound: `true`" in markdown
    assert "stored_consensus_replay_decision: `candidate_diff_charter_passed`" in markdown
    assert "runtime_condition_replay_smoke: `true`" in markdown
    assert "runtime_replay_claim_label: `MEASURED_LOCAL_PARTIAL`" in markdown
    assert "runtime_replay_sample_count: `24`" in markdown
    assert "does not upgrade the top-level axis claim" in markdown
    assert "not a rival benchmark" in markdown


def test_a3_cli_json_with_receipts_is_deterministic(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    result = _run_a3(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-20T19:50:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["generated_at_utc"] == "2026-05-20T19:50:00Z"
    assert payload["counterfactual_delta_proven"] is True
    assert payload["variant_count"] == 3
    assert payload["receipt_chain_verified"] is True
    assert payload["receipt_bound_stored_consensus_replay"] is True
    assert payload["stored_consensus_replay"]["candidate_diff_charter_allowed"] is True
    assert payload["receipt_bundle"]["receipt_count"] == 6
    assert payload["evaluation_result_version"] == "magma.evaluation_result.v1"
    assert payload["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert (
        payload["runtime_condition_replay_smoke"]["claim_label"]
        == "MEASURED_LOCAL_PARTIAL"
    )
    assert payload["runtime_condition_replay_smoke"]["runtime_conditions_met"] is True
    assert payload["runtime_condition_replay_smoke"]["sample_count"] == 24
    assert (
        payload["runtime_condition_replay_smoke"][
            "required_runtime_evidence_present"
        ]
        is False
    )
    assert (
        payload["runtime_condition_replay_smoke"]["claim_boundary"]
        == "runtime_condition_smoke_only_not_axis_claim_upgrade"
    )


def test_a3_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = _run_a3(
        "--json",
        "--out-dir",
        str(tmp_path / "a3-receipts"),
        "--now",
        "2026-05-20T22:50:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


def test_a3_output_does_not_leak_private_marker(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    result = _run_a3("--json", "--out-dir", str(out_dir))

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for path in out_dir.rglob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "operator_secret_goal_marker_DO_NOT_LEAK" not in combined
    assert "per_arm" not in result.stdout
    assert "divergences" not in result.stdout


def test_a3_v1_receipt_bundle_detects_tampered_axis_metadata(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    report = build_a3_counterfactual_axis_proof(
        receipt_out_dir=out_dir,
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_bundle"]["manifest"])
    evaluation_path = out_dir / "evaluation-001-limited_to_idle-factual.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["competitor_axis_reference"] = "A4"
    evaluation_path.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "entry 1: evaluation_result_digest mismatch" in verification["errors"]


def test_a3_v1_receipt_chain_detects_tampered_replay_binding(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    report = build_a3_counterfactual_axis_proof(
        receipt_out_dir=out_dir,
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_bundle"]["manifest"])
    receipt_path = out_dir / "receipt-001-limited_to_idle-factual.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["rco_decision_digest"] = "sha256:" + ("9" * 64)
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "chain: missing_parent for entry 2" in verification["errors"]


def _receipt_replay_binding(report: dict) -> dict:
    replay = report["stored_consensus_replay"]
    return {
        "replay_version": replay["replay_version"],
        "admission_report_version": replay["admission_report_version"],
        "stored_consensus_digest": replay["stored_consensus"]["digest"],
        "replay_seed_digest": replay["stored_consensus"]["replay_seed_digest"],
        "candidate_diff_digest": replay["candidate_diff"]["digest"],
        "candidate_diff_charter_allowed": replay["candidate_diff_charter_allowed"],
        "counterfactual_eval_satisfies_replay_gate": replay["counterfactual_eval"][
            "satisfies_replay_gate"
        ],
        "replay_decision": replay["decision"],
    }
