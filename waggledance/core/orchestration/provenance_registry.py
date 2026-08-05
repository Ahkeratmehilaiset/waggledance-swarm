# SPDX-License-Identifier: BUSL-1.1
"""Pure trusted-provenance registry contracts.

The registry binds a *digest* of a signing key to one reviewer cell, activation
scope, lineage, and the seven provenance dimensions used by
``evidence_consensus``.  It contains no signing key material, performs no
cryptography or I/O, and grants no authority.  A caller must authenticate and
pin a registry head through a separate trust layer before resolution has any
security meaning.

Snapshots are exact, bounded, content-addressed values.  Bindings are ordered
by the full signing-key digest.  A key identifies exactly one immutable set of
reviewer facts; revocation is the only permitted mutation and is irreversible.
Several keys may represent one cell/scope (key rotation), but those bindings
must retain identical lineage and provenance so rotation cannot manufacture
independence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration.evidence_consensus import (
    PROVENANCE_DIMENSIONS,
)

TRUSTED_PROVENANCE_BINDING_SCHEMA = "wd.trusted_provenance_binding.v1"
PROVENANCE_REGISTRY_SNAPSHOT_SCHEMA = "wd.provenance_registry_snapshot.v1"

TRUSTED_PROVENANCE_BINDING_DIGEST_DOMAIN = (
    "wd.trusted_provenance_binding.digest.v1"
)
PROVENANCE_REGISTRY_HEAD_DIGEST_DOMAIN = (
    "wd.provenance_registry_snapshot.head.digest.v1"
)

INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST = "sha256:" + "0" * 64
MAX_PROVENANCE_BINDINGS = 4096
MAX_REGISTRY_GENERATION = (1 << 63) - 1
PROVENANCE_BINDING_STATUSES = frozenset({"active", "revoked"})

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

_IDENTITY_FIELDS = (
    "signer_cell_id",
    "reviewer_activation_scope_digest",
    "signing_key_digest",
)

_NO_AUTHORITY_FLAGS = {
    "advisory_only": True,
    "authority_granted": False,
    "activation_performed": False,
    "routing_influence_applied": False,
}

TRUSTED_PROVENANCE_BINDING_CORE_KEYS = frozenset(
    {
        "schema_version",
        *_IDENTITY_FIELDS,
        *PROVENANCE_DIMENSIONS,
        "status",
        *_NO_AUTHORITY_FLAGS,
    }
)
TRUSTED_PROVENANCE_BINDING_KEYS = (
    TRUSTED_PROVENANCE_BINDING_CORE_KEYS | {"binding_digest"}
)

PROVENANCE_REGISTRY_SNAPSHOT_CORE_KEYS = frozenset(
    {
        "schema_version",
        "generation",
        "previous_registry_head_digest",
        "bindings",
        *_NO_AUTHORITY_FLAGS,
    }
)
PROVENANCE_REGISTRY_SNAPSHOT_KEYS = (
    PROVENANCE_REGISTRY_SNAPSHOT_CORE_KEYS | {"registry_head_digest"}
)

_IMMUTABLE_BINDING_FIELDS = (
    "schema_version",
    *_IDENTITY_FIELDS,
    *PROVENANCE_DIMENSIONS,
    *_NO_AUTHORITY_FLAGS,
)


class ProvenanceRegistryError(ValueError):
    """A value violates the pure trusted-provenance registry contract."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ProvenanceResolutionError(ProvenanceRegistryError):
    """A typed fail-closed trusted-provenance resolution refusal."""


def _refuse(reason: str, message: str) -> None:
    raise ProvenanceRegistryError(reason, message)


def _wire_dict(value: object, label: str, *, exact_keys: int) -> dict:
    if type(value) is not dict:
        _refuse("not_mapping", f"{label} must be an exact dict")
    if dict.__len__(value) > exact_keys:
        _refuse(f"{label}_keyset", f"{label} keyset exceeds its exact bound")
    snapshot = value.copy()
    if dict.__len__(snapshot) > exact_keys:
        _refuse(f"{label}_keyset", f"{label} keyset exceeds its exact bound")
    if any(type(key) is not str for key in snapshot):
        _refuse("not_mapping", f"{label} keys must be exact strings")
    return snapshot


