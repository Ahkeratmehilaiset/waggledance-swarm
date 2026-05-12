# SPDX-License-Identifier: BUSL-1.1
"""Temporal tunnel layers contract (L7, ADR-044)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "044-temporal-tunnel-layers.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "temporal_tunnel_layers.json"
REQUIRED_INVARIANT_IDS = {f"TTL-00{i}" for i in range(1, 8)}


def test_adr_044_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_044_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_default_layer_is_all() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["default_layer"] == "all"


def test_season_calendar_months() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    cal = c["season_calendar"]
    assert cal["spring"] == ["03", "04", "05"]
    assert cal["summer"] == ["06", "07", "08"]
    assert cal["autumn"] == ["09", "10", "11"]
    assert cal["winter"] == ["12", "01", "02"]


def test_valid_layers_include_all_buckets() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    layers = set(c["valid_layers"])
    expected = {"all", "season:spring", "season:summer", "season:autumn", "season:winter",
                "hour:0-6", "hour:6-12", "hour:12-18", "hour:18-24"}
    assert layers == expected


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
