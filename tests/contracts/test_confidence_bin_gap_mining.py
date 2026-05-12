# SPDX-License-Identifier: BUSL-1.1
"""Confidence-bin gap mining contract (L21, ADR-031)."""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "031-confidence-bin-gap-mining.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "confidence_bin_gap_mining.json"
)

REQUIRED_BINS = {"deep", "borderline", "marginal"}
REQUIRED_INVARIANT_IDS = {f"CBG-00{i}" for i in range(1, 8)}


def test_adr_031_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_031_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_bin_enum_matches() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(contract["bin_enum"]) == REQUIRED_BINS


def test_bin_ranges_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ranges = contract["bin_ranges"]
    assert ranges["deep"] == [0.0, 0.2]
    assert ranges["borderline"] == [0.2, 0.4]
    assert ranges["marginal"] == [0.4, 0.6]


def test_no_signal_threshold_is_0_6() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["no_signal_threshold"] == 0.6


def test_bin_strategies_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    strategies = contract["bin_strategies"]
    assert strategies["deep"] == "new_capability_id"
    assert strategies["borderline"] == "new_solver_within_capability"
    assert strategies["marginal"] == "routing_tweak"


def test_default_candidates_per_tick_is_10() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["bin_candidates_per_tick"] == 10


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_bin_ranges_cover_continuous_interval() -> None:
    """Verify the three bins cover [0.0, 0.6) continuously with no gap or
    overlap. Boundary 0.2 belongs to borderline (not deep), 0.4 to
    marginal (not borderline)."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ranges = contract["bin_ranges"]
    # deep upper == borderline lower
    assert ranges["deep"][1] == ranges["borderline"][0]
    # borderline upper == marginal lower
    assert ranges["borderline"][1] == ranges["marginal"][0]
    # marginal upper == no_signal_threshold
    assert ranges["marginal"][1] == contract["no_signal_threshold"]