def _wire_list(value: object, label: str, *, maximum: int) -> list:
    if type(value) is not list:
        _refuse("not_list", f"{label} must be an exact list")
    if list.__len__(value) > maximum:
        _refuse("binding_count", f"{label} exceeds its {maximum}-item bound")
    snapshot = value[: maximum + 1]
    if list.__len__(snapshot) > maximum:
        _refuse("binding_count", f"{label} exceeds its {maximum}-item bound")
    return snapshot


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a sha256:<64 lowercase hex> digest")
    return value


def _generation(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_REGISTRY_GENERATION:
        _refuse(
            "generation",
            "generation must be an exact integer within "
            f"0..{MAX_REGISTRY_GENERATION}",
        )
    return value


def _status(value: object) -> str:
    if type(value) is not str or value not in PROVENANCE_BINDING_STATUSES:
        _refuse("status", "status must be exactly 'active' or 'revoked'")
    return value


def _validate_no_authority_flags(value: dict, label: str) -> None:
    for field, required in _NO_AUTHORITY_FLAGS.items():
        actual = value[field]
        if type(actual) is not bool or actual is not required:
            _refuse(
                "authority_flags",
                f"{label}.{field} must remain {required!r}",
            )


def _validated_binding_fields(
    *,
    signer_cell_id: object,
    reviewer_activation_scope_digest: object,
    signing_key_digest: object,
    reviewer_lineage_digest: object,
    model_digest: object,
    provider_digest: object,
    tool_digest: object,
    data_corpus_digest: object,
    host_digest: object,
    review_policy_digest: object,
) -> dict[str, str]:
    raw = {
        "signer_cell_id": signer_cell_id,
        "reviewer_activation_scope_digest": reviewer_activation_scope_digest,
        "signing_key_digest": signing_key_digest,
        "reviewer_lineage_digest": reviewer_lineage_digest,
        "model_digest": model_digest,
        "provider_digest": provider_digest,
        "tool_digest": tool_digest,
        "data_corpus_digest": data_corpus_digest,
        "host_digest": host_digest,
        "review_policy_digest": review_policy_digest,
    }
    return {name: _digest(raw[name], name) for name in raw}


def derive_trusted_provenance_binding_digest(
    *,
    signer_cell_id: str,
    reviewer_activation_scope_digest: str,
    signing_key_digest: str,
    reviewer_lineage_digest: str,
    model_digest: str,
    provider_digest: str,
    tool_digest: str,
    data_corpus_digest: str,
    host_digest: str,
    review_policy_digest: str,
    status: str,
) -> str:
    """Content-address one exact key-to-provenance binding."""

    fields = _validated_binding_fields(
        signer_cell_id=signer_cell_id,
        reviewer_activation_scope_digest=reviewer_activation_scope_digest,
        signing_key_digest=signing_key_digest,
        reviewer_lineage_digest=reviewer_lineage_digest,
        model_digest=model_digest,
        provider_digest=provider_digest,
        tool_digest=tool_digest,
        data_corpus_digest=data_corpus_digest,
        host_digest=host_digest,
        review_policy_digest=review_policy_digest,
    )
    verified_status = _status(status)
    return sha256_digest(
        {
            "domain": TRUSTED_PROVENANCE_BINDING_DIGEST_DOMAIN,
            "schema_version": TRUSTED_PROVENANCE_BINDING_SCHEMA,
            **fields,
            "status": verified_status,
            **_NO_AUTHORITY_FLAGS,
        }
    )


@dataclass(frozen=True)
class TrustedProvenanceBindingV1:
    """One immutable key-to-reviewer binding with a one-way status."""

    signer_cell_id: str
    reviewer_activation_scope_digest: str
    signing_key_digest: str
    reviewer_lineage_digest: str
    model_digest: str
    provider_digest: str
    tool_digest: str
    data_corpus_digest: str
    host_digest: str
    review_policy_digest: str
    status: str
    binding_digest: str
    advisory_only: bool = True
    authority_granted: bool = False
    activation_performed: bool = False
    routing_influence_applied: bool = False
    schema_version: str = TRUSTED_PROVENANCE_BINDING_SCHEMA

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not str
            or self.schema_version != TRUSTED_PROVENANCE_BINDING_SCHEMA
        ):
            _refuse("binding_schema_version", "binding schema_version refused")
        flags = {name: getattr(self, name) for name in _NO_AUTHORITY_FLAGS}
        _validate_no_authority_flags(flags, "binding")
        _digest(self.binding_digest, "binding_digest")
        expected = derive_trusted_provenance_binding_digest(
            **{
                name: getattr(self, name)
                for name in (*_IDENTITY_FIELDS, *PROVENANCE_DIMENSIONS)
            },
            status=self.status,
        )
        if self.binding_digest != expected:
            _refuse("binding_digest", "binding_digest mismatch")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            **{name: getattr(self, name) for name in _IDENTITY_FIELDS},
            **{name: getattr(self, name) for name in PROVENANCE_DIMENSIONS},
            "status": self.status,
            **{name: getattr(self, name) for name in _NO_AUTHORITY_FLAGS},
            "binding_digest": self.binding_digest,
        }


