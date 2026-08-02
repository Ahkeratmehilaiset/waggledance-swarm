# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import waggledance.core.magma.understanding_projection as understanding_projection_module

from waggledance.core.learning.understanding_contracts import (
    HexCellAddressV1,
    PrivacyClass,
    UnderstandingContractError,
    UnderstandingDisposition,
    derive_target_key,
)
from waggledance.core.learning.understanding_loop import (
    InMemoryUnderstandingEventSink,
    PLAINTEXT_REVEAL_RETENTION_POLICY_V1,
    UnderstandingLoop,
    UnderstandingLoopError,
    UnderstandingPolicyV1,
)
from waggledance.core.magma.understanding_ledger import UnderstandingLedger
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.understanding_projection import (
    project_understanding_ledger,
    replay_understanding_projection,
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


def _target(entity: str = "wd.synthetic.hive-1") -> str:
    return derive_target_key(entity, "temperature")


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
    predictor_identity = (
        {
            "predictor_artifact_digest": sha256_digest(
                {"test_predictor": type(predictor).__name__}
            ),
            "predictor_config_digest": sha256_digest(
                {"test_predictor_config": "v1"}
            ),
        }
        if predictor is not None
        else {}
    )
    return UnderstandingLoop(
        cell=_cell(),
        event_sink=sink or InMemoryUnderstandingEventSink(),
        predictor=predictor,
        clock=clock,
        policy=policy,
        **predictor_identity,
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


def test_value_only_reuse_is_suppressed_and_header_reuse_quarantines_source() -> None:
    loop = _loop()
    first = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(first, 10.0)

    duplicate = loop.prepare_observation(_obs(1, 10.0))
    duplicate_outcome = loop.complete_numeric(duplicate, 10.0)
    value_only_reuse = loop.prepare_observation(_obs(1, 11.0))
    value_only_outcome = loop.complete_numeric(value_only_reuse, 11.0)
    conflicting_observation = _obs(1, 11.0)
    conflicting_observation["quality"] = 0.8
    conflict = loop.prepare_observation(conflicting_observation)
    conflict_outcome = loop.complete_numeric(conflict, 11.0)
    later = loop.prepare_observation(_obs(2, 12.0))

    assert duplicate_outcome.disposition is UnderstandingDisposition.DUPLICATE
    assert value_only_outcome.disposition is UnderstandingDisposition.DUPLICATE
    assert conflict_outcome.disposition is UnderstandingDisposition.CONTRADICTORY
    assert later.terminal_reason == "source_quarantined"
    assert loop.get_state(_target()).sample_count == 1


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
    assert loop.get_state(_target()).sample_count == 0


def test_prediction_sink_failure_cannot_update_shadow_state() -> None:
    loop = _loop(sink=FailingSink("prediction_committed"))

    with pytest.raises(OSError, match="injected"):
        loop.prepare_observation(_obs(1, 10.0))

    state = loop.get_state(_target())
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
    assert loop.get_state(_target()).sample_count == 1
    assert loop.stats()["resolved"] == 2
    assert loop.stats()["state_update_applied"] == 1
    assert loop.stats()["unresolved"] == 0


def test_revealed_value_must_match_original_observation_commitment() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink)
    ticket = loop.prepare_observation(_obs(1, 10.0))

    outcome = loop.complete_numeric(ticket, 100.0)

    assert outcome.disposition is UnderstandingDisposition.CONTRADICTORY
    assert outcome.local_update is None
    assert loop.get_state(_target()).sample_count == 0
    assert "'value': 100.0" not in repr(sink.events)
    assert loop.stats()["commitment_mismatch"] == 1
    assert loop.stats()["resolved"] == 1
    assert loop.stats()["state_update_applied"] == 0


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
    assert loop.get_state(_target()).sample_count == 0


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
    assert loop.get_state(_target()).sample_count == 0
    assert loop.stats()["resolved"] == 0
    assert loop.stats()["unresolved"] == 1
    assert loop.stats()["resolution_conservation_ok"] is True
    sink.fail_kind = ""
    outcome = loop.complete_numeric(ticket, 10.0)
    assert outcome.disposition is UnderstandingDisposition.COLD_START
    assert loop.get_state(_target()).sample_count == 1
    assert loop.stats()["resolved"] == 1
    assert loop.stats()["state_update_applied"] == 1
    assert loop.stats()["unresolved"] == 0


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
    with pytest.raises(UnderstandingLoopError, match="unissued_prediction_ticket"):
        loop.complete_numeric(replace(ticket, source_seq=999), 10.0)
    assert loop.get_state(_target()).sample_count == 1


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


def test_sequence_and_completed_retention_are_bounded_fail_closed() -> None:
    policy = UnderstandingPolicyV1(
        max_seen_sequences=1,
        max_completed_outcomes=2,
    )
    loop = _loop(policy=policy)
    for seq in (1, 2, 3):
        ticket = loop.prepare_observation(_obs(seq, 10.0 + seq))
        loop.complete_numeric(ticket, 10.0 + seq)

    late = loop.prepare_observation(_obs(1, 11.0))
    outcome = loop.complete_numeric(late, 11.0)
    stats = loop.stats()

    assert outcome.disposition is UnderstandingDisposition.EXPIRED
    assert stats["seen_sequence_count"] <= 1
    assert stats["completed_outcome_count"] <= 2
    assert loop.get_state(_target()).sample_count == 3


def test_target_and_pending_capacity_are_bounded_without_authority() -> None:
    policy = UnderstandingPolicyV1(max_targets=1, max_pending_tickets=1)
    loop = _loop(policy=policy)
    first = loop.prepare_observation(_obs(1, 10.0))
    pending_drop = loop.prepare_observation(_obs(2, 11.0))
    pending_outcome = loop.complete_numeric(pending_drop, 11.0)
    loop.complete_numeric(first, 10.0)
    other = loop.prepare_observation(
        _obs(1, 20.0, entity="wd.synthetic.hive-2")
    )
    other_outcome = loop.complete_numeric(other, 20.0)

    assert pending_outcome.disposition is UnderstandingDisposition.DROPPED_BUDGET
    assert other_outcome.disposition is UnderstandingDisposition.SAMPLED_OUT
    stats = loop.stats()
    assert stats["state_count"] == 1
    assert stats["pending_ticket_count"] == 0
    assert stats["runtime_authority_applied"] is False


def test_returned_ticket_mutation_cannot_redirect_internal_state() -> None:
    loop = _loop()
    ticket = loop.prepare_observation(_obs(1, 10.0))
    issued_snapshot = copy.deepcopy(ticket)
    object.__setattr__(ticket, "target_key", _target("wd.synthetic.forged"))

    with pytest.raises(UnderstandingLoopError, match="unissued_prediction_ticket"):
        loop.complete_numeric(ticket, 10.0)

    loop.complete_numeric(issued_snapshot, 10.0)
    assert loop.get_state(_target()).sample_count == 1
    assert loop.get_state(_target("wd.synthetic.forged")).sample_count == 0


def test_predictor_and_get_state_receive_detached_state_snapshots() -> None:
    class MutatingPredictor:
        def predict(self, _header, prior_state):
            object.__setattr__(prior_state, "expected_value", 999.0)
            return None

    loop = _loop(predictor=MutatingPredictor())
    ticket = loop.prepare_observation(_obs(1, 10.0))
    assert ticket.prior_state.expected_value is None
    assert loop.get_state(_target()).expected_value is None

    exposed = loop.get_state(_target())
    object.__setattr__(exposed, "expected_value", 123.0)
    assert loop.get_state(_target()).expected_value is None


def test_sequence_reuse_quarantines_before_pending_capacity_check() -> None:
    loop = _loop(policy=UnderstandingPolicyV1(max_pending_tickets=1))
    loop.prepare_observation(_obs(1, 10.0))

    conflicting_observation = _obs(1, 11.0)
    conflicting_observation["quality"] = 0.8
    conflict = loop.prepare_observation(conflicting_observation)
    conflict_outcome = loop.complete_numeric(conflict, 11.0)
    later = loop.prepare_observation(_obs(2, 12.0))

    assert conflict_outcome.disposition is UnderstandingDisposition.CONTRADICTORY
    assert conflict.terminal_reason == "source_sequence_reuse"
    assert later.terminal_reason == "source_quarantined"


def test_pre_reveal_source_identity_is_independent_of_low_entropy_value() -> None:
    first_sink = InMemoryUnderstandingEventSink()
    second_sink = InMemoryUnderstandingEventSink()
    first = _loop(sink=first_sink)
    second = _loop(sink=second_sink)

    first_ticket = first.prepare_observation(_obs(1, 0.0))
    second_ticket = second.prepare_observation(_obs(1, 1.0))

    first_payload = first_sink.events[0]["payload"]
    second_payload = second_sink.events[0]["payload"]
    assert first_payload["source_sequence_identity_digest"] == second_payload[
        "source_sequence_identity_digest"
    ]
    assert (
        first_ticket.observation_commitment.commitment_digest
        != second_ticket.observation_commitment.commitment_digest
    )
    assert first_payload["observation_header"]["metadata_digest"] != (
        second_payload["observation_header"]["metadata_digest"]
    )


def test_custom_predictor_requires_explicit_identity_digests() -> None:
    with pytest.raises(
        UnderstandingLoopError,
        match="custom predictor requires explicit artifact and config digests",
    ):
        UnderstandingLoop(
            cell=_cell(),
            event_sink=InMemoryUnderstandingEventSink(),
            predictor=CapturingPredictor(),
        )


def test_prediction_ttl_expires_before_reveal_and_replays_exactly() -> None:
    clock = FakeClock()
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(
        sink=sink,
        clock=clock,
        policy=UnderstandingPolicyV1(prediction_ttl_seconds=1),
    )
    ticket = loop.prepare_observation(_obs(1, 10.0))
    clock.advance(2)

    outcome = loop.complete_numeric(ticket, 10.0)

    assert outcome.disposition is UnderstandingDisposition.EXPIRED
    assert outcome.disposition_record.reason_codes == (
        "prediction_ttl_exceeded",
    )
    assert "observation_revealed" not in [
        event["event_kind"] for event in sink.events
    ]
    replayed = replay_understanding_projection(sink.events)
    assert replayed.pending_ticket_count == 0


def test_prediction_ttl_exact_boundary_updates_and_epsilon_expires() -> None:
    boundary_clock = FakeClock()
    boundary = _loop(
        clock=boundary_clock,
        policy=UnderstandingPolicyV1(prediction_ttl_seconds=1),
    )
    boundary_ticket = boundary.prepare_observation(_obs(1, 10.0))
    boundary_clock.advance(1)

    boundary_outcome = boundary.complete_numeric(boundary_ticket, 10.0)

    assert boundary_outcome.disposition is UnderstandingDisposition.COLD_START
    assert boundary_outcome.local_update is not None
    assert boundary.get_state(_target()).sample_count == 1

    expired_clock = FakeClock()
    expired_sink = InMemoryUnderstandingEventSink()
    expired = _loop(
        sink=expired_sink,
        clock=expired_clock,
        policy=UnderstandingPolicyV1(prediction_ttl_seconds=1),
    )
    expired_ticket = expired.prepare_observation(_obs(1, 10.0))
    expired_clock.now += timedelta(seconds=1, microseconds=1)

    first = expired.complete_numeric(expired_ticket, 10.0)
    event_count = len(expired_sink.events)
    second = expired.complete_numeric(expired_ticket, 10.0)

    assert first == second
    assert first.disposition is UnderstandingDisposition.EXPIRED
    assert first.disposition_record.reason_codes == (
        "prediction_ttl_exceeded",
    )
    assert len(expired_sink.events) == event_count
    assert [event["event_kind"] for event in expired_sink.events] == [
        "prediction_committed",
        "disposition_recorded",
    ]
    assert expired.get_state(_target()).sample_count == 0


def test_resolution_clock_rollback_does_not_poison_pending_ticket() -> None:
    clock = FakeClock()
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(
        sink=sink,
        clock=clock,
        policy=UnderstandingPolicyV1(prediction_ttl_seconds=10),
    )
    ticket = loop.prepare_observation(_obs(1, 10.0))
    before = tuple(sink.events)
    clock.now -= timedelta(microseconds=1)

    with pytest.raises(UnderstandingLoopError, match="trusted clock moved"):
        loop.complete_numeric(ticket, 10.0)

    assert sink.events == before
    assert loop.stats()["pending_ticket_count"] == 1
    assert loop.get_state(_target()).sample_count == 0

    clock.now += timedelta(microseconds=2)
    recovered = loop.complete_numeric(ticket, 10.0)
    assert recovered.disposition is UnderstandingDisposition.COLD_START
    assert loop.get_state(_target()).sample_count == 1


def test_failed_resolution_refreshes_time_after_same_loop_head_advances(
    tmp_path,
    monkeypatch,
) -> None:
    clock = FakeClock()
    ledger = UnderstandingLedger(tmp_path / "resolution-time-retry.db")
    loop = UnderstandingLoop(
        cell=_cell(),
        event_sink=ledger,
        clock=clock,
        recover_from_verified_ledger=True,
    )
    first = loop.prepare_observation(_obs(1, 10.0))
    append_if_head = ledger.append_batch_if_head
    fail_once = True

    def failing_resolution(events, *, idempotency_key, expected_head):
        nonlocal fail_once
        if fail_once and any(kind == "observation_revealed" for kind, _ in events):
            fail_once = False
            raise OSError("injected pre-commit resolution failure")
        return append_if_head(
            events,
            idempotency_key=idempotency_key,
            expected_head=expected_head,
        )

    monkeypatch.setattr(ledger, "append_batch_if_head", failing_resolution)
    try:
        with pytest.raises(OSError, match="pre-commit"):
            loop.complete_numeric(first, 10.0)
        clock.advance(1)
        second = loop.prepare_observation(_obs(2, 11.0))

        recovered = loop.complete_numeric(first, 10.0)
        stale = loop.complete_numeric(second, 11.0)
        projection = project_understanding_ledger(ledger)

        assert recovered.disposition is UnderstandingDisposition.COLD_START
        assert recovered.disposition_record.recorded_at_utc == (
            "2026-08-02T12:00:01Z"
        )
        assert stale.disposition is UnderstandingDisposition.EXPIRED
        assert stale.disposition_record.reason_codes == (
            "stale_prior_generation",
        )
        assert projection.pending_ticket_count == 0
        assert projection.local_states[0]["generation"] == 1
    finally:
        loop.close()


def test_clock_cannot_move_behind_a_resolved_event_for_new_prediction() -> None:
    clock = FakeClock()
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink, clock=clock)
    first = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(first, 10.0)
    event_count = len(sink.events)
    clock.now -= timedelta(microseconds=1)

    with pytest.raises(UnderstandingLoopError, match="trusted clock moved"):
        loop.prepare_observation(_obs(2, 11.0))

    assert len(sink.events) == event_count
    assert loop.get_state(_target()).sample_count == 1


