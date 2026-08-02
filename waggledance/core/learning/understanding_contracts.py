# SPDX-License-Identifier: Apache-2.0
"""Pure contracts for the Open-World Understanding V1 shadow slice.

The module deliberately carries no router, action, promotion, network, Dream,
LLM, or builder authority.  It defines the immutable records shared by the
runtime, MAGMA ledger, and offline Waggle Decision Protocol.

The important split is structural:

* an observation commitment may be prepared before a predictor sees the
  observation value;
* a prediction commitment binds the pre-update model generation and digest;
* local learning is explicitly provisional and reversible;
* hive knowledge remains a proposal until an independently-derived dance
  receipt says otherwise.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Mapping, Optional

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


CANONICALIZATION_VERSION = "magma-jcs-subset-v1"
OBSERVATION_SCHEMA = "wd.observation_envelope.v1"
COMMITMENT_SCHEMA = "wd.observation_commitment.v1"
PREDICTION_SCHEMA = "wd.prediction_commitment.v1"
DISPOSITION_SCHEMA = "wd.understanding_disposition.v1"
KNOWLEDGE_DELTA_SCHEMA = "wd.knowledge_delta.v1"
INDEPENDENCE_SCHEMA = "wd.independence_profile.v1"
DANCE_SIGNAL_SCHEMA = "wd.dance_signal.v1"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_MAX_SEQUENCE = (1 << 63) - 1
_MAX_AXIAL = 1_000_000


class UnderstandingContractError(ValueError):
    """A value is outside the Open-World Understanding V1 contract."""


class PrivacyClass(str, Enum):
    SYNTHETIC = "synthetic"
    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class PredictionStatus(str, Enum):
    PREDICTED = "predicted"
    COLD_START = "cold_start"


class UnderstandingDisposition(str, Enum):
    EXPLAINED = "explained"
    PARTIALLY_EXPLAINED = "partially_explained"
    COLD_START = "cold_start"
    UNKNOWN = "unknown"
    CONTRADICTORY = "contradictory"
    DUPLICATE = "duplicate"
    SAMPLED_OUT = "sampled_out"
    DROPPED_BUDGET = "dropped_budget"
    SCHEMA_INVALID = "schema_invalid"
    PRIVACY_BLOCKED = "privacy_blocked"
    EXPIRED = "expired"


class KnowledgeClaimKind(str, Enum):
    FACT_CANDIDATE = "fact_candidate"
    HYPOTHESIS = "hypothesis"
    MODEL_UPDATE = "model_update"
    SOURCE_RELIABILITY = "source_reliability"
    COUNTEREXAMPLE = "counterexample"
    CAPABILITY_GAP = "capability_gap"


class CuriosityAction(str, Enum):
    MAGMA_LOOKUP = "magma_lookup"
    DETERMINISTIC_REPLAY = "deterministic_replay"


class DanceSignalKind(str, Enum):
    SUPPORT = "support"
    CHALLENGE = "challenge"
    STOP = "stop"


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise UnderstandingContractError(f"{label} must be a bounded token")
    return value


def derive_target_key(entity_id: str, metric: str) -> str:
    """Return a stable bounded key for one observed entity/metric pair.

    The key is always content-addressed.  Human-readable delimiter joins are
    ambiguous (``a.b`` + ``c`` collides with ``a`` + ``b.c``) even when they
    fit the wire limit, so V1 never uses them as state identity.
    """

    entity = _require_token(entity_id, "entity_id")
    metric_name = _require_token(metric, "metric")
    digest = sha256_digest(
        {
            "domain": "wd.understanding_target_key.v1",
            "entity_id": entity,
            "metric": metric_name,
        }
    )
    return f"target-{digest.removeprefix('sha256:')}"


def derive_source_key(source: str, entity_id: str, metric: str) -> str:
    """Return the stable bounded source/target sequence namespace."""

    source_name = _require_token(source, "source")
    entity = _require_token(entity_id, "entity_id")
    metric_name = _require_token(metric, "metric")
    digest = sha256_digest(
        {
            "domain": "wd.understanding_source_key.v1",
            "source": source_name,
            "entity_id": entity,
            "metric": metric_name,
        }
    )
    return f"source-{digest.removeprefix('sha256:')}"


def derive_learning_domain_digest(
    *,
    cell: Mapping[str, object],
    source: str,
    metric: str,
    unit: str,
    learning_policy_digest: str,
    predictor_artifact_digest: str,
    predictor_config_digest: str,
    residual_abs_threshold: float,
    prediction_ttl_seconds: int,
    state_update_alpha: float,
) -> str:
    """Bind one ledger to an exact cell, modality, predictor, and updater.

    ``learning_policy_digest`` is an opaque commitment to the complete local
    policy, including the allowed entity namespace and numeric bounds.  The
    remaining fields are repeated explicitly so ledger validation can reject
    a digest copied onto another cell or observation modality.
    """

    return sha256_digest(
        {
            "domain": "wd.understanding_learning_domain.v1",
            "cell": dict(cell),
            "source": source,
            "metric": metric,
            "unit": unit,
            "learning_policy_digest": learning_policy_digest,
            "predictor_artifact_digest": predictor_artifact_digest,
            "predictor_config_digest": predictor_config_digest,
            "residual_abs_threshold": residual_abs_threshold,
            "prediction_ttl_seconds": prediction_ttl_seconds,
            "state_update_algorithm": "wd.last_value_ewma.v1",
            "state_update_alpha": state_update_alpha,
        }
    )


def _require_digest(value: object, label: str, *, hmac_ok: bool = False) -> str:
    valid = type(value) is str and bool(_SHA256.fullmatch(value))
    if hmac_ok:
        valid = valid or (type(value) is str and bool(_HMAC_SHA256.fullmatch(value)))
    if not valid:
        suffix = "sha256 or hmac-sha256" if hmac_ok else "sha256"
        raise UnderstandingContractError(f"{label} must be a canonical {suffix} digest")
    return value


def _require_int(value: object, label: str, *, minimum: int = 0, maximum: int = _MAX_SEQUENCE) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise UnderstandingContractError(
            f"{label} must be an integer within {minimum}..{maximum}"
        )
    return value


def _require_finite(value: object, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise UnderstandingContractError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise UnderstandingContractError(f"{label} must be finite")
    return number


def _require_probability(value: object, label: str) -> float:
    number = _require_finite(value, label)
    if not 0.0 <= number <= 1.0:
        raise UnderstandingContractError(f"{label} must be within 0..1")
    return number


def _require_utc(value: object, label: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise UnderstandingContractError(f"{label} must be an ISO-8601 UTC instant")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise UnderstandingContractError(
            f"{label} must be a real ISO-8601 UTC instant"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise UnderstandingContractError(f"{label} must use the Z timezone")
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise UnderstandingContractError(f"{label} must use one canonical spelling")
    return value


def _utc_instant(value: str) -> datetime:
    _require_utc(value, "timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _require_reason_codes(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > 16:
        raise UnderstandingContractError("reason_codes must be a bounded tuple")
    result = tuple(_require_token(item, "reason_code") for item in value)
    if len(set(result)) != len(result):
        raise UnderstandingContractError("reason_codes must be unique")
    return result


@dataclass(frozen=True)
class HexCellAddressV1:
    """Logical axial address plus generation fence for one rebuildable cell."""

    cell_id: str
    q: int
    r: int
    incarnation_id: str
    generation: int
    fence: int

    def __post_init__(self) -> None:
        _require_token(self.cell_id, "cell_id")
        _require_int(self.q, "q", minimum=-_MAX_AXIAL, maximum=_MAX_AXIAL)
        _require_int(self.r, "r", minimum=-_MAX_AXIAL, maximum=_MAX_AXIAL)
        _require_token(self.incarnation_id, "incarnation_id")
        _require_int(self.generation, "generation")
        _require_int(self.fence, "fence")

    def to_mapping(self) -> dict[str, object]:
        return {
            "cell_id": self.cell_id,
            "q": self.q,
            "r": self.r,
            "incarnation_id": self.incarnation_id,
            "generation": self.generation,
            "fence": self.fence,
        }


@dataclass(frozen=True)
class ObservationEnvelopeV1:
    observation_id: str
    cell: HexCellAddressV1
    ingest_seq: int
    source_seq: int
    source: str
    entity_id: str
    metric: str
    unit: str
    value: float
    observed_at_utc: str
    quality: float
    privacy_class: PrivacyClass
    metadata_digest: str
    schema_version: str = OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        if type(self.cell) is not HexCellAddressV1:
            raise UnderstandingContractError("cell must be HexCellAddressV1")
        for value, label in (
            (self.observation_id, "observation_id"),
            (self.source, "source"),
            (self.entity_id, "entity_id"),
            (self.metric, "metric"),
            (self.unit, "unit"),
        ):
            _require_token(value, label)
        _require_int(self.ingest_seq, "ingest_seq")
        _require_int(self.source_seq, "source_seq")
        _require_finite(self.value, "value")
        _require_utc(self.observed_at_utc, "observed_at_utc")
        _require_probability(self.quality, "quality")
        if type(self.privacy_class) is not PrivacyClass:
            raise UnderstandingContractError("privacy_class must be PrivacyClass")
        _require_digest(self.metadata_digest, "metadata_digest")
        if self.schema_version != OBSERVATION_SCHEMA:
            raise UnderstandingContractError("observation schema_version refused")

    def header_mapping(self) -> dict[str, object]:
        """Prediction-visible fields.  The actual value is intentionally absent."""

        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "cell": self.cell.to_mapping(),
            "ingest_seq": self.ingest_seq,
            "source_seq": self.source_seq,
            "source": self.source,
            "entity_id": self.entity_id,
            "metric": self.metric,
            "unit": self.unit,
            "observed_at_utc": self.observed_at_utc,
            "quality": float(self.quality),
            "privacy_class": self.privacy_class.value,
            "metadata_digest": self.metadata_digest,
        }

    def commitment_payload(self) -> dict[str, object]:
        return {**self.header_mapping(), "value": float(self.value)}


@dataclass(frozen=True)
class ObservationCommitmentV1:
    commitment_digest: str
    scheme: str
    privacy_domain: str
    nonce: str = ""
    key_id: str = ""
    key_epoch: str = ""
    canonicalization_version: str = CANONICALIZATION_VERSION
    schema_version: str = COMMITMENT_SCHEMA

    def __post_init__(self) -> None:
        _require_digest(self.commitment_digest, "commitment_digest", hmac_ok=True)
        if self.scheme not in ("sha256", "hmac-sha256"):
            raise UnderstandingContractError("commitment scheme refused")
        _require_token(self.privacy_domain, "privacy_domain")
        if self.scheme == "sha256":
            if type(self.nonce) is not str or not _NONCE.fullmatch(self.nonce):
                raise UnderstandingContractError(
                    "public commitment requires a secret-until-reveal nonce"
                )
            if self.key_id or self.key_epoch:
                raise UnderstandingContractError("public commitment cannot carry key metadata")
            if not _SHA256.fullmatch(self.commitment_digest):
                raise UnderstandingContractError("public commitment digest scheme mismatch")
        else:
            if type(self.nonce) is not str or not _NONCE.fullmatch(self.nonce):
                raise UnderstandingContractError("HMAC nonce must be 16-byte lowercase hex")
            _require_token(self.key_id, "key_id")
            try:
                date.fromisoformat(self.key_epoch)
            except (TypeError, ValueError) as exc:
                raise UnderstandingContractError("key_epoch must be YYYY-MM-DD") from exc
            if not _HMAC_SHA256.fullmatch(self.commitment_digest):
                raise UnderstandingContractError("HMAC commitment digest scheme mismatch")
        if self.canonicalization_version != CANONICALIZATION_VERSION:
            raise UnderstandingContractError("canonicalization version refused")
        if self.schema_version != COMMITMENT_SCHEMA:
            raise UnderstandingContractError("commitment schema_version refused")

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "commitment_digest": self.commitment_digest,
            "scheme": self.scheme,
            "privacy_domain": self.privacy_domain,
            "nonce": self.nonce,
            "key_id": self.key_id,
            "key_epoch": self.key_epoch,
            "canonicalization_version": self.canonicalization_version,
        }


def build_observation_commitment(
    envelope: ObservationEnvelopeV1,
    *,
    hmac_key: Optional[bytes] = None,
    key_id: str = "",
    key_epoch: str = "",
    nonce: Optional[str] = None,
) -> ObservationCommitmentV1:
    if type(envelope) is not ObservationEnvelopeV1:
        raise UnderstandingContractError("envelope must be ObservationEnvelopeV1")
    domain = f"wd.observation.{envelope.privacy_class.value}.v1"
    core = {
        "domain": domain,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "observation": envelope.commitment_payload(),
    }
    if envelope.privacy_class in (PrivacyClass.SYNTHETIC, PrivacyClass.PUBLIC):
        if hmac_key is not None or key_id or key_epoch:
            raise UnderstandingContractError("public commitment refuses key material")
        actual_nonce = secrets.token_hex(16) if nonce is None else nonce
        if type(actual_nonce) is not str or not _NONCE.fullmatch(actual_nonce):
            raise UnderstandingContractError("nonce must be 16-byte lowercase hex")
        return ObservationCommitmentV1(
            commitment_digest=sha256_digest({**core, "nonce": actual_nonce}),
            scheme="sha256",
            privacy_domain=domain,
            nonce=actual_nonce,
        )

    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise UnderstandingContractError("private commitment requires a 32-byte HMAC key")
    _require_token(key_id, "key_id")
    try:
        date.fromisoformat(key_epoch)
    except (TypeError, ValueError) as exc:
        raise UnderstandingContractError("key_epoch must be YYYY-MM-DD") from exc
    actual_nonce = secrets.token_hex(16) if nonce is None else nonce
    if type(actual_nonce) is not str or not _NONCE.fullmatch(actual_nonce):
        raise UnderstandingContractError("nonce must be 16-byte lowercase hex")
    signed = {
        **core,
        "nonce": actual_nonce,
        "key_id": key_id,
        "key_epoch": key_epoch,
    }
    digest = hmac.new(hmac_key, canonical_json_bytes(signed), hashlib.sha256).hexdigest()
    return ObservationCommitmentV1(
        commitment_digest=f"hmac-sha256:{digest}",
        scheme="hmac-sha256",
        privacy_domain=domain,
        nonce=actual_nonce,
        key_id=key_id,
        key_epoch=key_epoch,
    )


@dataclass(frozen=True)
class PredictionCommitmentV1:
    observation_commitment_digest: str
    ingest_seq: int
    prior_state_generation: int
    prior_state_digest: str
    predictor_artifact_digest: str
    predictor_config_digest: str
    status: PredictionStatus
    predicted_value: Optional[float]
    committed_at_utc: str
    schema_version: str = PREDICTION_SCHEMA

    def __post_init__(self) -> None:
        _require_digest(
            self.observation_commitment_digest,
            "observation_commitment_digest",
            hmac_ok=True,
        )
        _require_int(self.ingest_seq, "ingest_seq")
        _require_int(self.prior_state_generation, "prior_state_generation")
        _require_digest(self.prior_state_digest, "prior_state_digest")
        _require_digest(self.predictor_artifact_digest, "predictor_artifact_digest")
        _require_digest(self.predictor_config_digest, "predictor_config_digest")
        if type(self.status) is not PredictionStatus:
            raise UnderstandingContractError("prediction status refused")
        if self.status is PredictionStatus.PREDICTED:
            _require_finite(self.predicted_value, "predicted_value")
        elif self.predicted_value is not None:
            raise UnderstandingContractError("cold_start cannot carry predicted_value")
        _require_utc(self.committed_at_utc, "committed_at_utc")
        if self.schema_version != PREDICTION_SCHEMA:
            raise UnderstandingContractError("prediction schema_version refused")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "observation_commitment_digest": self.observation_commitment_digest,
            "ingest_seq": self.ingest_seq,
            "prior_state_generation": self.prior_state_generation,
            "prior_state_digest": self.prior_state_digest,
            "predictor_artifact_digest": self.predictor_artifact_digest,
            "predictor_config_digest": self.predictor_config_digest,
            "status": self.status.value,
            "predicted_value": (
                None if self.predicted_value is None else float(self.predicted_value)
            ),
            "committed_at_utc": self.committed_at_utc,
        }

    @property
    def prediction_digest(self) -> str:
        return sha256_digest({"domain": "wd.prediction_commitment.digest.v1", **self.to_mapping()})


@dataclass(frozen=True)
class UnderstandingDispositionV1:
    observation_commitment_digest: str
    prediction_digest: str
    disposition: UnderstandingDisposition
    residual: Optional[float]
    reason_codes: tuple[str, ...]
    recorded_at_utc: str
    schema_version: str = DISPOSITION_SCHEMA

    def __post_init__(self) -> None:
        _require_digest(
            self.observation_commitment_digest,
            "observation_commitment_digest",
            hmac_ok=True,
        )
        _require_digest(self.prediction_digest, "prediction_digest")
        if type(self.disposition) is not UnderstandingDisposition:
            raise UnderstandingContractError("disposition refused")
        if self.residual is not None:
            _require_finite(self.residual, "residual")
        _require_reason_codes(self.reason_codes)
        _require_utc(self.recorded_at_utc, "recorded_at_utc")
        if self.schema_version != DISPOSITION_SCHEMA:
            raise UnderstandingContractError("disposition schema_version refused")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "observation_commitment_digest": self.observation_commitment_digest,
            "prediction_digest": self.prediction_digest,
            "disposition": self.disposition.value,
            "residual": None if self.residual is None else float(self.residual),
            "reason_codes": list(self.reason_codes),
            "recorded_at_utc": self.recorded_at_utc,
        }


@dataclass(frozen=True)
class LocalProvisionalUpdateV1:
    update_id: str
    cell_id: str
    prediction_digest: str
    prior_state_digest: str
    new_state_digest: str
    applied_at_utc: str
    reversible: bool = True
    runtime_authority_applied: bool = False
    routing_influence_applied: bool = False

    def __post_init__(self) -> None:
        _require_token(self.update_id, "update_id")
        _require_token(self.cell_id, "cell_id")
        for value, label in (
            (self.prediction_digest, "prediction_digest"),
            (self.prior_state_digest, "prior_state_digest"),
            (self.new_state_digest, "new_state_digest"),
        ):
            _require_digest(value, label)
        _require_utc(self.applied_at_utc, "applied_at_utc")
        if self.reversible is not True:
            raise UnderstandingContractError("local update must remain reversible")
        if self.runtime_authority_applied is not False:
            raise UnderstandingContractError("local update cannot apply runtime authority")
        if self.routing_influence_applied is not False:
            raise UnderstandingContractError("local update cannot influence routing")

    def to_mapping(self) -> dict[str, object]:
        """Serialize authority fields as constants across the audit boundary."""
        if self.reversible is not True:
            raise UnderstandingContractError(
                "local update must remain reversible"
            )
        if self.runtime_authority_applied is not False:
            raise UnderstandingContractError(
                "local update cannot apply runtime authority"
            )
        if self.routing_influence_applied is not False:
            raise UnderstandingContractError(
                "local update cannot influence routing"
            )
        return {
            "update_id": self.update_id,
            "cell_id": self.cell_id,
            "prediction_digest": self.prediction_digest,
            "prior_state_digest": self.prior_state_digest,
            "new_state_digest": self.new_state_digest,
            "applied_at_utc": self.applied_at_utc,
            "reversible": True,
            "runtime_authority_applied": False,
            "routing_influence_applied": False,
        }

    @property
    def update_digest(self) -> str:
        return sha256_digest(
            {"domain": "wd.local_provisional_update.digest.v1", **self.to_mapping()}
        )


@dataclass(frozen=True)
class CuriosityItemV1:
    curiosity_id: str
    cell_id: str
    action: CuriosityAction
    evidence_refs: tuple[str, ...]
    expected_value: float
    cost: float
    risk: float
    created_at_utc: str

    def __post_init__(self) -> None:
        _require_token(self.curiosity_id, "curiosity_id")
        _require_token(self.cell_id, "cell_id")
        if type(self.action) is not CuriosityAction:
            raise UnderstandingContractError("curiosity action refused")
        _require_digest_tuple(self.evidence_refs, "evidence_refs", minimum=1, maximum=32)
        _require_probability(self.expected_value, "expected_value")
        _require_probability(self.cost, "cost")
        _require_probability(self.risk, "risk")
        _require_utc(self.created_at_utc, "created_at_utc")


def _require_digest_tuple(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if type(value) is not tuple or not minimum <= len(value) <= maximum:
        raise UnderstandingContractError(
            f"{label} must be a tuple with {minimum}..{maximum} entries"
        )
    result = tuple(_require_digest(item, label, hmac_ok=True) for item in value)
    if len(set(result)) != len(result):
        raise UnderstandingContractError(f"{label} must be unique")
    return result


@dataclass(frozen=True)
class KnowledgeDeltaV1:
    proposal_id: str
    proposer_cell_id: str
    claim_kind: KnowledgeClaimKind
    aggregate_digest: str
    evidence_refs: tuple[str, ...]
    confidence: float
    privacy_class: PrivacyClass
    created_at_utc: str
    expires_at_utc: str
    runtime_authority_applied: bool = False
    routing_influence_applied: bool = False
    schema_version: str = KNOWLEDGE_DELTA_SCHEMA

    def __post_init__(self) -> None:
        _require_token(self.proposal_id, "proposal_id")
        _require_token(self.proposer_cell_id, "proposer_cell_id")
        if type(self.claim_kind) is not KnowledgeClaimKind:
            raise UnderstandingContractError("knowledge claim kind refused")
        _require_digest(self.aggregate_digest, "aggregate_digest")
        _require_digest_tuple(self.evidence_refs, "evidence_refs", minimum=5, maximum=256)
        _require_probability(self.confidence, "confidence")
        if self.privacy_class not in (PrivacyClass.SYNTHETIC, PrivacyClass.PUBLIC):
            raise UnderstandingContractError("private knowledge cannot enter hive WDP v1")
        created = _utc_instant(self.created_at_utc)
        expires = _utc_instant(self.expires_at_utc)
        if expires <= created:
            raise UnderstandingContractError("knowledge delta expiry must follow creation")
        if self.runtime_authority_applied is not False:
            raise UnderstandingContractError("knowledge delta cannot apply runtime authority")
        if self.routing_influence_applied is not False:
            raise UnderstandingContractError("knowledge delta cannot influence routing")
        if self.schema_version != KNOWLEDGE_DELTA_SCHEMA:
            raise UnderstandingContractError("knowledge delta schema_version refused")

    def to_mapping(self) -> dict[str, object]:
        if self.runtime_authority_applied is not False:
            raise UnderstandingContractError(
                "knowledge delta cannot apply runtime authority"
            )
        if self.routing_influence_applied is not False:
            raise UnderstandingContractError(
                "knowledge delta cannot influence routing"
            )
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "proposer_cell_id": self.proposer_cell_id,
            "claim_kind": self.claim_kind.value,
            "aggregate_digest": self.aggregate_digest,
            "evidence_refs": list(self.evidence_refs),
            "confidence": float(self.confidence),
            "privacy_class": self.privacy_class.value,
            "created_at_utc": self.created_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "runtime_authority_applied": False,
            "routing_influence_applied": False,
        }

    @property
    def proposal_digest(self) -> str:
        return sha256_digest({"domain": "wd.knowledge_delta.digest.v1", **self.to_mapping()})


@dataclass(frozen=True)
class CapabilityGapCandidateV1:
    gap_id: str
    proposer_cell_id: str
    evidence_refs: tuple[str, ...]
    created_at_utc: str
    solver_build_eligible: bool = False
    builder_invoked: bool = False

    def __post_init__(self) -> None:
        _require_token(self.gap_id, "gap_id")
        _require_token(self.proposer_cell_id, "proposer_cell_id")
        _require_digest_tuple(self.evidence_refs, "evidence_refs", minimum=1, maximum=256)
        _require_utc(self.created_at_utc, "created_at_utc")
        if self.solver_build_eligible is not False or self.builder_invoked is not False:
            raise UnderstandingContractError("V1 capability gaps cannot invoke the builder")


@dataclass(frozen=True)
class IndependenceProfileV1:
    reviewer_cell_id: str
    identity_incarnation: str
    genesis_root_digest: str
    verifier_code_family: str
    model_provider_lineage: str
    prompt_policy_lineage: str
    evidence_root_lineage: str
    toolchain_image_lineage: str
    physical_failure_domain: str
    schema_version: str = INDEPENDENCE_SCHEMA

    def __post_init__(self) -> None:
        for value, label in (
            (self.reviewer_cell_id, "reviewer_cell_id"),
            (self.identity_incarnation, "identity_incarnation"),
            (self.verifier_code_family, "verifier_code_family"),
            (self.model_provider_lineage, "model_provider_lineage"),
            (self.prompt_policy_lineage, "prompt_policy_lineage"),
            (self.evidence_root_lineage, "evidence_root_lineage"),
            (self.toolchain_image_lineage, "toolchain_image_lineage"),
            (self.physical_failure_domain, "physical_failure_domain"),
        ):
            _require_token(value, label)
        _require_digest(self.genesis_root_digest, "genesis_root_digest")
        if self.schema_version != INDEPENDENCE_SCHEMA:
            raise UnderstandingContractError("independence schema_version refused")

    @property
    def method_group_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.independence.method_group.v1",
                "verifier_code_family": self.verifier_code_family,
                "model_provider_lineage": self.model_provider_lineage,
                "prompt_policy_lineage": self.prompt_policy_lineage,
                "toolchain_image_lineage": self.toolchain_image_lineage,
            }
        )

    @property
    def evidence_group_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.independence.evidence_group.v1",
                "evidence_root_lineage": self.evidence_root_lineage,
            }
        )

    @property
    def lineage_group_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.independence.genesis_lineage_group.v1",
                "genesis_root_digest": self.genesis_root_digest,
            }
        )

    @property
    def profile_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.independence.profile.v1",
                "schema_version": self.schema_version,
                "reviewer_cell_id": self.reviewer_cell_id,
                "identity_incarnation": self.identity_incarnation,
                "lineage_group_digest": self.lineage_group_digest,
                "method_group_digest": self.method_group_digest,
                "evidence_group_digest": self.evidence_group_digest,
                "physical_failure_domain": self.physical_failure_domain,
            }
        )


@dataclass(frozen=True)
class DanceSignalV1:
    proposal_digest: str
    signal_kind: DanceSignalKind
    reviewer: IndependenceProfileV1
    evidence_digest: str
    created_at_utc: str
    expires_at_utc: str
    supersedes_signal_digest: Optional[str] = None
    schema_version: str = DANCE_SIGNAL_SCHEMA

    def __post_init__(self) -> None:
        _require_digest(self.proposal_digest, "proposal_digest")
        if type(self.signal_kind) is not DanceSignalKind:
            raise UnderstandingContractError("dance signal kind refused")
        if type(self.reviewer) is not IndependenceProfileV1:
            raise UnderstandingContractError("reviewer must be IndependenceProfileV1")
        _require_digest(self.evidence_digest, "evidence_digest")
        if self.supersedes_signal_digest is not None:
            _require_digest(self.supersedes_signal_digest, "supersedes_signal_digest")
        created = _utc_instant(self.created_at_utc)
        expires = _utc_instant(self.expires_at_utc)
        if expires <= created:
            raise UnderstandingContractError("dance signal expiry must follow creation")
        if self.schema_version != DANCE_SIGNAL_SCHEMA:
            raise UnderstandingContractError("dance signal schema_version refused")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_digest": self.proposal_digest,
            "signal_kind": self.signal_kind.value,
            "reviewer_profile_digest": self.reviewer.profile_digest,
            "reviewer_cell_id": self.reviewer.reviewer_cell_id,
            "reviewer_identity_incarnation": self.reviewer.identity_incarnation,
            "genesis_root_digest": self.reviewer.genesis_root_digest,
            "lineage_group_digest": self.reviewer.lineage_group_digest,
            "method_group_digest": self.reviewer.method_group_digest,
            "evidence_group_digest": self.reviewer.evidence_group_digest,
            "physical_failure_domain": self.reviewer.physical_failure_domain,
            "evidence_digest": self.evidence_digest,
            "created_at_utc": self.created_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "supersedes_signal_digest": self.supersedes_signal_digest,
        }

    @property
    def signal_digest(self) -> str:
        return sha256_digest({"domain": "wd.dance_signal.digest.v1", **self.to_mapping()})


__all__ = [
    "CANONICALIZATION_VERSION",
    "CapabilityGapCandidateV1",
    "CuriosityAction",
    "CuriosityItemV1",
    "DanceSignalKind",
    "DanceSignalV1",
    "HexCellAddressV1",
    "IndependenceProfileV1",
    "KnowledgeClaimKind",
    "KnowledgeDeltaV1",
    "LocalProvisionalUpdateV1",
    "ObservationCommitmentV1",
    "ObservationEnvelopeV1",
    "PredictionCommitmentV1",
    "PredictionStatus",
    "PrivacyClass",
    "UnderstandingContractError",
    "UnderstandingDisposition",
    "UnderstandingDispositionV1",
    "build_observation_commitment",
    "derive_learning_domain_digest",
    "derive_source_key",
    "derive_target_key",
]
