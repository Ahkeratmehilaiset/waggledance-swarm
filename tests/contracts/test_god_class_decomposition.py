# SPDX-License-Identifier: BUSL-1.1
"""God-class decomposition contract (L52, ADR-061)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "061-god-class-decomposition.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "god_class_decomposition.json"
REQUIRED_INVARIANT_IDS = {f"GCD-00{i}" for i in range(1, 8)}


def test_adr_061_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_container_split_five_way() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["container_split"].keys()) == {"storage", "llm", "memory", "agents", "services"}


def test_runtime_split_three_way() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["runtime_split"].keys()) == {"core", "wiring", "policy"}


def test_total_pr_count_is_8() -> None:
    """5 container + 3 runtime = 8 PRs total."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["policy_defaults"]["total_pr_count"] == 8
    assert len(c["container_split"]) + len(c["runtime_split"]) == 8


def test_lines_per_pr_max_200() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["policy_defaults"]["lines_per_pr_max"] == 200


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_no_transitive_depth_claim() -> None:
    """GCD-004 forbids 'cold-boot reduction' claim. Verify ADR text
    does NOT promise that — the benefit is BLAST RADIUS only."""
    text = ADR_PATH.read_text(encoding="utf-8").lower()
    # ADR should explicitly note "no cold-import savings"
    assert "no change: transitive deps redistribute" in text or "no transitive-depth claim" in text or "transitive deps redistribute, not shrink" in text


def test_composition_over_inheritance() -> None:
    """GCD-007: sub-modules compose, no multi-inheritance."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    gcd007 = next((i for i in c["invariants"] if i["id"] == "GCD-007"), None)
    must_text = " ".join(gcd007.get("must", [])).lower()
    assert "composition" in must_text or "no mro" in must_text or "no multiple inheritance" in must_text
