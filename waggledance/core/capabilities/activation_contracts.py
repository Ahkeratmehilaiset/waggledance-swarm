# SPDX-License-Identifier: BUSL-1.1
"""Authority-free capability expression and activation contracts.

These contracts describe *which immutable variants are selected* under an
exact set of context heads.  They do not grant permission to execute a
variant.  In particular, an :class:`AuthorityCeilingV1` is only an upper-bound
constraint: possession of it, or of its digest, is never an authorization.
Actual grants remain in the separately authenticated charter/policy layer.

The module is deliberately pure: no clock, I/O, randomness, registry lookup,
or ambient profile ordering.  Every wire verifier requires an exact JSON
object shape and recomputes its domain-separated digest.

``ActivationHeadV1`` is suitable for an external compare-and-swap store.  The
pure transition verifier proves a one-step, predecessor-bound transition, but
cannot make an external read/write atomic; the adapter must compare the same
``expected_current_head_digest`` in its own transaction.  A rollback therefore
means publishing an older *selection* in a new generation, never reusing an
old head or generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Optional

from waggledance.core.magma.canonical import sha256_digest

AUTHORITY_CEILING_SCHEMA = "wd.authority_ceiling.v1"
CAPABILITY_VARIANT_SCHEMA = "wd.capability_variant.v1"
EXPRESSION_CONTEXT_SCHEMA = "wd.expression_context.v1"
ACTIVATION_HEAD_SCHEMA = "wd.activation_head.v1"

AUTHORITY_CEILING_DIGEST_DOMAIN = "wd.authority_ceiling.digest.v1"
CAPABILITY_VARIANT_DIGEST_DOMAIN = "wd.capability_variant.digest.v1"
EXPRESSION_CONTEXT_DIGEST_DOMAIN = "wd.expression_context.digest.v1"
ACTIVATION_HEAD_DIGEST_DOMAIN = "wd.activation_head.digest.v1"

# Generation zero has no predecessor.  All later generations must bind a real
# head digest, never this sentinel.
INITIAL_PREVIOUS_HEAD_DIGEST = "sha256:" + "0" * 64
MAX_GENERATION = (1 << 63) - 1
MAX_AUTHORITY_SCOPES = 256
MAX_VARIANTS_PER_SET = 4_096
MAX_FAMILY_ID_LENGTH = 128

# This is the existing WD write-risk order.  The ordering is explicit contract
# data; no profile name or deployment size is treated as a risk hierarchy.
RISK_RANK = MappingProxyType(
    {
        "informational": 0,
        "internal_memory": 1,
        "local_artifact": 2,
        "external_effect": 3,
    }
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FAMILY_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")

AUTHORITY_CEILING_KEYS = frozenset(
    {
        "schema_version",
        "ceiling_digest",
        "max_risk_class",
        "authority_scope_digests",
    }
)
CAPABILITY_VARIANT_KEYS = frozenset(
    {
        "schema_version",
        "variant_digest",
        "family_id",
        "risk_class",
        "artifact_digest",
        "input_schema_digest",
        "output_schema_digest",
        "compatibility_digest",
        "authority_ceiling_digest",
    }
)
EXPRESSION_CONTEXT_KEYS = frozenset(
    {
        "schema_version",
        "context_digest",
        "profile_head_digest",
        "policy_head_digest",
        "resource_head_digest",
        "domain_head_digest",
        "environment_head_digest",
        "charter_ceiling_digest",
        "expressed_ceiling_digest",
    }
)
ACTIVATION_HEAD_KEYS = frozenset(
    {
        "schema_version",
        "head_digest",
        "generation",
        "previous_head_digest",
        "expression_context_digest",
        "active_variant_digests",
        "shadow_variant_digests",
    }
)


class CapabilityActivationContractError(ValueError):
    """A value is outside one of the capability activation contracts."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise CapabilityActivationContractError(reason, message)


def _wire_snapshot(value: object, keys: frozenset[str]) -> dict:
    # Exact built-ins keep attacker-controlled Mapping/dict-subclass methods
    # outside the verification boundary.  The caller owns input quiescence.
    if type(value) is not dict:
        _refuse("not_mapping", "wire value must be an exact dict")
    # Exact built-in len is non-hostile.  Bound before copying so an oversized
    # decoded object cannot force an avoidable second allocation.
    if len(value) != len(keys):
        _refuse("keyset", "wire value has a non-exact keyset")
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        _refuse("not_mapping", "wire keys must be exact strings")
    if set(snapshot) != keys:
        _refuse("keyset", "wire value has a non-exact keyset")
    return snapshot


