# SPDX-License-Identifier: BUSL-1.1
"""Cold-tier read-through cache contract (L17, ADR-029).

Substrate-only landing. Pins TTL + size + read-through semantics +
layer separation from ADR-023.
"""
from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "029-cold-tier-read-through-cache.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "cold_tier_read_through_cache.json"
)
PROGRESSIVE_REPLAY_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "progressive_replay_l0_l4.json"
)
SNAPSHOT_CONTRACT = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "forensic_snapshot_rotation.json"
)


REQUIRED_INVARIANT_IDS = {
    "CTC-001", "CTC-002", "CTC-003", "CTC-004", "CTC-005", "CTC-006", "CTC-007",
}


def test_adr_029_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_029_marks_substrate_only_landing() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    assert "substrate-only landing" in text.lower()


def test_adr_029_references_related_adrs() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    for adr_id in ("ADR-021", "ADR-022", "ADR-023"):
        assert adr_id in text


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_ttl_is_24h() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["ttl_seconds"] == 86400


def test_max_entries_is_10000() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["cold_promo_max_entries"] == 10000


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item.get("id") for item in contract.get("invariants", [])}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract.get("invariants", []):
        must = item.get("must")
        assert isinstance(must, list) and must


def test_layer_separation_invariant_present() -> None:
    """CTC-007 forbids cross-population with ADR-023's provenance cache.
    Pin the invariant explicitly so a future merger doesn't accidentally
    collapse the two layers (which would defeat both semantics)."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ctc007 = next(
        (i for i in contract["invariants"] if i["id"] == "CTC-007"), None
    )
    assert ctc007 is not None
    must_text = " ".join(ctc007.get("must", [])).lower()
    assert "tip_cache" in must_text or "magmaprovenanceadapter" in must_text


def test_glued_to_progressive_replay_and_snapshot() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    deps = contract.get("depends_on_contracts", [])
    assert "progressive_replay_l0_l4.json" in deps
    assert "forensic_snapshot_rotation.json" in deps
    assert PROGRESSIVE_REPLAY_CONTRACT.exists()
    assert SNAPSHOT_CONTRACT.exists()


def test_contract_marks_out_of_scope() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = contract.get("out_of_scope", [])
    assert isinstance(out_of_scope, list) and out_of_scope
