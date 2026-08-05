"""Focused tests for the pure trusted-provenance registry contract."""

from __future__ import annotations

import hashlib

import pytest

from waggledance.core.orchestration.evidence_consensus import (
    PROVENANCE_DIMENSIONS,
)
from waggledance.core.orchestration.provenance_registry import (
    INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
    MAX_PROVENANCE_BINDINGS,
    ProvenanceRegistryError,
    ProvenanceResolutionError,
    build_provenance_registry_snapshot,
    build_trusted_provenance_binding,
    derive_provenance_registry_head_digest,
    parse_provenance_registry_snapshot,
    parse_trusted_provenance_binding,
    resolve_trusted_provenance,
    verify_provenance_registry_snapshot,
    verify_provenance_registry_transition,
    verify_trusted_provenance_binding,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _binding(
    label: str,
    *,
    key_label: str | None = None,
    cell_label: str | None = None,
    scope_label: str | None = None,
    lineage_label: str | None = None,
    provenance_label: str | None = None,
    status: str = "active",
) -> dict[str, object]:
    provenance = provenance_label or label
    value = build_trusted_provenance_binding(
        signer_cell_id=_digest(f"cell:{cell_label or label}"),
        reviewer_activation_scope_digest=_digest(
            f"scope:{scope_label or label}"
        ),
        signing_key_digest=_digest(f"key:{key_label or label}"),
        reviewer_lineage_digest=_digest(
            f"lineage:{lineage_label or provenance}"
        ),
        model_digest=_digest(f"model:{provenance}"),
        provider_digest=_digest(f"provider:{provenance}"),
        tool_digest=_digest(f"tool:{provenance}"),
        data_corpus_digest=_digest(f"corpus:{provenance}"),
        host_digest=_digest(f"host:{provenance}"),
        review_policy_digest=_digest(f"policy:{provenance}"),
        status=status,
    )
    return value.to_mapping()


def _snapshot(
    bindings: list[object],
    *,
    generation: int = 0,
    previous: str = INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
) -> dict[str, object]:
    return build_provenance_registry_snapshot(
        generation=generation,
        previous_registry_head_digest=previous,
        bindings=bindings,
    ).to_mapping()


def _rebuild_binding(
    original: dict[str, object], **changes: object
) -> dict[str, object]:
    fields = {
        name: original[name]
        for name in (
            "signer_cell_id",
            "reviewer_activation_scope_digest",
            "signing_key_digest",
            *PROVENANCE_DIMENSIONS,
            "status",
        )
    }
    fields.update(changes)
    return build_trusted_provenance_binding(**fields).to_mapping()


class _DictSubclass(dict):
    pass


class _ListSubclass(list):
    pass


class _EqAnyStr(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = str.__hash__


def test_binding_is_exact_content_addressed_and_authority_free() -> None:
    first = _binding("one")
    second = _binding("one")
    assert first == second
    assert verify_trusted_provenance_binding(first) == (True, None)
    assert set(PROVENANCE_DIMENSIONS) <= set(first)
    assert first["advisory_only"] is True
    assert first["authority_granted"] is False
    assert first["activation_performed"] is False
    assert first["routing_influence_applied"] is False
    assert "key_material" not in first
    assert "secret" not in first

    smuggled = {**first, "execution_permission_granted": True}
    assert verify_trusted_provenance_binding(smuggled) == (
        False,
        "binding_keyset",
    )


@pytest.mark.parametrize(
    "field",
    [
        "signer_cell_id",
        "reviewer_activation_scope_digest",
        "signing_key_digest",
        *PROVENANCE_DIMENSIONS,
        "status",
    ],
)
def test_every_binding_fact_is_digest_bound(field: str) -> None:
    binding = _binding("bound")
    forged = dict(binding)
    forged[field] = "revoked" if field == "status" else _digest(f"forged:{field}")
    assert verify_trusted_provenance_binding(forged)[0] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("advisory_only", False),
        ("authority_granted", True),
        ("activation_performed", True),
        ("routing_influence_applied", True),
        ("authority_granted", 0),
    ],
)
def test_binding_refuses_authority_flag_drift(field: str, value: object) -> None:
    binding = _binding("flags")
    binding[field] = value
    assert verify_trusted_provenance_binding(binding) == (
        False,
        "authority_flags",
    )