def _require_schema(value: object, expected: str) -> str:
    if type(value) is not str or value != expected:
        _refuse("schema_version", f"schema_version must be {expected!r}")
    return value


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 71 or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a sha256:<64 lowercase hex> digest")
    return value


def _require_risk(value: object, label: str) -> str:
    if type(value) is not str or len(value) > 32 or value not in RISK_RANK:
        _refuse(label, f"{label} must use the pinned WD risk taxonomy")
    return value


def _require_family_id(value: object) -> str:
    if (
        type(value) is not str
        or len(value) > MAX_FAMILY_ID_LENGTH
        or not _FAMILY_ID.fullmatch(value)
    ):
        _refuse("family_id", "family_id must be a bounded canonical ASCII id")
    return value


def _digest_set(
    value: object,
    label: str,
    *,
    limit: int,
    normalize: bool,
    container_types: tuple[type, ...] = (list, tuple),
) -> tuple[str, ...]:
    if type(value) not in container_types:
        names = " or ".join(item.__name__ for item in container_types)
        _refuse(label, f"{label} must be an exact {names}")
    # Exact list/tuple len is safe and precedes the immutable snapshot copy.
    if len(value) > limit:
        _refuse(label, f"{label} exceeds its {limit}-item bound")
    snapshot = tuple(value)
    for item in snapshot:
        _require_digest(item, label)
    if len(set(snapshot)) != len(snapshot):
        _refuse(f"{label}_duplicate", f"{label} contains a duplicate")
    ordered = tuple(sorted(snapshot))
    if not normalize and snapshot != ordered:
        _refuse(f"{label}_order", f"{label} must be in canonical sorted order")
    return ordered


def _bounded_contract_sequence(
    value: object, label: str, *, limit: int
) -> tuple[object, ...]:
    """Snapshot a caller-owned relation aggregate after a cheap exact bound."""

    if type(value) not in (list, tuple):
        _refuse(label, f"{label} must be an exact list or tuple")
    if len(value) > limit:
        _refuse(label, f"{label} exceeds its {limit}-item bound")
    return tuple(value)


def _require_generation(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        _refuse(
            "generation", f"generation must be an integer within 0..{MAX_GENERATION}"
        )
    return value


def derive_authority_ceiling_digest(
    *, max_risk_class: str, authority_scope_digests: object
) -> str:
    """Derive an upper-bound constraint digest; this never creates a grant."""

    risk = _require_risk(max_risk_class, "max_risk_class")
    scopes = _digest_set(
        authority_scope_digests,
        "authority_scope_digests",
        limit=MAX_AUTHORITY_SCOPES,
        normalize=True,
    )
    return sha256_digest(
        {
            "domain": AUTHORITY_CEILING_DIGEST_DOMAIN,
            "schema_version": AUTHORITY_CEILING_SCHEMA,
            "max_risk_class": risk,
            "authority_scope_digests": list(scopes),
        }
    )


@dataclass(frozen=True)
class AuthorityCeilingV1:
    """A content-addressed maximum constraint, explicitly not a permission."""

    ceiling_digest: str
    max_risk_class: str
    authority_scope_digests: tuple[str, ...]
    schema_version: str = AUTHORITY_CEILING_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, AUTHORITY_CEILING_SCHEMA)
        _require_digest(self.ceiling_digest, "ceiling_digest")
        _require_risk(self.max_risk_class, "max_risk_class")
        scopes = _digest_set(
            self.authority_scope_digests,
            "authority_scope_digests",
            limit=MAX_AUTHORITY_SCOPES,
            normalize=False,
            container_types=(tuple,),
        )
        expected = derive_authority_ceiling_digest(
            max_risk_class=self.max_risk_class,
            authority_scope_digests=scopes,
        )
        if self.ceiling_digest != expected:
            _refuse("ceiling_digest_mismatch", "ceiling_digest does not match content")

    def to_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "ceiling_digest": self.ceiling_digest,
            "max_risk_class": self.max_risk_class,
            "authority_scope_digests": list(self.authority_scope_digests),
        }


def build_authority_ceiling(
    *, max_risk_class: str, authority_scope_digests: object
) -> AuthorityCeilingV1:
    scopes = _digest_set(
        authority_scope_digests,
        "authority_scope_digests",
        limit=MAX_AUTHORITY_SCOPES,
        normalize=True,
    )
    return AuthorityCeilingV1(
        ceiling_digest=derive_authority_ceiling_digest(
            max_risk_class=max_risk_class,
            authority_scope_digests=scopes,
        ),
        max_risk_class=max_risk_class,
        authority_scope_digests=scopes,
    )


