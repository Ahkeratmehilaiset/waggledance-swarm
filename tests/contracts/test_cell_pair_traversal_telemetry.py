# SPDX-License-Identifier: BUSL-1.1
"""Cell-pair traversal telemetry contract (L10, ADR-047)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "047-cell-pair-traversal-telemetry.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "cell_pair_traversal_telemetry.json"
REQUIRED_INVARIANT_IDS = {f"CPT-00{i}" for i in range(1, 8)}
REQUIRED_FIELDS = {"event_type", "from_cell", "to_solver", "query_class_hash", "success", "latency_ms", "ts_utc"}


def test_adr_047_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_event_type_pinned() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["event_type"] == "cell_pair_traversal"


def test_required_fields_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["required_fields"]) == REQUIRED_FIELDS


def test_sampling_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["policy_defaults"]["sampling_rate"] == 1.0
    assert c["policy_defaults"]["sampling_rate_min"] == 0.01
    assert c["policy_defaults"]["retention_days"] == 30


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