def build_trusted_provenance_binding(
    *,
    signer_cell_id: str,
    reviewer_activation_scope_digest: str,
    signing_key_digest: str,
    reviewer_lineage_digest: str,
    model_digest: str,
    provider_digest: str,
    tool_digest: str,
    data_corpus_digest: str,
    host_digest: str,
    review_policy_digest: str,
    status: str = "active",
) -> TrustedProvenanceBindingV1:
    """Build a validated, content-addressed trusted-provenance binding."""

    fields = _validated_binding_fields(
        signer_cell_id=signer_cell_id,
        reviewer_activation_scope_digest=reviewer_activation_scope_digest,
        signing_key_digest=signing_key_digest,
        reviewer_lineage_digest=reviewer_lineage_digest,
        model_digest=model_digest,
        provider_digest=provider_digest,
        tool_digest=tool_digest,
        data_corpus_digest=data_corpus_digest,
        host_digest=host_digest,
        review_policy_digest=review_policy_digest,
    )
    verified_status = _status(status)
    return TrustedProvenanceBindingV1(
        **fields,
        status=verified_status,
        binding_digest=derive_trusted_provenance_binding_digest(
            **fields,
            status=verified_status,
        ),
    )


def parse_trusted_provenance_binding(value: object) -> dict[str, object]:
    binding = _wire_dict(
        value,
        "binding",
        exact_keys=len(TRUSTED_PROVENANCE_BINDING_KEYS),
    )
    if set(binding) != TRUSTED_PROVENANCE_BINDING_KEYS:
        _refuse("binding_keyset", "binding has a non-exact keyset")
    if (
        type(binding["schema_version"]) is not str
        or binding["schema_version"] != TRUSTED_PROVENANCE_BINDING_SCHEMA
    ):
        _refuse("binding_schema_version", "binding schema_version refused")
    fields = {
        name: _digest(binding[name], f"binding.{name}")
        for name in (*_IDENTITY_FIELDS, *PROVENANCE_DIMENSIONS)
    }
    verified_status = _status(binding["status"])
    _validate_no_authority_flags(binding, "binding")
    expected = derive_trusted_provenance_binding_digest(
        **fields,
        status=verified_status,
    )
    if _digest(binding["binding_digest"], "binding.binding_digest") != expected:
        _refuse("binding_digest", "binding_digest mismatch")
    return {
        "schema_version": TRUSTED_PROVENANCE_BINDING_SCHEMA,
        **fields,
        "status": verified_status,
        **_NO_AUTHORITY_FLAGS,
        "binding_digest": expected,
    }


def verify_trusted_provenance_binding(
    value: object,
) -> tuple[bool, Optional[str]]:
    try:
        parse_trusted_provenance_binding(value)
    except ProvenanceRegistryError as exc:
        return False, exc.reason
    return True, None


def _binding_sort_key(binding: dict[str, object]) -> tuple[str, str]:
    return (
        str(binding["signing_key_digest"]),
        str(binding["binding_digest"]),
    )


