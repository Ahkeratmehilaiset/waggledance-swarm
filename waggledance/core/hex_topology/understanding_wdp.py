# SPDX-License-Identifier: Apache-2.0
"""Authenticated, offline Waggle Decision Protocol for understanding proposals.

The evaluator is deliberately shadow-only.  It performs no transport,
persistence, routing, runtime, builder, or hive-commit work.  Authentication
uses per-identity HMAC-SHA256 keys supplied by an out-of-band resolver.  The
keys are not stored in, or accepted from, any wire artifact.

HMAC is a deliberately narrow V1 trust anchor for a central shadow evaluator.
It is not a replacement for asymmetric signatures once decisions cross hosts
or gain authority.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from waggledance.core.domain.hex_mesh import HexCoord
from waggledance.core.learning.understanding_contracts import (
    DanceSignalKind,
    DanceSignalV1,
    HexCellAddressV1,
    KnowledgeDeltaV1,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


WDP_RECEIPT_SCHEMA = "wd.waggle_decision_receipt.v1"
WDP_TRUST_REGISTRY_SCHEMA = "wd.wdp.trusted_registry.v1"
WDP_PROPOSAL_ENVELOPE_SCHEMA = "wd.wdp.authenticated_proposal.v1"
WDP_SIGNAL_ENVELOPE_SCHEMA = "wd.wdp.authenticated_signal.v1"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUTH_TAG = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")

KeyResolver = Callable[[str, str], bytes]

_MAX_TRUSTED_CELL_RECORDS_V1 = 64
_MAX_PROPOSAL_TTL_SECONDS_V1 = 7 * 24 * 60 * 60
_MAX_SIGNAL_TTL_SECONDS_V1 = 24 * 60 * 60
_MAX_FUTURE_SKEW_SECONDS_V1 = 60 * 60
_MAX_SIGNAL_ENVELOPES_V1 = 4_096
_MAX_REVISION_DEPTH_V1 = 64


class WDPContractError(ValueError):
    """The caller supplied an invalid offline WDP evaluation boundary."""


class WDPDecision(str, Enum):
    """Non-authoritative outcome of one offline WDP evaluation."""

    APPROVED_SHADOW = "approved_shadow"
    STOPPED = "stopped"
    CHALLENGED = "challenged"
    INSUFFICIENT_INDEPENDENCE = "insufficient_independence"
    SILENCE = "silence"
    PROPOSAL_EXPIRED = "proposal_expired"
    PROPOSAL_INVALID = "proposal_invalid"


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise WDPContractError(f"{label} must be a bounded token")
    return value


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise WDPContractError(f"{label} must be a canonical sha256 digest")
    return value


def _require_auth_tag(value: object, label: str) -> str:
    if type(value) is not str or not _AUTH_TAG.fullmatch(value):
        raise WDPContractError(f"{label} must be a canonical HMAC-SHA256 tag")
    return value


def _require_hmac_key(value: object) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise WDPContractError("HMAC keys must be bytes with at least 32 bytes")
    return value


def _auth_tag(payload: dict[str, object], key: bytes) -> str:
    digest = hmac.new(
        _require_hmac_key(key),
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def _resolved_key(
    resolver: KeyResolver,
    *,
    key_id: str,
    key_epoch: str,
) -> bytes | None:
    try:
        value = resolver(key_id, key_epoch)
    except Exception:
        return None
    if type(value) is not bytes or len(value) < 32:
        return None
    return value


def _parse_utc(value: str, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise WDPContractError(f"{label} must be a canonical UTC instant")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise WDPContractError(f"{label} must be a canonical UTC instant") from exc
    if parsed.tzinfo != timezone.utc:
        raise WDPContractError(f"{label} must use the Z timezone")
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise WDPContractError(f"{label} must use one canonical spelling")
    return parsed


@dataclass(frozen=True)
class WDPPolicyV1:
    """Bounded temporal policy; independence thresholds are fixed by V1."""

    max_proposal_ttl_seconds: int = 86_400
    max_signal_ttl_seconds: int = 3_600
    max_future_skew_seconds: int = 30
    max_signal_envelopes: int = 64
    max_revision_depth: int = 16

    def __post_init__(self) -> None:
        bounded_positive = (
            (
                "max_proposal_ttl_seconds",
                self.max_proposal_ttl_seconds,
                _MAX_PROPOSAL_TTL_SECONDS_V1,
            ),
            (
                "max_signal_ttl_seconds",
                self.max_signal_ttl_seconds,
                _MAX_SIGNAL_TTL_SECONDS_V1,
            ),
            (
                "max_signal_envelopes",
                self.max_signal_envelopes,
                _MAX_SIGNAL_ENVELOPES_V1,
            ),
            (
                "max_revision_depth",
                self.max_revision_depth,
                _MAX_REVISION_DEPTH_V1,
            ),
        )
        for label, value, ceiling in bounded_positive:
            if type(value) is not int or not 0 < value <= ceiling:
                raise WDPContractError(
                    f"{label} must be an integer within 1..{ceiling}"
                )
        if (
            type(self.max_future_skew_seconds) is not int
            or not 0
            <= self.max_future_skew_seconds
            <= _MAX_FUTURE_SKEW_SECONDS_V1
        ):
            raise WDPContractError(
                "max_future_skew_seconds must be an integer within "
                f"0..{_MAX_FUTURE_SKEW_SECONDS_V1}"
            )


@dataclass(frozen=True)
class HexRingSnapshotV1:
    """Current fenced view of one cell and its complete axial ring-1."""

    center: HexCellAddressV1
    neighbors: tuple[HexCellAddressV1, ...]

    def __post_init__(self) -> None:
        if type(self.center) is not HexCellAddressV1:
            raise WDPContractError("center must be HexCellAddressV1")
        if type(self.neighbors) is not tuple or len(self.neighbors) != 6:
            raise WDPContractError("ring-1 snapshot must contain exactly six neighbors")
        if any(type(cell) is not HexCellAddressV1 for cell in self.neighbors):
            raise WDPContractError("every neighbor must be HexCellAddressV1")
        cell_ids = {cell.cell_id for cell in self.neighbors}
        if len(cell_ids) != 6 or self.center.cell_id in cell_ids:
            raise WDPContractError("ring-1 cells must have six unique non-self identities")
        actual = {(cell.q, cell.r) for cell in self.neighbors}
        expected = {
            (coord.q, coord.r)
            for coord in HexCoord(self.center.q, self.center.r).neighbors()
        }
        if actual != expected:
            raise WDPContractError("neighbors must occupy all six axial ring-1 coordinates")

    def neighbor_by_id(self) -> dict[str, HexCellAddressV1]:
        return {cell.cell_id: cell for cell in self.neighbors}


@dataclass(frozen=True)
class TrustedCellRecordV1:
    """Out-of-band facts for one exact fenced cell identity."""

    cell: HexCellAddressV1
    auth_key_id: str
    key_epoch: str
    genesis_root_digest: str
    method_group_digest: str
    evidence_group_digest: str
    physical_failure_domain: str

    def __post_init__(self) -> None:
        if type(self.cell) is not HexCellAddressV1:
            raise WDPContractError("trusted cell must be HexCellAddressV1")
        _require_token(self.auth_key_id, "auth_key_id")
        _require_token(self.key_epoch, "key_epoch")
        _require_digest(self.genesis_root_digest, "genesis_root_digest")
        _require_digest(self.method_group_digest, "method_group_digest")
        _require_digest(self.evidence_group_digest, "evidence_group_digest")
        _require_token(self.physical_failure_domain, "physical_failure_domain")

    def to_mapping(self) -> dict[str, object]:
        return {
            "cell": self.cell.to_mapping(),
            "auth_key_id": self.auth_key_id,
            "key_epoch": self.key_epoch,
            "genesis_root_digest": self.genesis_root_digest,
            "method_group_digest": self.method_group_digest,
            "evidence_group_digest": self.evidence_group_digest,
            "physical_failure_domain": self.physical_failure_domain,
        }


def _registry_core(records: tuple[TrustedCellRecordV1, ...]) -> dict[str, object]:
    return {
        "schema_version": WDP_TRUST_REGISTRY_SCHEMA,
        "records": [record.to_mapping() for record in records],
    }


@dataclass(frozen=True)
class TrustedWDPRegistryV1:
    """Immutable digest-bound trust snapshot; key bytes live elsewhere."""

    records: tuple[TrustedCellRecordV1, ...]
    registry_digest: str
    schema_version: str = WDP_TRUST_REGISTRY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != WDP_TRUST_REGISTRY_SCHEMA:
            raise WDPContractError("unknown WDP trust registry schema")
        if (
            type(self.records) is not tuple
            or not 1 <= len(self.records) <= _MAX_TRUSTED_CELL_RECORDS_V1
        ):
            raise WDPContractError("trusted registry requires records")
        if any(type(record) is not TrustedCellRecordV1 for record in self.records):
            raise WDPContractError("trusted registry contains an invalid record")
        identities = tuple(record.cell.cell_id for record in self.records)
        if identities != tuple(sorted(identities)) or len(set(identities)) != len(
            identities
        ):
            raise WDPContractError("trusted records must be sorted and identity-unique")
        key_metadata = tuple(
            (record.auth_key_id, record.key_epoch) for record in self.records
        )
        if len(set(key_metadata)) != len(key_metadata):
            raise WDPContractError(
                "trusted records require distinct authentication key metadata"
            )
        _require_digest(self.registry_digest, "registry_digest")
        expected = sha256_digest(
            {"domain": "wd.wdp.trusted_registry.digest.v1", **_registry_core(self.records)}
        )
        if self.registry_digest != expected:
            raise WDPContractError("trusted registry digest mismatch")

    @classmethod
    def create(
        cls, records: tuple[TrustedCellRecordV1, ...]
    ) -> "TrustedWDPRegistryV1":
        if type(records) is not tuple:
            raise WDPContractError("trusted records must be a tuple")
        ordered = tuple(sorted(records, key=lambda item: item.cell.cell_id))
        return cls(
            records=ordered,
            registry_digest=sha256_digest(
                {
                    "domain": "wd.wdp.trusted_registry.digest.v1",
                    **_registry_core(ordered),
                }
            ),
        )

    def record_for(self, cell_id: str) -> TrustedCellRecordV1 | None:
        return next(
            (record for record in self.records if record.cell.cell_id == cell_id),
            None,
        )

    def to_mapping(self) -> dict[str, object]:
        return {**_registry_core(self.records), "registry_digest": self.registry_digest}


def _proposal_auth_payload(
    *,
    proposal: KnowledgeDeltaV1,
    proposer: HexCellAddressV1,
    registry_digest: str,
    key_id: str,
    key_epoch: str,
) -> dict[str, object]:
    return {
        "domain": "wd.wdp.proposal.auth.v1",
        "schema_version": WDP_PROPOSAL_ENVELOPE_SCHEMA,
        "registry_digest": registry_digest,
        "proposal": proposal.to_mapping(),
        "proposer": proposer.to_mapping(),
        "key_id": key_id,
        "key_epoch": key_epoch,
    }


@dataclass(frozen=True)
class AuthenticatedProposalEnvelopeV1:
    proposal: KnowledgeDeltaV1
    proposer: HexCellAddressV1
    registry_digest: str
    key_id: str
    key_epoch: str
    auth_tag: str
    schema_version: str = WDP_PROPOSAL_ENVELOPE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != WDP_PROPOSAL_ENVELOPE_SCHEMA:
            raise WDPContractError("unknown proposal envelope schema")
        if type(self.proposal) is not KnowledgeDeltaV1:
            raise WDPContractError("proposal must be KnowledgeDeltaV1")
        if type(self.proposer) is not HexCellAddressV1:
            raise WDPContractError("proposer must be HexCellAddressV1")
        if self.proposal.proposer_cell_id != self.proposer.cell_id:
            raise WDPContractError("proposal logical proposer must match its address")
        _require_digest(self.registry_digest, "registry_digest")
        _require_token(self.key_id, "key_id")
        _require_token(self.key_epoch, "key_epoch")
        _require_auth_tag(self.auth_tag, "auth_tag")

    @classmethod
    def create(
        cls,
        *,
        proposal: KnowledgeDeltaV1,
        proposer: HexCellAddressV1,
        registry_digest: str,
        key_id: str,
        key_epoch: str,
        hmac_key: bytes,
    ) -> "AuthenticatedProposalEnvelopeV1":
        payload = _proposal_auth_payload(
            proposal=proposal,
            proposer=proposer,
            registry_digest=registry_digest,
            key_id=key_id,
            key_epoch=key_epoch,
        )
        return cls(
            proposal=proposal,
            proposer=proposer,
            registry_digest=registry_digest,
            key_id=key_id,
            key_epoch=key_epoch,
            auth_tag=_auth_tag(payload, hmac_key),
        )

    @property
    def envelope_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.wdp.proposal.envelope.digest.v1",
                **_proposal_auth_payload(
                    proposal=self.proposal,
                    proposer=self.proposer,
                    registry_digest=self.registry_digest,
                    key_id=self.key_id,
                    key_epoch=self.key_epoch,
                ),
                "auth_tag": self.auth_tag,
            }
        )


def _signal_auth_payload(
    *,
    signal: DanceSignalV1,
    emitter: HexCellAddressV1,
    registry_digest: str,
    key_id: str,
    key_epoch: str,
) -> dict[str, object]:
    return {
        "domain": "wd.wdp.signal.auth.v1",
        "schema_version": WDP_SIGNAL_ENVELOPE_SCHEMA,
        "registry_digest": registry_digest,
        "signal": signal.to_mapping(),
        "emitter": emitter.to_mapping(),
        "key_id": key_id,
        "key_epoch": key_epoch,
    }


@dataclass(frozen=True)
class AuthenticatedDanceSignalEnvelopeV1:
    """A signal MAC-bound to the emitter's complete address and trust snapshot."""

    signal: DanceSignalV1
    emitter: HexCellAddressV1
    registry_digest: str
    key_id: str
    key_epoch: str
    auth_tag: str
    schema_version: str = WDP_SIGNAL_ENVELOPE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != WDP_SIGNAL_ENVELOPE_SCHEMA:
            raise WDPContractError("unknown signal envelope schema")
        if type(self.signal) is not DanceSignalV1:
            raise WDPContractError("signal must be DanceSignalV1")
        if type(self.emitter) is not HexCellAddressV1:
            raise WDPContractError("emitter must be HexCellAddressV1")
        if self.signal.reviewer.reviewer_cell_id != self.emitter.cell_id:
            raise WDPContractError("signal reviewer must match emitter cell")
        if self.signal.reviewer.identity_incarnation != self.emitter.incarnation_id:
            raise WDPContractError("signal reviewer incarnation must match emitter")
        _require_digest(self.registry_digest, "registry_digest")
        _require_token(self.key_id, "key_id")
        _require_token(self.key_epoch, "key_epoch")
        _require_auth_tag(self.auth_tag, "auth_tag")

    @classmethod
    def create(
        cls,
        *,
        signal: DanceSignalV1,
        emitter: HexCellAddressV1,
        registry_digest: str,
        key_id: str,
        key_epoch: str,
        hmac_key: bytes,
    ) -> "AuthenticatedDanceSignalEnvelopeV1":
        payload = _signal_auth_payload(
            signal=signal,
            emitter=emitter,
            registry_digest=registry_digest,
            key_id=key_id,
            key_epoch=key_epoch,
        )
        return cls(
            signal=signal,
            emitter=emitter,
            registry_digest=registry_digest,
            key_id=key_id,
            key_epoch=key_epoch,
            auth_tag=_auth_tag(payload, hmac_key),
        )

    @property
    def envelope_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.wdp.signal.envelope.digest.v1",
                **_signal_auth_payload(
                    signal=self.signal,
                    emitter=self.emitter,
                    registry_digest=self.registry_digest,
                    key_id=self.key_id,
                    key_epoch=self.key_epoch,
                ),
                "auth_tag": self.auth_tag,
            }
        )


