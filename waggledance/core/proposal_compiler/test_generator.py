# SPDX-License-Identifier: BUSL-1.1
"""Test spec generator — Phase 9 §O.

Pure: from a meta-proposal, emit a test_spec describing what tests
SHOULD exist for the proposed change. The generator does NOT write
test files; it produces structured shape that a future Phase Z step
materializes after human approval.
"""
from __future__ import annotations


def generate_test_spec(proposal: dict) -> dict:
    ptype = str(proposal.get("proposal_type") or "")
    target = str(proposal.get("canonical_target") or "")
    test_categories: list[str] = []
    if ptype == "solver_family_growth":
        test_categories = [
            "schema_validation",
            "deterministic_compile",
            "byte_identical_artifact",
            "no_runtime_mutation",
        ]
    elif ptype == "topology_subdivision":
        test_categories = [
            "subdivision_plan_deterministic",
            "shadow_first_invariant",
            "blast_radius_isolation",
            "parent_child_relation_integrity",
        ]
    elif ptype == "policy_gate_adjustment":
        test_categories = [
            "rule_id_deterministic",
            "no_relax_hard_rule",
            "tighten_only",
            "advisory_does_not_block",
        ]
    elif ptype == "introspection_gap":
        test_categories = [
            "self_model_snapshot_round_trip",
            "calibration_correction_emit",
        ]
    elif ptype == "infrastructure_followup":
        test_categories = [
            "resilience_envelope_documented",
            "no_runtime_mutation_in_change",
        ]
    else:
        test_categories = ["no_runtime_mutation", "deterministic_output"]
    return {
        "target": target,
        "categories": sorted(test_categories),
        "min_tests_per_category": 1,
        "must_run_in_isolated_worktree": True,
    }