def _parse_authority_ceiling(value: object) -> AuthorityCeilingV1:
    if type(value) is AuthorityCeilingV1:
        # Revalidate even an exact frozen instance: frozen dataclasses are an
        # ergonomic guardrail, not a trust boundary (object.__setattr__ exists).
        try:
            return AuthorityCeilingV1(
                schema_version=value.schema_version,
                ceiling_digest=value.ceiling_digest,
                max_risk_class=value.max_risk_class,
                authority_scope_digests=value.authority_scope_digests,
            )
        except AttributeError:
            _refuse("malformed_instance", "authority ceiling instance is malformed")
    snapshot = _wire_snapshot(value, AUTHORITY_CEILING_KEYS)
    return AuthorityCeilingV1(
        schema_version=snapshot["schema_version"],
        ceiling_digest=snapshot["ceiling_digest"],
        max_risk_class=snapshot["max_risk_class"],
        authority_scope_digests=_digest_set(
            snapshot["authority_scope_digests"],
            "authority_scope_digests",
            limit=MAX_AUTHORITY_SCOPES,
            normalize=False,
            container_types=(list,),
        ),
    )


def verify_authority_ceiling(value: object) -> tuple[bool, Optional[str]]:
    try:
        _parse_authority_ceiling(value)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    return True, None


def verify_ceiling_narrowing(
    parent: object, candidate: object
) -> tuple[bool, Optional[str]]:
    """Prove ``candidate`` is a subset/lower-or-equal constraint of ``parent``."""

    try:
        parent_ceiling = _parse_authority_ceiling(parent)
        candidate_ceiling = _parse_authority_ceiling(candidate)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    if RISK_RANK[candidate_ceiling.max_risk_class] > RISK_RANK[
        parent_ceiling.max_risk_class
    ]:
        return False, "risk_widening"
    if not set(candidate_ceiling.authority_scope_digests).issubset(
        parent_ceiling.authority_scope_digests
    ):
        return False, "scope_widening"
    return True, None


def derive_capability_variant_digest(
    *,
    family_id: str,
    risk_class: str,
    artifact_digest: str,
    input_schema_digest: str,
    output_schema_digest: str,
    compatibility_digest: str,
    authority_ceiling_digest: str,
) -> str:
    family = _require_family_id(family_id)
    risk = _require_risk(risk_class, "risk_class")
    fields = {
        "artifact_digest": _require_digest(artifact_digest, "artifact_digest"),
        "input_schema_digest": _require_digest(
            input_schema_digest, "input_schema_digest"
        ),
        "output_schema_digest": _require_digest(
            output_schema_digest, "output_schema_digest"
        ),
        "compatibility_digest": _require_digest(
            compatibility_digest, "compatibility_digest"
        ),
        "authority_ceiling_digest": _require_digest(
            authority_ceiling_digest, "authority_ceiling_digest"
        ),
    }
    return sha256_digest(
        {
            "domain": CAPABILITY_VARIANT_DIGEST_DOMAIN,
            "schema_version": CAPABILITY_VARIANT_SCHEMA,
            "family_id": family,
            "risk_class": risk,
            **fields,
        }
    )


@dataclass(frozen=True)
class CapabilityVariantV1:
    """Immutable implementation identity with an authority *upper bound*."""

    variant_digest: str
    family_id: str
    risk_class: str
    artifact_digest: str
    input_schema_digest: str
    output_schema_digest: str
    compatibility_digest: str
    authority_ceiling_digest: str
    schema_version: str = CAPABILITY_VARIANT_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, CAPABILITY_VARIANT_SCHEMA)
        _require_digest(self.variant_digest, "variant_digest")
        expected = derive_capability_variant_digest(
            family_id=self.family_id,
            risk_class=self.risk_class,
            artifact_digest=self.artifact_digest,
            input_schema_digest=self.input_schema_digest,
            output_schema_digest=self.output_schema_digest,
            compatibility_digest=self.compatibility_digest,
            authority_ceiling_digest=self.authority_ceiling_digest,
        )
        if self.variant_digest != expected:
            _refuse("variant_digest_mismatch", "variant_digest does not match content")

    def to_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "variant_digest": self.variant_digest,
            "family_id": self.family_id,
            "risk_class": self.risk_class,
            "artifact_digest": self.artifact_digest,
            "input_schema_digest": self.input_schema_digest,
            "output_schema_digest": self.output_schema_digest,
            "compatibility_digest": self.compatibility_digest,
            "authority_ceiling_digest": self.authority_ceiling_digest,
        }


