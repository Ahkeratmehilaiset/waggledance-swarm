# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.runtime_summary_receipt import (
    EVALUATION_VERSION_V1,
    PAYLOAD_VERSION,
    build_handle_query_runtime_summary,
    write_runtime_summary_receipt_bundle,
)


def _summary(secret_query: str = "private query marker DO_NOT_LEAK") -> dict:
    return build_handle_query_runtime_summary(
        query=secret_query,
        context={"operator_note": "context secret DO_NOT_LEAK"},
        profile="DEFAULT",
        intent="diagnose",
        quality_path="gold",
        capability_id="detect.fixture",
        action_id="action123",
        approved=True,
        executed=True,
        needs_approval=False,
        decision_reason="Read-only auto-approved",
        elapsed_ms=12.345,
        snapshot_id="snap123",
        case_id="case:autonomy_runtime:fixture123",
        verifier_passed=True,
        verifier_confidence=0.8,
        result_keys=["intent", "approved", "result"],
        solver_call_trace=[
            {
                "stage": "solver_call",
                "status": "selected",
                "intent": "diagnose",
                "capability_id": "detect.fixture",
                "selected_index": 0,
                "quality_path": "gold",
                "execution_boundary": "safe_action_bus",
                "query": "private query marker DO_NOT_LEAK",
            }
        ],
    )


def _all_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.json"))
    )


def test_runtime_summary_payload_is_sanitized_and_digest_bound() -> None:
    summary = _summary()

    assert summary["payload_version"] == PAYLOAD_VERSION
    assert summary["actual_gate"] == "allow"
    assert summary["verdict"] == "pass"
    assert summary["query_digest"].startswith("sha256:")
    assert "DO_NOT_LEAK" not in json.dumps(summary, sort_keys=True)
    assert summary["context_keys"] == ["operator_note"]
    assert summary["solver_call_trace"] == [
        {
            "stage": "solver_call",
            "status": "selected",
            "intent": "diagnose",
            "capability_id": "detect.fixture",
            "selected_index": 0,
            "quality_path": "gold",
            "execution_boundary": "safe_action_bus",
        }
    ]
    assert summary["solver_call_trace_count"] == 1
    assert summary["solver_call_trace_digest"] == sha256_digest(
        {"solver_call_trace": summary["solver_call_trace"]}
    )


def test_runtime_summary_receipt_bundle_writes_and_verifies(tmp_path: Path) -> None:
    out_dir = tmp_path / "runtime-summary"

    report = write_runtime_summary_receipt_bundle(
        out_dir=out_dir,
        summary_payload=_summary(),
        now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
        verify_manifest=verify_manifest,
    )

    assert report["receipt_count"] == 1
    assert report["verifier_report"]["ok"] is True
    payload = json.loads(
        (out_dir / "payload-001-runtime-summary.json").read_text(encoding="utf-8")
    )
    evaluation = json.loads(
        (out_dir / "evaluation-001-runtime-summary.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (out_dir / "receipt-001-runtime-summary.json").read_text(encoding="utf-8")
    )
    assert evaluation["target_digest"] == receipt["canonical_payload_digest"]
    assert evaluation["evaluation_version"] == "magma.evaluation_result.v0"
    assert evaluation["case_id"] == payload["case_id"]
    assert evaluation["solver_selection"] == ["detect.fixture"]
    assert "solver_trace:receipt_bound" in evaluation["reason_codes"]
    assert "solver_call_trace_receipt_bound" in evaluation["verifier_path"]
    assert "DO_NOT_LEAK" not in _all_text(out_dir)


def test_runtime_summary_receipt_bundle_can_emit_v1_evaluation_result(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "runtime-summary-v1"

    report = write_runtime_summary_receipt_bundle(
        out_dir=out_dir,
        summary_payload=_summary(),
        now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
        verify_manifest=verify_manifest,
        evaluation_version=EVALUATION_VERSION_V1,
    )

    assert report["receipt_count"] == 1
    assert report["verifier_report"]["ok"] is True
    evaluation = json.loads(
        (out_dir / "evaluation-001-runtime-summary.json").read_text(encoding="utf-8")
    )
    assert evaluation["evaluation_version"] == EVALUATION_VERSION_V1
    assert evaluation["confidence_basis"] == {
        "method": "point_estimate",
        "methodology_reference": "runtime_summary_receipt_v0",
        "sample_count": 1,
    }
    assert evaluation["sanitization_audit"] == {
        "applied": ["reserved_domain_allowlist"],
        "redaction_count": 0,
    }
    assert evaluation["subject_payload_size_bytes"] > 0
    assert "DO_NOT_LEAK" not in _all_text(out_dir)


def test_runtime_summary_receipt_bundle_accepts_legacy_v0_without_trace_fields(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "runtime-summary-legacy-v0"
    summary = _summary()
    summary.pop("solver_call_trace")
    summary.pop("solver_call_trace_count")
    summary.pop("solver_call_trace_digest")

    report = write_runtime_summary_receipt_bundle(
        out_dir=out_dir,
        summary_payload=summary,
        now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
        verify_manifest=verify_manifest,
    )

    assert report["receipt_count"] == 1
    assert report["verifier_report"]["ok"] is True
    payload = json.loads(
        (out_dir / "payload-001-runtime-summary.json").read_text(encoding="utf-8")
    )
    assert payload["solver_call_trace"] == []
    assert payload["solver_call_trace_count"] == 0
    assert payload["solver_call_trace_digest"] == sha256_digest(
        {"solver_call_trace": []}
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("stage", "solver_call:unsafe", "stage is not allowed"),
        ("status", "fallback", "status is not allowed"),
        ("execution_boundary", "direct_runtime", "execution_boundary is not allowed"),
    ],
)
def test_runtime_summary_receipt_bundle_rejects_disallowed_solver_trace_values(
    tmp_path: Path,
    field: str,
    value: str,
    match: str,
) -> None:
    summary = _summary()
    summary["solver_call_trace"][0][field] = value
    summary["solver_call_trace_digest"] = sha256_digest(
        {"solver_call_trace": summary["solver_call_trace"]}
    )

    with pytest.raises(ValueError, match=match):
        write_runtime_summary_receipt_bundle(
            out_dir=tmp_path / f"bad-trace-{field}",
            summary_payload=summary,
            now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
            verify_manifest=verify_manifest,
        )

    assert not (tmp_path / f"bad-trace-{field}").exists()


def test_runtime_summary_receipt_bundle_rejects_unknown_evaluation_version(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="evaluation_version"):
        write_runtime_summary_receipt_bundle(
            out_dir=tmp_path / "bad-version",
            summary_payload=_summary(),
            now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
            verify_manifest=verify_manifest,
            evaluation_version="magma.evaluation_result.v99",
        )

    assert not (tmp_path / "bad-version").exists()


def test_runtime_summary_receipt_bundle_fails_closed_on_existing_dir(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        write_runtime_summary_receipt_bundle(
            out_dir=out_dir,
            summary_payload=_summary(),
            now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
            verify_manifest=verify_manifest,
        )


def test_runtime_summary_receipt_bundle_rejects_wrong_payload_version(
    tmp_path: Path,
) -> None:
    summary = _summary()
    summary["payload_version"] = "wrong.version"

    with pytest.raises(ValueError, match="payload_version"):
        write_runtime_summary_receipt_bundle(
            out_dir=tmp_path / "bad",
            summary_payload=summary,
            now_utc=datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc),
            verify_manifest=verify_manifest,
        )

    assert not (tmp_path / "bad").exists()
