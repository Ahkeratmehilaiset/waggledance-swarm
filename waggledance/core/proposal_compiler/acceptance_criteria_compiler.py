"""Acceptance criteria compiler — Phase 9 §O."""
from __future__ import annotations


def compile_acceptance(proposal: dict) -> list[str]:
    """Return a list of human-readable acceptance criteria."""
    out: list[str] = [
        "all targeted tests pass",
        "no live runtime path is touched",
        "no main branch auto-merge occurs",
        "human review approval id is recorded",
    ]
    ptype = str(proposal.get("proposal_type") or "")
    if ptype == "solver_family_growth":
        out.extend([
            "solver compiles deterministically (byte-identical artifact)",
            "solver passes propose_solver gate",
            "solver remains shadow_only until canary review",
        ])
    elif ptype == "topology_subdivision":
        out.extend([
            "new children land in shadow_only state",
            "parent retains correct subdivision_state",
            "blast radius isolation verified",
        ])
    elif ptype == "policy_gate_adjustment":
        out.extend([
            "no hard rule is relaxed",
            "tighten / narrow_scope / add_advisory_check only",
            "rollback restores prior policy state",
        ])
    elif ptype == "infrastructure_followup":
        out.extend([
            "resilience envelope is documented",
            "best_effort vs guaranteed boundary is explicit",
        ])
    return sorted(set(out))


def compile_review_checklist(proposal: dict) -> str:
    items = [
        "[ ] human reviewer authenticated",
        "[ ] proposal artifact provenance verified",
        "[ ] no_runtime_mutation invariant held",
        "[ ] no_main_branch_auto_merge invariant held",
        "[ ] hook contracts re-hashed",
        "[ ] all tests in test_spec passed in isolated worktree",
        "[ ] rollback plan reviewed",
        "[ ] human_approval_id assigned to transition",
    ]
    return "\n".join(items)


def compile_pr_draft(proposal: dict, *,
                          affected_files: list[str],
                          rollout_plan: dict) -> str:
    sel = proposal.get("selected_proposal") or {}
    lines = [
        f"# Draft PR — {proposal.get('proposal_type')}",
        "",
        f"**source meta-proposal:** {proposal.get('meta_proposal_id')}",
        f"**target:** {proposal.get('canonical_target')}",
        f"**solver_name:** {sel.get('solver_name', '—')}",
        f"**no_runtime_mutation:** True",
        f"**no_main_branch_auto_merge:** True",
        "",
        "## Affected files",
        "",
    ]
    for f in affected_files:
        lines.append(f"- `{f}`")
    lines.extend([
        "",
        "## Rollout plan",
        "",
        f"- shadow_first: {rollout_plan.get('shadow_first')}",
        f"- stages: {' → '.join(rollout_plan.get('stages') or [])}",
        "",
        "## Notes",
        "",
        "Human review required before any runtime promotion.",
    ])
    return "\n".join(lines) + "\n"
