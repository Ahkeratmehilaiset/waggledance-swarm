# SPDX-License-Identifier: BUSL-1.1
"""Multi-cell candidate portfolio contract (L4, ADR-039)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "039-multi-cell-candidate-portfolio.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "multi_cell_portfolio.json"
REQUIRED_INVARIANT_IDS = {f"MCP-00{i}" for i in range(1, 8)}


def test_adr_039_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_039_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_k_default_3_range_1_to_10() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = contract["policy_defaults"]
    assert d["k_default"] == 3
    assert d["k_range"] == [1, 10]


def test_candidate_dataclass_shape() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    dc = contract["candidate_dataclass"]
    assert dc["name"] == "CellCandidate"
    assert dc["decorator"] == "@dataclass(frozen=True, slots=True)"
    assert dc["fields"] == ["cell_id", "confidence", "matched_selectors"]


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_backward_compat_invariant_present() -> None:
    """MCP-006 ensures the new portfolio API does NOT break the existing
    select_origin_cell signature. Crucial for incremental adoption."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    mcp006 = next((i for i in contract["invariants"] if i["id"] == "MCP-006"), None)
    must_text = " ".join(mcp006.get("must", [])).lower()
    assert "no breaking change" in must_text or "remains" in must_text


def test_empty_result_returns_empty_list_not_none() -> None:
    """MCP-007 distinguishes empty list (no matches) from None semantically.
    Callers handle 'no candidates' explicitly, not as exception."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    mcp007 = next((i for i in contract["invariants"] if i["id"] == "MCP-007"), None)
    must_text = " ".join(mcp007.get("must", [])).lower()
    assert "empty" in must_text or "[]" in must_text
    assert "no exception" in must_text or "not none" in must_text