# Compatibility spelling: this is authenticated, not a raw envelope.
DanceSignalEnvelopeV1 = AuthenticatedDanceSignalEnvelopeV1


@dataclass(frozen=True)
class RejectedDanceSignalV1:
    signal_digest: str
    reason_code: str
    envelope_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_digest(self.signal_digest, "signal_digest")
        _require_token(self.reason_code, "reason_code")
        if type(self.envelope_digests) is not tuple:
            raise WDPContractError("envelope_digests must be a tuple")
        for digest in self.envelope_digests:
            _require_digest(digest, "envelope_digest")
        if self.envelope_digests != tuple(sorted(set(self.envelope_digests))):
            raise WDPContractError("envelope_digests must be sorted and unique")

    def to_mapping(self) -> dict[str, object]:
        return {
            "signal_digest": self.signal_digest,
            "reason_code": self.reason_code,
            "envelope_digests": list(self.envelope_digests),
        }


@dataclass(frozen=True)
class WDPShadowReceiptV1:
    """Auditable decision output with no authority to apply the result."""

    proposal_digest: str
    trust_registry_digest: str
    decision: WDPDecision
    evaluated_at_utc: str
    reason_codes: tuple[str, ...]
    accepted_signal_digests: tuple[str, ...]
    active_support_signal_digests: tuple[str, ...]
    stop_signal_digests: tuple[str, ...]
    challenge_signal_digests: tuple[str, ...]
    rejected_signals: tuple[RejectedDanceSignalV1, ...]
    distinct_method_group_digests: tuple[str, ...]
    distinct_evidence_group_digests: tuple[str, ...]
    distinct_lineage_group_digests: tuple[str, ...]
    distinct_physical_failure_domains: tuple[str, ...]
    independent_support_pair: tuple[str, ...] = ()
    hive_commit_applied: bool = False
    runtime_authority_applied: bool = False
    routing_influence_applied: bool = False
    schema_version: str = WDP_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        _require_digest(self.proposal_digest, "proposal_digest")
        _require_digest(self.trust_registry_digest, "trust_registry_digest")
        if type(self.decision) is not WDPDecision:
            raise WDPContractError("decision must be WDPDecision")
        _parse_utc(self.evaluated_at_utc, "evaluated_at_utc")
        if self.hive_commit_applied is not False:
            raise WDPContractError("shadow receipt cannot apply a hive commit")
        if self.runtime_authority_applied is not False:
            raise WDPContractError("shadow receipt cannot apply runtime authority")
        if self.routing_influence_applied is not False:
            raise WDPContractError("shadow receipt cannot influence routing")
        if self.schema_version != WDP_RECEIPT_SCHEMA:
            raise WDPContractError("WDP receipt schema refused")
        if self.decision is WDPDecision.APPROVED_SHADOW:
            if (
                len(self.independent_support_pair) != 2
                or not set(self.independent_support_pair).issubset(
                    self.active_support_signal_digests
                )
                or self.stop_signal_digests
                or self.challenge_signal_digests
            ):
                raise WDPContractError("approved shadow receipt lacks a valid support pair")

    @property
    def approved(self) -> bool:
        return self.decision is WDPDecision.APPROVED_SHADOW

    def to_mapping(self) -> dict[str, object]:
        if self.hive_commit_applied is not False:
            raise WDPContractError("shadow receipt cannot apply a hive commit")
        if self.runtime_authority_applied is not False:
            raise WDPContractError(
                "shadow receipt cannot apply runtime authority"
            )
        if self.routing_influence_applied is not False:
            raise WDPContractError("shadow receipt cannot influence routing")
        return {
            "schema_version": self.schema_version,
            "proposal_digest": self.proposal_digest,
            "trust_registry_digest": self.trust_registry_digest,
            "decision": self.decision.value,
            "evaluated_at_utc": self.evaluated_at_utc,
            "reason_codes": list(self.reason_codes),
            "accepted_signal_digests": list(self.accepted_signal_digests),
            "active_support_signal_digests": list(
                self.active_support_signal_digests
            ),
            "stop_signal_digests": list(self.stop_signal_digests),
            "challenge_signal_digests": list(self.challenge_signal_digests),
            "rejected_signals": [item.to_mapping() for item in self.rejected_signals],
            "distinct_method_group_digests": list(
                self.distinct_method_group_digests
            ),
            "distinct_evidence_group_digests": list(
                self.distinct_evidence_group_digests
            ),
            "distinct_lineage_group_digests": list(
                self.distinct_lineage_group_digests
            ),
            "distinct_physical_failure_domains": list(
                self.distinct_physical_failure_domains
            ),
            "independent_support_pair": list(self.independent_support_pair),
            "hive_commit_applied": False,
            "runtime_authority_applied": False,
            "routing_influence_applied": False,
        }

    @property
    def receipt_digest(self) -> str:
        return sha256_digest({"domain": "wd.wdp.receipt.digest.v1", **self.to_mapping()})


