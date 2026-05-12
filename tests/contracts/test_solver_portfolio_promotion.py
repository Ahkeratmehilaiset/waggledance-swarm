# SPDX-License-Identifier: BUSL-1.1
"""Solver-portfolio promotion contract (L23, ADR-048)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "048-solver-portfolio-promotion.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "solver_portfolio_promotion.json"
REQUIRED_INVARIANT_IDS = {f"SPP-00{i}" for i in range(1, 8)}


def test_adr_048_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["portfolio_n_default"] == 3
    assert d["portfolio_n_range"] == [1, 5]
    assert d["cottage_profile_n"] == 1


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_unanimous_for_high_risk() -> None:
    """SPP-004 enforces unanimous for high_risk (per ADR-027). One
    disagreement BLOCKS — no weighted-majority fallback at high stakes."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    spp004 = next((i for i in c["invariants"] if i["id"] == "SPP-004"), None)
    must_text = " ".join(spp004.get("must", [])).lower()
    assert "block" in must_text
    assert "unanimous" in must_text or "one disagreement" in must_text
