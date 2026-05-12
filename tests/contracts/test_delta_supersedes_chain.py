# SPDX-License-Identifier: BUSL-1.1
"""Delta-encoded supersedes chain contract (L13, ADR-025).

Substrate-only landing. Implementation of CompactCardChain walker is
deferred. This file pins the SHAPE of the contract:

* ADR-025 exists and contains all 7 required invariants
* Machine-readable contract JSON exists with delta_mode enum + chain
  depth bound + field requirements
* Glued to ADR-024 (compact decision card schema) via
  depends_on_contracts -- changing the base card schema requires
  updating both contracts together.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "025-delta-encoded-supersedes-chain.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "delta_supersedes_chain.json"
)
BASE_CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "compact_decision_card.json"
)


REQUIRED_INVARIANT_IDS = {
    "DSC-001",  # delta_mode_enum_only
    "DSC-002",  # first_card_is_full
    "DSC-003",  # partial_requires_supersedes
    "DSC-004",  # chain_depth_bound
    "DSC-005",  # tombstone_terminates
    "DSC-006",  # per_card_hash
    "DSC-007",  # delta_removed_keys_required
}

REQUIRED_DELTA_MODES = {"full", "partial", "tombstone"}

REQUIRED_ADDED_FIELD_NAMES = {"delta_mode", "delta_removed_keys"}


def test_adr_025_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-025 missing at {ADR_PATH}"


def test_adr_025_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower(), (
        "ADR-025 must explicitly mark this as substrate-only landing."
    )


def test_adr_025_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-022", "ADR-024"):
        assert adr_id in text, (
            f"ADR-025 must reference {adr_id} (the chain: progressive replay + "
            "forensic snapshot rotation + compact card schema provide the "
            "substrate this delta-chain extends)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), f"Contract missing at {CONTRACT_PATH}"


def test_delta_mode_enum_matches() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    modes = set(contract.get("delta_mode_enum", []))
    assert modes == REQUIRED_DELTA_MODES, (
        f"delta_mode_enum {sorted(modes)} != required {sorted(REQUIRED_DELTA_MODES)}."
    )


def test_added_fields_match() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    field_names = {f.get("name") for f in contract.get("added_fields", [])}
    missing = REQUIRED_ADDED_FIELD_NAMES - field_names
    extra = field_names - REQUIRED_ADDED_FIELD_NAMES
    assert not missing, f"Missing added fields: {sorted(missing)}"
    assert not extra, f"Unexpected added fields: {sorted(extra)}"


def test_chain_max_depth_is_64() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract.get("chain_max_depth") == 64, (
        f"chain_max_depth MUST be 64 (got {contract.get('chain_max_depth')}). "
        "Changing this requires updating both ADR-025 and this test."
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


def test_glued_to_compact_card_schema_contract() -> None:
    """L13 depends on L12's compact-card-v1 schema. The contract MUST
    declare the dependency so future schema changes are coordinated.
    Catches drift between the base schema and the delta-chain semantics."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    deps = contract.get("depends_on_contracts", [])
    assert "compact_decision_card.json" in deps, (
        "L13 delta-chain contract MUST declare dependency on the L12 base "
        "compact-card-v1 schema contract (compact_decision_card.json)."
    )
    # Sanity: the base contract MUST exist on disk for the dependency to be real
    assert BASE_CONTRACT_PATH.exists(), (
        f"Declared base contract {BASE_CONTRACT_PATH} missing. The L13 "
        "dependency is dangling."
    )


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope, (
        "Contract must list out_of_scope items."
    )
