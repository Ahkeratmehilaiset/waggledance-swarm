"""Adversarial tests for the composed advisory consensus gate."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from waggledance.core.capabilities.activation_admission_intent import (
    build_activation_admission_intent,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration import attested_consensus_gate as G
from waggledance.core.orchestration.attestation_log import (
    INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
    build_attestation_log_entry,
    build_attestation_log_snapshot,
    build_next_attestation_log_snapshot,
)
from waggledance.core.orchestration.consensus_admission_policy import (
    build_consensus_admission_policy,
)
from waggledance.core.orchestration.evidence_attestation import (
    OBSERVATION_DIGEST_DOMAIN,
    build_evidence_attestation,
    derive_signing_key_digest,
)
from waggledance.core.orchestration.evidence_consensus import (
    EVALUATION_DIGEST_DOMAIN,
    PROVENANCE_DIMENSIONS,
    build_evidence_diversity,
    build_inhibitory_ballot,
)
from waggledance.core.orchestration.provenance_registry import (
    INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
    build_provenance_registry_snapshot,
    build_trusted_provenance_binding,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _provenance(label: str) -> dict[str, str]:
    return {
        "reviewer_lineage_digest": _digest(f"lineage:{label}"),
        "model_digest": _digest(f"model:{label}"),
        "provider_digest": _digest(f"provider:{label}"),
        "tool_digest": _digest(f"tool:{label}"),
        "data_corpus_digest": _digest(f"corpus:{label}"),
        "host_digest": _digest(f"host:{label}"),
        "review_policy_digest": _digest(f"review-policy:{label}"),
    }


def _case(
    ballot_types: tuple[str, ...] = ("support", "support"),
    *,
    required: int = 2,
    provenance_mismatch_at: int | None = None,
    provenance_mismatch_dimension: str = "provider_digest",
    revoked_at: int | None = None,
    same_signer_cell: bool = False,
    same_reviewer_scope: bool = False,
) -> dict[str, object]:
    policy = build_consensus_admission_policy(
        required_independent_support=required,
        maximum_evidence_records=16,
        maximum_ballots=16,
        maximum_attestations=16,
    )
    scope = _digest("target-activation-scope")
    query = _digest("query")
    keys = [bytes([65 + index]) * 32 for index in range(len(ballot_types))]
    trusted_dimensions = [
        _provenance(f"reviewer-{index}") for index in range(len(ballot_types))
    ]
    bindings = []
    for index, (key, dimensions) in enumerate(zip(keys, trusted_dimensions)):
        bindings.append(
            build_trusted_provenance_binding(
                signer_cell_id=_digest(
                    "reviewer-cell:shared"
                    if same_signer_cell
                    else f"reviewer-cell:{index}"
                ),
                # Reviewer scope is intentionally distinct from target scope.
                reviewer_activation_scope_digest=_digest(
                    "reviewer-scope:shared"
                    if same_reviewer_scope
                    else f"reviewer-scope:{index}"
                ),
                signing_key_digest=derive_signing_key_digest(key),
                **dimensions,
                status="revoked" if revoked_at == index else "active",
            ).to_mapping()
        )
    registry = build_provenance_registry_snapshot(
        generation=0,
        previous_registry_head_digest=INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
        bindings=bindings,
    ).to_mapping()
    base = build_attestation_log_snapshot(
        generation=0,
        previous_log_head_digest=INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=[],
    ).to_mapping()
    intent = build_activation_admission_intent(
        activation_scope_digest=scope,
        query_digest=query,
        expected_current_bundle_digest=_digest("bundle:current"),
        expected_current_activation_head_digest=_digest("head:current"),
        expected_current_store_revision=4,
        proposed_bundle_digest=_digest("bundle:proposed"),
        proposed_activation_head_digest=_digest("head:proposed"),
        proposed_store_revision=5,
        proposed_previous_bundle_digest=_digest("bundle:current"),
        proposed_previous_activation_head_digest=_digest("head:current"),
        trust_registry_head_digest=registry["registry_head_digest"],
        attestation_log_base_head_digest=base["log_head_digest"],
        consensus_policy_digest=policy.policy_digest,
        required_independent_support=required,
    )
    context = {
        "query_digest": intent["query_digest"],
        "decision_digest": intent["decision_digest"],
        "candidate_digest": intent["candidate_digest"],
        "activation_head_digest": intent["activation_head_digest"],
    }
    sources = []
    entries = []
    for index, (ballot_type, key, trusted) in enumerate(
        zip(ballot_types, keys, trusted_dimensions)
    ):
        claimed = dict(trusted)
        if provenance_mismatch_at == index:
            claimed[provenance_mismatch_dimension] = _digest(
                f"forged:{provenance_mismatch_dimension}:{index}"
            )
        evidence = build_evidence_diversity(
            **context,
            **claimed,
        ).to_mapping()
        ballot = build_inhibitory_ballot(
            ballot_type=ballot_type,
            evidence=evidence,
        ).to_mapping()
        attestation = build_evidence_attestation(
            evidence=evidence,
            ballot=ballot,
            trust_registry_head_digest=registry["registry_head_digest"],
            activation_scope_digest=scope,
            admission_challenge_digest=intent["admission_challenge_digest"],
            key=key,
        ).to_mapping()
        sources.append((evidence, ballot, attestation, key))
        entries.append(
            build_attestation_log_entry(
                activation_scope_digest=scope,
                admission_challenge_digest=intent[
                    "admission_challenge_digest"
                ],
                evidence_digest=evidence["evidence_digest"],
                ballot_digest=ballot["ballot_digest"],
                attestation_digest=attestation["attestation_digest"],
                reviewer_lineage_digest=evidence[
                    "reviewer_lineage_digest"
                ],
            )
        )
    appended_entries = entries
    if not appended_entries:
        appended_entries = [
            build_attestation_log_entry(
                activation_scope_digest=_digest("unrelated-scope"),
                admission_challenge_digest=_digest("unrelated-challenge"),
                evidence_digest=_digest("unrelated-evidence"),
                ballot_digest=_digest("unrelated-ballot"),
                attestation_digest=_digest("unrelated-attestation"),
                reviewer_lineage_digest=_digest("unrelated-lineage"),
            )
        ]
    closed = build_next_attestation_log_snapshot(
        base,
        expected_current_log_head_digest=base["log_head_digest"],
        appended_entries=appended_entries,
    ).to_mapping()
    key_map = {
        (
            source[2]["trust_registry_head_digest"],
            source[2]["reviewer_lineage_digest"],
            source[2]["signing_key_digest"],
        ): source[3]
        for source in sources
    }
    arguments = {
        "activation_admission_intent": intent,
        "policy": policy,
        "expected_consensus_policy_digest": policy.policy_digest,
        "expected_activation_scope_digest": scope,
        "expected_query_digest": query,
        "expected_current_bundle_digest": _digest("bundle:current"),
        "expected_current_activation_head_digest": _digest("head:current"),
        "expected_current_store_revision": 4,
        "expected_proposed_bundle_digest": _digest("bundle:proposed"),
        "expected_proposed_activation_head_digest": _digest("head:proposed"),
        "expected_proposed_store_revision": 5,
        "provenance_registry_snapshot": registry,
        "expected_trust_registry_head_digest": registry[
            "registry_head_digest"
        ],
        "attestation_log_base_snapshot": base,
        "expected_attestation_log_base_head_digest": base["log_head_digest"],
        "attestation_log_closed_snapshot": closed,
        "expected_attestation_log_closed_head_digest": closed[
            "log_head_digest"
        ],
        "evidence_records": [source[0] for source in sources],
        "ballots": [source[1] for source in sources],
        "attestations": [source[2] for source in sources],
        "key_lookup": lambda head, lineage, key_digest: key_map.get(
            (head, lineage, key_digest)
        ),
    }
    return {
        "arguments": arguments,
        "intent": intent,
        "policy": policy,
        "registry": registry,
        "base": base,
        "closed": closed,
        "sources": sources,
        "entries": entries,
    }


def _evaluate(case: dict[str, object]) -> dict[str, object]:
    return G.evaluate_attested_consensus_gate(**case["arguments"])


def _redigest_nested_consensus(receipt: dict[str, object]) -> None:
    observation = receipt["attested_consensus_observation"]
    consensus = observation["claimed_provenance_consensus"]
    unsigned = dict(consensus)
    unsigned.pop("evaluation_digest")
    consensus["evaluation_digest"] = sha256_digest(
        {"domain": EVALUATION_DIGEST_DOMAIN, **unsigned}
    )
    observation["claimed_provenance_consensus_digest"] = consensus[
        "evaluation_digest"
    ]


def _redigest_observation(receipt: dict[str, object]) -> None:
    observation = receipt["attested_consensus_observation"]
    unsigned = dict(observation)
    unsigned.pop("observation_digest")
    observation["observation_digest"] = sha256_digest(
        {"domain": OBSERVATION_DIGEST_DOMAIN, **unsigned}
    )


def _redigest_receipt(receipt: dict[str, object]) -> None:
    unsigned = dict(receipt)
    unsigned.pop("gate_receipt_digest")
    receipt["gate_receipt_digest"] = sha256_digest(
        {
            "domain": G.ATTESTED_CONSENSUS_GATE_RECEIPT_DIGEST_DOMAIN,
            **unsigned,
        }
    )


def test_positive_receipt_is_deterministic_complete_and_never_authority() -> None:
    case = _case()
    first = _evaluate(case)
    second = _evaluate(case)

    assert first == second
    assert set(first) == G.ATTESTED_CONSENSUS_GATE_RECEIPT_KEYS
    assert first["attestation_log_closed_head_digest"] == case["closed"][
        "log_head_digest"
    ]
    assert first["committed_entry_count"] == 2
    assert len(first["trusted_provenance_binding_digests"]) == 2
    for field in (
        "intent_bindings_verified",
        "policy_verified",
        "direct_log_transition_verified",
        "base_target_set_verified_empty",
        "source_set_completeness_verified",
        "trusted_provenance_verified",
        "all_committed_attestations_authenticated",
        "consensus_gate_passed",
        "positive_admission_ready",
        "activation_admission_advised",
    ):
        assert first[field] is True
    for field in (
        "activation_performed",
        "routing_influence_applied",
        "authority_granted",
    ):
        assert first[field] is False
    assert first["advisory_only"] is True

    # The nested observer remains honest about what it proves by itself.  The
    # stronger completeness/provenance proof exists only in the outer gate.
    observation = first["attested_consensus_observation"]
    assert observation["claimed_provenance_only"] is True
    assert observation["source_set_completeness_verified"] is False
    assert observation["activation_admission_advised"] is False
    assert G.parse_attested_consensus_gate_receipt(first) == first
    assert G.verify_attested_consensus_gate_receipt(
        first, **case["arguments"]
    ) == (True, None)


@pytest.mark.parametrize("inhibitor", ["stop", "veto"])
def test_inhibitory_signal_returns_audited_negative_receipt(
    inhibitor: str,
) -> None:
    receipt = _evaluate(_case(("support", inhibitor), required=1))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    assert consensus[f"{inhibitor}_latched"] is True
    assert receipt["consensus_gate_passed"] is False
    assert receipt["positive_admission_ready"] is False
    assert receipt["activation_admission_advised"] is False
    assert receipt["authority_granted"] is False


def test_insufficient_support_returns_negative_not_exception() -> None:
    receipt = _evaluate(_case(("support", "abstain"), required=2))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    assert consensus["blocker_reasons"] == ["insufficient_independent_support"]
    assert receipt["consensus_gate_passed"] is False


def test_empty_target_collection_is_explicit_audited_silence() -> None:
    receipt = _evaluate(_case((), required=1))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    assert receipt["committed_entry_count"] == 0
    assert receipt["trusted_provenance_binding_digests"] == []
    assert consensus["blocker_reasons"] == [
        "insufficient_independent_support",
        "silence",
    ]
    assert receipt["consensus_gate_passed"] is False


@pytest.mark.parametrize(
    ("argument", "reason"),
    [
        (
            "expected_attestation_log_closed_head_digest",
            "stale_attestation_log_closed_head",
        ),
        ("expected_trust_registry_head_digest", "intent_binding:stale_trust_registry_head_digest"),
        ("expected_query_digest", "intent_binding:stale_query_digest"),
        ("expected_consensus_policy_digest", "stale_consensus_policy"),
    ],
)
def test_independently_pinned_fact_drift_fails_closed(
    argument: str, reason: str
) -> None:
    case = _case()
    case["arguments"][argument] = _digest(f"stale:{argument}")
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(case)
    assert exc.value.reason == reason


def test_hidden_intermediate_log_generation_is_not_a_direct_window() -> None:
    case = _case()
    base = case["base"]
    unrelated = build_attestation_log_entry(
        activation_scope_digest=_digest("other-scope"),
        admission_challenge_digest=_digest("other-challenge"),
        evidence_digest=_digest("other-evidence"),
        ballot_digest=_digest("other-ballot"),
        attestation_digest=_digest("other-attestation"),
        reviewer_lineage_digest=_digest("other-lineage"),
    )
    middle = build_next_attestation_log_snapshot(
        base,
        expected_current_log_head_digest=base["log_head_digest"],
        appended_entries=[unrelated],
    )
    skipped = build_next_attestation_log_snapshot(
        middle,
        expected_current_log_head_digest=middle.log_head_digest,
        appended_entries=case["entries"],
    ).to_mapping()
    case["arguments"]["attestation_log_closed_snapshot"] = skipped
    case["arguments"]["expected_attestation_log_closed_head_digest"] = skipped[
        "log_head_digest"
    ]
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(case)
    assert exc.value.reason == "attestation_log_transition:generation_step"


@pytest.mark.parametrize("collection", ["evidence_records", "ballots", "attestations"])
def test_duplicate_payloads_are_not_silently_collapsed(collection: str) -> None:
    case = _case()
    case["arguments"][collection].append(case["arguments"][collection][0])
    expected = {
        "evidence_records": "duplicate_evidence_digest",
        "ballots": "duplicate_ballot_digest",
        "attestations": "duplicate_attestation_digest",
    }[collection]
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(case)
    assert exc.value.reason == expected


@pytest.mark.parametrize("collection", ["evidence_records", "ballots", "attestations"])
def test_missing_payload_breaks_the_committed_bijection(collection: str) -> None:
    case = _case()
    case["arguments"][collection].pop()
    with pytest.raises(G.AttestedConsensusGateError):
        _evaluate(case)


@pytest.mark.parametrize("dimension", PROVENANCE_DIMENSIONS)
def test_every_claimed_provenance_dimension_matches_registry(
    dimension: str,
) -> None:
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(
            _case(
                provenance_mismatch_at=0,
                provenance_mismatch_dimension=dimension,
            )
        )
    expected = (
        "trusted_lineage_mismatch"
        if dimension == "reviewer_lineage_digest"
        else "trusted_provenance_mismatch"
    )
    assert exc.value.reason == expected


def test_one_signer_cell_cannot_inflate_quorum_via_multiple_identities() -> None:
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(_case(same_signer_cell=True))
    assert exc.value.reason == "signer_cell_identity_conflict"


def test_one_reviewer_scope_cannot_inflate_quorum_via_multiple_cells() -> None:
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(_case(same_reviewer_scope=True))
    assert exc.value.reason == "reviewer_scope_identity_conflict"


def test_revoked_registry_key_is_never_accepted() -> None:
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(_case(revoked_at=0))
    assert exc.value.reason == "trusted_provenance:signing_key_revoked"


def test_invalid_hmac_is_refused_after_ledger_and_provenance_checks() -> None:
    case = _case()
    case["arguments"]["key_lookup"] = lambda *_: b"z" * 32
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        _evaluate(case)
    assert exc.value.reason == "authenticated_consensus:key_digest_mismatch"


def test_key_lookup_is_not_called_before_committed_set_matches() -> None:
    case = _case()
    calls = []
    case["arguments"]["attestations"].pop()
    case["arguments"]["key_lookup"] = lambda *items: calls.append(items)
    with pytest.raises(G.AttestedConsensusGateError):
        _evaluate(case)
    assert calls == []


def test_receipt_tampering_and_self_consistent_replay_are_refused() -> None:
    case = _case()
    receipt = _evaluate(case)
    tampered = deepcopy(receipt)
    tampered["activation_admission_advised"] = False
    assert G.verify_attested_consensus_gate_receipt(
        tampered, **case["arguments"]
    )[0] is False

    replay = deepcopy(receipt)
    replay["attestation_log_closed_head_digest"] = _digest("forged-closed")
    unsigned = dict(replay)
    unsigned.pop("gate_receipt_digest")
    replay["gate_receipt_digest"] = sha256_digest(
        {
            "domain": G.ATTESTED_CONSENSUS_GATE_RECEIPT_DIGEST_DOMAIN,
            **unsigned,
        }
    )
    assert G.verify_attested_consensus_gate_receipt(
        replay, **case["arguments"]
    ) == (False, "gate_receipt_recompute_mismatch")


def test_structural_parser_rejects_self_digested_inhibitory_contradiction() -> None:
    receipt = deepcopy(_evaluate(_case()))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    consensus["stop_latched"] = True
    _redigest_nested_consensus(receipt)
    _redigest_observation(receipt)
    _redigest_receipt(receipt)

    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert "stop latch mismatch" in exc.value.reason


@pytest.mark.parametrize("signal", ["stop", "veto"])
def test_structural_parser_requires_evidence_for_inhibitory_groups(
    signal: str,
) -> None:
    receipt = deepcopy(_evaluate(_case(required=1)))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    moved_group = consensus["support_group_digests"].pop()
    consensus["independent_support_count"] = 1
    consensus[f"{signal}_group_digests"] = [moved_group]
    consensus[f"independent_{signal}_count"] = 1
    _redigest_nested_consensus(receipt)
    _redigest_observation(receipt)
    _redigest_receipt(receipt)

    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert f"{signal} group lacks retained {signal} evidence" in exc.value.reason


def test_inhibitory_evidence_must_exist_in_observation_source_set() -> None:
    receipt = deepcopy(_evaluate(_case(("support", "stop"), required=1)))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    absent = _digest("absent-stop-evidence")
    consensus["stop_evidence_digests"] = [absent]
    consensus["negative_evidence_digests"] = [absent]
    _redigest_nested_consensus(receipt)
    _redigest_observation(receipt)
    _redigest_receipt(receipt)

    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert exc.value.reason == (
        "consensus_stop_evidence_digests_outside_observation"
    )


def test_gate_consensus_cannot_claim_collapsed_or_invalid_sources() -> None:
    receipt = deepcopy(_evaluate(_case()))
    consensus = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]
    consensus["submitted_evidence_count"] = 3
    _redigest_nested_consensus(receipt)
    _redigest_observation(receipt)
    _redigest_receipt(receipt)
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert exc.value.reason == "gate_consensus_source_metadata_mismatch"


def test_structural_parser_privately_copies_nested_consensus_lists() -> None:
    receipt = _evaluate(_case())
    parsed = G.parse_attested_consensus_gate_receipt(receipt)
    original_groups = receipt["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]["support_group_digests"]
    parsed_groups = parsed["attested_consensus_observation"][
        "claimed_provenance_consensus"
    ]["support_group_digests"]
    assert parsed_groups == original_groups
    original_groups.clear()
    assert len(parsed_groups) == 2


@pytest.mark.parametrize(
    ("field", "replacement", "reason"),
    [
        ("query_digest", _digest("other-query"), "observation_query_digest_mismatch"),
        (
            "trust_registry_head_digest",
            _digest("other-trust"),
            "observation_trust_registry_head_digest_mismatch",
        ),
        (
            "activation_scope_digest",
            _digest("other-scope"),
            "observation_activation_scope_digest_mismatch",
        ),
        (
            "admission_challenge_digest",
            _digest("other-challenge"),
            "observation_admission_challenge_digest_mismatch",
        ),
        (
            "required_independent_support",
            1,
            "observation_required_independent_support_mismatch",
        ),
    ],
)
def test_structural_parser_cross_binds_observation_to_receipt(
    field: str, replacement: object, reason: str
) -> None:
    receipt = deepcopy(_evaluate(_case()))
    receipt["attested_consensus_observation"][field] = replacement
    _redigest_observation(receipt)
    _redigest_receipt(receipt)
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert exc.value.reason == reason


def test_structural_parser_cross_binds_pointers_and_committed_counts() -> None:
    pointer_drift = deepcopy(_evaluate(_case()))
    pointer_drift["proposed_pointer"]["previous_bundle_digest"] = _digest(
        "wrong-predecessor"
    )
    _redigest_receipt(pointer_drift)
    with pytest.raises(G.AttestedConsensusGateError) as pointer_exc:
        G.parse_attested_consensus_gate_receipt(pointer_drift)
    assert pointer_exc.value.reason == "previous_bundle_binding"

    count_drift = deepcopy(_evaluate(_case()))
    count_drift["committed_entry_count"] = 1
    _redigest_receipt(count_drift)
    with pytest.raises(G.AttestedConsensusGateError) as count_exc:
        G.parse_attested_consensus_gate_receipt(count_drift)
    assert count_exc.value.reason == "committed_source_count_mismatch"

    binding_drift = deepcopy(_evaluate(_case()))
    binding_drift["trusted_provenance_binding_digests"] = []
    _redigest_receipt(binding_drift)
    with pytest.raises(G.AttestedConsensusGateError) as binding_exc:
        G.parse_attested_consensus_gate_receipt(binding_drift)
    assert binding_exc.value.reason == "trusted_binding_count_mismatch"

    too_few_bindings = deepcopy(_evaluate(_case()))
    too_few_bindings["trusted_provenance_binding_digests"] = (
        too_few_bindings["trusted_provenance_binding_digests"][:1]
    )
    _redigest_receipt(too_few_bindings)
    with pytest.raises(G.AttestedConsensusGateError) as few_bindings_exc:
        G.parse_attested_consensus_gate_receipt(too_few_bindings)
    assert few_bindings_exc.value.reason == "trusted_binding_count_mismatch"

    same_log_head = deepcopy(_evaluate(_case()))
    same_log_head["attestation_log_closed_head_digest"] = same_log_head[
        "attestation_log_base_head_digest"
    ]
    _redigest_receipt(same_log_head)
    with pytest.raises(G.AttestedConsensusGateError) as head_exc:
        G.parse_attested_consensus_gate_receipt(same_log_head)
    assert head_exc.value.reason == "attestation_log_head_not_advanced"

    decision_drift = deepcopy(_evaluate(_case()))
    decision_drift["decision_digest"] = _digest("arbitrary-decision")
    _redigest_receipt(decision_drift)
    with pytest.raises(G.AttestedConsensusGateError) as decision_exc:
        G.parse_attested_consensus_gate_receipt(decision_drift)
    assert decision_exc.value.reason == "decision_digest_intent_mismatch"


def test_structural_parser_rederives_the_admission_challenge() -> None:
    receipt = deepcopy(_evaluate(_case()))
    replacement = _digest("alternate-trust-head")
    receipt["trust_registry_head_digest"] = replacement
    receipt["attested_consensus_observation"][
        "trust_registry_head_digest"
    ] = replacement
    _redigest_observation(receipt)
    _redigest_receipt(receipt)
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert exc.value.reason == "admission_challenge_digest_intent_mismatch"


def test_nonempty_committed_set_cannot_claim_zero_evidence() -> None:
    receipt = deepcopy(_evaluate(_case()))
    observation = receipt["attested_consensus_observation"]
    consensus = observation["claimed_provenance_consensus"]
    observation["evidence_digests"] = []
    consensus["unique_evidence_count"] = 0
    consensus["independent_provenance_count"] = 0
    consensus["independent_support_count"] = 0
    consensus["support_group_digests"] = []
    consensus["quorum_reached"] = False
    consensus["acceptance_blocked"] = True
    consensus["acceptance_advised"] = False
    consensus["blocker_reasons"] = ["insufficient_independent_support"]
    receipt["consensus_gate_passed"] = False
    receipt["positive_admission_ready"] = False
    receipt["activation_admission_advised"] = False
    _redigest_nested_consensus(receipt)
    _redigest_observation(receipt)
    _redigest_receipt(receipt)
    with pytest.raises(G.AttestedConsensusGateError) as exc:
        G.parse_attested_consensus_gate_receipt(receipt)
    assert exc.value.reason == "committed_source_count_mismatch"


def test_nan_in_nested_consensus_returns_typed_verifier_refusal() -> None:
    case = _case()
    receipt = deepcopy(_evaluate(case))
    receipt["attested_consensus_observation"]["claimed_provenance_consensus"][
        "submitted_evidence_count"
    ] = float("nan")
    ok, reason = G.verify_attested_consensus_gate_receipt(
        receipt, **case["arguments"]
    )
    assert ok is False
    assert reason is not None
