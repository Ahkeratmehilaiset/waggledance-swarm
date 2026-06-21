# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index import (
    BUNDLE_INDEX_VERSION,
    PROOF_ID,
    build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index,
)
from tools.build_runtime_receipt_settings_sink_reviewer_handoff_summary import (
    SOURCE_PROOF_ID,
    build_runtime_receipt_settings_sink_reviewer_handoff_summary,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index.py"
)
FIXED_NOW = datetime(2026, 6, 21, 13, 20, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")
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
        "safe_conclusion": SOURCE_CONCLUSION,
        "settings_out_dir": SOURCE_OUT_DIR,
        "settings_evaluation_version": "magma.evaluation_result.v0",
    }


def _summary(source: dict[str, object]) -> dict[str, object]:
    return build_runtime_receipt_settings_sink_reviewer_handoff_summary(
        sink_proof=source,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="wd-solver-first-runtime-receipt-sink",
        now_utc=FIXED_NOW,
    )


def test_bundle_index_ties_source_and_summary_digests_without_authority() -> None:
    source = _ready_sink_proof()
    summary = _summary(source)
    source_bytes = _json_bytes(source)
    summary_bytes = _json_bytes(summary)

    index = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
        sink_proof=source,
        reviewer_summary=summary,
        sink_proof_bytes=source_bytes,
        summary_bytes=summary_bytes,
        now_utc=FIXED_NOW,
    )

    assert index["ok"] is True
    assert index["proof_id"] == PROOF_ID
    assert index["bundle_index_version"] == BUNDLE_INDEX_VERSION
    assert index["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in index["artifacts"]}
    assert by_id["source_sink_proof"]["sha256"] == _sha256(source_bytes)
    assert by_id["reviewer_handoff_summary"]["sha256"] == _sha256(summary_bytes)
    assert all(item["payload_included"] is False for item in index["artifacts"])
    assert all(item["local_path_recorded"] is False for item in index["artifacts"])
    assert index["consistency"]["source_summary_binding"] is True
    assert index["consistency"]["source_summary_coverage_match"] is True
    assert index["operator_boundary"]["manual_review_required"] is True
    assert index["operator_boundary"]["claim_safe_unchanged"] is True
    assert index["operator_boundary"]["approval_granted"] is False
    assert index["operator_boundary"]["runtime_authority_granted"] is False
    assert index["operator_boundary"]["default_runtime_receipt_emission_changed"] is False
    assert index["operator_boundary"]["direct_bridge_write_performed"] is False


def test_cli_json_is_path_free(tmp_path: Path) -> None:
    source = _ready_sink_proof()
    summary = _summary(source)
    source_path = tmp_path / "sink_proof.json"
    summary_path = tmp_path / "reviewer_summary.json"
    source_path.write_bytes(_json_bytes(source))
    summary_path.write_bytes(_json_bytes(summary))

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sink-proof-json",
            str(source_path),
            "--summary-json",
            str(summary_path),
            "--now",
            "2026-06-21T13:20:00Z",
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
    assert payload["artifact_count"] == 2
    assert payload["operator_boundary"]["runtime_authority_granted"] is False
    assert str(tmp_path) not in completed.stdout
    assert source_path.name not in completed.stdout
    assert summary_path.name not in completed.stdout
    assert SOURCE_CONCLUSION not in completed.stdout
    assert SOURCE_OUT_DIR not in completed.stdout
    assert not any(marker in completed.stdout for marker in PRIVATE_MARKERS)


def test_bundle_index_rejects_summary_receipt_count_mismatch_path_free() -> None:
    source = _ready_sink_proof()
    summary = _summary(source)
    summary = copy.deepcopy(summary)
    summary["configured_sink"]["receipt_count"] = 2

    index = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
        sink_proof=source,
        reviewer_summary=summary,
        sink_proof_bytes=_json_bytes(source),
        summary_bytes=_json_bytes(summary),
        now_utc=FIXED_NOW,
    )

    assert index["ok"] is False
    assert index["blockers"] == [
        "runtime_receipt_sink_reviewer_bundle_index_failed:"
        "summary_receipt_count_mismatch"
    ]
    serialized = json.dumps(index, sort_keys=True)
    assert SOURCE_CONCLUSION not in serialized
    assert SOURCE_OUT_DIR not in serialized


def test_bundle_index_rejects_summary_authority_flip() -> None:
    source = _ready_sink_proof()
    summary = _summary(source)
    summary = copy.deepcopy(summary)
    summary["runtime_authority_granted"] = True

    index = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
        sink_proof=source,
        reviewer_summary=summary,
        sink_proof_bytes=_json_bytes(source),
        summary_bytes=_json_bytes(summary),
        now_utc=FIXED_NOW,
    )

    assert index["ok"] is False
    assert index["runtime_authority_granted"] is False
    assert index["blockers"] == [
        "runtime_receipt_sink_reviewer_bundle_index_failed:"
        "summary_runtime_authority_granted_not_false"
    ]


def test_bundle_index_rejects_bytes_that_do_not_match_validated_mapping() -> None:
    source = _ready_sink_proof()
    summary = _summary(source)
    tampered_source_for_digest = copy.deepcopy(source)
    tampered_source_for_digest["configured_sink_receipt_count"] = 7

    index = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
        sink_proof=source,
        reviewer_summary=summary,
        sink_proof_bytes=_json_bytes(tampered_source_for_digest),
        summary_bytes=_json_bytes(summary),
        now_utc=FIXED_NOW,
    )

    assert index["ok"] is False
    assert index["blockers"] == [
        "runtime_receipt_sink_reviewer_bundle_index_failed:"
        "source_sink_proof_bytes_mismatch"
    ]
    assert index["artifact_payloads_included"] is False
    assert index["local_paths_recorded"] is False


def test_missing_input_failure_does_not_echo_path(tmp_path: Path) -> None:
    source = _ready_sink_proof()
    summary = _summary(source)
    summary_path = tmp_path / "reviewer_summary.json"
    summary_path.write_bytes(_json_bytes(summary))

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sink-proof-json",
            "C:/private/runtime_receipts/sink_proof.json",
            "--summary-json",
            str(summary_path),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert "runtime_receipts" not in completed.stdout
    assert str(tmp_path) not in completed.stdout
    assert not any(marker in completed.stdout for marker in PRIVATE_MARKERS)


def _json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
