# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

import pytest

from waggledance.core.learning.understanding_contracts import (
    CapabilityGapCandidateV1,
    DanceSignalKind,
    DanceSignalV1,
    HexCellAddressV1,
    IndependenceProfileV1,
    KnowledgeClaimKind,
    KnowledgeDeltaV1,
    LocalProvisionalUpdateV1,
    ObservationEnvelopeV1,
    PredictionCommitmentV1,
    PredictionStatus,
    PrivacyClass,
    UnderstandingContractError,
    build_observation_commitment,
)
from waggledance.core.magma.canonical import sha256_digest


NOW = "2026-08-02T12:00:00Z"
LATER = "2026-08-02T12:15:00Z"
DIGEST = sha256_digest({"fixture": "value"})


def _cell() -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id="bee_ops",
        q=0,
        r=0,
        incarnation_id="incarnation-1",
        generation=1,
        fence=1,
    )


def _observation(
    *,
    value: float = 35.25,
    privacy: PrivacyClass = PrivacyClass.SYNTHETIC,
) -> ObservationEnvelopeV1:
    return ObservationEnvelopeV1(
        observation_id="obs-1",
        cell=_cell(),
        ingest_seq=1,
        source_seq=7,
        source="mqtt",
        entity_id="wd.synthetic.hive-1",
        metric="temperature",
        unit="Cel",
        value=value,
        observed_at_utc=NOW,
        quality=0.9,
        privacy_class=privacy,
        metadata_digest=DIGEST,
    )


def _profile(*, model: str = "model-family-a", evidence: str = "trace-a") -> IndependenceProfileV1:
    return IndependenceProfileV1(
        reviewer_cell_id="reviewer-a",
        identity_incarnation="reviewer-a-inc-1",
        verifier_code_family="verifier-family-a",
        model_provider_lineage=model,
        prompt_policy_lineage="policy-a",
        evidence_root_lineage=evidence,
        toolchain_image_lineage="image-a",
        physical_failure_domain="host-a",
    )


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "35.0"])
def test_observation_rejects_non_finite_or_non_numeric_values(bad) -> None:
    with pytest.raises(UnderstandingContractError):
        _observation(value=bad)


def test_prediction_header_never_contains_actual_value() -> None:
    observation = _observation(value=123.456)

    header = observation.header_mapping()

    assert "value" not in header
    assert b"123.456" not in __import__("json").dumps(header).encode("utf-8")


def test_public_commitment_is_nonce_bound_and_keyless() -> None:
    first = build_observation_commitment(_observation(), nonce="0" * 32)
    same = build_observation_commitment(_observation(), nonce="0" * 32)
    second = build_observation_commitment(_observation(), nonce="1" * 32)

    assert first == same
    assert first.commitment_digest != second.commitment_digest
    assert first.scheme == "sha256"
    assert first.nonce == "0" * 32
    assert first.key_id == first.key_epoch == ""


def test_public_default_nonce_prevents_low_entropy_commitment_correlation() -> None:
    first = build_observation_commitment(_observation(value=0.0))
    second = build_observation_commitment(_observation(value=0.0))

    assert first.nonce != second.nonce
    assert first.commitment_digest != second.commitment_digest


def test_private_commitment_requires_key_and_hides_raw_value() -> None:
    observation = _observation(value=7.0, privacy=PrivacyClass.PRIVATE)
    with pytest.raises(UnderstandingContractError, match="HMAC key"):
        build_observation_commitment(observation)

    commitment = build_observation_commitment(
        observation,
        hmac_key=b"k" * 32,
        key_id="understanding-key-1",
        key_epoch="2026-08-02",
        nonce="0" * 32,
    )

    assert commitment.scheme == "hmac-sha256"
    assert commitment.commitment_digest.startswith("hmac-sha256:")
    assert "7.0" not in repr(commitment.to_mapping())


def test_private_commitment_nonce_separates_equal_low_entropy_values() -> None:
    kwargs = {
        "hmac_key": b"k" * 32,
        "key_id": "understanding-key-1",
        "key_epoch": "2026-08-02",
    }
    first = build_observation_commitment(_observation(privacy=PrivacyClass.PRIVATE), nonce="0" * 32, **kwargs)
    second = build_observation_commitment(_observation(privacy=PrivacyClass.PRIVATE), nonce="1" * 32, **kwargs)

    assert first.commitment_digest != second.commitment_digest


