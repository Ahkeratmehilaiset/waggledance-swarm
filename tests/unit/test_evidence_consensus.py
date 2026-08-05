"""Focused tests for pure diversity and inhibitory-consensus contracts."""

from __future__ import annotations

import hashlib

import pytest

from waggledance.core.orchestration.evidence_consensus import (
    BALLOT_TYPES,
    MAX_EVIDENCE_RECORDS,
    EvidenceConsensusError,
    build_evidence_diversity,
    build_inhibitory_ballot,
    evaluate_inhibitory_consensus,
    parse_consensus_evaluation_structure,
    verify_consensus_evaluation,
    verify_evidence_diversity,
    verify_inhibitory_ballot,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


_CONTEXT = {
    "query_digest": _digest("query"),
    "decision_digest": _digest("decision"),
    "candidate_digest": _digest("candidate"),
    "activation_head_digest": _digest("activation-head"),
}


def _evidence(
    identity: str,
    *,
    context: dict[str, str] | None = None,
    shared_model: str | None = None,
) -> dict[str, str]:
    context = _CONTEXT if context is None else context
    return build_evidence_diversity(
        **context,
        reviewer_lineage_digest=_digest(f"lineage:{identity}"),
        model_digest=_digest(shared_model or f"model:{identity}"),
        provider_digest=_digest(f"provider:{identity}"),
        tool_digest=_digest(f"tool:{identity}"),
        data_corpus_digest=_digest(f"corpus:{identity}"),
        host_digest=_digest(f"host:{identity}"),
        review_policy_digest=_digest(f"review-policy:{identity}"),
    ).to_mapping()


def _ballot(ballot_type: str, evidence: dict[str, str]) -> dict[str, object]:
    return build_inhibitory_ballot(
        ballot_type=ballot_type,
        evidence=evidence,
    ).to_mapping()


def _evaluate(
    evidence: object,
    ballots: object,
    *,
    required: int = 1,
    context: dict[str, str] | None = None,
) -> dict[str, object]:
    return evaluate_inhibitory_consensus(
        **(_CONTEXT if context is None else context),
        evidence_records=evidence,
        ballots=ballots,
        required_independent_support=required,
    )


def _verify(
    result: object,
    evidence: object,
    ballots: object,
    *,
    required: int = 1,
    context: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    return verify_consensus_evaluation(
        result,
        **(_CONTEXT if context is None else context),
        evidence_records=evidence,
        ballots=ballots,
        required_independent_support=required,
    )


class _EqAnyStr(str):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


def test_evidence_is_deterministic_exact_keyed_and_rejects_aliases() -> None:
    first = _evidence("one")
    second = _evidence("one")
    assert first == second
    assert verify_evidence_diversity(first) == (True, None)

    authority_smuggle = {**first, "authority_granted": True}
    assert verify_evidence_diversity(authority_smuggle) == (
        False,
        "evidence keyset",
    )

    alias = dict(first)
    query_digest = alias.pop("query_digest")
    alias[_EqAnyStr("query_digest")] = query_digest
    ok, reason = verify_evidence_diversity(alias)
    assert ok is False
    assert reason == "evidence must be an exact dict"


@pytest.mark.parametrize("ballot_type", sorted(BALLOT_TYPES))
def test_every_typed_ballot_is_bound_and_advisory(ballot_type: str) -> None:
    evidence = _evidence("typed")
    ballot = _ballot(ballot_type, evidence)
    assert verify_inhibitory_ballot(ballot) == (True, None)
    assert ballot["query_digest"] == evidence["query_digest"]
    assert ballot["candidate_digest"] == evidence["candidate_digest"]
    assert ballot["activation_head_digest"] == evidence["activation_head_digest"]
    assert ballot["evidence_digest"] == evidence["evidence_digest"]
    assert ballot["advisory_only"] is True

    forged = dict(ballot)
    forged["candidate_digest"] = _digest("foreign-candidate")
    ok, _ = verify_inhibitory_ballot(forged)
    assert ok is False


def test_aliases_and_same_provenance_count_once_not_as_quorum() -> None:
    evidence = _evidence("same-substrate")
    support = _ballot("support", evidence)
    evidence_records = [evidence, dict(evidence)]
    ballots = [support, dict(support), dict(support)]

    # There is deliberately no reviewer display-name in either contract, so
    # aliases cannot affect identity. Exact duplicate submissions remain audit
    # metadata but collapse to one evidence, ballot, and provenance component.
    result = _evaluate(
        evidence_records,
        ballots,
        required=2,
    )
    assert result["submitted_evidence_count"] == 2
    assert result["unique_evidence_count"] == 1
    assert result["submitted_ballot_count"] == 3
    assert result["unique_ballot_count"] == 1
    assert result["independent_support_count"] == 1
    assert "confidence" not in result
    assert result["quorum_reached"] is False
    assert result["acceptance_advised"] is False
    assert result["acceptance_blocked"] is True
    assert "insufficient_independent_support" in result["blocker_reasons"]
    assert _verify(result, evidence_records, ballots, required=2) == (True, None)


def test_any_shared_provenance_dimension_is_correlated() -> None:
    first = _evidence("reviewer-a", shared_model="one-model")
    second = _evidence("reviewer-b", shared_model="one-model")
    result = _evaluate(
        [first, second],
        [_ballot("support", first), _ballot("support", second)],
        required=2,
    )
    assert first["reviewer_lineage_digest"] != second["reviewer_lineage_digest"]
    assert first["host_digest"] != second["host_digest"]
    assert first["model_digest"] == second["model_digest"]
    assert result["unique_ballot_count"] == 2
    assert result["independent_provenance_count"] == 1
    assert result["independent_support_count"] == 1
    assert result["quorum_reached"] is False


def test_fully_orthogonal_support_reaches_quorum_order_invariant() -> None:
    first = _evidence("orthogonal-a")
    second = _evidence("orthogonal-b")
    evidence = [first, second]
    ballots = [_ballot("support", first), _ballot("support", second)]

    forward = _evaluate(evidence, ballots, required=2)
    reverse = _evaluate(list(reversed(evidence)), list(reversed(ballots)), required=2)
    assert forward == reverse
    assert forward["independent_support_count"] == 2
    assert forward["quorum_reached"] is True
    assert forward["acceptance_advised"] is True
    assert forward["acceptance_blocked"] is False
    assert forward["blocker_reasons"] == []
    assert forward["advisory_only"] is True
    assert forward["authority_granted"] is False
    assert _verify(forward, evidence, ballots, required=2) == (True, None)


def test_stale_activation_head_mismatch_fails_closed() -> None:
    stale_context = {
        **_CONTEXT,
        "activation_head_digest": _digest("stale-activation-head"),
    }
    stale_evidence = _evidence("stale", context=stale_context)
    stale_ballot = _ballot("support", stale_evidence)
    result = _evaluate([stale_evidence], [stale_ballot])

    assert result["invalid_item_count"] == 2
    assert "evidence:activation_head_digest_mismatch" in result["invalid_reasons"]
    assert "ballot:activation_head_digest_mismatch" in result["invalid_reasons"]
    assert result["independent_support_count"] == 0
    assert result["quorum_reached"] is False
    assert result["acceptance_advised"] is False
    assert "invalid_input" in result["blocker_reasons"]
    assert _verify(result, [stale_evidence], [stale_ballot]) == (True, None)


def test_veto_outranks_correlated_support_and_is_retained() -> None:
    support_evidence = _evidence("support", shared_model="correlated-model")
    veto_evidence = _evidence("veto", shared_model="correlated-model")
    result = _evaluate(
        [support_evidence, veto_evidence],
        [
            _ballot("support", support_evidence),
            _ballot("veto", veto_evidence),
        ],
    )

    # One shared model makes one correlated component; its strongest signal is
    # veto, so the support is not averaged into a positive result.
    assert result["independent_provenance_count"] == 1
    assert result["independent_support_count"] == 0
    assert result["independent_veto_count"] == 1
    assert result["veto_latched"] is True
    assert result["acceptance_advised"] is False
    assert result["veto_evidence_digests"] == [veto_evidence["evidence_digest"]]
    assert result["negative_evidence_digests"] == [
        veto_evidence["evidence_digest"]
    ]
    assert _verify(
        result,
        [support_evidence, veto_evidence],
        [_ballot("support", support_evidence), _ballot("veto", veto_evidence)],
    ) == (True, None)


def test_abstain_cannot_be_averaged_with_support_into_approval() -> None:
    evidence = _evidence("support-then-abstain")
    result = _evaluate(
        [evidence],
        [_ballot("support", evidence), _ballot("abstain", evidence)],
    )
    assert result["independent_provenance_count"] == 1
    assert result["independent_support_count"] == 0
    assert result["independent_abstain_count"] == 1
    assert result["quorum_reached"] is False
    assert result["acceptance_advised"] is False


def test_stop_latch_blocks_even_after_independent_support_quorum() -> None:
    first = _evidence("support-a")
    second = _evidence("support-b")
    stopper = _evidence("stopper")
    result = _evaluate(
        [first, second, stopper],
        [
            _ballot("support", first),
            _ballot("support", second),
            _ballot("stop", stopper),
        ],
        required=2,
    )
    assert result["quorum_reached"] is True
    assert result["stop_latched"] is True
    assert result["acceptance_advised"] is False
    assert result["acceptance_blocked"] is True
    assert result["stop_evidence_digests"] == [stopper["evidence_digest"]]
    assert result["negative_evidence_digests"] == [
        stopper["evidence_digest"]
    ]
    assert "stop_latched" in result["blocker_reasons"]


def test_silence_and_malformed_input_never_approve() -> None:
    silent = _evaluate([], [])
    assert silent["invalid_item_count"] == 0
    assert silent["quorum_reached"] is False
    assert silent["acceptance_advised"] is False
    assert "silence" in silent["blocker_reasons"]
    assert _verify(silent, [], []) == (True, None)

    evidence = _evidence("valid-support")
    support = _ballot("support", evidence)
    malformed = _evaluate([evidence], [support, {}])
    assert malformed["independent_support_count"] == 1
    assert malformed["invalid_item_count"] == 1
    assert malformed["quorum_reached"] is False
    assert malformed["acceptance_advised"] is False
    assert malformed["invalid_reasons"] == ["ballot:malformed"]
    assert "invalid_input" in malformed["blocker_reasons"]
    assert _verify(malformed, [evidence], [support, {}]) == (True, None)


def test_bounded_input_fails_closed_without_partial_quorum() -> None:
    evidence = _evidence("bounded")
    evidence_records = [evidence] * (MAX_EVIDENCE_RECORDS + 1)
    ballots = [_ballot("support", evidence)]
    result = _evaluate(
        evidence_records,
        ballots,
    )
    assert result["submitted_evidence_count"] == MAX_EVIDENCE_RECORDS + 1
    assert result["unique_evidence_count"] == 0
    assert result["independent_support_count"] == 0
    assert result["invalid_reasons"] == [
        "ballot:evidence_missing",
        "evidence:count_exceeded",
    ]
    assert result["acceptance_advised"] is False
    assert _verify(result, evidence_records, ballots) == (True, None)


def test_evaluation_tamper_and_invalid_threshold_rejected() -> None:
    evidence = _evidence("tamper")
    result = _evaluate([evidence], [_ballot("support", evidence)])
    forged = dict(result)
    forged["authority_granted"] = True
    ok, reason = _verify(forged, [evidence], [_ballot("support", evidence)])
    assert ok is False
    assert reason == "evaluation must remain advisory with no authority"

    with pytest.raises(EvidenceConsensusError, match="1..128"):
        _evaluate([evidence], [_ballot("support", evidence)], required=True)


def test_evaluation_cannot_self_certify_without_exact_source_sets() -> None:
    first = _evidence("source-bound-a")
    second = _evidence("source-bound-b")
    evidence = [first, second]
    ballots = [_ballot("support", first), _ballot("support", second)]
    result = _evaluate(evidence, ballots, required=2)
    assert result["acceptance_advised"] is True

    # The aggregate and its public content digest remain byte-for-byte valid,
    # but they are not proof that their alleged source ballots existed.
    assert _verify(result, [], [], required=2) == (
        False,
        "evaluation does not match recomputed source evidence",
    )


def test_structural_parser_returns_private_lists_and_typed_refusals() -> None:
    first = _evidence("private-a")
    second = _evidence("private-b")
    result = _evaluate(
        [first, second],
        [_ballot("support", first), _ballot("support", second)],
        required=2,
    )
    parsed = parse_consensus_evaluation_structure(result)
    result["support_group_digests"].clear()
    assert len(parsed["support_group_digests"]) == 2

    malformed = dict(parsed)
    malformed["submitted_evidence_count"] = float("nan")
    with pytest.raises(EvidenceConsensusError, match="exact int"):
        parse_consensus_evaluation_structure(malformed)


def test_retained_stop_may_be_promoted_into_a_correlated_veto_group() -> None:
    stopper = _evidence("structural-stop", shared_model="shared-inhibitor")
    vetoer = _evidence("structural-veto", shared_model="shared-inhibitor")
    result = _evaluate(
        [stopper, vetoer],
        [_ballot("stop", stopper), _ballot("veto", vetoer)],
    )
    assert result["independent_stop_count"] == 0
    assert result["independent_veto_count"] == 1
    assert result["stop_evidence_digests"] == [stopper["evidence_digest"]]
    assert parse_consensus_evaluation_structure(result) == result


def test_wire_lists_are_json_exact_and_groups_are_disjoint() -> None:
    evidence = _evidence("json-wire")
    ballot = _ballot("support", evidence)
    refused = _evaluate((evidence,), (ballot,))
    assert refused["invalid_reasons"] == [
        "ballot:not_sequence",
        "evidence:not_sequence",
    ]
    assert refused["acceptance_advised"] is False

    valid = _evaluate([evidence], [ballot])
    forged = dict(valid)
    group_digest = valid["support_group_digests"][0]
    forged["abstain_group_digests"] = [group_digest]
    forged["independent_abstain_count"] = 1
    forged["independent_provenance_count"] = 2
    ok, reason = _verify(forged, [evidence], [ballot])
    assert ok is False
    assert reason == "provenance groups must be mutually disjoint"