def build_capability_variant(
    *,
    family_id: str,
    risk_class: str,
    artifact_digest: str,
    input_schema_digest: str,
    output_schema_digest: str,
    compatibility_digest: str,
    authority_ceiling_digest: str,
) -> CapabilityVariantV1:
    return CapabilityVariantV1(
        variant_digest=derive_capability_variant_digest(
            family_id=family_id,
            risk_class=risk_class,
            artifact_digest=artifact_digest,
            input_schema_digest=input_schema_digest,
            output_schema_digest=output_schema_digest,
            compatibility_digest=compatibility_digest,
            authority_ceiling_digest=authority_ceiling_digest,
        ),
        family_id=family_id,
        risk_class=risk_class,
        artifact_digest=artifact_digest,
        input_schema_digest=input_schema_digest,
        output_schema_digest=output_schema_digest,
        compatibility_digest=compatibility_digest,
        authority_ceiling_digest=authority_ceiling_digest,
    )


def _parse_capability_variant(value: object) -> CapabilityVariantV1:
    if type(value) is CapabilityVariantV1:
        try:
            return CapabilityVariantV1(
                schema_version=value.schema_version,
                variant_digest=value.variant_digest,
                family_id=value.family_id,
                risk_class=value.risk_class,
                artifact_digest=value.artifact_digest,
                input_schema_digest=value.input_schema_digest,
                output_schema_digest=value.output_schema_digest,
                compatibility_digest=value.compatibility_digest,
                authority_ceiling_digest=value.authority_ceiling_digest,
            )
        except AttributeError:
            _refuse("malformed_instance", "capability variant instance is malformed")
    snapshot = _wire_snapshot(value, CAPABILITY_VARIANT_KEYS)
    return CapabilityVariantV1(
        schema_version=snapshot["schema_version"],
        variant_digest=snapshot["variant_digest"],
        family_id=snapshot["family_id"],
        risk_class=snapshot["risk_class"],
        artifact_digest=snapshot["artifact_digest"],
        input_schema_digest=snapshot["input_schema_digest"],
        output_schema_digest=snapshot["output_schema_digest"],
        compatibility_digest=snapshot["compatibility_digest"],
        authority_ceiling_digest=snapshot["authority_ceiling_digest"],
    )


def verify_capability_variant(value: object) -> tuple[bool, Optional[str]]:
    try:
        _parse_capability_variant(value)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    return True, None


def derive_expression_context_digest(
    *,
    profile_head_digest: str,
    policy_head_digest: str,
    resource_head_digest: str,
    domain_head_digest: str,
    environment_head_digest: str,
    charter_ceiling_digest: str,
    expressed_ceiling_digest: str,
) -> str:
    fields = {
        "profile_head_digest": _require_digest(
            profile_head_digest, "profile_head_digest"
        ),
        "policy_head_digest": _require_digest(
            policy_head_digest, "policy_head_digest"
        ),
        "resource_head_digest": _require_digest(
            resource_head_digest, "resource_head_digest"
        ),
        "domain_head_digest": _require_digest(
            domain_head_digest, "domain_head_digest"
        ),
        "environment_head_digest": _require_digest(
            environment_head_digest, "environment_head_digest"
        ),
        "charter_ceiling_digest": _require_digest(
            charter_ceiling_digest, "charter_ceiling_digest"
        ),
        "expressed_ceiling_digest": _require_digest(
            expressed_ceiling_digest, "expressed_ceiling_digest"
        ),
    }
    return sha256_digest(
        {
            "domain": EXPRESSION_CONTEXT_DIGEST_DOMAIN,
            "schema_version": EXPRESSION_CONTEXT_SCHEMA,
            **fields,
        }
    )


@dataclass(frozen=True)
class ExpressionContextV1:
    """Exact context heads plus ceiling constraints; contains no authority grant."""

    context_digest: str
    profile_head_digest: str
    policy_head_digest: str
    resource_head_digest: str
    domain_head_digest: str
    environment_head_digest: str
    charter_ceiling_digest: str
    expressed_ceiling_digest: str
    schema_version: str = EXPRESSION_CONTEXT_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, EXPRESSION_CONTEXT_SCHEMA)
        _require_digest(self.context_digest, "context_digest")
        expected = derive_expression_context_digest(
            profile_head_digest=self.profile_head_digest,
            policy_head_digest=self.policy_head_digest,
            resource_head_digest=self.resource_head_digest,
            domain_head_digest=self.domain_head_digest,
            environment_head_digest=self.environment_head_digest,
            charter_ceiling_digest=self.charter_ceiling_digest,
            expressed_ceiling_digest=self.expressed_ceiling_digest,
        )
        if self.context_digest != expected:
            _refuse("context_digest_mismatch", "context_digest does not match content")

    def to_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "context_digest": self.context_digest,
            "profile_head_digest": self.profile_head_digest,
            "policy_head_digest": self.policy_head_digest,
            "resource_head_digest": self.resource_head_digest,
            "domain_head_digest": self.domain_head_digest,
            "environment_head_digest": self.environment_head_digest,
            "charter_ceiling_digest": self.charter_ceiling_digest,
            "expressed_ceiling_digest": self.expressed_ceiling_digest,
        }


