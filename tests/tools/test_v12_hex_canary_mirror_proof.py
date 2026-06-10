# SPDX-License-Identifier: Apache-2.0
"""Receipted hex-canary proof: fixture through the real runtime path."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.run_v12_hex_canary_mirror_proof import (
    CLAIM_BOUNDARY,
    CLAIM_LABEL,
    PRIVACY_SENTINEL,
    REPORT_VERSION,
    build_hex_canary_mirror_proof,
    main,
)


def _build(tmp_path: Path) -> dict:
    return build_hex_canary_mirror_proof(
        out_dir=tmp_path / "canary-proof",
        now_utc=datetime(2026, 6, 10, 6, 30, tzinfo=timezone.utc),
    )


def test_proof_is_green_with_receipted_chain(tmp_path: Path) -> None:
    proof = _build(tmp_path)

    assert proof["report_version"] == REPORT_VERSION
    assert proof["ok"] is True
    assert proof["claim_label"] == CLAIM_LABEL
    assert proof["claim_boundary"] == CLAIM_BOUNDARY
    assert all(value is True for value in proof["conditions"].values())
    assert proof["receipt_chain_verified"] is True
    assert proof["receipt_bundle"]["verifier_report"]["ok"] is True
    assert proof["receipt_bundle"]["receipt_count"] == 1


def test_canary_report_covers_all_three_fixture_groups(tmp_path: Path) -> None:
    proof = _build(tmp_path)
    report = proof["canary_mirror_report"]

    assert report["sample_count"] == proof["fixture_query_count"] == 24
    by_classification = report["by_classification"]
    # real intent classifier at work: 8 math (solver path), 8 chat-shaped
    # queries whose keyword evidence diverges to the energy cell, 8 plain
    # chat agreeing with the intent cell
    assert by_classification["divergent_keyword_override"] == 8
    assert by_classification["match_intent_cell"] == 16
    assert report["by_mesh_cell"]["energy"] == 8
    assert report["by_mesh_cell"]["math"] == 8
    assert report["by_mesh_cell"]["general"] == 8
    assert report["by_mesh_method"] == {"intent": 16, "keyword": 8}
    assert report["agreement_rate"] == 16 / 24
    # read-only contract on the embedded mirror report
    assert report["no_runtime_mutation"] is True
    assert report["runtime_authority_granted"] is False
    assert report["routing_influence_applied"] is False


def test_privacy_sentinel_never_reaches_emitted_artifacts(
    tmp_path: Path,
) -> None:
    proof = _build(tmp_path)

    assert PRIVACY_SENTINEL not in json.dumps(proof, default=str)
    emitted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "canary-proof").rglob("*.json"))
    )
    assert emitted  # bundle files were written
    assert PRIVACY_SENTINEL not in emitted


def test_no_overclaim_guardrails_are_literal(tmp_path: Path) -> None:
    proof = _build(tmp_path)
    guardrails = proof["no_overclaim_guardrails"]

    assert guardrails["not_production_traffic"] is True
    assert guardrails["production_enablement_is_operator_decision"] is True
    assert guardrails["claim_gate_satisfied"] is False
    assert guardrails["claim_safe"] is False
    assert guardrails["literal_future_claim_safe"] is False
    assert guardrails["runtime_authority_granted"] is False
    assert guardrails["external_writes_applied"] is False


def test_cli_main_exits_zero(tmp_path: Path, capsys) -> None:
    exit_code = main(["--out-dir", str(tmp_path / "cli-run"), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
