# SPDX-License-Identifier: BUSL-1.1
"""Adversarial tests for scope-bound activation snapshot contracts."""

from __future__ import annotations

import hashlib
import json

import pytest

from waggledance.core.capabilities.activation_contracts import (
    INITIAL_PREVIOUS_HEAD_DIGEST,
    MAX_VARIANTS_PER_SET,
    build_activation_head,
    build_authority_ceiling,
    build_capability_variant,
    build_expression_context,
)
from waggledance.core.capabilities.activation_snapshot import (
    ACTIVATION_SCOPE_SCHEMA,
    INITIAL_PREVIOUS_BUNDLE_DIGEST,
    ActivationSnapshotContractError,
    build_activation_scope,
    build_activation_snapshot_bundle,
    canonicalize_activation_snapshot_bundle,
    canonicalize_activation_snapshot_publication,
    project_activation_snapshot_for_mirror,
    verify_activation_scope,
    verify_activation_snapshot_bundle,
    verify_activation_snapshot_publication,
    verify_activation_snapshot_transition,
)
from waggledance.core.capabilities.activation_mirror import (
    ACTIVE_MATCH,
    build_activation_mirror_record,
)
from waggledance.core.cell_identity import build_cell_identity


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _identity(label: str = "a"):
    return build_cell_identity(
        pubkey_digest=_digest(f"pubkey:{label}"),
        genesis_material_digest=_digest(f"genesis:{label}"),
        created_at_utc="2026-08-05T08:00:00Z",
    )


def _selection(
    *,
    generation: int = 0,
    previous_head_digest: str = INITIAL_PREVIOUS_HEAD_DIGEST,
    suffix: str = "a",
    two_variants: bool = False,
) -> dict:
    variant_ceiling = build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:b")],
    )
    charter_ceiling = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:c")],
    )
    expressed_ceiling = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a")],
    )

    def variant(family: str, artifact: str):
        return build_capability_variant(
            family_id=family,
            risk_class="internal_memory",
            artifact_digest=_digest(f"artifact:{artifact}"),
            input_schema_digest=_digest("input"),
            output_schema_digest=_digest("output"),
            compatibility_digest=_digest("compatibility"),
            authority_ceiling_digest=variant_ceiling.ceiling_digest,
        )

    variants = [variant("detect.fixture", f"one:{suffix}")]
    if two_variants:
        variants.append(variant("solve.fixture", f"two:{suffix}"))
    context = build_expression_context(
        profile_head_digest=_digest(f"profile:{suffix}"),
        policy_head_digest=_digest(f"policy:{suffix}"),
        resource_head_digest=_digest(f"resource:{suffix}"),
        domain_head_digest=_digest(f"domain:{suffix}"),
        environment_head_digest=_digest(f"environment:{suffix}"),
        charter_ceiling_digest=charter_ceiling.ceiling_digest,
        expressed_ceiling_digest=expressed_ceiling.ceiling_digest,
    )
    head = build_activation_head(
        generation=generation,
        previous_head_digest=previous_head_digest,
        expression_context_digest=context.context_digest,
        active_variant_digests=[item.variant_digest for item in variants],
        shadow_variant_digests=[],
    )
    return {
        "head": head.to_mapping(),
        "expected_activation_head_digest": head.head_digest,
        "context": context.to_mapping(),
        "variants": [item.to_mapping() for item in variants],
        "variant_ceilings": [variant_ceiling.to_mapping()],
        "charter_ceiling": charter_ceiling.to_mapping(),
        "expressed_ceiling": expressed_ceiling.to_mapping(),
        "expected_profile_head_digest": context.profile_head_digest,
        "expected_policy_head_digest": context.policy_head_digest,
        "expected_resource_head_digest": context.resource_head_digest,
        "expected_domain_head_digest": context.domain_head_digest,
        "expected_environment_head_digest": context.environment_head_digest,
    }