def build_expression_context(
    *,
    profile_head_digest: str,
    policy_head_digest: str,
    resource_head_digest: str,
    domain_head_digest: str,
    environment_head_digest: str,
    charter_ceiling_digest: str,
    expressed_ceiling_digest: str,
) -> ExpressionContextV1:
    return ExpressionContextV1(
        context_digest=derive_expression_context_digest(
            profile_head_digest=profile_head_digest,
            policy_head_digest=policy_head_digest,
            resource_head_digest=resource_head_digest,
            domain_head_digest=domain_head_digest,
            environment_head_digest=environment_head_digest,
            charter_ceiling_digest=charter_ceiling_digest,
            expressed_ceiling_digest=expressed_ceiling_digest,
        ),
        profile_head_digest=profile_head_digest,
        policy_head_digest=policy_head_digest,
        resource_head_digest=resource_head_digest,
        domain_head_digest=domain_head_digest,
        environment_head_digest=environment_head_digest,
        charter_ceiling_digest=charter_ceiling_digest,
        expressed_ceiling_digest=expressed_ceiling_digest,
    )


def _parse_expression_context(value: object) -> ExpressionContextV1:
    if type(value) is ExpressionContextV1:
        try:
            return ExpressionContextV1(
                schema_version=value.schema_version,
                context_digest=value.context_digest,
                profile_head_digest=value.profile_head_digest,
                policy_head_digest=value.policy_head_digest,
                resource_head_digest=value.resource_head_digest,
                domain_head_digest=value.domain_head_digest,
                environment_head_digest=value.environment_head_digest,
                charter_ceiling_digest=value.charter_ceiling_digest,
                expressed_ceiling_digest=value.expressed_ceiling_digest,
            )
        except AttributeError:
            _refuse("malformed_instance", "expression context instance is malformed")
    snapshot = _wire_snapshot(value, EXPRESSION_CONTEXT_KEYS)
    return ExpressionContextV1(
        schema_version=snapshot["schema_version"],
        context_digest=snapshot["context_digest"],
        profile_head_digest=snapshot["profile_head_digest"],
        policy_head_digest=snapshot["policy_head_digest"],
        resource_head_digest=snapshot["resource_head_digest"],
        domain_head_digest=snapshot["domain_head_digest"],
        environment_head_digest=snapshot["environment_head_digest"],
        charter_ceiling_digest=snapshot["charter_ceiling_digest"],
        expressed_ceiling_digest=snapshot["expressed_ceiling_digest"],
    )


def verify_expression_context(value: object) -> tuple[bool, Optional[str]]:
    try:
        _parse_expression_context(value)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    return True, None


def verify_expression_constraints(
    *,
    variant: object,
    context: object,
    variant_ceiling: object,
    charter_ceiling: object,
    expressed_ceiling: object,
) -> tuple[bool, Optional[str]]:
    """Verify expression only narrows both immutable upper bounds.

    The returned success is still not an execution grant.  It proves only that
    the selected constraint cannot exceed either the variant maximum or the
    separately supplied charter maximum.
    """

    try:
        parsed_variant = _parse_capability_variant(variant)
        parsed_context = _parse_expression_context(context)
        parsed_variant_ceiling = _parse_authority_ceiling(variant_ceiling)
        parsed_charter_ceiling = _parse_authority_ceiling(charter_ceiling)
        parsed_expressed_ceiling = _parse_authority_ceiling(expressed_ceiling)
    except CapabilityActivationContractError as exc:
        return False, exc.reason

    if parsed_variant.authority_ceiling_digest != (
        parsed_variant_ceiling.ceiling_digest
    ):
        return False, "variant_ceiling_binding"
    if parsed_context.charter_ceiling_digest != parsed_charter_ceiling.ceiling_digest:
        return False, "charter_ceiling_binding"
    if parsed_context.expressed_ceiling_digest != (
        parsed_expressed_ceiling.ceiling_digest
    ):
        return False, "expressed_ceiling_binding"

    for label, parent in (
        ("variant", parsed_variant_ceiling),
        ("charter", parsed_charter_ceiling),
    ):
        ok, reason = verify_ceiling_narrowing(parent, parsed_expressed_ceiling)
        if not ok:
            return False, f"{label}_{reason}"

    if RISK_RANK[parsed_variant.risk_class] > RISK_RANK[
        parsed_expressed_ceiling.max_risk_class
    ]:
        return False, "variant_risk_exceeds_expression"
    return True, None


