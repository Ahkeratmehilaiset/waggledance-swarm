# SPDX-License-Identifier: BUSL-1.1
"""Temporal trust decay contract (L49, ADR-037)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "037-temporal-trust-decay.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "temporal_trust_decay.json"
REQUIRED_INVARIANT_IDS = {f"TTD-00{i}" for i in range(1, 8)}


def test_adr_037_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_037_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_composition_mode_is_multiplicative() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["composition_mode"] == "multiplicative"


def test_decay_formula_is_exponential() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["decay_formula"] == "0.5 ** (age_days / half_life_days)"


def test_half_life_default_and_range() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["half_life_days"] == 7.0
    assert d["half_life_range_days"] == [1.0, 90.0]


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_decay_at_half_life_equals_0_5() -> None:
    """Sanity: the exponential decay formula gives 0.5 at age=half_life."""
    half_life_days = 7.0
    age_days = 7.0
    decay = 0.5 ** (age_days / half_life_days)
    assert decay == 0.5


def test_decay_at_double_half_life_equals_0_25() -> None:
    """Sanity: age = 2 * half_life -> decay = 0.25."""
    half_life_days = 7.0
    age_days = 14.0
    decay = 0.5 ** (age_days / half_life_days)
    assert decay == 0.25
