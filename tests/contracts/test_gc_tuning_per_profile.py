# SPDX-License-Identifier: BUSL-1.1
"""GC tuning per profile contract (L39, ADR-056)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "056-gc-tuning-per-profile.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "gc_tuning_per_profile.json"
REQUIRED_INVARIANT_IDS = {f"GTP-00{i}" for i in range(1, 8)}


def test_adr_056_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_gadget_uses_python_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["gen2_thresholds"]["GADGET"] == [700, 10, 10]


def test_thresholds_monotone() -> None:
    """GTP-005: gen-2 threshold monotone across profiles."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    t = c["gen2_thresholds"]
    assert t["GADGET"][0] <= t["COTTAGE"][0] <= t["HOME"][0] <= t["FACTORY"][0]


def test_off_peak_windows() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    w = c["off_peak_windows_local"]
    assert w["HOME"] == "02:00-04:00"
    assert w["FACTORY"] == "02:00-04:00"


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