def _ring_trust_reason(
    ring: HexRingSnapshotV1, registry: TrustedWDPRegistryV1
) -> str | None:
    cells = (ring.center, *ring.neighbors)
    for cell in cells:
        record = registry.record_for(cell.cell_id)
        if record is None or record.cell != cell:
            return "ring_cell_not_in_trusted_snapshot"
    return None


def _registry_integrity_reason(registry: TrustedWDPRegistryV1) -> str | None:
    try:
        expected = sha256_digest(
            {
                "domain": "wd.wdp.trusted_registry.digest.v1",
                **_registry_core(registry.records),
            }
        )
    except Exception:
        return "trusted_registry_snapshot_invalid"
    if not hmac.compare_digest(registry.registry_digest, expected):
        return "trusted_registry_snapshot_invalid"
    return None


def _resolved_trusted_key_snapshot(
    registry: TrustedWDPRegistryV1,
    key_resolver: KeyResolver,
) -> tuple[dict[tuple[str, str], bytes], str | None]:
    """Resolve each trusted principal once and reject aliased key material."""

    resolved: dict[tuple[str, str], bytes] = {}
    material_owners: dict[bytes, tuple[str, str]] = {}
    for record in registry.records:
        metadata = (record.auth_key_id, record.key_epoch)
        if metadata in resolved:
            return {}, "trusted_key_metadata_not_independent"
        key = _resolved_key(
            key_resolver,
            key_id=record.auth_key_id,
            key_epoch=record.key_epoch,
        )
        if key is None:
            return {}, "trusted_auth_key_unavailable"
        material_fingerprint = hashlib.sha256(key).digest()
        if material_fingerprint in material_owners:
            return {}, "trusted_key_material_not_independent"
        material_owners[material_fingerprint] = metadata
        resolved[metadata] = bytes(key)
    return resolved, None


