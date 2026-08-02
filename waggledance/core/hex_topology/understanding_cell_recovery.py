# SPDX-License-Identifier: Apache-2.0
"""Authenticated, fail-closed shadow recovery plans for understanding cells.

This module performs no filesystem, network, replica, router, action, builder,
or state-write work.  It authenticates three checkpoint claims against an
out-of-band trust snapshot, selects an unambiguous 2-of-3 state, rejects a
stale majority when any authenticated witness has a newer fence, and derives
a non-authoritative replacement address above every observed fence.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from waggledance.core.learning.understanding_contracts import (
    HexCellAddressV1,
    UnderstandingContractError,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


CHECKPOINT_SCHEMA = "wd.understanding_cell_checkpoint.v1"
RECOVERY_SCHEMA = "wd.understanding_cell_recovery.v1"
RECOVERY_TRUST_REGISTRY_SCHEMA = "wd.understanding_recovery_trust_registry.v1"
AUTHENTICATED_CHECKPOINT_SCHEMA = "wd.authenticated_cell_checkpoint.v1"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUTH_TAG = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "cell",
        "cell_identity_digest",
        "replica_identity",
        "replica_failure_domain_digest",
        "ledger_head_digest",
        "ledger_event_count",
        "projection_digest",
        "manifest_digest",
    }
)
_CELL_KEYS = frozenset(
    {"cell_id", "q", "r", "incarnation_id", "generation", "fence"}
)

KeyResolver = Callable[[str, str], bytes]


class CellRecoveryError(ValueError):
    """A recovery artifact or operation failed closed."""


class NoRecoveryQuorumError(CellRecoveryError):
    """Authenticated manifests did not contain a safe 2-of-3 quorum."""


class CellFenceError(CellRecoveryError):
    """A message address is not valid for the current cell incarnation."""


class StaleCellFenceError(CellFenceError):
    """A message belongs to an older cell generation or fence."""


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise CellRecoveryError(f"{label} must be a canonical sha256 digest")
    return value


def _require_auth_tag(value: object, label: str) -> str:
    if type(value) is not str or not _AUTH_TAG.fullmatch(value):
        raise CellRecoveryError(f"{label} must be a canonical HMAC-SHA256 tag")
    return value


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise CellRecoveryError(f"{label} must be a bounded token")
    return value


def _require_count(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value < (1 << 63):
        raise CellRecoveryError(f"{label} must be an integer within 0..2^63-1")
    return value


def _require_hmac_key(value: object) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise CellRecoveryError("HMAC keys must be bytes with at least 32 bytes")
    return value


def _auth_tag(payload: dict[str, object], key: bytes) -> str:
    digest = hmac.new(
        _require_hmac_key(key),
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def _resolved_key(
    resolver: KeyResolver, *, key_id: str, key_epoch: str
) -> bytes | None:
    try:
        key = resolver(key_id, key_epoch)
    except Exception:
        return None
    if type(key) is not bytes or len(key) < 32:
        return None
    return key


def _checkpoint_core(
    *,
    cell: HexCellAddressV1,
    cell_identity_digest: str,
    replica_identity: str,
    replica_failure_domain_digest: str,
    ledger_head_digest: str,
    ledger_event_count: int,
    projection_digest: str,
) -> dict[str, object]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "cell": cell.to_mapping(),
        "cell_identity_digest": cell_identity_digest,
        "replica_identity": replica_identity,
        "replica_failure_domain_digest": replica_failure_domain_digest,
        "ledger_head_digest": ledger_head_digest,
        "ledger_event_count": ledger_event_count,
        "projection_digest": projection_digest,
    }


def _state_core(
    *,
    cell: HexCellAddressV1,
    cell_identity_digest: str,
    ledger_head_digest: str,
    ledger_event_count: int,
    projection_digest: str,
) -> dict[str, object]:
    return {
        "cell": cell.to_mapping(),
        "cell_identity_digest": cell_identity_digest,
        "ledger_head_digest": ledger_head_digest,
        "ledger_event_count": ledger_event_count,
        "projection_digest": projection_digest,
    }


def _state_digest(core: Mapping[str, object]) -> str:
    return sha256_digest(
        {"domain": "wd.understanding_cell_checkpoint.state.v1", **dict(core)}
    )


def _parse_cell(value: object) -> HexCellAddressV1:
    if not isinstance(value, Mapping) or set(value) != _CELL_KEYS:
        raise CellRecoveryError("cell must contain the exact V1 address fields")
    try:
        return HexCellAddressV1(
            cell_id=value["cell_id"],
            q=value["q"],
            r=value["r"],
            incarnation_id=value["incarnation_id"],
            generation=value["generation"],
            fence=value["fence"],
        )
    except (KeyError, TypeError, UnderstandingContractError) as exc:
        raise CellRecoveryError("cell contains an invalid V1 address") from exc


@dataclass(frozen=True)
class CellCheckpointManifestV1:
    """One replica's digest-bound checkpoint claim; authenticity is separate."""

    cell: HexCellAddressV1
    cell_identity_digest: str
    replica_identity: str
    replica_failure_domain_digest: str
    ledger_head_digest: str
    ledger_event_count: int
    projection_digest: str
    manifest_digest: str
    schema_version: str = CHECKPOINT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA:
            raise CellRecoveryError("unknown checkpoint schema")
        if type(self.cell) is not HexCellAddressV1:
            raise CellRecoveryError("cell must be HexCellAddressV1")
        _require_digest(self.cell_identity_digest, "cell_identity_digest")
        _require_token(self.replica_identity, "replica_identity")
        _require_digest(
            self.replica_failure_domain_digest,
            "replica_failure_domain_digest",
        )
        _require_digest(self.ledger_head_digest, "ledger_head_digest")
        _require_count(self.ledger_event_count, "ledger_event_count")
        _require_digest(self.projection_digest, "projection_digest")
        _require_digest(self.manifest_digest, "manifest_digest")
        expected = sha256_digest(
            {
                "domain": "wd.understanding_cell_checkpoint.manifest.v1",
                **self.core_mapping(),
            }
        )
        if self.manifest_digest != expected:
            raise CellRecoveryError("checkpoint manifest digest mismatch")

    @classmethod
    def create(
        cls,
        *,
        cell: HexCellAddressV1,
        cell_identity_digest: str,
        replica_identity: str,
        replica_failure_domain_digest: str,
        ledger_head_digest: str,
        ledger_event_count: int,
        projection_digest: str,
    ) -> "CellCheckpointManifestV1":
        if type(cell) is not HexCellAddressV1:
            raise CellRecoveryError("cell must be HexCellAddressV1")
        _require_digest(cell_identity_digest, "cell_identity_digest")
        _require_token(replica_identity, "replica_identity")
        _require_digest(
            replica_failure_domain_digest,
            "replica_failure_domain_digest",
        )
        _require_digest(ledger_head_digest, "ledger_head_digest")
        _require_count(ledger_event_count, "ledger_event_count")
        _require_digest(projection_digest, "projection_digest")
        core = _checkpoint_core(
            cell=cell,
            cell_identity_digest=cell_identity_digest,
            replica_identity=replica_identity,
            replica_failure_domain_digest=replica_failure_domain_digest,
            ledger_head_digest=ledger_head_digest,
            ledger_event_count=ledger_event_count,
            projection_digest=projection_digest,
        )
        return cls(
            cell=cell,
            cell_identity_digest=cell_identity_digest,
            replica_identity=replica_identity,
            replica_failure_domain_digest=replica_failure_domain_digest,
            ledger_head_digest=ledger_head_digest,
            ledger_event_count=ledger_event_count,
            projection_digest=projection_digest,
            manifest_digest=sha256_digest(
                {
                    "domain": "wd.understanding_cell_checkpoint.manifest.v1",
                    **core,
                }
            ),
        )

    @classmethod
    def from_mapping(cls, value: object) -> "CellCheckpointManifestV1":
        if not isinstance(value, Mapping) or set(value) != _CHECKPOINT_KEYS:
            raise CellRecoveryError("checkpoint manifest is torn or has unknown fields")
        if value.get("schema_version") != CHECKPOINT_SCHEMA:
            raise CellRecoveryError("unknown checkpoint schema")
        return cls(
            cell=_parse_cell(value["cell"]),
            cell_identity_digest=value["cell_identity_digest"],
            replica_identity=value["replica_identity"],
            replica_failure_domain_digest=value[
                "replica_failure_domain_digest"
            ],
            ledger_head_digest=value["ledger_head_digest"],
            ledger_event_count=value["ledger_event_count"],
            projection_digest=value["projection_digest"],
            manifest_digest=value["manifest_digest"],
            schema_version=value["schema_version"],
        )

    def core_mapping(self) -> dict[str, object]:
        return _checkpoint_core(
            cell=self.cell,
            cell_identity_digest=self.cell_identity_digest,
            replica_identity=self.replica_identity,
            replica_failure_domain_digest=self.replica_failure_domain_digest,
            ledger_head_digest=self.ledger_head_digest,
            ledger_event_count=self.ledger_event_count,
            projection_digest=self.projection_digest,
        )

    def state_mapping(self) -> dict[str, object]:
        return _state_core(
            cell=self.cell,
            cell_identity_digest=self.cell_identity_digest,
            ledger_head_digest=self.ledger_head_digest,
            ledger_event_count=self.ledger_event_count,
            projection_digest=self.projection_digest,
        )

    @property
    def state_digest(self) -> str:
        return _state_digest(self.state_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {**self.core_mapping(), "manifest_digest": self.manifest_digest}


@dataclass(frozen=True)
class TrustedReplicaRecordV1:
    """Out-of-band identity, placement, and key metadata for one replica."""

    replica_identity: str
    auth_key_id: str
    key_epoch: str
    failure_domain_digest: str
    logical_cell_id: str
    q: int
    r: int
    cell_identity_digest: str

    def __post_init__(self) -> None:
        _require_token(self.replica_identity, "replica_identity")
        _require_token(self.auth_key_id, "auth_key_id")
        _require_token(self.key_epoch, "key_epoch")
        _require_digest(self.failure_domain_digest, "failure_domain_digest")
        _require_token(self.logical_cell_id, "logical_cell_id")
        if type(self.q) is not int or type(self.r) is not int:
            raise CellRecoveryError("replica axial coordinates must be exact integers")
        _require_digest(self.cell_identity_digest, "cell_identity_digest")

    def to_mapping(self) -> dict[str, object]:
        return {
            "replica_identity": self.replica_identity,
            "auth_key_id": self.auth_key_id,
            "key_epoch": self.key_epoch,
            "failure_domain_digest": self.failure_domain_digest,
            "logical_cell_id": self.logical_cell_id,
            "q": self.q,
            "r": self.r,
            "cell_identity_digest": self.cell_identity_digest,
        }


def _trust_registry_core(
    records: tuple[TrustedReplicaRecordV1, ...]
) -> dict[str, object]:
    return {
        "schema_version": RECOVERY_TRUST_REGISTRY_SCHEMA,
        "records": [record.to_mapping() for record in records],
    }


@dataclass(frozen=True)
class TrustedRecoveryRegistryV1:
    """Immutable digest-bound three-replica trust snapshot."""

    records: tuple[TrustedReplicaRecordV1, ...]
    registry_digest: str
    schema_version: str = RECOVERY_TRUST_REGISTRY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != RECOVERY_TRUST_REGISTRY_SCHEMA:
            raise CellRecoveryError("unknown recovery trust registry schema")
        if type(self.records) is not tuple or len(self.records) != 3:
            raise CellRecoveryError("recovery trust registry requires exactly three replicas")
        if any(type(record) is not TrustedReplicaRecordV1 for record in self.records):
            raise CellRecoveryError("recovery trust registry contains invalid records")
        identities = tuple(record.replica_identity for record in self.records)
        if identities != tuple(sorted(identities)) or len(set(identities)) != 3:
            raise CellRecoveryError("trusted replicas must be sorted and distinct")
        key_metadata = tuple(
            (record.auth_key_id, record.key_epoch) for record in self.records
        )
        if len(set(key_metadata)) != 3:
            raise CellRecoveryError(
                "trusted replicas require distinct authentication key metadata"
            )
        domains = tuple(record.failure_domain_digest for record in self.records)
        if len(set(domains)) != 3:
            raise CellRecoveryError("trusted replicas require distinct failure domains")
        logical = {
            (
                record.logical_cell_id,
                record.q,
                record.r,
                record.cell_identity_digest,
            )
            for record in self.records
        }
        if len(logical) != 1:
            raise CellRecoveryError("trusted replicas must describe one logical cell")
        _require_digest(self.registry_digest, "registry_digest")
        expected = sha256_digest(
            {
                "domain": "wd.understanding_recovery_trust_registry.digest.v1",
                **_trust_registry_core(self.records),
            }
        )
        if self.registry_digest != expected:
            raise CellRecoveryError("recovery trust registry digest mismatch")

    @classmethod
    def create(
        cls, records: tuple[TrustedReplicaRecordV1, ...]
    ) -> "TrustedRecoveryRegistryV1":
        if type(records) is not tuple:
            raise CellRecoveryError("trusted replica records must be a tuple")
        ordered = tuple(sorted(records, key=lambda item: item.replica_identity))
        return cls(
            records=ordered,
            registry_digest=sha256_digest(
                {
                    "domain": "wd.understanding_recovery_trust_registry.digest.v1",
                    **_trust_registry_core(ordered),
                }
            ),
        )

    def record_for(self, replica_identity: str) -> TrustedReplicaRecordV1 | None:
        return next(
            (
                record
                for record in self.records
                if record.replica_identity == replica_identity
            ),
            None,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            **_trust_registry_core(self.records),
            "registry_digest": self.registry_digest,
        }


def _checkpoint_auth_payload(
    *,
    manifest: CellCheckpointManifestV1,
    registry_digest: str,
    key_id: str,
    key_epoch: str,
) -> dict[str, object]:
    return {
        "domain": "wd.recovery.checkpoint.auth.v1",
        "schema_version": AUTHENTICATED_CHECKPOINT_SCHEMA,
        "registry_digest": registry_digest,
        "manifest": manifest.to_mapping(),
        "key_id": key_id,
        "key_epoch": key_epoch,
    }


@dataclass(frozen=True)
class AuthenticatedCheckpointEnvelopeV1:
    manifest: CellCheckpointManifestV1
    registry_digest: str
    key_id: str
    key_epoch: str
    auth_tag: str
    schema_version: str = AUTHENTICATED_CHECKPOINT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != AUTHENTICATED_CHECKPOINT_SCHEMA:
            raise CellRecoveryError("unknown authenticated checkpoint schema")
        if type(self.manifest) is not CellCheckpointManifestV1:
            raise CellRecoveryError("manifest must be CellCheckpointManifestV1")
        _require_digest(self.registry_digest, "registry_digest")
        _require_token(self.key_id, "key_id")
        _require_token(self.key_epoch, "key_epoch")
        _require_auth_tag(self.auth_tag, "auth_tag")

    @classmethod
    def create(
        cls,
        *,
        manifest: CellCheckpointManifestV1,
        registry_digest: str,
        key_id: str,
        key_epoch: str,
        hmac_key: bytes,
    ) -> "AuthenticatedCheckpointEnvelopeV1":
        payload = _checkpoint_auth_payload(
            manifest=manifest,
            registry_digest=registry_digest,
            key_id=key_id,
            key_epoch=key_epoch,
        )
        return cls(
            manifest=manifest,
            registry_digest=registry_digest,
            key_id=key_id,
            key_epoch=key_epoch,
            auth_tag=_auth_tag(payload, hmac_key),
        )

    @property
    def envelope_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.recovery.checkpoint.envelope.digest.v1",
                **_checkpoint_auth_payload(
                    manifest=self.manifest,
                    registry_digest=self.registry_digest,
                    key_id=self.key_id,
                    key_epoch=self.key_epoch,
                ),
                "auth_tag": self.auth_tag,
            }
        )


