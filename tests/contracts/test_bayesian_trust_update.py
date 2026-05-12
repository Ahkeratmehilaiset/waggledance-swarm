# SPDX-License-Identifier: BUSL-1.1
"""Bayesian trust update contract (L50, ADR-060)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "060-bayesian-trust-update.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "bayesian_trust_update.json"
REQUIRED_INVARIANT_IDS = {f"BTU-00{i}" for i in range(1, 8)}


def test_adr_060_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_distribution_beta() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["distribution"] == "Beta"


def test_prior_beta_2_2() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["prior"]["alpha"] == 2.0
    assert c["prior"]["beta"] == 2.0


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["credible_interval_pct"] == 95
    assert d["high_risk_uses_lower_ci"] is True


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_prior_mean_is_0_5() -> None:
    """Beta(2, 2) has mean 0.5 (weakly-informative neutral)."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    alpha = c["prior"]["alpha"]
    beta = c["prior"]["beta"]
    mean = alpha / (alpha + beta)
    assert mean == 0.5