def _proposal_auth_reason(
    envelope: AuthenticatedProposalEnvelopeV1,
    *,
    ring: HexRingSnapshotV1,
    registry: TrustedWDPRegistryV1,
    trusted_keys: dict[tuple[str, str], bytes],
) -> str | None:
    if envelope.registry_digest != registry.registry_digest:
        return "proposal_registry_mismatch"
    if envelope.proposer != ring.center:
        return "stale_or_foreign_proposer_fence"
    record = registry.record_for(ring.center.cell_id)
    if record is None or record.cell != envelope.proposer:
        return "proposer_not_trusted"
    if envelope.key_id != record.auth_key_id or envelope.key_epoch != record.key_epoch:
        return "proposal_key_metadata_mismatch"
    key = trusted_keys.get((envelope.key_id, envelope.key_epoch))
    if key is None:
        return "proposal_auth_key_unavailable"
    expected = _auth_tag(
        _proposal_auth_payload(
            proposal=envelope.proposal,
            proposer=envelope.proposer,
            registry_digest=envelope.registry_digest,
            key_id=envelope.key_id,
            key_epoch=envelope.key_epoch,
        ),
        key,
    )
    if not hmac.compare_digest(envelope.auth_tag, expected):
        return "proposal_authentication_failed"
    return None