@dataclass(frozen=True)
class RecoveryWitnessV1:
    """Identity-domain-manifest-auth tuple retained without losing pairing."""

    replica_identity: str
    failure_domain_digest: str
    manifest_digest: str
    auth_tag_digest: str
    supports_selected_state: bool

    def __post_init__(self) -> None:
        _require_token(self.replica_identity, "replica_identity")
        _require_digest(self.failure_domain_digest, "failure_domain_digest")
        _require_digest(self.manifest_digest, "manifest_digest")
        _require_digest(self.auth_tag_digest, "auth_tag_digest")
        if type(self.supports_selected_state) is not bool:
            raise CellRecoveryError("supports_selected_state must be bool")

    def to_mapping(self) -> dict[str, object]:
        return {
            "replica_identity": self.replica_identity,
            "failure_domain_digest": self.failure_domain_digest,
            "manifest_digest": self.manifest_digest,
            "auth_tag_digest": self.auth_tag_digest,
            "supports_selected_state": self.supports_selected_state,
        }


def _witness(
    envelope: AuthenticatedCheckpointEnvelopeV1,
    *,
    selected_state_digest: str,
) -> RecoveryWitnessV1:
    manifest = envelope.manifest
    return RecoveryWitnessV1(
        replica_identity=manifest.replica_identity,
        failure_domain_digest=manifest.replica_failure_domain_digest,
        manifest_digest=manifest.manifest_digest,
        auth_tag_digest=sha256_digest(
            {
                "domain": "wd.recovery.checkpoint.auth_tag.digest.v1",
                "auth_tag": envelope.auth_tag,
            }
        ),
        supports_selected_state=manifest.state_digest == selected_state_digest,
    )


