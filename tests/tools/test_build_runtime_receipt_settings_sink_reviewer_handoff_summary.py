# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_receipt_settings_sink_reviewer_handoff_summary import (
    PROOF_ID,
    SOURCE_PROOF_ID,
    SUMMARY_VERSION,
    build_runtime_receipt_settings_sink_reviewer_handoff_summary,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_runtime_receipt_settings_sink_reviewer_handoff_summary.py"
)
# A relative config path + free-text conclusion the renderer must NOT echo.
SOURCE_OUT_DIR = "var/runtime_receipts/configured_sink"
SOURCE_CONCLUSION = "configured runtime receipt sink coverage verified ok"


def _ready_sink_proof() -> dict[str, object]:
    return {
        "proof_id": SOURCE_PROOF_ID,
        "ok": True,
        "default_off_preserved": True,
        "configured_sink_callable": True,
        "configured_sink_verifier_ok": True,
        "configured_sink_result_path_free": True,
        "configured_sink_result_payload_free": True,
        "local_receipt_bundle_written": True,
        "emitted_receipt_payload_safe": True,
        "temp_artifacts_removed": True,
        "settings_default_enabled": False,
        "paths_returned": False,
        "payloads_returned": False,
        "default_runtime_receipt_emission_changed": False,
        "runtime_authority_changed": False,
        "external_writes_applied": False,
        "configured_sink_receipt_count": 1,
        # Source-only fields the renderer must NOT surface:
        "safe_conclusion": SOURCE_CONCLUSION,
        "settings_out_dir": SOURCE_OUT_DIR,
        "settings_evaluation_version": "magma.evaluation_result.v0",
    }


def _build(proof: dict[str, object]):
    return build_runtime_receipt_settings_sink_reviewer_handoff_summary(
        sink_proof=proof,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="wd-solver-first-runtime-receipt-sink",
        now_utc=datetime(2026, 6, 21, 11, 0, tzinfo=timezone.utc),
    )


def test_ready_sink_proof_renders_measurement_only_summary() -> None:
    summary = _build(_ready_sink_proof())

    assert summary["ok"] is True
    assert summary["proof_id"] == PROOF_ID
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["source_proof_id"] == SOURCE_PROOF_ID
    assert summary["source_proof_ok"] is True
    assert summary["manual_review_required"] is True
    assert summary["claim_safe_unchanged"] is True
    cov = summary["configured_sink"]
    assert cov["receipt_count"] == 1
    assert cov["configured_sink_verifier_ok"] is True
    assert cov["configured_sink_result_path_free"] is True
    assert cov["configured_sink_result_payload_free"] is True
    # Authority/decision fields are all pinned False.
    for field in (
        "approval_granted",
        "runtime_authority_granted",
        "direct_bridge_write_performed",
        "default_runtime_receipt_emission_changed",
        "external_writes_applied",
        "local_paths_recorded",
        "artifact_payloads_included",
    ):
        assert summary[field] is False


def test_summary_does_not_echo_source_freetext_or_path() -> None:
    summary = _build(_ready_sink_proof())
    serialized = json.dumps(summary, sort_keys=True)
    # The no-content lesson: only derived booleans/counts are surfaced, never
    # the source proof's free-text conclusion or its config path.
    assert SOURCE_CONCLUSION not in serialized
    assert SOURCE_OUT_DIR not in serialized
    assert "safe_conclusion" not in serialized
    assert "settings_out_dir" not in serialized


def test_source_proof_id_mismatch_rejected() -> None:
    proof = _ready_sink_proof()
    proof["proof_id"] = "some_other_proof_v9"
    summary = _build(proof)
    assert summary["ok"] is False
    assert any("source_proof_id_mismatch" in b for b in summary["blockers"])


def test_non_bool_coverage_field_rejected() -> None:
    proof = _ready_sink_proof()
    proof["configured_sink_verifier_ok"] = "yes"
    summary = _build(proof)
    assert summary["ok"] is False
    assert any("configured_sink_verifier_ok_not_bool" in b for b in summary["blockers"])


def test_tripped_boundary_flag_rejected() -> None:
    proof = _ready_sink_proof()
    proof["runtime_authority_changed"] = True
    summary = _build(proof)
    assert summary["ok"] is False
    assert any("runtime_authority_changed_not_false" in b for b in summary["blockers"])


def test_default_off_violation_rejected() -> None:
    proof = _ready_sink_proof()
    proof["settings_default_enabled"] = True
    summary = _build(proof)
    assert summary["ok"] is False
    assert any("settings_default_enabled_not_false" in b for b in summary["blockers"])


def test_non_uint_receipt_count_rejected() -> None:
    proof = _ready_sink_proof()
    proof["configured_sink_receipt_count"] = "1"
    summary = _build(proof)
    assert summary["ok"] is False
    assert any(
        "configured_sink_receipt_count_unsafe" in b for b in summary["blockers"]
    )


def test_forbidden_marker_in_proof_rejected_without_echo() -> None:
    proof = _ready_sink_proof()
    proof["settings_out_dir"] = r"C:\private\DO_NOT_LEAK\receipts"
    summary = _build(proof)
    assert summary["ok"] is False
    serialized = json.dumps(summary, sort_keys=True)
    assert r"C:\private" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_unsafe_reviewer_agent_rejected() -> None:
    summary = build_runtime_receipt_settings_sink_reviewer_handoff_summary(
        sink_proof=_ready_sink_proof(),
        reviewer_agent_id="Not A Valid Agent!",
        handoff_ref="wd-ref",
    )
    assert summary["ok"] is False
    assert any("reviewer_agent_unsafe" in b for b in summary["blockers"])


def test_cli_json_is_path_free(tmp_path: Path) -> None:
    proof_path = tmp_path / "sink_proof.json"
    proof_path.write_text(json.dumps(_ready_sink_proof()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sink-proof-json",
            str(proof_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "wd-solver-first-runtime-receipt-sink",
            "--now",
            "2026-06-21T11:00:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["proof_id"] == PROOF_ID
    assert str(tmp_path) not in completed.stdout
    assert SOURCE_CONCLUSION not in completed.stdout
