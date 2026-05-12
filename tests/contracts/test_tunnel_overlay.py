# SPDX-License-Identifier: BUSL-1.1
"""Tunnel overlay registry contract (L2, ADR-038)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "038-tunnel-overlay.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "tunnel_overlay.json"

REQUIRED_INVARIANT_IDS = {f"TUN-00{i}" for i in range(1, 8)}
REQUIRED_FIELDS = {
    "tunnel_id", "from_cell", "to_solver", "trust_score",
    "provenance_event_id", "added_at_utc", "last_validated_utc", "direction",
}
REQUIRED_DIRECTIONS = {"forward", "negative"}


def test_adr_038_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_038_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_yaml_path_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["yaml_path"] == "configs/tunnel_overlay.yaml"


def test_direction_enum_matches() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(contract["direction_enum"]) == REQUIRED_DIRECTIONS


def test_policy_defaults_correct() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["min_trust_score"] == 0.70
    assert d["revalidation_interval_days"] == 30
    assert d["archive_after_days_stale"] == 90
    assert d["hot_path_lookup_budget_us"] == 5


def test_tunnel_required_fields_match() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(contract["tunnel_required_fields"]) == REQUIRED_FIELDS


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_lookup_budget_matches_L34_envelope() -> None:
    """TUN-005 sets the tunnel lookup budget at 5 µs. This MUST be
    coherent with L34's hot-path budgets (which allow classify_intent
    at 10 µs as the slowest hot-path). Tunnel lookup is a NEW hot-path
    op; budget MUST be tighter than the slowest existing hot path."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    budget = contract["policy_defaults"]["hot_path_lookup_budget_us"]
    # L34 budgets in tests/contracts/test_hot_path_perf_budgets.py:
    #   classify_intent_short < 10 µs
    #   AliasRegistry.resolve < 5 µs
    # Tunnel lookup is finer-grained than classify_intent; 5 µs is the right tier.
    assert budget <= 10
