# SPDX-License-Identifier: Apache-2.0
"""Run the local synthetic acceptance proof for Open-World Understanding V1.

The proof is deliberately non-authoritative.  It uses a disposable SQLite
ledger, synthetic observations, process-local HMAC keys, and no network, LLM,
router, action bus, BuilderHost, replica writer, or runtime activation path.

Exit codes: 0 all invariants passed, 1 an invariant failed, 2 invalid input.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.understanding_cell_recovery import (  # noqa: E402
    AuthenticatedCheckpointEnvelopeV1,
    CellCheckpointManifestV1,
    CellFenceError,
    NoRecoveryQuorumError,
    RebuiltCellManifestV1,
    StaleCellFenceError,
    TrustedRecoveryRegistryV1,
    TrustedReplicaRecordV1,
    plan_cell_recovery,
    select_recovery_checkpoint,
    validate_cell_message_fence,
)
from waggledance.core.hex_topology.understanding_wdp import (  # noqa: E402
    AuthenticatedDanceSignalEnvelopeV1,
    AuthenticatedProposalEnvelopeV1,
    HexRingSnapshotV1,
    TrustedCellRecordV1,
    TrustedWDPRegistryV1,
    WDPDecision,
    WDPPolicyV1,
    WDPShadowReceiptV1,
    evaluate_waggle_decision,
)
from waggledance.core.learning.understanding_contracts import (  # noqa: E402
    DanceSignalKind,
    DanceSignalV1,
    HexCellAddressV1,
    IndependenceProfileV1,
    KnowledgeClaimKind,
    KnowledgeDeltaV1,
    PrivacyClass,
)
from waggledance.core.learning.understanding_loop import (  # noqa: E402
    InMemoryUnderstandingEventSink,
    UnderstandingLoop,
    UnderstandingLoopError,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.understanding_ledger import (  # noqa: E402
    GENESIS_EVENT_HASH,
    UnderstandingLedger,
    UnderstandingLedgerCorruptionError,
    UnderstandingLedgerError,
    build_understanding_event,
)
from waggledance.core.magma.understanding_projection import (  # noqa: E402
    UnderstandingProjectionError,
    project_understanding_ledger,
    replay_understanding_projection,
)


REPORT_SCHEMA = "wd.open_world_understanding.acceptance.v1"
CLAIM_LABEL = "LOCAL_SYNTHETIC_SHADOW_ACCEPTANCE"
SYNTHETIC_SECRET_MARKER = "acceptance-secret-must-not-enter-public-proof"
FIXED_NOW = "2026-08-02T12:00:00Z"
KEY_EPOCH = "2026-08-02"
PROBE_ARTIFACT_DIGEST = sha256_digest(
    {"predictor": "acceptance_header_probe", "schema_version": "v1"}
)
PROBE_CONFIG_DIGEST = sha256_digest(
    {"predictor_config": "last_value_equivalent", "schema_version": "v1"}
)

CLAIM_GATES: tuple[str, ...] = (
    "claim_safe",
    "runtime_authority_granted",
    "routing_influence_applied",
    "action_authority_granted",
    "builder_authority_granted",
    "solver_promotion_applied",
    "replica_write_applied",
    "hive_commit_applied",
    "external_writes_applied",
)

CHECK_NAMES: tuple[str, ...] = (
    "prediction_header_is_value_free",
    "low_entropy_commitment_uses_fresh_nonce",
    "opaque_metadata_digest_uses_unrevealed_salt",
    "pre_reveal_source_identity_is_value_independent",
    "durable_restart_hydrates_prior_state",
    "lost_reveal_context_expires_atomically",
    "ledger_tamper_is_rejected",
    "public_projection_is_raw_free",
    "learning_domain_is_persisted",
    "authenticated_stop_survives_forged_conflict",
    "shared_key_material_cannot_form_wdp_quorum",
    "revision_depth_is_bounded",
    "stale_recovery_majority_is_rejected",
    "rebuild_plan_advances_generation_and_fence",
    "rebuilt_fence_rejects_stale_and_foreign_messages",
    "all_product_authority_flags_are_false",
)

REPORT_KEYS = frozenset(
    {
        "schema_version",
        "claim_label",
        "generated_at_utc",
        "input_class",
        "ok",
        "checks",
        "measurements",
        "report_digest",
        *CLAIM_GATES,
    }
)

_PRODUCT_AUTHORITY_KEYS = frozenset(
    {
        "runtime_authority_applied",
        "routing_influence_applied",
        "hive_commit_applied",
        "router_authority",
        "action_authority",
        "builder_authority",
        "builder_invoked",
        "network_invoked",
        "llm_invoked",
        "solver_build_eligible",
    }
)


class AcceptanceHarnessError(RuntimeError):
    """One synthetic acceptance invariant failed."""


class _HeaderProbePredictor:
    def __init__(self) -> None:
        self.headers: list[dict[str, Any]] = []

    def predict(self, header, prior_state):
        self.headers.append(copy.deepcopy(dict(header)))
        return prior_state.expected_value


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise AcceptanceHarnessError(label)


def _digest(label: str) -> str:
    return sha256_digest({"acceptance_fixture": label})


def _key(label: str) -> bytes:
    return hashlib.sha256(f"acceptance-key:{label}".encode("utf-8")).digest()


def _resolver(keys: Mapping[tuple[str, str], bytes]):
    def resolve(key_id: str, key_epoch: str) -> bytes:
        return keys[(key_id, key_epoch)]

    return resolve


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            keys.update(_walk_keys(nested))
    return keys


def _all_authority_flags_false(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in _PRODUCT_AUTHORITY_KEYS and nested is not False:
                return False
            if not _all_authority_flags_false(nested):
                return False
    elif isinstance(value, (list, tuple)):
        if any(not _all_authority_flags_false(item) for item in value):
            return False
    return True


def _has_literal_false_fields(
    value: Mapping[str, object],
    fields: frozenset[str],
) -> bool:
    return fields.issubset(value) and all(value[field] is False for field in fields)


def _cell(
    cell_id: str,
    q: int,
    r: int,
    *,
    incarnation_id: str | None = None,
    generation: int = 1,
    fence: int = 1,
) -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id=cell_id,
        q=q,
        r=r,
        incarnation_id=incarnation_id or f"inc-{cell_id}",
        generation=generation,
        fence=fence,
    )


def _observation(source_seq: int, value: float) -> dict[str, object]:
    return {
        "observation_id": f"acceptance-observation-{source_seq}",
        "source_seq": source_seq,
        "source": "mqtt",
        "entity_id": "wd.synthetic.acceptance-hive",
        "metric": "temperature",
        "unit": "Cel",
        "value": value,
        "observed_at_utc": FIXED_NOW,
        "quality": 0.9,
        "privacy_class": "synthetic",
        "metadata": {"marker": SYNTHETIC_SECRET_MARKER},
    }


def _exercise_learning(scratch_dir: Path) -> tuple[dict[str, bool], dict[str, int], dict]:
    checks: dict[str, bool] = {}
    cell = _cell("understanding-temperature", 0, 0)
    path = scratch_dir / "open-world-understanding-acceptance.db"
    probe = _HeaderProbePredictor()

    first_ledger = UnderstandingLedger(path)
    try:
        first = UnderstandingLoop(
            cell=cell,
            event_sink=first_ledger,
            predictor=probe,
            predictor_artifact_digest=PROBE_ARTIFACT_DIGEST,
            predictor_config_digest=PROBE_CONFIG_DIGEST,
            recover_from_verified_ledger=True,
        )
        first_ticket = first.prepare_observation(_observation(1, 0.0))
        first_commitment = first_ticket.observation_commitment.commitment_digest
        first_source_identity = first_ledger.events[0]["payload"][
            "source_sequence_identity_digest"
        ]
        first_metadata_digest = probe.headers[0]["metadata_digest"]
        _require(len(probe.headers) == 1, "predictor was not called exactly once")
        forbidden_header_keys = {
            "value",
            "metadata",
            "nonce",
            "commitment_nonce",
        }
        checks["prediction_header_is_value_free"] = not (
            forbidden_header_keys & _walk_keys(probe.headers[0])
        )
        _require(
            checks["prediction_header_is_value_free"],
            "predictor header exposed observation material",
        )
        first_outcome = first.complete_numeric(first_ticket, 0.0)
    finally:
        first_ledger.close()

    nonce_probe = _HeaderProbePredictor()
    nonce_comparison = UnderstandingLoop(
        cell=cell,
        event_sink=InMemoryUnderstandingEventSink(),
        predictor=nonce_probe,
        predictor_artifact_digest=PROBE_ARTIFACT_DIGEST,
        predictor_config_digest=PROBE_CONFIG_DIGEST,
    )
    nonce_ticket = nonce_comparison.prepare_observation(_observation(1, 0.0))
    checks["low_entropy_commitment_uses_fresh_nonce"] = (
        nonce_ticket.observation_commitment.commitment_digest != first_commitment
    )
    _require(
        checks["low_entropy_commitment_uses_fresh_nonce"],
        "identical low-entropy observations reused a commitment",
    )
    checks["opaque_metadata_digest_uses_unrevealed_salt"] = (
        nonce_probe.headers[0]["metadata_digest"] != first_metadata_digest
        and first_metadata_digest
        != sha256_digest(
            {"metadata": {"marker": SYNTHETIC_SECRET_MARKER}}
        )
    )
    _require(
        checks["opaque_metadata_digest_uses_unrevealed_salt"],
        "identical opaque metadata reused an unsalted digest",
    )
    nonce_comparison.close()

    value_comparison = UnderstandingLoop(
        cell=cell,
        event_sink=InMemoryUnderstandingEventSink(),
    )
    value_comparison.prepare_observation(_observation(1, 1.0))
    comparison_source_identity = value_comparison.event_sink.events[0]["payload"][
        "source_sequence_identity_digest"
    ]
    vulnerable_value_digests = {
        sha256_digest(
            {
                "domain": "wd.source_observation.content.v1",
                "observation_id": "acceptance-observation-1",
                "source_seq": 1,
                "source": "mqtt",
                "entity_id": "wd.synthetic.acceptance-hive",
                "metric": "temperature",
                "unit": "Cel",
                "value": candidate,
                "observed_at_utc": FIXED_NOW,
                "quality": 0.9,
                "privacy_class": "synthetic",
                "metadata_digest": first_metadata_digest,
            }
        )
        for candidate in (0.0, 1.0)
    }
    checks["pre_reveal_source_identity_is_value_independent"] = (
        comparison_source_identity == first_source_identity
        and first_source_identity not in vulnerable_value_digests
    )
    _require(
        checks["pre_reveal_source_identity_is_value_independent"],
        "pre-reveal source identity changed with a low-entropy raw value",
    )
    value_comparison.close()

    second_ledger = UnderstandingLedger(path)
    try:
        second = UnderstandingLoop(
            cell=cell,
            event_sink=second_ledger,
            predictor=_HeaderProbePredictor(),
            predictor_artifact_digest=PROBE_ARTIFACT_DIGEST,
            predictor_config_digest=PROBE_CONFIG_DIGEST,
            recover_from_verified_ledger=True,
        )
        pending = second.prepare_observation(_observation(2, 5.0))
        checks["durable_restart_hydrates_prior_state"] = (
            pending.prediction is not None
            and pending.prediction.ingest_seq == 2
            and pending.prediction.predicted_value == 0.0
        )
        _require(
            checks["durable_restart_hydrates_prior_state"],
            "restart did not hydrate the prior numeric state",
        )
    finally:
        second_ledger.close()

    third_ledger = UnderstandingLedger(path)
    try:
        third = UnderstandingLoop(
            cell=cell,
            event_sink=third_ledger,
            predictor=_HeaderProbePredictor(),
            predictor_artifact_digest=PROBE_ARTIFACT_DIGEST,
            predictor_config_digest=PROBE_CONFIG_DIGEST,
            recover_from_verified_ledger=True,
        )
        reconciled_events = third_ledger.events
        exact_expiries = [
            event
            for event in reconciled_events
            if event["event_kind"] == "disposition_recorded"
            and event["payload"].get("ticket_id") == pending.ticket_id
            and event["payload"].get("reason_codes")
            == ["restart_lost_reveal_context"]
        ]
        pending_lifecycle_kinds = [
            event["event_kind"]
            for event in reconciled_events
            if event["payload"].get("ticket_id") == pending.ticket_id
        ]
        reconciled_projection = project_understanding_ledger(third_ledger)
        local_states = reconciled_projection.to_mapping()["local_states"]
        try:
            third.complete_numeric(pending, 5.0)
        except UnderstandingLoopError as exc:
            old_ticket_refused = "unissued_prediction_ticket" in str(exc)
        else:
            old_ticket_refused = False
        checks["lost_reveal_context_expires_atomically"] = (
            len(exact_expiries) == 1
            and pending_lifecycle_kinds
            == ["prediction_committed", "disposition_recorded"]
            and reconciled_projection.pending_ticket_count == 0
            and len(local_states) == 1
            and local_states[0]["generation"] == 1
            and local_states[0]["sample_count"] == 1
            and old_ticket_refused
        )
        _require(
            checks["lost_reveal_context_expires_atomically"],
            "restart did not exactly and safely expire the pending prediction",
        )
        reconciled_event_count = len(reconciled_events)
    finally:
        third_ledger.close()

    fourth_ledger = UnderstandingLedger(path)
    try:
        fourth = UnderstandingLoop(
            cell=cell,
            event_sink=fourth_ledger,
            predictor=_HeaderProbePredictor(),
            predictor_artifact_digest=PROBE_ARTIFACT_DIGEST,
            predictor_config_digest=PROBE_CONFIG_DIGEST,
            recover_from_verified_ledger=True,
        )
        _require(
            len(fourth_ledger.events) == reconciled_event_count
            and fourth.stats()["pending_ticket_count"] == 0,
            "restart reconciliation was not one-time and idempotent",
        )
        final_ticket = fourth.prepare_observation(_observation(3, 5.0))
        _require(
            final_ticket.prediction is not None
            and final_ticket.prediction.ingest_seq == 3,
            "restart reused or skipped the durable ingest sequence",
        )
        final_outcome = fourth.complete_numeric(final_ticket, 5.0)
        fourth_stats = fourth.stats()

        projection = project_understanding_ledger(fourth_ledger)
        projection_mapping = projection.to_mapping()
        forbidden_projection_keys = {
            "value",
            "predicted_value",
            "expected_value",
            "residual",
            "commitment_nonce",
            "privacy_domain",
            "observation_header",
        }
        checks["public_projection_is_raw_free"] = not (
            forbidden_projection_keys & _walk_keys(projection_mapping)
        ) and SYNTHETIC_SECRET_MARKER not in json.dumps(
            projection_mapping, sort_keys=True
        )
        _require(
            checks["public_projection_is_raw_free"],
            "public projection exposed local observation material",
        )

        events = fourth_ledger.events
        prediction_events = [
            event
            for event in events
            if event["event_kind"] == "prediction_committed"
        ]
        checks["learning_domain_is_persisted"] = bool(prediction_events) and all(
            event["payload"].get("learning_domain_digest", "").startswith(
                "sha256:"
            )
            and event["payload"].get("learning_policy_digest", "").startswith(
                "sha256:"
            )
            for event in prediction_events
        )
        _require(
            checks["learning_domain_is_persisted"],
            "prediction events lack the semantic learning-domain commitment",
        )
    finally:
        fourth_ledger.close()

    tampered = copy.deepcopy(events)
    reveal = next(
        event for event in tampered if event["event_kind"] == "observation_revealed"
    )
    reveal["payload"]["value"] = 123456.0
    try:
        replay_understanding_projection(tampered)
    except (
        UnderstandingLedgerCorruptionError,
        UnderstandingLedgerError,
        UnderstandingProjectionError,
    ):
        structural_tamper_rejected = True
    else:
        structural_tamper_rejected = False

    semantic_payloads = [
        (event["event_kind"], copy.deepcopy(event["payload"]))
        for event in events
    ]
    update_index = max(
        index
        for index, (kind, _payload) in enumerate(semantic_payloads)
        if kind == "local_provisional_update"
    )
    update_payload = semantic_payloads[update_index][1]
    update = update_payload["update"]
    next_state = update_payload["next_state"]
    next_state["expected_value"] += 1000.0
    next_state["state_digest"] = sha256_digest(
        {
            "domain": "wd.understanding_state.v1",
            "target_key": next_state["target_key"],
            "generation": next_state["generation"],
            "expected_value": next_state["expected_value"],
            "sample_count": next_state["sample_count"],
            "prediction_digest": update["prediction_digest"],
        }
    )
    update["new_state_digest"] = next_state["state_digest"]
    update_payload["update_digest"] = sha256_digest(
        {"domain": "wd.local_provisional_update.digest.v1", **update}
    )
    rebuilt: list[dict[str, Any]] = []
    predecessor = GENESIS_EVENT_HASH
    for sequence, (kind, payload) in enumerate(semantic_payloads, start=1):
        event = build_understanding_event(
            kind,
            payload,
            seq=sequence,
            prev_event_hash=predecessor,
        )
        rebuilt.append(event)
        predecessor = event["event_hash"]
    try:
        replay_understanding_projection(rebuilt)
    except UnderstandingProjectionError:
        semantic_tamper_rejected = True
    else:
        semantic_tamper_rejected = False

    alternate_payloads = [
        (event["event_kind"], copy.deepcopy(event["payload"]))
        for event in events
    ]
    curiosity_index = next(
        index
        for index, (kind, _payload) in enumerate(alternate_payloads)
        if kind == "curiosity_enqueued"
    )
    alternate_payloads[curiosity_index][1]["curiosity"]["cost"] = 0.2
    alternate_chain: list[dict[str, Any]] = []
    predecessor = GENESIS_EVENT_HASH
    for sequence, (kind, payload) in enumerate(alternate_payloads, start=1):
        event = build_understanding_event(
            kind,
            payload,
            seq=sequence,
            prev_event_hash=predecessor,
        )
        alternate_chain.append(event)
        predecessor = event["event_hash"]
    replay_understanding_projection(alternate_chain)
    try:
        replay_understanding_projection(
            alternate_chain,
            expected_head=events[-1]["event_hash"],
        )
    except UnderstandingLedgerCorruptionError:
        pinned_head_rejected = True
    else:
        pinned_head_rejected = False
    checks["ledger_tamper_is_rejected"] = (
        structural_tamper_rejected
        and semantic_tamper_rejected
        and pinned_head_rejected
    )
    _require(
        checks["ledger_tamper_is_rejected"],
        "tampered event chain was accepted",
    )
    return (
        checks,
        {
            "ledger_event_count": len(events),
            "projection_ticket_count": len(projection_mapping["tickets"]),
        },
        {
            "projection": projection_mapping,
            "events": events,
            "outcomes": (first_outcome, final_outcome),
            "stats": fourth_stats,
        },
    )


def _wdp_profile(cell: HexCellAddressV1, index: int) -> IndependenceProfileV1:
    return IndependenceProfileV1(
        reviewer_cell_id=cell.cell_id,
        identity_incarnation=cell.incarnation_id,
        genesis_root_digest=_digest(f"wdp-genesis-{index}"),
        verifier_code_family=f"code-family-{index}",
        model_provider_lineage=f"model-lineage-{index}",
        prompt_policy_lineage=f"prompt-lineage-{index}",
        evidence_root_lineage=f"evidence-lineage-{index}",
        toolchain_image_lineage=f"toolchain-lineage-{index}",
        physical_failure_domain=f"host-{index}",
    )


def _wdp_context():
    center = _cell("center", 0, 0)
    neighbors = (
        _cell("east", 1, 0),
        _cell("north-east", 1, -1),
        _cell("north-west", 0, -1),
        _cell("west", -1, 0),
        _cell("south-west", -1, 1),
        _cell("south-east", 0, 1),
    )
    ring = HexRingSnapshotV1(center=center, neighbors=neighbors)
    cells = (center, *neighbors)
    profiles = {cell.cell_id: _wdp_profile(cell, index) for index, cell in enumerate(cells)}
    records = tuple(
        TrustedCellRecordV1(
            cell=cell,
            auth_key_id=f"wdp-key-{cell.cell_id}",
            key_epoch=KEY_EPOCH,
            genesis_root_digest=profiles[cell.cell_id].genesis_root_digest,
            method_group_digest=profiles[cell.cell_id].method_group_digest,
            evidence_group_digest=profiles[cell.cell_id].evidence_group_digest,
            physical_failure_domain=profiles[cell.cell_id].physical_failure_domain,
        )
        for cell in cells
    )
    registry = TrustedWDPRegistryV1.create(records)
    keys = {
        (f"wdp-key-{cell.cell_id}", KEY_EPOCH): _key(f"wdp-{cell.cell_id}")
        for cell in cells
    }
    proposal = KnowledgeDeltaV1(
        proposal_id="acceptance-proposal",
        proposer_cell_id=center.cell_id,
        claim_kind=KnowledgeClaimKind.HYPOTHESIS,
        aggregate_digest=_digest("wdp-aggregate"),
        evidence_refs=tuple(_digest(f"wdp-evidence-{index}") for index in range(5)),
        confidence=0.8,
        privacy_class=PrivacyClass.PUBLIC,
        created_at_utc="2026-08-02T11:55:00Z",
        expires_at_utc="2026-08-02T12:30:00Z",
    )
    center_record = registry.record_for(center.cell_id)
    assert center_record is not None
    proposal_envelope = AuthenticatedProposalEnvelopeV1.create(
        proposal=proposal,
        proposer=center,
        registry_digest=registry.registry_digest,
        key_id=center_record.auth_key_id,
        key_epoch=center_record.key_epoch,
        hmac_key=keys[(center_record.auth_key_id, center_record.key_epoch)],
    )
    return ring, profiles, registry, keys, proposal, proposal_envelope


def _wdp_signal(
    *,
    proposal: KnowledgeDeltaV1,
    cell: HexCellAddressV1,
    profile: IndependenceProfileV1,
    registry: TrustedWDPRegistryV1,
    keys: Mapping[tuple[str, str], bytes],
    index: int,
    kind: DanceSignalKind = DanceSignalKind.SUPPORT,
    supersedes: str | None = None,
) -> AuthenticatedDanceSignalEnvelopeV1:
    signal = DanceSignalV1(
        proposal_digest=proposal.proposal_digest,
        signal_kind=kind,
        reviewer=profile,
        evidence_digest=_digest(f"wdp-signal-evidence-{index}"),
        created_at_utc=f"2026-08-02T11:58:{index:02d}Z",
        expires_at_utc="2026-08-02T12:20:00Z",
        supersedes_signal_digest=supersedes,
    )
    record = registry.record_for(cell.cell_id)
    assert record is not None
    return AuthenticatedDanceSignalEnvelopeV1.create(
        signal=signal,
        emitter=cell,
        registry_digest=registry.registry_digest,
        key_id=record.auth_key_id,
        key_epoch=record.key_epoch,
        hmac_key=keys[(record.auth_key_id, record.key_epoch)],
    )


def _exercise_wdp() -> tuple[
    dict[str, bool],
    dict[str, int],
    WDPShadowReceiptV1,
]:
    ring, profiles, registry, keys, proposal, proposal_envelope = _wdp_context()
    supports = tuple(
        _wdp_signal(
            proposal=proposal,
            cell=ring.neighbors[index],
            profile=profiles[ring.neighbors[index].cell_id],
            registry=registry,
            keys=keys,
            index=index + 1,
        )
        for index in range(2)
    )
    stop = _wdp_signal(
        proposal=proposal,
        cell=ring.neighbors[2],
        profile=profiles[ring.neighbors[2].cell_id],
        registry=registry,
        keys=keys,
        index=3,
        kind=DanceSignalKind.STOP,
    )
    forged_conflict = AuthenticatedDanceSignalEnvelopeV1(
        signal=stop.signal,
        emitter=stop.emitter,
        registry_digest=stop.registry_digest,
        key_id=stop.key_id,
        key_epoch=stop.key_epoch,
        auth_tag="hmac-sha256:" + "0" * 64,
    )
    stopped = evaluate_waggle_decision(
        proposal_envelope,
        ring,
        supports + (stop, forged_conflict),
        trust_registry=registry,
        key_resolver=_resolver(keys),
        now_utc=FIXED_NOW,
    )
    checks = {
        "authenticated_stop_survives_forged_conflict": (
            stopped.decision is WDPDecision.STOPPED
            and stop.signal.signal_digest in stopped.stop_signal_digests
            and any(
                rejected.reason_code == "signal_authentication_failed"
                for rejected in stopped.rejected_signals
            )
        )
    }
    _require(
        checks["authenticated_stop_survives_forged_conflict"],
        "authenticated STOP was stripped or ignored",
    )

    shared_keys = dict(keys)
    shared_keys[("wdp-key-north-east", KEY_EPOCH)] = shared_keys[
        ("wdp-key-east", KEY_EPOCH)
    ]
    shared_supports = tuple(
        _wdp_signal(
            proposal=proposal,
            cell=ring.neighbors[index],
            profile=profiles[ring.neighbors[index].cell_id],
            registry=registry,
            keys=shared_keys,
            index=10 + index,
        )
        for index in range(2)
    )
    shared_receipt = evaluate_waggle_decision(
        proposal_envelope,
        ring,
        shared_supports,
        trust_registry=registry,
        key_resolver=_resolver(shared_keys),
        now_utc=FIXED_NOW,
    )
    checks["shared_key_material_cannot_form_wdp_quorum"] = (
        shared_receipt.decision is WDPDecision.PROPOSAL_INVALID
        and "trusted_key_material_not_independent" in shared_receipt.reason_codes
    )
    _require(
        checks["shared_key_material_cannot_form_wdp_quorum"],
        "shared key material manufactured WDP independence",
    )

    revisions: list[AuthenticatedDanceSignalEnvelopeV1] = []
    supersedes: str | None = None
    for index in range(5):
        revision = _wdp_signal(
            proposal=proposal,
            cell=ring.neighbors[0],
            profile=profiles[ring.neighbors[0].cell_id],
            registry=registry,
            keys=keys,
            index=20 + index,
            supersedes=supersedes,
        )
        revisions.append(revision)
        supersedes = revision.signal.signal_digest
    revision_receipt = evaluate_waggle_decision(
        proposal_envelope,
        ring,
        tuple(revisions),
        trust_registry=registry,
        key_resolver=_resolver(keys),
        now_utc=FIXED_NOW,
        policy=WDPPolicyV1(max_revision_depth=2),
    )
    over_depth = [
        rejected
        for rejected in revision_receipt.rejected_signals
        if rejected.reason_code == "revision_depth_exceeded"
    ]
    checks["revision_depth_is_bounded"] = (
        len(revision_receipt.accepted_signal_digests) == 3
        and len(over_depth) == 2
    )
    _require(
        checks["revision_depth_is_bounded"],
        "revision-depth cap did not reject all descendants",
    )
    return (
        checks,
        {
            "ring_cell_count": 1 + len(ring.neighbors),
            "accepted_revision_signal_count": len(
                revision_receipt.accepted_signal_digests
            ),
            "rejected_revision_signal_count": len(over_depth),
        },
        stopped,
    )


def _recovery_context():
    cell_identity = _digest("recovery-cell-identity")
    records = tuple(
        TrustedReplicaRecordV1(
            replica_identity=f"replica-{label}",
            auth_key_id=f"recovery-key-{label}",
            key_epoch=KEY_EPOCH,
            failure_domain_digest=_digest(f"failure-domain-{label}"),
            logical_cell_id="understanding-temperature",
            q=2,
            r=-1,
            cell_identity_digest=cell_identity,
        )
        for label in ("a", "b", "c")
    )
    registry = TrustedRecoveryRegistryV1.create(records)
    keys = {
        (f"recovery-key-{label}", KEY_EPOCH): _key(f"recovery-{label}")
        for label in ("a", "b", "c")
    }
    return cell_identity, registry, keys


def _checkpoint_envelope(
    *,
    replica: str,
    cell: HexCellAddressV1,
    cell_identity: str,
    ledger_head: str,
    event_count: int,
    projection_digest: str,
    registry: TrustedRecoveryRegistryV1,
    keys: Mapping[tuple[str, str], bytes],
) -> AuthenticatedCheckpointEnvelopeV1:
    record = registry.record_for(replica)
    assert record is not None
    manifest = CellCheckpointManifestV1.create(
        cell=cell,
        cell_identity_digest=cell_identity,
        replica_identity=replica,
        replica_failure_domain_digest=record.failure_domain_digest,
        ledger_head_digest=ledger_head,
        ledger_event_count=event_count,
        projection_digest=projection_digest,
    )
    return AuthenticatedCheckpointEnvelopeV1.create(
        manifest=manifest,
        registry_digest=registry.registry_digest,
        key_id=record.auth_key_id,
        key_epoch=record.key_epoch,
        hmac_key=keys[(record.auth_key_id, record.key_epoch)],
    )


def _exercise_recovery() -> tuple[
    dict[str, bool],
    dict[str, int],
    RebuiltCellManifestV1,
]:
    cell_identity, registry, keys = _recovery_context()
    current = _cell(
        "understanding-temperature",
        2,
        -1,
        incarnation_id="incarnation-7",
        generation=7,
        fence=19,
    )
    head_a = _digest("recovery-ledger-a")
    head_b = _digest("recovery-ledger-b")
    projection_a = _digest("recovery-projection-a")
    projection_b = _digest("recovery-projection-b")
    success = tuple(
        _checkpoint_envelope(
            replica=f"replica-{label}",
            cell=current,
            cell_identity=cell_identity,
            ledger_head=head_a if label in ("a", "b") else head_b,
            event_count=41 if label in ("a", "b") else 42,
            projection_digest=(
                projection_a if label in ("a", "b") else projection_b
            ),
            registry=registry,
            keys=keys,
        )
        for label in ("a", "b", "c")
    )
    plan = plan_cell_recovery(
        success,
        trust_registry=registry,
        key_resolver=_resolver(keys),
        new_incarnation_id="incarnation-8",
    )
    checks = {
        "rebuild_plan_advances_generation_and_fence": (
            plan.rebuilt_cell.generation == 8
            and plan.rebuilt_cell.fence == 20
            and plan.rebuilt_cell.incarnation_id == "incarnation-8"
            and plan.router_authority is False
            and plan.action_authority is False
            and plan.builder_authority is False
        )
    }
    _require(
        checks["rebuild_plan_advances_generation_and_fence"],
        "recovery plan did not advance the fenced cell identity",
    )
    validate_cell_message_fence(plan.rebuilt_cell, plan.rebuilt_cell)
    try:
        validate_cell_message_fence(plan.source_cell, plan.rebuilt_cell)
    except StaleCellFenceError:
        stale_message_rejected = True
    else:
        stale_message_rejected = False
    foreign_incarnation = _cell(
        "understanding-temperature",
        2,
        -1,
        incarnation_id="incarnation-foreign",
        generation=plan.rebuilt_cell.generation,
        fence=plan.rebuilt_cell.fence,
    )
    try:
        validate_cell_message_fence(foreign_incarnation, plan.rebuilt_cell)
    except CellFenceError:
        foreign_message_rejected = True
    else:
        foreign_message_rejected = False
    checks["rebuilt_fence_rejects_stale_and_foreign_messages"] = (
        stale_message_rejected and foreign_message_rejected
    )
    _require(
        checks["rebuilt_fence_rejects_stale_and_foreign_messages"],
        "rebuilt cell fence accepted a stale or foreign-incarnation message",
    )

    old = _cell(
        "understanding-temperature",
        2,
        -1,
        incarnation_id="incarnation-6",
        generation=6,
        fence=18,
    )
    stale_majority = tuple(
        _checkpoint_envelope(
            replica=f"replica-{label}",
            cell=old if label in ("a", "b") else current,
            cell_identity=cell_identity,
            ledger_head=head_b if label in ("a", "b") else head_a,
            event_count=40 if label in ("a", "b") else 41,
            projection_digest=(
                projection_b if label in ("a", "b") else projection_a
            ),
            registry=registry,
            keys=keys,
        )
        for label in ("a", "b", "c")
    )
    try:
        select_recovery_checkpoint(
            stale_majority,
            trust_registry=registry,
            key_resolver=_resolver(keys),
        )
    except NoRecoveryQuorumError:
        checks["stale_recovery_majority_is_rejected"] = True
    else:
        checks["stale_recovery_majority_is_rejected"] = False
    _require(
        checks["stale_recovery_majority_is_rejected"],
        "a stale two-of-three majority overruled a newer authenticated fence",
    )
    return (
        checks,
        {"recovery_witness_count": len(plan.witnesses)},
        plan,
    )


def build_acceptance_report(
    *,
    scratch_dir: Path,
    generated_at_utc: str = FIXED_NOW,
) -> dict[str, object]:
    """Execute all synthetic checks and return a raw-free local artifact."""

    scratch_dir = Path(scratch_dir)
    generated_at_utc = _parse_utc(generated_at_utc)
    if not scratch_dir.is_dir():
        raise AcceptanceHarnessError("scratch_dir must be an existing directory")

    learning_checks, learning_counts, learning_evidence = _exercise_learning(
        scratch_dir
    )
    wdp_checks, wdp_counts, wdp_receipt = _exercise_wdp()
    recovery_checks, recovery_counts, recovery_plan = _exercise_recovery()
    checks = {**learning_checks, **wdp_checks, **recovery_checks}

    projection = learning_evidence["projection"]
    events = learning_evidence["events"]
    outcomes = learning_evidence["outcomes"]
    stats = learning_evidence["stats"]
    wdp_mapping = wdp_receipt.to_mapping()
    recovery_mapping = recovery_plan.to_mapping()
    projection_authority = frozenset(
        {
            "runtime_authority_applied",
            "routing_influence_applied",
            "hive_commit_applied",
        }
    )
    wdp_authority = projection_authority
    recovery_authority = frozenset(
        {"router_authority", "action_authority", "builder_authority"}
    )
    direct_outcomes_safe = all(
        outcome.runtime_authority_applied is False
        and outcome.routing_influence_applied is False
        and (
            outcome.local_update is None
            or (
                outcome.local_update.reversible is True
                and outcome.local_update.runtime_authority_applied is False
                and outcome.local_update.routing_influence_applied is False
                and _all_authority_flags_false(
                    outcome.local_update.to_mapping()
                )
            )
        )
        for outcome in outcomes
    )
    authority_false = (
        all(
            _all_authority_flags_false(value)
            for value in (
                projection,
                events,
                stats,
                wdp_mapping,
                recovery_mapping,
            )
        )
        and _has_literal_false_fields(projection, projection_authority)
        and _has_literal_false_fields(wdp_mapping, wdp_authority)
        and _has_literal_false_fields(recovery_mapping, recovery_authority)
        and wdp_receipt.hive_commit_applied is False
        and wdp_receipt.runtime_authority_applied is False
        and wdp_receipt.routing_influence_applied is False
        and recovery_plan.router_authority is False
        and recovery_plan.action_authority is False
        and recovery_plan.builder_authority is False
        and _has_literal_false_fields(
            stats,
            frozenset(
                {"runtime_authority_applied", "routing_influence_applied"}
            ),
        )
        and direct_outcomes_safe
    )
    checks["all_product_authority_flags_are_false"] = authority_false
    _require(authority_false, "a product authority flag was not literal false")
    _require(set(checks) == set(CHECK_NAMES), "acceptance check set drifted")

    core: dict[str, object] = {
        "schema_version": REPORT_SCHEMA,
        "claim_label": CLAIM_LABEL,
        "generated_at_utc": generated_at_utc,
        "input_class": "synthetic_local_only",
        "ok": all(checks.values()),
        "checks": {key: checks[key] for key in sorted(checks)},
        "measurements": {
            **learning_counts,
            **wdp_counts,
            **recovery_counts,
        },
        **{gate: False for gate in CLAIM_GATES},
    }
    report = {
        **core,
        "report_digest": sha256_digest(
            {"domain": "wd.open_world_understanding.acceptance.digest.v1", **core}
        ),
    }
    _require(set(report) == REPORT_KEYS, "acceptance report field set drifted")
    _require(
        SYNTHETIC_SECRET_MARKER not in json.dumps(report, sort_keys=True),
        "acceptance report exposed the synthetic secret marker",
    )
    return report


def _parse_utc(value: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("timestamp must be canonical UTC")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must use UTC")
    canonical = parsed.isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise ValueError("timestamp must use canonical spelling")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the disposable, synthetic Open-World Understanding V1 "
            "acceptance proof. This grants no runtime or promotion authority."
        )
    )
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=None,
        help="Existing directory under which a disposable proof directory is made.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Canonical ISO-8601 UTC timestamp for the report artifact.",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        generated_at = _parse_utc(args.now) if args.now else datetime.now(
            timezone.utc
        ).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError) as exc:
        print(f"invalid --now: {exc}", file=sys.stderr)
        return 2

    if args.scratch_root is not None and not args.scratch_root.is_dir():
        print("--scratch-root must be an existing directory", file=sys.stderr)
        return 2

    try:
        with tempfile.TemporaryDirectory(
            prefix="wd-open-world-v1-",
            dir=args.scratch_root,
        ) as temporary:
            report = build_acceptance_report(
                scratch_dir=Path(temporary),
                generated_at_utc=generated_at,
            )
        serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
        if args.out is not None:
            args.out.write_text(serialized, encoding="utf-8")
    except (AcceptanceHarnessError, OSError) as exc:
        print(f"acceptance proof failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(serialized, end="")
    else:
        print(
            "Open-World Understanding V1 local synthetic acceptance: "
            f"PASS ({len(CHECK_NAMES)} checks, no authority granted)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
