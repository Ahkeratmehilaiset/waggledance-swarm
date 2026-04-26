"""Proposal compiler — Phase 9 §O.

Turns Session D meta-proposals into implementation-shaped artifacts:
patch skeleton, affected_files, test_spec, rollout_plan,
rollback_plan, acceptance_criteria, review_checklist, pr_draft.

CRITICAL CONTRACT (Prompt_1_Master §O):
- compiler may PREPARE change artifacts
- it may NEVER apply them to live runtime or main
- compiler outputs are deterministic from pinned meta-proposal inputs
- reviewability > automation

Crown-jewel area waggledance/core/proposal_compiler/*
(BUSL Change Date 2030-03-19).
"""

PROPOSAL_COMPILER_SCHEMA_VERSION = 1
