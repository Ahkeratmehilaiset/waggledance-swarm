# SPDX-License-Identifier: BUSL-1.1
"""Negative tunnel mining contract (L5, ADR-040)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "040-negative-tunnels.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "negative_tunnels.json"
REQUIRED_INVARIANT_IDS = {f"NTU-00{i}" for i in range(1, 8)}


def test_adr_040_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_040_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_adr_040_references_tunnel_overlay_adr() -> None:
    """ADR-040 (mining) extends ADR-038 (registry). Must reference."""
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "ADR-038" in text


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_policy_defaults_correct() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["mining_window_hours"] == 24
    assert d["mining_window_range_hours"] == [1, 168]
    assert d["min_contradictions"] == 5
    assert d["min_contradiction_rate"] == 0.30
    assert d["mining_cadence_seconds"] == 3600


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_hard_veto_semantics() -> None:
    """NTU-004 makes negative tunnels override forward tunnels regardless
    of forward trust_score. This is the safety property: a known-bad
    route stays blocked even if other signals say it might work."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ntu004 = next((i for i in contract["invariants"] if i["id"] == "NTU-004"), None)
    must_text = " ".join(ntu004.get("must", [])).lower()
    assert "first" in must_text or "veto" in must_text or "skip" in must_text


def test_lifecycle_alignment_invariant() -> None:
    """NTU-007 keeps negative-tunnel lifecycle aligned with forward
    tunnels (ADR-038 TUN-007). Stale entries archive, do not silently
    delete."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ntu007 = next((i for i in contract["invariants"] if i["id"] == "NTU-007"), None)
    must_text = " ".join(ntu007.get("must", [])).lower()
    assert "archive" in must_text or "active=false" in must_text
    assert "not deleted" in must_text or "not deleted." in must_text
