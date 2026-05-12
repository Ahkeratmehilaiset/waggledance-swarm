# SPDX-License-Identifier: BUSL-1.1
"""Multi-objective promotion contract (L28, ADR-052)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "052-multi-objective-promotion.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "multi_objective_promotion.json"
REQUIRED_INVARIANT_IDS = {f"MOP-00{i}" for i in range(1, 8)}
REQUIRED_AXES = {"accuracy", "latency", "breadth", "novelty"}


def test_adr_052_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_axes_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["axes"]) == REQUIRED_AXES


def test_default_weights_sum_to_1() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    weights = c["default_weights"]
    assert weights["accuracy"] == 0.50
    assert weights["latency"] == 0.20
    assert weights["breadth"] == 0.15
    assert weights["novelty"] == 0.15
    assert sum(weights.values()) == 1.0


def test_accuracy_majority() -> None:
    """MOP-005: accuracy must hold at least half the total weight."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    w = c["default_weights"]
    assert w["accuracy"] >= 0.50


def test_caps() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    caps = c["axis_caps"]
    assert caps["breadth_max_domains"] == 5
    assert caps["novelty_max_intent_classes"] == 10


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
