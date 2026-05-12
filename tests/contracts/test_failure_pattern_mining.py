# SPDX-License-Identifier: BUSL-1.1
"""Failure-pattern mining contract (L25, ADR-033)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "033-failure-pattern-mining.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "failure_pattern_mining.json"
REQUIRED_INVARIANT_IDS = {f"FPM-00{i}" for i in range(1, 8)}


def test_adr_033_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_033_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_policy_defaults_correct() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    defaults = contract["policy_defaults"]
    assert defaults["window_n_rejections"] == 200
    assert defaults["anti_feature_threshold_k"] == 20
    assert defaults["anti_feature_ttl_days"] == 30
    assert defaults["anti_feature_penalty"] == 0.3
    assert defaults["mining_cadence_seconds"] == 3600


def test_yaml_path_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["yaml_path"] == "configs/gap_miner_anti_features.yaml"


def test_k_lte_n() -> None:
    """K (threshold) must be <= N (window size). Otherwise the threshold
    can never be reached."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["anti_feature_threshold_k"] <= d["window_n_rejections"]


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_advisory_not_blocking_invariant() -> None:
    """FPM-004 forbids hard-banning candidates with anti-features. Anti-
    features are scoring adjustments, NOT filters."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    fpm004 = next((i for i in contract["invariants"] if i["id"] == "FPM-004"), None)
    must_text = " ".join(fpm004.get("must", [])).lower()
    assert "score" in must_text
    assert "not as filter" in must_text or "may still be promoted" in must_text