def _bundle(
    *,
    identity=None,
    deployment_scope_digest: str | None = None,
    generation: int = 0,
    previous_head_digest: str = INITIAL_PREVIOUS_HEAD_DIGEST,
    previous_bundle_digest: str = INITIAL_PREVIOUS_BUNDLE_DIGEST,
    suffix: str = "a",
    two_variants: bool = False,
) -> dict:
    identity = identity or _identity()
    deployment = deployment_scope_digest or _digest("deployment:a")
    selection = _selection(
        generation=generation,
        previous_head_digest=previous_head_digest,
        suffix=suffix,
        two_variants=two_variants,
    )
    return build_activation_snapshot_bundle(
        deployment_scope_digest=deployment,
        cell_identity=identity,
        store_revision=generation,
        previous_bundle_digest=previous_bundle_digest,
        **selection,
    )


def test_scope_binds_verified_genesis_cell_and_deployment_only() -> None:
    identity = _identity("one")
    deployment = _digest("deployment:one")
    scope = build_activation_scope(
        deployment_scope_digest=deployment,
        cell_identity=identity,
    )
    assert scope["schema_version"] == ACTIVATION_SCOPE_SCHEMA
    assert scope["cell_id"] == identity.cell_id
    assert verify_activation_scope(
        scope,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (True, None)

    assert verify_activation_scope(
        scope,
        cell_identity=_identity("two"),
        expected_deployment_scope_digest=deployment,
    ) == (False, "cell_identity_binding")
    assert verify_activation_scope(
        scope,
        cell_identity=identity,
        expected_deployment_scope_digest=_digest("deployment:two"),
    ) == (False, "deployment_scope_binding")

    # Profile and logical topology coordinates are expression state, not
    # ambient scope selectors.  Exact keysets prevent either being smuggled.
    for forbidden in ("profile", "cell_coord", "hostname", "db_path"):
        smuggled = {**scope, forbidden: "ambient"}
        assert verify_activation_scope(
            smuggled,
            cell_identity=identity,
            expected_deployment_scope_digest=deployment,
        ) == (False, "keyset")


def test_scope_requires_the_full_valid_cell_identity_source() -> None:
    identity = _identity().to_mapping()
    identity["cell_id"] = _digest("forged-cell")
    with pytest.raises(
        ActivationSnapshotContractError, match="cell identity verification failed"
    ):
        build_activation_scope(
            deployment_scope_digest=_digest("deployment"),
            cell_identity=identity,
        )

    scope = {
        "schema_version": ACTIVATION_SCOPE_SCHEMA,
        "activation_scope_digest": _digest("scope"),
        "deployment_scope_digest": _digest("deployment"),
        "cell_id": _digest("cell"),
    }
    assert verify_activation_scope(
        scope,
        cell_identity=identity,
        expected_deployment_scope_digest=_digest("deployment"),
    ) == (False, "cell_identity:cell_id_mismatch")


def test_bundle_is_whole_relation_bound_and_explicitly_authority_free() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    bundle = _bundle(identity=identity, deployment_scope_digest=deployment)
    assert verify_activation_snapshot_bundle(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (True, None)
    assert bundle["store_revision"] == bundle["head"]["generation"] == 0
    assert bundle["previous_bundle_digest"] == INITIAL_PREVIOUS_BUNDLE_DIGEST
    assert bundle["provider_authentication_verified"] is False
    assert bundle["runtime_authority_granted"] is False
    assert bundle["routing_influence_applied"] is False
    assert bundle["execution_permission_granted"] is False

    for field in (
        "provider_authentication_verified",
        "runtime_authority_granted",
        "routing_influence_applied",
        "execution_permission_granted",
    ):
        forged = dict(bundle)
        forged[field] = True
        assert verify_activation_snapshot_bundle(
            forged,
            cell_identity=identity,
            expected_deployment_scope_digest=deployment,
        ) == (False, field)


def test_bundle_detaches_from_mutable_builder_inputs() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    selection = _selection(two_variants=True)
    original_variant_count = len(selection["variants"])
    bundle = build_activation_snapshot_bundle(
        deployment_scope_digest=deployment,
        cell_identity=identity,
        store_revision=0,
        previous_bundle_digest=INITIAL_PREVIOUS_BUNDLE_DIGEST,
        **selection,
    )
    selection["variants"].clear()
    selection["head"]["active_variant_digests"].clear()
    selection["variant_ceilings"][0]["authority_scope_digests"].clear()
    assert len(bundle["variants"]) == original_variant_count
    assert len(bundle["head"]["active_variant_digests"]) == original_variant_count
    assert bundle["variant_ceilings"][0]["authority_scope_digests"]
    assert verify_activation_snapshot_bundle(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (True, None)

    canonical = canonicalize_activation_snapshot_bundle(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    )
    bundle["variants"].clear()
    frozen = json.loads(canonical)
    assert len(frozen["variants"]) == original_variant_count


def test_publication_rebinds_external_current_heads_and_returns_frozen_bytes() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    bundle = _bundle(identity=identity, deployment_scope_digest=deployment)
    expected = {
        "expected_profile_head_digest": bundle["expected_profile_head_digest"],
        "expected_policy_head_digest": bundle["expected_policy_head_digest"],
        "expected_resource_head_digest": bundle["expected_resource_head_digest"],
        "expected_domain_head_digest": bundle["expected_domain_head_digest"],
        "expected_environment_head_digest": bundle[
            "expected_environment_head_digest"
        ],
        "expected_charter_ceiling_digest": bundle["charter_ceiling"][
            "ceiling_digest"
        ],
        "expected_expressed_ceiling_digest": bundle["expressed_ceiling"][
            "ceiling_digest"
        ],
    }
    assert verify_activation_snapshot_publication(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
        **expected,
    ) == (True, None)
    frozen = canonicalize_activation_snapshot_publication(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
        **expected,
    )
    assert type(frozen) is bytes
    assert json.loads(frozen)["bundle_digest"] == bundle["bundle_digest"]

    stale_policy = {**expected, "expected_policy_head_digest": _digest("old")}
    assert verify_activation_snapshot_publication(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
        **stale_policy,
    ) == (False, "current_policy_head_digest_binding")


def test_verified_projection_is_exactly_compatible_with_read_only_mirror() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    bundle = _bundle(identity=identity, deployment_scope_digest=deployment)
    projected = project_activation_snapshot_for_mirror(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    )
    record = build_activation_mirror_record(
        capability_id="detect.fixture",
        snapshot=projected,
    )
    assert record["classification"] == ACTIVE_MATCH
    assert record["runtime_authority_granted"] is False


@pytest.mark.parametrize(
    "field",
    [
        "expected_activation_head_digest",
        "expected_profile_head_digest",
        "expected_policy_head_digest",
        "expected_resource_head_digest",
        "expected_domain_head_digest",
        "expected_environment_head_digest",
    ],
)
def test_every_stale_current_pointer_is_rejected(field: str) -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    bundle = _bundle(identity=identity, deployment_scope_digest=deployment)
    stale = dict(bundle)
    stale[field] = _digest(f"stale:{field}")
    ok, reason = verify_activation_snapshot_bundle(
        stale,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    )
    assert ok is False
    assert reason is not None and reason.startswith("selection:stale_")


def test_bundle_refuses_cross_cell_and_cross_deployment_transplant() -> None:
    identity = _identity("one")
    deployment = _digest("deployment:one")
    bundle = _bundle(identity=identity, deployment_scope_digest=deployment)
    assert verify_activation_snapshot_bundle(
        bundle,
        cell_identity=_identity("two"),
        expected_deployment_scope_digest=deployment,
    ) == (False, "cell_identity_binding")
    assert verify_activation_snapshot_bundle(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=_digest("deployment:two"),
    ) == (False, "deployment_scope_binding")


def test_bundle_refuses_noncanonical_oversized_and_nonexact_wire_values() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    with pytest.raises(ActivationSnapshotContractError, match="store_revision"):
        build_activation_snapshot_bundle(
            deployment_scope_digest=deployment,
            cell_identity=identity,
            store_revision=True,
            previous_bundle_digest=INITIAL_PREVIOUS_BUNDLE_DIGEST,
            **_selection(),
        )

    oversized = _selection()
    oversized["variants"] = [object()] * (MAX_VARIANTS_PER_SET * 2 + 1)
    with pytest.raises(ActivationSnapshotContractError, match="exceeds"):
        build_activation_snapshot_bundle(
            deployment_scope_digest=deployment,
            cell_identity=identity,
            store_revision=0,
            previous_bundle_digest=INITIAL_PREVIOUS_BUNDLE_DIGEST,
            **oversized,
        )

    bundle = _bundle(
        identity=identity,
        deployment_scope_digest=deployment,
        two_variants=True,
    )
    noncanonical = dict(bundle)
    noncanonical["variants"] = list(reversed(bundle["variants"]))
    assert verify_activation_snapshot_bundle(
        noncanonical,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "variant_order")

    class DictSubclass(dict):
        pass

    assert verify_activation_snapshot_bundle(
        DictSubclass(bundle),
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "not_mapping")


def test_partially_validated_records_never_invoke_hostile_equality() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    bundle = _bundle(
        identity=identity,
        deployment_scope_digest=deployment,
        two_variants=True,
    )

    class HostileEquality:
        def __eq__(self, _other):
            raise RuntimeError("HOSTILE_EQ_ESCAPED")

    hostile = dict(bundle)
    hostile["variants"] = [dict(item) for item in bundle["variants"]]
    hostile["variants"][1]["schema_version"] = HostileEquality()
    ok, reason = verify_activation_snapshot_bundle(
        hostile,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    )
    assert ok is False
    assert reason == "selection:schema_version"


def test_transition_binds_scope_bundle_revision_and_activation_head_chain() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    current = _bundle(identity=identity, deployment_scope_digest=deployment)
    proposed = _bundle(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=1,
        previous_head_digest=current["head"]["head_digest"],
        previous_bundle_digest=current["bundle_digest"],
        suffix="b",
    )
    assert verify_activation_snapshot_transition(
        current,
        proposed,
        expected_current_bundle_digest=current["bundle_digest"],
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (True, None)

    assert verify_activation_snapshot_transition(
        current,
        proposed,
        expected_current_bundle_digest=_digest("stale-bundle"),
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "stale_current_bundle")

    wrong_bundle_predecessor = _bundle(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=1,
        previous_head_digest=current["head"]["head_digest"],
        previous_bundle_digest=_digest("wrong-bundle-predecessor"),
        suffix="b",
    )
    assert verify_activation_snapshot_transition(
        current,
        wrong_bundle_predecessor,
        expected_current_bundle_digest=current["bundle_digest"],
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "previous_bundle_binding")

    wrong_head_predecessor = _bundle(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=1,
        previous_head_digest=_digest("wrong-head-predecessor"),
        previous_bundle_digest=current["bundle_digest"],
        suffix="b",
    )
    assert verify_activation_snapshot_transition(
        current,
        wrong_head_predecessor,
        expected_current_bundle_digest=current["bundle_digest"],
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "activation_head:previous_head_binding")


def test_transition_refuses_generation_skip_and_old_bundle_aba_reuse() -> None:
    identity = _identity()
    deployment = _digest("deployment:a")
    current = _bundle(identity=identity, deployment_scope_digest=deployment)
    skipped = _bundle(
        identity=identity,
        deployment_scope_digest=deployment,
        generation=2,
        previous_head_digest=current["head"]["head_digest"],
        previous_bundle_digest=current["bundle_digest"],
        suffix="c",
    )
    assert verify_activation_snapshot_transition(
        current,
        skipped,
        expected_current_bundle_digest=current["bundle_digest"],
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "revision_step")

    # A rollback may reselect old content only in a newly chained generation;
    # re-publishing the old immutable bundle itself is an ABA attempt.
    assert verify_activation_snapshot_transition(
        current,
        current,
        expected_current_bundle_digest=current["bundle_digest"],
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    ) == (False, "revision_step")
