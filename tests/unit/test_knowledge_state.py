from __future__ import annotations

from copy import deepcopy

import pytest

from waggledance.core.magma.knowledge_state import (
    CORROBORATED,
    MAX_CORROBORATORS,
    MAX_HISTORY_EVENTS,
    OBSERVATION_LOCAL,
    QUARANTINED,
    REVOKED,
    VERIFIED,
    KnowledgeStateError,
    build_initial_knowledge_state,
    build_knowledge_transition,
    is_structurally_admissible,
    verify_knowledge_state,
    verify_knowledge_history,
    verify_knowledge_transition,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _initial() -> dict:
    return build_initial_knowledge_state(
        claim_key_digest=_digest("1"),
        content_digest=_digest("2"),
        source_identity_digest=_digest("3"),
        source_lineage_digest=_digest("4"),
        policy_head_digest=_digest("5"),
        reason_digest=_digest("6"),
    )


def _corroborators() -> list[dict[str, str]]:
    return [
        {
            "identity_digest": _digest("7"),
            "lineage_digest": _digest("8"),
            "receipt_digest": _digest("9"),
        },
        {
            "identity_digest": _digest("a"),
            "lineage_digest": _digest("b"),
            "receipt_digest": _digest("c"),
        },
    ]


def _verified_chain() -> tuple[dict, dict, dict]:
    initial = _initial()
    corroborated = build_knowledge_transition(
        previous=initial,
        new_state=CORROBORATED,
        reason_digest=_digest("d"),
        corroborators=_corroborators(),
        evidence_head_digest=_digest("e"),
    )
    verified = build_knowledge_transition(
        previous=corroborated,
        new_state=VERIFIED,
        reason_digest=_digest("f"),
        verification_receipt_digest=_digest("0"),
    )
    return initial, corroborated, verified


def test_initial_observation_is_deterministic_and_local_only() -> None:
    first = _initial()
    second = _initial()

    assert first == second
    assert first["state"] == OBSERVATION_LOCAL
    assert first["revision"] == 0
    assert first["previous_event_digest"] is None
    assert "global_retrieval_eligible" not in first
    assert "training_eligible" not in first
    assert is_structurally_admissible(
        first,
        validated_history=[first],
        expected_policy_head_digest=_digest("5"),
        expected_evidence_head_digest=_digest("e"),
        expected_current_event_digest=first["event_digest"],
        expected_current_revision=first["revision"],
        validated_corroborators=_corroborators(),
        validated_verification_receipt_digest=_digest("0"),
    ) is False
    assert verify_knowledge_state(first) == (True, None)


def test_exact_keyset_and_digest_tampering_fail_closed() -> None:
    extra = {**_initial(), "authority": "smuggled"}
    assert verify_knowledge_state(extra)[0] is False

    tampered = {**_initial(), "global_retrieval_eligible": True}
    ok, reason = verify_knowledge_state(tampered)
    assert ok is False
    assert reason == "knowledge event keyset"

    bad_digest = deepcopy(_initial())
    bad_digest["reason_digest"] = _digest("a")
    ok, reason = verify_knowledge_state(bad_digest)
    assert ok is False
    assert reason == "event_digest mismatch"


def test_two_independent_receipt_bound_sources_are_required() -> None:
    with pytest.raises(KnowledgeStateError, match="two independent"):
        build_knowledge_transition(
            previous=_initial(),
            new_state=CORROBORATED,
            reason_digest=_digest("d"),
            corroborators=_corroborators()[:1],
            evidence_head_digest=_digest("e"),
        )

    mirrored = _corroborators()
    mirrored[0]["lineage_digest"] = _digest("4")
    with pytest.raises(KnowledgeStateError, match="cannot corroborate itself"):
        build_knowledge_transition(
            previous=_initial(),
            new_state=CORROBORATED,
            reason_digest=_digest("d"),
            corroborators=mirrored,
            evidence_head_digest=_digest("e"),
        )

    correlated = _corroborators()
    correlated[1]["lineage_digest"] = correlated[0]["lineage_digest"]
    with pytest.raises(KnowledgeStateError, match="duplicate corroborator lineage"):
        build_knowledge_transition(
            previous=_initial(),
            new_state=CORROBORATED,
            reason_digest=_digest("d"),
            corroborators=correlated,
            evidence_head_digest=_digest("e"),
        )


def test_verified_requires_separate_receipt_for_structural_admission() -> None:
    initial, corroborated, verified = _verified_chain()

    # Digest fixtures stand in for bindings already authenticated by external
    # receipt verifiers.  This pure contract does not authenticate them or grant
    # authority by itself.
    admission = {
        "validated_history": [initial, corroborated, verified],
        "expected_policy_head_digest": _digest("5"),
        "expected_evidence_head_digest": _digest("e"),
        "expected_current_event_digest": verified["event_digest"],
        "expected_current_revision": verified["revision"],
        "validated_corroborators": _corroborators(),
        "validated_verification_receipt_digest": _digest("0"),
    }
    assert is_structurally_admissible(initial, **admission) is False
    assert is_structurally_admissible(corroborated, **admission) is False
    assert is_structurally_admissible(verified, **admission) is True
    assert is_structurally_admissible(
        verified,
        **{**admission, "validated_history": [corroborated, verified]},
    ) is False
    assert is_structurally_admissible(
        verified,
        **{**admission, "expected_policy_head_digest": _digest("6")},
    ) is False
    assert is_structurally_admissible(
        verified,
        **{
            **admission,
            "validated_verification_receipt_digest": _digest("1"),
        },
    ) is False

    with pytest.raises(KnowledgeStateError, match="independent"):
        build_knowledge_transition(
            previous=corroborated,
            new_state=VERIFIED,
            reason_digest=_digest("f"),
            verification_receipt_digest=_digest("9"),
        )


def test_order_is_canonical_and_does_not_change_event_digest() -> None:
    forward = build_knowledge_transition(
        previous=_initial(),
        new_state=CORROBORATED,
        reason_digest=_digest("d"),
        corroborators=_corroborators(),
        evidence_head_digest=_digest("e"),
    )
    reverse = build_knowledge_transition(
        previous=_initial(),
        new_state=CORROBORATED,
        reason_digest=_digest("d"),
        corroborators=list(reversed(_corroborators())),
        evidence_head_digest=_digest("e"),
    )
    assert forward == reverse

    noncanonical = deepcopy(forward)
    noncanonical["corroborators"].reverse()
    assert verify_knowledge_state(noncanonical)[0] is False


def test_stale_previous_event_and_non_monotonic_revision_are_refused() -> None:
    initial, corroborated, verified = _verified_chain()

    assert verify_knowledge_transition(initial, corroborated) == (True, None)
    ok, reason = verify_knowledge_transition(verified, corroborated)
    assert ok is False
    assert reason == "revision is not exactly monotonic"

    stale = deepcopy(verified)
    stale["previous_event_digest"] = initial["event_digest"]
    assert verify_knowledge_state(stale)[0] is False


def test_revocation_and_correction_preserve_history_but_reset_visibility() -> None:
    _, _, verified = _verified_chain()
    revoked = build_knowledge_transition(
        previous=verified,
        new_state=REVOKED,
        reason_digest=_digest("1"),
    )
    assert revoked["state"] == REVOKED
    assert revoked["previous_event_digest"] == verified["event_digest"]
    admission = {
        "validated_history": [*_verified_chain(), revoked],
        "expected_policy_head_digest": _digest("5"),
        "expected_evidence_head_digest": _digest("e"),
        "expected_current_event_digest": revoked["event_digest"],
        "expected_current_revision": revoked["revision"],
        "validated_corroborators": _corroborators(),
        "validated_verification_receipt_digest": _digest("0"),
    }
    assert is_structurally_admissible(revoked, **admission) is False

    corrected = build_knowledge_transition(
        previous=revoked,
        new_state=OBSERVATION_LOCAL,
        reason_digest=_digest("2"),
        corrected_content_digest=_digest("3"),
    )
    assert corrected["previous_event_digest"] == revoked["event_digest"]
    assert corrected["content_digest"] != revoked["content_digest"]
    assert corrected["corroborators"] == []
    assert corrected["evidence_head_digest"] is None
    assert corrected["verification_receipt_digest"] is None
    assert is_structurally_admissible(
        corrected,
        **{
            **admission,
            "validated_history": [*admission["validated_history"], corrected],
            "expected_current_event_digest": corrected["event_digest"],
            "expected_current_revision": corrected["revision"],
        },
    ) is False
    assert verify_knowledge_transition(revoked, corrected) == (True, None)

    with pytest.raises(KnowledgeStateError, match="must change"):
        build_knowledge_transition(
            previous=revoked,
            new_state=OBSERVATION_LOCAL,
            reason_digest=_digest("2"),
            corrected_content_digest=revoked["content_digest"],
        )


def test_illegal_backward_transition_and_evidence_loss_are_refused() -> None:
    _, corroborated, verified = _verified_chain()
    with pytest.raises(KnowledgeStateError, match="not allowed"):
        build_knowledge_transition(
            previous=verified,
            new_state=CORROBORATED,
            reason_digest=_digest("1"),
        )

    quarantined = build_knowledge_transition(
        previous=corroborated,
        new_state=QUARANTINED,
        reason_digest=_digest("2"),
    )
    assert quarantined["corroborators"] == []
    assert quarantined["evidence_head_digest"] is None
    assert quarantined["verification_receipt_digest"] is None

    polluted = deepcopy(quarantined)
    polluted["corroborators"] = _corroborators()
    assert verify_knowledge_state(polluted)[0] is False


def test_history_must_start_at_root_and_have_no_stale_or_missing_link() -> None:
    initial, corroborated, verified = _verified_chain()
    assert verify_knowledge_history([initial, corroborated, verified]) == (True, None)

    ok, reason = verify_knowledge_history([corroborated, verified])
    assert ok is False
    assert reason == "knowledge history root is not an initial local observation"

    ok, reason = verify_knowledge_history([initial, verified])
    assert ok is False
    assert reason == (
        "knowledge history link 1 invalid: revision is not exactly monotonic"
    )


def test_authoritative_head_rejects_revoked_prefix_and_parallel_fork() -> None:
    initial, corroborated, verified = _verified_chain()
    revoked = build_knowledge_transition(
        previous=verified,
        new_state=REVOKED,
        reason_digest=_digest("1"),
    )
    stale_admission = {
        "validated_history": [initial, corroborated, verified],
        "expected_policy_head_digest": _digest("5"),
        "expected_evidence_head_digest": _digest("e"),
        "expected_current_event_digest": revoked["event_digest"],
        "expected_current_revision": revoked["revision"],
        "validated_corroborators": _corroborators(),
        "validated_verification_receipt_digest": _digest("0"),
    }
    assert is_structurally_admissible(verified, **stale_admission) is False

    parallel = build_knowledge_transition(
        previous=corroborated,
        new_state=VERIFIED,
        reason_digest=_digest("f"),
        verification_receipt_digest=_digest("1"),
    )
    assert parallel["event_digest"] != verified["event_digest"]
    assert is_structurally_admissible(
        parallel,
        **{
            **stale_admission,
            "validated_history": [initial, corroborated, parallel],
            "expected_current_event_digest": verified["event_digest"],
            "expected_current_revision": verified["revision"],
            "validated_verification_receipt_digest": _digest("1"),
        },
    ) is False


def test_verification_cannot_rebind_heads_or_corroborators() -> None:
    _, corroborated, _ = _verified_chain()
    common = {
        "previous": corroborated,
        "new_state": VERIFIED,
        "reason_digest": _digest("f"),
        "verification_receipt_digest": _digest("0"),
    }
    with pytest.raises(KnowledgeStateError, match="policy head changed"):
        build_knowledge_transition(
            **common,
            policy_head_digest=_digest("1"),
        )
    with pytest.raises(KnowledgeStateError, match="evidence head changed"):
        build_knowledge_transition(
            **common,
            evidence_head_digest=_digest("2"),
        )

    changed_corroborators = _corroborators()
    changed_corroborators[1] = {
        "identity_digest": _digest("1"),
        "lineage_digest": _digest("2"),
        "receipt_digest": _digest("3"),
    }
    with pytest.raises(KnowledgeStateError, match="corroborators changed"):
        build_knowledge_transition(
            **common,
            corroborators=changed_corroborators,
        )


def test_state_specific_shapes_clear_and_require_fresh_evidence() -> None:
    initial, corroborated, verified = _verified_chain()
    with pytest.raises(KnowledgeStateError, match="must not carry"):
        build_knowledge_transition(
            previous=initial,
            new_state=QUARANTINED,
            reason_digest=_digest("1"),
            corroborators=_corroborators(),
        )
    with pytest.raises(KnowledgeStateError, match="must not carry a verification"):
        build_knowledge_transition(
            previous=initial,
            new_state=CORROBORATED,
            reason_digest=_digest("2"),
            corroborators=_corroborators(),
            evidence_head_digest=_digest("e"),
            verification_receipt_digest=_digest("0"),
        )

    quarantined = build_knowledge_transition(
        previous=corroborated,
        new_state=QUARANTINED,
        reason_digest=_digest("3"),
    )
    assert quarantined["corroborators"] == []
    assert quarantined["evidence_head_digest"] is None
    assert quarantined["verification_receipt_digest"] is None
    with pytest.raises(KnowledgeStateError, match="two independent"):
        build_knowledge_transition(
            previous=quarantined,
            new_state=CORROBORATED,
            reason_digest=_digest("4"),
        )

    recorrobated = build_knowledge_transition(
        previous=quarantined,
        new_state=CORROBORATED,
        reason_digest=_digest("5"),
        corroborators=_corroborators(),
        evidence_head_digest=_digest("6"),
    )
    assert recorrobated["evidence_head_digest"] == _digest("6")

    revoked = build_knowledge_transition(
        previous=verified,
        new_state=REVOKED,
        reason_digest=_digest("7"),
    )
    assert revoked["corroborators"] == []
    assert revoked["evidence_head_digest"] is None
    assert revoked["verification_receipt_digest"] is None


def test_hostile_container_bounds_fail_closed() -> None:
    oversized_event = {
        **_initial(),
        **{f"extra_{index}": index for index in range(32)},
    }
    assert verify_knowledge_state(oversized_event) == (
        False,
        "knowledge event keyset",
    )
    assert verify_knowledge_history(
        [_initial()] * (MAX_HISTORY_EVENTS + 1)
    ) == (False, "knowledge history must be non-empty and bounded")

    with pytest.raises(KnowledgeStateError, match="bounded maximum"):
        build_knowledge_transition(
            previous=_initial(),
            new_state=CORROBORATED,
            reason_digest=_digest("1"),
            corroborators=_corroborators()
            * ((MAX_CORROBORATORS // len(_corroborators())) + 1),
            evidence_head_digest=_digest("e"),
        )