def test_new_ingress_expires_stale_pending_ticket_before_capacity_gate() -> None:
    clock = FakeClock()
    loop = _loop(
        clock=clock,
        policy=UnderstandingPolicyV1(
            max_pending_tickets=1,
            prediction_ttl_seconds=1,
        ),
    )
    stale = loop.prepare_observation(_obs(1, 10.0))
    clock.advance(2)

    current = loop.prepare_observation(_obs(2, 11.0))
    stale_outcome = loop.complete_numeric(stale, 10.0)

    assert current.prediction is not None
    assert current.terminal_disposition is None
    assert stale_outcome.disposition is UnderstandingDisposition.EXPIRED
    assert loop.stats()["pending_ticket_count"] == 1


def test_capacity_sweep_expires_every_stale_ticket_before_replacement() -> None:
    clock = FakeClock()
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(
        sink=sink,
        clock=clock,
        policy=UnderstandingPolicyV1(
            max_pending_tickets=2,
            prediction_ttl_seconds=1,
        ),
    )
    stale = (
        loop.prepare_observation(_obs(1, 10.0)),
        loop.prepare_observation(_obs(2, 11.0)),
    )
    clock.now += timedelta(seconds=1, microseconds=1)

    replacement = loop.prepare_observation(_obs(3, 12.0))

    assert replacement.prediction is not None
    assert loop.stats()["pending_ticket_count"] == 1
    dispositions = [
        event["payload"]
        for event in sink.events
        if event["event_kind"] == "disposition_recorded"
    ]
    assert {
        payload["ticket_id"] for payload in dispositions
    } == {ticket.ticket_id for ticket in stale}
    assert all(
        payload["reason_codes"] == ["prediction_ttl_exceeded"]
        for payload in dispositions
    )
    assert not any(
        event["event_kind"] in {"observation_revealed", "local_provisional_update"}
        and event["payload"].get("ticket_id")
        in {ticket.ticket_id for ticket in stale}
        for event in sink.events
    )


