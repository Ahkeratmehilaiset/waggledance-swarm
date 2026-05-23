# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for MAGMA EvaluationResult v1 (Phase F PR2).

v1 is a strictly-additive superset of v0. These tests verify:
  * version-keyed dispatcher selects the right schema by ``evaluation_version``;
  * v0 builder unchanged (no regression);
  * v1 builder produces a record satisfying both the v1 schema and the
    v0-required-fields subset;
  * each v1 optional field validates with its semantic constraints
    (confidence_basis sample_count, sanitization_audit redaction_count
    when pii_redaction is applied, competitor_axis_reference enum,
    subject_payload_size_bytes range);
  * anti-claim invariants from v0 carry over to v1 (external_effect
    still demands operator_required=True);
  * receipt-binding equivalence: a v1 record's target_digest matches
    the canonical sha256 of the subject_payload exactly like v0.

No GitHub/network call; no release artifact mutated; no consensus_grade
or A3/A4 label change.
"""
from __future__ import annotations

import pytest

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import (
    build_evaluation_result,
    build_evaluation_result_v1,
)


def _v1_kwargs(**overrides):
    base = dict(
        case_id="case:magma:eval-v1:001",
        subject_type="counterfactual",
        target_payload={"hello": "world"},
        risk_class="internal_memory",
        expected_gate="review",
        actual_gate="review",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:fixture:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:fixture:v1",
        verdict="review",
        reason_codes=["fixture:reason"],
        confidence_score=0.8,
    )
    base.update(overrides)
    return base


# --- v0 unchanged ----------------------------------------------------------

def test_v0_builder_still_emits_v0_record():
    result = build_evaluation_result(
        case_id="case:magma:eval-v0-still:001",
        subject_type="counterfactual",
        target_payload={"hello": "world"},
        risk_class="internal_memory",
        expected_gate="review",
        actual_gate="review",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:fixture:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:fixture:v1",
        verdict="review",
        reason_codes=["fixture:reason"],
        confidence_score=0.8,
    )
    assert result["evaluation_version"] == "magma.evaluation_result.v0"
    assert "confidence_basis" not in result
    assert "sanitization_audit" not in result


# --- v1 baseline + receipt-binding equivalence -----------------------------

def test_v1_builder_emits_v1_record_without_optional_fields():
    result = build_evaluation_result_v1(**_v1_kwargs())
    assert result["evaluation_version"] == "magma.evaluation_result.v1"
    # v1 with no optional extras still satisfies all v0-required fields:
    for field in (
        "case_id",
        "subject_type",
        "target_digest",
        "risk_class",
        "expected_gate",
        "actual_gate",
        "verifier_path",
        "solver_selection",
        "policy_version",
        "charter_version",
        "domain_threshold_version",
        "verdict",
        "reason_codes",
        "operator_required",
        "confidence_score",
        "uncertainty_sources",
    ):
        assert field in result, field


def test_v1_target_digest_matches_canonical_sha256_of_payload():
    """Receipt-binding invariant: v1 binds its payload via the same
    canonical sha256 as v0, so the receipt chain works identically."""
    payload = {"some": "structure", "with": ["nested", "values"]}
    result = build_evaluation_result_v1(**_v1_kwargs(target_payload=payload))
    assert result["target_digest"] == sha256_digest(payload)


# --- v1 confidence_basis ---------------------------------------------------

def test_v1_confidence_basis_bootstrap_validates():
    result = build_evaluation_result_v1(
        **_v1_kwargs(
            confidence_basis={
                "method": "bootstrap",
                "sample_count": 30,
                "model": "claude-opus-4-7",
                "methodology_reference": "docs/architecture/EVALUATION_RESULT_V1_DRAFT.md#confidence_basis",
            }
        )
    )
    assert result["confidence_basis"]["method"] == "bootstrap"
    assert result["confidence_basis"]["sample_count"] == 30


def test_v1_confidence_basis_rejects_invalid_method():
    with pytest.raises(ValueError, match="invalid"):
        build_evaluation_result_v1(
            **_v1_kwargs(
                confidence_basis={
                    "method": "psychic_guess",
                    "sample_count": 1,
                }
            )
        )


def test_v1_confidence_basis_rejects_zero_sample_count():
    with pytest.raises(ValueError, match="invalid"):
        build_evaluation_result_v1(
            **_v1_kwargs(
                confidence_basis={
                    "method": "point_estimate",
                    "sample_count": 0,
                }
            )
        )


# --- v1 sanitization_audit -------------------------------------------------

def test_v1_sanitization_audit_with_redaction_count_validates():
    result = build_evaluation_result_v1(
        **_v1_kwargs(
            sanitization_audit={
                "applied": ["pii_redaction", "locale_normalization"],
                "redaction_count": 3,
                "redaction_kinds": ["email", "phone"],
                "false_positive_count": 0,
            }
        )
    )
    assert result["sanitization_audit"]["redaction_count"] == 3


def test_v1_sanitization_audit_pii_applied_requires_nonzero_redaction_count():
    """Anti-claim invariant: if sanitization claims pii_redaction was
    applied, it MUST report at least 1 redaction. A record that says
    'applied PII redaction but redacted nothing' is inconsistent and
    must be refused."""
    with pytest.raises(ValueError, match="invalid"):
        build_evaluation_result_v1(
            **_v1_kwargs(
                sanitization_audit={
                    "applied": ["pii_redaction"],
                    "redaction_count": 0,
                }
            )
        )


def test_v1_sanitization_audit_without_pii_allows_zero_redactions():
    """If sanitization is run but did not apply pii_redaction (e.g., only
    locale_normalization), redaction_count=0 is consistent and allowed."""
    result = build_evaluation_result_v1(
        **_v1_kwargs(
            sanitization_audit={
                "applied": ["locale_normalization"],
                "redaction_count": 0,
            }
        )
    )
    assert result["sanitization_audit"]["redaction_count"] == 0


# --- v1 competitor_axis_reference ------------------------------------------

def test_v1_competitor_axis_reference_accepts_must_win_axes():
    for axis in ("A3", "A4"):
        result = build_evaluation_result_v1(
            **_v1_kwargs(competitor_axis_reference=axis)
        )
        assert result["competitor_axis_reference"] == axis


def test_v1_competitor_axis_reference_accepts_ceded_axes_for_evidence():
    for axis in ("A6", "A7", "A8"):
        result = build_evaluation_result_v1(
            **_v1_kwargs(competitor_axis_reference=axis)
        )
        assert result["competitor_axis_reference"] == axis


def test_v1_competitor_axis_reference_rejects_unknown_axis():
    with pytest.raises(ValueError, match="invalid"):
        build_evaluation_result_v1(
            **_v1_kwargs(competitor_axis_reference="A99")
        )


# --- v1 subject_payload_size_bytes ----------------------------------------

def test_v1_subject_payload_size_bytes_accepts_nonnegative():
    result = build_evaluation_result_v1(
        **_v1_kwargs(subject_payload_size_bytes=42)
    )
    assert result["subject_payload_size_bytes"] == 42


def test_v1_subject_payload_size_bytes_rejects_negative():
    with pytest.raises(ValueError, match="invalid"):
        build_evaluation_result_v1(
            **_v1_kwargs(subject_payload_size_bytes=-1)
        )


# --- v1 anti-claim invariants carry from v0 -------------------------------

def test_v1_external_effect_refused_by_default():
    with pytest.raises(ValueError, match="refuses external_effect"):
        build_evaluation_result_v1(**_v1_kwargs(risk_class="external_effect"))


def test_v1_external_effect_with_allow_flag_sets_operator_required():
    result = build_evaluation_result_v1(
        **_v1_kwargs(risk_class="external_effect", allow_external_effect=True)
    )
    assert result["operator_required"] is True


# --- dispatcher rejects unknown versions ----------------------------------

def test_dispatcher_rejects_unknown_evaluation_version():
    """If the dispatcher sees an unknown version, validation refuses to
    run -- caller cannot smuggle a future version past the gate."""
    from waggledance.core.magma.evaluation_result import _validate_evaluation_result

    fake = {
        "evaluation_version": "magma.evaluation_result.v99",
        "case_id": "case:magma:fake:001",
    }
    with pytest.raises(ValueError, match="unknown evaluation_version"):
        _validate_evaluation_result(fake)
