# SPDX-License-Identifier: BUSL-1.1
"""Trust-staged routing contract (L8, ADR-045)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "045-trust-staged-routing.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "trust_staged_routing.json"
REQUIRED_INVARIANT_IDS = {f"TSR-00{i}" for i in range(1, 8)}


def test_adr_045_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_stage_enum() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["stage_enum"]) == {"direct", "broad", "verified"}


def test_thresholds() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["stage_thresholds"]["direct_min"] == 0.80
    assert c["stage_thresholds"]["broad_min"] == 0.50


def test_k_mapping() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    m = c["stage_k_mapping"]
    assert m["direct"] == 1
    assert m["broad"] == 3
    assert m["verified"] == 5


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
