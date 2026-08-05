# SPDX-License-Identifier: BUSL-1.1
"""Adversarial tests for the authority-free capability activation contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from waggledance.core.capabilities import activation_contracts as C


def _d(char: str) -> str:
    return "sha256:" + char * 64


def _variant_ceiling() -> C.AuthorityCeilingV1:
    return C.build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_d("b"), _d("a")],
    )


def _charter_ceiling() -> C.AuthorityCeilingV1:
    return C.build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_d("a"), _d("c")],
    )


def _expressed_ceiling(
    *, risk: str = "internal_memory", scopes: object = None
) -> C.AuthorityCeilingV1:
    return C.build_authority_ceiling(
        max_risk_class=risk,
        authority_scope_digests=[_d("a")] if scopes is None else scopes,
    )


def _variant(
    *,
    artifact: str = "1",
    risk: str = "internal_memory",
    ceiling: C.AuthorityCeilingV1 | None = None,
) -> C.CapabilityVariantV1:
    ceiling = ceiling or _variant_ceiling()
    return C.build_capability_variant(
        family_id="solve.math",
        risk_class=risk,
        artifact_digest=_d(artifact),
        input_schema_digest=_d("2"),
        output_schema_digest=_d("3"),
        compatibility_digest=_d("4"),
        authority_ceiling_digest=ceiling.ceiling_digest,
    )


def _context(
    *,
    charter: C.AuthorityCeilingV1 | None = None,
    expressed: C.AuthorityCeilingV1 | None = None,
    profile: str = "5",
) -> C.ExpressionContextV1:
    charter = charter or _charter_ceiling()
    expressed = expressed or _expressed_ceiling()
    return C.build_expression_context(
        profile_head_digest=_d(profile),
        policy_head_digest=_d("6"),
        resource_head_digest=_d("7"),
        domain_head_digest=_d("8"),
        environment_head_digest=_d("9"),
        charter_ceiling_digest=charter.ceiling_digest,
        expressed_ceiling_digest=expressed.ceiling_digest,
    )


def _initial_head(
    *,
    active: object = None,
    shadow: object = None,
    context: C.ExpressionContextV1 | None = None,
) -> C.ActivationHeadV1:
    variant = _variant()
    return C.build_activation_head(
        generation=0,
        previous_head_digest=C.INITIAL_PREVIOUS_HEAD_DIGEST,
        expression_context_digest=(context or _context()).context_digest,
        active_variant_digests=[variant.variant_digest] if active is None else active,
        shadow_variant_digests=[] if shadow is None else shadow,
    )


def _selection_kwargs() -> dict:
    variant_ceiling = _variant_ceiling()
    charter = _charter_ceiling()
    expressed = _expressed_ceiling()
    active = _variant(artifact="1", ceiling=variant_ceiling)
    shadow = _variant(artifact="a", ceiling=variant_ceiling)
    context = _context(charter=charter, expressed=expressed)
    head = _initial_head(
        active=[active.variant_digest],
        shadow=[shadow.variant_digest],
        context=context,
    )
    return {
        "head": head.to_mapping(),
        "expected_activation_head_digest": head.head_digest,
        "context": context.to_mapping(),
        "variants": [shadow.to_mapping(), active.to_mapping()],
        "variant_ceilings": [variant_ceiling.to_mapping()],
        "charter_ceiling": charter.to_mapping(),
        "expressed_ceiling": expressed.to_mapping(),
        "expected_profile_head_digest": context.profile_head_digest,
        "expected_policy_head_digest": context.policy_head_digest,
        "expected_resource_head_digest": context.resource_head_digest,
        "expected_domain_head_digest": context.domain_head_digest,
        "expected_environment_head_digest": context.environment_head_digest,
    }


def test_known_answer_vectors_lock_all_canonical_recipes() -> None:
    variant_ceiling = _variant_ceiling()
    charter_ceiling = _charter_ceiling()
    expressed = _expressed_ceiling()
    variant = _variant(ceiling=variant_ceiling)
    context = _context(charter=charter_ceiling, expressed=expressed)
    head = _initial_head(context=context)

    assert variant_ceiling.ceiling_digest == (
        "sha256:8776ed2c412f892180918103c0c5aeb2"
        "43877dbe44e194c4288595281646ae39"
    )
    assert charter_ceiling.ceiling_digest == (
        "sha256:f368c98289ca1007778dfd8b68e43eb1"
        "ff9f60370d61af0d3f65ee7ad47cf3d0"
    )
    assert expressed.ceiling_digest == (
        "sha256:23820dca9ab41c0db8cb2b5107e126d4"
        "315639212cf74e31a9f5081c3fd926ec"
    )
    assert variant.variant_digest == (
        "sha256:e12225ecc1981ca39ca940d3b7fd8a63"
        "f444cccd2327a8819d3243ec6a3728bd"
    )
    assert context.context_digest == (
        "sha256:92d3caa4e25a98f2b9612c36b48d6ea"
        "756320d708183f12d92dc8b62a3ac3298"
    )
    assert head.head_digest == (
        "sha256:94ad1e45a2762128e8efa03ef08975cc"
        "af01fd309a260415de800987181821c5"
    )


def test_set_inputs_are_canonical_and_permutation_invariant() -> None:
    first = C.build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_d("a"), _d("b")],
    )
    permuted = C.build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_d("b"), _d("a")],
    )
    assert first == permuted

    v1, v2 = _variant(artifact="1"), _variant(artifact="a")
    left = _initial_head(active=[v1.variant_digest, v2.variant_digest])
    right = _initial_head(active=[v2.variant_digest, v1.variant_digest])
    assert left == right
    assert list(left.active_variant_digests) == sorted(left.active_variant_digests)


@pytest.mark.parametrize(
    ("verify", "mapping"),
    [
        (C.verify_authority_ceiling, lambda: _variant_ceiling().to_mapping()),
        (C.verify_capability_variant, lambda: _variant().to_mapping()),
        (C.verify_expression_context, lambda: _context().to_mapping()),
        (C.verify_activation_head, lambda: _initial_head().to_mapping()),
    ],
)
def test_contracts_require_exact_keysets(verify, mapping) -> None:
    honest = mapping()
    assert verify(honest) == (True, None)

    absent = dict(honest)
    absent.pop(next(iter(absent)))
    assert verify(absent) == (False, "keyset")

    smuggled = dict(honest)
    smuggled["grants_runtime_authority"] = True
    assert verify(smuggled) == (False, "keyset")


def test_expression_context_has_heads_and_constraints_but_no_grant_surface() -> None:
    mapping = _context().to_mapping()
    assert set(mapping) == C.EXPRESSION_CONTEXT_KEYS
    assert {
        "profile_head_digest",
        "policy_head_digest",
        "resource_head_digest",
        "domain_head_digest",
        "environment_head_digest",
        "charter_ceiling_digest",
        "expressed_ceiling_digest",
    } <= set(mapping)
    assert not any("grant" in key or "allow" in key for key in mapping)


def test_wire_boundaries_reject_dict_subclasses_and_str_subclasses() -> None:
    class DictSubclass(dict):
        pass

    class EqAnyStr(str):
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    assert C.verify_capability_variant(DictSubclass(_variant().to_mapping())) == (
        False,
        "not_mapping",
    )
    forged = _variant().to_mapping()
    forged["variant_digest"] = EqAnyStr(forged["variant_digest"])
    assert C.verify_capability_variant(forged) == (False, "variant_digest")


def test_dataclasses_and_collection_fields_are_immutable() -> None:
    ceiling = _variant_ceiling()
    head = _initial_head()
    assert type(ceiling.authority_scope_digests) is tuple
    assert type(head.active_variant_digests) is tuple
    with pytest.raises(FrozenInstanceError):
        ceiling.max_risk_class = "external_effect"
    with pytest.raises(FrozenInstanceError):
        head.generation = 10


def test_mutated_frozen_instances_still_fail_verification_closed() -> None:
    """``frozen=True`` is ergonomic; public verification is the boundary."""

    ceiling = _variant_ceiling()
    object.__setattr__(ceiling, "authority_scope_digests", None)
    assert C.verify_authority_ceiling(ceiling) == (
        False,
        "authority_scope_digests",
    )
    deleted_ceiling = _variant_ceiling()
    object.__delattr__(deleted_ceiling, "ceiling_digest")
    assert C.verify_authority_ceiling(deleted_ceiling) == (
        False,
        "malformed_instance",
    )

    head = _initial_head()
    object.__setattr__(head, "generation", True)
    assert C.verify_activation_head(head) == (False, "generation")
    deleted_head = _initial_head()
    object.__delattr__(deleted_head, "previous_head_digest")
    assert C.verify_activation_head(deleted_head) == (
        False,
        "malformed_instance",
    )

    variant = _variant()
    object.__delattr__(variant, "family_id")
    assert C.verify_capability_variant(variant) == (
        False,
        "malformed_instance",
    )

    context = _context()
    object.__delattr__(context, "policy_head_digest")
    assert C.verify_expression_context(context) == (
        False,
        "malformed_instance",
    )


def test_wire_collection_fields_are_json_lists_not_python_tuples() -> None:
    ceiling = _variant_ceiling().to_mapping()
    ceiling["authority_scope_digests"] = tuple(
        ceiling["authority_scope_digests"]
    )
    assert C.verify_authority_ceiling(ceiling) == (
        False,
        "authority_scope_digests",
    )

    head = _initial_head().to_mapping()
    head["active_variant_digests"] = tuple(head["active_variant_digests"])
    assert C.verify_activation_head(head) == (
        False,
        "active_variant_digests",
    )


@pytest.mark.parametrize(
    "risk", ["", "low", "critical", "EXTERNAL_EFFECT", None, 3, True]
)
def test_unknown_or_nonexact_risk_taxonomy_rejected(risk) -> None:
    with pytest.raises(C.CapabilityActivationContractError):
        C.build_authority_ceiling(
            max_risk_class=risk, authority_scope_digests=[]
        )


@pytest.mark.parametrize(
    "family", ["", "Solve.Math", "solve/math", ".solve", "solve..math", 42]
)
def test_noncanonical_family_identity_rejected(family) -> None:
    with pytest.raises(C.CapabilityActivationContractError):
        C.build_capability_variant(
            family_id=family,
            risk_class="informational",
            artifact_digest=_d("1"),
            input_schema_digest=_d("2"),
            output_schema_digest=_d("3"),
            compatibility_digest=_d("4"),
            authority_ceiling_digest=_variant_ceiling().ceiling_digest,
        )


def test_ceiling_subset_and_lower_or_equal_risk_is_the_only_valid_direction() -> None:
    parent = _variant_ceiling()  # local_artifact, {a, b}
    equal = C.build_authority_ceiling(
        max_risk_class="local_artifact", authority_scope_digests=[_d("a"), _d("b")]
    )
    narrower = C.build_authority_ceiling(
        max_risk_class="internal_memory", authority_scope_digests=[_d("a")]
    )
    extra_scope = C.build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_d("a"), _d("c")],
    )
    higher_risk = C.build_authority_ceiling(
        max_risk_class="external_effect", authority_scope_digests=[_d("a")]
    )

    assert C.verify_ceiling_narrowing(parent, equal) == (True, None)
    assert C.verify_ceiling_narrowing(parent, narrower) == (True, None)
    assert C.verify_ceiling_narrowing(parent, extra_scope) == (
        False,
        "scope_widening",
    )
    assert C.verify_ceiling_narrowing(parent, higher_risk) == (
        False,
        "risk_widening",
    )


def test_expression_must_narrow_both_variant_and_charter_ceilings() -> None:
    variant_ceiling = _variant_ceiling()  # {a, b}, local_artifact
    charter_ceiling = _charter_ceiling()  # {a, c}, internal_memory
    expressed = _expressed_ceiling()  # intersection {a}, internal_memory
    variant = _variant(ceiling=variant_ceiling)
    context = _context(charter=charter_ceiling, expressed=expressed)

    assert C.verify_expression_constraints(
        variant=variant,
        context=context,
        variant_ceiling=variant_ceiling,
        charter_ceiling=charter_ceiling,
        expressed_ceiling=expressed,
    ) == (True, None)

    outside_charter = C.build_authority_ceiling(
        max_risk_class="internal_memory", authority_scope_digests=[_d("b")]
    )
    outside_charter_context = _context(
        charter=charter_ceiling, expressed=outside_charter
    )
    assert C.verify_expression_constraints(
        variant=variant,
        context=outside_charter_context,
        variant_ceiling=variant_ceiling,
        charter_ceiling=charter_ceiling,
        expressed_ceiling=outside_charter,
    ) == (False, "charter_scope_widening")

    outside_variant = C.build_authority_ceiling(
        max_risk_class="internal_memory", authority_scope_digests=[_d("c")]
    )
    outside_variant_context = _context(
        charter=charter_ceiling, expressed=outside_variant
    )
    assert C.verify_expression_constraints(
        variant=variant,
        context=outside_variant_context,
        variant_ceiling=variant_ceiling,
        charter_ceiling=charter_ceiling,
        expressed_ceiling=outside_variant,
    ) == (False, "variant_scope_widening")


def test_expression_rejects_binding_substitution_and_risk_mismatch() -> None:
    variant_ceiling = _variant_ceiling()
    charter_ceiling = _charter_ceiling()
    expressed = _expressed_ceiling()
    variant = _variant(ceiling=variant_ceiling)
    context = _context(charter=charter_ceiling, expressed=expressed)

    substituted_variant_ceiling = C.build_authority_ceiling(
        max_risk_class="local_artifact", authority_scope_digests=[_d("a")]
    )
    assert C.verify_expression_constraints(
        variant=variant,
        context=context,
        variant_ceiling=substituted_variant_ceiling,
        charter_ceiling=charter_ceiling,
        expressed_ceiling=expressed,
    ) == (False, "variant_ceiling_binding")

    too_low = _expressed_ceiling(risk="informational")
    too_low_context = _context(charter=charter_ceiling, expressed=too_low)
    assert C.verify_expression_constraints(
        variant=variant,
        context=too_low_context,
        variant_ceiling=variant_ceiling,
        charter_ceiling=charter_ceiling,
        expressed_ceiling=too_low,
    ) == (False, "variant_risk_exceeds_expression")


@pytest.mark.parametrize(
    "field",
    [
        "profile_head_digest",
        "policy_head_digest",
        "resource_head_digest",
        "domain_head_digest",
        "environment_head_digest",
        "charter_ceiling_digest",
        "expressed_ceiling_digest",
    ],
)
def test_every_expression_head_is_digest_bound(field: str) -> None:
    tampered = _context().to_mapping()
    tampered[field] = _d("f")
    assert C.verify_expression_context(tampered) == (
        False,
        "context_digest_mismatch",
    )


def test_duplicate_variants_and_active_shadow_overlap_fail_closed() -> None:
    digest = _variant().variant_digest
    with pytest.raises(
        C.CapabilityActivationContractError,
        match="duplicate",
    ):
        _initial_head(active=[digest, digest])
    with pytest.raises(
        C.CapabilityActivationContractError,
        match="active and shadow",
    ):
        _initial_head(active=[digest], shadow=[digest])

    duplicate_wire = _initial_head().to_mapping()
    duplicate_wire["active_variant_digests"].append(digest)
    assert C.verify_activation_head(duplicate_wire) == (
        False,
        "active_variant_digests_duplicate",
    )


def test_activation_wire_requires_canonical_set_order() -> None:
    v1, v2 = _variant(artifact="1"), _variant(artifact="a")
    head = _initial_head(active=[v1.variant_digest, v2.variant_digest]).to_mapping()
    head["active_variant_digests"].reverse()
    assert C.verify_activation_head(head) == (
        False,
        "active_variant_digests_order",
    )


def test_initial_and_noninitial_predecessor_rules_are_bidirectional() -> None:
    with pytest.raises(
        C.CapabilityActivationContractError, match="generation zero"
    ):
        C.build_activation_head(
            generation=0,
            previous_head_digest=_d("f"),
            expression_context_digest=_context().context_digest,
            active_variant_digests=[],
            shadow_variant_digests=[],
        )
    with pytest.raises(
        C.CapabilityActivationContractError, match="noninitial"
    ):
        C.build_activation_head(
            generation=1,
            previous_head_digest=C.INITIAL_PREVIOUS_HEAD_DIGEST,
            expression_context_digest=_context().context_digest,
            active_variant_digests=[],
            shadow_variant_digests=[],
        )


def test_cas_transition_and_rollback_use_new_heads_and_generations() -> None:
    v1, v2 = _variant(artifact="1"), _variant(artifact="a")
    first = _initial_head(
        active=[v1.variant_digest], shadow=[v2.variant_digest]
    )
    promoted = C.build_next_activation_head(
        first,
        expected_current_head_digest=first.head_digest,
        expression_context_digest=first.expression_context_digest,
        active_variant_digests=[v2.variant_digest],
        shadow_variant_digests=[v1.variant_digest],
    )
    rollback = C.build_next_activation_head(
        promoted,
        expected_current_head_digest=promoted.head_digest,
        expression_context_digest=first.expression_context_digest,
        active_variant_digests=[v1.variant_digest],
        shadow_variant_digests=[v2.variant_digest],
    )

    assert C.verify_activation_transition(
        first,
        promoted,
        expected_current_head_digest=first.head_digest,
    ) == (True, None)
    assert C.verify_activation_transition(
        promoted,
        rollback,
        expected_current_head_digest=promoted.head_digest,
    ) == (True, None)
    assert rollback.generation == 2
    assert rollback.previous_head_digest == promoted.head_digest
    assert rollback.active_variant_digests == first.active_variant_digests
    assert rollback.shadow_variant_digests == first.shadow_variant_digests
    assert rollback.head_digest not in {first.head_digest, promoted.head_digest}


def test_stale_and_aba_attempts_fail_closed() -> None:
    v1, v2 = _variant(artifact="1"), _variant(artifact="a")
    first = _initial_head(active=[v1.variant_digest])
    second = C.build_next_activation_head(
        first,
        expected_current_head_digest=first.head_digest,
        expression_context_digest=first.expression_context_digest,
        active_variant_digests=[v2.variant_digest],
        shadow_variant_digests=[],
    )
    third = C.build_next_activation_head(
        second,
        expected_current_head_digest=second.head_digest,
        expression_context_digest=first.expression_context_digest,
        active_variant_digests=[v1.variant_digest],
        shadow_variant_digests=[],
    )

    # A stale reader expected generation zero while the current head is one.
    assert C.verify_activation_transition(
        second,
        third,
        expected_current_head_digest=first.head_digest,
    ) == (False, "stale_current_head")
    # Stale compare-and-swap rejection precedes parsing any proposed payload.
    assert C.verify_activation_transition(
        second,
        object(),
        expected_current_head_digest=first.head_digest,
    ) == (False, "stale_current_head")
    # Reusing the original A object as the proposed A after A->B is not a
    # rollback: its old generation and predecessor make the ABA shape reject.
    assert C.verify_activation_transition(
        second,
        first,
        expected_current_head_digest=second.head_digest,
    ) == (False, "generation_step")
    with pytest.raises(
        C.CapabilityActivationContractError, match="stale"
    ):
        C.build_next_activation_head(
            second,
            expected_current_head_digest=first.head_digest,
            expression_context_digest=second.expression_context_digest,
            active_variant_digests=[v1.variant_digest],
            shadow_variant_digests=[],
        )


def test_generation_skip_and_wrong_predecessor_reject_relationally() -> None:
    current = _initial_head()
    skipped = C.build_activation_head(
        generation=2,
        previous_head_digest=current.head_digest,
        expression_context_digest=current.expression_context_digest,
        active_variant_digests=current.active_variant_digests,
        shadow_variant_digests=current.shadow_variant_digests,
    )
    assert C.verify_activation_transition(
        current, skipped, expected_current_head_digest=current.head_digest
    ) == (False, "generation_step")

    wrong_previous = C.build_activation_head(
        generation=1,
        previous_head_digest=_d("f"),
        expression_context_digest=current.expression_context_digest,
        active_variant_digests=current.active_variant_digests,
        shadow_variant_digests=current.shadow_variant_digests,
    )
    assert C.verify_activation_transition(
        current,
        wrong_previous,
        expected_current_head_digest=current.head_digest,
    ) == (False, "previous_head_binding")


def test_generation_is_exact_bounded_int_and_never_wraps() -> None:
    for malformed in (True, -1, C.MAX_GENERATION + 1, 1.0, "1"):
        with pytest.raises(C.CapabilityActivationContractError):
            C.build_activation_head(
                generation=malformed,
                previous_head_digest=C.INITIAL_PREVIOUS_HEAD_DIGEST,
                expression_context_digest=_context().context_digest,
                active_variant_digests=[],
                shadow_variant_digests=[],
            )

    exhausted = C.build_activation_head(
        generation=C.MAX_GENERATION,
        previous_head_digest=_d("f"),
        expression_context_digest=_context().context_digest,
        active_variant_digests=[],
        shadow_variant_digests=[],
    )
    assert C.verify_activation_transition(
        exhausted,
        exhausted,
        expected_current_head_digest=exhausted.head_digest,
    ) == (False, "generation_exhausted")


def test_end_to_end_selection_binds_head_context_variants_and_ceilings() -> None:
    kwargs = _selection_kwargs()
    assert C.verify_activation_selection(**kwargs) == (True, None)

    missing_variant = dict(kwargs)
    missing_variant["variants"] = kwargs["variants"][:1]
    assert C.verify_activation_selection(**missing_variant) == (
        False,
        "variant_set_binding",
    )

    extra_variant = dict(kwargs)
    extra_variant["variants"] = [
        *kwargs["variants"],
        _variant(artifact="f").to_mapping(),
    ]
    assert C.verify_activation_selection(**extra_variant) == (
        False,
        "variant_set_binding",
    )

    missing_ceiling = dict(kwargs)
    missing_ceiling["variant_ceilings"] = []
    assert C.verify_activation_selection(**missing_ceiling) == (
        False,
        "variant_ceiling_set_binding",
    )


def test_end_to_end_selection_rejects_stale_activation_and_context_heads() -> None:
    kwargs = _selection_kwargs()
    stale_activation = dict(kwargs)
    stale_activation["expected_activation_head_digest"] = _d("f")
    assert C.verify_activation_selection(**stale_activation) == (
        False,
        "stale_activation_head",
    )

    mismatched_context = dict(kwargs)
    mismatched_context["context"] = _context(profile="f").to_mapping()
    assert C.verify_activation_selection(**mismatched_context) == (
        False,
        "expression_context_binding",
    )


@pytest.mark.parametrize(
    ("argument", "reason"),
    [
        ("expected_profile_head_digest", "stale_profile_head"),
        ("expected_policy_head_digest", "stale_policy_head"),
        ("expected_resource_head_digest", "stale_resource_head"),
        ("expected_domain_head_digest", "stale_domain_head"),
        ("expected_environment_head_digest", "stale_environment_head"),
    ],
)
def test_end_to_end_selection_requires_every_current_plane_head(
    argument: str, reason: str
) -> None:
    kwargs = _selection_kwargs()
    kwargs[argument] = _d("f")
    assert C.verify_activation_selection(**kwargs) == (False, reason)


def test_end_to_end_selection_checks_shadow_variant_constraints_too() -> None:
    safe_ceiling = _variant_ceiling()  # contains expressed scope a
    unsafe_shadow_ceiling = C.build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_d("b")],
    )
    charter = _charter_ceiling()
    expressed = _expressed_ceiling()
    active = _variant(artifact="1", ceiling=safe_ceiling)
    shadow = _variant(artifact="a", ceiling=unsafe_shadow_ceiling)
    context = _context(charter=charter, expressed=expressed)
    head = _initial_head(
        active=[active.variant_digest],
        shadow=[shadow.variant_digest],
        context=context,
    )
    kwargs = _selection_kwargs()
    kwargs.update(
        {
            "head": head,
            "expected_activation_head_digest": head.head_digest,
            "context": context,
            "variants": [active, shadow],
            "variant_ceilings": [safe_ceiling, unsafe_shadow_ceiling],
            "charter_ceiling": charter,
            "expressed_ceiling": expressed,
            "expected_profile_head_digest": context.profile_head_digest,
            "expected_policy_head_digest": context.policy_head_digest,
            "expected_resource_head_digest": context.resource_head_digest,
            "expected_domain_head_digest": context.domain_head_digest,
            "expected_environment_head_digest": context.environment_head_digest,
        }
    )
    assert C.verify_activation_selection(**kwargs) == (
        False,
        "variant_expression_variant_scope_widening",
    )


@pytest.mark.parametrize(
    ("role", "reason"),
    [
        ("active", "ambiguous_active_family"),
        ("shadow", "ambiguous_shadow_family"),
    ],
)
def test_selection_rejects_multiple_alleles_in_one_expression_role(
    role: str, reason: str
) -> None:
    variant_ceiling = _variant_ceiling()
    charter = _charter_ceiling()
    expressed = _expressed_ceiling()
    first = _variant(artifact="1", ceiling=variant_ceiling)
    second = _variant(artifact="f", ceiling=variant_ceiling)
    context = _context(charter=charter, expressed=expressed)
    selected = [first.variant_digest, second.variant_digest]
    head = _initial_head(
        active=selected if role == "active" else [],
        shadow=selected if role == "shadow" else [],
        context=context,
    )
    assert C.verify_activation_selection(
        head=head.to_mapping(),
        expected_activation_head_digest=head.head_digest,
        context=context.to_mapping(),
        variants=[first.to_mapping(), second.to_mapping()],
        variant_ceilings=[variant_ceiling.to_mapping()],
        charter_ceiling=charter.to_mapping(),
        expressed_ceiling=expressed.to_mapping(),
        expected_profile_head_digest=context.profile_head_digest,
        expected_policy_head_digest=context.policy_head_digest,
        expected_resource_head_digest=context.resource_head_digest,
        expected_domain_head_digest=context.domain_head_digest,
        expected_environment_head_digest=context.environment_head_digest,
    ) == (False, reason)


def test_selection_aggregate_is_bounded_before_copy_or_item_parsing() -> None:
    kwargs = _selection_kwargs()
    kwargs["variants"] = [object()] * (C.MAX_VARIANTS_PER_SET * 2 + 1)
    assert C.verify_activation_selection(**kwargs) == (False, "variants")
