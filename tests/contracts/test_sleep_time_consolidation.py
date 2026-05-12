# SPDX-License-Identifier: BUSL-1.1
"""Sleep-time consolidation contract (L24, ADR-049)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "049-sleep-time-consolidation.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "sleep_time_consolidation.json"
REQUIRED_INVARIANT_IDS = {f"STC-00{i}" for i in range(1, 8)}


def test_adr_049_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["schedule_cron"] == "0 2 * * *"
    assert d["rolling_window_days"] == 7
    assert d["shadow_ceiling_days"] == 14


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_atomic_promotion() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    stc005 = next((i for i in c["invariants"] if i["id"] == "STC-005"), None)
    must_text = " ".join(stc005.get("must", [])).lower()
    assert "atomic" in must_text or "single transaction" in must_text