def test_quarantine_capacity_cannot_be_lower_than_target_capacity() -> None:
    with pytest.raises(
        Exception, match="max_quarantined_sources cannot be below max_targets"
    ):
        UnderstandingPolicyV1(max_targets=2, max_quarantined_sources=1)


def test_ingress_budget_bounds_terminal_audit_amplification() -> None:
    clock = FakeClock()
    sink = InMemoryUnderstandingEventSink()
    policy = UnderstandingPolicyV1(
        per_source_rate=1,
        per_source_burst=1,
        global_rate=1,
        global_burst=1,
    )
    loop = _loop(clock=clock, sink=sink, policy=policy)

    first = loop.prepare_observation(_obs(1, 1.0, privacy="private"))
    second = loop.prepare_observation(_obs(2, 2.0, privacy="private"))
    invalid_source = {**_obs(3, 3.0), "source": "untrusted"}
    third = loop.prepare_observation(invalid_source)

    assert loop.complete_numeric(first, 1.0).disposition is (
        UnderstandingDisposition.PRIVACY_BLOCKED
    )
    assert loop.complete_numeric(second, 2.0).disposition is (
        UnderstandingDisposition.DROPPED_BUDGET
    )
    assert loop.complete_numeric(third, 3.0).disposition is (
        UnderstandingDisposition.DROPPED_BUDGET
    )
    assert len(sink.events) == 1
    stats = loop.stats()
    assert stats["audit_suppressed"] == 2
    assert stats["counter_conservation_ok"] is True


