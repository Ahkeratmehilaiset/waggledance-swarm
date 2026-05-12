# SPDX-License-Identifier: BUSL-1.1
"""Curiosity-gradient routing contract (L6, ADR-043)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "043-curiosity-gradient-routing.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "curiosity_gradient_routing.json"
REQUIRED_INVARIANT_IDS = {f"CGR-00{i}" for i in range(1, 8)}


def test_adr_043_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_043_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_defaults_correct() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["policy_defaults"]["curiosity_weight"] == 0.15
    assert c["policy_defaults"]["curiosity_weight_max"] == 0.20
    assert c["policy_defaults"]["hot_path_budget_us"] == 5


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