def _validate_binding_set(bindings: list[dict[str, object]]) -> None:
    seen_keys: set[str] = set()
    seen_digests: set[str] = set()
    cell_scope_facts: dict[tuple[str, str], tuple[str, ...]] = {}

    for binding in bindings:
        signing_key = str(binding["signing_key_digest"])
        binding_digest = str(binding["binding_digest"])
        if signing_key in seen_keys:
            _refuse(
                "duplicate_signing_key",
                "a signing_key_digest may occur only once in one snapshot",
            )
        if binding_digest in seen_digests:
            _refuse(
                "duplicate_binding",
                "a binding_digest may occur only once in one snapshot",
            )
        seen_keys.add(signing_key)
        seen_digests.add(binding_digest)

        cell_scope = (
            str(binding["signer_cell_id"]),
            str(binding["reviewer_activation_scope_digest"]),
        )
        facts = tuple(str(binding[field]) for field in PROVENANCE_DIMENSIONS)
        prior_facts = cell_scope_facts.setdefault(cell_scope, facts)
        if prior_facts != facts:
            _refuse(
                "cell_scope_provenance_conflict",
                "rotated keys for one cell/scope must retain identical "
                "lineage and provenance",
            )


def _coerce_binding_for_build(value: object) -> dict[str, object]:
    if type(value) is TrustedProvenanceBindingV1:
        value = value.to_mapping()
    return parse_trusted_provenance_binding(value)


def _validated_snapshot_core(
    *,
    generation: object,
    previous_registry_head_digest: object,
    bindings: object,
    require_canonical_order: bool,
    allow_binding_values: bool,
) -> tuple[int, str, list[dict[str, object]]]:
    verified_generation = _generation(generation)
    previous = _digest(
        previous_registry_head_digest,
        "previous_registry_head_digest",
    )
    if verified_generation == 0 and previous != INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST:
        _refuse(
            "initial_predecessor",
            "generation zero must use the initial predecessor sentinel",
        )
    if verified_generation > 0 and previous == INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST:
        _refuse(
            "non_initial_predecessor",
            "non-zero generation may not use the initial predecessor sentinel",
        )

    items = _wire_list(bindings, "bindings", maximum=MAX_PROVENANCE_BINDINGS)
    if allow_binding_values:
        parsed = [_coerce_binding_for_build(item) for item in items]
    else:
        parsed = [parse_trusted_provenance_binding(item) for item in items]
    canonical = sorted(parsed, key=_binding_sort_key)
    if require_canonical_order and parsed != canonical:
        _refuse(
            "binding_order",
            "bindings must be sorted by the full signing_key_digest",
        )
    parsed = canonical
    _validate_binding_set(parsed)
    return verified_generation, previous, parsed


def _derive_registry_head_from_validated(
    *,
    generation: int,
    previous_registry_head_digest: str,
    bindings: list[dict[str, object]],
) -> str:
    return sha256_digest(
        {
            "domain": PROVENANCE_REGISTRY_HEAD_DIGEST_DOMAIN,
            "schema_version": PROVENANCE_REGISTRY_SNAPSHOT_SCHEMA,
            "generation": generation,
            "previous_registry_head_digest": previous_registry_head_digest,
            "bindings": bindings,
            **_NO_AUTHORITY_FLAGS,
        }
    )


def derive_provenance_registry_head_digest(
    *,
    generation: int,
    previous_registry_head_digest: str,
    bindings: object,
) -> str:
    """Derive a snapshot head after canonicalizing exact validated bindings."""

    verified_generation, previous, parsed = _validated_snapshot_core(
        generation=generation,
        previous_registry_head_digest=previous_registry_head_digest,
        bindings=bindings,
        require_canonical_order=False,
        allow_binding_values=True,
    )
    return _derive_registry_head_from_validated(
        generation=verified_generation,
        previous_registry_head_digest=previous,
        bindings=parsed,
    )


