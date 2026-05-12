# SPDX-License-Identifier: BUSL-1.1
"""Queue + backpressure for compact-card writes (L32, ADR-054)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "054-queue-backpressure-compact-card.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "queue_backpressure_compact_card.json"
REQUIRED_INVARIANT_IDS = {f"QBC-00{i}" for i in range(1, 8)}


def test_adr_054_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["max_queue_size"] == 10000
    assert d["soft_threshold_pct"] == 70
    assert d["hard_threshold_pct"] == 100
    assert d["batch_size"] == 100
    assert d["drain_interval_ms"] == 100
    assert d["sustained_rate_per_sec"] == 1000


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
