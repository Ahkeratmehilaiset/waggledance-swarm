# SPDX-License-Identifier: BUSL-1.1
"""Affected files analyzer — Phase 9 §O.

Pure analysis: from a meta-proposal's selected_proposal payload,
derive which files would plausibly be touched if a human approved.
"""
from __future__ import annotations


_PROPOSAL_TYPE_TO_FILES: dict[str, tuple[str, ...]] = {
    "topology_subdivision": (
        "configs/topology/cells.yaml",
        "waggledance/core/hex_topology/parent_child_relations.py",
    ),
    "solver_family_growth": (
        "configs/axioms/<cell_id>/<solver_name>.yaml",
        "tests/test_<solver_name>.py",
    ),
    "solver_family_consolidation": (
        "configs/axioms/<cell_id>/",
        "docs/architecture/SOLVER_LIBRARY.md",
    ),
    "policy_gate_adjustment": (
        "tools/propose_solver.py",
        "waggledance/core/autonomy/policy_core.py",
    ),
    "introspection_gap": (
        "docs/runs/self_model/<sha12>/",
        "tools/build_self_model_snapshot.py",
    ),
    "infrastructure_followup": (
        "docs/architecture/VECTOR_WRITER_RESILIENCE.md",
        "waggledance/core/magma/vector_events.py",
    ),
    "archival_cleanup": (
        "configs/axioms/",
        "docs/runs/<archived_run>/",
    ),
}


def analyze(proposal: dict) -> list[str]:
    """Return a sorted, deduped list of plausibly-affected file paths."""
    ptype = str(proposal.get("proposal_type") or "")
    cell_id = str(proposal.get("canonical_target") or
                    (proposal.get("selected_proposal") or {}).get("cell_id")
                    or "general")
    solver_name = str(
        (proposal.get("selected_proposal") or {}).get("solver_name") or "x"
    )
    template = _PROPOSAL_TYPE_TO_FILES.get(ptype, ())
    out: list[str] = []
    for t in template:
        path = (t.replace("<cell_id>", cell_id)
                  .replace("<solver_name>", solver_name))
        out.append(path)
    return sorted(set(out))