def _resolved_trusted_key_snapshot(
    trust_registry: TrustedRecoveryRegistryV1,
    key_resolver: KeyResolver,
) -> dict[tuple[str, str], bytes]:
    """Resolve each recovery principal once; shared keys are not quorum."""

    resolved: dict[tuple[str, str], bytes] = {}
    material_fingerprints: set[bytes] = set()
    for record in trust_registry.records:
        metadata = (record.auth_key_id, record.key_epoch)
        if metadata in resolved:
            raise CellRecoveryError(
                "recovery authentication key metadata is not independent"
            )
        key = _resolved_key(
            key_resolver,
            key_id=record.auth_key_id,
            key_epoch=record.key_epoch,
        )
        if key is None:
            raise CellRecoveryError("checkpoint authentication key unavailable")
        fingerprint = hashlib.sha256(key).digest()
        if fingerprint in material_fingerprints:
            raise CellRecoveryError(
                "recovery authentication key material is not independent"
            )
        material_fingerprints.add(fingerprint)
        resolved[metadata] = bytes(key)
    return resolved


def _selection_core(
    *,
    registry_digest: str,
    state: Mapping[str, object],
    state_digest: str,
    observed_max_generation: int,
    observed_max_fence: int,
    witnesses: tuple[RecoveryWitnessV1, ...],
) -> dict[str, object]:
    return {
        "trust_registry_digest": registry_digest,
        "state": dict(state),
        "state_digest": state_digest,
        "observed_max_generation": observed_max_generation,
        "observed_max_fence": observed_max_fence,
        "witnesses": [witness.to_mapping() for witness in witnesses],
    }


