# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the Phase 13 runtime-integrated harvest proof.

Exercises the real runtime seam end-to-end. The test asserts the
before/after invariants the operator prompt requires:

* Pass 1 (before harvest): every structured runtime query in the
  corpus misses and the router emits one bounded signal per query
  class, automatically.
* Harvest cycle: digest -> queue -> scheduler drains everything, no
  errors, no provider calls.
* Pass 2 (after harvest): every query is now served via
  capability-aware dispatch (``source == 'auto_promoted_solver'``).
* Inner-loop zero-provider invariant: ``provider_jobs`` and
  ``builder_jobs`` stay empty.
"""

from __future__ import annotations

from pathlib import Path

from tools.run_runtime_harvest_proof import run as run_runtime_harvest_proof


def test_runtime_harvest_proof_before_after_passes(tmp_path: Path) -> None:
    proof = run_runtime_harvest_proof(tmp_path / "out", tmp_path / "p.db")

    corpus_size = proof["corpus_size"]
    assert corpus_size >= 48, (
        f"corpus has only {corpus_size} runtime queries; minimum is 48"
    )

    # Pass 1: nothing promoted yet
    assert proof["before"]["served_total"] == 0
    assert proof["before"]["fallback_or_miss_total"] == corpus_size
    assert proof["before"]["signals_emitted_total"] == corpus_size

    # Harvest cycle: every signal -> intent -> queue -> promoted
    assert proof["harvest"]["intents_created"] == corpus_size
    assert proof["harvest"]["intents_enqueued"] == corpus_size
    assert proof["harvest"]["scheduler_drained"] == corpus_size
    assert proof["harvest"]["scheduler_promoted"] == corpus_size
    assert proof["harvest"]["scheduler_rejected"] == 0
    assert proof["harvest"]["scheduler_errored"] == 0

    # Pass 2: every query now served via capability-aware lookup
    assert proof["after"]["served_total"] == corpus_size
    assert proof["after"]["served_via_capability_lookup_total"] == corpus_size
    assert proof["after"]["fallback_or_miss_total"] == 0

    # Per-family coverage: every allowlisted family appears in the
    # after-served map.
    per_family = proof["after"]["per_family_served"]
    assert set(per_family.keys()) == {
        "bounded_interpolation",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "lookup_table",
        "scalar_unit_conversion",
        "threshold_rule",
    }

    # Per-cell coverage: at least four distinct hex cells appear
    assert len(proof["after"]["per_cell_served"]) >= 4


def test_runtime_harvest_proof_zero_provider_calls(tmp_path: Path) -> None:
    """The runtime-harvest common path must use zero provider calls
    and zero builder subprocess invocations."""

    proof = run_runtime_harvest_proof(tmp_path / "out2", tmp_path / "p2.db")
    assert proof["kpis"]["provider_jobs_total"] == 0
    assert proof["kpis"]["builder_jobs_total"] == 0
    # Capability features were recorded for every promoted solver
    # (multiple feature rows per solver across families)
    assert proof["kpis"]["solver_capability_features_total"] > 0


def test_runtime_harvest_proof_records_primary_teacher_lane(
    tmp_path: Path,
) -> None:
    proof = run_runtime_harvest_proof(tmp_path / "out3", tmp_path / "p3.db")
    assert proof["primary_teacher_lane_id"] == "claude_code_builder_lane"
    assert proof["schema_version"] >= 4
