# SPDX-License-Identifier: BUSL-1.1
"""Cross-validation score contract (L46, ADR-058)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "058-cross-validation-score.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "cross_validation_score.json"
REQUIRED_INVARIANT_IDS = {f"CVS-00{i}" for i in range(1, 8)}


def test_adr_058_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_field_name() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["field_name"] == "cross_validation_score"


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["probe_sample_size"] == 50
    assert d["min_distinct_domains"] == 5
    assert d["exclude_own_domain"] is True
    assert d["default_on_insufficient"] == 0.5
    assert d["refresh_cadence_seconds"] == 604800


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