@dataclass(frozen=True)
class CellRecoverySelectionV1:
    """Non-authoritative evidence summary returned by authenticated selection."""

    trust_registry_digest: str
    source_cell: HexCellAddressV1
    cell_identity_digest: str
    ledger_head_digest: str
    ledger_event_count: int
    projection_digest: str
    observed_max_generation: int
    observed_max_fence: int
    witnesses: tuple[RecoveryWitnessV1, ...]
    state_digest: str
    selection_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.trust_registry_digest, "trust_registry_digest")
        if type(self.source_cell) is not HexCellAddressV1:
            raise CellRecoveryError("source_cell must be HexCellAddressV1")
        _require_digest(self.cell_identity_digest, "cell_identity_digest")
        _require_digest(self.ledger_head_digest, "ledger_head_digest")
        _require_count(self.ledger_event_count, "ledger_event_count")
        _require_digest(self.projection_digest, "projection_digest")
        _require_count(self.observed_max_generation, "observed_max_generation")
        _require_count(self.observed_max_fence, "observed_max_fence")
        if (
            self.source_cell.generation != self.observed_max_generation
            or self.source_cell.fence != self.observed_max_fence
        ):
            raise CellRecoveryError("selected state must be at the maximum observed fence")
        if type(self.witnesses) is not tuple or len(self.witnesses) != 3:
            raise CellRecoveryError("selection requires exactly three authenticated witnesses")
        if any(type(witness) is not RecoveryWitnessV1 for witness in self.witnesses):
            raise CellRecoveryError("selection contains an invalid witness")
        identities = tuple(witness.replica_identity for witness in self.witnesses)
        domains = tuple(witness.failure_domain_digest for witness in self.witnesses)
        if identities != tuple(sorted(identities)) or len(set(identities)) != 3:
            raise CellRecoveryError("selection witnesses must be sorted and distinct")
        if len(set(domains)) != 3:
            raise CellRecoveryError("selection witnesses need distinct failure domains")
        if sum(witness.supports_selected_state for witness in self.witnesses) < 2:
            raise CellRecoveryError("selection witnesses do not prove a quorum")
        state = _state_core(
            cell=self.source_cell,
            cell_identity_digest=self.cell_identity_digest,
            ledger_head_digest=self.ledger_head_digest,
            ledger_event_count=self.ledger_event_count,
            projection_digest=self.projection_digest,
        )
        expected_state = _state_digest(state)
        if self.state_digest != expected_state:
            raise CellRecoveryError("recovery selection state digest mismatch")
        expected_selection = sha256_digest(
            {
                "domain": "wd.understanding_cell_recovery.selection.v1",
                **_selection_core(
                    registry_digest=self.trust_registry_digest,
                    state=state,
                    state_digest=self.state_digest,
                    observed_max_generation=self.observed_max_generation,
                    observed_max_fence=self.observed_max_fence,
                    witnesses=self.witnesses,
                ),
            }
        )
        if self.selection_digest != expected_selection:
            raise CellRecoveryError("recovery selection digest mismatch")

    @property
    def agreeing_replica_identities(self) -> tuple[str, ...]:
        return tuple(
            witness.replica_identity
            for witness in self.witnesses
            if witness.supports_selected_state
        )

    @property
    def agreeing_replica_failure_domain_digests(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                witness.failure_domain_digest
                for witness in self.witnesses
                if witness.supports_selected_state
            )
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "trust_registry_digest": self.trust_registry_digest,
            "source_cell": self.source_cell.to_mapping(),
            "cell_identity_digest": self.cell_identity_digest,
            "ledger_head_digest": self.ledger_head_digest,
            "ledger_event_count": self.ledger_event_count,
            "projection_digest": self.projection_digest,
            "observed_max_generation": self.observed_max_generation,
            "observed_max_fence": self.observed_max_fence,
            "witnesses": [witness.to_mapping() for witness in self.witnesses],
            "state_digest": self.state_digest,
            "selection_digest": self.selection_digest,
        }


