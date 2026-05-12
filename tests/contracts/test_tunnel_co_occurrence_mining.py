# SPDX-License-Identifier: BUSL-1.1
"""Tunnel co-occurrence mining contract (L3, ADR-042)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "042-tunnel-co-occurrence-mining.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "tunnel_co_occurrence_mining.json"
REQUIRED_INVARIANT_IDS = {f"TCM-00{i}" for i in range(1, 8)}


def test_adr_042_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_042_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_policy_defaults_correct() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["mining_window_hours"] == 168
    assert d["min_cofire_count"] == 10
    assert d["min_trust_score"] == 0.70
    assert d["mining_cadence_seconds"] == 3600
    assert d["trust_formula"] == "cofire_count / max(invocations_A, invocations_B)"


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_threshold_matches_adr_038() -> None:
    """Mined tunnels MUST honor ADR-038's sparse-threshold (0.70)."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["min_trust_score"] == 0.70


def test_bidirectional_emission_invariant() -> None:
    """TCM-005: co-fire mines BOTH directions. A->B AND B->A."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    tcm005 = next((i for i in contract["invariants"] if i["id"] == "TCM-005"), None)
    must_text = " ".join(tcm005.get("must", [])).lower()
    assert "two tunnel records" in must_text or "both directions" in must_text or "a->b and b->a" in must_text


def test_jaccard_uses_max_denominator() -> None:
    """TCM-003 requires max() denominator (conservative bound).
    NOT min() (would inflate trust on rare-solver co-fires).
    NOT product (would explode for high-volume solvers)."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    tcm003 = next((i for i in contract["invariants"] if i["id"] == "TCM-003"), None)
    must_text = " ".join(tcm003.get("must", [])).lower()
    assert "max" in must_text
    assert "not min" in must_text
    assert "not product" in must_text