def _validate_head_fields(
    *,
    generation: object,
    previous_head_digest: object,
    expression_context_digest: object,
    active_variant_digests: object,
    shadow_variant_digests: object,
    normalize: bool,
    container_types: tuple[type, ...] = (list, tuple),
) -> tuple[int, str, str, tuple[str, ...], tuple[str, ...]]:
    parsed_generation = _require_generation(generation)
    previous = _require_digest(previous_head_digest, "previous_head_digest")
    context = _require_digest(
        expression_context_digest, "expression_context_digest"
    )
    active = _digest_set(
        active_variant_digests,
        "active_variant_digests",
        limit=MAX_VARIANTS_PER_SET,
        normalize=normalize,
        container_types=container_types,
    )
    shadow = _digest_set(
        shadow_variant_digests,
        "shadow_variant_digests",
        limit=MAX_VARIANTS_PER_SET,
        normalize=normalize,
        container_types=container_types,
    )
    if set(active).intersection(shadow):
        _refuse(
            "active_shadow_overlap",
            "a variant cannot be active and shadow in the same head",
        )
    if parsed_generation == 0 and previous != INITIAL_PREVIOUS_HEAD_DIGEST:
        _refuse(
            "initial_previous_head",
            "generation zero must use the initial predecessor sentinel",
        )
    if parsed_generation > 0 and previous == INITIAL_PREVIOUS_HEAD_DIGEST:
        _refuse(
            "noninitial_previous_head",
            "a noninitial generation must bind a real predecessor head",
        )
    return parsed_generation, previous, context, active, shadow


def derive_activation_head_digest(
    *,
    generation: int,
    previous_head_digest: str,
    expression_context_digest: str,
    active_variant_digests: object,
    shadow_variant_digests: object,
) -> str:
    generation, previous, context, active, shadow = _validate_head_fields(
        generation=generation,
        previous_head_digest=previous_head_digest,
        expression_context_digest=expression_context_digest,
        active_variant_digests=active_variant_digests,
        shadow_variant_digests=shadow_variant_digests,
        normalize=True,
    )
    return sha256_digest(
        {
            "domain": ACTIVATION_HEAD_DIGEST_DOMAIN,
            "schema_version": ACTIVATION_HEAD_SCHEMA,
            "generation": generation,
            "previous_head_digest": previous,
            "expression_context_digest": context,
            "active_variant_digests": list(active),
            "shadow_variant_digests": list(shadow),
        }
    )


@dataclass(frozen=True)
class ActivationHeadV1:
    """An immutable active/shadow selection at one never-reused generation."""

    head_digest: str
    generation: int
    previous_head_digest: str
    expression_context_digest: str
    active_variant_digests: tuple[str, ...]
    shadow_variant_digests: tuple[str, ...]
    schema_version: str = ACTIVATION_HEAD_SCHEMA

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, ACTIVATION_HEAD_SCHEMA)
        _require_digest(self.head_digest, "head_digest")
        if type(self.active_variant_digests) is not tuple or type(
            self.shadow_variant_digests
        ) is not tuple:
            _refuse(
                "variant_sets",
                "ActivationHeadV1 stores variant sets as immutable tuples",
            )
        generation, previous, context, active, shadow = _validate_head_fields(
            generation=self.generation,
            previous_head_digest=self.previous_head_digest,
            expression_context_digest=self.expression_context_digest,
            active_variant_digests=self.active_variant_digests,
            shadow_variant_digests=self.shadow_variant_digests,
            normalize=False,
            container_types=(tuple,),
        )
        expected = derive_activation_head_digest(
            generation=generation,
            previous_head_digest=previous,
            expression_context_digest=context,
            active_variant_digests=active,
            shadow_variant_digests=shadow,
        )
        if self.head_digest != expected:
            _refuse("head_digest_mismatch", "head_digest does not match content")

    def to_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "head_digest": self.head_digest,
            "generation": self.generation,
            "previous_head_digest": self.previous_head_digest,
            "expression_context_digest": self.expression_context_digest,
            "active_variant_digests": list(self.active_variant_digests),
            "shadow_variant_digests": list(self.shadow_variant_digests),
        }


def build_activation_head(
    *,
    generation: int,
    previous_head_digest: str,
    expression_context_digest: str,
    active_variant_digests: object,
    shadow_variant_digests: object,
) -> ActivationHeadV1:
    generation, previous, context, active, shadow = _validate_head_fields(
        generation=generation,
        previous_head_digest=previous_head_digest,
        expression_context_digest=expression_context_digest,
        active_variant_digests=active_variant_digests,
        shadow_variant_digests=shadow_variant_digests,
        normalize=True,
    )
    return ActivationHeadV1(
        head_digest=derive_activation_head_digest(
            generation=generation,
            previous_head_digest=previous,
            expression_context_digest=context,
            active_variant_digests=active,
            shadow_variant_digests=shadow,
        ),
        generation=generation,
        previous_head_digest=previous,
        expression_context_digest=context,
        active_variant_digests=active,
        shadow_variant_digests=shadow,
    )


