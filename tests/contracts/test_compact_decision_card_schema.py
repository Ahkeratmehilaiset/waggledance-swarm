# SPDX-License-Identifier: BUSL-1.1
"""Compact decision card schema contract (L12, ADR-024).

Substrate-only landing. Implementation of CompactCardWriter and
CompactCardStore is deferred. This file pins the SHAPE of the contract:

* ADR-024 exists and contains all 7 required invariants
* Machine-readable contract JSON exists with required schema
* When CompactCardWriter lands, this file gains behavioral tests; until
  then the structural contract locks in so no implementation PR can
  land without acknowledging it.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "024-compact-decision-card-schema.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "compact_decision_card.json"
)


REQUIRED_INVARIANT_IDS = {
    "CDC-001",  # stable_schema_name
    "CDC-002",  # required_fields_present
    "CDC-003",  # card_hash_deterministic
    "CDC-004",  # source_event_hash_anchoring
    "CDC-005",  # immutability
    "CDC-006",  # bounded_summary
    "CDC-007",  # allowed_decision_kinds
}

REQUIRED_FIELD_NAMES = {
    "card_id",
    "card_version",
    "supersedes_card_id",
    "source_event_hash",
    "produced_at_utc",
    "producer",
    "decision_kind",
    "subject_id",
    "summary",
    "card_hash",
}

REQUIRED_DECISION_KINDS = {
    "solver_dispatch",
    "promotion",
    "rollback",
    "snapshot_anchor",
    "gap_signal",
    "capability_bind",
}


def test_adr_024_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-024 missing at {ADR_PATH}"


def test_adr_024_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower(), (
        "ADR-024 must explicitly mark this as substrate-only landing."
    )


def test_adr_024_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-011", "ADR-014", "ADR-021", "ADR-023"):
        assert adr_id in text, (
            f"ADR-024 must reference {adr_id} (the substrate chain: write-storm "
            "breaker, queue+backpressure, progressive replay, provenance cache)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), f"Contract missing at {CONTRACT_PATH}"


def test_card_version_constant_is_v1() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract.get("card_version_constant") == "compact-card-v1", (
        "Card version constant MUST be 'compact-card-v1'. Bumping requires "
        "a new ADR and contract version bump (see CDC-001)."
    )


def test_required_field_set_matches() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    fields = set(contract.get("required_fields", []))
    missing = REQUIRED_FIELD_NAMES - fields
    extra = fields - REQUIRED_FIELD_NAMES
    assert not missing, f"Contract missing required fields: {sorted(missing)}"
    assert not extra, f"Contract has extra fields not in spec: {sorted(extra)}"


def test_allowed_decision_kinds_match() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    kinds = set(contract.get("allowed_decision_kinds", []))
    missing = REQUIRED_DECISION_KINDS - kinds
    extra = kinds - REQUIRED_DECISION_KINDS
    assert not missing, f"Missing decision_kinds: {sorted(missing)}"
    assert not extra, (
        f"Extra decision_kinds {sorted(extra)} -- add to REQUIRED_DECISION_KINDS "
        "in this test AND ensure ADR-024 lists them in 'Allowed decision_kinds'."
    )


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item.get("id") for item in contract.get("invariants", [])}
    missing = REQUIRED_INVARIANT_IDS - ids
    extra = ids - REQUIRED_INVARIANT_IDS
    assert not missing, f"Missing invariants: {sorted(missing)}"
    assert not extra, f"Extra invariants: {sorted(extra)}"


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract.get("invariants", []):
        must = item.get("must")
        assert isinstance(must, list) and must, (
            f"Invariant {item.get('id')} has no 'must' clauses."
        )


def test_size_limits_match_l1_budget() -> None:
    """The 512-token total card budget MUST match the L1 strata budget
    pinned in ADR-021 (progressive_replay_l0_l4.json). Drifting the two
    values apart silently breaks L1 boot prefetch invariants."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    limits = contract.get("size_limits", {})
    assert limits.get("card_total_max_tokens") == 512, (
        "card_total_max_tokens MUST equal 512 (the L1 strata budget per ADR-021). "
        "If you need to change this, update ADR-021 / progressive_replay_l0_l4.json "
        "and ADR-024 TOGETHER -- they are pinned to each other."
    )
    assert limits.get("summary_max_keys") == 8
    assert limits.get("summary_value_max_bytes") == 256


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope, (
        "Contract must list out_of_scope items."
    )