def _signal_rejection_reason(
    envelope: AuthenticatedDanceSignalEnvelopeV1,
    *,
    proposal: KnowledgeDeltaV1,
    ring: HexRingSnapshotV1,
    registry: TrustedWDPRegistryV1,
    trusted_keys: dict[tuple[str, str], bytes],
    now: datetime,
    policy: WDPPolicyV1,
) -> str | None:
    signal = envelope.signal
    if envelope.registry_digest != registry.registry_digest:
        return "signal_registry_mismatch"
    if signal.proposal_digest != proposal.proposal_digest:
        return "proposal_digest_mismatch"
    if signal.reviewer.reviewer_cell_id == ring.center.cell_id:
        return "self_vote"
    current = ring.neighbor_by_id().get(signal.reviewer.reviewer_cell_id)
    if current is None:
        return "reviewer_not_ring1_neighbor"
    if envelope.emitter != current:
        return "stale_incarnation_generation_or_fence"
    record = registry.record_for(current.cell_id)
    if record is None or record.cell != current:
        return "reviewer_not_trusted"
    if envelope.key_id != record.auth_key_id or envelope.key_epoch != record.key_epoch:
        return "signal_key_metadata_mismatch"
    key = trusted_keys.get((envelope.key_id, envelope.key_epoch))
    if key is None:
        return "signal_auth_key_unavailable"
    expected = _auth_tag(
        _signal_auth_payload(
            signal=signal,
            emitter=envelope.emitter,
            registry_digest=envelope.registry_digest,
            key_id=envelope.key_id,
            key_epoch=envelope.key_epoch,
        ),
        key,
    )
    if not hmac.compare_digest(envelope.auth_tag, expected):
        return "signal_authentication_failed"
    profile = signal.reviewer
    if profile.genesis_root_digest != record.genesis_root_digest:
        return "untrusted_genesis_claim"
    if profile.method_group_digest != record.method_group_digest:
        return "untrusted_method_claim"
    if profile.evidence_group_digest != record.evidence_group_digest:
        return "untrusted_evidence_claim"
    if profile.physical_failure_domain != record.physical_failure_domain:
        return "untrusted_failure_domain_claim"
    proposal_created = _parse_utc(proposal.created_at_utc, "proposal.created_at_utc")
    proposal_expires = _parse_utc(proposal.expires_at_utc, "proposal.expires_at_utc")
    created = _parse_utc(signal.created_at_utc, "signal.created_at_utc")
    expires = _parse_utc(signal.expires_at_utc, "signal.expires_at_utc")
    skew = timedelta(seconds=policy.max_future_skew_seconds)
    if created < proposal_created:
        return "signal_predates_proposal"
    if created > now + skew:
        return "signal_from_future"
    if expires <= now:
        return "signal_expired"
    if expires > proposal_expires:
        return "signal_outlives_proposal"
    if expires - created > timedelta(seconds=policy.max_signal_ttl_seconds):
        return "signal_ttl_exceeded"
    return None


