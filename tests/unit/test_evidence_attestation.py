"""Adversarial tests for the local signed-evidence observer boundary."""

from __future__ import annotations

import hashlib

import pytest

import waggledance.core.orchestration.evidence_attestation as subject
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration.evidence_attestation import (
    ATTESTATION_DIGEST_DOMAIN,
    MAX_ATTESTATIONS,
    OBSERVATION_DIGEST_DOMAIN,
    EvidenceAttestationError,
    build_evidence_attestation,
    canonical_signing_bytes,
    canonicalize_evidence_attestation,
    derive_signing_key_digest,
    evaluate_attested_inhibitory_consensus,
    verify_attested_inhibitory_consensus,
    verify_evidence_attestation,
)
from waggledance.core.orchestration.evidence_consensus import (
    MAX_EVIDENCE_RECORDS,
    build_evidence_diversity,
    build_inhibitory_ballot,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


CONTEXT = {
    "query_digest": _digest("query"),
    "decision_digest": _digest("decision"),
    "candidate_digest": _digest("candidate"),
    "activation_head_digest": _digest("shared-activation-head"),
}
TRUST_HEAD = _digest("trust-registry-head")
SCOPE = _digest("cell-and-deployment-scope")
CHALLENGE = _digest("ledger-challenge")
KEY_A = b"a" * 32
KEY_B = b"b" * 32


def _source(
    identity: str,
    ballot_type: str = "support",
    *,
    key: bytes = KEY_A,
    context: dict[str, str] | None = None,
    trust_head: str = TRUST_HEAD,
    scope: str = SCOPE,
    challenge: str = CHALLENGE,
) -> tuple[dict[str, str], dict[str, object], dict[str, object], bytes]:
    bindings = CONTEXT if context is None else context
    evidence = build_evidence_diversity(
        **bindings,
        reviewer_lineage_digest=_digest(f"lineage:{identity}"),
        model_digest=_digest(f"model:{identity}"),
        provider_digest=_digest(f"provider:{identity}"),
        tool_digest=_digest(f"tool:{identity}"),
        data_corpus_digest=_digest(f"corpus:{identity}"),
        host_digest=_digest(f"host:{identity}"),
        review_policy_digest=_digest(f"policy:{identity}"),
    ).to_mapping()
    ballot = build_inhibitory_ballot(
        ballot_type=ballot_type, evidence=evidence
    ).to_mapping()
    attestation = build_evidence_attestation(
        evidence=evidence,
        ballot=ballot,
        trust_registry_head_digest=trust_head,
        activation_scope_digest=scope,
        admission_challenge_digest=challenge,
        key=key,
    ).to_mapping()
    return evidence, ballot, attestation, key


def _lookup_for(*sources):
    keys = {
        (
            item[2]["trust_registry_head_digest"],
            item[2]["reviewer_lineage_digest"],
            item[2]["signing_key_digest"],
        ): item[3]
        for item in sources
    }
    return lambda head, lineage, key_digest: keys.get(
        (head, lineage, key_digest)
    )


def _batch(
    sources,
    *,
    context: dict[str, str] | None = None,
    trust_head: str = TRUST_HEAD,
    scope: str = SCOPE,
    challenge: str = CHALLENGE,
    required: int = 1,
    key_lookup=None,
):
    return evaluate_attested_inhibitory_consensus(
        **(CONTEXT if context is None else context),
        expected_trust_registry_head_digest=trust_head,
        expected_activation_scope_digest=scope,
        expected_admission_challenge_digest=challenge,
        evidence_records=[item[0] for item in sources],
        ballots=[item[1] for item in sources],
        attestations=[item[2] for item in sources],
        required_independent_support=required,
        key_lookup=_lookup_for(*sources) if key_lookup is None else key_lookup,
    )


def _verify_batch(value, sources, **overrides):
    arguments = {
        **CONTEXT,
        "expected_trust_registry_head_digest": TRUST_HEAD,
        "expected_activation_scope_digest": SCOPE,
        "expected_admission_challenge_digest": CHALLENGE,
        "evidence_records": [item[0] for item in sources],
        "ballots": [item[1] for item in sources],
        "attestations": [item[2] for item in sources],
        "required_independent_support": 1,
        "key_lookup": _lookup_for(*sources),
    }
    arguments.update(overrides)
    return verify_attested_inhibitory_consensus(value, **arguments)


def _redigest_attestation(attestation: dict[str, object]) -> None:
    unsigned = {
        key: value
        for key, value in attestation.items()
        if key != "attestation_digest"
    }
    signature = unsigned.pop("signature")
    attestation["attestation_digest"] = sha256_digest(
        {
            "domain": ATTESTATION_DIGEST_DOMAIN,
            **unsigned,
            "signature": signature,
        }
    )


def _redigest_observation(observation: dict[str, object]) -> None:
    unsigned = {
        key: value
        for key, value in observation.items()
        if key != "observation_digest"
    }
    observation["observation_digest"] = sha256_digest(
        {"domain": OBSERVATION_DIGEST_DOMAIN, **unsigned}
    )


def test_roundtrip_is_deterministic_and_strictly_observer_only() -> None:
    source = _source("one")
    evidence, ballot, attestation, _ = source
    rebuilt = _source("one")[2]
    assert rebuilt == attestation
    assert canonicalize_evidence_attestation(attestation) == attestation
    assert canonical_signing_bytes(
        **{
            name: attestation[name]
            for name in (
                "evidence_digest",
                "ballot_digest",
                "reviewer_lineage_digest",
                "trust_registry_head_digest",
                "activation_scope_digest",
                "admission_challenge_digest",
                "signing_key_digest",
                "advisory_only",
                "authority_granted",
            )
        }
    ) == canonical_signing_bytes(
        **{
            name: rebuilt[name]
            for name in (
                "evidence_digest",
                "ballot_digest",
                "reviewer_lineage_digest",
                "trust_registry_head_digest",
                "activation_scope_digest",
                "admission_challenge_digest",
                "signing_key_digest",
                "advisory_only",
                "authority_granted",
            )
        }
    )
    assert verify_evidence_attestation(
        attestation,
        evidence=evidence,
        ballot=ballot,
        expected_trust_registry_head_digest=TRUST_HEAD,
        expected_activation_scope_digest=SCOPE,
        expected_admission_challenge_digest=CHALLENGE,
        key_lookup=_lookup_for(source),
    ) == (True, None)

    observation = _batch([source])
    consensus = observation["claimed_provenance_consensus"]
    assert consensus["acceptance_advised"] is True
    for flag, expected in {
        "claimed_provenance_only": True,
        "source_set_completeness_verified": False,
        "positive_admission_ready": False,
        "activation_admission_advised": False,
        "activation_performed": False,
        "routing_influence_applied": False,
        "advisory_only": True,
        "authority_granted": False,
    }.items():
        assert observation[flag] is expected
    assert _verify_batch(observation, [source]) == (True, None)


def test_attestation_authenticates_ballot_as_well_as_evidence() -> None:
    evidence, support, support_attestation, _ = _source("reviewer")
    stop = build_inhibitory_ballot(
        ballot_type="stop", evidence=evidence
    ).to_mapping()
    ok, reason = verify_evidence_attestation(
        support_attestation,
        evidence=evidence,
        ballot=stop,
        expected_trust_registry_head_digest=TRUST_HEAD,
        expected_activation_scope_digest=SCOPE,
        expected_admission_challenge_digest=CHALLENGE,
        key_lookup=lambda *_: KEY_A,
    )
    assert ok is False
    assert reason == "ballot_binding_mismatch"
    assert support["ballot_digest"] != stop["ballot_digest"]


@pytest.mark.parametrize(
    ("expected_name", "expected_value", "reason"),
    [
        (
            "expected_trust_registry_head_digest",
            _digest("other-head"),
            "trust_registry_head_mismatch",
        ),
        (
            "expected_activation_scope_digest",
            _digest("other-cell"),
            "activation_scope_mismatch",
        ),
        (
            "expected_admission_challenge_digest",
            _digest("other-challenge"),
            "admission_challenge_mismatch",
        ),
    ],
)
def test_cross_head_cell_and_challenge_replay_is_refused(
    expected_name: str, expected_value: str, reason: str
) -> None:
    source = _source("replay")
    kwargs = {
        "evidence": source[0],
        "ballot": source[1],
        "expected_trust_registry_head_digest": TRUST_HEAD,
        "expected_activation_scope_digest": SCOPE,
        "expected_admission_challenge_digest": CHALLENGE,
        "key_lookup": _lookup_for(source),
    }
    kwargs[expected_name] = expected_value
    assert verify_evidence_attestation(source[2], **kwargs) == (False, reason)


@pytest.mark.parametrize("field", sorted(CONTEXT))
def test_batch_recomputes_every_independent_context_binding(field: str) -> None:
    source = _source("context")
    context = {**CONTEXT, field: _digest(f"foreign:{field}")}
    with pytest.raises(
        EvidenceAttestationError,
        match="^(evidence|ballot)_context_mismatch$",
    ):
        _batch([source], context=context)


def test_signed_field_tamper_cannot_survive_digest_recompute() -> None:
    source = _source("tamper")
    forged = dict(source[2])
    forged["signature"] = "hmac-sha256:" + "0" * 64
    _redigest_attestation(forged)
    assert canonicalize_evidence_attestation(forged) == forged
    ok, reason = verify_evidence_attestation(
        forged,
        evidence=source[0],
        ballot=source[1],
        expected_trust_registry_head_digest=TRUST_HEAD,
        expected_activation_scope_digest=SCOPE,
        expected_admission_challenge_digest=CHALLENGE,
        key_lookup=_lookup_for(source),
    )
    assert (ok, reason) == (False, "signature_invalid")


@pytest.mark.parametrize(
    ("lookup", "reason"),
    [
        (lambda *_: None, "key_unavailable"),
        (lambda *_: b"x" * 31, "key_invalid"),
        (lambda *_: KEY_B, "key_digest_mismatch"),
    ],
)
def test_missing_malformed_and_wrong_keys_fail_closed(lookup, reason: str) -> None:
    source = _source("key")
    assert verify_evidence_attestation(
        source[2],
        evidence=source[0],
        ballot=source[1],
        expected_trust_registry_head_digest=TRUST_HEAD,
        expected_activation_scope_digest=SCOPE,
        expected_admission_challenge_digest=CHALLENGE,
        key_lookup=lookup,
    ) == (False, reason)


def test_key_contract_is_exact_and_errors_never_echo_secret_payloads() -> None:
    source = _source("secret")
    with pytest.raises(EvidenceAttestationError, match="^key_invalid$"):
        derive_signing_key_digest(bytearray(KEY_A))
    with pytest.raises(EvidenceAttestationError, match="^key_invalid$"):
        build_evidence_attestation(
            evidence=source[0],
            ballot=source[1],
            trust_registry_head_digest=TRUST_HEAD,
            activation_scope_digest=SCOPE,
            admission_challenge_digest=CHALLENGE,
            key=b"too-short-and-secret",
        )

    secret = "DO_NOT_ECHO_SUPER_SECRET"

    def exploding_lookup(*_):
        raise RuntimeError(secret)

    ok, reason = verify_evidence_attestation(
        source[2],
        evidence=source[0],
        ballot=source[1],
        expected_trust_registry_head_digest=TRUST_HEAD,
        expected_activation_scope_digest=SCOPE,
        expected_admission_challenge_digest=CHALLENGE,
        key_lookup=exploding_lookup,
    )
    assert (ok, reason) == (False, "key_lookup_failed")
    assert secret not in reason
    assert KEY_A.hex() not in reason


class _DictSubclass(dict):
    pass


class _EqAnyStr(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


def test_wire_boundaries_reject_hostile_types_authority_smuggling_and_bounds() -> None:
    source = _source("wire")
    with pytest.raises(EvidenceAttestationError, match="attestation_not_dict"):
        canonicalize_evidence_attestation(_DictSubclass(source[2]))
    alias = dict(source[2])
    value = alias.pop("evidence_digest")
    alias[_EqAnyStr("evidence_digest")] = value
    with pytest.raises(EvidenceAttestationError, match="attestation_not_dict"):
        canonicalize_evidence_attestation(alias)
    smuggled = {**source[2], "runtime_authority": True}
    with pytest.raises(EvidenceAttestationError, match="attestation_keyset"):
        canonicalize_evidence_attestation(smuggled)
    with pytest.raises(EvidenceAttestationError, match="not_list"):
        evaluate_attested_inhibitory_consensus(
            **CONTEXT,
            expected_trust_registry_head_digest=TRUST_HEAD,
            expected_activation_scope_digest=SCOPE,
            expected_admission_challenge_digest=CHALLENGE,
            evidence_records=(source[0],),
            ballots=[source[1]],
            attestations=[source[2]],
            required_independent_support=1,
            key_lookup=_lookup_for(source),
        )
    with pytest.raises(EvidenceAttestationError, match="count_exceeded"):
        evaluate_attested_inhibitory_consensus(
            **CONTEXT,
            expected_trust_registry_head_digest=TRUST_HEAD,
            expected_activation_scope_digest=SCOPE,
            expected_admission_challenge_digest=CHALLENGE,
            evidence_records=[source[0]] * (MAX_EVIDENCE_RECORDS + 1),
            ballots=[source[1]],
            attestations=[source[2]],
            required_independent_support=1,
            key_lookup=_lookup_for(source),
        )
    assert MAX_ATTESTATIONS > 0


def test_builder_and_canonicalizer_detach_from_mutable_inputs() -> None:
    evidence, ballot, _, _ = _source("mutation")
    built = build_evidence_attestation(
        evidence=evidence,
        ballot=ballot,
        trust_registry_head_digest=TRUST_HEAD,
        activation_scope_digest=SCOPE,
        admission_challenge_digest=CHALLENGE,
        key=KEY_A,
    ).to_mapping()
    evidence["evidence_digest"] = _digest("mutated")
    ballot["ballot_digest"] = _digest("mutated")
    assert built["evidence_digest"] != evidence["evidence_digest"]
    assert built["ballot_digest"] != ballot["ballot_digest"]

    wire = dict(built)
    canonical = canonicalize_evidence_attestation(wire)
    wire["signature"] = "hmac-sha256:" + "f" * 64
    assert canonical["signature"] != wire["signature"]


def test_order_invariance_and_duplicate_collapse_after_authentication() -> None:
    first = _source("order-a", key=KEY_A)
    second = _source("order-b", key=KEY_B)
    forward = _batch([first, second], required=2)
    reverse = _batch([second, first], required=2)
    assert forward == reverse
    assert forward["evidence_digests"] == sorted(forward["evidence_digests"])
    assert forward["ballot_digests"] == sorted(forward["ballot_digests"])
    assert forward["attestation_digests"] == sorted(
        forward["attestation_digests"]
    )

    duplicate = evaluate_attested_inhibitory_consensus(
        **CONTEXT,
        expected_trust_registry_head_digest=TRUST_HEAD,
        expected_activation_scope_digest=SCOPE,
        expected_admission_challenge_digest=CHALLENGE,
        evidence_records=[first[0], dict(first[0])],
        ballots=[first[1], dict(first[1])],
        attestations=[first[2], dict(first[2])],
        required_independent_support=1,
        key_lookup=_lookup_for(first),
    )
    assert len(duplicate["evidence_digests"]) == 1
    assert len(duplicate["ballot_digests"]) == 1
    assert len(duplicate["attestation_digests"]) == 1


def test_distinct_attestations_and_cross_lineage_key_reuse_refuse() -> None:
    first = _source("cardinality", key=KEY_A)
    alternative = build_evidence_attestation(
        evidence=first[0],
        ballot=first[1],
        trust_registry_head_digest=TRUST_HEAD,
        activation_scope_digest=SCOPE,
        admission_challenge_digest=CHALLENGE,
        key=KEY_B,
    ).to_mapping()
    lookup = {
        first[2]["signing_key_digest"]: KEY_A,
        alternative["signing_key_digest"]: KEY_B,
    }
    with pytest.raises(
        EvidenceAttestationError, match="ballot_attestation_cardinality"
    ):
        evaluate_attested_inhibitory_consensus(
            **CONTEXT,
            expected_trust_registry_head_digest=TRUST_HEAD,
            expected_activation_scope_digest=SCOPE,
            expected_admission_challenge_digest=CHALLENGE,
            evidence_records=[first[0]],
            ballots=[first[1]],
            attestations=[first[2], alternative],
            required_independent_support=1,
            key_lookup=lambda _h, _l, key_digest: lookup.get(key_digest),
        )

    same_key_a = _source("same-key-a", key=KEY_A)
    same_key_b = _source("same-key-b", key=KEY_A)
    with pytest.raises(EvidenceAttestationError, match="cross_lineage_key_reuse"):
        _batch([same_key_a, same_key_b], required=2)


def test_orphans_and_missing_stop_veto_block_before_evaluation(
    monkeypatch,
) -> None:
    support = _source("support", "support", key=KEY_A)
    orphan_evidence = _source("orphan-evidence", key=KEY_B)
    with pytest.raises(EvidenceAttestationError, match="evidence_unreferenced"):
        evaluate_attested_inhibitory_consensus(
            **CONTEXT,
            expected_trust_registry_head_digest=TRUST_HEAD,
            expected_activation_scope_digest=SCOPE,
            expected_admission_challenge_digest=CHALLENGE,
            evidence_records=[support[0], orphan_evidence[0]],
            ballots=[support[1]],
            attestations=[support[2]],
            required_independent_support=1,
            key_lookup=_lookup_for(support),
        )

    outsider = _source("outsider", key=KEY_B)
    with pytest.raises(EvidenceAttestationError, match="attestation_orphan"):
        evaluate_attested_inhibitory_consensus(
            **CONTEXT,
            expected_trust_registry_head_digest=TRUST_HEAD,
            expected_activation_scope_digest=SCOPE,
            expected_admission_challenge_digest=CHALLENGE,
            evidence_records=[support[0]],
            ballots=[support[1]],
            attestations=[support[2], outsider[2]],
            required_independent_support=1,
            key_lookup=_lookup_for(support, outsider),
        )

    def evaluator_must_not_run(**_):
        raise AssertionError("partial source set reached evaluator")

    monkeypatch.setattr(
        subject, "evaluate_inhibitory_consensus", evaluator_must_not_run
    )
    for ballot_type in ("stop", "veto"):
        negative = _source(f"unsigned-{ballot_type}", ballot_type)
        with pytest.raises(EvidenceAttestationError, match="ballot_unattested"):
            evaluate_attested_inhibitory_consensus(
                **CONTEXT,
                expected_trust_registry_head_digest=TRUST_HEAD,
                expected_activation_scope_digest=SCOPE,
                expected_admission_challenge_digest=CHALLENGE,
                evidence_records=[negative[0]],
                ballots=[negative[1]],
                attestations=[],
                required_independent_support=1,
                key_lookup=_lookup_for(negative),
            )

        forged_attestation = dict(negative[2])
        forged_attestation["signature"] = "hmac-sha256:" + "0" * 64
        _redigest_attestation(forged_attestation)
        with pytest.raises(EvidenceAttestationError, match="signature_invalid"):
            evaluate_attested_inhibitory_consensus(
                **CONTEXT,
                expected_trust_registry_head_digest=TRUST_HEAD,
                expected_activation_scope_digest=SCOPE,
                expected_admission_challenge_digest=CHALLENGE,
                evidence_records=[negative[0]],
                ballots=[negative[1]],
                attestations=[forged_attestation],
                required_independent_support=1,
                key_lookup=_lookup_for(negative),
            )


def test_batch_verifier_reauthenticates_sources_and_recomputes_observation() -> None:
    source = _source("verify")
    observation = _batch([source])
    forged = dict(observation)
    forged["evidence_digests"] = [
        *forged["evidence_digests"],
        _digest("invented-evidence"),
    ]
    forged["evidence_digests"].sort()
    _redigest_observation(forged)
    assert _verify_batch(forged, [source]) == (
        False,
        "observation_recompute_mismatch",
    )

    forged_flags = dict(observation)
    forged_flags["activation_admission_advised"] = True
    _redigest_observation(forged_flags)
    assert _verify_batch(forged_flags, [source]) == (
        False,
        "observation_authority_flags",
    )
    assert _verify_batch(
        observation, [source], key_lookup=lambda *_: None
    ) == (False, "key_unavailable")
