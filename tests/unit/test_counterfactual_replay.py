# SPDX-License-Identifier: BUSL-1.1
"""Forge/behaviour tests for the pure counterfactual-replay core (T1 slice 1)."""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.counterfactual_replay import (
    A3_LABEL_INSUFFICIENT,
    A3_LABEL_MEASURED_LOCAL_PARTIAL,
    A3_LABEL_NONDETERMINISTIC_ORACLE,
    A3_LABEL_RUNTIME_MEASURED,
    COUNTERFACTUAL_DELTA_SCHEMA,
    COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
    DIVERGENCE_IMPROVEMENT,
    DIVERGENCE_NEUTRAL,
    DIVERGENCE_REGRESSION,
    CounterfactualReplayError,
    _classify_divergence_oracle,
    compute_counterfactual_delta,
    derive_a3_label,
    summarize_counterfactual_observability,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec


def _spec(name: str, offset: float) -> SolverSpec:
    return SolverSpec(
        schema_version=1,
        spec_id=f"spec_{name}",
        family_kind="scalar_unit_conversion",
        solver_name=name,
        cell_id="general",
        spec={"from_unit": "C", "to_unit": "K", "factor": 1.0, "offset": offset},
        source="t1_test",
        source_kind="hand_authored",
    )


def _oracle(inputs, artifact):
    return float(inputs["x"]) * float(artifact["factor"]) + float(artifact.get("offset", 0.0))


def _samples(n=24):
    return [{"x": float(i)} for i in range(n)]


def test_happy_path_divergence_and_runtime_measured_label():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle, oracle_kind="formula_recompute",
    )
    assert delta["candidate_hash"] != delta["incumbent_hash"]
    assert delta["divergence_count"] == 24  # every output differs by the offset
    assert delta["no_delta"] is False
    assert delta["deterministic"] is True
    assert delta["candidate_sample_set_digest"] == delta["incumbent_sample_set_digest"]
    assert derive_a3_label(delta) == A3_LABEL_RUNTIME_MEASURED


def _external_oracle(target_offset):
    """A fixed external reference truth (``x + target_offset``), independent of
    which arm's artifact is passed — so exactly one arm can match it."""

    def oracle(inputs, artifact):  # noqa: ARG001 - external truth ignores artifact
        return float(inputs["x"]) * 1.0 + float(target_offset)

    return oracle


def test_divergences_classified_improvement_when_candidate_matches_oracle():
    # candidate output == external truth, incumbent output != truth.
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 100.0),
        incumbent=_spec("inc", 0.0), oracle=_external_oracle(100.0),
    )
    assert delta["divergence_count"] == 24
    assert delta["improvement_count"] == 24
    assert delta["regression_count"] == 0
    assert delta["neutral_divergence_count"] == 0
    assert all(
        d["oracle_classification"] == DIVERGENCE_IMPROVEMENT
        and d["candidate_oracle_agree"] is True
        and d["incumbent_oracle_agree"] is False
        for d in delta["divergences"]
    )


def test_divergences_classified_regression_when_incumbent_matches_oracle():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 100.0),
        incumbent=_spec("inc", 0.0), oracle=_external_oracle(0.0),
    )
    assert delta["regression_count"] == 24
    assert delta["improvement_count"] == 0
    assert delta["neutral_divergence_count"] == 0
    assert all(
        d["oracle_classification"] == DIVERGENCE_REGRESSION
        for d in delta["divergences"]
    )


def test_divergences_classified_neutral_when_neither_matches_oracle():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 100.0),
        incumbent=_spec("inc", 0.0), oracle=_external_oracle(-999.0),
    )
    assert delta["neutral_divergence_count"] == 24
    assert delta["improvement_count"] == 0
    assert delta["regression_count"] == 0


def test_classification_counts_sum_to_divergence_count_when_mixed():
    # External truth equals the candidate on even x and the incumbent on odd x:
    # even-x divergences are improvements, odd-x are regressions.
    def mixed_oracle(inputs, artifact):  # noqa: ARG001
        x = float(inputs["x"])
        return x + 100.0 if int(x) % 2 == 0 else x

    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 100.0),
        incumbent=_spec("inc", 0.0), oracle=mixed_oracle,
    )
    assert delta["improvement_count"] == 12
    assert delta["regression_count"] == 12
    assert delta["neutral_divergence_count"] == 0
    assert (
        delta["improvement_count"]
        + delta["regression_count"]
        + delta["neutral_divergence_count"]
    ) == delta["divergence_count"]


