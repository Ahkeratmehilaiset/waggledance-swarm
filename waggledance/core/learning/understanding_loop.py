# SPDX-License-Identifier: Apache-2.0
"""Default-off, shadow-only Open-World Understanding V1 learning loop.

This module owns no production routing or action authority.  Its two-phase
interface exists so a durable prediction can be committed against the prior
shadow state before the observed numeric value is used to resolve it.
"""

from __future__ import annotations

import copy
import hmac
import json
import math
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from waggledance.core.learning.understanding_contracts import (
    CapabilityGapCandidateV1,
    CuriosityAction,
    CuriosityItemV1,
    HexCellAddressV1,
    KnowledgeClaimKind,
    KnowledgeDeltaV1,
    LocalProvisionalUpdateV1,
    ObservationCommitmentV1,
    ObservationEnvelopeV1,
    PredictionCommitmentV1,
    PredictionStatus,
    PrivacyClass,
    UnderstandingContractError,
    UnderstandingDisposition,
    UnderstandingDispositionV1,
    build_observation_commitment,
    derive_source_key,
    derive_target_key,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


STATE_UPDATE_ALPHA_V1 = 0.1


class UnderstandingLoopError(RuntimeError):
    """The shadow loop could not preserve its ordering or audit invariant."""


class UnderstandingEventSink(Protocol):
    def append_event(self, event_kind: str, payload: Mapping[str, Any]) -> str:
        """Durably append an event and return its canonical event digest."""

    def append_batch(
        self,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        idempotency_key: str,
    ) -> tuple[str, ...]:
        """Atomically append an idempotent ordered event batch."""


class NumericPredictor(Protocol):
    def predict(
        self,
        header: Mapping[str, Any],
        prior_state: "PredictionStateV1",
    ) -> Optional[float]:
        """Predict using only the value-free header and prior shadow state."""


@dataclass(frozen=True)
class UnderstandingPolicyV1:
    allowed_source: str = "mqtt"
    entity_namespace: str = "wd.synthetic"
    metric: str = "temperature"
    unit: str = "Cel"
    min_value: float = -80.0
    max_value: float = 150.0
    residual_abs_threshold: float = 2.0
    per_source_rate: int = 10
    per_source_burst: int = 20
    global_rate: int = 100
    global_burst: int = 200
    curiosity_top_k: int = 32
    curiosity_per_minute: int = 3
    delta_min_samples: int = 5
    delta_window_seconds: int = 600
    proposal_ttl_seconds: int = 900
    max_source_buckets: int = 1024
    max_targets: int = 256
    max_seen_sequences: int = 4096
    max_pending_tickets: int = 512
    max_completed_outcomes: int = 4096
    max_quarantined_sources: int = 256

    def __post_init__(self) -> None:
        for name in ("allowed_source", "entity_namespace", "metric", "unit"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise UnderstandingContractError(f"{name} must be a non-empty string")
        for name in ("min_value", "max_value", "residual_abs_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise UnderstandingContractError(f"{name} must be finite")
        if self.min_value >= self.max_value or self.residual_abs_threshold <= 0:
            raise UnderstandingContractError("numeric policy bounds refused")
        for name in (
            "per_source_rate",
            "per_source_burst",
            "global_rate",
            "global_burst",
            "curiosity_top_k",
            "curiosity_per_minute",
            "delta_min_samples",
            "delta_window_seconds",
            "proposal_ttl_seconds",
            "max_source_buckets",
            "max_targets",
            "max_seen_sequences",
            "max_pending_tickets",
            "max_completed_outcomes",
            "max_quarantined_sources",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise UnderstandingContractError(f"{name} must be a positive integer")
        if self.per_source_burst < self.per_source_rate:
            raise UnderstandingContractError("per-source burst cannot be below rate")
        if self.global_burst < self.global_rate:
            raise UnderstandingContractError("global burst cannot be below rate")
        if self.delta_min_samples < 5:
            raise UnderstandingContractError("delta_min_samples cannot be below five")
        if self.max_quarantined_sources < self.max_targets:
            raise UnderstandingContractError(
                "max_quarantined_sources cannot be below max_targets"
            )


@dataclass(frozen=True)
class PredictionStateV1:
    target_key: str
    generation: int
    state_digest: str
    expected_value: Optional[float]
    sample_count: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "target_key": self.target_key,
            "generation": self.generation,
            "state_digest": self.state_digest,
            "expected_value": self.expected_value,
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class PredictionTicketV1:
    ticket_id: str
    target_key: str
    source_key: str
    source_seq: int
    observation_commitment: ObservationCommitmentV1
    prediction: Optional[PredictionCommitmentV1]
    prior_state: PredictionStateV1
    terminal_disposition: Optional[UnderstandingDisposition] = None
    terminal_reason: str = ""


@dataclass(frozen=True)
class UnderstandingOutcomeV1:
    ticket_id: str
    disposition: UnderstandingDisposition
    disposition_record: Optional[UnderstandingDispositionV1]
    local_update: Optional[LocalProvisionalUpdateV1]
    curiosity_item: Optional[CuriosityItemV1]
    knowledge_delta: Optional[KnowledgeDeltaV1]
    capability_gap: Optional[CapabilityGapCandidateV1]
    runtime_authority_applied: bool = False
    routing_influence_applied: bool = False

    def __post_init__(self) -> None:
        if self.runtime_authority_applied is not False:
            raise UnderstandingLoopError("outcome cannot apply runtime authority")
        if self.routing_influence_applied is not False:
            raise UnderstandingLoopError("outcome cannot influence routing")


@dataclass(frozen=True)
class _IssuedTicketV1:
    ticket: PredictionTicketV1
    ticket_fingerprint: str
    header: Optional[dict[str, Any]]
    resolution_at_utc: Optional[str] = None


@dataclass(frozen=True)
class _CompletedTicketV1:
    ticket: PredictionTicketV1
    ticket_fingerprint: str
    outcome: UnderstandingOutcomeV1


@dataclass(frozen=True)
class _CuriosityPlanV1:
    item: CuriosityItemV1
    minute: int


@dataclass(frozen=True)
class _DeltaPlanV1:
    entries: tuple[tuple[datetime, str, str, float, PrivacyClass], ...]
    delta: Optional[KnowledgeDeltaV1]
    evidence_set_digest: Optional[str]


class LastValuePredictor:
    """Deterministic predictor used by the first numeric shadow slice."""

    def predict(
        self,
        header: Mapping[str, Any],
        prior_state: PredictionStateV1,
    ) -> Optional[float]:
        del header
        return prior_state.expected_value


class InMemoryUnderstandingEventSink:
    """Strict append-only sink for unit tests and explicitly ephemeral runs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._idempotent_batches: dict[str, tuple[str, tuple[str, ...]]] = {}

    @staticmethod
    def _canonical_copy(value: Any) -> Any:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))

    def _build_event(
        self,
        event_kind: str,
        payload: Mapping[str, Any],
        *,
        seq: int,
        prev_hash: str,
    ) -> dict[str, Any]:
        if type(event_kind) is not str or not event_kind:
            raise UnderstandingLoopError("event_kind refused")
        if not isinstance(payload, Mapping):
            raise UnderstandingLoopError("event payload must be a mapping")
        payload_copy = self._canonical_copy(dict(payload))
        core = {
            "schema_version": "wd.understanding_event.v1",
            "seq": seq,
            "event_kind": event_kind,
            "payload": payload_copy,
            "prev_event_hash": prev_hash,
        }
        event_hash = sha256_digest(
            {"domain": "wd.understanding_event.digest.v1", **core}
        )
        return {**core, "event_hash": event_hash}

    def append_event(self, event_kind: str, payload: Mapping[str, Any]) -> str:
        with self._lock:
            seq = len(self._events) + 1
            prev_hash = self._events[-1]["event_hash"] if self._events else "sha256:" + "0" * 64
            event = self._build_event(
                event_kind, payload, seq=seq, prev_hash=prev_hash
            )
            self._events.append(event)
            return event["event_hash"]

    def append_batch(
        self,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        idempotency_key: str,
    ) -> tuple[str, ...]:
        if type(idempotency_key) is not str or not idempotency_key:
            raise UnderstandingLoopError("idempotency_key refused")
        if not isinstance(events, (list, tuple)) or not events:
            raise UnderstandingLoopError("event batch must be non-empty")
        canonical_batch = self._canonical_copy(
            {
                "events": [
                    {"event_kind": kind, "payload": dict(payload)}
                    for kind, payload in events
                ]
            }
        )
        batch_digest = sha256_digest(
            {"domain": "wd.understanding_event_batch.v1", **canonical_batch}
        )
        with self._lock:
            prior = self._idempotent_batches.get(idempotency_key)
            if prior is not None:
                if not hmac.compare_digest(prior[0], batch_digest):
                    raise UnderstandingLoopError("idempotency_key payload mismatch")
                return prior[1]
            staged: list[dict[str, Any]] = []
            prev_hash = (
                self._events[-1]["event_hash"]
                if self._events
                else "sha256:" + "0" * 64
            )
            seq = len(self._events) + 1
            for item in canonical_batch["events"]:
                event_kind = item["event_kind"]
                payload = item["payload"]
                event = self._build_event(
                    event_kind, payload, seq=seq, prev_hash=prev_hash
                )
                staged.append(event)
                prev_hash = event["event_hash"]
                seq += 1
            digests = tuple(event["event_hash"] for event in staged)
            self._events.extend(staged)
            self._idempotent_batches[idempotency_key] = (batch_digest, digests)
            return digests

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._canonical_copy(event) for event in self._events)


class UnderstandingLoop:
    """One-cell, one-modality, non-authoritative shadow learning loop."""

    def __init__(
        self,
        *,
        cell: HexCellAddressV1,
        event_sink: UnderstandingEventSink,
        predictor: Optional[NumericPredictor] = None,
        policy: Optional[UnderstandingPolicyV1] = None,
        clock: Optional[Callable[[], datetime]] = None,
        hmac_key_provider: Optional[Callable[[str], tuple[bytes, str, str]]] = None,
        predictor_artifact_digest: Optional[str] = None,
        predictor_config_digest: Optional[str] = None,
        recover_from_verified_ledger: bool = False,
    ) -> None:
        if type(cell) is not HexCellAddressV1:
            raise UnderstandingLoopError("cell must be HexCellAddressV1")
        if not hasattr(event_sink, "append_event") or not hasattr(
            event_sink, "append_batch"
        ):
            raise UnderstandingLoopError("event sink lacks atomic append API")
        if type(recover_from_verified_ledger) is not bool:
            raise UnderstandingLoopError(
                "recover_from_verified_ledger must be an exact bool"
            )
        self.cell = copy.deepcopy(cell)
        self.event_sink = event_sink
        self.predictor = predictor or LastValuePredictor()
        self.policy = copy.deepcopy(policy or UnderstandingPolicyV1())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._hmac_key_provider = hmac_key_provider
        self._predictor_artifact_digest = predictor_artifact_digest or sha256_digest(
            {"predictor": "last_value", "schema_version": "v1"}
        )
        self._predictor_config_digest = predictor_config_digest or sha256_digest(
            {"policy": self.policy.__dict__}
        )
        self._lock = threading.RLock()
        self._ingest_seq = 0
        self._states: dict[str, PredictionStateV1] = {}
        self._seen_sequences: dict[tuple[str, int], str] = {}
        self._source_high_watermarks: dict[str, int] = {}
        self._quarantined_sources: set[str] = set()
        self._quarantine_saturated = False
        self._issued_tickets: dict[str, _IssuedTicketV1] = {}
        self._completed_outcomes: dict[str, _CompletedTicketV1] = {}
        self._source_buckets: dict[str, tuple[float, float]] = {}
        now_timestamp = self._trusted_timestamp()
        self._global_bucket = (float(self.policy.global_burst), now_timestamp)
        self._curiosity: list[CuriosityItemV1] = []
        self._curiosity_minute_counts: dict[int, int] = {}
        self._surprises: dict[
            str, list[tuple[datetime, str, str, float, PrivacyClass]]
        ] = {}
        self._last_delta_evidence_digest: dict[str, str] = {}
        self._counters = {
            "received": 0,
            "accepted": 0,
            "duplicate": 0,
            "invalid": 0,
            "privacy_blocked": 0,
            "sampled_out": 0,
            "dropped_budget": 0,
            "audit_suppressed": 0,
            "resolved": 0,
            "state_update_applied": 0,
            "append_failure": 0,
            "commitment_mismatch": 0,
        }
        if recover_from_verified_ledger:
            self._recover_from_verified_ledger()

    def _recover_from_verified_ledger(self) -> None:
        """Hydrate local shadow state after verified semantic ledger replay.

        Predictions whose secret reveal context died with the prior process are
        deterministically expired before hydration.  This never recreates an
        issued ticket and never grants routing, action, builder, or hive
        authority.
        """

        from waggledance.core.magma.understanding_ledger import UnderstandingLedger
        from waggledance.core.magma.understanding_projection import (
            reduce_understanding_ledger_restart_checkpoint,
        )

        if type(self.event_sink) is not UnderstandingLedger:
            raise UnderstandingLoopError(
                "verified restart recovery requires UnderstandingLedger"
            )
        checkpoint = reduce_understanding_ledger_restart_checkpoint(
            self.event_sink
        )
        self._validate_restart_checkpoint(checkpoint)
        if checkpoint.pending_tickets:
            if any(
                pending.reveal_verified
                for pending in checkpoint.pending_tickets.values()
            ):
                raise UnderstandingLoopError(
                    "revealed pending prediction cannot be safely recovered"
                )
            recorded_at = self._iso_utc(self._clock())
            events: list[tuple[str, Mapping[str, Any]]] = []
            for ticket_id in sorted(checkpoint.pending_tickets):
                pending = checkpoint.pending_tickets[ticket_id]
                disposition = UnderstandingDispositionV1(
                    observation_commitment_digest=(
                        pending.observation_commitment_digest
                    ),
                    prediction_digest=pending.prediction_digest,
                    disposition=UnderstandingDisposition.EXPIRED,
                    residual=None,
                    reason_codes=("restart_lost_reveal_context",),
                    recorded_at_utc=recorded_at,
                )
                events.append(
                    (
                        "disposition_recorded",
                        {
                            "ticket_id": pending.ticket_id,
                            **disposition.to_mapping(),
                            "runtime_authority_applied": False,
                            "routing_influence_applied": False,
                        },
                    )
                )
            reconciliation_digest = sha256_digest(
                {
                    "domain": "wd.understanding_restart_reconciliation.v1",
                    "cell": self.cell.to_mapping(),
                    "prior_ledger_head": checkpoint.ledger_head,
                    "pending_ticket_ids": sorted(checkpoint.pending_tickets),
                }
            )
            self._append_batch_checked(
                tuple(events),
                idempotency_key=f"restart-reconcile:{reconciliation_digest[7:]}",
            )
            checkpoint = reduce_understanding_ledger_restart_checkpoint(
                self.event_sink
            )
            self._validate_restart_checkpoint(checkpoint)
            if checkpoint.pending_tickets:
                raise UnderstandingLoopError(
                    "restart reconciliation left pending predictions"
                )
        self._hydrate_restart_checkpoint(checkpoint)

    def _validate_restart_checkpoint(self, checkpoint: Any) -> None:
        expected_cell_key = (self.cell.cell_id, self.cell.incarnation_id)
        foreign_cells = set(checkpoint.cell_ingest_high_watermarks) - {
            expected_cell_key
        }
        if foreign_cells:
            raise UnderstandingLoopError(
                "ledger contains a foreign cell incarnation"
            )
        expected_ingest = checkpoint.cell_ingest_high_watermarks.get(
            expected_cell_key,
            0,
        )
        if checkpoint.max_ingest_seq != expected_ingest:
            raise UnderstandingLoopError(
                "ledger ingest high-watermark differs from current cell"
            )
        if len(checkpoint.numeric_states) > self.policy.max_targets:
            raise UnderstandingLoopError(
                "replayed state exceeds configured target capacity"
            )
        if (
            len(checkpoint.source_high_watermarks)
            > self.policy.max_source_buckets
        ):
            raise UnderstandingLoopError(
                "replayed sources exceed configured source capacity"
            )
        if len(checkpoint.pending_tickets) > self.policy.max_pending_tickets:
            raise UnderstandingLoopError(
                "replayed pending tickets exceed configured capacity"
            )
        expected_cell = self.cell.to_mapping()
        for pending in checkpoint.pending_tickets.values():
            if dict(pending.cell) != expected_cell:
                raise UnderstandingLoopError(
                    "pending prediction belongs to another cell fence"
                )

    def _hydrate_restart_checkpoint(self, checkpoint: Any) -> None:
        self._ingest_seq = checkpoint.max_ingest_seq
        self._states = {
            target_key: PredictionStateV1(
                target_key=state.target_key,
                generation=state.generation,
                state_digest=state.state_digest,
                expected_value=state.expected_value,
                sample_count=state.sample_count,
            )
            for target_key, state in checkpoint.numeric_states.items()
        }
        seen_items = sorted(checkpoint.source_sequence_registry.items())
        if len(seen_items) > self.policy.max_seen_sequences:
            seen_items = seen_items[-self.policy.max_seen_sequences :]
        self._seen_sequences = dict(seen_items)
        self._source_high_watermarks = dict(
            checkpoint.source_high_watermarks
        )

    @staticmethod
    def _iso_utc(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except (AttributeError, ValueError) as exc:
            raise UnderstandingLoopError("observed_at_utc invalid") from exc
        return parsed.astimezone(timezone.utc)

    def _trusted_timestamp(self) -> float:
        now = self._clock()
        if not isinstance(now, datetime):
            raise UnderstandingLoopError("clock returned non-datetime")
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        timestamp = now.astimezone(timezone.utc).timestamp()
        if not math.isfinite(timestamp):
            raise UnderstandingLoopError("clock returned non-finite instant")
        return timestamp

    @staticmethod
    def _event_digest_valid(value: object) -> bool:
        if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
            return False
        try:
            int(value[7:], 16)
        except ValueError:
            return False
        return True

    @staticmethod
    def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
        digest = sha256_digest(
            {"domain": f"wd.understanding_id.{prefix}.v1", **dict(payload)}
        )
        return f"{prefix}-{digest.removeprefix('sha256:')}"

    @staticmethod
    def _ticket_fingerprint(ticket: PredictionTicketV1) -> str:
        if (
            type(ticket) is not PredictionTicketV1
            or type(ticket.ticket_id) is not str
            or type(ticket.target_key) is not str
            or type(ticket.source_key) is not str
            or type(ticket.source_seq) is not int
            or type(ticket.observation_commitment) is not ObservationCommitmentV1
            or (
                ticket.prediction is not None
                and type(ticket.prediction) is not PredictionCommitmentV1
            )
            or type(ticket.prior_state) is not PredictionStateV1
            or (
                ticket.terminal_disposition is not None
                and type(ticket.terminal_disposition) is not UnderstandingDisposition
            )
            or type(ticket.terminal_reason) is not str
        ):
            raise UnderstandingLoopError("prediction ticket shape refused")
        mapping = {
            "ticket_id": ticket.ticket_id,
            "target_key": ticket.target_key,
            "source_key": ticket.source_key,
            "source_seq": ticket.source_seq,
            "observation_commitment": ticket.observation_commitment.to_mapping(),
            "prediction": (
                ticket.prediction.to_mapping()
                if ticket.prediction is not None
                else None
            ),
            "prior_state": ticket.prior_state.to_mapping(),
            "terminal_disposition": (
                ticket.terminal_disposition.value
                if ticket.terminal_disposition is not None
                else None
            ),
            "terminal_reason": ticket.terminal_reason,
        }
        return sha256_digest(
            {"domain": "wd.prediction_ticket.fingerprint.v1", **mapping}
        )

    def _append_batch_checked(
        self,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        idempotency_key: str,
    ) -> tuple[str, ...]:
        try:
            digests = self.event_sink.append_batch(
                events, idempotency_key=idempotency_key
            )
        except Exception:
            self._counters["append_failure"] += 1
            raise
        if (
            type(digests) is not tuple
            or len(digests) != len(events)
            or not all(self._event_digest_valid(digest) for digest in digests)
        ):
            self._counters["append_failure"] += 1
            raise UnderstandingLoopError("event sink returned invalid batch receipt")
        return digests

    def _issue_ticket(
        self,
        ticket: PredictionTicketV1,
        *,
        header: Optional[Mapping[str, Any]],
    ) -> PredictionTicketV1:
        if (
            ticket.ticket_id in self._issued_tickets
            or ticket.ticket_id in self._completed_outcomes
        ):
            raise UnderstandingLoopError("prediction ticket collision")
        if len(self._issued_tickets) >= self.policy.max_pending_tickets:
            raise UnderstandingLoopError("pending prediction capacity exhausted")
        sanitized_header = None
        if header is not None:
            sanitized_header = json.loads(
                canonical_json_bytes(dict(header)).decode("utf-8")
            )
        self._issued_tickets[ticket.ticket_id] = _IssuedTicketV1(
            ticket=copy.deepcopy(ticket),
            ticket_fingerprint=self._ticket_fingerprint(ticket),
            header=sanitized_header,
        )
        return ticket

    def _remember_completed(
        self,
        ticket: PredictionTicketV1,
        outcome: UnderstandingOutcomeV1,
    ) -> None:
        fingerprint = self._ticket_fingerprint(ticket)
        prior = self._completed_outcomes.get(ticket.ticket_id)
        if prior is not None:
            if (
                not hmac.compare_digest(prior.ticket_fingerprint, fingerprint)
                or prior.outcome != outcome
            ):
                raise UnderstandingLoopError("completed ticket collision")
            return
        self._completed_outcomes[ticket.ticket_id] = _CompletedTicketV1(
            ticket=copy.deepcopy(ticket),
            ticket_fingerprint=fingerprint,
            outcome=copy.deepcopy(outcome),
        )
        while len(self._completed_outcomes) > self.policy.max_completed_outcomes:
            oldest = next(iter(self._completed_outcomes))
            del self._completed_outcomes[oldest]

    def _cache_terminal_ticket(
        self,
        ticket: PredictionTicketV1,
    ) -> PredictionTicketV1:
        if ticket.terminal_disposition is None:
            raise UnderstandingLoopError("terminal ticket lacks disposition")
        outcome = UnderstandingOutcomeV1(
            ticket_id=ticket.ticket_id,
            disposition=ticket.terminal_disposition,
            disposition_record=None,
            local_update=None,
            curiosity_item=None,
            knowledge_delta=None,
            capability_gap=None,
        )
        self._remember_completed(ticket, outcome)
        return ticket

    def _empty_state(self, target_key: str) -> PredictionStateV1:
        return PredictionStateV1(
            target_key=target_key,
            generation=0,
            state_digest=sha256_digest(
                {"domain": "wd.understanding_state.empty.v1", "target_key": target_key}
            ),
            expected_value=None,
            sample_count=0,
        )

    def _current_state(self, target_key: str) -> PredictionStateV1:
        return self._states.get(target_key, self._empty_state(target_key))

    def prepare_observation(self, observation: Mapping[str, Any]) -> PredictionTicketV1:
        with self._lock:
            return self._prepare_observation_locked(observation)

    def _prepare_observation_locked(
        self, observation: Mapping[str, Any]
    ) -> PredictionTicketV1:
        """Normalize a legacy runtime observation, then call the value-free API.

        The adapter sees the full observation, but ``NumericPredictor.predict``
        receives only ``ObservationEnvelopeV1.header_mapping()``.
        """

        if not isinstance(observation, Mapping):
            raise UnderstandingLoopError("observation must be a mapping")
        with self._lock:
            self._counters["received"] += 1
            self._ingest_seq += 1
            ingest_seq = self._ingest_seq
        try:
            privacy_raw = observation.get("privacy_class", "restricted")
            if type(privacy_raw) is not str:
                raise ValueError("privacy_class")
            privacy = PrivacyClass(privacy_raw)
            observed_at = observation.get("observed_at_utc")
            if observed_at is None:
                timestamp = observation.get("timestamp")
                if timestamp is not None and (
                    type(timestamp) not in (int, float)
                    or isinstance(timestamp, bool)
                    or not math.isfinite(float(timestamp))
                ):
                    raise ValueError("timestamp")
                when = (
                    self._clock()
                    if timestamp is None
                    else datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                )
                observed_at = self._iso_utc(when)
            source_seq_raw = observation.get(
                "source_seq", observation.get("source_sequence", ingest_seq)
            )
            if type(source_seq_raw) is not int or source_seq_raw < 0:
                raise ValueError("source_seq")
            source_seq = source_seq_raw
            metadata = observation.get("metadata", {})
            if type(metadata) is not dict:
                raise ValueError("metadata")
            metadata_digest = sha256_digest({"metadata": metadata})
            observation_id_raw = observation.get("observation_id")
            if observation_id_raw is not None and type(observation_id_raw) is not str:
                raise ValueError("observation_id")
            observation_id = (
                observation_id_raw
                if observation_id_raw is not None
                else self._stable_id(
                    "obs",
                    {
                        "cell": self.cell.to_mapping(),
                        "ingest_seq": ingest_seq,
                    },
                )
            )
            source = observation.get("source", "sensor")
            entity_id = observation.get("entity_id", "")
            metric = observation.get("metric", "")
            unit = observation.get("unit", "")
            for raw, label in (
                (source, "source"),
                (entity_id, "entity_id"),
                (metric, "metric"),
                (unit, "unit"),
            ):
                if type(raw) is not str:
                    raise ValueError(label)
            raw_value = observation.get("value")
            quality = observation.get("quality", 0.8)
            for raw, label in ((raw_value, "value"), (quality, "quality")):
                if (
                    type(raw) not in (int, float)
                    or isinstance(raw, bool)
                    or not math.isfinite(float(raw))
                ):
                    raise ValueError(label)
            envelope = ObservationEnvelopeV1(
                observation_id=observation_id,
                cell=self.cell,
                ingest_seq=ingest_seq,
                source_seq=source_seq,
                source=source,
                entity_id=entity_id,
                metric=metric,
                unit=unit,
                value=float(raw_value),
                observed_at_utc=observed_at,
                quality=float(quality),
                privacy_class=privacy,
                metadata_digest=metadata_digest,
            )
            source_content_digest = sha256_digest(
                {
                    "domain": "wd.source_observation.content.v1",
                    "observation_id": (
                        observation_id_raw if observation_id_raw is not None else None
                    ),
                    "source_seq": source_seq,
                    "source": envelope.source,
                    "entity_id": envelope.entity_id,
                    "metric": envelope.metric,
                    "unit": envelope.unit,
                    "value": envelope.value,
                    "observed_at_utc": (
                        envelope.observed_at_utc
                        if observation.get("observed_at_utc") is not None
                        or observation.get("timestamp") is not None
                        else None
                    ),
                    "quality": envelope.quality,
                    "privacy_class": envelope.privacy_class.value,
                    "metadata_digest": envelope.metadata_digest,
                }
            )
        except (TypeError, ValueError, UnderstandingContractError) as exc:
            with self._lock:
                self._counters["invalid"] += 1
            raise UnderstandingLoopError("observation_schema_invalid") from exc

        if privacy in (PrivacyClass.PRIVATE, PrivacyClass.RESTRICTED):
            if self._hmac_key_provider is None:
                return self._terminal_observation_with_budget(
                    envelope=envelope,
                    commitment=self._header_only_commitment(envelope),
                    disposition=UnderstandingDisposition.PRIVACY_BLOCKED,
                    reason="hmac_key_unavailable",
                )
            key, key_id, key_epoch = self._hmac_key_provider(privacy.value)
            commitment = build_observation_commitment(
                envelope,
                hmac_key=key,
                key_id=key_id,
                key_epoch=key_epoch,
            )
            return self._terminal_observation_with_budget(
                envelope=envelope,
                commitment=commitment,
                disposition=UnderstandingDisposition.PRIVACY_BLOCKED,
                reason="private_shadow_learning_disabled",
            )
        else:
            commitment = build_observation_commitment(envelope)
        return self._prepare_numeric(
            envelope.header_mapping(),
            commitment,
            source_content_digest=source_content_digest,
        )

    def _terminal_observation_with_budget(
        self,
        *,
        envelope: ObservationEnvelopeV1,
        commitment: ObservationCommitmentV1,
        disposition: UnderstandingDisposition,
        reason: str,
    ) -> PredictionTicketV1:
        target_key = derive_target_key(envelope.entity_id, envelope.metric)
        source_key = derive_source_key(
            envelope.source, envelope.entity_id, envelope.metric
        )
        with self._lock:
            state = copy.deepcopy(self._current_state(target_key))
            if not self._budget_available(source_key):
                return self._suppressed_budget_ticket(
                    envelope.header_mapping(), commitment, state
                )
            return self._terminal_ticket(
                envelope=envelope,
                commitment=commitment,
                disposition=disposition,
                reason=reason,
            )

    def _header_only_commitment(self, envelope: ObservationEnvelopeV1) -> ObservationCommitmentV1:
        nonce = secrets.token_hex(16)
        return ObservationCommitmentV1(
            commitment_digest=sha256_digest(
                {
                    "domain": "wd.observation.blocked_opaque_receipt.v1",
                    "nonce": nonce,
                }
            ),
            scheme="sha256",
            privacy_domain=f"wd.observation.{envelope.privacy_class.value}.blocked.v1",
            nonce=nonce,
        )

    def _terminal_ticket(
        self,
        *,
        envelope: ObservationEnvelopeV1,
        commitment: ObservationCommitmentV1,
        disposition: UnderstandingDisposition,
        reason: str,
    ) -> PredictionTicketV1:
        with self._lock:
            target_key = derive_target_key(envelope.entity_id, envelope.metric)
            state = copy.deepcopy(self._current_state(target_key))
            ticket_id = sha256_digest(
                {
                    "domain": "wd.prediction_ticket.terminal.v1",
                    "commitment": commitment.commitment_digest,
                    "disposition": disposition.value,
                    "reason": reason,
                    "observation_header": envelope.header_mapping(),
                }
            )
            payload = {
                "ticket_id": ticket_id,
                "observation_commitment_digest": commitment.commitment_digest,
                "prediction_digest": None,
                "disposition": disposition.value,
                "reason_codes": [reason],
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
            }
            self._append_batch_checked(
                (("disposition_recorded", payload),),
                idempotency_key=f"terminal:{ticket_id}",
            )
            counter = {
                UnderstandingDisposition.PRIVACY_BLOCKED: "privacy_blocked",
                UnderstandingDisposition.DROPPED_BUDGET: "dropped_budget",
                UnderstandingDisposition.SAMPLED_OUT: "sampled_out",
            }.get(disposition, "invalid")
            self._counters[counter] += 1
            ticket = PredictionTicketV1(
                ticket_id=ticket_id,
                target_key=target_key,
                source_key=envelope.source,
                source_seq=envelope.source_seq,
                observation_commitment=commitment,
                prediction=None,
                prior_state=state,
                terminal_disposition=disposition,
                terminal_reason=reason,
            )
            return self._cache_terminal_ticket(ticket)

    def _admission_reason(self, header: Mapping[str, Any]) -> Optional[str]:
        if header.get("source") != self.policy.allowed_source:
            return "source_not_allowed"
        entity_id = header.get("entity_id")
        if type(entity_id) is not str or not (
            entity_id == self.policy.entity_namespace
            or entity_id.startswith(self.policy.entity_namespace + ".")
        ):
            return "entity_namespace_not_allowed"
        if header.get("metric") != self.policy.metric:
            return "metric_not_allowed"
        if header.get("unit") != self.policy.unit:
            return "unit_not_allowed"
        if header.get("privacy_class") not in (
            PrivacyClass.SYNTHETIC.value,
            PrivacyClass.PUBLIC.value,
        ):
            return "privacy_not_allowed"
        return None

    @staticmethod
    def _take_token(
        bucket: tuple[float, float],
        *,
        now: float,
        rate: int,
        burst: int,
    ) -> tuple[bool, tuple[float, float]]:
        tokens, prior = bucket
        effective_now = max(now, prior)
        replenished = min(float(burst), tokens + (effective_now - prior) * rate)
        if replenished < 1.0:
            return False, (replenished, effective_now)
        return True, (replenished - 1.0, effective_now)

    def _budget_available(self, source_key: str) -> bool:
        now = self._trusted_timestamp()
        stale_before = now - max(
            60.0,
            2.0 * self.policy.per_source_burst / self.policy.per_source_rate,
        )
        stale = [
            key for key, (_tokens, updated) in self._source_buckets.items()
            if updated < stale_before
        ]
        for key in stale:
            del self._source_buckets[key]
        if (
            source_key not in self._source_buckets
            and len(self._source_buckets) >= self.policy.max_source_buckets
        ):
            return False
        source_bucket = self._source_buckets.get(
            source_key,
            (float(self.policy.per_source_burst), now),
        )
        source_ok, next_source = self._take_token(
            source_bucket,
            now=now,
            rate=self.policy.per_source_rate,
            burst=self.policy.per_source_burst,
        )
        global_ok, next_global = self._take_token(
            self._global_bucket,
            now=now,
            rate=self.policy.global_rate,
            burst=self.policy.global_burst,
        )
        if not source_ok or not global_ok:
            return False
        self._source_buckets[source_key] = next_source
        self._global_bucket = next_global
        return True

    def prepare_numeric(
        self,
        header: Mapping[str, Any],
        observation_commitment: ObservationCommitmentV1,
    ) -> PredictionTicketV1:
        """Prepare an externally committed public numeric observation."""
        with self._lock:
            self._counters["received"] += 1
        return self._prepare_numeric(
            header,
            observation_commitment,
            source_content_digest=observation_commitment.commitment_digest,
        )

    def _validated_header(self, header: Mapping[str, Any]) -> dict[str, Any]:
        if type(header) is not dict:
            raise UnderstandingLoopError("prediction header must be an exact dict")
        snapshot = dict(header)
        expected = {
            "schema_version",
            "observation_id",
            "cell",
            "ingest_seq",
            "source_seq",
            "source",
            "entity_id",
            "metric",
            "unit",
            "observed_at_utc",
            "quality",
            "privacy_class",
            "metadata_digest",
        }
        if set(snapshot) != expected:
            raise UnderstandingLoopError("prediction header shape refused")
        cell_raw = snapshot["cell"]
        if type(cell_raw) is not dict:
            raise UnderstandingLoopError("prediction header cell refused")
        try:
            cell = HexCellAddressV1(
                cell_id=cell_raw["cell_id"],
                q=cell_raw["q"],
                r=cell_raw["r"],
                incarnation_id=cell_raw["incarnation_id"],
                generation=cell_raw["generation"],
                fence=cell_raw["fence"],
            )
            privacy = PrivacyClass(snapshot["privacy_class"])
            validated = ObservationEnvelopeV1(
                observation_id=snapshot["observation_id"],
                cell=cell,
                ingest_seq=snapshot["ingest_seq"],
                source_seq=snapshot["source_seq"],
                source=snapshot["source"],
                entity_id=snapshot["entity_id"],
                metric=snapshot["metric"],
                unit=snapshot["unit"],
                value=0.0,
                observed_at_utc=snapshot["observed_at_utc"],
                quality=snapshot["quality"],
                privacy_class=privacy,
                metadata_digest=snapshot["metadata_digest"],
                schema_version=snapshot["schema_version"],
            )
        except (KeyError, TypeError, ValueError, UnderstandingContractError) as exc:
            raise UnderstandingLoopError("prediction header schema refused") from exc
        if validated.cell != self.cell:
            raise UnderstandingLoopError("prediction header cell mismatch")
        return validated.header_mapping()

    @staticmethod
    def _validate_commitment_domain(
        header: Mapping[str, Any], commitment: ObservationCommitmentV1
    ) -> None:
        privacy = PrivacyClass(header["privacy_class"])
        expected_domain = f"wd.observation.{privacy.value}.v1"
        expected_scheme = (
            "sha256"
            if privacy in (PrivacyClass.SYNTHETIC, PrivacyClass.PUBLIC)
            else "hmac-sha256"
        )
        if (
            commitment.privacy_domain != expected_domain
            or commitment.scheme != expected_scheme
        ):
            raise UnderstandingLoopError("observation commitment domain mismatch")

    def _prepare_numeric(
        self,
        header: Mapping[str, Any],
        observation_commitment: ObservationCommitmentV1,
        *,
        source_content_digest: str,
    ) -> PredictionTicketV1:
        snapshot = self._validated_header(header)
        if type(observation_commitment) is not ObservationCommitmentV1:
            raise UnderstandingLoopError("observation commitment refused")
        if not self._event_digest_valid(source_content_digest):
            raise UnderstandingLoopError("source content digest refused")
        self._validate_commitment_domain(snapshot, observation_commitment)
        target_key = derive_target_key(snapshot["entity_id"], snapshot["metric"])
        source_key = derive_source_key(
            snapshot["source"], snapshot["entity_id"], snapshot["metric"]
        )
        source_seq = snapshot["source_seq"]

        with self._lock:
            state = copy.deepcopy(self._current_state(target_key))
            sequence_key = (source_key, source_seq)
            prior_commitment = self._seen_sequences.get(sequence_key)
            sequence_reuse = False
            if prior_commitment is not None:
                if prior_commitment == source_content_digest:
                    self._counters["duplicate"] += 1
                    ticket = self._ticket_without_event(
                        snapshot,
                        observation_commitment,
                        state,
                        UnderstandingDisposition.DUPLICATE,
                        "duplicate_source_sequence",
                    )
                    return self._cache_terminal_ticket(ticket)
                sequence_reuse = True
                if source_key not in self._quarantined_sources:
                    if (
                        len(self._quarantined_sources)
                        >= self.policy.max_quarantined_sources
                    ):
                        self._quarantine_saturated = True
                    else:
                        self._quarantined_sources.add(source_key)
            high_watermark = self._source_high_watermarks.get(source_key)
            is_late = high_watermark is not None and source_seq <= high_watermark

            # The token gate precedes every durable terminal/prediction event.
            # Known sequence reuse is still detected and quarantined first so
            # a full audit bucket cannot turn a conflict into a clean retry.
            if not self._budget_available(source_key):
                return self._suppressed_budget_ticket(
                    snapshot,
                    observation_commitment,
                    state,
                )

            reason = self._admission_reason(snapshot)
            if reason is not None:
                disposition = (
                    UnderstandingDisposition.PRIVACY_BLOCKED
                    if reason == "privacy_not_allowed"
                    else UnderstandingDisposition.SCHEMA_INVALID
                )
                return self._terminal_from_header(
                    snapshot,
                    observation_commitment,
                    state,
                    disposition,
                    reason,
                )
            if self._quarantine_saturated:
                return self._terminal_from_header(
                    snapshot,
                    observation_commitment,
                    state,
                    UnderstandingDisposition.CONTRADICTORY,
                    "quarantine_capacity_fail_closed",
                )
            if source_key in self._quarantined_sources:
                return self._terminal_from_header(
                    snapshot,
                    observation_commitment,
                    state,
                    UnderstandingDisposition.CONTRADICTORY,
                    (
                        "source_sequence_reuse"
                        if sequence_reuse
                        else "source_quarantined"
                    ),
                )
            if is_late:
                return self._terminal_from_header(
                    snapshot,
                    observation_commitment,
                    state,
                    UnderstandingDisposition.EXPIRED,
                    "late_or_evicted_source_sequence",
                )
            active_targets = set(self._states)
            active_targets.update(
                issued.ticket.target_key
                for issued in self._issued_tickets.values()
                if issued.ticket.prediction is not None
            )
            if (
                target_key not in active_targets
                and len(active_targets) >= self.policy.max_targets
            ):
                self._counters["sampled_out"] += 1
                return self._terminal_from_header(
                    snapshot,
                    observation_commitment,
                    state,
                    UnderstandingDisposition.SAMPLED_OUT,
                    "target_capacity_exhausted",
                    increment_counter=False,
                )
            if len(self._issued_tickets) >= self.policy.max_pending_tickets:
                self._counters["dropped_budget"] += 1
                return self._terminal_from_header(
                    snapshot,
                    observation_commitment,
                    state,
                    UnderstandingDisposition.DROPPED_BUDGET,
                    "pending_prediction_capacity_exhausted",
                    increment_counter=False,
                )
            predictor_header = json.loads(
                canonical_json_bytes(snapshot).decode("utf-8")
            )
            predicted = self.predictor.predict(
                predictor_header,
                copy.deepcopy(state),
            )
            if predicted is not None:
                if type(predicted) not in (int, float) or isinstance(predicted, bool) or not math.isfinite(float(predicted)):
                    raise UnderstandingLoopError("predictor returned non-finite value")
                predicted = float(predicted)
                status = PredictionStatus.PREDICTED
            else:
                status = PredictionStatus.COLD_START
            committed_at = self._iso_utc(self._clock())
            prediction = PredictionCommitmentV1(
                observation_commitment_digest=observation_commitment.commitment_digest,
                ingest_seq=snapshot["ingest_seq"],
                prior_state_generation=state.generation,
                prior_state_digest=state.state_digest,
                predictor_artifact_digest=self._predictor_artifact_digest,
                predictor_config_digest=self._predictor_config_digest,
                status=status,
                predicted_value=predicted,
                committed_at_utc=committed_at,
            )
            ticket_id = sha256_digest(
                {
                    "domain": "wd.prediction_ticket.v1",
                    "prediction_digest": prediction.prediction_digest,
                    "source_key": source_key,
                    "source_seq": source_seq,
                }
            )
            prediction_payload = {
                    "ticket_id": ticket_id,
                    "target_key": target_key,
                    "source_key": source_key,
                    "source_seq": source_seq,
                    "source_content_digest": source_content_digest,
                    "cell": self.cell.to_mapping(),
                    "observation_header": snapshot,
                    "prediction": prediction.to_mapping(),
                    "prediction_digest": prediction.prediction_digest,
                    "residual_abs_threshold": float(
                        self.policy.residual_abs_threshold
                    ),
                    "state_update_alpha": STATE_UPDATE_ALPHA_V1,
                    "runtime_authority_applied": False,
                    "routing_influence_applied": False,
                }
            self._append_batch_checked(
                (("prediction_committed", prediction_payload),),
                idempotency_key=f"prediction:{ticket_id}",
            )
            self._seen_sequences[sequence_key] = source_content_digest
            self._source_high_watermarks[source_key] = source_seq
            while len(self._seen_sequences) > self.policy.max_seen_sequences:
                oldest = next(iter(self._seen_sequences))
                del self._seen_sequences[oldest]
            self._counters["accepted"] += 1
            ticket = PredictionTicketV1(
                ticket_id=ticket_id,
                target_key=target_key,
                source_key=source_key,
                source_seq=source_seq,
                observation_commitment=observation_commitment,
                prediction=prediction,
                prior_state=state,
            )
            return self._issue_ticket(ticket, header=snapshot)

    def _ticket_without_event(
        self,
        header: Mapping[str, Any],
        commitment: ObservationCommitmentV1,
        state: PredictionStateV1,
        disposition: UnderstandingDisposition,
        reason: str,
    ) -> PredictionTicketV1:
        source_key = derive_source_key(
            header["source"], header["entity_id"], header["metric"]
        )
        ticket_id = sha256_digest(
            {
                "domain": "wd.prediction_ticket.noop.v1",
                "commitment": commitment.commitment_digest,
                "disposition": disposition.value,
                "reason": reason,
                "observation_header": dict(header),
                "target_key": state.target_key,
                "source_key": source_key,
            }
        )
        return PredictionTicketV1(
            ticket_id=ticket_id,
            target_key=state.target_key,
            source_key=source_key,
            source_seq=int(header["source_seq"]),
            observation_commitment=commitment,
            prediction=None,
            prior_state=state,
            terminal_disposition=disposition,
            terminal_reason=reason,
        )

    def _terminal_from_header(
        self,
        header: Mapping[str, Any],
        commitment: ObservationCommitmentV1,
        state: PredictionStateV1,
        disposition: UnderstandingDisposition,
        reason: str,
        *,
        increment_counter: bool = True,
    ) -> PredictionTicketV1:
        ticket = self._ticket_without_event(header, commitment, state, disposition, reason)
        payload = {
                "ticket_id": ticket.ticket_id,
                "observation_commitment_digest": commitment.commitment_digest,
                "prediction_digest": None,
                "disposition": disposition.value,
                "reason_codes": [reason],
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
            }
        self._append_batch_checked(
            (("disposition_recorded", payload),),
            idempotency_key=f"terminal:{ticket.ticket_id}",
        )
        if increment_counter:
            counter = {
                UnderstandingDisposition.PRIVACY_BLOCKED: "privacy_blocked",
                UnderstandingDisposition.DROPPED_BUDGET: "dropped_budget",
                UnderstandingDisposition.SAMPLED_OUT: "sampled_out",
            }.get(disposition, "invalid")
            self._counters[counter] += 1
        return self._cache_terminal_ticket(ticket)

    def _suppressed_budget_ticket(
        self,
        header: Mapping[str, Any],
        commitment: ObservationCommitmentV1,
        state: PredictionStateV1,
    ) -> PredictionTicketV1:
        """Return an in-memory terminal outcome without amplifying the ledger.

        Once the ingress token buckets are empty, writing one durable drop row
        per hostile input would itself be an unbounded write primitive.  The
        aggregate counters remain visible, while the per-item audit is
        deliberately suppressed until trusted time replenishes the budget.
        """

        self._counters["dropped_budget"] += 1
        self._counters["audit_suppressed"] += 1
        ticket = self._ticket_without_event(
            header,
            commitment,
            state,
            UnderstandingDisposition.DROPPED_BUDGET,
            "rate_budget_exhausted_audit_suppressed",
        )
        return self._cache_terminal_ticket(ticket)

    def complete_numeric(
        self,
        ticket: PredictionTicketV1,
        value: float,
    ) -> UnderstandingOutcomeV1:
        if type(ticket) is not PredictionTicketV1:
            raise UnderstandingLoopError("prediction ticket refused")
        if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise UnderstandingLoopError("revealed value must be finite")
        actual = float(value)
        with self._lock:
            supplied_fingerprint = self._ticket_fingerprint(ticket)
            completed = self._completed_outcomes.get(ticket.ticket_id)
            if completed is not None:
                if not hmac.compare_digest(
                    completed.ticket_fingerprint, supplied_fingerprint
                ):
                    raise UnderstandingLoopError("unissued_prediction_ticket")
                return copy.deepcopy(completed.outcome)
            issued = self._issued_tickets.get(ticket.ticket_id)
            if issued is None or not hmac.compare_digest(
                issued.ticket_fingerprint, supplied_fingerprint
            ):
                raise UnderstandingLoopError("unissued_prediction_ticket")
            # From this point on use only the detached, issue-time snapshot.
            # The caller may retain and even mutate its own frozen-dataclass
            # object concurrently; it cannot change this resolution.
            ticket = copy.deepcopy(issued.ticket)
            if ticket.terminal_disposition is not None:
                outcome = UnderstandingOutcomeV1(
                    ticket_id=ticket.ticket_id,
                    disposition=ticket.terminal_disposition,
                    disposition_record=None,
                    local_update=None,
                    curiosity_item=None,
                    knowledge_delta=None,
                    capability_gap=None,
                )
                self._issued_tickets.pop(ticket.ticket_id)
                self._remember_completed(ticket, outcome)
                return outcome
            if ticket.prediction is None or issued.header is None:
                raise UnderstandingLoopError("prediction ticket lacks commitment")
            if issued.resolution_at_utc is None:
                issued = _IssuedTicketV1(
                    ticket=issued.ticket,
                    ticket_fingerprint=issued.ticket_fingerprint,
                    header=issued.header,
                    resolution_at_utc=self._iso_utc(self._clock()),
                )
                self._issued_tickets[ticket.ticket_id] = issued
            current = self._current_state(ticket.target_key)
            if (
                current.generation != ticket.prior_state.generation
                or current.state_digest != ticket.prior_state.state_digest
            ):
                return self._resolve_without_update(
                    issued,
                    UnderstandingDisposition.EXPIRED,
                    "stale_prior_generation",
                )

            try:
                privacy_class = self._verify_reveal(issued, actual)
            except UnderstandingLoopError:
                outcome = self._resolve_without_update(
                    issued,
                    UnderstandingDisposition.CONTRADICTORY,
                    "observation_commitment_mismatch",
                )
                self._quarantined_sources.add(ticket.source_key)
                self._counters["commitment_mismatch"] += 1
                return outcome
            if not self.policy.min_value <= actual <= self.policy.max_value:
                return self._resolve_without_update(
                    issued,
                    UnderstandingDisposition.SCHEMA_INVALID,
                    "value_out_of_policy_range",
                )

            assert issued.resolution_at_utc is not None
            now_text = issued.resolution_at_utc
            now = self._parse_utc(now_text)
            predicted = ticket.prediction.predicted_value
            residual: Optional[float]
            if predicted is None:
                disposition = UnderstandingDisposition.COLD_START
                residual = None
                reasons = ("no_prior_shadow_state",)
            else:
                residual = actual - predicted
                magnitude = abs(residual)
                if magnitude <= self.policy.residual_abs_threshold * 0.5:
                    disposition = UnderstandingDisposition.EXPLAINED
                    reasons = ("residual_within_expected_band",)
                elif magnitude <= self.policy.residual_abs_threshold:
                    disposition = UnderstandingDisposition.PARTIALLY_EXPLAINED
                    reasons = ("residual_near_boundary",)
                elif magnitude <= self.policy.residual_abs_threshold * 2:
                    disposition = UnderstandingDisposition.UNKNOWN
                    reasons = ("unexplained_residual",)
                else:
                    disposition = UnderstandingDisposition.CONTRADICTORY
                    reasons = ("large_counterexample_residual",)
            disposition_record = UnderstandingDispositionV1(
                observation_commitment_digest=ticket.observation_commitment.commitment_digest,
                prediction_digest=ticket.prediction.prediction_digest,
                disposition=disposition,
                residual=residual,
                reason_codes=reasons,
                recorded_at_utc=now_text,
            )

            next_count = current.sample_count + 1
            next_expected = actual if current.expected_value is None else (
                current.expected_value * (1.0 - STATE_UPDATE_ALPHA_V1)
                + actual * STATE_UPDATE_ALPHA_V1
            )
            next_core = {
                "domain": "wd.understanding_state.v1",
                "target_key": ticket.target_key,
                "generation": current.generation + 1,
                "expected_value": next_expected,
                "sample_count": next_count,
                "prediction_digest": ticket.prediction.prediction_digest,
            }
            next_state = PredictionStateV1(
                target_key=ticket.target_key,
                generation=current.generation + 1,
                state_digest=sha256_digest(next_core),
                expected_value=next_expected,
                sample_count=next_count,
            )
            update = LocalProvisionalUpdateV1(
                update_id=self._stable_id(
                    "update",
                    {
                        "cell_id": self.cell.cell_id,
                        "ingest_seq": ticket.prediction.ingest_seq,
                    },
                ),
                cell_id=self.cell.cell_id,
                prediction_digest=ticket.prediction.prediction_digest,
                prior_state_digest=current.state_digest,
                new_state_digest=next_state.state_digest,
                applied_at_utc=now_text,
            )
            curiosity_plan = self._plan_curiosity(
                ticket=ticket,
                disposition=disposition,
                now=now,
            )
            delta_plan = self._plan_delta(
                ticket=ticket,
                residual=residual,
                disposition=disposition,
                now=now,
                privacy_class=privacy_class,
            )
            curiosity = curiosity_plan.item if curiosity_plan is not None else None
            delta = delta_plan.delta if delta_plan is not None else None
            gap = None
            if delta is not None and delta.claim_kind is KnowledgeClaimKind.CAPABILITY_GAP:
                gap = CapabilityGapCandidateV1(
                    gap_id=self._stable_id(
                        "gap",
                        {
                            "cell_id": self.cell.cell_id,
                            "ingest_seq": ticket.prediction.ingest_seq,
                        },
                    ),
                    proposer_cell_id=self.cell.cell_id,
                    evidence_refs=delta.evidence_refs,
                    created_at_utc=now_text,
                )
            outcome = UnderstandingOutcomeV1(
                ticket_id=ticket.ticket_id,
                disposition=disposition,
                disposition_record=disposition_record,
                local_update=update,
                curiosity_item=curiosity,
                knowledge_delta=delta,
                capability_gap=gap,
            )

            events: list[tuple[str, Mapping[str, Any]]] = [
                (
                    "observation_revealed",
                    {
                        "ticket_id": ticket.ticket_id,
                        "observation_commitment_digest": (
                            ticket.observation_commitment.commitment_digest
                        ),
                        "value": actual,
                        "privacy_domain": ticket.observation_commitment.privacy_domain,
                        "commitment_nonce": ticket.observation_commitment.nonce,
                    },
                ),
                (
                    "disposition_recorded",
                    {
                        "ticket_id": ticket.ticket_id,
                        **disposition_record.to_mapping(),
                        "runtime_authority_applied": False,
                        "routing_influence_applied": False,
                    },
                ),
            ]
            if curiosity_plan is not None:
                item = curiosity_plan.item
                events.append(
                    (
                        "curiosity_enqueued",
                        {
                            "curiosity": {
                                **item.__dict__,
                                "action": item.action.value,
                                "evidence_refs": list(item.evidence_refs),
                            },
                            "network_invoked": False,
                            "llm_invoked": False,
                            "builder_invoked": False,
                        },
                    )
                )
            if delta is not None:
                events.append(
                    (
                        "knowledge_delta_proposed",
                        {
                            "delta": delta.to_mapping(),
                            "proposal_digest": delta.proposal_digest,
                            "hive_commit_applied": False,
                            "runtime_authority_applied": False,
                            "routing_influence_applied": False,
                        },
                    )
                )
            events.append(
                (
                    "local_provisional_update",
                    {
                        "ticket_id": ticket.ticket_id,
                        "update": update.to_mapping(),
                        "update_digest": update.update_digest,
                        "next_state": next_state.to_mapping(),
                        "runtime_authority_applied": False,
                        "routing_influence_applied": False,
                    },
                )
            )
            self._append_batch_checked(
                events,
                idempotency_key=f"resolution:{ticket.ticket_id}",
            )

            self._states[ticket.target_key] = next_state
            if curiosity_plan is not None:
                self._curiosity.append(curiosity_plan.item)
                self._curiosity_minute_counts[curiosity_plan.minute] = (
                    self._curiosity_minute_counts.get(curiosity_plan.minute, 0) + 1
                )
            if delta_plan is not None:
                self._surprises[ticket.target_key] = list(delta_plan.entries)
                if (
                    delta_plan.delta is not None
                    and delta_plan.evidence_set_digest is not None
                ):
                    self._last_delta_evidence_digest[ticket.target_key] = (
                        delta_plan.evidence_set_digest
                    )
            self._issued_tickets.pop(ticket.ticket_id)
            self._remember_completed(ticket, outcome)
            self._counters["resolved"] += 1
            self._counters["state_update_applied"] += 1
            return outcome

    def _verify_reveal(
        self,
        issued: _IssuedTicketV1,
        actual: float,
    ) -> PrivacyClass:
        header = issued.header
        ticket = issued.ticket
        if header is None or ticket.prediction is None:
            raise UnderstandingLoopError("prediction reveal context missing")
        privacy = PrivacyClass(header["privacy_class"])
        if privacy not in (PrivacyClass.SYNTHETIC, PrivacyClass.PUBLIC):
            raise UnderstandingLoopError("private observation reveal refused")
        self._validate_commitment_domain(header, ticket.observation_commitment)
        envelope = ObservationEnvelopeV1(
            observation_id=header["observation_id"],
            cell=self.cell,
            ingest_seq=header["ingest_seq"],
            source_seq=header["source_seq"],
            source=header["source"],
            entity_id=header["entity_id"],
            metric=header["metric"],
            unit=header["unit"],
            value=actual,
            observed_at_utc=header["observed_at_utc"],
            quality=header["quality"],
            privacy_class=privacy,
            metadata_digest=header["metadata_digest"],
            schema_version=header["schema_version"],
        )
        recomputed = build_observation_commitment(
            envelope,
            nonce=ticket.observation_commitment.nonce,
        )
        if not hmac.compare_digest(
            recomputed.commitment_digest,
            ticket.observation_commitment.commitment_digest,
        ):
            raise UnderstandingLoopError("observation commitment mismatch")
        if not hmac.compare_digest(
            ticket.prediction.observation_commitment_digest,
            ticket.observation_commitment.commitment_digest,
        ):
            raise UnderstandingLoopError("prediction commitment mismatch")
        return privacy

    def _resolve_without_update(
        self,
        issued: _IssuedTicketV1,
        disposition: UnderstandingDisposition,
        reason: str,
    ) -> UnderstandingOutcomeV1:
        ticket = issued.ticket
        if ticket.prediction is None or issued.resolution_at_utc is None:
            raise UnderstandingLoopError("terminal prediction context missing")
        record = UnderstandingDispositionV1(
            observation_commitment_digest=ticket.observation_commitment.commitment_digest,
            prediction_digest=ticket.prediction.prediction_digest,
            disposition=disposition,
            residual=None,
            reason_codes=(reason,),
            recorded_at_utc=issued.resolution_at_utc,
        )
        payload = {
            "ticket_id": ticket.ticket_id,
            **record.to_mapping(),
            "runtime_authority_applied": False,
            "routing_influence_applied": False,
        }
        self._append_batch_checked(
            (("disposition_recorded", payload),),
            idempotency_key=f"resolution-rejected:{ticket.ticket_id}",
        )
        outcome = UnderstandingOutcomeV1(
            ticket_id=ticket.ticket_id,
            disposition=disposition,
            disposition_record=record,
            local_update=None,
            curiosity_item=None,
            knowledge_delta=None,
            capability_gap=None,
        )
        self._issued_tickets.pop(ticket.ticket_id)
        self._remember_completed(ticket, outcome)
        self._counters["resolved"] += 1
        return outcome

    def _plan_curiosity(
        self,
        *,
        ticket: PredictionTicketV1,
        disposition: UnderstandingDisposition,
        now: datetime,
    ) -> Optional[_CuriosityPlanV1]:
        if disposition not in (
            UnderstandingDisposition.UNKNOWN,
            UnderstandingDisposition.CONTRADICTORY,
        ):
            return None
        minute = int(now.timestamp()) // 60
        if len(self._curiosity) >= self.policy.curiosity_top_k:
            return None
        if self._curiosity_minute_counts.get(minute, 0) >= self.policy.curiosity_per_minute:
            return None
        assert ticket.prediction is not None
        item = CuriosityItemV1(
            curiosity_id=self._stable_id(
                "curiosity",
                {
                    "cell_id": self.cell.cell_id,
                    "ingest_seq": ticket.prediction.ingest_seq,
                },
            ),
            cell_id=self.cell.cell_id,
            action=CuriosityAction.DETERMINISTIC_REPLAY,
            evidence_refs=(
                ticket.observation_commitment.commitment_digest,
                ticket.prediction.prediction_digest,
            ),
            expected_value=0.7,
            cost=0.1,
            risk=0.1,
            created_at_utc=self._iso_utc(now),
        )
        return _CuriosityPlanV1(item=item, minute=minute)

    def _plan_delta(
        self,
        *,
        ticket: PredictionTicketV1,
        residual: Optional[float],
        disposition: UnderstandingDisposition,
        now: datetime,
        privacy_class: PrivacyClass,
    ) -> Optional[_DeltaPlanV1]:
        if residual is None or disposition not in (
            UnderstandingDisposition.UNKNOWN,
            UnderstandingDisposition.CONTRADICTORY,
        ):
            return None
        assert ticket.prediction is not None
        window_start = now - timedelta(seconds=self.policy.delta_window_seconds)
        entries = [
            entry
            for entry in self._surprises.get(ticket.target_key, [])
            if entry[0] >= window_start and (entry[3] >= 0) == (residual >= 0)
        ]
        entries.append(
            (
                now,
                ticket.observation_commitment.commitment_digest,
                ticket.prediction.prediction_digest,
                residual,
                privacy_class,
            )
        )
        if len(entries) < self.policy.delta_min_samples:
            return _DeltaPlanV1(
                entries=tuple(entries),
                delta=None,
                evidence_set_digest=None,
            )
        selected = entries[-self.policy.delta_min_samples :]
        evidence = tuple(entry[1] for entry in selected)
        evidence_set_digest = sha256_digest({"evidence_refs": list(evidence)})
        if self._last_delta_evidence_digest.get(ticket.target_key) == evidence_set_digest:
            return _DeltaPlanV1(
                entries=tuple(entries),
                delta=None,
                evidence_set_digest=None,
            )
        aggregate = sha256_digest(
            {
                "domain": "wd.knowledge_delta.aggregate.v1",
                "target_key": ticket.target_key,
                "residuals": [entry[3] for entry in selected],
            }
        )
        evidence_privacy = (
            PrivacyClass.PUBLIC
            if any(entry[4] is PrivacyClass.PUBLIC for entry in selected)
            else PrivacyClass.SYNTHETIC
        )
        delta = KnowledgeDeltaV1(
            proposal_id=self._stable_id(
                "delta",
                {
                    "cell_id": self.cell.cell_id,
                    "ingest_seq": ticket.prediction.ingest_seq,
                },
            ),
            proposer_cell_id=self.cell.cell_id,
            claim_kind=KnowledgeClaimKind.MODEL_UPDATE,
            aggregate_digest=aggregate,
            evidence_refs=evidence,
            confidence=min(0.95, 0.5 + len(selected) * 0.05),
            privacy_class=evidence_privacy,
            created_at_utc=self._iso_utc(now),
            expires_at_utc=self._iso_utc(
                now + timedelta(seconds=self.policy.proposal_ttl_seconds)
            ),
        )
        return _DeltaPlanV1(
            entries=tuple(entries),
            delta=delta,
            evidence_set_digest=evidence_set_digest,
        )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            accounted = sum(
                self._counters[name]
                for name in (
                    "accepted",
                    "duplicate",
                    "invalid",
                    "privacy_blocked",
                    "sampled_out",
                    "dropped_budget",
                )
            )
            pending_predictions = len(self._issued_tickets)
            unresolved = max(
                self._counters["accepted"] - self._counters["resolved"],
                0,
            )
            return {
                **self._counters,
                "accounted": accounted,
                "counter_conservation_ok": accounted == self._counters["received"],
                "unresolved": unresolved,
                "resolution_conservation_ok": (
                    self._counters["accepted"]
                    == self._counters["resolved"]
                    + pending_predictions
                    and unresolved == pending_predictions
                ),
                "state_count": len(self._states),
                "curiosity_count": len(self._curiosity),
                "quarantined_source_count": len(self._quarantined_sources),
                "seen_sequence_count": len(self._seen_sequences),
                "source_high_watermark_count": len(self._source_high_watermarks),
                "pending_ticket_count": pending_predictions,
                "completed_outcome_count": len(self._completed_outcomes),
                "source_bucket_count": len(self._source_buckets),
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
                "quarantine_fail_closed": self._quarantine_saturated,
            }

    def get_state(self, target_key: str) -> PredictionStateV1:
        with self._lock:
            return copy.deepcopy(self._current_state(target_key))

    def close(self) -> None:
        """Close a durable sink when it exposes an idempotent close method."""

        close = getattr(self.event_sink, "close", None)
        if callable(close):
            close()


__all__ = [
    "InMemoryUnderstandingEventSink",
    "LastValuePredictor",
    "NumericPredictor",
    "PredictionStateV1",
    "PredictionTicketV1",
    "STATE_UPDATE_ALPHA_V1",
    "UnderstandingEventSink",
    "UnderstandingLoop",
    "UnderstandingLoopError",
    "UnderstandingOutcomeV1",
    "UnderstandingPolicyV1",
]
