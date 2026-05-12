# SPDX-License-Identifier: BUSL-1.1
"""Forensic snapshot rotation contract (L19, ADR-022).

This is a substrate-only contract landing. The implementation
(MagmaSnapshotEngine) is deferred to a follow-up PR. This file pins
the SHAPE of the contract:

* ADR-022 exists and contains all 6 required invariants
* Machine-readable contract JSON exists with required schema
* When MagmaSnapshotEngine lands, this test file will get behavioral
  assertions added; until then the structural contract is locked in
  so any implementation PR cannot land without acknowledging it.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "022-forensic-snapshot-rotation.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "forensic_snapshot_rotation.json"
)


REQUIRED_INVARIANT_IDS = {
    "FSR-001",  # raw chain authoritative
    "FSR-002",  # snapshot boundary hash
    "FSR-003",  # fail-closed fallback
    "FSR-004",  # replay equivalence
    "FSR-005",  # no write amplification
    "FSR-006",  # rotation is policy not code
}


def test_adr_022_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-022 file missing at {ADR_PATH}"


def test_adr_022_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower(), (
        "ADR-022 must explicitly mark this as substrate-only landing "
        "(implementation deferred) so future readers understand why "
        "no MagmaSnapshotEngine exists yet."
    )


def test_adr_022_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    # MAGMA forensic replay is governed by the ADR-011/014/021 chain.
    for adr_id in ("ADR-011", "ADR-014", "ADR-021"):
        assert adr_id in text, (
            f"ADR-022 must reference {adr_id} (the related ADRs in the "
            "MAGMA progressive-replay / write-storm chain)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), (
        f"Machine-readable contract missing at {CONTRACT_PATH}. "
        "Per the L11 pattern (ADR-021), every substrate ADR ships with a "
        "JSON contract file that downstream code can validate against."
    )


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    invariants = contract.get("invariants", [])
    ids = {item.get("id") for item in invariants}
    missing = REQUIRED_INVARIANT_IDS - ids
    extra = ids - REQUIRED_INVARIANT_IDS
    assert not missing, (
        f"Contract is missing required invariants: {sorted(missing)}. "
        "Each invariant in ADR-022 must have a matching entry in the "
        "machine-readable contract."
    )
    assert not extra, (
        f"Contract has extra invariants not in REQUIRED_INVARIANT_IDS: "
        f"{sorted(extra)}. Update this test if the addition is intentional."
    )


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract.get("invariants", []):
        inv_id = item.get("id")
        must = item.get("must")
        assert isinstance(must, list) and must, (
            f"Invariant {inv_id} has no 'must' clauses -- every contract "
            "invariant must list at least one concrete MUST condition that "
            "implementations can be checked against."
        )


def test_contract_marks_out_of_scope() -> None:
    """The substrate-only landing MUST explicitly enumerate what is NOT
    being contracted yet. Future PRs that implement MagmaSnapshotEngine
    or compression can lift items out of this list."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope, (
        "Contract must list out_of_scope items so the substrate boundary "
        "is explicit. Empty list defeats the substrate-only pattern."
    )