def _verify_checkpoint_envelopes(
    envelopes: Sequence[AuthenticatedCheckpointEnvelopeV1],
    *,
    trust_registry: TrustedRecoveryRegistryV1,
    key_resolver: KeyResolver,
) -> tuple[
    tuple[AuthenticatedCheckpointEnvelopeV1, ...],
    TrustedRecoveryRegistryV1,
]:
    # Detach caller-owned frozen dataclasses before invoking a resolver.  A
    # closure may hold aliases and use object.__setattr__; only this local
    # snapshot is consulted after the first callback.
    if isinstance(envelopes, (str, bytes)):
        raise NoRecoveryQuorumError("recovery requires exactly three envelopes")
    try:
        envelope_snapshot = copy.deepcopy(tuple(envelopes))
        trust_registry = copy.deepcopy(trust_registry)
    except (TypeError, ValueError) as exc:
        raise NoRecoveryQuorumError(
            "recovery requires exactly three envelopes"
        ) from exc
    except Exception as exc:
        raise CellRecoveryError(
            "recovery inputs could not be safely snapshotted"
        ) from exc
    try:
        expected_registry_digest = sha256_digest(
            {
                "domain": "wd.understanding_recovery_trust_registry.digest.v1",
                **_trust_registry_core(trust_registry.records),
            }
        )
    except Exception as exc:
        raise CellRecoveryError("recovery trust registry snapshot is invalid") from exc
    if not hmac.compare_digest(
        trust_registry.registry_digest, expected_registry_digest
    ):
        raise CellRecoveryError("recovery trust registry snapshot is invalid")
    if len(envelope_snapshot) != 3:
        raise NoRecoveryQuorumError("recovery requires exactly three envelopes")
    if any(
        type(item) is not AuthenticatedCheckpointEnvelopeV1
        for item in envelope_snapshot
    ):
        raise CellRecoveryError(
            "bare checkpoint manifests are not authenticated recovery evidence"
        )
    trusted_keys = _resolved_trusted_key_snapshot(
        trust_registry,
        key_resolver,
    )
    verified: list[AuthenticatedCheckpointEnvelopeV1] = []
    for envelope in envelope_snapshot:
        # Reparse the manifest mapping so a mutated frozen object is not trusted.
        if type(envelope.manifest) is not CellCheckpointManifestV1:
            raise CellRecoveryError("checkpoint envelope manifest is invalid")
        try:
            manifest = CellCheckpointManifestV1.from_mapping(
                envelope.manifest.to_mapping()
            )
        except CellRecoveryError:
            raise
        except Exception as exc:
            raise CellRecoveryError(
                "checkpoint envelope manifest is invalid"
            ) from exc
        if envelope.registry_digest != trust_registry.registry_digest:
            raise CellRecoveryError("checkpoint trust registry mismatch")
        record = trust_registry.record_for(manifest.replica_identity)
        if record is None:
            raise CellRecoveryError("checkpoint replica is not trusted")
        if manifest.replica_failure_domain_digest != record.failure_domain_digest:
            raise CellRecoveryError("checkpoint failure domain is not registry-derived")
        if (
            manifest.cell.cell_id != record.logical_cell_id
            or manifest.cell.q != record.q
            or manifest.cell.r != record.r
            or manifest.cell_identity_digest != record.cell_identity_digest
        ):
            raise CellRecoveryError("checkpoint logical cell is not registry-derived")
        if envelope.key_id != record.auth_key_id or envelope.key_epoch != record.key_epoch:
            raise CellRecoveryError("checkpoint key metadata mismatch")
        key = trusted_keys.get((envelope.key_id, envelope.key_epoch))
        if key is None:
            raise CellRecoveryError("checkpoint authentication key unavailable")
        expected = _auth_tag(
            _checkpoint_auth_payload(
                manifest=manifest,
                registry_digest=envelope.registry_digest,
                key_id=envelope.key_id,
                key_epoch=envelope.key_epoch,
            ),
            key,
        )
        if not hmac.compare_digest(envelope.auth_tag, expected):
            raise CellRecoveryError("checkpoint authentication failed")
        verified.append(
            AuthenticatedCheckpointEnvelopeV1(
                manifest=manifest,
                registry_digest=envelope.registry_digest,
                key_id=envelope.key_id,
                key_epoch=envelope.key_epoch,
                auth_tag=envelope.auth_tag,
            )
        )
    ordered = tuple(
        sorted(verified, key=lambda item: item.manifest.replica_identity)
    )
    identities = tuple(item.manifest.replica_identity for item in ordered)
    expected_identities = tuple(
        record.replica_identity for record in trust_registry.records
    )
    if identities != expected_identities:
        raise NoRecoveryQuorumError(
            "recovery requires each trusted replica exactly once"
        )
    return ordered, trust_registry