def test_no_delta_has_zero_classification_counts():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("same", 100.0),
        incumbent=_spec("same", 100.0), oracle=_external_oracle(100.0),
    )
    assert delta["no_delta"] is True
    assert delta["divergence_count"] == 0
    assert delta["improvement_count"] == 0
    assert delta["regression_count"] == 0
    assert delta["neutral_divergence_count"] == 0


def test_classification_is_rederivable_under_canonical_digest():
    from waggledance.core.magma.canonical import sha256_digest

    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 100.0),
        incumbent=_spec("inc", 0.0), oracle=_external_oracle(100.0),
    )
    # the breakdown is inside the digested core — an auditor recomputes it.
    core = {k: v for k, v in delta.items() if k != "canonical_digest"}
    assert delta["canonical_digest"] == sha256_digest(core)
    recomputed = sum(
        1
        for d in delta["divergences"]
        if _classify_divergence_oracle(
            d["candidate_oracle_agree"], d["incumbent_oracle_agree"]
        )
        == DIVERGENCE_IMPROVEMENT
    )
    assert recomputed == delta["improvement_count"] == 24


def test_classify_divergence_oracle_treats_none_as_non_agreement():
    # Only an explicit True counts as agreement; None/missing never passes.
    assert _classify_divergence_oracle(True, False) == DIVERGENCE_IMPROVEMENT
    assert _classify_divergence_oracle(False, True) == DIVERGENCE_REGRESSION
    assert _classify_divergence_oracle(True, True) == DIVERGENCE_NEUTRAL
    assert _classify_divergence_oracle(False, False) == DIVERGENCE_NEUTRAL
    assert _classify_divergence_oracle(None, True) == DIVERGENCE_REGRESSION
    assert _classify_divergence_oracle(True, None) == DIVERGENCE_IMPROVEMENT
    assert _classify_divergence_oracle(None, None) == DIVERGENCE_NEUTRAL


def test_same_solver_is_explicit_no_delta():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("same_solver", 273.15),
        incumbent=_spec("same_solver", 273.15), oracle=_oracle,
    )
    assert delta["no_delta"] is True
    assert delta["divergence_count"] == 0


def test_below_floor_is_partial_not_runtime_measured():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(5), candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle,
    )
    assert delta["sample_count"] == 5
    assert derive_a3_label(delta) == A3_LABEL_MEASURED_LOCAL_PARTIAL


def test_label_rederive_rejects_mismatched_sample_set_digests():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle,
    )
    delta["incumbent_sample_set_digest"] = "0" * 64  # arms did not see same inputs
    assert derive_a3_label(delta) == A3_LABEL_INSUFFICIENT


def test_label_rederive_nondeterministic():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle,
    )
    delta["deterministic"] = False
    assert derive_a3_label(delta) == A3_LABEL_NONDETERMINISTIC_ORACLE


def test_label_rederive_non_mapping_and_bad_count():
    assert derive_a3_label(None) == A3_LABEL_INSUFFICIENT
    assert derive_a3_label({"sample_count": "24"}) == A3_LABEL_INSUFFICIENT


def test_ill_formed_samples_raise():
    for bad in ("notalist", 123, [{"x": 1}, "nope"]):
        with pytest.raises(CounterfactualReplayError):
            compute_counterfactual_delta(
                shadow_samples=bad, candidate=_spec("cand_a", 1.0),
                incumbent=_spec("inc_a", 0.0), oracle=_oracle,
            )


def test_non_solverspec_raises():
    with pytest.raises(CounterfactualReplayError):
        compute_counterfactual_delta(
            shadow_samples=_samples(), candidate={"not": "a spec"},
            incumbent=_spec("inc_a", 0.0), oracle=_oracle,
        )


def test_read_only_does_not_mutate_samples():
    samples = _samples()
    snapshot = [dict(s) for s in samples]
    compute_counterfactual_delta(
        shadow_samples=samples, candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle,
    )
    assert samples == snapshot  # caller's list untouched


def test_canonical_digest_present_and_recomputes():
    from waggledance.core.magma.canonical import sha256_digest
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(), candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle,
    )
    core = {k: v for k, v in delta.items() if k != "canonical_digest"}
    assert delta["canonical_digest"] == sha256_digest(core)


