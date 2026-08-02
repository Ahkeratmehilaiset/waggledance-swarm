# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from waggledance.core.learning.understanding_contracts import (
    HexCellAddressV1,
    PrivacyClass,
    UnderstandingDisposition,
)
from waggledance.core.learning.understanding_loop import (
    InMemoryUnderstandingEventSink,
    UnderstandingLoop,
    UnderstandingLoopError,
    UnderstandingPolicyV1,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += timedelta(seconds=seconds)


class CapturingPredictor:
    def __init__(self) -> None:
        self.calls = []

    def predict(self, header, prior_state):
        self.calls.append((dict(header), prior_state))
        return prior_state.expected_value


class FailingSink(InMemoryUnderstandingEventSink):
    def __init__(self, fail_kind: str) -> None:
        super().__init__()
        self.fail_kind = fail_kind

    def append_event(self, event_kind, payload):
        if event_kind == self.fail_kind:
            raise OSError("injected sink failure")
        return super().append_event(event_kind, payload)

    def append_batch(self, events, *, idempotency_key):
        if any(event_kind == self.fail_kind for event_kind, _payload in events):
            raise OSError("injected sink failure")
        return super().append_batch(events, idempotency_key=idempotency_key)


def _cell() -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id="bee_ops",
        q=0,
        r=0,
        incarnation_id="inc-1",
        generation=1,
        fence=1,
    )


def _obs(seq: int, value: float, *, privacy: str = "synthetic", entity: str = "wd.synthetic.hive-1"):
    return {
        "observation_id": f"obs-{seq}",
        "source_seq": seq,
        "source": "mqtt",
        "entity_id": entity,
        "metric": "temperature",
        "unit": "Cel",
        "value": value,
        "quality": 0.9,
        "privacy_class": privacy,
        "metadata": {"fixture": True},
    }


def _loop(*, sink=None, predictor=None, clock=None, policy=None) -> UnderstandingLoop:
    return UnderstandingLoop(
        cell=_cell(),
        event_sink=sink or InMemoryUnderstandingEventSink(),
        predictor=predictor,
        clock=clock,
        policy=policy,
    )


def test_prediction_is_committed_before_value_reveal_and_update() -> None:
    sink = InMemoryUnderstandingEventSink()
    predictor = CapturingPredictor()
    loop = _loop(sink=sink, predictor=predictor)

    ticket = loop.prepare_observation(_obs(1, 10.0))
    kinds_before = [event["event_kind"] for event in sink.events]
    outcome = loop.complete_numeric(ticket, 10.0)
    kinds_after = [event["event_kind"] for event in sink.events]

    assert kinds_before == ["prediction_committed"]
    assert kinds_after[:4] == [
        "prediction_committed",
        "observation_revealed",
        "disposition_recorded",
        "local_provisional_update",
    ]
    assert outcome.disposition is UnderstandingDisposition.COLD_START
    assert "value" not in predictor.calls[0][0]
    assert "metadata" not in predictor.calls[0][0]


def test_second_prediction_uses_only_prior_shadow_state() -> None:
    predictor = CapturingPredictor()
    loop = _loop(predictor=predictor)

    first = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(first, 10.0)
    second = loop.prepare_observation(_obs(2, 20.0))

    assert second.prediction is not None
    assert second.prediction.predicted_value == 10.0
    assert second.prior_state.generation == 1
    assert predictor.calls[1][1].expected_value == 10.0


def test_duplicate_is_idempotent_and_conflicting_reuse_quarantines_source() -> None:
    loop = _loop()
    first = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(first, 10.0)

    duplicate = loop.prepare_observation(_obs(1, 10.0))
    duplicate_outcome = loop.complete_numeric(duplicate, 10.0)
    conflict = loop.prepare_observation(_obs(1, 11.0))
    conflict_outcome = loop.complete_numeric(conflict, 11.0)
    later = loop.prepare_observation(_obs(2, 12.0))

    assert duplicate_outcome.disposition is UnderstandingDisposition.DUPLICATE
    assert conflict_outcome.disposition is UnderstandingDisposition.CONTRADICTORY
    assert later.terminal_reason == "source_quarantined"
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 1


