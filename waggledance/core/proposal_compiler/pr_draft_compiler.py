"""PR draft + bundle compiler — Phase 9 §O.

Top-level entry: compile_meta_proposal_bundle pulls all the pieces
into one ProposalBundle.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import PROPOSAL_COMPILER_SCHEMA_VERSION
from .acceptance_criteria_compiler import (
    compile_acceptance,
    compile_pr_draft,
    compile_review_checklist,
)
from .affected_files_analyzer import analyze
from .patch_generator import generate_patch_skeleton
from .rollout_planner import plan_rollback, plan_rollout
from .test_generator import generate_test_spec


@dataclass(frozen=True)
class ProposalBundle:
    schema_version: int
    bundle_id: str
    source_meta_proposal_id: str
    patch_skeleton: str
    affected_files: tuple[str, ...]
    test_spec: dict
    rollout_plan: dict
    rollback_plan: dict
    acceptance_criteria: tuple[str, ...]
    review_checklist: str
    pr_draft_md: str
    no_main_branch_auto_merge: bool
    no_runtime_mutation: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "bundle_id": self.bundle_id,
            "source_meta_proposal_id": self.source_meta_proposal_id,
            "patch_skeleton": self.patch_skeleton,
            "affected_files": list(self.affected_files),
            "test_spec": dict(self.test_spec),
            "rollout_plan": dict(self.rollout_plan),
            "rollback_plan": dict(self.rollback_plan),
            "acceptance_criteria": list(self.acceptance_criteria),
            "review_checklist": self.review_checklist,
            "pr_draft_md": self.pr_draft_md,
            "no_main_branch_auto_merge": self.no_main_branch_auto_merge,
            "no_runtime_mutation": self.no_runtime_mutation,
        }


def compile_bundle(proposal: dict) -> ProposalBundle:
    """Pure: deterministic bundle from a meta-proposal dict."""
    affected = analyze(proposal)
    test_spec = generate_test_spec(proposal)
    rollout = plan_rollout(proposal)
    rollback = plan_rollback(proposal)
    acceptance = compile_acceptance(proposal)
    review_checklist = compile_review_checklist(proposal)
    pr_draft = compile_pr_draft(
        proposal, affected_files=affected, rollout_plan=rollout,
    )
    patch = generate_patch_skeleton(proposal, affected)
    src_id = str(proposal.get("meta_proposal_id") or "unknown")
    canonical = json.dumps({
        "src": src_id, "affected": affected,
        "ptype": proposal.get("proposal_type"),
    }, sort_keys=True, separators=(",", ":"))
    bundle_id = "bundle_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return ProposalBundle(
        schema_version=PROPOSAL_COMPILER_SCHEMA_VERSION,
        bundle_id=bundle_id,
        source_meta_proposal_id=src_id,
        patch_skeleton=patch,
        affected_files=tuple(affected),
        test_spec=test_spec,
        rollout_plan=rollout,
        rollback_plan=rollback,
        acceptance_criteria=tuple(acceptance),
        review_checklist=review_checklist,
        pr_draft_md=pr_draft,
        no_main_branch_auto_merge=True,
        no_runtime_mutation=True,
    )