@dataclass(frozen=True)
class ProvenanceRegistrySnapshotV1:
    """A deterministic immutable provenance registry generation."""

    generation: int
    previous_registry_head_digest: str
    bindings: tuple[TrustedProvenanceBindingV1, ...]
    registry_head_digest: str
    advisory_only: bool = True
    authority_granted: bool = False
    activation_performed: bool = False
    routing_influence_applied: bool = False
    schema_version: str = PROVENANCE_REGISTRY_SNAPSHOT_SCHEMA

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not str
            or self.schema_version != PROVENANCE_REGISTRY_SNAPSHOT_SCHEMA
        ):
            _refuse("snapshot_schema_version", "snapshot schema_version refused")
        flags = {name: getattr(self, name) for name in _NO_AUTHORITY_FLAGS}
        _validate_no_authority_flags(flags, "snapshot")
        if type(self.bindings) is not tuple:
            _refuse("not_tuple", "snapshot dataclass bindings must be an exact tuple")
        if tuple.__len__(self.bindings) > MAX_PROVENANCE_BINDINGS:
            _refuse("binding_count", "snapshot binding count exceeds its bound")
        if any(type(item) is not TrustedProvenanceBindingV1 for item in self.bindings):
            _refuse(
                "binding_type",
                "snapshot dataclass bindings must contain exact binding values",
            )
        mappings = [item.to_mapping() for item in self.bindings]
        verified_generation, previous, parsed = _validated_snapshot_core(
            generation=self.generation,
            previous_registry_head_digest=self.previous_registry_head_digest,
            bindings=mappings,
            require_canonical_order=True,
            allow_binding_values=False,
        )
        expected = _derive_registry_head_from_validated(
            generation=verified_generation,
            previous_registry_head_digest=previous,
            bindings=parsed,
        )
        if _digest(self.registry_head_digest, "registry_head_digest") != expected:
            _refuse("registry_head_digest", "registry_head_digest mismatch")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "previous_registry_head_digest": self.previous_registry_head_digest,
            "bindings": [item.to_mapping() for item in self.bindings],
            **{name: getattr(self, name) for name in _NO_AUTHORITY_FLAGS},
            "registry_head_digest": self.registry_head_digest,
        }


def _binding_from_mapping(
    binding: dict[str, object],
) -> TrustedProvenanceBindingV1:
    return TrustedProvenanceBindingV1(
        **{
            name: binding[name]
            for name in (*_IDENTITY_FIELDS, *PROVENANCE_DIMENSIONS)
        },
        status=binding["status"],
        binding_digest=binding["binding_digest"],
    )


def build_provenance_registry_snapshot(
    *,
    generation: int,
    bindings: object,
    previous_registry_head_digest: str = INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
) -> ProvenanceRegistrySnapshotV1:
    """Build one canonical snapshot; input order has no digest significance."""

    verified_generation, previous, parsed = _validated_snapshot_core(
        generation=generation,
        previous_registry_head_digest=previous_registry_head_digest,
        bindings=bindings,
        require_canonical_order=False,
        allow_binding_values=True,
    )
    return ProvenanceRegistrySnapshotV1(
        generation=verified_generation,
        previous_registry_head_digest=previous,
        bindings=tuple(_binding_from_mapping(item) for item in parsed),
        registry_head_digest=_derive_registry_head_from_validated(
            generation=verified_generation,
            previous_registry_head_digest=previous,
            bindings=parsed,
        ),
    )


def parse_provenance_registry_snapshot(value: object) -> dict[str, object]:
    snapshot = _wire_dict(
        value,
        "snapshot",
        exact_keys=len(PROVENANCE_REGISTRY_SNAPSHOT_KEYS),
    )
    if set(snapshot) != PROVENANCE_REGISTRY_SNAPSHOT_KEYS:
        _refuse("snapshot_keyset", "snapshot has a non-exact keyset")
    if (
        type(snapshot["schema_version"]) is not str
        or snapshot["schema_version"] != PROVENANCE_REGISTRY_SNAPSHOT_SCHEMA
    ):
        _refuse("snapshot_schema_version", "snapshot schema_version refused")
    _validate_no_authority_flags(snapshot, "snapshot")
    generation, previous, bindings = _validated_snapshot_core(
        generation=snapshot["generation"],
        previous_registry_head_digest=snapshot["previous_registry_head_digest"],
        bindings=snapshot["bindings"],
        require_canonical_order=True,
        allow_binding_values=False,
    )
    expected = _derive_registry_head_from_validated(
        generation=generation,
        previous_registry_head_digest=previous,
        bindings=bindings,
    )
    if _digest(
        snapshot["registry_head_digest"],
        "snapshot.registry_head_digest",
    ) != expected:
        _refuse("registry_head_digest", "registry_head_digest mismatch")
    return {
        "schema_version": PROVENANCE_REGISTRY_SNAPSHOT_SCHEMA,
        "generation": generation,
        "previous_registry_head_digest": previous,
        "bindings": [dict(binding) for binding in bindings],
        **_NO_AUTHORITY_FLAGS,
        "registry_head_digest": expected,
    }


