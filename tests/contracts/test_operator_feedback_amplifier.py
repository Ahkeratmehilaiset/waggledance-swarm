# SPDX-License-Identifier: BUSL-1.1
"""Operator-feedback amplifier contract (L30, ADR-053)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.autonomy_growth.operator_feedback_amplifier import (
    OPS_FEEDBACK_EVENT_TYPE,
    OperatorFeedbackValidationError,
    build_operator_feedback_scheduler_preflight,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "053-operator-feedback-amplifier.md"
CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "operator_feedback_amplifier.json"
REQUIRED_INVARIANT_IDS = {f"OFA-{i:03d}" for i in range(1, 12)}
REQUIRED_FIELDS = {"event_type", "feedback_id", "feedback_kind", "query_class_hash", "operator_id", "priority", "submitted_at_utc"}
REQUIRED_KINDS = {"needs_solver", "broken_route", "wrong_output"}


def test_adr_053_exists() -> None:
    assert ADR_PATH.exists()


def test_planner_status_documents_deferred_integrations() -> None:
    text = ADR_PATH.read_text(encoding="utf-8").lower()
    assert "scheduler-preflight landing" in text
    assert "bridge writer" in text
    assert "scheduler enqueue/execution" in text


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


def test_conditional_fields_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert c["conditional_fields"] == {"broken_route": ["route_context_hash"]}


def test_defaults() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    d = c["policy_defaults"]
    assert d["fast_track_canary_minutes"] == 15
    assert d["fast_track_per_hour_max"] == 10
    assert d["fast_track_global_per_hour_max"] == 30


def test_invariants_match() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert {i["id"] for i in c["invariants"]} == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_musts() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in c["invariants"]:
        assert item["must"]


def test_ofa006_is_planner_only_until_bridge_writer_lands() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ofa006 = next(item for item in c["invariants"] if item["id"] == "OFA-006")
    text = " ".join([
        ofa006["name"],
        ofa006["description"],
        *ofa006["must"],
    ]).lower()

    assert "feedback_action_taken" in text
    assert "links feedback_id to action_id" in text
    assert "bridge_event_written remains false" in text
    assert "echo event written" not in text


def test_scheduler_preflight_invariants_pin_rco_safety_conditions() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    text_by_id = {
        item["id"]: " ".join([item["name"], item["description"], *item["must"]]).lower()
        for item in c["invariants"]
    }

    assert "durable bridge log" in text_by_id["OFA-008"]
    assert "free-string operator_id rejected" in text_by_id["OFA-008"]
    assert "durable_bridge_log" in text_by_id["OFA-009"]
    assert "gate_skip_allowed remains false" in text_by_id["OFA-010"]
    assert "promotion/adversarial/canary gates are not skipped" in (
        text_by_id["OFA-010"]
    )


def test_scheduler_preflight_contract_pins_durable_payload_binding() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ofa011 = next(item for item in c["invariants"] if item["id"] == "OFA-011")
    text = " ".join([
        ofa011["name"],
        ofa011["description"],
        *ofa011["must"],
    ]).lower()

    assert "source_bridge_event.payload.ops_feedback" in text
    assert "feedback_id matches durable source payload" in text
    assert "query_class_hash matches durable source payload" in text
    assert "mismatch fails closed" in text


def test_scheduler_preflight_rejects_event_mismatch_named_by_contract() -> None:
    durable_feedback = _feedback(
        feedback_id="fb-durable",
        query_class_hash="sha256:" + "a" * 64,
        operator_id="bridge:operator",
    )
    supplied_feedback = _feedback(
        feedback_id="fb-supplied",
        query_class_hash="sha256:" + "b" * 64,
        operator_id="bridge:operator",
    )
    source = _bridge_event(durable_feedback)

    with pytest.raises(OperatorFeedbackValidationError, match="durable source"):
        build_operator_feedback_scheduler_preflight(
            supplied_feedback,
            source_bridge_event=source,
            durable_bridge_events=[source],
        )


def test_remaining_out_of_scope_integrations_are_precise() -> None:
    c = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    out_of_scope = " ".join(c["out_of_scope"]).lower()
    assert "bridge event writer integration" in out_of_scope
    assert "autogrowth scheduler execution/enqueue" in out_of_scope
    assert "operator ui" in out_of_scope


def _feedback(**overrides: str) -> dict[str, str]:
    base = {
        "event_type": OPS_FEEDBACK_EVENT_TYPE,
        "feedback_id": "fb-001",
        "feedback_kind": "needs_solver",
        "query_class_hash": "sha256:" + "a" * 64,
        "operator_id": "bridge:operator",
        "priority": "high",
        "submitted_at_utc": "2026-06-05T12:00:00Z",
    }
    base.update(overrides)
    return base


def _bridge_event(feedback: dict[str, str]) -> dict[str, object]:
    return {
        "ts_utc": "2026-06-05T12:00:00Z",
        "agent": "operator",
        "type": "message",
        "task_id": "operator-feedback-contract-test",
        "status": "ops_feedback_received",
        "message": "operator feedback",
        "agent_uuid": "",
        "session_id": "",
        "payload": {"ops_feedback": feedback},
    }