def _select_verified(
    envelopes: Sequence[AuthenticatedCheckpointEnvelopeV1],
    *,
    trust_registry: TrustedRecoveryRegistryV1,
    key_resolver: KeyResolver,
) -> tuple[
    CellRecoverySelectionV1, tuple[AuthenticatedCheckpointEnvelopeV1, ...]
]:
    verified, trust_registry = _verify_checkpoint_envelopes(
        envelopes,
        trust_registry=trust_registry,
        key_resolver=key_resolver,
    )
    groups: dict[str, list[AuthenticatedCheckpointEnvelopeV1]] = {}
    for envelope in verified:
        groups.setdefault(envelope.manifest.state_digest, []).append(envelope)
    quorum_groups = [items for items in groups.values() if len(items) >= 2]
    if len(quorum_groups) != 1:
        raise NoRecoveryQuorumError("no unambiguous 2-of-3 recovery quorum")
    winner_group = quorum_groups[0]
    winner = winner_group[0].manifest
    max_generation = max(item.manifest.cell.generation for item in verified)
    max_fence = max(item.manifest.cell.fence for item in verified)
    if winner.cell.generation != max_generation or winner.cell.fence != max_fence:
        raise NoRecoveryQuorumError("newer generation or fence exists without quorum")
    witnesses = tuple(
        _witness(item, selected_state_digest=winner.state_digest)
        for item in verified
    )
    state = winner.state_mapping()
    core = _selection_core(
        registry_digest=trust_registry.registry_digest,
        state=state,
        state_digest=winner.state_digest,
        observed_max_generation=max_generation,
        observed_max_fence=max_fence,
        witnesses=witnesses,
    )
    selection = CellRecoverySelectionV1(
        trust_registry_digest=trust_registry.registry_digest,
        source_cell=winner.cell,
        cell_identity_digest=winner.cell_identity_digest,
        ledger_head_digest=winner.ledger_head_digest,
        ledger_event_count=winner.ledger_event_count,
        projection_digest=winner.projection_digest,
        observed_max_generation=max_generation,
        observed_max_fence=max_fence,
        witnesses=witnesses,
        state_digest=winner.state_digest,
        selection_digest=sha256_digest(
            {"domain": "wd.understanding_cell_recovery.selection.v1", **core}
        ),
    )
    return selection, verified


