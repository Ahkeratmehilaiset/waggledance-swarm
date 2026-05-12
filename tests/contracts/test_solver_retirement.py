# SPDX-License-Identifier: BUSL-1.1
"""Solver retirement contract (L27, ADR-051)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "051-solver-retirement.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "solver_retirement.json"
REQUIRED_INVARIANT_IDS = {f"SOR-00{i}" for i in range(1, 8)}


def test_adr_051_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["quality_floor"] == 0.55
    assert d["consecutive_days_below_floor"] == 30
    assert d["archive_strikes_window_days"] == 90
    assert d["archive_strikes_count"] == 2


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_consecutive_not_cumulative() -> None:
    """SOR-002 requires 30 CONSECUTIVE days. Streak resets on recovery."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sor002 = next((i for i in c["invariants"] if i["id"] == "SOR-002"), None)
    must_text = " ".join(sor002.get("must", [])).lower()
    assert "streak resets" in must_text or "consecutive" in must_text