def test_prediction_commitment_binds_pre_update_generation() -> None:
    observation_ref = build_observation_commitment(_observation()).commitment_digest
    prediction = PredictionCommitmentV1(
        observation_commitment_digest=observation_ref,
        ingest_seq=1,
        prior_state_generation=4,
        prior_state_digest=DIGEST,
        predictor_artifact_digest=sha256_digest({"predictor": "last-value"}),
        predictor_config_digest=sha256_digest({"config": "v1"}),
        status=PredictionStatus.PREDICTED,
        predicted_value=34.5,
        committed_at_utc=NOW,
    )
    changed_generation = PredictionCommitmentV1(
        **{**prediction.__dict__, "prior_state_generation": 5}
    )

    assert prediction.prediction_digest != changed_generation.prediction_digest
    assert "value" not in prediction.to_mapping()


def test_cold_start_refuses_hindsight_value() -> None:
    with pytest.raises(UnderstandingContractError, match="cold_start"):
        PredictionCommitmentV1(
            observation_commitment_digest=DIGEST,
            ingest_seq=1,
            prior_state_generation=0,
            prior_state_digest=DIGEST,
            predictor_artifact_digest=DIGEST,
            predictor_config_digest=DIGEST,
            status=PredictionStatus.COLD_START,
            predicted_value=35.0,
            committed_at_utc=NOW,
        )


def test_knowledge_delta_requires_five_unique_public_evidence_refs() -> None:
    refs = tuple(sha256_digest({"evidence": i}) for i in range(5))
    delta = KnowledgeDeltaV1(
        proposal_id="proposal-1",
        proposer_cell_id="bee_ops",
        claim_kind=KnowledgeClaimKind.MODEL_UPDATE,
        aggregate_digest=DIGEST,
        evidence_refs=refs,
        confidence=0.8,
        privacy_class=PrivacyClass.SYNTHETIC,
        created_at_utc=NOW,
        expires_at_utc=LATER,
    )

    assert delta.proposal_digest.startswith("sha256:")
    with pytest.raises(UnderstandingContractError, match="5..256"):
        KnowledgeDeltaV1(**{**delta.__dict__, "evidence_refs": refs[:4]})
    with pytest.raises(UnderstandingContractError, match="private knowledge"):
        KnowledgeDeltaV1(**{**delta.__dict__, "privacy_class": PrivacyClass.PRIVATE})


def test_local_update_and_capability_gap_cannot_smuggle_authority() -> None:
    update = LocalProvisionalUpdateV1(
        update_id="update-1",
        cell_id="bee_ops",
        prediction_digest=DIGEST,
        prior_state_digest=DIGEST,
        new_state_digest=sha256_digest({"state": 2}),
        applied_at_utc=NOW,
    )
    assert update.reversible is True
    assert update.update_digest.startswith("sha256:")
    with pytest.raises(UnderstandingContractError, match="runtime authority"):
        LocalProvisionalUpdateV1(**{**update.__dict__, "runtime_authority_applied": True})
    object.__setattr__(update, "runtime_authority_applied", True)
    assert update.to_mapping()["runtime_authority_applied"] is False

    gap = CapabilityGapCandidateV1(
        gap_id="gap-1",
        proposer_cell_id="bee_ops",
        evidence_refs=(DIGEST,),
        created_at_utc=NOW,
    )
    assert gap.solver_build_eligible is False
    with pytest.raises(UnderstandingContractError, match="cannot invoke"):
        CapabilityGapCandidateV1(**{**gap.__dict__, "builder_invoked": True})


def test_different_sha_does_not_create_method_independence() -> None:
    first = _profile(evidence="trace-a")
    clone = IndependenceProfileV1(
        **{
            **first.__dict__,
            "reviewer_cell_id": "reviewer-b",
            "identity_incarnation": "reviewer-b-inc-9",
            "evidence_root_lineage": "trace-b",
        }
    )

    assert first.profile_digest != clone.profile_digest
    assert first.method_group_digest == clone.method_group_digest
    assert first.evidence_group_digest != clone.evidence_group_digest


def test_dance_signal_binds_exact_proposal_and_ttl() -> None:
    signal = DanceSignalV1(
        proposal_digest=DIGEST,
        signal_kind=DanceSignalKind.SUPPORT,
        reviewer=_profile(),
        evidence_digest=sha256_digest({"rederived": True}),
        created_at_utc=NOW,
        expires_at_utc=LATER,
    )
    changed = DanceSignalV1(
        **{**signal.__dict__, "proposal_digest": sha256_digest({"proposal": 2})}
    )

    assert signal.signal_digest != changed.signal_digest
    with pytest.raises(UnderstandingContractError, match="expiry"):
        DanceSignalV1(**{**signal.__dict__, "expires_at_utc": NOW})
