# SPDX-License-Identifier: BUSL-1.1
"""Cross-agent failed-candidate broadcast contract (L22, ADR-032)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "032-cross-agent-failed-candidate-broadcast.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "cross_agent_failed_broadcast.json"
)

REQUIRED_FIELDS = {
    "event_type", "candidate_class_hash", "rejection_reason",
    "rejected_at_utc", "rejecting_agent_id", "feature_fingerprint", "ttl_hours",
}
REQUIRED_INVARIANT_IDS = {f"CFB-00{i}" for i in range(1, 8)}


def test_adr_032_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_032_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_event_type_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["event_type"] == "failed_candidate_broadcast"


def test_required_fields_match() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(contract["required_fields"]) == REQUIRED_FIELDS


def test_ttl_defaults_and_bounds() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["policy_defaults"]["ttl_hours"] == 720
    assert contract["policy_defaults"]["ttl_hours_max"] == 8760


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_no_shared_database_invariant() -> None:
    """CFB-004 forbids a shared anti-knowledge database. All transfer
    MUST go through bridge events.jsonl. This is critical: a shared DB
    would create a central failure point for the swarm's anti-knowledge."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    cfb004 = next(
        (i for i in contract["invariants"] if i["id"] == "CFB-004"), None
    )
    assert cfb004 is not None
    must_text = " ".join(cfb004.get("must", [])).lower()
    assert "no database" in must_text or "no shared database" in must_text or "events.jsonl" in must_text


def test_per_agent_local_cache_invariant() -> None:
    """CFB-005 forbids a central anti-knowledge service. Each agent owns
    its cache state."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    cfb005 = next(
        (i for i in contract["invariants"] if i["id"] == "CFB-005"), None
    )
    assert cfb005 is not None
    must_text = " ".join(cfb005.get("must", [])).lower()
    assert "per-agent" in must_text or "no shared anti-knowledge" in must_text or "no central" in must_text