def test_binding_refuses_hostile_containers_alias_keys_and_inexact_values() -> None:
    binding = _binding("hostile")
    assert verify_trusted_provenance_binding(_DictSubclass(binding)) == (
        False,
        "not_mapping",
    )

    aliased = dict(binding)
    cell = aliased.pop("signer_cell_id")
    aliased[_EqAnyStr("signer_cell_id")] = cell
    assert verify_trusted_provenance_binding(aliased) == (False, "not_mapping")

    forged = dict(binding)
    forged["signing_key_digest"] = _EqAnyStr(binding["signing_key_digest"])
    assert verify_trusted_provenance_binding(forged)[0] is False

    with pytest.raises(ProvenanceRegistryError) as exc:
        build_trusted_provenance_binding(
            **{
                name: binding[name]
                for name in (
                    "signer_cell_id",
                    "reviewer_activation_scope_digest",
                    "signing_key_digest",
                    *PROVENANCE_DIMENSIONS,
                )
            },
            status=_EqAnyStr("active"),
        )
    assert exc.value.reason == "status"


def test_snapshot_builder_is_order_invariant_and_parser_requires_order() -> None:
    first = _binding("first")
    second = _binding("second")
    forward = _snapshot([first, second])
    reverse = _snapshot([second, first])
    assert forward == reverse
    assert verify_provenance_registry_snapshot(forward) == (True, None)
    keys = [item["signing_key_digest"] for item in forward["bindings"]]
    assert keys == sorted(keys)

    noncanonical = dict(forward)
    noncanonical["bindings"] = list(reversed(forward["bindings"]))
    assert verify_provenance_registry_snapshot(noncanonical) == (
        False,
        "binding_order",
    )


def test_snapshot_head_binds_generation_predecessor_bindings_and_flags() -> None:
    snapshot = _snapshot([_binding("head")])
    expected = derive_provenance_registry_head_digest(
        generation=0,
        previous_registry_head_digest=INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
        bindings=snapshot["bindings"],
    )
    assert snapshot["registry_head_digest"] == expected

    for field, value in (
        ("generation", 1),
        ("previous_registry_head_digest", _digest("foreign-predecessor")),
        ("authority_granted", True),
    ):
        forged = dict(snapshot)
        forged[field] = value
        assert verify_provenance_registry_snapshot(forged)[0] is False


def test_snapshot_rejects_duplicate_key_and_conflicting_rotated_provenance() -> None:
    first = _binding("one-key")
    duplicate_key = _rebuild_binding(
        first,
        signer_cell_id=_digest("different-cell"),
        reviewer_activation_scope_digest=_digest("different-scope"),
        reviewer_lineage_digest=_digest("different-lineage"),
    )
    with pytest.raises(ProvenanceRegistryError) as exc:
        _snapshot([first, duplicate_key])
    assert exc.value.reason == "duplicate_signing_key"

    rotated_but_drifted = _binding(
        "drifted",
        key_label="rotated-key",
        cell_label="shared",
        scope_label="shared",
        lineage_label="other-lineage",
    )
    original = _binding(
        "original",
        key_label="original-key",
        cell_label="shared",
        scope_label="shared",
    )
    with pytest.raises(ProvenanceRegistryError) as exc:
        _snapshot([original, rotated_but_drifted])
    assert exc.value.reason == "cell_scope_provenance_conflict"


def test_same_cell_scope_can_rotate_keys_without_changing_lineage() -> None:
    original = _binding(
        "facts",
        key_label="key-one",
        cell_label="shared-cell",
        scope_label="shared-scope",
        provenance_label="same-facts",
    )
    rotated = _binding(
        "facts",
        key_label="key-two",
        cell_label="shared-cell",
        scope_label="shared-scope",
        provenance_label="same-facts",
    )
    snapshot = _snapshot([rotated, original])
    assert verify_provenance_registry_snapshot(snapshot) == (True, None)
    assert len(snapshot["bindings"]) == 2
    assert {
        item["reviewer_lineage_digest"] for item in snapshot["bindings"]
    } == {original["reviewer_lineage_digest"]}


