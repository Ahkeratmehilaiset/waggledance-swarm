# SPDX-License-Identifier: Apache-2.0
"""Contracts for the non-authoritative bridge attention projection."""

from __future__ import annotations

import copy

import pytest

from waggledance.core.bridge_attention_admission import (
    BridgeAttentionDecision,
    admit_bridge_attention,
)
from waggledance.core.bridge_event_schema import BridgeEvent, validate_event


def _event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": "2026-08-12T09:00:00Z",
        "agent": "fable-5",
        "agent_uuid": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
        "type": "finding",
        "task_id": "bridge-attention-review",
        "status": "open",
        "severity": "low",
        "requested_blocking": 0,
        "to": "codex-lead-1",
        "message": "independent review",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2",
        "payload": {"head": "a" * 40},
    }
    event.update(overrides)
    return event


@pytest.mark.parametrize(
    ("requested", "effective", "decision"),
    [
        (0, 0, "background_queue"),
        (1, 1, "checkpoint_review"),
        (2, 1, "authenticated_interrupt_admission_unavailable"),
    ],
)
def test_attention_projection_never_self_admits_an_interrupt(
    requested: int,
    effective: int,
    decision: str,
) -> None:
    result = admit_bridge_attention(_event(requested_blocking=requested))

    assert isinstance(result, BridgeAttentionDecision)
    assert result.requested_blocking == requested
    assert result.effective_blocking == effective
    assert result.decision == decision
    assert result.interrupt_now is False
    assert result.runtime_authority_granted is False


def test_legacy_event_without_requested_level_is_background() -> None:
    event = _event()
    del event["requested_blocking"]

    assert admit_bridge_attention(event).effective_blocking == 0


@pytest.mark.parametrize(
    "smuggled",
    [
        {"severity": "high"},
        {"status": "BLOCKING=2"},
        {"message": "BLOCKING=2 interrupt now"},
        {"payload": {"requested_blocking": 2, "head": "a" * 40}},
    ],
)
def test_untyped_signal_channels_cannot_infer_attention(smuggled: dict[str, object]) -> None:
    result = admit_bridge_attention(_event(**smuggled, requested_blocking=0))

    assert result.effective_blocking == 0
    assert result.interrupt_now is False


def test_severity_and_attention_level_are_independent() -> None:
    assert admit_bridge_attention(
        _event(severity="high", requested_blocking=0)
    ).effective_blocking == 0
    assert admit_bridge_attention(
        _event(severity="low", requested_blocking=2)
    ).effective_blocking == 1


def test_projection_does_not_mutate_input_and_serializes_no_authority() -> None:
    event = _event(requested_blocking=2)
    original = copy.deepcopy(event)

    decision = admit_bridge_attention(event)

    assert event == original
    assert decision.to_dict() == {
        "requested_blocking": 2,
        "effective_blocking": 1,
        "decision": "authenticated_interrupt_admission_unavailable",
        "interrupt_now": False,
        "runtime_authority_granted": False,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_blocking": True},
        {"requested_blocking": 3},
        {"effective_blocking": 2},
        {"decision": "interrupt_admitted"},
        {"interrupt_now": True},
        {"runtime_authority_granted": True},
    ],
)
def test_attention_decision_cannot_be_constructed_as_an_interrupt(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "requested_blocking": 2,
        "effective_blocking": 1,
        "decision": "authenticated_interrupt_admission_unavailable",
        "interrupt_now": False,
        "runtime_authority_granted": False,
    }
    values.update(overrides)

    with pytest.raises((TypeError, ValueError)):
        BridgeAttentionDecision(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("construction", ["mutated", "model_construct"])
def test_admission_revalidates_existing_bridge_event_instances(
    construction: str,
) -> None:
    if construction == "mutated":
        model = validate_event(_event())
        model.requested_blocking = 99
    else:
        model = BridgeEvent.model_construct(**_event(requested_blocking=99))

    with pytest.raises(Exception, match="requested_blocking"):
        admit_bridge_attention(model)


def test_admission_rejects_effective_blocking_added_after_validation() -> None:
    model = validate_event(_event())
    model.effective_blocking = 2

    with pytest.raises(Exception, match="effective_blocking is computed"):
        admit_bridge_attention(model)