def test_external_prepare_numeric_counts_received_and_uses_unique_terminal_ids() -> None:
    seed_sink = InMemoryUnderstandingEventSink()
    seed_loop = _loop(sink=seed_sink)
    seed_ticket = seed_loop.prepare_observation(_obs(1, 10.0))
    header = seed_sink.events[0]["payload"]["observation_header"]

    direct = _loop()
    accepted = direct.prepare_numeric(header, seed_ticket.observation_commitment)
    assert accepted.prediction is not None
    assert direct.stats()["received"] == direct.stats()["accepted"] == 1
    assert direct.stats()["counter_conservation_ok"] is True

    first_header = {**header, "entity_id": "outside.one"}
    second_header = {**header, "entity_id": "outside.two"}
    first = direct.prepare_numeric(first_header, seed_ticket.observation_commitment)
    second = direct.prepare_numeric(second_header, seed_ticket.observation_commitment)
    assert first.ticket_id != second.ticket_id


def test_external_prepare_numeric_classifies_pre_admission_failures_once() -> None:
    seed_sink = InMemoryUnderstandingEventSink()
    seed_loop = _loop(sink=seed_sink)
    seed_ticket = seed_loop.prepare_observation(_obs(1, 10.0))
    header = seed_sink.events[0]["payload"]["observation_header"]

    direct = _loop()
    with pytest.raises(UnderstandingLoopError, match="header shape"):
        direct.prepare_numeric({}, seed_ticket.observation_commitment)
    with pytest.raises(UnderstandingLoopError, match="commitment refused"):
        direct.prepare_numeric(header, object())  # type: ignore[arg-type]

    stats = direct.stats()
    assert stats["received"] == 2
    assert stats["invalid"] == 2
    assert stats["accounted"] == 2
    assert stats["counter_conservation_ok"] is True


def test_external_prepare_numeric_does_not_misclassify_predictor_failure() -> None:
    class RaisingPredictor:
        def predict(self, header, prior_state):
            raise UnderstandingLoopError("forced predictor failure")

    seed_sink = InMemoryUnderstandingEventSink()
    seed_loop = _loop(sink=seed_sink)
    seed_ticket = seed_loop.prepare_observation(_obs(1, 10.0))
    header = seed_sink.events[0]["payload"]["observation_header"]
    direct = _loop(predictor=RaisingPredictor())

    with pytest.raises(UnderstandingLoopError, match="forced predictor"):
        direct.prepare_numeric(header, seed_ticket.observation_commitment)

    assert direct.stats()["received"] == 1
    assert direct.stats()["invalid"] == 0


@pytest.mark.parametrize(
    "policy",
    (None, "", "ttl", "redact", "encrypt", "PLAINTEXT"),
)
def test_reveal_retention_policy_refuses_unimplemented_claims(policy) -> None:
    with pytest.raises(UnderstandingContractError, match="plaintext local retention"):
        UnderstandingPolicyV1(reveal_retention_policy=policy)


def test_retention_truth_is_aggregate_explicit_and_raw_free() -> None:
    clock = FakeClock()
    loop = _loop(clock=clock)
    ticket = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(ticket, 10.0)
    clock.advance(120)

    audit = loop.retention_truth()
    serialized = json.dumps(audit, sort_keys=True)

    assert audit["reveal_retention_policy"] == (
        PLAINTEXT_REVEAL_RETENTION_POLICY_V1
    )
    assert audit["raw_reveal_event_count"] == 1
    assert audit["oldest_reveal_recorded_at_utc"] == "2026-08-02T12:00:00Z"
    assert audit["oldest_reveal_age_seconds"] == 120.0
    assert audit["timestamp_coverage_complete"] is True
    assert audit["semantic_replay_verified"] is True
    assert audit["durable_verified_local_ledger"] is False
    assert audit["plaintext_reconstructive_fields_retained_by_v1_contract"] is True
    assert audit["plaintext_reconstructive_reveal_material_present"] is True
    assert audit["full_semantic_replay_requires_plaintext"] is True
    assert audit["retention_deletion_supported"] is False
    assert audit["retention_encryption_supported"] is False
    assert audit["public_projection_raw_free"] is True
    assert audit["runtime_authority_applied"] is False
    assert audit["routing_influence_applied"] is False
    assert loop.stats()["public_projection_raw_free_by_v1_contract"] is True
    assert "public_projection_raw_free" not in loop.stats()
    assert '"value"' not in serialized
    assert '"residual"' not in serialized
    assert '"commitment_nonce"' not in serialized


def test_retention_truth_fails_closed_on_semantically_orphaned_reveal() -> None:
    source_sink = InMemoryUnderstandingEventSink()
    source = _loop(sink=source_sink)
    ticket = source.prepare_observation(_obs(1, 10.0))
    source.complete_numeric(ticket, 10.0)
    orphan_events = tuple(
        (event["event_kind"], event["payload"])
        for event in source_sink.events
        if event["event_kind"] in {"observation_revealed", "disposition_recorded"}
    )
    orphan_sink = InMemoryUnderstandingEventSink()
    orphan_sink.append_batch(orphan_events, idempotency_key="orphan-reveal")

    with pytest.raises(UnderstandingLoopError, match="semantic replay refused"):
        _loop(sink=orphan_sink).retention_truth()


def test_retention_truth_computes_negative_projection_raw_free_flag(
    monkeypatch,
) -> None:
    loop = _loop()
    ticket = loop.prepare_observation(_obs(1, 10.0))
    loop.complete_numeric(ticket, 10.0)
    replay = understanding_projection_module.replay_understanding_projection

    class ProjectionWithForbiddenKey:
        def __init__(self, events) -> None:
            self._projection = replay(events)

        def to_mapping(self) -> dict:
            return {**self._projection.to_mapping(), "value": 10.0}

    monkeypatch.setattr(
        understanding_projection_module,
        "replay_understanding_projection",
        ProjectionWithForbiddenKey,
    )

    assert loop.retention_truth()["public_projection_raw_free"] is False


def test_retention_truth_uses_verified_durable_restart_view(tmp_path) -> None:
    clock = FakeClock()
    path = tmp_path / "retention-truth.db"
    first = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        clock=clock,
    )
    ticket = first.prepare_observation(_obs(1, 10.0))
    first.complete_numeric(ticket, 10.0)
    first.close()

    second = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        clock=clock,
        recover_from_verified_ledger=True,
    )
    audit = second.retention_truth()

    assert audit["semantic_replay_verified"] is True
    assert audit["durable_verified_local_ledger"] is True
    assert audit["raw_reveal_event_count"] == 1
    assert audit["public_projection_raw_free"] is True
    second.close()


def test_retention_policy_literal_is_bound_into_learning_policy_digest() -> None:
    sink = InMemoryUnderstandingEventSink()
    loop = _loop(sink=sink)
    loop.prepare_observation(_obs(1, 10.0))
    policy_mapping = dict(loop.policy.__dict__)
    expected_digest = sha256_digest(
        {"domain": "wd.understanding_policy.v1", "policy": policy_mapping}
    )
    legacy_mapping = dict(policy_mapping)
    del legacy_mapping["reveal_retention_policy"]
    legacy_digest = sha256_digest(
        {"domain": "wd.understanding_policy.v1", "policy": legacy_mapping}
    )

    persisted_digest = sink.events[0]["payload"]["learning_policy_digest"]
    assert persisted_digest == expected_digest
    assert persisted_digest != legacy_digest


def test_in_memory_batch_persists_the_same_snapshot_it_digests() -> None:
    sink = InMemoryUnderstandingEventSink()
    payload = {
        "nested": {"value": "before"},
        "runtime_authority_applied": False,
    }
    sink.append_batch((("snapshot_test", payload),), idempotency_key="snapshot")
    payload["nested"]["value"] = "after"

    assert sink.events[0]["payload"]["nested"]["value"] == "before"


def test_verified_ledger_restart_hydrates_numeric_state_and_sequences(
    tmp_path,
) -> None:
    path = tmp_path / "understanding-restart.db"
    first = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        recover_from_verified_ledger=True,
    )
    ticket = first.prepare_observation(_obs(1, 10.0))
    first.complete_numeric(ticket, 10.0)
    first.close()

    second_ledger = UnderstandingLedger(path)
    second = UnderstandingLoop(
        cell=_cell(),
        event_sink=second_ledger,
        recover_from_verified_ledger=True,
    )
    next_ticket = second.prepare_observation(_obs(2, 20.0))

    assert next_ticket.prediction is not None
    assert next_ticket.prediction.ingest_seq == 2
    assert next_ticket.prediction.predicted_value == 10.0
    assert next_ticket.prior_state.generation == 1
    second.complete_numeric(next_ticket, 20.0)
    assert second.get_state(_target()).sample_count == 2
    projection = project_understanding_ledger(second_ledger)
    assert projection.pending_ticket_count == 0
    assert projection.runtime_authority_applied is False
    second.close()