def test_snapshot_exact_types_count_bound_and_initial_chain_rules() -> None:
    binding = _binding("bounds")
    snapshot = _snapshot([binding])

    hostile = dict(snapshot)
    hostile["bindings"] = _ListSubclass(snapshot["bindings"])
    assert verify_provenance_registry_snapshot(hostile) == (False, "not_list")
    assert verify_provenance_registry_snapshot(_DictSubclass(snapshot)) == (
        False,
        "not_mapping",
    )
    object_in_wire_list = dict(snapshot)
    object_in_wire_list["bindings"] = [
        build_trusted_provenance_binding(
            **{
                name: binding[name]
                for name in (
                    "signer_cell_id",
                    "reviewer_activation_scope_digest",
                    "signing_key_digest",
                    *PROVENANCE_DIMENSIONS,
                    "status",
                )
            }
        )
    ]
    assert verify_provenance_registry_snapshot(object_in_wire_list) == (
        False,
        "not_mapping",
    )

    with pytest.raises(ProvenanceRegistryError) as exc:
        _snapshot([binding] * (MAX_PROVENANCE_BINDINGS + 1))
    assert exc.value.reason == "binding_count"
    with pytest.raises(ProvenanceRegistryError) as exc:
        _snapshot([binding], generation=True)
    assert exc.value.reason == "generation"
    with pytest.raises(ProvenanceRegistryError) as exc:
        _snapshot([binding], previous=_digest("wrong-initial"))
    assert exc.value.reason == "initial_predecessor"
    with pytest.raises(ProvenanceRegistryError) as exc:
        _snapshot([binding], generation=1)
    assert exc.value.reason == "non_initial_predecessor"


def test_snapshot_builder_detaches_from_mutable_inputs_and_outputs() -> None:
    source = _binding("detached")
    built = build_provenance_registry_snapshot(generation=0, bindings=[source])
    before = built.to_mapping()
    source["model_digest"] = _digest("mutated-after-build")
    assert built.to_mapping() == before

    projected = built.to_mapping()
    projected["bindings"][0]["model_digest"] = _digest("mutated-output")
    assert built.to_mapping() == before


def test_resolve_requires_pinned_head_and_returns_private_active_mapping() -> None:
    binding = _binding("active")
    snapshot = _snapshot([binding])
    resolved = resolve_trusted_provenance(
        snapshot,
        binding["signing_key_digest"],
        snapshot["registry_head_digest"],
    )
    assert resolved == binding
    assert resolved is not snapshot["bindings"][0]
    resolved["model_digest"] = _digest("caller-mutation")
    resolved_again = resolve_trusted_provenance(
        snapshot,
        binding["signing_key_digest"],
        snapshot["registry_head_digest"],
    )
    assert resolved_again == binding

    with pytest.raises(ProvenanceResolutionError) as exc:
        resolve_trusted_provenance(
            snapshot,
            binding["signing_key_digest"],
            _digest("stale-head"),
        )
    assert exc.value.reason == "registry_head_mismatch"


@pytest.mark.parametrize(
    ("kind", "expected_reason"),
    [
        ("missing", "signing_key_not_found"),
        ("revoked", "signing_key_revoked"),
        ("bad_key", "invalid_signing_key_digest"),
        ("bad_head", "invalid_expected_registry_head_digest"),
        ("bad_snapshot", "invalid_registry_snapshot"),
    ],
)
def test_resolve_fails_typed_without_fallback(
    kind: str, expected_reason: str
) -> None:
    binding = _binding(
        "lookup", status="revoked" if kind == "revoked" else "active"
    )
    snapshot = _snapshot([binding])
    key: object = binding["signing_key_digest"]
    head: object = snapshot["registry_head_digest"]
    supplied_snapshot: object = snapshot
    if kind == "missing":
        key = _digest("missing-key")
    elif kind == "bad_key":
        key = "not-a-digest"
    elif kind == "bad_head":
        head = "not-a-digest"
    elif kind == "bad_snapshot":
        supplied_snapshot = {**snapshot, "authority_granted": True}
    with pytest.raises(ProvenanceResolutionError) as exc:
        resolve_trusted_provenance(supplied_snapshot, key, head)
    assert exc.value.reason == expected_reason