def _independent_pair(
    support: tuple[AuthenticatedDanceSignalEnvelopeV1, ...],
) -> tuple[str, ...]:
    """Return the canonical first pair independent on all four V1 axes."""

    ordered = sorted(support, key=lambda item: item.signal.signal_digest)
    for index, left in enumerate(ordered):
        left_profile = left.signal.reviewer
        for right in ordered[index + 1 :]:
            right_profile = right.signal.reviewer
            if (
                left_profile.method_group_digest
                != right_profile.method_group_digest
                and left_profile.lineage_group_digest
                != right_profile.lineage_group_digest
                and left_profile.evidence_group_digest
                != right_profile.evidence_group_digest
                and left_profile.physical_failure_domain
                != right_profile.physical_failure_domain
            ):
                return (left.signal.signal_digest, right.signal.signal_digest)
    return ()


def _receipt(
    *,
    proposal_digest: str,
    registry_digest: str,
    decision: WDPDecision,
    now_utc: str,
    reasons: set[str],
    accepted: tuple[AuthenticatedDanceSignalEnvelopeV1, ...] = (),
    active_support: tuple[AuthenticatedDanceSignalEnvelopeV1, ...] = (),
    stopped: tuple[AuthenticatedDanceSignalEnvelopeV1, ...] = (),
    challenged: tuple[AuthenticatedDanceSignalEnvelopeV1, ...] = (),
    rejected: tuple[RejectedDanceSignalV1, ...] = (),
    independent_pair: tuple[str, ...] = (),
) -> WDPShadowReceiptV1:
    profiles = [item.signal.reviewer for item in active_support]
    return WDPShadowReceiptV1(
        proposal_digest=proposal_digest,
        trust_registry_digest=registry_digest,
        decision=decision,
        evaluated_at_utc=now_utc,
        reason_codes=tuple(sorted(reasons)),
        accepted_signal_digests=tuple(
            sorted(item.signal.signal_digest for item in accepted)
        ),
        active_support_signal_digests=tuple(
            sorted(item.signal.signal_digest for item in active_support)
        ),
        stop_signal_digests=tuple(
            sorted(item.signal.signal_digest for item in stopped)
        ),
        challenge_signal_digests=tuple(
            sorted(item.signal.signal_digest for item in challenged)
        ),
        rejected_signals=tuple(
            sorted(
                rejected,
                key=lambda item: (
                    item.signal_digest,
                    item.reason_code,
                    item.envelope_digests,
                ),
            )
        ),
        distinct_method_group_digests=tuple(
            sorted({profile.method_group_digest for profile in profiles})
        ),
        distinct_evidence_group_digests=tuple(
            sorted({profile.evidence_group_digest for profile in profiles})
        ),
        distinct_lineage_group_digests=tuple(
            sorted({profile.lineage_group_digest for profile in profiles})
        ),
        distinct_physical_failure_domains=tuple(
            sorted({profile.physical_failure_domain for profile in profiles})
        ),
        independent_support_pair=independent_pair,
        hive_commit_applied=False,
        runtime_authority_applied=False,
        routing_influence_applied=False,
    )


