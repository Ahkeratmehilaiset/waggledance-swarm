# SPDX-License-Identifier: BUSL-1.1
"""LRU memoization contract (L40, ADR-057)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "057-lru-memoization-pure-hot-path.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "lru_memoization.json"
REQUIRED_INVARIANT_IDS = {f"LRU-00{i}" for i in range(1, 8)}


def test_adr_057_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_known_allowlist_entry_exists() -> None:
    """The current @lru_cache in status.py MUST be allowlisted."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sites = c["allowed_sites"]
    assert any("status.py" in s["module"] for s in sites)


def test_policy_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["forbid_unbounded_cache"] is True
    assert d["require_hashable_args"] is True
    assert d["require_pure_function"] is True


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_each_allowed_site_has_maxsize() -> None:
    """LRU-002: every entry has explicit bounded maxsize."""
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for s in c["allowed_sites"]:
        assert s.get("maxsize") is not None
        assert s["maxsize"] > 0
