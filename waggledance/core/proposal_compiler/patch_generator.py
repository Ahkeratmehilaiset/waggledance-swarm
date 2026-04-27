# SPDX-License-Identifier: BUSL-1.1
"""Patch generator — Phase 9 §O.

Produces a deterministic patch_skeleton STRING from a meta-proposal.
NEVER writes the patch to disk; never auto-applies. The skeleton is
a placeholder that a human or U2 builder lane fills in concrete
diffs.
"""
from __future__ import annotations


def generate_patch_skeleton(proposal: dict,
                                  affected_files: list[str]) -> str:
    ptype = str(proposal.get("proposal_type") or "")
    target = str(proposal.get("canonical_target") or "")
    lines = [
        f"# Patch skeleton — {ptype} → {target}",
        "# This is a SKELETON, not a real diff. A human reviewer or",
        "# Phase U2 builder lane fills in the concrete changes.",
        "",
        "# Affected files (TBD diffs):",
    ]
    for f in affected_files:
        lines.append(f"#   - {f}")
    lines.extend([
        "",
        "# Required invariants:",
        "#   - no live runtime mutation",
        "#   - no main branch auto-merge",
        "#   - tests in test_spec must pass in isolated worktree",
        "#   - rollback plan must be ready before any runtime stage",
        "",
    ])
    return "\n".join(lines)
