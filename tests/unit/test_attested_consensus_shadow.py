"""Tests for the default-off attested-consensus shadow adapter."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from waggledance.core.capabilities.activation_admission_intent import (
    build_activation_admission_intent,
)
from waggledance.core.orchestration import attested_consensus_shadow as S
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
    build_evidence_attestation,
    derive_signing_key_digest,
)
from waggledance.core.orchestration.evidence_consensus import (
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


def _request(ballot_type: str = "support") -> dict[str, object]:
    key = b"s" * 32
    provenance = {
        "reviewer_lineage_digest": _digest("lineage"),
        "model_digest": _digest("model"),
        "provider_digest": _digest("provider"),
        "tool_digest": _digest("tool"),
        "data_corpus_digest": _digest("corpus"),
        "host_digest": _digest("host"),
        "review_policy_digest": _digest("review-policy"),
    }
    binding = build_trusted_provenance_binding(
        signer_cell_id=_digest("reviewer-cell"),
        reviewer_activation_scope_digest=_digest("reviewer-scope"),
        signing_key_digest=derive_signing_key_digest(key),
        **provenance,
        status="active",
    )
    registry = build_provenance_registry_snapshot(
        generation=0,
        previous_registry_head_digest=INITIAL_PREVIOUS_REGISTRY_HEAD_DIGEST,
        bindings=[binding],
    ).to_mapping()
    policy = build_consensus_admission_policy(
        required_independent_support=1,
        maximum_evidence_records=4,
        maximum_ballots=4,
        maximum_attestations=4,
    )
    base = build_attestation_log_snapshot(
        generation=0,
        previous_log_head_digest=INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=[],
    ).to_mapping()
    scope = _digest("target-scope")
    intent = build_activation_admission_intent(
        activation_scope_digest=scope,
        query_digest=_digest("query"),
        expected_current_bundle_digest=_digest("bundle:current"),
        expected_current_activation_head_digest=_digest("head:current"),
        expected_current_store_revision=2,
        proposed_bundle_digest=_digest("bundle:proposed"),
        proposed_activation_head_digest=_digest("head:proposed"),
        proposed_store_revision=3,
        proposed_previous_bundle_digest=_digest("bundle:current"),
        proposed_previous_activation_head_digest=_digest("head:current"),
        trust_registry_head_digest=registry["registry_head_digest"],
        attestation_log_base_head_digest=base["log_head_digest"],
        consensus_policy_digest=policy.policy_digest,
        required_independent_support=1,
    )
    evidence = build_evidence_diversity(
        query_digest=intent["query_digest"],
        decision_digest=intent["decision_digest"],
        candidate_digest=intent["candidate_digest"],
        activation_head_digest=intent["activation_head_digest"],
        **provenance,
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
    entry = build_attestation_log_entry(
        activation_scope_digest=scope,
        admission_challenge_digest=intent["admission_challenge_digest"],
        evidence_digest=evidence["evidence_digest"],
        ballot_digest=ballot["ballot_digest"],
        attestation_digest=attestation["attestation_digest"],
        reviewer_lineage_digest=evidence["reviewer_lineage_digest"],
    )
    closed = build_next_attestation_log_snapshot(
        base,
        expected_current_log_head_digest=base["log_head_digest"],
        appended_entries=[entry],
    ).to_mapping()

    expected_lookup = (
        registry["registry_head_digest"],
        evidence["reviewer_lineage_digest"],
        derive_signing_key_digest(key),
    )
    return {
        "activation_admission_intent": intent,
        "policy": policy,
        "expected_consensus_policy_digest": policy.policy_digest,
        "expected_activation_scope_digest": scope,
        "expected_query_digest": intent["query_digest"],
        "expected_current_bundle_digest": _digest("bundle:current"),
        "expected_current_activation_head_digest": _digest("head:current"),
        "expected_current_store_revision": 2,
        "expected_proposed_bundle_digest": _digest("bundle:proposed"),
        "expected_proposed_activation_head_digest": _digest("head:proposed"),
        "expected_proposed_store_revision": 3,
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
        "evidence_records": [evidence],
        "ballots": [ballot],
        "attestations": [attestation],
        "key_lookup": lambda head, lineage, key_digest: (
            key if (head, lineage, key_digest) == expected_lookup else None
        ),
    }


def _split_request(
    request: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], object]:
    materials = {key: request[key] for key in S.GATE_MATERIAL_KEYS}
    expectations = {key: request[key] for key in S.GATE_EXPECTATION_KEYS}
    return materials, expectations, request["key_lookup"]


def _evaluate_request(ballot_type: str = "support") -> dict[str, object]:
    materials, expectations, key_lookup = _split_request(_request(ballot_type))
    return S.evaluate_attested_consensus_shadow_request(
        materials,
        expected_bindings=expectations,
        trusted_key_lookup=key_lookup,
    )


def test_shadow_request_evaluates_sources_not_provider_selected_advice() -> None:
    request = _request("support")
    materials, expectations, key_lookup = _split_request(request)
    receipt = S.evaluate_attested_consensus_shadow_request(
        materials,
        expected_bindings=expectations,
        trusted_key_lookup=key_lookup,
    )
    assert set(request) == S.GATE_REQUEST_KEYS
    assert set(materials) == S.GATE_MATERIAL_KEYS
    assert set(expectations) == S.GATE_EXPECTATION_KEYS
    assert S.GATE_RUNTIME_DEPENDENCY_KEYS == {"key_lookup"}
    assert not (set(materials) & set(expectations))
    assert receipt["consensus_gate_passed"] is True
    assert receipt["activation_admission_advised"] is True
    assert receipt["activation_performed"] is False
    assert receipt["routing_influence_applied"] is False
    assert receipt["authority_granted"] is False
    assert "consensus_gate_passed" not in request


def test_inhibitory_request_remains_a_negative_advisory_receipt() -> None:
    receipt = _evaluate_request("stop")
    assert receipt["consensus_gate_passed"] is False
    assert receipt["activation_admission_advised"] is False
    assert receipt["activation_performed"] is False


def test_request_boundary_is_exact_and_wraps_gate_refusals() -> None:
    materials, expectations, key_lookup = _split_request(_request())
    with pytest.raises(S.AttestedConsensusShadowError) as extra:
        S.evaluate_attested_consensus_shadow_request(
            {**materials, "provider_advice": True},
            expected_bindings=expectations,
            trusted_key_lookup=key_lookup,
        )
    assert extra.value.reason == "gate_materials_keyset"

    with pytest.raises(S.AttestedConsensusShadowError) as smuggled_key:
        S.evaluate_attested_consensus_shadow_request(
            {**materials, "key_lookup": key_lookup},
            expected_bindings=expectations,
            trusted_key_lookup=key_lookup,
        )
    assert smuggled_key.value.reason == "gate_materials_keyset"

    with pytest.raises(S.AttestedConsensusShadowError) as key_lookup_exc:
        S.evaluate_attested_consensus_shadow_request(
            materials,
            expected_bindings=expectations,
            trusted_key_lookup=None,
        )
    assert key_lookup_exc.value.reason == "key_lookup_not_callable"

    stale = dict(expectations)
    stale["expected_attestation_log_closed_head_digest"] = _digest("stale")
    with pytest.raises(S.AttestedConsensusShadowError) as gate:
        S.evaluate_attested_consensus_shadow_request(
            materials,
            expected_bindings=stale,
            trusted_key_lookup=key_lookup,
        )
    assert gate.value.reason == "gate:stale_attestation_log_closed_head"


def test_expectations_are_a_separate_exact_stable_snapshot() -> None:
    _, expectations, _ = _split_request(_request())
    parsed = S.parse_attested_consensus_shadow_expectations(expectations)
    expectations["expected_query_digest"] = _digest("mutated-after-copy")
    assert parsed["expected_query_digest"] != expectations[
        "expected_query_digest"
    ]

    with pytest.raises(S.AttestedConsensusShadowError) as smuggled:
        S.parse_attested_consensus_shadow_expectations(
            {**parsed, "self_certified": True}
        )
    assert smuggled.value.reason == "gate_expectations_keyset"

    malformed = dict(parsed)
    malformed["expected_current_store_revision"] = True
    with pytest.raises(S.AttestedConsensusShadowError) as revision:
        S.parse_attested_consensus_shadow_expectations(malformed)
    assert revision.value.reason == "expected_current_store_revision"


def test_report_is_deterministic_bounded_and_explicitly_structural_only() -> None:
    positive = _evaluate_request("support")
    negative = _evaluate_request("veto")
    report = S.summarize_attested_consensus_shadow([negative, positive, positive])
    reverse = S.summarize_attested_consensus_shadow(
        [positive, positive, negative]
    )
    assert report == reverse
    assert set(report) == S.SHADOW_REPORT_KEYS
    assert report["receipt_count"] == 3
    assert report["unique_receipt_count"] == 2
    assert report["advisory_pass_count"] == 2
    assert report["advisory_block_count"] == 1
    assert report["committed_entry_count_total"] == 3
    assert report["structural_summary_only"] is True
    assert report["source_reverification_performed"] is False
    assert report["observer_only"] is True
    assert report["activation_performed"] is False
    assert report["routing_influence_applied"] is False
    assert report["production_decision_unchanged"] is True
    assert report["authority_granted"] is False
    assert S.parse_attested_consensus_shadow_report(report) == report
    assert S.verify_attested_consensus_shadow_report(
        report,
        receipts=[positive, negative, positive],
    ) == (True, None)


def test_report_refuses_tamper_aliases_invalid_receipts_and_overflow() -> None:
    receipt = _evaluate_request()
    report = S.summarize_attested_consensus_shadow([receipt])

    tampered = dict(report)
    tampered["authority_granted"] = True
    assert S.verify_attested_consensus_shadow_report(
        tampered, receipts=[receipt]
    ) == (False, "authority_granted")

    invalid_receipt = deepcopy(receipt)
    invalid_receipt["activation_performed"] = True
    with pytest.raises(S.AttestedConsensusShadowError) as invalid:
        S.summarize_attested_consensus_shadow([invalid_receipt])
    assert invalid.value.reason == "receipt:activation_performed"

    class ListAlias(list):
        pass

    with pytest.raises(S.AttestedConsensusShadowError) as alias:
        S.summarize_attested_consensus_shadow(ListAlias([receipt]))
    assert alias.value.reason == "receipts_type"

    with pytest.raises(S.AttestedConsensusShadowError) as overflow:
        S.summarize_attested_consensus_shadow(
            [receipt] * (S.MAX_SHADOW_RECEIPTS + 1)
        )
    assert overflow.value.reason == "receipts_count_exceeded"


def test_report_parser_owns_its_digest_list() -> None:
    receipt = _evaluate_request()
    report = S.summarize_attested_consensus_shadow([receipt])
    parsed = S.parse_attested_consensus_shadow_report(report)
    report["gate_receipt_digests"].clear()
    assert len(parsed["gate_receipt_digests"]) == 1