def _parse_activation_head(value: object) -> ActivationHeadV1:
    if type(value) is ActivationHeadV1:
        try:
            return ActivationHeadV1(
                schema_version=value.schema_version,
                head_digest=value.head_digest,
                generation=value.generation,
                previous_head_digest=value.previous_head_digest,
                expression_context_digest=value.expression_context_digest,
                active_variant_digests=value.active_variant_digests,
                shadow_variant_digests=value.shadow_variant_digests,
            )
        except AttributeError:
            _refuse("malformed_instance", "activation head instance is malformed")
    snapshot = _wire_snapshot(value, ACTIVATION_HEAD_KEYS)
    return ActivationHeadV1(
        schema_version=snapshot["schema_version"],
        head_digest=snapshot["head_digest"],
        generation=snapshot["generation"],
        previous_head_digest=snapshot["previous_head_digest"],
        expression_context_digest=snapshot["expression_context_digest"],
        active_variant_digests=_digest_set(
            snapshot["active_variant_digests"],
            "active_variant_digests",
            limit=MAX_VARIANTS_PER_SET,
            normalize=False,
            container_types=(list,),
        ),
        shadow_variant_digests=_digest_set(
            snapshot["shadow_variant_digests"],
            "shadow_variant_digests",
            limit=MAX_VARIANTS_PER_SET,
            normalize=False,
            container_types=(list,),
        ),
    )


def verify_activation_head(value: object) -> tuple[bool, Optional[str]]:
    try:
        _parse_activation_head(value)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    return True, None