def verify_provenance_registry_snapshot(
    value: object,
) -> tuple[bool, Optional[str]]:
    try:
        parse_provenance_registry_snapshot(value)
    except ProvenanceRegistryError as exc:
        return False, exc.reason
    return True, None


def verify_provenance_registry_transition(
    current: object,
    proposed: object,
    *,
    expected_current_registry_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify one append-only generation before an external atomic CAS."""

    try:
        expected_current = _digest(
            expected_current_registry_head_digest,
            "expected_current_registry_head_digest",
        )
        parsed_current = parse_provenance_registry_snapshot(current)
    except ProvenanceRegistryError as exc:
        return False, exc.reason
    if parsed_current["registry_head_digest"] != expected_current:
        return False, "stale_current_registry_head"
    try:
        parsed_proposed = parse_provenance_registry_snapshot(proposed)
    except ProvenanceRegistryError as exc:
        return False, exc.reason

    current_generation = int(parsed_current["generation"])
    if current_generation == MAX_REGISTRY_GENERATION:
        return False, "generation_exhausted"
    if parsed_proposed["generation"] != current_generation + 1:
        return False, "generation_step"
    if (
        parsed_proposed["previous_registry_head_digest"]
        != parsed_current["registry_head_digest"]
    ):
        return False, "previous_registry_head_binding"

    current_by_key = {
        binding["signing_key_digest"]: binding
        for binding in parsed_current["bindings"]
    }
    proposed_by_key = {
        binding["signing_key_digest"]: binding
        for binding in parsed_proposed["bindings"]
    }
    for signing_key, before in current_by_key.items():
        after = proposed_by_key.get(signing_key)
        if after is None:
            return False, "binding_removed"
        if any(after[field] != before[field] for field in _IMMUTABLE_BINDING_FIELDS):
            return False, "binding_facts_changed"
        if before["status"] == "revoked" and after["status"] != "revoked":
            return False, "revocation_irreversible"
        if before["status"] == after["status"]:
            if before["binding_digest"] != after["binding_digest"]:
                return False, "binding_digest_changed"
        elif not (before["status"] == "active" and after["status"] == "revoked"):
            return False, "status_transition"
    return True, None


def resolve_trusted_provenance(
    snapshot: object,
    signing_key_digest: str,
    expected_registry_head_digest: str,
) -> dict[str, object]:
    """Resolve one active binding from an externally pinned registry head.

    The returned dict is a private copy.  A missing, revoked, malformed, or
    stale-head lookup raises :class:`ProvenanceResolutionError`; no fallback or
    partial provenance is returned.
    """

    try:
        key = _digest(signing_key_digest, "signing_key_digest")
    except ProvenanceRegistryError as exc:
        raise ProvenanceResolutionError(
            "invalid_signing_key_digest", str(exc)
        ) from exc
    try:
        expected_head = _digest(
            expected_registry_head_digest,
            "expected_registry_head_digest",
        )
    except ProvenanceRegistryError as exc:
        raise ProvenanceResolutionError(
            "invalid_expected_registry_head_digest", str(exc)
        ) from exc
    try:
        parsed = parse_provenance_registry_snapshot(snapshot)
    except ProvenanceRegistryError as exc:
        raise ProvenanceResolutionError(
            "invalid_registry_snapshot",
            f"registry snapshot refused: {exc.reason}",
        ) from exc
    if parsed["registry_head_digest"] != expected_head:
        raise ProvenanceResolutionError(
            "registry_head_mismatch",
            "registry snapshot does not match the expected pinned head",
        )
    for binding in parsed["bindings"]:
        if binding["signing_key_digest"] != key:
            continue
        if binding["status"] != "active":
            raise ProvenanceResolutionError(
                "signing_key_revoked",
                "trusted provenance binding is revoked",
            )
        return dict(binding)
    raise ProvenanceResolutionError(
        "signing_key_not_found",
        "signing key digest has no trusted provenance binding",
    )