def evaluate_waggle_decision(
    proposal_envelope: AuthenticatedProposalEnvelopeV1,
    ring: HexRingSnapshotV1,
    signals: tuple[AuthenticatedDanceSignalEnvelopeV1, ...],
    *,
    trust_registry: TrustedWDPRegistryV1,
    key_resolver: KeyResolver,
    now_utc: str,
    policy: WDPPolicyV1 = WDPPolicyV1(),
) -> WDPShadowReceiptV1:
    """Authenticate and evaluate a proposal without applying any authority."""

    if type(proposal_envelope) is not AuthenticatedProposalEnvelopeV1:
        raise WDPContractError(
            "proposal_envelope must be AuthenticatedProposalEnvelopeV1"
        )
    if type(ring) is not HexRingSnapshotV1:
        raise WDPContractError("ring must be HexRingSnapshotV1")
    if type(policy) is not WDPPolicyV1:
        raise WDPContractError("policy must be WDPPolicyV1")
    if type(trust_registry) is not TrustedWDPRegistryV1:
        raise WDPContractError("trust_registry must be TrustedWDPRegistryV1")
    if not callable(key_resolver):
        raise WDPContractError("key_resolver must be callable")
    if type(signals) is not tuple:
        raise WDPContractError(
            "signals must be a tuple of AuthenticatedDanceSignalEnvelopeV1"
        )
    if len(signals) > policy.max_signal_envelopes:
        return _receipt(
            proposal_digest=proposal_envelope.proposal.proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={"signal_envelope_limit_exceeded"},
        )
    if any(
        type(item) is not AuthenticatedDanceSignalEnvelopeV1 for item in signals
    ):
        raise WDPContractError(
            "signals must be a tuple of AuthenticatedDanceSignalEnvelopeV1"
        )
    # Detach every caller-owned artifact before the first resolver callback.
    # Frozen dataclasses can still be mutated with object.__setattr__; using
    # only this private snapshot prevents a resolver closure from changing
    # trust, lineage, fencing, proposal, or signal facts after digest checks.
    try:
        proposal_envelope = copy.deepcopy(proposal_envelope)
        ring = copy.deepcopy(ring)
        signals = copy.deepcopy(signals)
        trust_registry = copy.deepcopy(trust_registry)
        policy = copy.deepcopy(policy)
    except Exception as exc:
        raise WDPContractError("WDP inputs could not be safely snapshotted") from exc

    now = _parse_utc(now_utc, "now_utc")
    proposal = proposal_envelope.proposal
    proposal_digest = proposal.proposal_digest
    registry_reason = _registry_integrity_reason(trust_registry)
    if registry_reason is not None:
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={registry_reason},
        )
    ring_reason = _ring_trust_reason(ring, trust_registry)
    if ring_reason is not None:
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={ring_reason},
        )
    trusted_keys, key_snapshot_reason = _resolved_trusted_key_snapshot(
        trust_registry,
        key_resolver,
    )
    if key_snapshot_reason is not None:
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={key_snapshot_reason},
        )
    proposal_reason = _proposal_auth_reason(
        proposal_envelope,
        ring=ring,
        registry=trust_registry,
        trusted_keys=trusted_keys,
    )
    if proposal_reason is not None:
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={proposal_reason},
        )

    proposal_created = _parse_utc(proposal.created_at_utc, "proposal.created_at_utc")
    proposal_expires = _parse_utc(proposal.expires_at_utc, "proposal.expires_at_utc")
    if proposal_created > now + timedelta(seconds=policy.max_future_skew_seconds):
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={"proposal_from_future"},
        )
    if proposal_expires - proposal_created > timedelta(
        seconds=policy.max_proposal_ttl_seconds
    ):
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_INVALID,
            now_utc=now_utc,
            reasons={"proposal_ttl_exceeded"},
        )
    if proposal_expires <= now:
        return _receipt(
            proposal_digest=proposal_digest,
            registry_digest=trust_registry.registry_digest,
            decision=WDPDecision.PROPOSAL_EXPIRED,
            now_utc=now_utc,
            reasons={"proposal_expired"},
        )

    rejected: list[RejectedDanceSignalV1] = []
    authenticated_groups: dict[
        str, list[AuthenticatedDanceSignalEnvelopeV1]
    ] = {}
    for envelope in signals:
        try:
            signal_digest = envelope.signal.signal_digest
            envelope_digest = envelope.envelope_digest
            reason = _signal_rejection_reason(
                envelope,
                proposal=proposal,
                ring=ring,
                registry=trust_registry,
                trusted_keys=trusted_keys,
                now=now,
                policy=policy,
            )
        except Exception:
            # The public constructors reject malformed artifacts.  This guard
            # normalizes post-construction object mutation without exposing a
            # raw implementation exception or letting it erase a valid veto.
            continue
        if reason is not None:
            rejected.append(
                RejectedDanceSignalV1(
                    signal_digest,
                    reason,
                    (envelope_digest,),
                )
            )
            continue
        authenticated_groups.setdefault(signal_digest, []).append(envelope)

    basic_valid: dict[str, AuthenticatedDanceSignalEnvelopeV1] = {}
    for signal_digest in sorted(authenticated_groups):
        group = authenticated_groups[signal_digest]
        variants = {
            envelope.envelope_digest: envelope
            for envelope in sorted(group, key=lambda item: item.envelope_digest)
        }
        variant_digests = tuple(sorted(variants))
        if len(variants) > 1:
            rejected.append(
                RejectedDanceSignalV1(
                    signal_digest,
                    "conflicting_signal_envelopes",
                    variant_digests,
                )
            )
            continue
        envelope = variants[variant_digests[0]]
        if len(group) > 1:
            rejected.append(
                RejectedDanceSignalV1(
                    signal_digest,
                    "duplicate_signal",
                    variant_digests,
                )
            )
        basic_valid[signal_digest] = envelope

    valid = dict(basic_valid)
    changed = True
    while changed:
        changed = False
        for digest, envelope in tuple(valid.items()):
            supersedes = envelope.signal.supersedes_signal_digest
            if supersedes is None:
                continue
            prior = valid.get(supersedes)
            reason: str | None = None
            if prior is None:
                reason = "supersedes_unknown_or_invalid_signal"
            elif (
                prior.signal.reviewer.reviewer_cell_id
                != envelope.signal.reviewer.reviewer_cell_id
            ):
                reason = "cross_reviewer_revision"
            elif _parse_utc(
                envelope.signal.created_at_utc, "signal.created_at_utc"
            ) <= _parse_utc(prior.signal.created_at_utc, "prior.created_at_utc"):
                reason = "non_monotonic_revision"
            if reason is not None:
                rejected.append(
                    RejectedDanceSignalV1(
                        digest,
                        reason,
                        (envelope.envelope_digest,),
                    )
                )
                del valid[digest]
                changed = True

    revision_snapshot = dict(valid)
    revision_invalid: dict[str, str] = {}
    for digest, envelope in revision_snapshot.items():
        depth = 0
        cursor = envelope
        visited = {digest}
        while cursor.signal.supersedes_signal_digest is not None:
            prior_digest = cursor.signal.supersedes_signal_digest
            if prior_digest in visited:
                revision_invalid[digest] = "revision_cycle"
                break
            visited.add(prior_digest)
            prior = revision_snapshot.get(prior_digest)
            if prior is None:
                break
            depth += 1
            if depth > policy.max_revision_depth:
                revision_invalid[digest] = "revision_depth_exceeded"
                break
            cursor = prior
    for digest in sorted(revision_invalid):
        envelope = revision_snapshot[digest]
        rejected.append(
            RejectedDanceSignalV1(
                digest,
                revision_invalid[digest],
                (envelope.envelope_digest,),
            )
        )
        valid.pop(digest, None)

    reviewer_profiles: dict[str, set[str]] = {}
    for envelope in valid.values():
        reviewer_profiles.setdefault(
            envelope.signal.reviewer.reviewer_cell_id, set()
        ).add(envelope.signal.reviewer.profile_digest)
    equivocators = {
        reviewer for reviewer, profiles in reviewer_profiles.items() if len(profiles) > 1
    }

    accepted = tuple(valid[digest] for digest in sorted(valid))
    stopped = tuple(
        item for item in accepted if item.signal.signal_kind is DanceSignalKind.STOP
    )
    challenged = tuple(
        item
        for item in accepted
        if item.signal.signal_kind is DanceSignalKind.CHALLENGE
    )
    superseded = {
        item.signal.supersedes_signal_digest
        for item in accepted
        if item.signal.supersedes_signal_digest is not None
    }
    active_support_candidates = tuple(
        item
        for item in accepted
        if item.signal.signal_kind is DanceSignalKind.SUPPORT
        and item.signal.signal_digest not in superseded
        and item.signal.reviewer.reviewer_cell_id not in equivocators
    )
    heads_by_reviewer: dict[
        str, list[AuthenticatedDanceSignalEnvelopeV1]
    ] = {}
    for item in active_support_candidates:
        heads_by_reviewer.setdefault(
            item.signal.reviewer.reviewer_cell_id, []
        ).append(item)
    forked_reviewers = {
        reviewer for reviewer, heads in heads_by_reviewer.items() if len(heads) > 1
    }
    active_support = tuple(
        item
        for item in active_support_candidates
        if item.signal.reviewer.reviewer_cell_id not in forked_reviewers
    )
    pair = _independent_pair(active_support)

    reasons = {item.reason_code for item in rejected}
    if equivocators:
        reasons.add("reviewer_profile_equivocation")
    if forked_reviewers:
        reasons.add("reviewer_revision_fork")
    if stopped:
        reasons.add("valid_stop_veto")
        decision = WDPDecision.STOPPED
    elif challenged or equivocators or forked_reviewers:
        reasons.add("challenge_or_equivocation")
        decision = WDPDecision.CHALLENGED
    elif pair:
        reasons.add("independent_support_pair")
        decision = WDPDecision.APPROVED_SHADOW
    elif not accepted:
        reasons.add("no_valid_signals")
        decision = WDPDecision.SILENCE
    else:
        reasons.add("insufficient_independent_support")
        decision = WDPDecision.INSUFFICIENT_INDEPENDENCE

    return _receipt(
        proposal_digest=proposal_digest,
        registry_digest=trust_registry.registry_digest,
        decision=decision,
        now_utc=now_utc,
        reasons=reasons,
        accepted=accepted,
        active_support=active_support,
        stopped=stopped,
        challenged=challenged,
        rejected=tuple(rejected),
        independent_pair=pair,
    )


__all__ = [
    "AuthenticatedDanceSignalEnvelopeV1",
    "AuthenticatedProposalEnvelopeV1",
    "DanceSignalEnvelopeV1",
    "HexRingSnapshotV1",
    "KeyResolver",
    "RejectedDanceSignalV1",
    "TrustedCellRecordV1",
    "TrustedWDPRegistryV1",
    "WDPContractError",
    "WDPDecision",
    "WDPPolicyV1",
    "WDPShadowReceiptV1",
    "evaluate_waggle_decision",
]