def test_transition_allows_addition_rotation_and_one_way_revocation() -> None:
    original = _binding(
        "facts",
        key_label="old-key",
        cell_label="cell",
        scope_label="scope",
        provenance_label="facts",
    )
    current = _snapshot([original])
    revoked = _rebuild_binding(original, status="revoked")
    rotated = _binding(
        "facts",
        key_label="new-key",
        cell_label="cell",
        scope_label="scope",
        provenance_label="facts",
    )
    proposed = _snapshot(
        [rotated, revoked],
        generation=1,
        previous=current["registry_head_digest"],
    )
    assert verify_provenance_registry_transition(
        current,
        proposed,
        expected_current_registry_head_digest=current["registry_head_digest"],
    ) == (True, None)


def test_transition_refuses_removal_fact_changes_and_reactivation() -> None:
    active = _binding("immutable")
    current = _snapshot([active])
    removed = _snapshot(
        [], generation=1, previous=current["registry_head_digest"]
    )
    assert verify_provenance_registry_transition(
        current,
        removed,
        expected_current_registry_head_digest=current["registry_head_digest"],
    ) == (False, "binding_removed")

    drifted = _rebuild_binding(active, model_digest=_digest("changed-model"))
    changed = _snapshot(
        [drifted], generation=1, previous=current["registry_head_digest"]
    )
    assert verify_provenance_registry_transition(
        current,
        changed,
        expected_current_registry_head_digest=current["registry_head_digest"],
    ) == (False, "binding_facts_changed")

    revoked_binding = _binding("revoked", status="revoked")
    revoked_current = _snapshot([revoked_binding])
    reactivated = _rebuild_binding(revoked_binding, status="active")
    proposed_reactivation = _snapshot(
        [reactivated],
        generation=1,
        previous=revoked_current["registry_head_digest"],
    )
    assert verify_provenance_registry_transition(
        revoked_current,
        proposed_reactivation,
        expected_current_registry_head_digest=revoked_current[
            "registry_head_digest"
        ],
    ) == (False, "revocation_irreversible")


def test_transition_rejects_replay_wrong_generation_and_wrong_predecessor() -> None:
    current = _snapshot([_binding("chain")])
    valid = _snapshot(
        current["bindings"],
        generation=1,
        previous=current["registry_head_digest"],
    )

    assert verify_provenance_registry_transition(
        current,
        valid,
        expected_current_registry_head_digest=_digest("stale-current"),
    ) == (False, "stale_current_registry_head")
    assert verify_provenance_registry_transition(
        current,
        current,
        expected_current_registry_head_digest=current["registry_head_digest"],
    ) == (False, "generation_step")

    skipped = _snapshot(
        current["bindings"],
        generation=2,
        previous=current["registry_head_digest"],
    )
    assert verify_provenance_registry_transition(
        current,
        skipped,
        expected_current_registry_head_digest=current["registry_head_digest"],
    ) == (False, "generation_step")

    wrong_previous = _snapshot(
        current["bindings"],
        generation=1,
        previous=_digest("other-non-sentinel-head"),
    )
    assert verify_provenance_registry_transition(
        current,
        wrong_previous,
        expected_current_registry_head_digest=current["registry_head_digest"],
    ) == (False, "previous_registry_head_binding")


def test_parsers_return_detached_exact_mappings() -> None:
    binding = _binding("parse")
    parsed_binding = parse_trusted_provenance_binding(binding)
    parsed_binding["model_digest"] = _digest("mutated-private-copy")
    assert binding["model_digest"] != parsed_binding["model_digest"]

    snapshot = _snapshot([binding])
    parsed_snapshot = parse_provenance_registry_snapshot(snapshot)
    parsed_snapshot["bindings"][0]["model_digest"] = _digest("changed-copy")
    assert snapshot["bindings"][0]["model_digest"] != parsed_snapshot[
        "bindings"
    ][0]["model_digest"]
