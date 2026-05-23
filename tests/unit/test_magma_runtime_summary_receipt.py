# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.runtime_summary_receipt import (
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
    assert evaluation["case_id"] == payload["case_id"]
    assert "DO_NOT_LEAK" not in _all_text(out_dir)


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