def select_recovery_checkpoint(
    envelopes: Sequence[AuthenticatedCheckpointEnvelopeV1],
    *,
    trust_registry: TrustedRecoveryRegistryV1,
    key_resolver: KeyResolver,
) -> CellRecoverySelectionV1:
    """Return a shadow selection only after authenticating all three witnesses."""

    if type(trust_registry) is not TrustedRecoveryRegistryV1:
        raise CellRecoveryError(
            "trust_registry must be TrustedRecoveryRegistryV1"
        )
    if not callable(key_resolver):
        raise CellRecoveryError("key_resolver must be callable")
    selection, _ = _select_verified(
        envelopes,
        trust_registry=trust_registry,
        key_resolver=key_resolver,
    )
    return selection


@dataclass(frozen=True)
class RebuiltCellManifestV1:
    """A deterministic, non-authoritative shadow plan for a replacement."""

    trust_registry_digest: str
    source_cell: HexCellAddressV1
    rebuilt_cell: HexCellAddressV1
    cell_identity_digest: str
    ledger_head_digest: str
    ledger_event_count: int
    projection_digest: str
    witnesses: tuple[RecoveryWitnessV1, ...]
    source_selection_digest: str
    recovery_digest: str
    router_authority: bool = False
    action_authority: bool = False
    builder_authority: bool = False
    schema_version: str = RECOVERY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != RECOVERY_SCHEMA:
            raise CellRecoveryError("unknown recovery schema")
        _require_digest(self.trust_registry_digest, "trust_registry_digest")
        if type(self.source_cell) is not HexCellAddressV1 or type(
            self.rebuilt_cell
        ) is not HexCellAddressV1:
            raise CellRecoveryError("source and rebuilt cells must be V1 addresses")
        if (
            self.rebuilt_cell.cell_id != self.source_cell.cell_id
            or self.rebuilt_cell.q != self.source_cell.q
            or self.rebuilt_cell.r != self.source_cell.r
            or self.rebuilt_cell.incarnation_id == self.source_cell.incarnation_id
            or self.rebuilt_cell.generation != self.source_cell.generation + 1
            or self.rebuilt_cell.fence != self.source_cell.fence + 1
        ):
            raise CellRecoveryError("rebuilt cell must advance incarnation generation/fence")
        _require_digest(self.cell_identity_digest, "cell_identity_digest")
        _require_digest(self.ledger_head_digest, "ledger_head_digest")
        _require_count(self.ledger_event_count, "ledger_event_count")
        _require_digest(self.projection_digest, "projection_digest")
        if type(self.witnesses) is not tuple or len(self.witnesses) != 3:
            raise CellRecoveryError("recovery plan requires three witnesses")
        if any(type(witness) is not RecoveryWitnessV1 for witness in self.witnesses):
            raise CellRecoveryError("recovery plan contains an invalid witness")
        _require_digest(self.source_selection_digest, "source_selection_digest")
        if (
            type(self.router_authority) is not bool
            or type(self.action_authority) is not bool
            or type(self.builder_authority) is not bool
            or self.router_authority
            or self.action_authority
            or self.builder_authority
        ):
            raise CellRecoveryError("recovery manifests cannot grant authority")
        _require_digest(self.recovery_digest, "recovery_digest")
        expected = sha256_digest(
            {
                "domain": "wd.understanding_cell_recovery.manifest.v1",
                **self.core_mapping(),
            }
        )
        if self.recovery_digest != expected:
            raise CellRecoveryError("recovery manifest digest mismatch")

    @property
    def agreeing_replica_identities(self) -> tuple[str, ...]:
        return tuple(
            witness.replica_identity
            for witness in self.witnesses
            if witness.supports_selected_state
        )

    @property
    def agreeing_replica_failure_domain_digests(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                witness.failure_domain_digest
                for witness in self.witnesses
                if witness.supports_selected_state
            )
        )

    def core_mapping(self) -> dict[str, object]:
        if (
            self.router_authority is not False
            or self.action_authority is not False
            or self.builder_authority is not False
        ):
            raise CellRecoveryError("recovery manifests cannot grant authority")
        return {
            "schema_version": self.schema_version,
            "trust_registry_digest": self.trust_registry_digest,
            "source_cell": self.source_cell.to_mapping(),
            "rebuilt_cell": self.rebuilt_cell.to_mapping(),
            "cell_identity_digest": self.cell_identity_digest,
            "ledger_head_digest": self.ledger_head_digest,
            "ledger_event_count": self.ledger_event_count,
            "projection_digest": self.projection_digest,
            "witnesses": [witness.to_mapping() for witness in self.witnesses],
            "source_selection_digest": self.source_selection_digest,
            "router_authority": False,
            "action_authority": False,
            "builder_authority": False,
        }

    def to_mapping(self) -> dict[str, object]:
        return {**self.core_mapping(), "recovery_digest": self.recovery_digest}


