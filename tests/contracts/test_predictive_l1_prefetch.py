# SPDX-License-Identifier: BUSL-1.1
"""Predictive L1 prefetch contract (L14, ADR-026).

Substrate-only landing. Implementation of L1Prefetcher is deferred.
Pins the policy contract so implementation cannot land with a
different shape.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "026-predictive-l1-prefetch.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "predictive_l1_prefetch.json"
)
PROGRESSIVE_REPLAY_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "progressive_replay_l0_l4.json"
)


REQUIRED_INVARIANT_IDS = {
    "PLP-001",  # k_bounded_positive_integer
    "PLP-002",  # recency_window_bound
    "PLP-003",  # diversity_floor
    "PLP-004",  # decision_kind_coverage
    "PLP-005",  # no_history_recency_fallback
    "PLP-006",  # read_only_stats
    "PLP-007",  # missing_card_skip
}


def test_adr_026_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-026 missing at {ADR_PATH}"


def test_adr_026_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower(), (
        "ADR-026 must explicitly mark this as substrate-only landing."
    )


def test_adr_026_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-024"):
        assert adr_id in text, (
            f"ADR-026 must reference {adr_id} (progressive replay parent + card schema)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), f"Contract missing at {CONTRACT_PATH}"


def test_policy_defaults_have_expected_values() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    defaults = contract.get("policy_defaults", {})
    assert defaults.get("prefetch_k") == 100, (
        f"prefetch_k default MUST be 100 (got {defaults.get('prefetch_k')}). "
        "Per ADR-026."
    )
    assert defaults.get("recency_window_hours") == 24
    assert defaults.get("same_subject_max_share") == 0.10


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


def test_glued_to_progressive_replay_contract() -> None:
    """L14 prefetch policy lives inside the L1 strata defined by ADR-021.
    The contract MUST declare the dependency so cross-ADR changes are
    coordinated."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    deps = contract.get("depends_on_contracts", [])
    assert "progressive_replay_l0_l4.json" in deps, (
        "L14 prefetch contract MUST declare dependency on the L11 "
        "progressive replay strata contract (progressive_replay_l0_l4.json)."
    )
    # Sanity check: the base contract MUST exist on disk
    assert PROGRESSIVE_REPLAY_CONTRACT.exists(), (
        f"Declared base contract {PROGRESSIVE_REPLAY_CONTRACT} missing."
    )


def test_l1_prefetch_within_l1_strata_budget() -> None:
    """The L1 strata budget per ADR-021 is 512 tokens per card. Default
    prefetch_k * 512 tokens = ~50KB working set. This is a sanity check
    that the policy default is operationally reasonable."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    k = contract["policy_defaults"]["prefetch_k"]
    # 100 cards * 512 tokens * ~1 byte/token = ~50KB. Sanity-only.
    estimated_kb = (k * 512) / 1024
    assert estimated_kb < 1024, (
        f"Default prefetch_k={k} implies ~{estimated_kb:.0f}KB L1 working set, "
        "which exceeds the 1MB sanity bound. Reconsider the default."
    )


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope
