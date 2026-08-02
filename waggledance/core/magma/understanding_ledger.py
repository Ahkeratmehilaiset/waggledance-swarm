# SPDX-License-Identifier: Apache-2.0
"""Durable MAGMA ledger for Open-World Understanding V1.

The ledger is deliberately narrow.  It accepts only the seven V1 shadow
events, assigns the sequence and predecessor hash inside one SQLite write
transaction, and rejects payload fields which are not part of the frozen
event contract.  It grants no runtime, routing, promotion, or builder
authority.

SQLite is used as an append-only journal rather than as a mutable projection:
WAL mode, ``synchronous=FULL``, a bounded busy timeout, ``BEGIN IMMEDIATE``,
and update/delete triggers protect both event and idempotency rows.  Consumers
must use :meth:`UnderstandingLedger.read_verified_events` before replay; the
reader checks canonical JSON, every self hash, the complete predecessor chain,
and an optional expected head.

The append path performs constant-time structural validation against the
current tail; it does not rescan historical lifecycle state on every write.
Consequently restart activation must pass ``read_verified_events()`` through
``reduce_understanding_ledger_restart_checkpoint()`` before hydrating a loop.
A structurally valid but semantically forged low-level stream is retained for
audit and fails closed in that reducer; it is never treated as safe runtime
state.
"""

from __future__ import annotations