def _rebuild_verified(
    selection: CellRecoverySelectionV1,
    verified: tuple[AuthenticatedCheckpointEnvelopeV1, ...],
    *,
    new_incarnation_id: str,
) -> RebuiltCellManifestV1:
    _require_token(new_incarnation_id, "new_incarnation_id")
    observed_incarnations = {
        envelope.manifest.cell.incarnation_id for envelope in verified
    }
    if new_incarnation_id in observed_incarnations:
        raise CellRecoveryError("a rebuild requires a globally new observed incarnation")
    try:
        rebuilt = HexCellAddressV1(
            cell_id=selection.source_cell.cell_id,
            q=selection.source_cell.q,
            r=selection.source_cell.r,
            incarnation_id=new_incarnation_id,
            generation=selection.observed_max_generation + 1,
            fence=selection.observed_max_fence + 1,
        )
    except UnderstandingContractError as exc:
        raise CellRecoveryError("cell generation or fence cannot be incremented") from exc
    core = {
        "schema_version": RECOVERY_SCHEMA,
        "trust_registry_digest": selection.trust_registry_digest,
        "source_cell": selection.source_cell.to_mapping(),
        "rebuilt_cell": rebuilt.to_mapping(),
        "cell_identity_digest": selection.cell_identity_digest,
        "ledger_head_digest": selection.ledger_head_digest,
        "ledger_event_count": selection.ledger_event_count,
        "projection_digest": selection.projection_digest,
        "witnesses": [witness.to_mapping() for witness in selection.witnesses],
        "source_selection_digest": selection.selection_digest,
        "router_authority": False,
        "action_authority": False,
        "builder_authority": False,
    }
    return RebuiltCellManifestV1(
        trust_registry_digest=selection.trust_registry_digest,
        source_cell=selection.source_cell,
        rebuilt_cell=rebuilt,
        cell_identity_digest=selection.cell_identity_digest,
        ledger_head_digest=selection.ledger_head_digest,
        ledger_event_count=selection.ledger_event_count,
        projection_digest=selection.projection_digest,
        witnesses=selection.witnesses,
        source_selection_digest=selection.selection_digest,
        recovery_digest=sha256_digest(
            {"domain": "wd.understanding_cell_recovery.manifest.v1", **core}
        ),
        router_authority=False,
        action_authority=False,
        builder_authority=False,
    )


def plan_cell_recovery(
    envelopes: Sequence[AuthenticatedCheckpointEnvelopeV1],
    *,
    trust_registry: TrustedRecoveryRegistryV1,
    key_resolver: KeyResolver,
    new_incarnation_id: str,
) -> RebuiltCellManifestV1:
    """Authenticate evidence and derive a shadow plan in one public boundary."""

    if type(trust_registry) is not TrustedRecoveryRegistryV1:
        raise CellRecoveryError(
            "trust_registry must be TrustedRecoveryRegistryV1"
        )
    if not callable(key_resolver):
        raise CellRecoveryError("key_resolver must be callable")
    selection, verified = _select_verified(
        envelopes,
        trust_registry=trust_registry,
        key_resolver=key_resolver,
    )
    return _rebuild_verified(
        selection,
        verified,
        new_incarnation_id=new_incarnation_id,
    )


def rebuild_from_selection(
    selection: CellRecoverySelectionV1,
    *,
    new_incarnation_id: str,
) -> RebuiltCellManifestV1:
    """Refuse forgeable bare selections; use plan_cell_recovery instead."""

    del selection, new_incarnation_id
    raise CellRecoveryError(
        "bare recovery selections are not accepted; authenticate checkpoint envelopes"
    )


def validate_cell_message_fence(
    message_cell: HexCellAddressV1,
    current_cell: HexCellAddressV1,
) -> None:
    """Accept only the exact current logical address and generation fence."""

    if type(message_cell) is not HexCellAddressV1 or type(
        current_cell
    ) is not HexCellAddressV1:
        raise CellFenceError("message_cell and current_cell must be V1 addresses")
    if (
        message_cell.cell_id != current_cell.cell_id
        or message_cell.q != current_cell.q
        or message_cell.r != current_cell.r
    ):
        raise CellFenceError("message targets a different logical cell")
    if (
        message_cell.fence < current_cell.fence
        or message_cell.generation < current_cell.generation
    ):
        raise StaleCellFenceError("message belongs to an older generation fence")
    if (
        message_cell.incarnation_id != current_cell.incarnation_id
        or message_cell.generation != current_cell.generation
        or message_cell.fence != current_cell.fence
    ):
        raise CellFenceError("message does not match the current incarnation fence")


__all__ = [
    "AUTHENTICATED_CHECKPOINT_SCHEMA",
    "CHECKPOINT_SCHEMA",
    "RECOVERY_SCHEMA",
    "AuthenticatedCheckpointEnvelopeV1",
    "CellCheckpointManifestV1",
    "CellFenceError",
    "CellRecoveryError",
    "CellRecoverySelectionV1",
    "KeyResolver",
    "NoRecoveryQuorumError",
    "RebuiltCellManifestV1",
    "RecoveryWitnessV1",
    "StaleCellFenceError",
    "TrustedRecoveryRegistryV1",
    "TrustedReplicaRecordV1",
    "plan_cell_recovery",
    "select_recovery_checkpoint",
    "validate_cell_message_fence",
]
