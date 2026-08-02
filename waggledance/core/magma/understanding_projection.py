# SPDX-License-Identifier: Apache-2.0
"""Pure semantic replay for the Open-World Understanding V1 ledger.

The normal projection contains commitments and lifecycle summaries, never
revealed observations, prediction values, residuals, commitment nonces, or
learned numeric values.  Its nested records are deeply frozen and its public
mapping is rebuilt from exact field allowlists with literal-false authority
flags.

``reduce_understanding_restart_checkpoint`` is a deliberately separate,
process-local recovery API.  Its result contains local numeric state and may
contain a revealed value for a prediction interrupted mid-resolution.  It has
no serializer, digest, routing, action, promotion, or authority API.  Never
log, share, persist, or expose that checkpoint; use it only after verified
ledger replay to hydrate the same local shadow loop.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Optional, TYPE_CHECKING

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.understanding_ledger import (
    CURIOSITY_ENQUEUED,
    DISPOSITION_RECORDED,
    GENESIS_EVENT_HASH,
    KNOWLEDGE_DELTA_PROPOSED,
    KNOWLEDGE_DELTA_RETRACTED,
    LOCAL_PROVISIONAL_UPDATE,
    OBSERVATION_REVEALED,
    PREDICTION_COMMITTED,
    verify_understanding_event_chain,
)

if TYPE_CHECKING:
    from waggledance.core.magma.understanding_ledger import UnderstandingLedger


PROJECTION_SCHEMA = "wd.understanding_projection.v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNREVEALED = object()

_FORECAST_REASONS = {
    "cold_start": ("no_prior_shadow_state",),
    "explained": ("residual_within_expected_band",),
    "partially_explained": ("residual_near_boundary",),
    "unknown": ("unexplained_residual",),
    "contradictory": ("large_counterexample_residual",),
}
_NON_FORECAST_RESOLUTIONS = {
    ("expired", ("stale_prior_generation",)),
    ("expired", ("restart_lost_reveal_context",)),
    ("expired", ("prediction_ttl_exceeded",)),
    ("contradictory", ("observation_commitment_mismatch",)),
    ("schema_invalid", ("value_out_of_policy_range",)),
}

_TICKET_KEYS = frozenset(
    {
        "ticket_id",
        "target_key",
        "source_key",
        "source_seq",
        "cell_id",
        "cell_incarnation_id",
        "cell_generation",
        "cell_fence",
        "observation_commitment_digest",
        "prediction_digest",
        "prediction_status",
        "prior_state_generation",
        "prior_state_digest",
        "lifecycle",
        "observation_reveal_verified",
        "disposition",
        "local_update_digest",
        "runtime_authority_applied",
        "routing_influence_applied",
    }
)
_LOCAL_STATE_KEYS = frozenset(
    {
        "target_key",
        "generation",
        "state_digest",
        "sample_count",
        "last_ticket_id",
        "last_update_digest",
        "reversible",
        "runtime_authority_applied",
        "routing_influence_applied",
    }
)
_CURIOSITY_KEYS = frozenset(
    {
        "curiosity_id",
        "cell_id",
        "action",
        "evidence_refs",
        "expected_information_value",
        "cost",
        "risk",
        "created_at_utc",
        "network_invoked",
        "llm_invoked",
        "builder_invoked",
    }
)
_KNOWLEDGE_DELTA_KEYS = frozenset(
    {
        "proposal_digest",
        "proposal_id",
        "proposer_cell_id",
        "claim_kind",
        "aggregate_digest",
        "evidence_refs",
        "confidence",
        "privacy_class",
        "created_at_utc",
        "expires_at_utc",
        "status",
        "retraction_id",
        "runtime_authority_applied",
        "routing_influence_applied",
        "hive_commit_applied",
    }
)
_RETRACTION_KEYS = frozenset(
    {
        "retraction_id",
        "proposal_digest",
        "reason_codes",
        "evidence_refs",
        "retracted_at_utc",
        "runtime_authority_applied",
        "routing_influence_applied",
    }
)
_RECORD_ALLOWLISTS = {
    "tickets": _TICKET_KEYS,
    "local_states": _LOCAL_STATE_KEYS,
    "curiosity_items": _CURIOSITY_KEYS,
    "knowledge_deltas": _KNOWLEDGE_DELTA_KEYS,
    "retractions": _RETRACTION_KEYS,
}


class UnderstandingProjectionError(RuntimeError):
    """A verified event stream violates the V1 semantic state machine."""


def _canonical_copy(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


def _freeze_records(records: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_deep_freeze(_canonical_copy(record)) for record in records)


def _allowlisted_records(
    records: Sequence[Mapping[str, Any]],
    allowed_keys: frozenset[str],
    label: str,
    *,
    forced_false: frozenset[str] = frozenset(),
    forced_true: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or frozenset(record) != allowed_keys:
            raise UnderstandingProjectionError(
                f"{label}[{index}] fields differ from the projection allowlist"
            )
        serialized = {
            key: _deep_thaw(record[key]) for key in sorted(allowed_keys)
        }
        for key in forced_false:
            if serialized[key] is not False:
                raise UnderstandingProjectionError(
                    f"{label}[{index}].{key} must be literal false"
                )
        for key in forced_true:
            if serialized[key] is not True:
                raise UnderstandingProjectionError(
                    f"{label}[{index}].{key} must be literal true"
                )
        result.append(serialized)
    return result


@dataclass(frozen=True, repr=False)
class NumericStateCheckpointV1:
    """Sensitive process-local numeric state; never serialize or expose."""

    target_key: str
    generation: int
    state_digest: str
    expected_value: float
    sample_count: int


@dataclass(frozen=True, repr=False)
class PendingPredictionCheckpointV1:
    """Sensitive context for one prediction not yet durably resolved."""

    ticket_id: str
    target_key: str
    source_key: str
    source_seq: int
    source_sequence_identity_digest: str
    cell: Mapping[str, Any]
    ingest_seq: int
    observation_header: Mapping[str, Any]
    observation_commitment_digest: str
    prediction_digest: str
    prediction_status: str
    predicted_value: Optional[float]
    prior_state_generation: int
    prior_state_digest: str
    prior_expected_value: Optional[float]
    residual_abs_threshold: float
    committed_at_utc: str
    prediction_ttl_seconds: int
    state_update_alpha: float
    reveal_verified: bool
    revealed_value: Optional[float]
    commitment_nonce: Optional[str]
    privacy_domain: Optional[str]


@dataclass(frozen=True, repr=False)
class UnderstandingRestartCheckpointV1:
    """Sensitive in-memory reducer state with intentionally no serializer.

    This object may contain local observation-derived numeric values.  It is
    only a restart hydration input for the same shadow loop and carries no
    authority of any kind.
    """

    event_count: int
    ledger_head: str
    learning_domain_digest: Optional[str]
    trusted_time_watermark_utc: Optional[str]
    max_ingest_seq: int
    numeric_states: Mapping[str, NumericStateCheckpointV1]
    cell_ingest_high_watermarks: Mapping[tuple[str, str], int]
    source_sequence_registry: Mapping[tuple[str, int], str]
    source_high_watermarks: Mapping[str, int]
    pending_tickets: Mapping[str, PendingPredictionCheckpointV1]


@dataclass(frozen=True)
class UnderstandingProjectionV1:
    """Deeply frozen, raw-value-free replay result."""

    event_count: int
    ledger_head: str
    event_kind_counts: tuple[tuple[str, int], ...]
    disposition_counts: tuple[tuple[str, int], ...]
    tickets: tuple[Mapping[str, Any], ...]
    local_states: tuple[Mapping[str, Any], ...]
    curiosity_items: tuple[Mapping[str, Any], ...]
    knowledge_deltas: tuple[Mapping[str, Any], ...]
    retractions: tuple[Mapping[str, Any], ...]
    runtime_authority_applied: bool = False
    routing_influence_applied: bool = False
    hive_commit_applied: bool = False
    schema_version: str = PROJECTION_SCHEMA

    def __post_init__(self) -> None:
        if type(self.event_count) is not int or self.event_count < 0:
            raise UnderstandingProjectionError("event_count refused")
        if type(self.ledger_head) is not str or _SHA256.fullmatch(
            self.ledger_head
        ) is None:
            raise UnderstandingProjectionError("ledger_head refused")
        for label, counts in (
            ("event_kind_counts", self.event_kind_counts),
            ("disposition_counts", self.disposition_counts),
        ):
            if type(counts) is not tuple:
                raise UnderstandingProjectionError(f"{label} must be a tuple")
            seen: set[str] = set()
            for item in counts:
                if (
                    type(item) is not tuple
                    or len(item) != 2
                    or type(item[0]) is not str
                    or type(item[1]) is not int
                    or item[1] < 0
                    or item[0] in seen
                ):
                    raise UnderstandingProjectionError(f"{label} refused")
                seen.add(item[0])
            if counts != tuple(sorted(counts)):
                raise UnderstandingProjectionError(f"{label} must be canonical")
        if sum(count for _, count in self.event_kind_counts) != self.event_count:
            raise UnderstandingProjectionError("event kind counts do not conserve")
        if self.runtime_authority_applied is not False:
            raise UnderstandingProjectionError("projection cannot apply runtime authority")
        if self.routing_influence_applied is not False:
            raise UnderstandingProjectionError("projection cannot influence routing")
        if self.hive_commit_applied is not False:
            raise UnderstandingProjectionError("projection cannot commit hive knowledge")
        if self.schema_version != PROJECTION_SCHEMA:
            raise UnderstandingProjectionError("projection schema refused")
        for field, allowed_keys in _RECORD_ALLOWLISTS.items():
            value = getattr(self, field)
            if isinstance(value, (str, bytes, bytearray)) or not isinstance(
                value, Sequence
            ):
                raise UnderstandingProjectionError(f"{field} refused")
            frozen = _freeze_records(value)
            if any(
                not isinstance(record, Mapping)
                or frozenset(record) != allowed_keys
                for record in frozen
            ):
                raise UnderstandingProjectionError(
                    f"{field} fields differ from the projection allowlist"
                )
            forced_false = {
                "tickets": frozenset(
                    {"runtime_authority_applied", "routing_influence_applied"}
                ),
                "local_states": frozenset(
                    {"runtime_authority_applied", "routing_influence_applied"}
                ),
                "curiosity_items": frozenset(
                    {"network_invoked", "llm_invoked", "builder_invoked"}
                ),
                "knowledge_deltas": frozenset(
                    {
                        "runtime_authority_applied",
                        "routing_influence_applied",
                        "hive_commit_applied",
                    }
                ),
                "retractions": frozenset(
                    {"runtime_authority_applied", "routing_influence_applied"}
                ),
            }[field]
            forced_true = (
                frozenset({"reversible"})
                if field == "local_states"
                else frozenset()
            )
            _allowlisted_records(
                frozen,
                allowed_keys,
                field,
                forced_false=forced_false,
                forced_true=forced_true,
            )
            object.__setattr__(self, field, frozen)

    @property
    def pending_ticket_count(self) -> int:
        return sum(ticket["lifecycle"] == "pending" for ticket in self.tickets)

    @property
    def resolved_ticket_count(self) -> int:
        return sum(ticket["lifecycle"] == "resolved" for ticket in self.tickets)

    @property
    def active_knowledge_delta_count(self) -> int:
        return sum(delta["status"] == "active" for delta in self.knowledge_deltas)

    def to_mapping(self) -> dict[str, Any]:
        """Return only the exact public projection fields and false authority."""

        if self.runtime_authority_applied is not False:
            raise UnderstandingProjectionError(
                "projection cannot apply runtime authority"
            )
        if self.routing_influence_applied is not False:
            raise UnderstandingProjectionError(
                "projection cannot influence routing"
            )
        if self.hive_commit_applied is not False:
            raise UnderstandingProjectionError(
                "projection cannot commit hive knowledge"
            )

        return _canonical_copy(
            {
                "schema_version": PROJECTION_SCHEMA,
                "event_count": self.event_count,
                "ledger_head": self.ledger_head,
                "event_kind_counts": dict(self.event_kind_counts),
                "disposition_counts": dict(self.disposition_counts),
                "pending_ticket_count": self.pending_ticket_count,
                "resolved_ticket_count": self.resolved_ticket_count,
                "active_knowledge_delta_count": self.active_knowledge_delta_count,
                "tickets": _allowlisted_records(
                    self.tickets,
                    _TICKET_KEYS,
                    "tickets",
                    forced_false=frozenset(
                        {"runtime_authority_applied", "routing_influence_applied"}
                    ),
                ),
                "local_states": _allowlisted_records(
                    self.local_states,
                    _LOCAL_STATE_KEYS,
                    "local_states",
                    forced_false=frozenset(
                        {"runtime_authority_applied", "routing_influence_applied"}
                    ),
                    forced_true=frozenset({"reversible"}),
                ),
                "curiosity_items": _allowlisted_records(
                    self.curiosity_items,
                    _CURIOSITY_KEYS,
                    "curiosity_items",
                    forced_false=frozenset(
                        {"network_invoked", "llm_invoked", "builder_invoked"}
                    ),
                ),
                "knowledge_deltas": _allowlisted_records(
                    self.knowledge_deltas,
                    _KNOWLEDGE_DELTA_KEYS,
                    "knowledge_deltas",
                    forced_false=frozenset(
                        {
                            "runtime_authority_applied",
                            "routing_influence_applied",
                            "hive_commit_applied",
                        }
                    ),
                ),
                "retractions": _allowlisted_records(
                    self.retractions,
                    _RETRACTION_KEYS,
                    "retractions",
                    forced_false=frozenset(
                        {"runtime_authority_applied", "routing_influence_applied"}
                    ),
                ),
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
                "hive_commit_applied": False,
            }
        )

    @property
    def projection_digest(self) -> str:
        return sha256_digest(
            {"domain": "wd.understanding_projection.digest.v1", **self.to_mapping()}
        )


@dataclass(frozen=True)
class _ReplayArtifacts:
    projection: UnderstandingProjectionV1
    checkpoint: UnderstandingRestartCheckpointV1


def _fail(seq: int, message: str) -> None:
    raise UnderstandingProjectionError(f"event {seq} lifecycle refused: {message}")


def _expected_forecast(
    predicted_value: Optional[float],
    actual: float,
    threshold: float,
) -> tuple[str, Optional[float], tuple[str, ...]]:
    if predicted_value is None:
        return "cold_start", None, _FORECAST_REASONS["cold_start"]
    residual = actual - predicted_value
    magnitude = abs(residual)
    if magnitude <= threshold * 0.5:
        disposition = "explained"
    elif magnitude <= threshold:
        disposition = "partially_explained"
    elif magnitude <= threshold * 2:
        disposition = "unknown"
    else:
        disposition = "contradictory"
    return disposition, residual, _FORECAST_REASONS[disposition]


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _reduce_verified_events(
    verified: Sequence[Mapping[str, Any]],
) -> _ReplayArtifacts:
    tickets: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    public_states: dict[str, dict[str, Any]] = {}
    numeric_states: dict[str, dict[str, Any]] = {}
    curiosity: dict[str, dict[str, Any]] = {}
    proposals: dict[str, dict[str, Any]] = {}
    proposal_ids: set[str] = set()
    retractions: dict[str, dict[str, Any]] = {}
    event_counts: dict[str, int] = {}
    disposition_counts: dict[str, int] = {}
    source_registry: dict[tuple[str, int], str] = {}
    source_high_watermarks: dict[str, int] = {}
    cell_ingest_high_watermarks: dict[tuple[str, str], int] = {}
    learning_domain_digest: Optional[str] = None
    trusted_time_watermark: Optional[datetime] = None
    max_ingest_seq = 0

    for event in verified:
        seq = event["seq"]
        kind = event["event_kind"]
        payload = event["payload"]
        event_counts[kind] = event_counts.get(kind, 0) + 1

        if kind == PREDICTION_COMMITTED:
            event_learning_domain_digest = payload["learning_domain_digest"]
            if learning_domain_digest is None:
                learning_domain_digest = event_learning_domain_digest
            elif not hmac.compare_digest(
                learning_domain_digest,
                event_learning_domain_digest,
            ):
                _fail(seq, "prediction changes the ledger learning domain")
            ticket_id = payload["ticket_id"]
            if ticket_id in tickets:
                _fail(seq, "ticket_id already exists")
            target_key = payload["target_key"]
            source_key = payload["source_key"]
            source_seq = payload["source_seq"]
            sequence_key = (source_key, source_seq)
            if sequence_key in source_registry:
                _fail(seq, "source_key/source_seq already accepted")
            prior_source_seq = source_high_watermarks.get(source_key)
            if prior_source_seq is not None and source_seq <= prior_source_seq:
                _fail(seq, "source sequence is not strictly monotonic")

            prediction = payload["prediction"]
            committed_at = _parse_utc(prediction["committed_at_utc"])
            if (
                trusted_time_watermark is not None
                and committed_at < trusted_time_watermark
            ):
                _fail(seq, "prediction committed_at moves trusted time backwards")
            trusted_time_watermark = committed_at
            ingest_seq = prediction["ingest_seq"]
            cell = payload["cell"]
            cell_key = (
                cell["cell_id"],
                cell["incarnation_id"],
            )
            prior_ingest_seq = cell_ingest_high_watermarks.get(cell_key)
            if prior_ingest_seq is not None and ingest_seq <= prior_ingest_seq:
                _fail(seq, "ingest_seq is not strictly monotonic for cell incarnation")

            current = numeric_states.get(target_key)
            prior_generation = prediction["prior_state_generation"]
            prior_digest = prediction["prior_state_digest"]
            prior_expected: Optional[float]
            if current is None:
                empty_digest = sha256_digest(
                    {
                        "domain": "wd.understanding_state.empty.v1",
                        "target_key": target_key,
                    }
                )
                if prior_generation != 0 or prior_digest != empty_digest:
                    _fail(seq, "first prediction does not bind the empty state")
                prior_expected = None
            else:
                if (
                    prior_generation != current["generation"]
                    or prior_digest != current["state_digest"]
                ):
                    _fail(seq, "prediction does not bind current projected state")
                prior_expected = current["expected_value"]

            source_registry[sequence_key] = payload[
                "source_sequence_identity_digest"
            ]
            source_high_watermarks[source_key] = source_seq
            cell_ingest_high_watermarks[cell_key] = ingest_seq
            max_ingest_seq = max(max_ingest_seq, ingest_seq)
            tickets[ticket_id] = {
                "ticket_id": ticket_id,
                "target_key": target_key,
                "source_key": source_key,
                "source_seq": source_seq,
                "cell_id": cell["cell_id"],
                "cell_incarnation_id": cell["incarnation_id"],
                "cell_generation": cell["generation"],
                "cell_fence": cell["fence"],
                "observation_commitment_digest": prediction[
                    "observation_commitment_digest"
                ],
                "prediction_digest": payload["prediction_digest"],
                "prediction_status": prediction["status"],
                "prior_state_generation": prior_generation,
                "prior_state_digest": prior_digest,
                "lifecycle": "pending",
                "observation_reveal_verified": False,
                "disposition": None,
                "local_update_digest": None,
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
            }
            contexts[ticket_id] = {
                "cell": _canonical_copy(cell),
                "header": _canonical_copy(payload["observation_header"]),
                "source_sequence_identity_digest": payload[
                    "source_sequence_identity_digest"
                ],
                "ingest_seq": ingest_seq,
                "predicted_value": prediction["predicted_value"],
                "prior_expected_value": prior_expected,
                "threshold": payload["residual_abs_threshold"],
                "committed_at": committed_at,
                "prediction_ttl_seconds": payload[
                    "prediction_ttl_seconds"
                ],
                "alpha": payload["state_update_alpha"],
                "actual": _UNREVEALED,
                "commitment_nonce": None,
                "privacy_domain": None,
                "forecast_resolved": False,
                "resolved_at": None,
            }

        elif kind == OBSERVATION_REVEALED:
            ticket = tickets.get(payload["ticket_id"])
            if ticket is None:
                _fail(seq, "observation reveal has no prediction")
            if ticket["lifecycle"] != "pending":
                _fail(seq, "observation reveal follows a terminal disposition")
            if ticket["observation_reveal_verified"]:
                _fail(seq, "observation was revealed twice")
            if (
                payload["observation_commitment_digest"]
                != ticket["observation_commitment_digest"]
            ):
                _fail(seq, "observation commitment differs from prediction")
            context = contexts[ticket["ticket_id"]]
            header = context["header"]
            expected_privacy_domain = f"wd.observation.{header['privacy_class']}.v1"
            if payload["privacy_domain"] != expected_privacy_domain:
                _fail(seq, "observation privacy domain differs from header")
            recomputed_commitment = sha256_digest(
                {
                    "domain": expected_privacy_domain,
                    "canonicalization_version": "magma-jcs-subset-v1",
                    "observation": {
                        **header,
                        "value": float(payload["value"]),
                    },
                    "nonce": payload["commitment_nonce"],
                }
            )
            if not hmac.compare_digest(
                recomputed_commitment,
                ticket["observation_commitment_digest"],
            ):
                _fail(seq, "revealed observation does not open commitment")
            ticket["observation_reveal_verified"] = True
            context["actual"] = float(payload["value"])
            context["commitment_nonce"] = payload["commitment_nonce"]
            context["privacy_domain"] = payload["privacy_domain"]

        elif kind == DISPOSITION_RECORDED:
            ticket_id = payload["ticket_id"]
            prediction_digest = payload["prediction_digest"]
            if prediction_digest is None:
                if ticket_id in tickets:
                    _fail(seq, "terminal no-prediction ticket collides")
                tickets[ticket_id] = {
                    "ticket_id": ticket_id,
                    "target_key": None,
                    "source_key": None,
                    "source_seq": None,
                    "cell_id": None,
                    "cell_incarnation_id": None,
                    "cell_generation": None,
                    "cell_fence": None,
                    "observation_commitment_digest": payload[
                        "observation_commitment_digest"
                    ],
                    "prediction_digest": None,
                    "prediction_status": None,
                    "prior_state_generation": None,
                    "prior_state_digest": None,
                    "lifecycle": "resolved",
                    "observation_reveal_verified": False,
                    "disposition": payload["disposition"],
                    "local_update_digest": None,
                    "runtime_authority_applied": False,
                    "routing_influence_applied": False,
                }
            else:
                ticket = tickets.get(ticket_id)
                if ticket is None:
                    _fail(seq, "disposition has no prediction")
                if ticket["lifecycle"] != "pending":
                    _fail(seq, "ticket has multiple dispositions")
                if prediction_digest != ticket["prediction_digest"]:
                    _fail(seq, "disposition prediction digest mismatch")
                if (
                    payload["observation_commitment_digest"]
                    != ticket["observation_commitment_digest"]
                ):
                    _fail(seq, "disposition observation commitment mismatch")
                context = contexts[ticket_id]
                reasons = tuple(payload["reason_codes"])
                recorded_at = _parse_utc(payload["recorded_at_utc"])
                committed_at = context["committed_at"]
                if recorded_at < committed_at:
                    _fail(seq, "disposition predates prediction commitment")
                if (
                    trusted_time_watermark is not None
                    and recorded_at < trusted_time_watermark
                ):
                    _fail(seq, "disposition moves trusted time backwards")
                if (
                    payload["disposition"] == "expired"
                    and reasons == ("prediction_ttl_exceeded",)
                    and recorded_at
                    <= committed_at
                    + timedelta(
                        seconds=context["prediction_ttl_seconds"]
                    )
                ):
                    _fail(seq, "TTL expiry does not exceed prediction deadline")
                trusted_time_watermark = recorded_at
                context["resolved_at"] = recorded_at
                actual = context["actual"]
                non_forecast = (payload["disposition"], reasons)
                if non_forecast in _NON_FORECAST_RESOLUTIONS:
                    if actual is not _UNREVEALED or payload["residual"] is not None:
                        _fail(seq, "non-forecast resolution carries a reveal or residual")
                else:
                    if actual is _UNREVEALED:
                        _fail(seq, "forecast-derived disposition lacks reveal")
                    expected_disposition, expected_residual, expected_reasons = (
                        _expected_forecast(
                            context["predicted_value"],
                            actual,
                            context["threshold"],
                        )
                    )
                    if payload["disposition"] != expected_disposition:
                        _fail(seq, "forecast disposition does not match residual")
                    if reasons != expected_reasons:
                        _fail(seq, "forecast reason_codes do not match disposition")
                    if payload["residual"] != expected_residual:
                        _fail(seq, "recorded residual is not exact")
                    context["forecast_resolved"] = True
                ticket["lifecycle"] = "resolved"
                ticket["disposition"] = payload["disposition"]
            disposition = payload["disposition"]
            disposition_counts[disposition] = (
                disposition_counts.get(disposition, 0) + 1
            )

        elif kind == CURIOSITY_ENQUEUED:
            item = payload["curiosity"]
            created_at = _parse_utc(item["created_at_utc"])
            if (
                trusted_time_watermark is not None
                and created_at < trusted_time_watermark
            ):
                _fail(seq, "curiosity moves trusted time backwards")
            trusted_time_watermark = created_at
            curiosity_id = item["curiosity_id"]
            if curiosity_id in curiosity:
                _fail(seq, "curiosity_id already exists")
            curiosity[curiosity_id] = {
                "curiosity_id": curiosity_id,
                "cell_id": item["cell_id"],
                "action": item["action"],
                "evidence_refs": list(item["evidence_refs"]),
                "expected_information_value": item["expected_value"],
                "cost": item["cost"],
                "risk": item["risk"],
                "created_at_utc": item["created_at_utc"],
                "network_invoked": False,
                "llm_invoked": False,
                "builder_invoked": False,
            }

        elif kind == KNOWLEDGE_DELTA_PROPOSED:
            digest = payload["proposal_digest"]
            delta = payload["delta"]
            created_at = _parse_utc(delta["created_at_utc"])
            if (
                trusted_time_watermark is not None
                and created_at < trusted_time_watermark
            ):
                _fail(seq, "knowledge delta moves trusted time backwards")
            trusted_time_watermark = created_at
            if digest in proposals or delta["proposal_id"] in proposal_ids:
                _fail(seq, "knowledge delta identity already exists")
            proposal_ids.add(delta["proposal_id"])
            proposals[digest] = {
                "proposal_digest": digest,
                "proposal_id": delta["proposal_id"],
                "proposer_cell_id": delta["proposer_cell_id"],
                "claim_kind": delta["claim_kind"],
                "aggregate_digest": delta["aggregate_digest"],
                "evidence_refs": list(delta["evidence_refs"]),
                "confidence": delta["confidence"],
                "privacy_class": delta["privacy_class"],
                "created_at_utc": delta["created_at_utc"],
                "expires_at_utc": delta["expires_at_utc"],
                "status": "active",
                "retraction_id": None,
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
                "hive_commit_applied": False,
            }

        elif kind == LOCAL_PROVISIONAL_UPDATE:
            ticket = tickets.get(payload["ticket_id"])
            if ticket is None or ticket["prediction_digest"] is None:
                _fail(seq, "local update has no prediction")
            context = contexts[ticket["ticket_id"]]
            if ticket["lifecycle"] != "resolved":
                _fail(seq, "local update precedes disposition")
            if ticket["local_update_digest"] is not None:
                _fail(seq, "ticket has multiple local updates")
            if not context["forecast_resolved"]:
                _fail(seq, "local update lacks an exact forecast resolution")
            actual = context["actual"]
            if actual is _UNREVEALED:
                _fail(seq, "local update lacks a verified reveal")
            update = payload["update"]
            state = payload["next_state"]
            applied_at = _parse_utc(update["applied_at_utc"])
            if (
                context["resolved_at"] is None
                or applied_at != context["resolved_at"]
            ):
                _fail(seq, "local update time differs from disposition")
            if (
                trusted_time_watermark is not None
                and applied_at < trusted_time_watermark
            ):
                _fail(seq, "local update moves trusted time backwards")
            trusted_time_watermark = applied_at
            if update["prediction_digest"] != ticket["prediction_digest"]:
                _fail(seq, "local update prediction digest mismatch")
            if update["cell_id"] != ticket["cell_id"]:
                _fail(seq, "local update cell identity mismatch")
            if update["prior_state_digest"] != ticket["prior_state_digest"]:
                _fail(seq, "local update prior state mismatch")
            if state["target_key"] != ticket["target_key"]:
                _fail(seq, "local update target mismatch")
            if state["generation"] != ticket["prior_state_generation"] + 1:
                _fail(seq, "local update generation is not monotonic")
            current = numeric_states.get(state["target_key"])
            prior_sample_count = 0
            if current is not None:
                if (
                    current["generation"] != ticket["prior_state_generation"]
                    or current["state_digest"] != update["prior_state_digest"]
                ):
                    _fail(seq, "local update is stale")
                prior_sample_count = current["sample_count"]
            if state["sample_count"] != prior_sample_count + 1:
                _fail(seq, "local update sample count is not monotonic")
            prior_expected = context["prior_expected_value"]
            expected_value = (
                actual
                if prior_expected is None
                else prior_expected * (1.0 - context["alpha"])
                + actual * context["alpha"]
            )
            if state["expected_value"] != expected_value:
                _fail(seq, "next expected state is not the exact V1 EWMA")
            numeric_states[state["target_key"]] = {
                "target_key": state["target_key"],
                "generation": state["generation"],
                "state_digest": state["state_digest"],
                "expected_value": expected_value,
                "sample_count": state["sample_count"],
            }
            public_states[state["target_key"]] = {
                "target_key": state["target_key"],
                "generation": state["generation"],
                "state_digest": state["state_digest"],
                "sample_count": state["sample_count"],
                "last_ticket_id": ticket["ticket_id"],
                "last_update_digest": payload["update_digest"],
                "reversible": True,
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
            }
            ticket["local_update_digest"] = payload["update_digest"]

        elif kind == KNOWLEDGE_DELTA_RETRACTED:
            digest = payload["proposal_digest"]
            proposal = proposals.get(digest)
            if proposal is None:
                _fail(seq, "retraction targets an unknown proposal")
            if proposal["status"] != "active":
                _fail(seq, "proposal was already retracted")
            retraction_id = payload["retraction_id"]
            if retraction_id in retractions:
                _fail(seq, "retraction_id already exists")
            retracted_at = datetime.fromisoformat(
                payload["retracted_at_utc"][:-1] + "+00:00"
            )
            if (
                trusted_time_watermark is not None
                and retracted_at < trusted_time_watermark
            ):
                _fail(seq, "retraction moves trusted time backwards")
            proposed_at = datetime.fromisoformat(
                proposal["created_at_utc"][:-1] + "+00:00"
            )
            if retracted_at < proposed_at:
                _fail(seq, "retraction predates proposal")
            trusted_time_watermark = retracted_at
            retraction = {
                "retraction_id": retraction_id,
                "proposal_digest": digest,
                "reason_codes": list(payload["reason_codes"]),
                "evidence_refs": list(payload["evidence_refs"]),
                "retracted_at_utc": payload["retracted_at_utc"],
                "runtime_authority_applied": False,
                "routing_influence_applied": False,
            }
            retractions[retraction_id] = retraction
            proposal["status"] = "retracted"
            proposal["retraction_id"] = retraction_id

        else:  # pragma: no cover - structural verifier owns the allowlist.
            _fail(seq, "unknown event kind")

    for ticket_id, context in contexts.items():
        ticket = tickets[ticket_id]
        if context["forecast_resolved"] and ticket["local_update_digest"] is None:
            _fail(len(verified), "forecast resolution lacks local update")

    head = verified[-1]["event_hash"] if verified else GENESIS_EVENT_HASH
    projection = UnderstandingProjectionV1(
        event_count=len(verified),
        ledger_head=head,
        event_kind_counts=tuple(sorted(event_counts.items())),
        disposition_counts=tuple(sorted(disposition_counts.items())),
        tickets=tuple(_canonical_copy(tickets[key]) for key in sorted(tickets)),
        local_states=tuple(
            _canonical_copy(public_states[key]) for key in sorted(public_states)
        ),
        curiosity_items=tuple(
            _canonical_copy(curiosity[key]) for key in sorted(curiosity)
        ),
        knowledge_deltas=tuple(
            _canonical_copy(proposals[key]) for key in sorted(proposals)
        ),
        retractions=tuple(
            _canonical_copy(retractions[key]) for key in sorted(retractions)
        ),
    )

    checkpoint_states = MappingProxyType(
        {
            key: NumericStateCheckpointV1(**numeric_states[key])
            for key in sorted(numeric_states)
        }
    )
    pending: dict[str, PendingPredictionCheckpointV1] = {}
    for ticket_id in sorted(contexts):
        ticket = tickets[ticket_id]
        if ticket["lifecycle"] != "pending":
            continue
        context = contexts[ticket_id]
        actual = context["actual"]
        pending[ticket_id] = PendingPredictionCheckpointV1(
            ticket_id=ticket_id,
            target_key=ticket["target_key"],
            source_key=ticket["source_key"],
            source_seq=ticket["source_seq"],
            source_sequence_identity_digest=context[
                "source_sequence_identity_digest"
            ],
            cell=_deep_freeze(context["cell"]),
            ingest_seq=context["ingest_seq"],
            observation_header=_deep_freeze(context["header"]),
            observation_commitment_digest=ticket[
                "observation_commitment_digest"
            ],
            prediction_digest=ticket["prediction_digest"],
            prediction_status=ticket["prediction_status"],
            predicted_value=context["predicted_value"],
            prior_state_generation=ticket["prior_state_generation"],
            prior_state_digest=ticket["prior_state_digest"],
            prior_expected_value=context["prior_expected_value"],
            residual_abs_threshold=context["threshold"],
            committed_at_utc=context["committed_at"].isoformat().replace(
                "+00:00", "Z"
            ),
            prediction_ttl_seconds=context["prediction_ttl_seconds"],
            state_update_alpha=context["alpha"],
            reveal_verified=actual is not _UNREVEALED,
            revealed_value=None if actual is _UNREVEALED else actual,
            commitment_nonce=context["commitment_nonce"],
            privacy_domain=context["privacy_domain"],
        )
    checkpoint = UnderstandingRestartCheckpointV1(
        event_count=len(verified),
        ledger_head=head,
        learning_domain_digest=learning_domain_digest,
        trusted_time_watermark_utc=(
            None
            if trusted_time_watermark is None
            else trusted_time_watermark.isoformat().replace("+00:00", "Z")
        ),
        max_ingest_seq=max_ingest_seq,
        numeric_states=checkpoint_states,
        cell_ingest_high_watermarks=MappingProxyType(
            dict(sorted(cell_ingest_high_watermarks.items()))
        ),
        source_sequence_registry=MappingProxyType(
            dict(sorted(source_registry.items()))
        ),
        source_high_watermarks=MappingProxyType(
            dict(sorted(source_high_watermarks.items()))
        ),
        pending_tickets=MappingProxyType(pending),
    )
    return _ReplayArtifacts(projection=projection, checkpoint=checkpoint)


def replay_understanding_projection(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_head: Optional[str] = None,
) -> UnderstandingProjectionV1:
    """Verify and semantically replay without exporting local numeric values."""

    verified = verify_understanding_event_chain(events, expected_head=expected_head)
    return _reduce_verified_events(verified).projection


def reduce_understanding_restart_checkpoint(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_head: Optional[str] = None,
) -> UnderstandingRestartCheckpointV1:
    """Build sensitive in-memory restart state from a verified event stream.

    The return value intentionally has no serialization method.  It may contain
    observation-derived numerics and must stay inside the local shadow runtime.
    """

    verified = verify_understanding_event_chain(events, expected_head=expected_head)
    return _reduce_verified_events(verified).checkpoint


def replay_understanding_projection_and_checkpoint(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_head: Optional[str] = None,
) -> tuple[UnderstandingProjectionV1, UnderstandingRestartCheckpointV1]:
    """Run one verified replay when both public and restart views are needed."""

    verified = verify_understanding_event_chain(events, expected_head=expected_head)
    artifacts = _reduce_verified_events(verified)
    return artifacts.projection, artifacts.checkpoint


def project_understanding_ledger(
    ledger: "UnderstandingLedger", *, expected_head: Optional[str] = None
) -> UnderstandingProjectionV1:
    events = ledger.read_verified_events(expected_head=expected_head)
    return _reduce_verified_events(events).projection


def reduce_understanding_ledger_restart_checkpoint(
    ledger: "UnderstandingLedger", *, expected_head: Optional[str] = None
) -> UnderstandingRestartCheckpointV1:
    """Verified safe restart-hydration path for a durable ledger."""

    events = ledger.read_verified_events(expected_head=expected_head)
    return _reduce_verified_events(events).checkpoint


replay_projection = replay_understanding_projection
UnderstandingProjection = UnderstandingProjectionV1


__all__ = [
    "NumericStateCheckpointV1",
    "PROJECTION_SCHEMA",
    "PendingPredictionCheckpointV1",
    "UnderstandingProjection",
    "UnderstandingProjectionError",
    "UnderstandingProjectionV1",
    "UnderstandingRestartCheckpointV1",
    "project_understanding_ledger",
    "reduce_understanding_ledger_restart_checkpoint",
    "reduce_understanding_restart_checkpoint",
    "replay_projection",
    "replay_understanding_projection",
    "replay_understanding_projection_and_checkpoint",
]
