# SPDX-License-Identifier: BUSL-1.1
"""Profile-aware budgets contract (L38, ADR-055)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "055-profile-aware-budgets.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "profile_aware_budgets.json"
REQUIRED_INVARIANT_IDS = {f"PAB-00{i}" for i in range(1, 8)}
REQUIRED_PROFILES = ["GADGET", "COTTAGE", "HOME", "FACTORY"]


def test_adr_055_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_profile_enum() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["profile_enum"] == REQUIRED_PROFILES


def test_budget_keys() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["budget_keys"]) == {"max_memory_mb", "max_replay_concurrency", "l1_prefetch_k", "l3_elevated_budget"}


def test_monotonic_across_profiles() -> None:
    """PAB-002: GADGET <= COTTAGE <= HOME <= FACTORY for each budget key."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    budgets = c["default_budgets"]
    for key in c["budget_keys"]:
        values = [budgets[p][key] for p in REQUIRED_PROFILES]
        for i in range(len(values) - 1):
            assert values[i] <= values[i + 1], (
                f"Budget {key} not monotone: {REQUIRED_PROFILES[i]}={values[i]} > "
                f"{REQUIRED_PROFILES[i+1]}={values[i+1]}"
            )


def test_yaml_path_pinned() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["yaml_path"] == "configs/profile_budgets.yaml"


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
