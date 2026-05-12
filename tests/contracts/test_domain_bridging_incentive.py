# SPDX-License-Identifier: BUSL-1.1
"""Domain-bridging incentive contract (L26, ADR-050)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "050-domain-bridging-incentive.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "domain_bridging_incentive.json"
REQUIRED_INVARIANT_IDS = {f"DBI-00{i}" for i in range(1, 8)}


def test_adr_050_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["domain_bridge_multiplier"] == 1.5
    assert d["multiplier_range"] == [1.0, 2.0]
    assert d["min_examples_per_domain"] == 10
    assert d["min_distinct_domains"] == 2


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
