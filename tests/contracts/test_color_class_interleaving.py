# SPDX-License-Identifier: BUSL-1.1
"""Color-class interleaving contract (L9, ADR-046)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "046-color-class-interleaving.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "color_class_interleaving.json"
REQUIRED_INVARIANT_IDS = {f"CCI-00{i}" for i in range(1, 8)}


def test_adr_046_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_color_enum_3() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["color_enum"]) == {"A", "B", "C"}


def test_formula_pinned() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert "q + 2 * r" in c["coloring_formula"] or "(coord.q + 2 * coord.r)" in c["coloring_formula"]


def test_rotation_default() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["rotation_default"] == "round_robin"


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_formula_3_coloring_property() -> None:
    """Sanity: the (q + 2r) mod 3 formula must produce a valid 3-coloring
    on a small axial-hex sample. Check that ring-1 neighbors do not
    share color."""
    # Ring-1 neighbor offsets in axial coords:
    neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]
    # Test at origin (0, 0):
    origin_color = (0 + 2 * 0) % 3
    for dq, dr in neighbors:
        n_color = ((0 + dq) + 2 * (0 + dr)) % 3
        assert n_color != origin_color, f"Neighbor {(dq, dr)} shares color with origin"
