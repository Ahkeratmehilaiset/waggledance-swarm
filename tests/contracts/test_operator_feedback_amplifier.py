# SPDX-License-Identifier: BUSL-1.1
"""Operator-feedback amplifier contract (L30, ADR-053)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "053-operator-feedback-amplifier.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "operator_feedback_amplifier.json"
REQUIRED_INVARIANT_IDS = {f"OFA-00{i}" for i in range(1, 8)}
REQUIRED_FIELDS = {"event_type", "feedback_id", "feedback_kind", "query_class_hash", "operator_id", "priority", "submitted_at_utc"}
REQUIRED_KINDS = {"needs_solver", "broken_route", "wrong_output"}


def test_adr_053_exists() -> None:
    assert ADR_PATH.exists()


def test_substrate_only() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_event_type_pinned() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["event_type"] == "ops_feedback"


def test_kinds_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["feedback_kinds"]) == REQUIRED_KINDS


def test_priority_enum() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["priority_enum"]) == {"high", "normal"}


def test_required_fields_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(c["required_fields"]) == REQUIRED_FIELDS


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["fast_track_canary_minutes"] == 15
    assert d["fast_track_per_hour_max"] == 10


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]