import hmac
import json
import math
import re
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from waggledance.core.learning.understanding_contracts import (
    derive_source_key,
    derive_target_key,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


EVENT_SCHEMA = "wd.understanding_event.v1"
LEDGER_SCHEMA_VERSION = 1
GENESIS_EVENT_HASH = "sha256:" + "0" * 64

PREDICTION_COMMITTED = "prediction_committed"
OBSERVATION_REVEALED = "observation_revealed"
DISPOSITION_RECORDED = "disposition_recorded"
CURIOSITY_ENQUEUED = "curiosity_enqueued"
KNOWLEDGE_DELTA_PROPOSED = "knowledge_delta_proposed"
LOCAL_PROVISIONAL_UPDATE = "local_provisional_update"
KNOWLEDGE_DELTA_RETRACTED = "knowledge_delta_retracted"

UNDERSTANDING_EVENT_KINDS = frozenset(
    {
        PREDICTION_COMMITTED,
        OBSERVATION_REVEALED,
        DISPOSITION_RECORDED,
        CURIOSITY_ENQUEUED,
        KNOWLEDGE_DELTA_PROPOSED,
        LOCAL_PROVISIONAL_UPDATE,
        KNOWLEDGE_DELTA_RETRACTED,
    }
)

_PROTECTED_TABLES = frozenset({"understanding_events", "understanding_batches"})
_TRIGGER_DEFINITIONS = {
    "understanding_events_no_update": """
        CREATE TRIGGER IF NOT EXISTS understanding_events_no_update
        BEFORE UPDATE ON understanding_events
        BEGIN
            SELECT RAISE(ABORT, 'understanding_events is append-only');
        END
    """,
    "understanding_events_no_delete": """
        CREATE TRIGGER IF NOT EXISTS understanding_events_no_delete
        BEFORE DELETE ON understanding_events
        BEGIN
            SELECT RAISE(ABORT, 'understanding_events is append-only');
        END
    """,
    "understanding_batches_no_update": """
        CREATE TRIGGER IF NOT EXISTS understanding_batches_no_update
        BEFORE UPDATE ON understanding_batches
        BEGIN
            SELECT RAISE(ABORT, 'understanding_batches is append-only');
        END
    """,
    "understanding_batches_no_delete": """
        CREATE TRIGGER IF NOT EXISTS understanding_batches_no_delete
        BEFORE DELETE ON understanding_batches
        BEGIN
            SELECT RAISE(ABORT, 'understanding_batches is append-only');
        END
    """,
}

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_MAX_SEQUENCE = (1 << 63) - 1

_OBSERVATION_SCHEMA = "wd.observation_envelope.v1"
_PREDICTION_SCHEMA = "wd.prediction_commitment.v1"
_DISPOSITION_SCHEMA = "wd.understanding_disposition.v1"
_KNOWLEDGE_DELTA_SCHEMA = "wd.knowledge_delta.v1"

_PREDICTION_STATUSES = frozenset({"predicted", "cold_start"})
_DISPOSITIONS = frozenset(
    {
        "explained",
        "partially_explained",
        "cold_start",
        "unknown",
        "contradictory",
        "duplicate",
        "sampled_out",
        "dropped_budget",
        "schema_invalid",
        "privacy_blocked",
        "expired",
    }
)
_TERMINAL_WITHOUT_PREDICTION = frozenset(
    {
        "contradictory",
        "sampled_out",
        "dropped_budget",
        "schema_invalid",
        "privacy_blocked",
        "expired",
    }
)
_CURIOSITY_ACTIONS = frozenset({"magma_lookup", "deterministic_replay"})
_KNOWLEDGE_CLAIM_KINDS = frozenset(
    {
        "fact_candidate",
        "hypothesis",
        "model_update",
        "source_reliability",
        "counterexample",
        "capability_gap",
    }
)


class UnderstandingLedgerError(ValueError):
    """An append request is outside the V1 ledger contract."""


class UnderstandingLedgerCorruptionError(RuntimeError):
    """Persisted rows do not form the exact verified V1 event stream."""


class UnderstandingLedgerOverflowError(UnderstandingLedgerError):
    """The fixed-width V1 SQLite sequence space has been exhausted."""


@dataclass(frozen=True)
class UnderstandingLedgerHeadV1:
    """Verified position of an understanding ledger."""

    event_count: int
    event_hash: str

    def to_mapping(self) -> dict[str, object]:
        return {"event_count": self.event_count, "event_hash": self.event_hash}


def _normalize_schema_sql(value: object) -> str:
    if type(value) is not str:
        raise UnderstandingLedgerCorruptionError("schema SQL is not text")
    return " ".join(value.strip().rstrip(";").split())


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UnderstandingLedgerError(f"{label} must be a mapping")
    if not all(type(key) is str for key in value):
        raise UnderstandingLedgerError(f"{label} keys must be strings")
    return value


def _require_exact_keys(
    value: object,
    expected: frozenset[str] | set[str],
    label: str,
) -> Mapping[str, Any]:
    mapping = _require_mapping(value, label)
    actual = frozenset(mapping.keys())
    expected_frozen = frozenset(expected)
    if actual != expected_frozen:
        missing = sorted(expected_frozen - actual)
        extra = sorted(actual - expected_frozen)
        raise UnderstandingLedgerError(
            f"{label} keys refused (missing={missing}, extra={extra})"
        )
    return mapping


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise UnderstandingLedgerError(f"{label} must be a bounded token")
    return value


def _require_digest(value: object, label: str, *, hmac_ok: bool = False) -> str:
    valid = type(value) is str and _SHA256.fullmatch(value) is not None
    if hmac_ok:
        valid = valid or (
            type(value) is str and _HMAC_SHA256.fullmatch(value) is not None
        )
    if not valid:
        raise UnderstandingLedgerError(f"{label} must be a canonical digest")
    return value


def _require_int(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SEQUENCE,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise UnderstandingLedgerError(f"{label} must be an exact bounded integer")
    return value


def _require_finite(value: object, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise UnderstandingLedgerError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise UnderstandingLedgerError(f"{label} must be finite")
    return number


def _require_probability(value: object, label: str) -> float:
    number = _require_finite(value, label)
    if not 0.0 <= number <= 1.0:
        raise UnderstandingLedgerError(f"{label} must be within 0..1")
    return number


def _require_utc(value: object, label: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise UnderstandingLedgerError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise UnderstandingLedgerError(f"{label} must be a real UTC instant") from exc
    if (
        parsed.tzinfo != timezone.utc
        or parsed.isoformat().replace("+00:00", "Z") != value
    ):
        raise UnderstandingLedgerError(f"{label} must use canonical Z spelling")
    return value


def _require_false(value: object, label: str) -> None:
    if value is not False:
        raise UnderstandingLedgerError(f"{label} must be exactly false")


def _require_digest_list(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
    hmac_ok: bool = True,
) -> tuple[str, ...]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise UnderstandingLedgerError(
            f"{label} must contain {minimum}..{maximum} digests"
        )
    digests = tuple(
        _require_digest(item, label, hmac_ok=hmac_ok) for item in value
    )
    if len(set(digests)) != len(digests):
        raise UnderstandingLedgerError(f"{label} must be unique")
    return digests


def _require_reason_codes(value: object) -> tuple[str, ...]:
    if type(value) is not list or not 1 <= len(value) <= 16:
        raise UnderstandingLedgerError("reason_codes must be a non-empty bounded list")
    reasons = tuple(_require_token(item, "reason_code") for item in value)
    if len(set(reasons)) != len(reasons):
        raise UnderstandingLedgerError("reason_codes must be unique")
    return reasons


def _validate_cell(value: object, label: str) -> Mapping[str, Any]:
    cell = _require_exact_keys(
        value,
        {"cell_id", "q", "r", "incarnation_id", "generation", "fence"},
        label,
    )
    _require_token(cell["cell_id"], f"{label}.cell_id")
    _require_int(cell["q"], f"{label}.q", minimum=-1_000_000, maximum=1_000_000)
    _require_int(cell["r"], f"{label}.r", minimum=-1_000_000, maximum=1_000_000)
    _require_token(cell["incarnation_id"], f"{label}.incarnation_id")
    _require_int(cell["generation"], f"{label}.generation")
    _require_int(cell["fence"], f"{label}.fence")
    return cell


def _validate_observation_header(value: object) -> Mapping[str, Any]:
    header = _require_exact_keys(
        value,
        {
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
        },
        "observation_header",
    )
    if header["schema_version"] != _OBSERVATION_SCHEMA:
        raise UnderstandingLedgerError("observation header schema refused")
    _require_token(header["observation_id"], "observation_id")
    _validate_cell(header["cell"], "observation_header.cell")
    _require_int(header["ingest_seq"], "ingest_seq")
    _require_int(header["source_seq"], "source_seq")
    for field in ("source", "entity_id", "metric", "unit"):
        _require_token(header[field], field)
    _require_utc(header["observed_at_utc"], "observed_at_utc")
    _require_probability(header["quality"], "quality")
    if header["privacy_class"] not in ("synthetic", "public"):
        raise UnderstandingLedgerError("prediction header privacy class refused")
    _require_digest(header["metadata_digest"], "metadata_digest")
    return header


def _validate_prediction(value: object) -> Mapping[str, Any]:
    prediction = _require_exact_keys(
        value,
        {
            "schema_version",
            "observation_commitment_digest",
            "ingest_seq",
            "prior_state_generation",
            "prior_state_digest",
            "predictor_artifact_digest",
            "predictor_config_digest",
            "status",
            "predicted_value",
            "committed_at_utc",
        },
        "prediction",
    )
    if prediction["schema_version"] != _PREDICTION_SCHEMA:
        raise UnderstandingLedgerError("prediction schema refused")
    _require_digest(
        prediction["observation_commitment_digest"],
        "observation_commitment_digest",
        hmac_ok=True,
    )
    _require_int(prediction["ingest_seq"], "prediction.ingest_seq")
    _require_int(
        prediction["prior_state_generation"], "prediction.prior_state_generation"
    )
    for field in (
        "prior_state_digest",
        "predictor_artifact_digest",
        "predictor_config_digest",
    ):
        _require_digest(prediction[field], field)
    if prediction["status"] not in _PREDICTION_STATUSES:
        raise UnderstandingLedgerError("prediction status refused")
    if prediction["status"] == "predicted":
        _require_finite(prediction["predicted_value"], "predicted_value")
    elif prediction["predicted_value"] is not None:
        raise UnderstandingLedgerError("cold_start cannot carry predicted_value")
    _require_utc(prediction["committed_at_utc"], "committed_at_utc")
    return prediction


def _validate_prediction_committed(payload: object) -> None:
    record = _require_exact_keys(
        payload,
        {
            "ticket_id",
            "target_key",
            "source_key",
            "source_seq",
            "cell",
            "observation_header",
            "prediction",
            "prediction_digest",
            "source_content_digest",
            "residual_abs_threshold",
            "state_update_alpha",
            "runtime_authority_applied",
            "routing_influence_applied",
        },
        PREDICTION_COMMITTED,
    )
    _require_digest(record["ticket_id"], "ticket_id")
    _require_token(record["target_key"], "target_key")
    _require_token(record["source_key"], "source_key")
    _require_int(record["source_seq"], "source_seq")
    cell = _validate_cell(record["cell"], "cell")
    header = _validate_observation_header(record["observation_header"])
    prediction = _validate_prediction(record["prediction"])
    prediction_digest = _require_digest(
        record["prediction_digest"], "prediction_digest"
    )
    _require_digest(record["source_content_digest"], "source_content_digest")
    residual_abs_threshold = _require_finite(
        record["residual_abs_threshold"], "residual_abs_threshold"
    )
    if residual_abs_threshold <= 0:
        raise UnderstandingLedgerError("residual_abs_threshold must be positive")
    if type(record["state_update_alpha"]) is not float or record[
        "state_update_alpha"
    ] != 0.1:
        raise UnderstandingLedgerError("state_update_alpha must be exactly 0.1")
    expected_prediction_digest = sha256_digest(
        {"domain": "wd.prediction_commitment.digest.v1", **dict(prediction)}
    )
    if not hmac.compare_digest(prediction_digest, expected_prediction_digest):
        raise UnderstandingLedgerError("prediction_digest mismatch")
    if dict(cell) != dict(header["cell"]):
        raise UnderstandingLedgerError("cell/header cell mismatch")
    if record["source_seq"] != header["source_seq"]:
        raise UnderstandingLedgerError("source sequence mismatch")
    if prediction["ingest_seq"] != header["ingest_seq"]:
        raise UnderstandingLedgerError("prediction ingest sequence mismatch")
    expected_target = derive_target_key(header["entity_id"], header["metric"])
    expected_source = derive_source_key(
        header["source"], header["entity_id"], header["metric"]
    )
    if record["target_key"] != expected_target or record["source_key"] != expected_source:
        raise UnderstandingLedgerError("derived prediction keys mismatch")
    expected_ticket_id = sha256_digest(
        {
            "domain": "wd.prediction_ticket.v1",
            "prediction_digest": prediction_digest,
            "source_key": record["source_key"],
            "source_seq": record["source_seq"],
        }
    )
    if not hmac.compare_digest(record["ticket_id"], expected_ticket_id):
        raise UnderstandingLedgerError("prediction ticket_id mismatch")
    _require_false(record["runtime_authority_applied"], "runtime authority")
    _require_false(record["routing_influence_applied"], "routing influence")


def _validate_observation_revealed(payload: object) -> None:
    record = _require_exact_keys(
        payload,
        {
            "ticket_id",
            "observation_commitment_digest",
            "value",
            "privacy_domain",
            "commitment_nonce",
        },
        OBSERVATION_REVEALED,
    )
    _require_digest(record["ticket_id"], "ticket_id")
    _require_digest(
        record["observation_commitment_digest"],
        "observation_commitment_digest",
        hmac_ok=True,
    )
    _require_finite(record["value"], "revealed value")
    if record["privacy_domain"] not in (
        "wd.observation.synthetic.v1",
        "wd.observation.public.v1",
    ):
        raise UnderstandingLedgerError("revealed privacy domain refused")
    if (
        type(record["commitment_nonce"]) is not str
        or _NONCE.fullmatch(record["commitment_nonce"]) is None
    ):
        raise UnderstandingLedgerError("commitment_nonce refused")


def _validate_disposition_recorded(payload: object) -> None:
    common = {
        "ticket_id",
        "observation_commitment_digest",
        "prediction_digest",
        "disposition",
        "reason_codes",
        "runtime_authority_applied",
        "routing_influence_applied",
    }
    resolved = common | {"schema_version", "residual", "recorded_at_utc"}
    mapping = _require_mapping(payload, DISPOSITION_RECORDED)
    if frozenset(mapping.keys()) == frozenset(common):
        record = _require_exact_keys(mapping, common, DISPOSITION_RECORDED)
        if record["prediction_digest"] is not None:
            raise UnderstandingLedgerError("terminal disposition prediction must be null")
        if record["disposition"] not in _TERMINAL_WITHOUT_PREDICTION:
            raise UnderstandingLedgerError("terminal disposition kind refused")
    elif frozenset(mapping.keys()) == frozenset(resolved):
        record = _require_exact_keys(mapping, resolved, DISPOSITION_RECORDED)
        if record["schema_version"] != _DISPOSITION_SCHEMA:
            raise UnderstandingLedgerError("disposition schema refused")
        _require_digest(record["prediction_digest"], "prediction_digest")
        if record["residual"] is not None:
            _require_finite(record["residual"], "residual")
        _require_utc(record["recorded_at_utc"], "recorded_at_utc")
    else:
        raise UnderstandingLedgerError("disposition payload keys refused")
    _require_digest(record["ticket_id"], "ticket_id")
    _require_digest(
        record["observation_commitment_digest"],
        "observation_commitment_digest",
        hmac_ok=True,
    )
    if record["disposition"] not in _DISPOSITIONS:
        raise UnderstandingLedgerError("disposition refused")
    _require_reason_codes(record["reason_codes"])
    _require_false(record["runtime_authority_applied"], "runtime authority")
    _require_false(record["routing_influence_applied"], "routing influence")


def _validate_curiosity_enqueued(payload: object) -> None:
    record = _require_exact_keys(
        payload,
        {"curiosity", "network_invoked", "llm_invoked", "builder_invoked"},
        CURIOSITY_ENQUEUED,
    )
    curiosity = _require_exact_keys(
        record["curiosity"],
        {
            "curiosity_id",
            "cell_id",
            "action",
            "evidence_refs",
            "expected_value",
            "cost",
            "risk",
            "created_at_utc",
        },
        "curiosity",
    )
    _require_token(curiosity["curiosity_id"], "curiosity_id")
    _require_token(curiosity["cell_id"], "curiosity.cell_id")
    if curiosity["action"] not in _CURIOSITY_ACTIONS:
        raise UnderstandingLedgerError("curiosity action refused")
    _require_digest_list(
        curiosity["evidence_refs"], "curiosity.evidence_refs", minimum=1, maximum=32
    )
    for field in ("expected_value", "cost", "risk"):
        _require_probability(curiosity[field], f"curiosity.{field}")
    _require_utc(curiosity["created_at_utc"], "curiosity.created_at_utc")
    _require_false(record["network_invoked"], "network_invoked")
    _require_false(record["llm_invoked"], "llm_invoked")
    _require_false(record["builder_invoked"], "builder_invoked")


def _validate_knowledge_delta(value: object) -> Mapping[str, Any]:
    delta = _require_exact_keys(
        value,
        {
            "schema_version",
            "proposal_id",
            "proposer_cell_id",
            "claim_kind",
            "aggregate_digest",
            "evidence_refs",
            "confidence",
            "privacy_class",
            "created_at_utc",
            "expires_at_utc",
            "runtime_authority_applied",
            "routing_influence_applied",
        },
        "delta",
    )
    if delta["schema_version"] != _KNOWLEDGE_DELTA_SCHEMA:
        raise UnderstandingLedgerError("knowledge delta schema refused")
    _require_token(delta["proposal_id"], "proposal_id")
    _require_token(delta["proposer_cell_id"], "proposer_cell_id")
    if delta["claim_kind"] not in _KNOWLEDGE_CLAIM_KINDS:
        raise UnderstandingLedgerError("knowledge claim kind refused")
    _require_digest(delta["aggregate_digest"], "aggregate_digest")
    _require_digest_list(
        delta["evidence_refs"], "delta.evidence_refs", minimum=5, maximum=256
    )
    _require_probability(delta["confidence"], "delta.confidence")
    if delta["privacy_class"] not in ("synthetic", "public"):
        raise UnderstandingLedgerError("knowledge delta privacy refused")
    created_text = _require_utc(delta["created_at_utc"], "delta.created_at_utc")
    expires_text = _require_utc(delta["expires_at_utc"], "delta.expires_at_utc")
    created = datetime.fromisoformat(created_text[:-1] + "+00:00")
    expires = datetime.fromisoformat(expires_text[:-1] + "+00:00")
    if expires <= created:
        raise UnderstandingLedgerError("knowledge delta expiry refused")
    _require_false(delta["runtime_authority_applied"], "delta runtime authority")
    _require_false(delta["routing_influence_applied"], "delta routing influence")
    return delta


def _validate_knowledge_delta_proposed(payload: object) -> None:
    record = _require_exact_keys(
        payload,
        {
            "delta",
            "proposal_digest",
            "hive_commit_applied",
            "runtime_authority_applied",
            "routing_influence_applied",
        },
        KNOWLEDGE_DELTA_PROPOSED,
    )
    delta = _validate_knowledge_delta(record["delta"])
    digest = _require_digest(record["proposal_digest"], "proposal_digest")
    expected = sha256_digest(
        {"domain": "wd.knowledge_delta.digest.v1", **dict(delta)}
    )
    if not hmac.compare_digest(digest, expected):
        raise UnderstandingLedgerError("proposal_digest mismatch")
    _require_false(record["hive_commit_applied"], "hive commit")
    _require_false(record["runtime_authority_applied"], "runtime authority")
    _require_false(record["routing_influence_applied"], "routing influence")


def _validate_local_provisional_update(payload: object) -> None:
    record = _require_exact_keys(
        payload,
        {
            "ticket_id",
            "update",
            "update_digest",
            "next_state",
            "runtime_authority_applied",
            "routing_influence_applied",
        },
        LOCAL_PROVISIONAL_UPDATE,
    )
    _require_digest(record["ticket_id"], "ticket_id")
    update = _require_exact_keys(
        record["update"],
        {
            "update_id",
            "cell_id",
            "prediction_digest",
            "prior_state_digest",
            "new_state_digest",
            "applied_at_utc",
            "reversible",
            "runtime_authority_applied",
            "routing_influence_applied",
        },
        "update",
    )
    _require_token(update["update_id"], "update_id")
    _require_token(update["cell_id"], "update.cell_id")
    for field in ("prediction_digest", "prior_state_digest", "new_state_digest"):
        _require_digest(update[field], f"update.{field}")
    _require_utc(update["applied_at_utc"], "update.applied_at_utc")
    if update["reversible"] is not True:
        raise UnderstandingLedgerError("local update must be reversible")
    _require_false(update["runtime_authority_applied"], "update runtime authority")
    _require_false(update["routing_influence_applied"], "update routing influence")
    update_digest = _require_digest(record["update_digest"], "update_digest")
    expected_update = sha256_digest(
        {"domain": "wd.local_provisional_update.digest.v1", **dict(update)}
    )
    if not hmac.compare_digest(update_digest, expected_update):
        raise UnderstandingLedgerError("update_digest mismatch")
    state = _require_exact_keys(
        record["next_state"],
        {"target_key", "generation", "state_digest", "expected_value", "sample_count"},
        "next_state",
    )
    _require_token(state["target_key"], "next_state.target_key")
    _require_int(state["generation"], "next_state.generation", minimum=1)
    _require_digest(state["state_digest"], "next_state.state_digest")
    _require_finite(state["expected_value"], "next_state.expected_value")
    _require_int(state["sample_count"], "next_state.sample_count", minimum=1)
    if state["state_digest"] != update["new_state_digest"]:
        raise UnderstandingLedgerError("next state/update digest mismatch")
    expected_state_digest = sha256_digest(
        {
            "domain": "wd.understanding_state.v1",
            "target_key": state["target_key"],
            "generation": state["generation"],
            "expected_value": state["expected_value"],
            "sample_count": state["sample_count"],
            "prediction_digest": update["prediction_digest"],
        }
    )
    if not hmac.compare_digest(state["state_digest"], expected_state_digest):
        raise UnderstandingLedgerError("next state_digest mismatch")
    _require_false(record["runtime_authority_applied"], "runtime authority")
    _require_false(record["routing_influence_applied"], "routing influence")


def _validate_knowledge_delta_retracted(payload: object) -> None:
    record = _require_exact_keys(
        payload,
        {
            "proposal_digest",
            "retraction_id",
            "reason_codes",
            "evidence_refs",
            "retracted_at_utc",
            "runtime_authority_applied",
            "routing_influence_applied",
        },
        KNOWLEDGE_DELTA_RETRACTED,
    )
    _require_digest(record["proposal_digest"], "proposal_digest")
    _require_token(record["retraction_id"], "retraction_id")
    _require_reason_codes(record["reason_codes"])
    _require_digest_list(
        record["evidence_refs"], "retraction.evidence_refs", minimum=1, maximum=256
    )
    _require_utc(record["retracted_at_utc"], "retracted_at_utc")
    _require_false(record["runtime_authority_applied"], "runtime authority")
    _require_false(record["routing_influence_applied"], "routing influence")


_PAYLOAD_VALIDATORS = {
    PREDICTION_COMMITTED: _validate_prediction_committed,
    OBSERVATION_REVEALED: _validate_observation_revealed,
    DISPOSITION_RECORDED: _validate_disposition_recorded,
    CURIOSITY_ENQUEUED: _validate_curiosity_enqueued,
    KNOWLEDGE_DELTA_PROPOSED: _validate_knowledge_delta_proposed,
    LOCAL_PROVISIONAL_UPDATE: _validate_local_provisional_update,
    KNOWLEDGE_DELTA_RETRACTED: _validate_knowledge_delta_retracted,
}


def validate_understanding_event_payload(
    event_kind: object, payload: object
) -> dict[str, Any]:
    """Validate and return an isolated canonical copy of one V1 payload."""

    if type(event_kind) is not str or event_kind not in UNDERSTANDING_EVENT_KINDS:
        raise UnderstandingLedgerError("event kind is outside the V1 allowlist")
    mapping = _require_mapping(payload, "event payload")
    try:
        payload_copy = json.loads(canonical_json_bytes(dict(mapping)).decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UnderstandingLedgerError("event payload is not canonical JSON") from exc
    _PAYLOAD_VALIDATORS[event_kind](payload_copy)
    return payload_copy


def build_understanding_event(
    event_kind: str,
    payload: Mapping[str, Any],
    *,
    seq: int,
    prev_event_hash: str,
) -> dict[str, Any]:
    """Build one validated, self-hashed event with caller-supplied chain position."""

    _require_int(seq, "seq", minimum=1)
    _require_digest(prev_event_hash, "prev_event_hash")
    payload_copy = validate_understanding_event_payload(event_kind, payload)
    core = {
        "schema_version": EVENT_SCHEMA,
        "seq": seq,
        "event_kind": event_kind,
        "payload": payload_copy,
        "prev_event_hash": prev_event_hash,
    }
    event_hash = sha256_digest(
        {"domain": "wd.understanding_event.digest.v1", **core}
    )
    return {**core, "event_hash": event_hash}


def verify_understanding_event_chain(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_head: Optional[str] = None,
) -> tuple[dict[str, Any], ...]:
    """Fail closed unless *events* is the complete canonical V1 chain."""

    if isinstance(events, (str, bytes, bytearray)) or not isinstance(events, Sequence):
        raise UnderstandingLedgerCorruptionError("event stream must be a sequence")
    if expected_head is not None:
        try:
            _require_digest(expected_head, "expected_head")
        except UnderstandingLedgerError as exc:
            raise UnderstandingLedgerCorruptionError(str(exc)) from exc
    verified: list[dict[str, Any]] = []
    predecessor = GENESIS_EVENT_HASH
    for index, untrusted in enumerate(events, start=1):
        try:
            event = _require_exact_keys(
                untrusted,
                {
                    "schema_version",
                    "seq",
                    "event_kind",
                    "payload",
                    "prev_event_hash",
                    "event_hash",
                },
                f"event[{index}]",
            )
            if event["schema_version"] != EVENT_SCHEMA:
                raise UnderstandingLedgerError("event schema refused")
            if type(event["seq"]) is not int or event["seq"] != index:
                raise UnderstandingLedgerError("event sequence is not contiguous")
            if event["prev_event_hash"] != predecessor:
                raise UnderstandingLedgerError("event predecessor hash mismatch")
            rebuilt = build_understanding_event(
                event["event_kind"],
                event["payload"],
                seq=index,
                prev_event_hash=predecessor,
            )
            recorded_hash = _require_digest(event["event_hash"], "event_hash")
            if not hmac.compare_digest(recorded_hash, rebuilt["event_hash"]):
                raise UnderstandingLedgerError("event self hash mismatch")
        except UnderstandingLedgerError as exc:
            raise UnderstandingLedgerCorruptionError(
                f"understanding event {index} refused: {exc}"
            ) from exc
        verified.append(rebuilt)
        predecessor = rebuilt["event_hash"]
    if expected_head is not None and not hmac.compare_digest(
        predecessor, expected_head
    ):
        raise UnderstandingLedgerCorruptionError("expected ledger head mismatch")
    return tuple(verified)


class UnderstandingLedger:
    """SQLite append-only sink; semantic safety is established by the reducer."""

    def __init__(self, db_path: Path | str, *, busy_timeout_ms: int = 5_000) -> None:
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 600_000:
            raise UnderstandingLedgerError("busy_timeout_ms refused")
        if str(db_path) == ":memory:":
            raise UnderstandingLedgerError("WAL ledger requires a filesystem path")
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,
            timeout=busy_timeout_ms / 1000.0,
        )
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            journal_mode = str(
                self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            ).lower()
            if journal_mode != "wal":
                raise UnderstandingLedgerError("SQLite WAL mode unavailable")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._create_schema()
            self.read_verified_events()
        except BaseException:
            self._conn.close()
            self._closed = True
            raise

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_open(self) -> None:
        if self._closed:
            raise UnderstandingLedgerError("understanding ledger is closed")

    def _create_schema(self) -> None:
        prior_version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if prior_version not in (0, LEDGER_SCHEMA_VERSION):
            raise UnderstandingLedgerCorruptionError(
                "unsupported understanding ledger schema version"
            )
        preexisting = {
            row["name"]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('understanding_events', 'understanding_batches')"
            )
        }
        if prior_version == 0 and preexisting:
            raise UnderstandingLedgerCorruptionError(
                "unversioned understanding ledger tables refused"
            )
        event_kinds_sql = ",".join(
            "'" + kind.replace("'", "''") + "'"
            for kind in sorted(UNDERSTANDING_EVENT_KINDS)
        )
        with self._lock:
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS understanding_events (
                    seq INTEGER PRIMARY KEY CHECK (seq >= 1),
                    schema_version TEXT NOT NULL CHECK (schema_version = '{EVENT_SCHEMA}'),
                    event_kind TEXT NOT NULL CHECK (event_kind IN ({event_kinds_sql})),
                    payload_json TEXT NOT NULL,
                    prev_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS understanding_batches (
                    idempotency_key TEXT PRIMARY KEY,
                    batch_digest TEXT NOT NULL,
                    first_seq INTEGER NOT NULL CHECK (first_seq >= 1),
                    event_count INTEGER NOT NULL CHECK (event_count >= 1),
                    event_hashes_json TEXT NOT NULL
                );
                """
            )
            for trigger_sql in _TRIGGER_DEFINITIONS.values():
                self._conn.execute(trigger_sql)
            if prior_version == 0:
                self._conn.execute(f"PRAGMA user_version={LEDGER_SCHEMA_VERSION}")
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version != LEDGER_SCHEMA_VERSION:
                raise UnderstandingLedgerCorruptionError("ledger schema version mismatch")
            self._verify_trigger_definitions_locked()

    def _verify_trigger_definitions_locked(self) -> None:
        rows = list(
            self._conn.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE type='trigger' AND tbl_name IN "
                "('understanding_events', 'understanding_batches')"
            )
        )
        actual = {row["name"]: row for row in rows}
        if frozenset(actual) != frozenset(_TRIGGER_DEFINITIONS):
            raise UnderstandingLedgerCorruptionError(
                "understanding ledger trigger set mismatch"
            )
        for name, expected_sql in _TRIGGER_DEFINITIONS.items():
            # sqlite_master intentionally omits the creation-only
            # ``IF NOT EXISTS`` clause while preserving the trigger body.
            stored_expected = expected_sql.replace(
                "CREATE TRIGGER IF NOT EXISTS", "CREATE TRIGGER"
            )
            if _normalize_schema_sql(actual[name]["sql"]) != _normalize_schema_sql(
                stored_expected
            ):
                raise UnderstandingLedgerCorruptionError(
                    f"understanding ledger trigger definition mismatch: {name}"
                )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        raw_payload = row["payload_json"]
        if type(raw_payload) is not str:
            raise UnderstandingLedgerCorruptionError("payload_json is not text")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise UnderstandingLedgerCorruptionError("payload_json is malformed") from exc
        try:
            canonical = canonical_json_bytes(payload).decode("utf-8")
        except (TypeError, ValueError) as exc:
            raise UnderstandingLedgerCorruptionError("payload_json is not canonical") from exc
        if raw_payload != canonical:
            raise UnderstandingLedgerCorruptionError("payload_json spelling is non-canonical")
        return {
            "schema_version": row["schema_version"],
            "seq": row["seq"],
            "event_kind": row["event_kind"],
            "payload": payload,
            "prev_event_hash": row["prev_event_hash"],
            "event_hash": row["event_hash"],
        }

    def _rows_locked(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT seq, schema_version, event_kind, payload_json, "
                "prev_event_hash, event_hash FROM understanding_events ORDER BY seq"
            )
        )

    def read_verified_events(
        self, *, expected_head: Optional[str] = None
    ) -> tuple[dict[str, Any], ...]:
        with self._lock:
            self._ensure_open()
            if self._conn.in_transaction:
                raise UnderstandingLedgerError(
                    "verified read cannot borrow a mutable transaction"
                )
            self._conn.execute("BEGIN")
            try:
                self._verify_trigger_definitions_locked()
                rows = self._rows_locked()
                events = tuple(self._row_to_event(row) for row in rows)
                verified = verify_understanding_event_chain(
                    events, expected_head=expected_head
                )
                self._verify_batch_receipts_locked(verified)
                self._conn.execute("COMMIT")
                return verified
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _verify_batch_receipts_locked(
        self, events: Sequence[Mapping[str, Any]]
    ) -> None:
        claimed_sequences: set[int] = set()
        rows = list(
            self._conn.execute(
                "SELECT idempotency_key, batch_digest, first_seq, event_count, "
                "event_hashes_json FROM understanding_batches "
                "ORDER BY first_seq, idempotency_key"
            )
        )
        for row in rows:
            try:
                _require_token(row["idempotency_key"], "stored idempotency_key")
                batch_digest = _require_digest(
                    row["batch_digest"], "stored batch_digest"
                )
                first_seq = _require_int(
                    row["first_seq"], "stored first_seq", minimum=1
                )
                count = _require_int(
                    row["event_count"], "stored event_count", minimum=1
                )
            except UnderstandingLedgerError as exc:
                raise UnderstandingLedgerCorruptionError(
                    "stored batch receipt fields refused"
                ) from exc
            if first_seq > _MAX_SEQUENCE - count + 1:
                raise UnderstandingLedgerCorruptionError(
                    "stored batch sequence range overflows V1"
                )
            stop = first_seq + count
            if stop - 1 > len(events):
                raise UnderstandingLedgerCorruptionError(
                    "stored batch receipt is outside the event stream"
                )
            sequences = set(range(first_seq, stop))
            if claimed_sequences.intersection(sequences):
                raise UnderstandingLedgerCorruptionError(
                    "stored batch receipts overlap"
                )
            claimed_sequences.update(sequences)
            raw_hashes = row["event_hashes_json"]
            if type(raw_hashes) is not str:
                raise UnderstandingLedgerCorruptionError(
                    "stored batch hashes are not text"
                )
            try:
                parsed_hashes = json.loads(raw_hashes)
                canonical_hashes = canonical_json_bytes(parsed_hashes).decode("utf-8")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise UnderstandingLedgerCorruptionError(
                    "stored batch hashes are malformed"
                ) from exc
            if raw_hashes != canonical_hashes or type(parsed_hashes) is not list:
                raise UnderstandingLedgerCorruptionError(
                    "stored batch hashes are non-canonical"
                )
            selected = events[first_seq - 1 : stop - 1]
            expected_hashes = [event["event_hash"] for event in selected]
            if parsed_hashes != expected_hashes:
                raise UnderstandingLedgerCorruptionError(
                    "stored batch hashes differ from the event stream"
                )
            expected_batch_digest = self._batch_digest(
                tuple(
                    (event["event_kind"], event["payload"])
                    for event in selected
                )
            )
            if not hmac.compare_digest(batch_digest, expected_batch_digest):
                raise UnderstandingLedgerCorruptionError(
                    "stored batch digest differs from the event stream"
                )
        expected_sequences = set(range(1, len(events) + 1))
        if claimed_sequences != expected_sequences:
            raise UnderstandingLedgerCorruptionError(
                "event stream lacks exact batch receipt coverage"
            )

    def verify_chain(
        self, *, expected_head: Optional[str] = None
    ) -> tuple[dict[str, Any], ...]:
        """Alias used by audit callers; corruption is always an exception."""

        return self.read_verified_events(expected_head=expected_head)

    def _current_head_locked(self) -> UnderstandingLedgerHeadV1:
        # One statement gives a single SQLite read snapshot even when another
        # process appends through a separate UnderstandingLedger instance.
        rows = list(
            self._conn.execute(
                "SELECT seq, schema_version, event_kind, payload_json, "
                "prev_event_hash, event_hash, "
                "(SELECT COUNT(*) FROM understanding_events) AS total_count "
                "FROM understanding_events ORDER BY seq DESC LIMIT 2"
            )
        )
        if not rows:
            return UnderstandingLedgerHeadV1(0, GENESIS_EVENT_HASH)
        count = int(rows[0]["total_count"])
        if int(rows[0]["seq"]) != count:
            raise UnderstandingLedgerCorruptionError("event sequence contains a hole")
        events = [self._row_to_event(row) for row in reversed(rows)]
        last = events[-1]
        expected_prev = (
            GENESIS_EVENT_HASH if count == 1 else events[-2]["event_hash"]
        )
        try:
            rebuilt = build_understanding_event(
                last["event_kind"],
                last["payload"],
                seq=count,
                prev_event_hash=expected_prev,
            )
        except UnderstandingLedgerError as exc:
            raise UnderstandingLedgerCorruptionError("ledger tail refused") from exc
        if (
            last["seq"] != count
            or last["prev_event_hash"] != expected_prev
            or not hmac.compare_digest(last["event_hash"], rebuilt["event_hash"])
        ):
            raise UnderstandingLedgerCorruptionError("ledger tail chain mismatch")
        return UnderstandingLedgerHeadV1(count, last["event_hash"])

    @property
    def head(self) -> str:
        with self._lock:
            self._ensure_open()
            return self._current_head_locked().event_hash

    @property
    def event_count(self) -> int:
        with self._lock:
            self._ensure_open()
            return self._current_head_locked().event_count

    def head_record(self) -> UnderstandingLedgerHeadV1:
        with self._lock:
            self._ensure_open()
            return self._current_head_locked()

    @staticmethod
    def _canonical_batch(
        events: Sequence[tuple[str, Mapping[str, Any]]]
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        if isinstance(events, (str, bytes, bytearray)) or not isinstance(
            events, Sequence
        ):
            raise UnderstandingLedgerError("event batch must be a sequence")
        if not events:
            raise UnderstandingLedgerError("event batch must be non-empty")
        validated: list[tuple[str, dict[str, Any]]] = []
        for index, item in enumerate(events):
            if type(item) not in (tuple, list) or len(item) != 2:
                raise UnderstandingLedgerError(f"event batch item {index} refused")
            kind, payload = item
            validated.append(
                (kind, validate_understanding_event_payload(kind, payload))
            )
        return tuple(validated)

    @staticmethod
    def _batch_digest(events: Sequence[tuple[str, Mapping[str, Any]]]) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding_event_batch.v1",
                "events": [
                    {"event_kind": kind, "payload": dict(payload)}
                    for kind, payload in events
                ],
            }
        )

    def _prior_batch_locked(
        self, idempotency_key: str, batch_digest: str
    ) -> Optional[tuple[str, ...]]:
        row = self._conn.execute(
            "SELECT batch_digest, first_seq, event_count, event_hashes_json "
            "FROM understanding_batches WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        stored_digest = _require_digest(row["batch_digest"], "stored batch digest")
        if not hmac.compare_digest(stored_digest, batch_digest):
            raise UnderstandingLedgerError("idempotency_key payload mismatch")
        first_seq = _require_int(row["first_seq"], "stored first_seq", minimum=1)
        event_count = _require_int(
            row["event_count"], "stored event_count", minimum=1
        )
        if first_seq > _MAX_SEQUENCE - event_count + 1:
            raise UnderstandingLedgerCorruptionError(
                "idempotency receipt sequence range overflows V1"
            )
        raw_hashes = row["event_hashes_json"]
        try:
            hashes_value = json.loads(raw_hashes)
            canonical_hashes = canonical_json_bytes(hashes_value).decode("utf-8")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UnderstandingLedgerCorruptionError(
                "idempotency receipt is malformed"
            ) from exc
        if (
            type(raw_hashes) is not str
            or raw_hashes != canonical_hashes
            or type(hashes_value) is not list
            or len(hashes_value) != event_count
        ):
            raise UnderstandingLedgerCorruptionError("idempotency receipt length mismatch")
        hashes = tuple(
            _require_digest(value, "idempotency event hash") for value in hashes_value
        )
        rows = list(
            self._conn.execute(
                "SELECT event_hash FROM understanding_events "
                "WHERE seq >= ? AND seq < ? ORDER BY seq",
                (first_seq, first_seq + event_count),
            )
        )
        actual = tuple(row["event_hash"] for row in rows)
        if actual != hashes:
            raise UnderstandingLedgerCorruptionError(
                "idempotency receipt does not bind persisted events"
            )
        return hashes

    def _append_validated_locked(
        self,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        idempotency_key: Optional[str],
        batch_digest: str,
    ) -> tuple[str, ...]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._verify_trigger_definitions_locked()
            if idempotency_key is not None:
                prior = self._prior_batch_locked(idempotency_key, batch_digest)
                if prior is not None:
                    self._conn.execute("COMMIT")
                    return prior
            head = self._current_head_locked()
            if head.event_count > _MAX_SEQUENCE - len(events):
                raise UnderstandingLedgerOverflowError(
                    "understanding event sequence space exhausted"
                )
            sequence = head.event_count + 1
            predecessor = head.event_hash
            staged: list[dict[str, Any]] = []
            for event_kind, payload in events:
                event = build_understanding_event(
                    event_kind,
                    payload,
                    seq=sequence,
                    prev_event_hash=predecessor,
                )
                self._conn.execute(
                    "INSERT INTO understanding_events "
                    "(seq, schema_version, event_kind, payload_json, "
                    "prev_event_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event["seq"],
                        event["schema_version"],
                        event["event_kind"],
                        canonical_json_bytes(event["payload"]).decode("utf-8"),
                        event["prev_event_hash"],
                        event["event_hash"],
                    ),
                )
                staged.append(event)
                predecessor = event["event_hash"]
                sequence += 1
            hashes = tuple(event["event_hash"] for event in staged)
            receipt_key = idempotency_key
            if receipt_key is None:
                receipt_key = (
                    f"append-event:{head.event_count + 1}:{hashes[0][7:]}"
                )
            self._conn.execute(
                "INSERT INTO understanding_batches "
                "(idempotency_key, batch_digest, first_seq, event_count, "
                "event_hashes_json) VALUES (?, ?, ?, ?, ?)",
                (
                    receipt_key,
                    batch_digest,
                    head.event_count + 1,
                    len(staged),
                    canonical_json_bytes(list(hashes)).decode("utf-8"),
                ),
            )
            self._conn.execute("COMMIT")
            return hashes
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def append_event(self, event_kind: str, payload: Mapping[str, Any]) -> str:
        validated_payload = validate_understanding_event_payload(event_kind, payload)
        validated = ((event_kind, validated_payload),)
        batch_digest = self._batch_digest(validated)
        with self._lock:
            self._ensure_open()
            return self._append_validated_locked(
                validated,
                idempotency_key=None,
                batch_digest=batch_digest,
            )[0]

    def append_batch(
        self,
        events: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        idempotency_key: str,
    ) -> tuple[str, ...]:
        key = _require_token(idempotency_key, "idempotency_key")
        validated = self._canonical_batch(events)
        batch_digest = self._batch_digest(validated)
        with self._lock:
            self._ensure_open()
            return self._append_validated_locked(
                validated,
                idempotency_key=key,
                batch_digest=batch_digest,
            )

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        """Verified compatibility view matching ``InMemoryUnderstandingEventSink``."""

        return self.read_verified_events()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self) -> "UnderstandingLedger":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


# Concise aliases for callers which use the general MAGMA naming convention.
verify_chain = verify_understanding_event_chain
validate_event_payload = validate_understanding_event_payload


__all__ = [
    "CURIOSITY_ENQUEUED",
    "DISPOSITION_RECORDED",
    "EVENT_SCHEMA",
    "GENESIS_EVENT_HASH",
    "KNOWLEDGE_DELTA_PROPOSED",
    "KNOWLEDGE_DELTA_RETRACTED",
    "LEDGER_SCHEMA_VERSION",
    "LOCAL_PROVISIONAL_UPDATE",
    "OBSERVATION_REVEALED",
    "PREDICTION_COMMITTED",
    "UNDERSTANDING_EVENT_KINDS",
    "UnderstandingLedger",
    "UnderstandingLedgerCorruptionError",
    "UnderstandingLedgerError",
    "UnderstandingLedgerHeadV1",
    "UnderstandingLedgerOverflowError",
    "build_understanding_event",
    "validate_event_payload",
    "validate_understanding_event_payload",
    "verify_chain",
    "verify_understanding_event_chain",
]
