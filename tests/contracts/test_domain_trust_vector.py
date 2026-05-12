# SPDX-License-Identifier: BUSL-1.1
"""Domain-specific trust vector contract (L48, ADR-059)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "059-domain-specific-trust-vector.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "domain_trust_vector.json"
REQUIRED_INVARIANT_IDS = {f"DTV-00{i}" for i in range(1, 8)}


def test_adr_059_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_field_name_domain_trust() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["field_name"] == "domain_trust"


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["default_value"] == {}
    assert d["ema_alpha"] == 0.10
    assert d["max_keys"] == 10


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