def test_private_observation_without_key_is_accounted_without_raw_reveal() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink)

    ticket = loop.prepare_observation(_obs(1, 7.0, privacy="private"))
    outcome = loop.complete_numeric(ticket, 7.0)

    assert outcome.disposition is UnderstandingDisposition.PRIVACY_BLOCKED
    serialized = repr(sink.events)
    assert "observation_revealed" not in serialized
    assert "'value': 7.0" not in serialized
    assert loop.stats()["counter_conservation_ok"] is True


def test_private_observation_with_key_is_still_blocked_from_shadow_learning() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = UnderstandingLoop(
        cell=_cell(),
        event_sink=sink,
        hmac_key_provider=lambda _privacy: (b"k" * 32, "test-key", "2026-08-02"),
    )

    ticket = loop.prepare_observation(_obs(1, 7.0, privacy="private"))
    outcome = loop.complete_numeric(ticket, 7.0)

    assert outcome.disposition is UnderstandingDisposition.PRIVACY_BLOCKED
    assert ticket.observation_commitment.scheme == "hmac-sha256"
    serialized = repr(sink.events)
    assert "observation_revealed" not in serialized
    assert "'value': 7.0" not in serialized
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 0


def test_prediction_sink_failure_cannot_update_shadow_state() -> None:
    loop = _loop(sink=FailingSink("prediction_committed"))

    with pytest.raises(OSError, match="injected"):
        loop.prepare_observation(_obs(1, 10.0))

    state = loop.get_state("wd.synthetic.hive-1.temperature")
    assert state.sample_count == 0
    assert loop.stats()["accepted"] == 0


def test_stale_ticket_becomes_audited_expired_outcome_without_aba_update() -> None:
    loop = _loop()
    first = loop.prepare_observation(_obs(1, 10.0))
    second = loop.prepare_observation(_obs(2, 20.0))
    loop.complete_numeric(first, 10.0)

    outcome = loop.complete_numeric(second, 20.0)

    assert outcome.disposition is UnderstandingDisposition.EXPIRED
    assert outcome.local_update is None
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 1


def test_revealed_value_must_match_original_observation_commitment() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink)
    ticket = loop.prepare_observation(_obs(1, 10.0))

    outcome = loop.complete_numeric(ticket, 100.0)

    assert outcome.disposition is UnderstandingDisposition.CONTRADICTORY
    assert outcome.local_update is None
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 0
    assert "'value': 100.0" not in repr(sink.events)
    assert loop.stats()["commitment_mismatch"] == 1


def test_ticket_from_another_loop_and_modified_ticket_are_rejected() -> None:
    first = _loop()
    second = _loop()
    foreign = first.prepare_observation(_obs(1, 10.0))

    with pytest.raises(UnderstandingLoopError, match="unissued_prediction_ticket"):
        second.complete_numeric(foreign, 10.0)
    forged = replace(foreign, source_seq=2)
    with pytest.raises(UnderstandingLoopError, match="unissued_prediction_ticket"):
        first.complete_numeric(forged, 10.0)


def test_substring_privacy_domain_is_rejected_before_prediction() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink)
    ticket = loop.prepare_observation(_obs(1, 10.0))
    header = sink.events[0]["payload"]["observation_header"]
    commitment = replace(
        ticket.observation_commitment,
        privacy_domain="evil.synthetic.secret",
    )

    with pytest.raises(UnderstandingLoopError, match="domain mismatch"):
        loop.prepare_numeric(
            header,
            commitment,
        )


def test_out_of_range_value_never_updates_shadow_state() -> None:
    loop = _loop()
    ticket = loop.prepare_observation(_obs(1, 151.0))

    outcome = loop.complete_numeric(ticket, 151.0)

    assert outcome.disposition is UnderstandingDisposition.SCHEMA_INVALID
    assert outcome.local_update is None
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 0


def test_namespace_match_requires_dot_boundary() -> None:
    loop = _loop()
    ticket = loop.prepare_observation(
        _obs(1, 10.0, entity="wd.synthetic_evil.hive-1")
    )

    outcome = loop.complete_numeric(ticket, 10.0)

    assert outcome.disposition is UnderstandingDisposition.SCHEMA_INVALID
    assert outcome.local_update is None


