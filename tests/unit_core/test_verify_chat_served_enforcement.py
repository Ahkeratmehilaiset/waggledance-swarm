# SPDX-License-Identifier: BUSL-1.1
"""Adversarial + liveness tests for the P2 S1b chat-served sentinel enforcement
in ``tools.verify_magma_receipt`` (the claim-safety keystone).

A chat-served receipt runs no RCO/approval gate and has no solver contract, so
its ``rco_decision_digest`` / ``solver_contract_digest`` carry fixed N/A
sentinels. Those fields are schema-constrained to ``^sha256:[a-f0-9]{64}$``, so
a forged *real-looking* value is schema-valid; only the verifier requiring the
exact sentinel stops a chat path from masquerading a governed decision. These
tests exercise rco-2's adversarial matrix (forge -> reject; tamper the
self-describing declaration -> payload-binding fail; break the chain -> topology
fail) AND the liveness carve-out (a non-chat receipt with real governance
digests must remain untouched).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.verify_magma_receipt import (
    _CHAT_DIGEST_SEMANTICS,
    _CHAT_RCO_DECISION_NA_SENTINEL,
    _CHAT_ROUTE_STAGE_ORDER,
    _CHAT_SOLVER_CONTRACT_NA_SENTINEL,
    verify_manifest,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_served_receipt import (
    _DIGEST_SEMANTICS,
    _ROUTE_STAGE_ORDER,
    RCO_DECISION_NA_SENTINEL,
    SOLVER_CONTRACT_NA_SENTINEL,
    build_chat_served_summary,
    write_chat_served_receipt_bundle,
)
from waggledance.core.magma.runtime_summary_receipt import (
    build_handle_query_runtime_summary,
    write_runtime_summary_receipt_bundle,
)

_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _chat_summary():
    return build_chat_served_summary(
        query="cheapest hours today",
        response="hours 2,3,4",
        route_type="solver",
        source="solver",
        confidence=0.95,
        latency_ms=12.3,
        cached=False,
        round_table=False,
        agent_id=None,
        language="en",
        profile="COTTAGE",
        world_snapshot_ref="snap-1",
        route_stage_trace=[
            {
                "stage": "route_selection",
                "route_type": "solver",
                "solver_intent": "math",
                "memory_score": 0.0,
            }
        ],
    )


def _write_genuine_chat(out_dir: Path) -> dict:
    return write_chat_served_receipt_bundle(
        out_dir=out_dir,
        summary_payload=_chat_summary(),
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )


def _entry_file(out_dir: Path, kind: str) -> Path:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    return out_dir / manifest["entries"][0][kind]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ── anti-drift: the verifier re-derivation MUST equal the builder's constants ──


def test_verifier_sentinels_match_builder_constants() -> None:
    # The verifier re-derives the sentinels INDEPENDENTLY from the same
    # self-describing preimage; this proves the two derivations agree (a drift
    # would silently disable enforcement or reject genuine receipts).
    assert _CHAT_RCO_DECISION_NA_SENTINEL == RCO_DECISION_NA_SENTINEL
    assert _CHAT_SOLVER_CONTRACT_NA_SENTINEL == SOLVER_CONTRACT_NA_SENTINEL


def test_verifier_classifier_values_match_builder_contract() -> None:
    assert _CHAT_DIGEST_SEMANTICS == _DIGEST_SEMANTICS
    assert _CHAT_ROUTE_STAGE_ORDER == _ROUTE_STAGE_ORDER


# ── genuine chat receipt passes the enforcement ──


def test_genuine_chat_receipt_passes(tmp_path: Path) -> None:
    report = _write_genuine_chat(tmp_path / "chat")
    assert report["verifier_report"]["ok"] is True


# ── forge: a real-looking governance digest on a chat receipt is REJECTED ──


def test_forged_rco_decision_digest_rejected(tmp_path: Path) -> None:
    out = tmp_path / "chat"
    _write_genuine_chat(out)  # self-verifies clean
    rp = _entry_file(out, "receipt")
    receipt = _load(rp)
    # schema-valid, real-looking sha256 that is NOT the N/A sentinel
    receipt["rco_decision_digest"] = sha256_digest({"forged": "looks_governed"})
    _dump(rp, receipt)
    report = verify_manifest(out / "manifest.json")
    assert report["ok"] is False
    assert any(
        "rco_decision_digest must be the N/A sentinel" in e for e in report["errors"]
    )


def test_forged_solver_contract_digest_rejected(tmp_path: Path) -> None:
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    rp = _entry_file(out, "receipt")
    receipt = _load(rp)
    receipt["solver_contract_digest"] = sha256_digest({"forged": "solver_contract"})
    _dump(rp, receipt)
    report = verify_manifest(out / "manifest.json")
    assert report["ok"] is False
    assert any(
        "solver_contract_digest must be the N/A sentinel" in e
        for e in report["errors"]
    )


# ── a chat receipt claiming a non-informational risk_class is REJECTED ──


def test_chat_receipt_non_informational_risk_class_rejected(tmp_path: Path) -> None:
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    rp = _entry_file(out, "receipt")
    receipt = _load(rp)
    receipt["risk_class"] = "external_effect"
    _dump(rp, receipt)
    report = verify_manifest(out / "manifest.json")
    assert report["ok"] is False
    assert any("risk_class=informational" in e for e in report["errors"])


def test_chat_evaluation_cannot_claim_pass_or_carry_raw_text(tmp_path: Path) -> None:
    marker = "SECRET_RAW_EVALUATION_DETAIL"
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    ep = _entry_file(out, "evaluation_result")
    rp = _entry_file(out, "receipt")
    evaluation = _load(ep)
    receipt = _load(rp)
    evaluation["verdict"] = "pass"
    evaluation["uncertainty_sources"] = [
        {"kind": "unknown", "detail": marker}
    ]
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    _dump(ep, evaluation)
    _dump(rp, receipt)

    report = verify_manifest(out / "manifest.json")

    assert report["ok"] is False
    assert any("invalid chat_served evaluation_result" in e for e in report["errors"])
    assert marker not in json.dumps(report)


def test_chat_receipt_cannot_add_free_form_anchor(tmp_path: Path) -> None:
    marker = "SECRET_RAW_RECEIPT_ANCHOR"
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    rp = _entry_file(out, "receipt")
    receipt = _load(rp)
    receipt["anchored_at"] = marker
    _dump(rp, receipt)

    report = verify_manifest(out / "manifest.json")

    assert report["ok"] is False
    assert any("invalid chat_served receipt envelope" in e for e in report["errors"])
    assert marker not in json.dumps(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ts_utc", "2026-01-01T00:00:01Z"),
        ("event_id", "magma:chat_served:20260101T000001000000Z-000001"),
    ],
)
def test_chat_receipt_event_and_timestamp_must_match(
    tmp_path: Path, field: str, value: str
) -> None:
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    rp = _entry_file(out, "receipt")
    receipt = _load(rp)
    receipt[field] = value
    _dump(rp, receipt)

    report = verify_manifest(out / "manifest.json")

    assert report["ok"] is False
    assert any("timestamp mismatch" in e for e in report["errors"])


# ── tamper the self-describing declaration -> payload-binding fail ──


def test_tampered_digest_semantics_breaks_payload_binding(tmp_path: Path) -> None:
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    pp = _entry_file(out, "payload")
    payload = _load(pp)
    # lie that the rco_decision field is a real governed pass
    payload["digest_semantics"]["rco_decision_digest"] = "real:governed_pass"
    _dump(pp, payload)
    report = verify_manifest(out / "manifest.json")
    assert report["ok"] is False
    assert any("canonical_payload_digest mismatch" in e for e in report["errors"])


# ── break the hash-chain link -> topology fail ──


def test_broken_prev_receipt_hash_rejected(tmp_path: Path) -> None:
    out = tmp_path / "chat"
    _write_genuine_chat(out)
    rp = _entry_file(out, "receipt")
    receipt = _load(rp)
    # point the only receipt at a non-existent parent (no genesis remains)
    receipt["prev_receipt_hash"] = sha256_digest({"nonexistent": "parent"})
    _dump(rp, receipt)
    report = verify_manifest(out / "manifest.json")
    assert report["ok"] is False
    assert any(
        ("no_genesis" in e or "missing_parent" in e) for e in report["errors"]
    )


# ── LIVENESS: a non-chat receipt with REAL governance digests is UNTOUCHED ──


def test_liveness_non_chat_receipt_with_real_digests_passes(tmp_path: Path) -> None:
    summary = build_handle_query_runtime_summary(
        query="q",
        context={"a": 1},
        profile="COTTAGE",
        intent="eng01",
        quality_path="deterministic_solver",
        capability_id="cap-1",
        action_id="act-1",
        approved=True,
        executed=True,
        needs_approval=False,
        decision_reason="ok",
        elapsed_ms=5.0,
        snapshot_id="snap-1",
        case_id="case-1",
        verifier_passed=True,
        verifier_confidence=0.9,
        result_keys=["hours"],
    )
    out = tmp_path / "rt"
    report = write_runtime_summary_receipt_bundle(
        out_dir=out,
        summary_payload=summary,
        now_utc=_NOW,
        verify_manifest=verify_manifest,
    )
    # A non-chat receipt legitimately carries REAL rco_decision/solver_contract
    # digests and MUST still verify -- the chat enforcement must not over-reach.
    assert report["verifier_report"]["ok"] is True
    receipt = _load(_entry_file(out, "receipt"))
    assert receipt["rco_decision_digest"] != _CHAT_RCO_DECISION_NA_SENTINEL
    assert receipt["solver_contract_digest"] != _CHAT_SOLVER_CONTRACT_NA_SENTINEL
