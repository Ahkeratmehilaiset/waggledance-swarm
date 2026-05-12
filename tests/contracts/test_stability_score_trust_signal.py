# SPDX-License-Identifier: BUSL-1.1
"""Stability score trust signal contract (L45, ADR-035)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "035-stability-score-trust-signal.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "stability_score_trust_signal.json"
REQUIRED_INVARIANT_IDS = {f"STB-00{i}" for i in range(1, 8)}


def test_adr_035_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_035_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_field_name_is_stability_score() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["field_name"] == "stability_score"


def test_field_range_is_0_to_1() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["field_range"] == [0.0, 1.0]


def test_convention_higher_is_better() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["convention"] == "higher_is_better"


def test_policy_defaults_correct() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["rolling_window_days"] == 7
    assert d["min_query_clusters"] == 5
    assert d["min_responses_per_cluster"] == 2
    assert d["default_on_insufficient_data"] == 0.5
    assert d["refresh_cadence_seconds"] == 86400


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_l51_contract_update_invariant_present() -> None:
    """STB-006 requires the L51 fan-in contract test be updated together
    with the dataclass field. This prevents silent contract drift."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    stb006 = next((i for i in contract["invariants"] if i["id"] == "STB-006"), None)
    must_text = " ".join(stb006.get("must", [])).lower()
    assert "l51" in must_text or "fan_in" in must_text or "fan-in" in must_text
