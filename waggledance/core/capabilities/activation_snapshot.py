# SPDX-License-Identifier: BUSL-1.1
"""Scope-bound, authority-free capability activation snapshots.

``ActivationHeadV1`` intentionally describes a selection without naming the
deployment or Genesis cell that owns its mutable current pointer.  A single
control-plane database can serve several cells, so using the head digest as a
global current key would allow otherwise valid state to be transplanted across
cells.

This module closes that isolation gap with two pure contracts:

* ``ActivationScopeV1`` derives a naming key from an explicit deployment
  digest and a fully verified ``CellIdentityV1.cell_id``; and
* ``ActivationSnapshotBundleV1`` binds that scope to one complete, canonical
  activation selection and to a monotonic bundle predecessor chain.

Neither contract authenticates a provider, grants execution permission, or
changes routing.  Persistence and signature adapters must perform those
separate checks.  In particular, a scope digest is an isolation key, never an
authorization token.

Decoded aggregate inputs follow WD's ``caller_owned_quiescent.v1`` boundary:
the verifier bounds and privately copies every exact built-in container, but a
caller must not mutate nested input containers concurrently with one call.  A
storage adapter must persist only the immutable bytes returned by
``canonicalize_activation_snapshot_publication`` rather than re-reading the
caller's mutable object after verification.
"""

from __future__ import annotations

import re
from typing import Optional