def test_observability_summary_from_delta_is_privacy_safe():
    samples = [{"x": float(i), "note": "operator-secret-token"} for i in range(24)]
    delta = compute_counterfactual_delta(
        shadow_samples=samples, candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0), oracle=_oracle,
    )

    status = summarize_counterfactual_observability(delta)

    assert status == {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": True,
        "compute_status": "computed",
        "status": "runtime_measured",
        "a3_label": A3_LABEL_RUNTIME_MEASURED,
        "sample_count": 24,
        "divergence_count": 24,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }
    rendered = repr(status)
    assert "operator-secret-token" not in rendered
    assert "per_arm" not in rendered
    assert "divergences" not in rendered
    assert delta["canonical_digest"] not in rendered


def test_observability_summary_preserves_raw_delta_authority_drift_flags():
    delta = compute_counterfactual_delta(
        shadow_samples=_samples(),
        candidate=_spec("cand", 273.15),
        incumbent=_spec("inc", 0.0),
        oracle=_oracle,
    )
    drifted = {
        **delta,
        "controls_present": True,
        "runtime_authority_granted": True,
        "external_writes_applied": True,
        "payload_fields_exported": True,
    }

    status = summarize_counterfactual_observability(drifted)

    assert status["status"] == "runtime_measured"
    assert status["controls_present"] is True
    assert status["runtime_authority_granted"] is True
    assert status["external_writes_applied"] is True
    assert status["payload_fields_exported"] is True
    assert "per_arm" not in repr(status)
    assert "divergences" not in repr(status)


def test_observability_summary_requires_explicit_sample_set_digests():
    malformed_delta = {
        "schema_version": COUNTERFACTUAL_DELTA_SCHEMA,
        "sample_count": 24,
        "divergence_count": 0,
        "deterministic": True,
        "no_delta": True,
        "canonical_digest": "sha256:redacted",
    }

    assert derive_a3_label(malformed_delta) == A3_LABEL_INSUFFICIENT

    status = summarize_counterfactual_observability(malformed_delta)

    assert status["status"] == "insufficient"
    assert status["a3_label"] == A3_LABEL_INSUFFICIENT
    assert status["same_sample_set"] is False


def test_observability_summary_from_promotion_summary_and_missing_source():
    computed = summarize_counterfactual_observability({
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": A3_LABEL_MEASURED_LOCAL_PARTIAL,
        "sample_count": 5,
        "same_sample_set": True,
        "deterministic": True,
        "divergence_count": 3,
        "no_delta": False,
        "delta_digest": "sha256:private-digest",
        "per_arm": {"candidate": "private"},
    })
    assert computed["status"] == "measured_local_partial"
    assert computed["delta_digest_present"] is True
    assert "private-digest" not in repr(computed)
    assert "per_arm" not in computed

    failed = summarize_counterfactual_observability({
        "status": "failed",
        "error_type": "CounterfactualReplayError",
    })
    assert failed["source_available"] is True
    assert failed["status"] == "failed"
    assert failed["a3_label"] == A3_LABEL_INSUFFICIENT

    missing = summarize_counterfactual_observability(None)
    assert missing["source_available"] is False
    assert missing["status"] == "unavailable"


def test_observability_summary_bounds_stored_a3_label():
    status = summarize_counterfactual_observability({
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": "gpt-4o-RAW",
        "sample_count": 24,
        "same_sample_set": True,
        "deterministic": True,
        "divergence_count": 0,
        "no_delta": True,
        "delta_digest": "sha256:private-digest",
    })

    assert status["status"] == "insufficient"
    assert status["a3_label"] == A3_LABEL_INSUFFICIENT
    assert "gpt-4o-RAW" not in repr(status)


def test_observability_summary_preserves_explicit_authority_drift_flags():
    status = summarize_counterfactual_observability({
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": A3_LABEL_MEASURED_LOCAL_PARTIAL,
        "sample_count": 20,
        "same_sample_set": True,
        "deterministic": True,
        "divergence_count": 3,
        "no_delta": False,
        "delta_digest": "sha256:private-digest",
        "controls_present": True,
        "runtime_authority_granted": True,
        "external_writes_applied": True,
        "payload_fields_exported": True,
    })

    assert status["status"] == "measured_local_partial"
    assert status["controls_present"] is True
    assert status["runtime_authority_granted"] is True
    assert status["external_writes_applied"] is True
    assert status["payload_fields_exported"] is True
    assert "private-digest" not in repr(status)