def test_resolution_batch_failure_leaves_state_unchanged_and_is_retryable() -> None:
    sink = FailingSink("local_provisional_update")
    loop = _loop(sink=sink)
    ticket = loop.prepare_observation(_obs(1, 10.0))

    with pytest.raises(OSError, match="injected"):
        loop.complete_numeric(ticket, 10.0)

    assert [event["event_kind"] for event in sink.events] == ["prediction_committed"]
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 0
    sink.fail_kind = ""
    outcome = loop.complete_numeric(ticket, 10.0)
    assert outcome.disposition is UnderstandingDisposition.COLD_START
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 1


def test_terminal_and_successful_completion_are_idempotent() -> None:
    private_loop = _loop()
    private_ticket = private_loop.prepare_observation(
        _obs(1, 7.0, privacy="private")
    )
    first_private = private_loop.complete_numeric(private_ticket, 7.0)
    assert private_loop.complete_numeric(private_ticket, 999.0) == first_private

    loop = _loop()
    ticket = loop.prepare_observation(_obs(1, 10.0))
    first = loop.complete_numeric(ticket, 10.0)
    assert loop.complete_numeric(ticket, 10.0) == first
    assert loop.get_state("wd.synthetic.hive-1.temperature").sample_count == 1


def test_in_memory_sink_cannot_be_mutated_through_returned_nested_payload() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink)
    loop.prepare_observation(_obs(1, 10.0))
    exposed = sink.events[0]
    exposed["payload"]["observation_header"]["entity_id"] = "mutated"

    assert sink.events[0]["payload"]["observation_header"]["entity_id"] == (
        "wd.synthetic.hive-1"
    )

def test_curiosity_is_bounded_and_never_invokes_external_actor() -> None:
    clock = FakeClock()
    policy = UnderstandingPolicyV1(curiosity_top_k=2, curiosity_per_minute=1)
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(clock=clock, policy=policy, sink=sink)
    first = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(first, 10.0)

    for seq, value in ((2, 20.0), (3, 30.0), (4, 40.0)):
        ticket = loop.prepare_observation(_obs(seq, value))
        loop.complete_numeric(ticket, value)

    curiosity_events = [event for event in sink.events if event["event_kind"] == "curiosity_enqueued"]
    assert len(curiosity_events) == 1
    payload = curiosity_events[0]["payload"]
    assert payload["network_invoked"] is False
    assert payload["llm_invoked"] is False
    assert payload["builder_invoked"] is False


def test_five_directionally_consistent_surprises_propose_shadow_delta_only() -> None:
    clock = FakeClock()
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(clock=clock, sink=sink)
    cold = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(cold, 10.0)

    delta = None
    for seq in range(2, 7):
        clock.advance()
        value = 20.0 + seq
        ticket = loop.prepare_observation(_obs(seq, value))
        outcome = loop.complete_numeric(ticket, value)
        delta = outcome.knowledge_delta or delta

    assert delta is not None
    assert delta.runtime_authority_applied is False
    assert delta.routing_influence_applied is False
    events = [event for event in sink.events if event["event_kind"] == "knowledge_delta_proposed"]
    assert len(events) == 1
    assert events[0]["payload"]["hive_commit_applied"] is False


def test_rate_budget_drops_are_audited_and_conserved() -> None:
    clock = FakeClock()
    policy = UnderstandingPolicyV1(
        per_source_rate=1,
        per_source_burst=1,
        global_rate=1,
        global_burst=1,
    )
    loop = _loop(clock=clock, policy=policy)

    accepted = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(accepted, 10.0)
    dropped = loop.prepare_observation(_obs(2, 11.0))
    outcome = loop.complete_numeric(dropped, 11.0)

    assert outcome.disposition is UnderstandingDisposition.DROPPED_BUDGET
    stats = loop.stats()
    assert stats["dropped_budget"] == 1
    assert stats["counter_conservation_ok"] is True


def test_runtime_authority_flags_remain_false() -> None:
    loop = _loop()
    ticket = loop.prepare_observation(_obs(1, 10.0))
    outcome = loop.complete_numeric(ticket, 10.0)

    assert outcome.runtime_authority_applied is False
    assert outcome.routing_influence_applied is False
    assert loop.stats()["runtime_authority_applied"] is False