from waggledance.core.capabilities.activation_contracts import (
    ACTIVATION_HEAD_KEYS,
    AUTHORITY_CEILING_KEYS,
    CAPABILITY_VARIANT_KEYS,
    EXPRESSION_CONTEXT_KEYS,
    MAX_AUTHORITY_SCOPES,
    MAX_GENERATION,
    MAX_VARIANTS_PER_SET,
    verify_activation_selection,
    verify_activation_transition,
)
from waggledance.core.cell_identity import (
    IDENTITY_KEYS,
    CellIdentityV1,
    verify_cell_identity,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest

ACTIVATION_SCOPE_SCHEMA = "wd.activation_scope.v1"
ACTIVATION_SNAPSHOT_BUNDLE_SCHEMA = "wd.activation_snapshot_bundle.v1"

ACTIVATION_SCOPE_DIGEST_DOMAIN = "wd.activation_scope.digest.v1"
ACTIVATION_SNAPSHOT_BUNDLE_DIGEST_DOMAIN = (
    "wd.activation_snapshot_bundle.digest.v1"
)

INITIAL_PREVIOUS_BUNDLE_DIGEST = "sha256:" + "0" * 64
MIRROR_SNAPSHOT_SCHEMA = "wd.activation_snapshot.v1"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

ACTIVATION_SCOPE_KEYS = frozenset(
    {
        "schema_version",
        "activation_scope_digest",
        "deployment_scope_digest",
        "cell_id",
    }
)

_SELECTION_KEYS = frozenset(
    {
        "head",
        "expected_activation_head_digest",
        "context",
        "variants",
        "variant_ceilings",
        "charter_ceiling",
        "expressed_ceiling",
        "expected_profile_head_digest",
        "expected_policy_head_digest",
        "expected_resource_head_digest",
        "expected_domain_head_digest",
        "expected_environment_head_digest",
    }
)

_NON_AUTHORITY_FLAGS = {
    "provider_authentication_verified": False,
    "runtime_authority_granted": False,
    "routing_influence_applied": False,
    "execution_permission_granted": False,
}

_CURRENT_CONTEXT_BINDING_FIELDS = (
    "profile_head_digest",
    "policy_head_digest",
    "resource_head_digest",
    "domain_head_digest",
    "environment_head_digest",
)

ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS = frozenset(
    {
        "schema_version",
        "activation_scope",
        "store_revision",
        "activation_head_generation",
        "previous_bundle_digest",
        *_SELECTION_KEYS,
        *_NON_AUTHORITY_FLAGS,
    }
)
ACTIVATION_SNAPSHOT_BUNDLE_KEYS = (
    ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS | {"bundle_digest"}
)


class ActivationSnapshotContractError(ValueError):
    """A scope or snapshot value violates the pure contract."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise ActivationSnapshotContractError(reason, message)


def _wire_dict(value: object, label: str, *, maximum_keys: int) -> dict:
    if type(value) is not dict:
        _refuse("not_mapping", f"{label} must be an exact dict")
    if dict.__len__(value) > maximum_keys:
        _refuse("keyset", f"{label} keyset exceeds its bound")
    snapshot = value.copy()
    if dict.__len__(snapshot) > maximum_keys:
        _refuse("keyset", f"{label} keyset exceeds its bound")
    if any(type(key) is not str for key in snapshot):
        _refuse("not_mapping", f"{label} keys must be exact strings")
    return snapshot


def _wire_list(value: object, label: str, *, maximum: int) -> list:
    if type(value) is not list:
        _refuse(label, f"{label} must be an exact list")
    if list.__len__(value) > maximum:
        _refuse(label, f"{label} exceeds its {maximum}-item bound")
    # A bounded slice also catches growth between the O(1) length check and
    # the snapshot.  Exact list was required before invoking this protocol.
    snapshot = value[: maximum + 1]
    if list.__len__(snapshot) > maximum:
        _refuse(label, f"{label} exceeds its {maximum}-item bound")
    return snapshot


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a sha256:<64 lowercase hex> digest")
    return value


def _revision(value: object, label: str = "store_revision") -> int:
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        _refuse(
            label,
            f"{label} must be an exact integer within 0..{MAX_GENERATION}",
        )
    return value


def _cell_identity_mapping(value: object) -> dict:
    if type(value) is CellIdentityV1:
        try:
            snapshot = {
                "schema_version": value.schema_version,
                "cell_id": value.cell_id,
                "pubkey_digest": value.pubkey_digest,
                "genesis_material_digest": value.genesis_material_digest,
                "created_at_utc": value.created_at_utc,
            }
        except AttributeError:
            _refuse("cell_identity", "cell identity instance is malformed")
    else:
        snapshot = _wire_dict(
            value,
            "cell_identity",
            maximum_keys=len(IDENTITY_KEYS),
        )
    ok, reason = verify_cell_identity(snapshot)
    if not ok:
        _refuse(
            f"cell_identity:{reason}",
            f"cell identity verification failed: {reason}",
        )
    return snapshot


def derive_activation_scope_digest(
    *, deployment_scope_digest: str, cell_id: str
) -> str:
    """Derive an isolation key from explicit immutable scope facts.

    Callers normally use :func:`build_activation_scope`, which first verifies
    the full cell identity.  This lower-level derivation validates digest
    shapes but cannot prove the provenance of a bare ``cell_id``.
    """

    deployment = _digest(deployment_scope_digest, "deployment_scope_digest")
    verified_cell_id = _digest(cell_id, "cell_id")
    return sha256_digest(
        {
            "domain": ACTIVATION_SCOPE_DIGEST_DOMAIN,
            "schema_version": ACTIVATION_SCOPE_SCHEMA,
            "deployment_scope_digest": deployment,
            "cell_id": verified_cell_id,
        }
    )


def build_activation_scope(
    *, deployment_scope_digest: str, cell_identity: object
) -> dict[str, str]:
    """Build a scope after verifying the complete Genesis cell identity."""

    identity = _cell_identity_mapping(cell_identity)
    deployment = _digest(deployment_scope_digest, "deployment_scope_digest")
    return {
        "schema_version": ACTIVATION_SCOPE_SCHEMA,
        "activation_scope_digest": derive_activation_scope_digest(
            deployment_scope_digest=deployment,
            cell_id=identity["cell_id"],
        ),
        "deployment_scope_digest": deployment,
        "cell_id": identity["cell_id"],
    }


def _parse_activation_scope(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> dict[str, str]:
    scope = _wire_dict(
        value,
        "activation_scope",
        maximum_keys=len(ACTIVATION_SCOPE_KEYS),
    )
    if set(scope) != ACTIVATION_SCOPE_KEYS:
        _refuse("scope_keyset", "activation scope has a non-exact keyset")
    if type(scope["schema_version"]) is not str or scope[
        "schema_version"
    ] != ACTIVATION_SCOPE_SCHEMA:
        _refuse("scope_schema_version", "activation scope schema refused")

    identity = _cell_identity_mapping(cell_identity)
    expected_deployment = _digest(
        expected_deployment_scope_digest,
        "expected_deployment_scope_digest",
    )
    claimed_deployment = _digest(
        scope["deployment_scope_digest"], "deployment_scope_digest"
    )
    claimed_cell = _digest(scope["cell_id"], "cell_id")
    claimed_scope = _digest(
        scope["activation_scope_digest"], "activation_scope_digest"
    )
    if claimed_deployment != expected_deployment:
        _refuse("deployment_scope_binding", "deployment scope is not current")
    if claimed_cell != identity["cell_id"]:
        _refuse("cell_identity_binding", "scope names a different Genesis cell")
    expected_scope = derive_activation_scope_digest(
        deployment_scope_digest=claimed_deployment,
        cell_id=claimed_cell,
    )
    if claimed_scope != expected_scope:
        _refuse("activation_scope_digest_mismatch", "scope digest mismatch")
    return {
        "schema_version": ACTIVATION_SCOPE_SCHEMA,
        "activation_scope_digest": claimed_scope,
        "deployment_scope_digest": claimed_deployment,
        "cell_id": claimed_cell,
    }


def verify_activation_scope(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> tuple[bool, Optional[str]]:
    try:
        _parse_activation_scope(
            value,
            cell_identity=cell_identity,
            expected_deployment_scope_digest=expected_deployment_scope_digest,
        )
    except ActivationSnapshotContractError as exc:
        return False, exc.reason
    return True, None


def _snapshot_head(value: object, *, normalize: bool) -> dict:
    head = _wire_dict(
        value,
        "activation snapshot head",
        maximum_keys=len(ACTIVATION_HEAD_KEYS),
    )
    if set(head) != ACTIVATION_HEAD_KEYS:
        _refuse("head_keyset", "activation snapshot head keyset")
    for field in ("active_variant_digests", "shadow_variant_digests"):
        values = _wire_list(
            head[field],
            f"head.{field}",
            maximum=MAX_VARIANTS_PER_SET,
        )
        for item in values:
            _digest(item, f"head.{field}")
        canonical = sorted(values)
        if not normalize and values != canonical:
            _refuse(f"{field}_order", f"head.{field} is not canonical")
        head[field] = canonical
    return head


def _snapshot_context(value: object) -> dict:
    context = _wire_dict(
        value,
        "activation snapshot context",
        maximum_keys=len(EXPRESSION_CONTEXT_KEYS),
    )
    if set(context) != EXPRESSION_CONTEXT_KEYS:
        _refuse("context_keyset", "activation snapshot context keyset")
    return context


def _snapshot_variant(value: object, index: int) -> dict:
    variant = _wire_dict(
        value,
        f"activation snapshot variants[{index}]",
        maximum_keys=len(CAPABILITY_VARIANT_KEYS),
    )
    if set(variant) != CAPABILITY_VARIANT_KEYS:
        _refuse("variant_keyset", f"variants[{index}] keyset")
    _digest(variant["variant_digest"], "variant_digest")
    return variant


def _snapshot_ceiling(value: object, label: str, *, normalize: bool) -> dict:
    ceiling = _wire_dict(
        value,
        label,
        maximum_keys=len(AUTHORITY_CEILING_KEYS),
    )
    if set(ceiling) != AUTHORITY_CEILING_KEYS:
        _refuse("ceiling_keyset", f"{label} keyset")
    scopes = _wire_list(
        ceiling["authority_scope_digests"],
        f"{label}.authority_scope_digests",
        maximum=MAX_AUTHORITY_SCOPES,
    )
    for item in scopes:
        _digest(item, f"{label}.authority_scope_digests")
    canonical = sorted(scopes)
    if not normalize and scopes != canonical:
        _refuse("authority_scope_order", f"{label} is not canonical")
    ceiling["authority_scope_digests"] = canonical
    _digest(ceiling["ceiling_digest"], f"{label}.ceiling_digest")
    return ceiling


def _stable_bundle_core(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
    normalize: bool,
) -> dict:
    core = _wire_dict(
        value,
        "activation snapshot bundle",
        maximum_keys=len(ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS),
    )
    if set(core) != ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS:
        _refuse("bundle_keyset", "activation snapshot bundle core keyset")
    if type(core["schema_version"]) is not str or core[
        "schema_version"
    ] != ACTIVATION_SNAPSHOT_BUNDLE_SCHEMA:
        _refuse("bundle_schema_version", "activation snapshot bundle schema refused")

    core["activation_scope"] = _parse_activation_scope(
        core["activation_scope"],
        cell_identity=cell_identity,
        expected_deployment_scope_digest=expected_deployment_scope_digest,
    )
    revision = _revision(core["store_revision"])
    generation = _revision(
        core["activation_head_generation"], "activation_head_generation"
    )
    if generation != revision:
        _refuse(
            "revision_generation_binding",
            "store revision must equal activation head generation",
        )
    previous = _digest(core["previous_bundle_digest"], "previous_bundle_digest")
    if revision == 0 and previous != INITIAL_PREVIOUS_BUNDLE_DIGEST:
        _refuse(
            "initial_previous_bundle",
            "revision zero must use the bundle predecessor sentinel",
        )
    if revision > 0 and previous == INITIAL_PREVIOUS_BUNDLE_DIGEST:
        _refuse(
            "noninitial_previous_bundle",
            "a noninitial revision must bind a real predecessor bundle",
        )

    core["head"] = _snapshot_head(core["head"], normalize=normalize)
    core["context"] = _snapshot_context(core["context"])

    raw_variants = _wire_list(
        core["variants"],
        "variants",
        maximum=MAX_VARIANTS_PER_SET * 2,
    )
    variants = [
        _snapshot_variant(item, index) for index, item in enumerate(raw_variants)
    ]
    canonical_variants = sorted(variants, key=lambda item: item["variant_digest"])
    variant_digest_order = [item["variant_digest"] for item in variants]
    if not normalize and variant_digest_order != sorted(variant_digest_order):
        _refuse("variant_order", "variants are not in canonical digest order")
    core["variants"] = canonical_variants

    raw_ceilings = _wire_list(
        core["variant_ceilings"],
        "variant_ceilings",
        maximum=MAX_VARIANTS_PER_SET * 2,
    )
    ceilings = [
        _snapshot_ceiling(
            item,
            f"variant_ceilings[{index}]",
            normalize=normalize,
        )
        for index, item in enumerate(raw_ceilings)
    ]
    canonical_ceilings = sorted(
        ceilings, key=lambda item: item["ceiling_digest"]
    )
    ceiling_digest_order = [item["ceiling_digest"] for item in ceilings]
    if not normalize and ceiling_digest_order != sorted(ceiling_digest_order):
        _refuse(
            "variant_ceiling_order",
            "variant ceilings are not in canonical digest order",
        )
    core["variant_ceilings"] = canonical_ceilings
    core["charter_ceiling"] = _snapshot_ceiling(
        core["charter_ceiling"], "charter_ceiling", normalize=normalize
    )
    core["expressed_ceiling"] = _snapshot_ceiling(
        core["expressed_ceiling"], "expressed_ceiling", normalize=normalize
    )

    for field, expected in _NON_AUTHORITY_FLAGS.items():
        if type(core[field]) is not bool or core[field] is not expected:
            _refuse(field, f"{field} must remain {expected}")

    valid, reason = verify_activation_selection(
        head=core["head"],
        expected_activation_head_digest=core[
            "expected_activation_head_digest"
        ],
        context=core["context"],
        variants=core["variants"],
        variant_ceilings=core["variant_ceilings"],
        charter_ceiling=core["charter_ceiling"],
        expressed_ceiling=core["expressed_ceiling"],
        expected_profile_head_digest=core["expected_profile_head_digest"],
        expected_policy_head_digest=core["expected_policy_head_digest"],
        expected_resource_head_digest=core["expected_resource_head_digest"],
        expected_domain_head_digest=core["expected_domain_head_digest"],
        expected_environment_head_digest=core[
            "expected_environment_head_digest"
        ],
    )
    if not valid:
        _refuse(
            f"selection:{reason}",
            f"activation selection verification failed: {reason}",
        )
    if core["head"]["generation"] != generation:
        _refuse(
            "head_generation_binding",
            "bundle generation does not match the activation head",
        )
    return core


def build_activation_snapshot_bundle(
    *,
    deployment_scope_digest: str,
    cell_identity: object,
    store_revision: int,
    previous_bundle_digest: str,
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
) -> dict:
    """Build one canonical, scope-bound snapshot with no authority claim."""

    scope = build_activation_scope(
        deployment_scope_digest=deployment_scope_digest,
        cell_identity=cell_identity,
    )
    raw_core = {
        "schema_version": ACTIVATION_SNAPSHOT_BUNDLE_SCHEMA,
        "activation_scope": scope,
        "store_revision": store_revision,
        "activation_head_generation": store_revision,
        "previous_bundle_digest": previous_bundle_digest,
        "head": head,
        "expected_activation_head_digest": expected_activation_head_digest,
        "context": context,
        "variants": variants,
        "variant_ceilings": variant_ceilings,
        "charter_ceiling": charter_ceiling,
        "expressed_ceiling": expressed_ceiling,
        "expected_profile_head_digest": expected_profile_head_digest,
        "expected_policy_head_digest": expected_policy_head_digest,
        "expected_resource_head_digest": expected_resource_head_digest,
        "expected_domain_head_digest": expected_domain_head_digest,
        "expected_environment_head_digest": expected_environment_head_digest,
        **_NON_AUTHORITY_FLAGS,
    }
    core = _stable_bundle_core(
        raw_core,
        cell_identity=cell_identity,
        expected_deployment_scope_digest=deployment_scope_digest,
        normalize=True,
    )
    return {
        **core,
        "bundle_digest": sha256_digest(
            {
                "domain": ACTIVATION_SNAPSHOT_BUNDLE_DIGEST_DOMAIN,
                "bundle": core,
            }
        ),
    }


def _parse_activation_snapshot_bundle(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> dict:
    bundle = _wire_dict(
        value,
        "activation snapshot bundle",
        maximum_keys=len(ACTIVATION_SNAPSHOT_BUNDLE_KEYS),
    )
    if set(bundle) != ACTIVATION_SNAPSHOT_BUNDLE_KEYS:
        _refuse("bundle_keyset", "activation snapshot bundle keyset")
    claimed = _digest(bundle["bundle_digest"], "bundle_digest")
    raw_core = {
        key: bundle[key] for key in ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS
    }
    core = _stable_bundle_core(
        raw_core,
        cell_identity=cell_identity,
        expected_deployment_scope_digest=expected_deployment_scope_digest,
        normalize=False,
    )
    expected = sha256_digest(
        {
            "domain": ACTIVATION_SNAPSHOT_BUNDLE_DIGEST_DOMAIN,
            "bundle": core,
        }
    )
    if claimed != expected:
        _refuse("bundle_digest_mismatch", "bundle digest does not match content")
    return {**core, "bundle_digest": claimed}


def verify_activation_snapshot_bundle(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify full content/scope consistency, but no signature or authority."""

    try:
        _parse_activation_snapshot_bundle(
            value,
            cell_identity=cell_identity,
            expected_deployment_scope_digest=expected_deployment_scope_digest,
        )
    except ActivationSnapshotContractError as exc:
        return False, exc.reason
    return True, None


def canonicalize_activation_snapshot_bundle(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> bytes:
    """Return immutable canonical bytes from the same verified private copy.

    This proves structural content/scope consistency only.  Publication code
    must use :func:`canonicalize_activation_snapshot_publication`, which also
    binds externally obtained current context heads and ceiling decisions.
    """

    parsed = _parse_activation_snapshot_bundle(
        value,
        cell_identity=cell_identity,
        expected_deployment_scope_digest=expected_deployment_scope_digest,
    )
    return canonical_json_bytes(parsed)


def _validate_external_current_bindings(
    parsed: dict,
    *,
    expected_profile_head_digest: str,
    expected_policy_head_digest: str,
    expected_resource_head_digest: str,
    expected_domain_head_digest: str,
    expected_environment_head_digest: str,
    expected_charter_ceiling_digest: str,
    expected_expressed_ceiling_digest: str,
) -> None:
    expected_context_heads = {
        "profile_head_digest": expected_profile_head_digest,
        "policy_head_digest": expected_policy_head_digest,
        "resource_head_digest": expected_resource_head_digest,
        "domain_head_digest": expected_domain_head_digest,
        "environment_head_digest": expected_environment_head_digest,
    }
    context = parsed["context"]
    for field in _CURRENT_CONTEXT_BINDING_FIELDS:
        expected = _digest(expected_context_heads[field], f"expected_{field}")
        if context[field] != expected:
            _refuse(
                f"current_{field}_binding",
                f"bundle does not bind the externally current {field}",
            )

    expected_charter = _digest(
        expected_charter_ceiling_digest,
        "expected_charter_ceiling_digest",
    )
    if parsed["charter_ceiling"]["ceiling_digest"] != expected_charter:
        _refuse(
            "current_charter_ceiling_binding",
            "bundle does not bind the externally current charter ceiling",
        )
    expected_expressed = _digest(
        expected_expressed_ceiling_digest,
        "expected_expressed_ceiling_digest",
    )
    if parsed["expressed_ceiling"]["ceiling_digest"] != expected_expressed:
        _refuse(
            "current_expressed_ceiling_binding",
            "bundle does not bind the externally selected expressed ceiling",
        )


def canonicalize_activation_snapshot_publication(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
    expected_profile_head_digest: str,
    expected_policy_head_digest: str,
    expected_resource_head_digest: str,
    expected_domain_head_digest: str,
    expected_environment_head_digest: str,
    expected_charter_ceiling_digest: str,
    expected_expressed_ceiling_digest: str,
) -> bytes:
    """Freeze a bundle only after rebinding authenticated current inputs.

    The caller supplies heads obtained from its authenticated control/charter
    layer and must keep them stable through the eventual write transaction.
    This function authenticates neither the caller nor those supplied values;
    it prevents the bundle from using its own embedded claims as their source.
    Its immutable return value is the only object a persistence adapter should
    decode or store, eliminating a verify-then-reread mutation window.
    """

    parsed = _parse_activation_snapshot_bundle(
        value,
        cell_identity=cell_identity,
        expected_deployment_scope_digest=expected_deployment_scope_digest,
    )
    _validate_external_current_bindings(
        parsed,
        expected_profile_head_digest=expected_profile_head_digest,
        expected_policy_head_digest=expected_policy_head_digest,
        expected_resource_head_digest=expected_resource_head_digest,
        expected_domain_head_digest=expected_domain_head_digest,
        expected_environment_head_digest=expected_environment_head_digest,
        expected_charter_ceiling_digest=expected_charter_ceiling_digest,
        expected_expressed_ceiling_digest=expected_expressed_ceiling_digest,
    )
    return canonical_json_bytes(parsed)


def verify_activation_snapshot_publication(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
    expected_profile_head_digest: str,
    expected_policy_head_digest: str,
    expected_resource_head_digest: str,
    expected_domain_head_digest: str,
    expected_environment_head_digest: str,
    expected_charter_ceiling_digest: str,
    expected_expressed_ceiling_digest: str,
) -> tuple[bool, Optional[str]]:
    """Boolean boundary for external-current publication rebinding."""

    try:
        canonicalize_activation_snapshot_publication(
            value,
            cell_identity=cell_identity,
            expected_deployment_scope_digest=expected_deployment_scope_digest,
            expected_profile_head_digest=expected_profile_head_digest,
            expected_policy_head_digest=expected_policy_head_digest,
            expected_resource_head_digest=expected_resource_head_digest,
            expected_domain_head_digest=expected_domain_head_digest,
            expected_environment_head_digest=expected_environment_head_digest,
            expected_charter_ceiling_digest=expected_charter_ceiling_digest,
            expected_expressed_ceiling_digest=expected_expressed_ceiling_digest,
        )
    except ActivationSnapshotContractError as exc:
        return False, exc.reason
    return True, None


def project_activation_snapshot_for_mirror(
    value: object,
    *,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> dict:
    """Project a verified bundle into the existing read-only mirror schema.

    The explicit projection preserves the mirror's exact-keyset boundary; a
    caller must not strip bundle fields ad hoc.  Like the mirror itself, this
    structural projection authenticates no provider and changes no route.
    """

    parsed = _parse_activation_snapshot_bundle(
        value,
        cell_identity=cell_identity,
        expected_deployment_scope_digest=expected_deployment_scope_digest,
    )
    return {
        "schema_version": MIRROR_SNAPSHOT_SCHEMA,
        **{key: parsed[key] for key in _SELECTION_KEYS},
    }


def verify_activation_snapshot_transition(
    current: object,
    proposed: object,
    *,
    expected_current_bundle_digest: str,
    cell_identity: object,
    expected_deployment_scope_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify one scope-local bundle transition before an external CAS.

    This function cannot make persistence atomic.  The storage adapter must
    compare the same expected bundle, head, revision and scope in one write
    transaction.
    """

    try:
        expected = _digest(
            expected_current_bundle_digest, "expected_current_bundle_digest"
        )
        parsed_current = _parse_activation_snapshot_bundle(
            current,
            cell_identity=cell_identity,
            expected_deployment_scope_digest=expected_deployment_scope_digest,
        )
    except ActivationSnapshotContractError as exc:
        return False, exc.reason
    if parsed_current["bundle_digest"] != expected:
        return False, "stale_current_bundle"
    try:
        parsed_proposed = _parse_activation_snapshot_bundle(
            proposed,
            cell_identity=cell_identity,
            expected_deployment_scope_digest=expected_deployment_scope_digest,
        )
    except ActivationSnapshotContractError as exc:
        return False, exc.reason

    if (
        parsed_proposed["activation_scope"]["activation_scope_digest"]
        != parsed_current["activation_scope"]["activation_scope_digest"]
    ):
        return False, "activation_scope_binding"
    current_revision = parsed_current["store_revision"]
    if current_revision == MAX_GENERATION:
        return False, "revision_exhausted"
    if parsed_proposed["store_revision"] != current_revision + 1:
        return False, "revision_step"
    if parsed_proposed["previous_bundle_digest"] != parsed_current[
        "bundle_digest"
    ]:
        return False, "previous_bundle_binding"

    transitioned, reason = verify_activation_transition(
        parsed_current["head"],
        parsed_proposed["head"],
        expected_current_head_digest=parsed_current[
            "expected_activation_head_digest"
        ],
    )
    if not transitioned:
        return False, f"activation_head:{reason}"
    return True, None
