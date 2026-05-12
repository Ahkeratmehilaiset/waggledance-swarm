# SPDX-License-Identifier: BUSL-1.1
"""Risk-tiered L3 hydration budget contract (L15, ADR-027).

Substrate-only landing. Implementation of L3BudgetEnforcer is deferred.
Pins the tier enum + budget mapping + default tier + invariants so any
implementation cannot land with a different shape.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "027-risk-tiered-l3-budget.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "risk_tiered_l3_budget.json"
)
PROGRESSIVE_REPLAY_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "progressive_replay_l0_l4.json"
)


REQUIRED_TIERS = {"routine", "standard", "elevated", "high_risk"}
REQUIRED_BUDGETS = {
    "routine": 0,
    "standard": 2048,
    "elevated": 8192,
    "high_risk": 32768,
}
REQUIRED_INVARIANT_IDS = {
    "RTB-001",  # tier_enum_only
    "RTB-002",  # budget_mapping_fixed
    "RTB-003",  # routine_skips_L3
    "RTB-004",  # default_tier_is_standard
    "RTB-005",  # caller_declares
    "RTB-006",  # profile_override_scope
    "RTB-007",  # no_silent_escalation
}


def test_adr_027_file_exists() -> None:
    assert ADR_PATH.exists(), f"ADR-027 missing at {ADR_PATH}"


def test_adr_027_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower()


def test_adr_027_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-024", "ADR-026"):
        assert adr_id in text, (
            f"ADR-027 must reference {adr_id} (the L0-L4 strata + card schema "
            "+ L1 prefetch policy provide the substrate this L3 budget slots into)."
        )


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists(), f"Contract missing at {CONTRACT_PATH}"


def test_tier_enum_matches_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    tiers = set(contract.get("tier_enum", []))
    assert tiers == REQUIRED_TIERS, (
        f"tier_enum {sorted(tiers)} != required {sorted(REQUIRED_TIERS)}."
    )


def test_budget_mapping_exact_values() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    budgets = contract.get("tier_budgets_tokens", {})
    for tier, expected_tokens in REQUIRED_BUDGETS.items():
        assert budgets.get(tier) == expected_tokens, (
            f"Budget for {tier} MUST be {expected_tokens} (got {budgets.get(tier)}). "
            "These values are pinned per ADR-027; changing requires ADR amendment."
        )


def test_default_tier_is_standard() -> None:
    """Per RTB-004: default tier MUST be 'standard', NOT 'elevated'. This
    prevents accidental footprint expansion when callers forget to
    declare a tier."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    default = contract.get("default_tier")
    assert default == "standard", (
        f"default_tier MUST be 'standard' (got {default!r}). 'elevated' as "
        "default would silently expand L3 footprint across the codebase."
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


def test_glued_to_progressive_replay_contract() -> None:
    """L15 L3 budgets live inside the L0-L4 strata defined by ADR-021.
    The contract MUST declare the dependency."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    deps = contract.get("depends_on_contracts", [])
    assert "progressive_replay_l0_l4.json" in deps, (
        "L15 budget contract MUST declare dependency on the L11 progressive "
        "replay strata contract."
    )
    assert PROGRESSIVE_REPLAY_CONTRACT.exists()


def test_elevated_matches_adr_021_default() -> None:
    """The 'elevated' tier budget (8192) MUST match ADR-021's L3 default
    (8192 tokens). This is the cross-ADR glue: changing one without the
    other silently breaks the L0-L4 strata contract."""
    rtb = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    pr = json.loads(PROGRESSIVE_REPLAY_CONTRACT.read_text(encoding="utf-8"))
    elevated_budget = rtb["tier_budgets_tokens"]["elevated"]
    # Find L3 entry in progressive_replay_l0_l4.json levels list
    l3_max_tokens = None
    for level in pr.get("levels", []):
        if level.get("level") == "L3":
            l3_max_tokens = level.get("max_tokens")
            break
    assert l3_max_tokens == elevated_budget == 8192, (
        f"L3 budget pinning broken: ADR-021 L3 max_tokens={l3_max_tokens}, "
        f"ADR-027 elevated budget={elevated_budget}. Both MUST equal 8192. "
        "If you change one, you MUST change the other together."
    )


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope
