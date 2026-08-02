from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from waggledance.core.learning.understanding_contracts import (
    HexCellAddressV1,
    KnowledgeClaimKind,
    KnowledgeDeltaV1,
    PrivacyClass,
    derive_learning_domain_digest,
)
from waggledance.core.learning.understanding_loop import UnderstandingLoop
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.understanding_ledger import (
    DISPOSITION_RECORDED,
    KNOWLEDGE_DELTA_PROPOSED,
    KNOWLEDGE_DELTA_RETRACTED,
    LOCAL_PROVISIONAL_UPDATE,
    OBSERVATION_REVEALED,
    PREDICTION_COMMITTED,
    UnderstandingLedger,
    UnderstandingLedgerCorruptionError,
    build_understanding_event,
)
from waggledance.core.magma.understanding_projection import (
    UnderstandingProjectionError,
    project_understanding_ledger,
    reduce_understanding_restart_checkpoint,
    replay_understanding_projection,
)


NOW = datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc)


def _cell() -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id="cell-projection",
        q=1,
        r=-1,
        incarnation_id="inc-projection",
        generation=2,
        fence=11,
    )


def _observation(value: float = 37.125, source_seq: int = 1) -> dict[str, object]:
    return {
        "observation_id": f"projection-obs-{source_seq}",
        "source": "mqtt",
        "source_seq": source_seq,
        "entity_id": "wd.synthetic.projection",
        "metric": "temperature",
        "unit": "Cel",
        "value": value,
        "quality": 0.8,
        "privacy_class": "synthetic",
        "observed_at_utc": "2026-08-02T13:00:00Z",
        "metadata": {"fixture": "projection"},
    }


def _resolved_ledger(tmp_path, value: float = 37.125) -> UnderstandingLedger:
    ledger = UnderstandingLedger(tmp_path / "projection.db")
    loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
    ticket = loop.prepare_observation(_observation(value))
    loop.complete_numeric(ticket, value)
    return ledger


def _one_resolved_then_pending_events(tmp_path):
    ledger = UnderstandingLedger(tmp_path / "resolved-then-pending.db")
    loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
    first = loop.prepare_observation(_observation(10.0, 1))
    loop.complete_numeric(first, 10.0)
    loop.prepare_observation(_observation(20.0, 2))
    try:
        return ledger.events
    finally:
        ledger.close()


def _two_resolved_events(tmp_path):
    ledger = UnderstandingLedger(tmp_path / "two-resolved.db")
    loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
    for source_seq, value in ((1, 10.0), (2, 20.0)):
        ticket = loop.prepare_observation(_observation(value, source_seq))
        loop.complete_numeric(ticket, value)
    try:
        return ledger.events
    finally:
        ledger.close()


def _retarget_prediction(
    payload,
    *,
    source_seq=None,
    ingest_seq=None,
    incarnation_id=None,
    committed_at_utc=None,
):
    result = copy.deepcopy(payload)
    if source_seq is not None:
        result["source_seq"] = source_seq
        result["observation_header"]["source_seq"] = source_seq
    if ingest_seq is not None:
        result["observation_header"]["ingest_seq"] = ingest_seq
        result["prediction"]["ingest_seq"] = ingest_seq
    if incarnation_id is not None:
        result["cell"]["incarnation_id"] = incarnation_id
        result["observation_header"]["cell"]["incarnation_id"] = incarnation_id
    if committed_at_utc is not None:
        result["prediction"]["committed_at_utc"] = committed_at_utc
    result["learning_domain_digest"] = derive_learning_domain_digest(
        cell=result["cell"],
        source=result["observation_header"]["source"],
        metric=result["observation_header"]["metric"],
        unit=result["observation_header"]["unit"],
        learning_policy_digest=result["learning_policy_digest"],
        predictor_artifact_digest=result["prediction"][
            "predictor_artifact_digest"
        ],
        predictor_config_digest=result["prediction"][
            "predictor_config_digest"
        ],
        residual_abs_threshold=result["residual_abs_threshold"],
        prediction_ttl_seconds=result["prediction_ttl_seconds"],
        state_update_alpha=result["state_update_alpha"],
    )
    result["prediction_digest"] = sha256_digest(
        {
            "domain": "wd.prediction_commitment.digest.v1",
            **result["prediction"],
        }
    )
    result["ticket_id"] = sha256_digest(
        {
            "domain": "wd.prediction_ticket.v1",
            "prediction_digest": result["prediction_digest"],
            "source_key": result["source_key"],
            "source_seq": result["source_seq"],
        }
    )
    return result


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def _delta_payload(tag: str = "one") -> dict[str, object]:
    evidence = tuple(sha256_digest({"evidence": index}) for index in range(5))
    delta = KnowledgeDeltaV1(
        proposal_id=f"proposal-{tag}",
        proposer_cell_id="cell-projection",
        claim_kind=KnowledgeClaimKind.MODEL_UPDATE,
        aggregate_digest=sha256_digest({"aggregate": tag}),
        evidence_refs=evidence,
        confidence=0.75,
        privacy_class=PrivacyClass.SYNTHETIC,
        created_at_utc="2026-08-02T13:00:00Z",
        expires_at_utc="2026-08-02T13:15:00Z",
    )
    return {
        "delta": delta.to_mapping(),
        "proposal_digest": delta.proposal_digest,
        "hive_commit_applied": False,
        "runtime_authority_applied": False,
        "routing_influence_applied": False,
    }


def _retraction_payload(proposal_digest: str, tag: str = "one") -> dict[str, object]:
    return {
        "proposal_digest": proposal_digest,
        "retraction_id": f"retraction-{tag}",
        "reason_codes": ["counterevidence_confirmed"],
        "evidence_refs": [sha256_digest({"counterevidence": tag})],
        "retracted_at_utc": "2026-08-02T13:05:00Z",
        "runtime_authority_applied": False,
        "routing_influence_applied": False,
    }


def _extend_chain(events, pairs):
    result = [dict(event) for event in events]
    predecessor = result[-1]["event_hash"] if result else "sha256:" + "0" * 64
    sequence = len(result) + 1
    for kind, payload in pairs:
        event = build_understanding_event(
            kind, payload, seq=sequence, prev_event_hash=predecessor
        )
        result.append(event)
        predecessor = event["event_hash"]
        sequence += 1
    return result


def _expiry_payload(
    prediction_payload: dict[str, object],
    *,
    recorded_at_utc: str,
    reason: str,
) -> dict[str, object]:
    prediction = prediction_payload["prediction"]
    assert isinstance(prediction, dict)
    return {
        "schema_version": "wd.understanding_disposition.v1",
        "ticket_id": prediction_payload["ticket_id"],
        "observation_commitment_digest": prediction[
            "observation_commitment_digest"
        ],
        "prediction_digest": prediction_payload["prediction_digest"],
        "disposition": "expired",
        "residual": None,
        "reason_codes": [reason],
        "recorded_at_utc": recorded_at_utc,
        "runtime_authority_applied": False,
        "routing_influence_applied": False,
    }


def test_replay_matches_durable_head_and_excludes_all_observation_values(tmp_path) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        projection = project_understanding_ledger(ledger, expected_head=ledger.head)
        mapping = projection.to_mapping()
        assert projection.event_count == 4
        assert projection.ledger_head == ledger.head
        assert projection.pending_ticket_count == 0
        assert projection.resolved_ticket_count == 1
        assert projection.local_states[0]["generation"] == 1
        assert projection.local_states[0]["sample_count"] == 1
        assert projection.runtime_authority_applied is False
        assert mapping["runtime_authority_applied"] is False
        assert mapping["routing_influence_applied"] is False
        assert mapping["hive_commit_applied"] is False

        forbidden = {
            "value",
            "predicted_value",
            "expected_value",
            "residual",
            "commitment_nonce",
            "observation_header",
            "next_state",
        }
        assert not (_walk_keys(mapping) & forbidden)
        assert b"37.125" not in canonical_json_bytes(mapping)

        second = replay_understanding_projection(ledger.events)
        assert second.to_mapping() == mapping
        assert second.projection_digest == projection.projection_digest
    finally:
        ledger.close()


def test_crash_after_prediction_is_visible_as_pending_without_state_update(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "pending.db") as ledger:
        loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
        loop.prepare_observation(_observation())
        projection = project_understanding_ledger(ledger)
        assert projection.pending_ticket_count == 1
        assert projection.resolved_ticket_count == 0
        assert projection.local_states == ()


def test_replay_enforces_prediction_deadline_and_restart_timestamp(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "temporal-replay.db") as ledger:
        loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
        loop.prepare_observation(_observation())
        prediction_event = ledger.events[0]
    payload = prediction_event["payload"]

    exact_deadline = _extend_chain(
        [prediction_event],
        [
            (
                DISPOSITION_RECORDED,
                _expiry_payload(
                    payload,
                    recorded_at_utc="2026-08-02T13:05:00Z",
                    reason="prediction_ttl_exceeded",
                ),
            )
        ],
    )
    with pytest.raises(UnderstandingProjectionError, match="deadline"):
        replay_understanding_projection(exact_deadline)

    after_deadline = _extend_chain(
        [prediction_event],
        [
            (
                DISPOSITION_RECORDED,
                _expiry_payload(
                    payload,
                    recorded_at_utc="2026-08-02T13:05:00.000001Z",
                    reason="prediction_ttl_exceeded",
                ),
            )
        ],
    )
    assert replay_understanding_projection(after_deadline).pending_ticket_count == 0

    before_commit = _extend_chain(
        [prediction_event],
        [
            (
                DISPOSITION_RECORDED,
                _expiry_payload(
                    payload,
                    recorded_at_utc="2026-08-02T12:59:59Z",
                    reason="restart_lost_reveal_context",
                ),
            )
        ],
    )
    with pytest.raises(UnderstandingProjectionError, match="predates"):
        replay_understanding_projection(before_commit)


def test_restart_checkpoint_contains_exact_local_state_but_has_no_serializer(
    tmp_path,
) -> None:
    events = _one_resolved_then_pending_events(tmp_path)
    checkpoint = reduce_understanding_restart_checkpoint(events)
    target_key = events[0]["payload"]["target_key"]
    pending_id = events[-1]["payload"]["ticket_id"]

    assert checkpoint.max_ingest_seq == 2
    assert checkpoint.numeric_states[target_key].expected_value == 10.0
    assert checkpoint.numeric_states[target_key].generation == 1
    assert checkpoint.source_high_watermarks[
        events[-1]["payload"]["source_key"]
    ] == 2
    assert len(checkpoint.source_sequence_registry) == 2
    assert checkpoint.cell_ingest_high_watermarks[
        ("cell-projection", "inc-projection")
    ] == 2
    assert checkpoint.pending_tickets[pending_id].prior_expected_value == 10.0
    assert checkpoint.pending_tickets[pending_id].predicted_value == 10.0
    assert checkpoint.pending_tickets[pending_id].committed_at_utc == (
        "2026-08-02T13:00:00Z"
    )
    assert checkpoint.pending_tickets[pending_id].prediction_ttl_seconds == 300
    assert checkpoint.pending_tickets[pending_id].revealed_value is None
    assert checkpoint.trusted_time_watermark_utc == "2026-08-02T13:00:00Z"
    assert not hasattr(checkpoint, "to_mapping")
    with pytest.raises(TypeError):
        checkpoint.source_high_watermarks["forged"] = 99
    with pytest.raises(TypeError):
        checkpoint.pending_tickets[pending_id].observation_header["value"] = 20.0


def test_restart_checkpoint_reduces_the_exact_non_cold_start_ewma(tmp_path) -> None:
    events = _two_resolved_events(tmp_path)
    checkpoint = reduce_understanding_restart_checkpoint(events)
    target_key = events[0]["payload"]["target_key"]
    assert checkpoint.numeric_states[target_key].expected_value == 11.0
    assert checkpoint.numeric_states[target_key].generation == 2
    assert checkpoint.numeric_states[target_key].sample_count == 2


def test_retraction_is_append_only_and_retains_retracted_proposal(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "retraction.db") as ledger:
        proposal = _delta_payload()
        ledger.append_event(KNOWLEDGE_DELTA_PROPOSED, proposal)
        before = project_understanding_ledger(ledger)
        assert before.active_knowledge_delta_count == 1

        ledger.append_event(
            KNOWLEDGE_DELTA_RETRACTED,
            _retraction_payload(proposal["proposal_digest"]),
        )
        after = project_understanding_ledger(ledger)
        assert ledger.event_count == 2
        assert after.active_knowledge_delta_count == 0
        assert after.knowledge_deltas[0]["status"] == "retracted"
        assert after.knowledge_deltas[0]["retraction_id"] == "retraction-one"
        assert after.retractions[0]["proposal_digest"] == proposal["proposal_digest"]
        assert after.knowledge_deltas[0]["hive_commit_applied"] is False
        with pytest.raises(TypeError):
            after.knowledge_deltas[0]["status"] = "active"
        with pytest.raises(TypeError):
            after.knowledge_deltas[0]["evidence_refs"][0] = "forged"


def test_unknown_and_double_retraction_fail_closed() -> None:
    proposal = _delta_payload()
    unknown = _extend_chain(
        [],
        [
            (
                KNOWLEDGE_DELTA_RETRACTED,
                _retraction_payload(proposal["proposal_digest"]),
            )
        ],
    )
    with pytest.raises(UnderstandingProjectionError, match="unknown proposal"):
        replay_understanding_projection(unknown)

    double = _extend_chain(
        [],
        [
            (KNOWLEDGE_DELTA_PROPOSED, proposal),
            (
                KNOWLEDGE_DELTA_RETRACTED,
                _retraction_payload(proposal["proposal_digest"], "first"),
            ),
            (
                KNOWLEDGE_DELTA_RETRACTED,
                _retraction_payload(proposal["proposal_digest"], "second"),
            ),
        ],
    )
    with pytest.raises(UnderstandingProjectionError, match="already retracted"):
        replay_understanding_projection(double)


def test_reveal_without_prediction_fails_lifecycle_even_with_valid_hash_chain() -> None:
    reveal = {
        "ticket_id": sha256_digest({"missing": "ticket"}),
        "observation_commitment_digest": sha256_digest({"missing": "observation"}),
        "value": 2.0,
        "privacy_domain": "wd.observation.synthetic.v1",
        "commitment_nonce": "a" * 32,
    }
    events = _extend_chain([], [(OBSERVATION_REVEALED, reveal)])
    with pytest.raises(UnderstandingProjectionError, match="no prediction"):
        replay_understanding_projection(events)


def test_replay_independently_refuses_a_forged_reveal_value(tmp_path) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        original = ledger.events
    finally:
        ledger.close()
    forged_reveal = dict(original[1]["payload"])
    forged_reveal["value"] = 37.5
    forged = _extend_chain(
        [original[0]], [(OBSERVATION_REVEALED, forged_reveal)]
    )
    with pytest.raises(UnderstandingProjectionError, match="does not open commitment"):
        replay_understanding_projection(forged)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("residual", 0.0, "residual is not exact"),
        ("disposition", "explained", "does not match residual"),
        ("reason_codes", ["unexplained_residual"], "reason_codes"),
    ],
)
def test_semantic_replay_refuses_forged_forecast_resolution(
    tmp_path, field, replacement, message
) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        original = ledger.events
    finally:
        ledger.close()
    disposition = copy.deepcopy(original[2]["payload"])
    disposition[field] = replacement
    forged = _extend_chain(
        original[:2], [(DISPOSITION_RECORDED, disposition)]
    )
    with pytest.raises(UnderstandingProjectionError, match=message):
        replay_understanding_projection(forged)


def test_forecast_disposition_requires_a_durable_reveal(tmp_path) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        original = ledger.events
    finally:
        ledger.close()
    forged = _extend_chain(
        original[:1], [(DISPOSITION_RECORDED, original[2]["payload"])]
    )
    with pytest.raises(UnderstandingProjectionError, match="lacks reveal"):
        replay_understanding_projection(forged)


def test_semantic_replay_refuses_digest_consistent_wrong_ewma_state(tmp_path) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        original = ledger.events
    finally:
        ledger.close()
    update_payload = copy.deepcopy(original[3]["payload"])
    update = update_payload["update"]
    state = update_payload["next_state"]
    state["expected_value"] = 99.0
    state["state_digest"] = sha256_digest(
        {
            "domain": "wd.understanding_state.v1",
            "target_key": state["target_key"],
            "generation": state["generation"],
            "expected_value": state["expected_value"],
            "sample_count": state["sample_count"],
            "prediction_digest": update["prediction_digest"],
        }
    )
    update["new_state_digest"] = state["state_digest"]
    update_payload["update_digest"] = sha256_digest(
        {
            "domain": "wd.local_provisional_update.digest.v1",
            **update,
        }
    )
    forged = _extend_chain(
        original[:3], [(LOCAL_PROVISIONAL_UPDATE, update_payload)]
    )
    with pytest.raises(UnderstandingProjectionError, match="exact V1 EWMA"):
        replay_understanding_projection(forged)


def test_semantic_replay_binds_local_update_time_to_disposition(tmp_path) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        original = ledger.events
    finally:
        ledger.close()
    update_payload = copy.deepcopy(original[3]["payload"])
    update_payload["update"]["applied_at_utc"] = "2030-01-01T00:00:00Z"
    update_payload["update_digest"] = sha256_digest(
        {
            "domain": "wd.local_provisional_update.digest.v1",
            **update_payload["update"],
        }
    )
    forged = _extend_chain(
        original[:3],
        [(LOCAL_PROVISIONAL_UPDATE, update_payload)],
    )

    with pytest.raises(UnderstandingProjectionError, match="update time"):
        replay_understanding_projection(forged)


@pytest.mark.parametrize(
    ("source_seq", "message"),
    [(1, "already accepted"), (0, "strictly monotonic")],
)
def test_replay_refuses_reused_or_nonmonotonic_source_sequence(
    tmp_path, source_seq, message
) -> None:
    events = _one_resolved_then_pending_events(tmp_path)
    second_prediction = _retarget_prediction(
        events[-1]["payload"], source_seq=source_seq
    )
    forged = _extend_chain(
        events[:4], [(PREDICTION_COMMITTED, second_prediction)]
    )
    with pytest.raises(UnderstandingProjectionError, match=message):
        replay_understanding_projection(forged)


def test_ingest_sequence_is_monotonic_and_cell_rebuild_requires_new_ledger(
    tmp_path,
) -> None:
    events = _one_resolved_then_pending_events(tmp_path)
    duplicate_ingest = _retarget_prediction(events[-1]["payload"], ingest_seq=1)
    forged = _extend_chain(
        events[:4], [(PREDICTION_COMMITTED, duplicate_ingest)]
    )
    with pytest.raises(UnderstandingProjectionError, match="ingest_seq"):
        replay_understanding_projection(forged)

    rebuilt_cell = _retarget_prediction(
        events[-1]["payload"], ingest_seq=1, incarnation_id="inc-rebuilt"
    )
    rebuilt = _extend_chain(
        events[:4], [(PREDICTION_COMMITTED, rebuilt_cell)]
    )
    with pytest.raises(
        UnderstandingProjectionError,
        match="changes the ledger learning domain",
    ):
        replay_understanding_projection(rebuilt)


def test_replay_refuses_prediction_that_moves_trusted_time_backwards(tmp_path) -> None:
    events = _one_resolved_then_pending_events(tmp_path)
    backwards = _retarget_prediction(
        events[-1]["payload"],
        committed_at_utc="2026-08-02T12:59:59Z",
    )
    forged = _extend_chain(
        events[:4],
        [(PREDICTION_COMMITTED, backwards)],
    )

    with pytest.raises(UnderstandingProjectionError, match="time backwards"):
        replay_understanding_projection(forged)


def test_projection_mapping_is_detached_from_internal_result(tmp_path) -> None:
    ledger = _resolved_ledger(tmp_path)
    try:
        projection = project_understanding_ledger(ledger)
        exposed = projection.to_mapping()
        exposed["tickets"][0]["lifecycle"] = "forged"
        assert projection.tickets[0]["lifecycle"] == "resolved"
        assert projection.to_mapping()["tickets"][0]["lifecycle"] == "resolved"
        with pytest.raises(TypeError):
            projection.tickets[0]["lifecycle"] = "forged"
        object.__setattr__(projection, "runtime_authority_applied", True)
        with pytest.raises(
            UnderstandingProjectionError,
            match="cannot apply runtime authority",
        ):
            projection.to_mapping()
        object.__setattr__(projection, "runtime_authority_applied", False)

        forged_authority = dict(projection.tickets[0])
        forged_authority["runtime_authority_applied"] = True
        with pytest.raises(
            UnderstandingProjectionError,
            match="must be literal false",
        ):
            replace(projection, tickets=(forged_authority,))
        object.__setattr__(projection, "tickets", (forged_authority,))
        with pytest.raises(
            UnderstandingProjectionError,
            match="must be literal false",
        ):
            projection.to_mapping()

        smuggled = dict(forged_authority)
        smuggled["raw_value"] = 37.125
        object.__setattr__(projection, "tickets", (smuggled,))
        with pytest.raises(UnderstandingProjectionError, match="allowlist"):
            projection.to_mapping()
    finally:
        ledger.close()


def test_replay_refuses_hash_consistent_raw_authority_true_event() -> None:
    valid = _extend_chain(
        [],
        [(KNOWLEDGE_DELTA_PROPOSED, _delta_payload())],
    )[0]
    forged = copy.deepcopy(valid)
    forged["payload"]["delta"]["runtime_authority_applied"] = True
    forged["payload"]["proposal_digest"] = sha256_digest(
        {
            "domain": "wd.knowledge_delta.digest.v1",
            **forged["payload"]["delta"],
        }
    )
    core = {
        key: forged[key]
        for key in (
            "schema_version",
            "seq",
            "event_kind",
            "payload",
            "prev_event_hash",
        )
    }
    forged["event_hash"] = sha256_digest(
        {"domain": "wd.understanding_event.digest.v1", **core}
    )

    with pytest.raises(
        UnderstandingLedgerCorruptionError,
        match="delta runtime authority must be exactly false",
    ):
        replay_understanding_projection([forged])
