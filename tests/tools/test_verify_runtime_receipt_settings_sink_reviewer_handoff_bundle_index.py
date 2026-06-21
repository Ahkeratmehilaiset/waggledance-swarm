# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index import (
    build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index,
)
from tools.build_runtime_receipt_settings_sink_reviewer_handoff_summary import (
    BOUNDARY_FALSE_FIELDS,
    COVERAGE_BOOL_FIELDS,
    SOURCE_PROOF_ID,
    build_runtime_receipt_settings_sink_reviewer_handoff_summary,
)
from tools.verify_runtime_receipt_settings_sink_reviewer_handoff_bundle_index import (
    PROOF_ID,
    verify_runtime_receipt_settings_sink_reviewer_handoff_bundle_index,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "verify_runtime_receipt_settings_sink_reviewer_handoff_bundle_index.py"
)


def _ready_sink_proof() -> dict[str, object]:
    proof: dict[str, object] = {
        "proof_id": SOURCE_PROOF_ID,
        "ok": True,
        "configured_sink_receipt_count": 1,
    }
    for field in COVERAGE_BOOL_FIELDS:
        proof[field] = True
    for field in BOUNDARY_FALSE_FIELDS:
        proof[field] = False
    return proof


def _canon(mapping: dict[str, object]) -> bytes:
    return json.dumps(mapping, sort_keys=True, allow_nan=False).encode("utf-8")


def _artifacts():
    sink_proof = _ready_sink_proof()
    summary = build_runtime_receipt_settings_sink_reviewer_handoff_summary(
        sink_proof=sink_proof,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="wd-bundle",
        now_utc=datetime(2026, 6, 21, 11, 0, tzinfo=timezone.utc),
    )
    assert summary["ok"] is True
    sink_bytes = _canon(sink_proof)
    summary_bytes = _canon(summary)
    bundle_index = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
        sink_proof=sink_proof,
        reviewer_summary=summary,
        sink_proof_bytes=sink_bytes,
        summary_bytes=summary_bytes,
        now_utc=datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert bundle_index["ok"] is True
    return sink_proof, summary, sink_bytes, summary_bytes, bundle_index


def _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes):
    return verify_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        sink_proof=sink_proof,
        reviewer_summary=summary,
        sink_proof_bytes=sink_bytes,
        summary_bytes=summary_bytes,
    )


def test_verified_bundle_index_passes() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    report = _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is True
    assert report["proof_id"] == PROOF_ID
    assert report["rederivation_match"] is True
    assert report["claim_safe_unchanged"] is True
    assert report["blockers"] == []
    # different recorded created_at_utc than re-derivation must still verify.
    assert bundle_index["created_at_utc"] != "2026-01-01T00:00:00Z"


def test_tampered_artifact_digest_fails() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    bundle_index["artifacts"][0]["sha256"] = "0" * 64
    report = _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is False
    assert "bundle_index_does_not_match_rederivation" in report["blockers"]


def test_tampered_version_fails() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    bundle_index["bundle_index_version"] = "wrong.version.v9"
    report = _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is False
    assert "bundle_index_version_mismatch" in report["blockers"]


def test_claim_safe_flip_fails() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    bundle_index["operator_boundary"]["claim_safe_unchanged"] = False
    report = _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is False
    assert "claim_safe_unchanged_not_true" in report["blockers"]


def test_tampered_authority_flag_fails() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    bundle_index["operator_boundary"]["approval_granted"] = True
    report = _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is False
    assert any("approval_granted_not_false" in b for b in report["blockers"])


def test_forbidden_marker_in_bundle_index_rejected_without_echo() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    bundle_index["leaked"] = r"C:\private\DO_NOT_LEAK\bundle.json"
    report = _verify(bundle_index, sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is False
    serialized = json.dumps(report, sort_keys=True)
    assert r"C:\private" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_non_object_bundle_index_rejected() -> None:
    sink_proof, summary, sink_bytes, summary_bytes, _ = _artifacts()
    report = _verify(["not", "a", "mapping"], sink_proof, summary, sink_bytes, summary_bytes)
    assert report["ok"] is False
    assert any("not_object" in b for b in report["blockers"])


def test_cli_verifies_path_free(tmp_path: Path) -> None:
    sink_proof, summary, sink_bytes, summary_bytes, bundle_index = _artifacts()
    bundle_path = tmp_path / "bundle.json"
    sink_path = tmp_path / "sink.json"
    summary_path = tmp_path / "summary.json"
    bundle_path.write_text(json.dumps(bundle_index), encoding="utf-8")
    sink_path.write_bytes(sink_bytes)
    summary_path.write_bytes(summary_bytes)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bundle-index-json",
            str(bundle_path),
            "--sink-proof-json",
            str(sink_path),
            "--reviewer-summary-json",
            str(summary_path),
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
    assert payload["rederivation_match"] is True
    assert str(tmp_path) not in completed.stdout