def verify_activation_selection(
    *,
    head: object,
    expected_activation_head_digest: str,
    context: object,
    variants: object,
    variant_ceilings: object,
    charter_ceiling: object,
    expressed_ceiling: object,
    expected_profile_head_digest: str,
    expected_policy_head_digest: str,
    expected_resource_head_digest: str,
    expected_domain_head_digest: str,
    expected_environment_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify an exact, bounded, advisory activation selection end to end.

    This closes the relational gap between independently valid records:

    * the activation head must be the externally expected current head;
    * its context digest must name the supplied ``ExpressionContextV1``;
    * every context-plane head must equal the caller's current expected head;
    * supplied variants must exactly equal active union shadow, with no extras;
    * each family has at most one active and one shadow allele;
    * supplied variant ceilings must exactly cover the ceilings those variants
      bind; and
    * every active *and* shadow variant must pass the dual variant/charter
      narrowing proof against the expressed ceiling.

    Success proves selection consistency only.  It neither authenticates the
    expected heads nor grants execution authority.  An external adapter must
    obtain them from its authenticated current snapshot and keep that aggregate
    stable for this call.
    """

    try:
        expected_activation = _require_digest(
            expected_activation_head_digest, "expected_activation_head_digest"
        )
        expected_heads = {
            "profile": _require_digest(
                expected_profile_head_digest, "expected_profile_head_digest"
            ),
            "policy": _require_digest(
                expected_policy_head_digest, "expected_policy_head_digest"
            ),
            "resource": _require_digest(
                expected_resource_head_digest, "expected_resource_head_digest"
            ),
            "domain": _require_digest(
                expected_domain_head_digest, "expected_domain_head_digest"
            ),
            "environment": _require_digest(
                expected_environment_head_digest,
                "expected_environment_head_digest",
            ),
        }
        parsed_head = _parse_activation_head(head)
    except CapabilityActivationContractError as exc:
        return False, exc.reason

    if parsed_head.head_digest != expected_activation:
        return False, "stale_activation_head"

    try:
        parsed_context = _parse_expression_context(context)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    if parsed_head.expression_context_digest != parsed_context.context_digest:
        return False, "expression_context_binding"

    actual_heads = {
        "profile": parsed_context.profile_head_digest,
        "policy": parsed_context.policy_head_digest,
        "resource": parsed_context.resource_head_digest,
        "domain": parsed_context.domain_head_digest,
        "environment": parsed_context.environment_head_digest,
    }
    for label in ("profile", "policy", "resource", "domain", "environment"):
        if actual_heads[label] != expected_heads[label]:
            return False, f"stale_{label}_head"

    try:
        parsed_charter = _parse_authority_ceiling(charter_ceiling)
        parsed_expressed = _parse_authority_ceiling(expressed_ceiling)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    if parsed_context.charter_ceiling_digest != parsed_charter.ceiling_digest:
        return False, "charter_ceiling_binding"
    if parsed_context.expressed_ceiling_digest != parsed_expressed.ceiling_digest:
        return False, "expressed_ceiling_binding"
    charter_ok, charter_reason = verify_ceiling_narrowing(
        parsed_charter, parsed_expressed
    )
    if not charter_ok:
        return False, f"charter_{charter_reason}"

    try:
        variant_values = _bounded_contract_sequence(
            variants,
            "variants",
            limit=MAX_VARIANTS_PER_SET * 2,
        )
        parsed_variants = tuple(
            _parse_capability_variant(item) for item in variant_values
        )
    except CapabilityActivationContractError as exc:
        return False, exc.reason

    variant_digests = tuple(item.variant_digest for item in parsed_variants)
    if len(set(variant_digests)) != len(variant_digests):
        return False, "duplicate_variant_record"
    selected_digests = set(parsed_head.active_variant_digests).union(
        parsed_head.shadow_variant_digests
    )
    if set(variant_digests) != selected_digests:
        return False, "variant_set_binding"

    # Digest-set uniqueness is insufficient for dispatch: two different
    # artifacts in the same active family would leave runtime choice ambiguous
    # and could make ordering an ambient authority source.  Digital diploidy
    # permits one active plus one shadow allele per family, never two in either
    # expression set.
    active_digests = set(parsed_head.active_variant_digests)
    shadow_digests = set(parsed_head.shadow_variant_digests)
    active_families: set[str] = set()
    shadow_families: set[str] = set()
    for variant in parsed_variants:
        families = (
            active_families
            if variant.variant_digest in active_digests
            else shadow_families
        )
        if variant.family_id in families:
            role = "active" if variant.variant_digest in active_digests else "shadow"
            return False, f"ambiguous_{role}_family"
        families.add(variant.family_id)

    try:
        ceiling_values = _bounded_contract_sequence(
            variant_ceilings,
            "variant_ceilings",
            limit=MAX_VARIANTS_PER_SET * 2,
        )
        parsed_ceilings = tuple(
            _parse_authority_ceiling(item) for item in ceiling_values
        )
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    ceiling_by_digest = {
        item.ceiling_digest: item for item in parsed_ceilings
    }
    if len(ceiling_by_digest) != len(parsed_ceilings):
        return False, "duplicate_variant_ceiling"
    referenced_ceilings = {
        item.authority_ceiling_digest for item in parsed_variants
    }
    if set(ceiling_by_digest) != referenced_ceilings:
        return False, "variant_ceiling_set_binding"

    for variant in parsed_variants:
        ok, reason = verify_expression_constraints(
            variant=variant,
            context=parsed_context,
            variant_ceiling=ceiling_by_digest[variant.authority_ceiling_digest],
            charter_ceiling=parsed_charter,
            expressed_ceiling=parsed_expressed,
        )
        if not ok:
            return False, f"variant_expression_{reason}"
    return True, None


def verify_activation_transition(
    current: object,
    proposed: object,
    *,
    expected_current_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify one CAS transition without claiming external atomicity."""

    try:
        expected = _require_digest(
            expected_current_head_digest, "expected_current_head_digest"
        )
        current_head = _parse_activation_head(current)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    if expected != current_head.head_digest:
        return False, "stale_current_head"
    # Reject a stale CAS before spending work on the proposed bounded sets.
    try:
        proposed_head = _parse_activation_head(proposed)
    except CapabilityActivationContractError as exc:
        return False, exc.reason
    if current_head.generation == MAX_GENERATION:
        return False, "generation_exhausted"
    if proposed_head.generation != current_head.generation + 1:
        return False, "generation_step"
    if proposed_head.previous_head_digest != current_head.head_digest:
        return False, "previous_head_binding"
    return True, None


def build_next_activation_head(
    current: object,
    *,
    expected_current_head_digest: str,
    expression_context_digest: str,
    active_variant_digests: object,
    shadow_variant_digests: object,
) -> ActivationHeadV1:
    """Build a one-step proposal from an explicitly expected current head.

    The persistence adapter must still perform the actual atomic compare-and-
    swap against ``expected_current_head_digest``.
    """

    expected = _require_digest(
        expected_current_head_digest, "expected_current_head_digest"
    )
    current_head = _parse_activation_head(current)
    if expected != current_head.head_digest:
        _refuse("stale_current_head", "expected current head is stale")
    if current_head.generation == MAX_GENERATION:
        _refuse("generation_exhausted", "activation generation is exhausted")
    return build_activation_head(
        generation=current_head.generation + 1,
        previous_head_digest=current_head.head_digest,
        expression_context_digest=expression_context_digest,
        active_variant_digests=active_variant_digests,
        shadow_variant_digests=shadow_variant_digests,
    )
