# SPDX-License-Identifier: BUSL-1.1
"""Forge/behaviour tests for the pure counterfactual-replay core (T1 slice 1)."""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.counterfactual_replay import (
    A3_LABEL_INSUFFICIENT,
    A3_LABEL_MEASURED_LOCAL_PARTIAL,
    A3_LABEL_NONDETERMINISTIC_ORACLE,
    A3_LABEL_RUNTIME_MEASURED,
    CounterfactualReplayError,
    compute_counterfactual_delta,
    derive_a3_label,
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
