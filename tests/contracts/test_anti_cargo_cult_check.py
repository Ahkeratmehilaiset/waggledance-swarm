# SPDX-License-Identifier: BUSL-1.1
"""Anti-cargo-cult check contract (L29, ADR-034)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "034-anti-cargo-cult-check.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "anti_cargo_cult_check.json"

REQUIRED_PROBE_FIELDS = {
    "probe_id", "probe_version", "input", "expected_class",
    "baseline_accuracy", "added_at_utc", "added_by",
}
REQUIRED_INVARIANT_IDS = {f"ACC-00{i}" for i in range(1, 8)}


def test_adr_034_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_034_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_yaml_path_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["yaml_path"] == "configs/anti_cargo_cult_probes.yaml"


def test_tolerance_default_and_range() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["tolerance_pct"] == 5
    assert contract["tolerance_range"] == [0, 20]


def test_stable_tag_threshold() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["stable_tag_after_days_clean"] == 90


def test_required_probe_fields_match() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(contract["required_probe_fields"]) == REQUIRED_PROBE_FIELDS


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_block_not_penalize_invariant() -> None:
    """ACC-004 ensures the gate BLOCKS, not penalizes, on tolerance fail.
    A penalized but still-promoted memorized solver is exactly the
    cargo-cult outcome this leap is designed to prevent."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    acc004 = next((i for i in contract["invariants"] if i["id"] == "ACC-004"), None)
    must_text = " ".join(acc004.get("must", [])).lower()
    assert "block" in must_text
