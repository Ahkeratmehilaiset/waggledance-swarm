# SPDX-License-Identifier: BUSL-1.1
"""Provenance tip cache contract (L20, ADR-023).

Substrate-only landing. Implementation of _TipCache is deferred to a
follow-up PR. This file pins the SHAPE of the contract:

* ADR-023 exists and contains all 7 required invariants
* Machine-readable contract JSON exists with required schema
* When _TipCache lands, this file gains behavioral tests; until then
  the structural contract locks in so no implementation PR can land
  without acknowledging it.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "023-provenance-tip-cache.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "provenance_tip_cache.json"
)


REQUIRED_INVARIANT_IDS = {
    "PTC-001",  # tracker_authoritative
    "PTC-002",  # L0_supersedes_L1
    "PTC-003",  # ttl_bound
    "PTC-004",  # lru_bounded
    "PTC-005",  # no_write_amplification
    "PTC-006",  # boot_preload_optional
    "PTC-007",  # invalidation_hook
}

REQUIRED_TIERS = {"L0", "L1", "L2"}


def test_adr_023_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-023 file missing at {ADR_PATH}"


def test_adr_023_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower(), (
        "ADR-023 must explicitly mark this as substrate-only landing "
        "(implementation deferred)."
    )


def test_adr_023_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-022"):
        assert adr_id in text, (
            f"ADR-023 must reference {adr_id} (the chain: progressive replay + "
            "forensic snapshot rotation provide the context this provenance "
            "cache slots into)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), (
        f"Machine-readable contract missing at {CONTRACT_PATH}."
    )


def test_contract_tiers_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    tiers = {t.get("tier") for t in contract.get("tiers", [])}
    assert tiers == REQUIRED_TIERS, (
        f"Contract tier set {sorted(tiers)} does not match required "
        f"{sorted(REQUIRED_TIERS)}."
    )


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item.get("id") for item in contract.get("invariants", [])}
    missing = REQUIRED_INVARIANT_IDS - ids
    extra = ids - REQUIRED_INVARIANT_IDS
    assert not missing, f"Contract missing invariants: {sorted(missing)}"
    assert not extra, (
        f"Contract has invariants not in REQUIRED set: {sorted(extra)}. "
        "Update this test if intentional."
    )


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract.get("invariants", []):
        inv_id = item.get("id")
        must = item.get("must")
        assert isinstance(must, list) and must, (
            f"Invariant {inv_id} has no 'must' clauses."
        )


def test_lookup_order_is_l0_l1_l2() -> None:
    """Lookup order MUST be hot-then-warm-then-cold. Reordering would
    defeat the cache hierarchy (L1 must NOT be consulted before L0;
    L2 must NOT be consulted before L1)."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract.get("lookup_order") == ["L0", "L1", "L2"], (
        "Lookup order is L0 (hot dict) -> L1 (warm TTL) -> L2 (cold tracker). "
        "Reordering is a contract violation."
    )


def test_l1_has_ttl_and_bounded_size() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    tiers = {t.get("tier"): t for t in contract.get("tiers", [])}
    l1 = tiers["L1"]
    assert l1["ttl_seconds"] == 5.0, (
        f"L1 TTL must be 5.0 seconds (got {l1.get('ttl_seconds')})."
    )
    assert l1["max_entries"] == 1000, (
        f"L1 max_entries must be 1000 (got {l1.get('max_entries')})."
    )


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope, (
        "Contract must list out_of_scope items so substrate boundary is explicit."
    )
