# SPDX-License-Identifier: BUSL-1.1
"""Latency consistency trust signal contract (L47, ADR-036)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "036-latency-consistency-trust-signal.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "latency_consistency_trust_signal.json"
)
REQUIRED_INVARIANT_IDS = {f"LCO-00{i}" for i in range(1, 8)}


def test_adr_036_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_036_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_field_name_is_latency_consistency() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["field_name"] == "latency_consistency"


def test_field_range_0_to_1() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["field_range"] == [0.0, 1.0]


def test_convention_higher_is_better() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["convention"] == "higher_is_better"


def test_formula_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["formula"] == "clamp(p50_ms / p99_ms, 0.0, 1.0)"


def test_policy_defaults_correct() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["rolling_window_hours"] == 24
    assert d["sample_size_n"] == 100
    assert d["min_samples_for_score"] == 20
    assert d["default_on_insufficient_data"] == 0.5
    assert d["refresh_cadence_seconds"] == 3600


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]