def test_restart_expires_prediction_that_lost_secret_reveal_context(
    tmp_path,
) -> None:
    path = tmp_path / "understanding-pending.db"
    first = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        recover_from_verified_ledger=True,
    )
    lost_ticket = first.prepare_observation(_obs(1, 10.0))
    first.close()

    ledger = UnderstandingLedger(path)
    restarted = UnderstandingLoop(
        cell=_cell(),
        event_sink=ledger,
        recover_from_verified_ledger=True,
    )
    projection = project_understanding_ledger(ledger)

    assert projection.pending_ticket_count == 0
    assert dict(projection.disposition_counts)["expired"] == 1
    with pytest.raises(UnderstandingLoopError, match="unissued_prediction_ticket"):
        restarted.complete_numeric(lost_ticket, 10.0)

    replacement = restarted.prepare_observation(_obs(2, 11.0))
    assert replacement.prediction is not None
    assert replacement.prediction.ingest_seq == 2
    assert replacement.prediction.predicted_value is None
    restarted.complete_numeric(replacement, 11.0)
    assert restarted.get_state(_target()).sample_count == 1
    restarted.close()


def test_restart_reconciliation_retries_when_live_completion_wins_head_cas(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "understanding-restart-race.db"
    clock = FakeClock()
    live_ledger = UnderstandingLedger(path)
    live = UnderstandingLoop(
        cell=_cell(),
        event_sink=live_ledger,
        clock=clock,
        recover_from_verified_ledger=True,
    )
    ticket = live.prepare_observation(_obs(1, 10.0))
    restart_ledger = UnderstandingLedger(path)
    append_if_head = restart_ledger.append_batch_if_head
    raced = False

    def racing_append(events, *, idempotency_key, expected_head):
        nonlocal raced
        if not raced:
            raced = True
            live.complete_numeric(ticket, 10.0)
        return append_if_head(
            events,
            idempotency_key=idempotency_key,
            expected_head=expected_head,
        )

    monkeypatch.setattr(restart_ledger, "append_batch_if_head", racing_append)
    restarted = None
    try:
        restarted = UnderstandingLoop(
            cell=_cell(),
            event_sink=restart_ledger,
            clock=clock,
            recover_from_verified_ledger=True,
        )
        projection = project_understanding_ledger(restart_ledger)
        ticket_dispositions = [
            event["payload"]
            for event in restart_ledger.events
            if event["event_kind"] == "disposition_recorded"
            and event["payload"]["ticket_id"] == ticket.ticket_id
        ]

        assert raced is True
        assert len(ticket_dispositions) == 1
        assert ticket_dispositions[0]["reason_codes"] == [
            "no_prior_shadow_state"
        ]
        assert projection.pending_ticket_count == 0
        assert restarted.get_state(_target()).sample_count == 1
    finally:
        if restarted is not None:
            restarted.close()
        else:
            restart_ledger.close()
        live.close()


def test_no_pending_restart_snapshot_replays_again_when_live_head_wins(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "understanding-restart-no-pending-race.db"
    clock = FakeClock()
    live_ledger = UnderstandingLedger(path)
    live = UnderstandingLoop(
        cell=_cell(),
        event_sink=live_ledger,
        clock=clock,
        recover_from_verified_ledger=True,
    )
    first = live.prepare_observation(_obs(1, 10.0))
    live.complete_numeric(first, 10.0)
    restart_ledger = UnderstandingLedger(path)
    original_reduce = (
        understanding_projection_module.reduce_understanding_ledger_restart_checkpoint
    )
    raced = False

    def racing_reduce(ledger, *, expected_head=None):
        nonlocal raced
        checkpoint = original_reduce(ledger, expected_head=expected_head)
        if not raced:
            raced = True
            second = live.prepare_observation(_obs(2, 11.0))
            live.complete_numeric(second, 11.0)
        return checkpoint

    monkeypatch.setattr(
        understanding_projection_module,
        "reduce_understanding_ledger_restart_checkpoint",
        racing_reduce,
    )
    restarted = None
    try:
        restarted = UnderstandingLoop(
            cell=_cell(),
            event_sink=restart_ledger,
            clock=clock,
            recover_from_verified_ledger=True,
        )
        third = restarted.prepare_observation(_obs(3, 12.0))
        restarted.complete_numeric(third, 12.0)

        assert raced is True
        assert third.prediction is not None
        assert third.prediction.ingest_seq == 3
        assert third.prior_state.generation == 2
        projection = project_understanding_ledger(restart_ledger)
        assert projection.pending_ticket_count == 0
        assert projection.local_states[0]["generation"] == 3
    finally:
        if restarted is not None:
            restarted.close()
        else:
            restart_ledger.close()
        live.close()


def test_recovered_loop_refuses_to_write_after_external_head_advance(
    tmp_path,
) -> None:
    path = tmp_path / "understanding-post-recovery-head-race.db"
    clock = FakeClock()
    ledger = UnderstandingLedger(path)
    recovered = UnderstandingLoop(
        cell=_cell(),
        event_sink=ledger,
        clock=clock,
        recover_from_verified_ledger=True,
    )
    competitor = UnderstandingLedger(path)
    try:
        competitor.append_event(
            "disposition_recorded",
            {
                "ticket_id": sha256_digest({"competitor": "ticket"}),
                "observation_commitment_digest": sha256_digest(
                    {"competitor": "observation"}
                ),
                "prediction_digest": None,
                "disposition": "privacy_blocked",
                "reason_codes": ["external_competitor_probe"],
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
            },
        )
        winning_count = competitor.event_count

        with pytest.raises(
            UnderstandingLoopError,
            match="durable ledger advanced",
        ):
            recovered.prepare_observation(_obs(1, 10.0))

        assert competitor.event_count == winning_count
        assert project_understanding_ledger(competitor).event_count == winning_count
    finally:
        competitor.close()
        recovered.close()


def test_restart_clock_rollback_fails_before_expiry_append_and_can_recover(
    tmp_path,
) -> None:
    path = tmp_path / "understanding-restart-clock.db"
    clock = FakeClock()
    first = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        clock=clock,
        recover_from_verified_ledger=True,
    )
    first.prepare_observation(_obs(1, 10.0))
    first.close()
    clock.now -= timedelta(microseconds=1)

    rolled_back_ledger = UnderstandingLedger(path)
    try:
        with pytest.raises(UnderstandingLoopError, match="trusted clock moved"):
            UnderstandingLoop(
                cell=_cell(),
                event_sink=rolled_back_ledger,
                clock=clock,
                recover_from_verified_ledger=True,
            )
        assert rolled_back_ledger.event_count == 1
        assert project_understanding_ledger(
            rolled_back_ledger
        ).pending_ticket_count == 1
    finally:
        rolled_back_ledger.close()

    clock.now += timedelta(microseconds=1)
    recovered_ledger = UnderstandingLedger(path)
    recovered = UnderstandingLoop(
        cell=_cell(),
        event_sink=recovered_ledger,
        clock=clock,
        recover_from_verified_ledger=True,
    )
    try:
        assert recovered_ledger.event_count == 2
        assert project_understanding_ledger(
            recovered_ledger
        ).pending_ticket_count == 0
    finally:
        recovered.close()


def test_restart_refuses_impossible_partial_reveal_lifecycle(tmp_path) -> None:
    path = tmp_path / "understanding-partial-reveal.db"
    ledger = UnderstandingLedger(path)
    first = UnderstandingLoop(cell=_cell(), event_sink=ledger)
    ticket = first.prepare_observation(_obs(1, 10.0))
    ledger.append_event(
        "observation_revealed",
        {
            "ticket_id": ticket.ticket_id,
            "observation_commitment_digest": (
                ticket.observation_commitment.commitment_digest
            ),
            "value": 10.0,
            "privacy_domain": ticket.observation_commitment.privacy_domain,
            "commitment_nonce": ticket.observation_commitment.nonce,
        },
    )
    first.close()

    with UnderstandingLedger(path) as reopened:
        with pytest.raises(
            UnderstandingLoopError,
            match="revealed pending prediction cannot be safely recovered",
        ):
            UnderstandingLoop(
                cell=_cell(),
                event_sink=reopened,
                recover_from_verified_ledger=True,
            )


def test_verified_restart_flag_rejects_ephemeral_sink() -> None:
    with pytest.raises(
        UnderstandingLoopError,
        match="requires UnderstandingLedger",
    ):
        UnderstandingLoop(
            cell=_cell(),
            event_sink=InMemoryUnderstandingEventSink(),
            recover_from_verified_ledger=True,
        )
