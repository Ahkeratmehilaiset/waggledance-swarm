"""Claude Code Builder / Mentor Lane — Phase 9 §U2.

Allows WD to autonomously delegate code-aware work to Claude Code
builder/mentor agents in worktree-isolated branches. Outputs are
ALWAYS request-pack driven, schema-validated, reviewable, replayable,
and NEVER auto-merged to main.

CRITICAL CONTRACT (Prompt_1_Master §U2):
- builder lane may create isolated branches/worktrees, commit
  candidates, run targeted tests, emit review-ready packs
- builder lane may NOT merge to main, mutate live runtime, change
  constitution, or finalize foundational identity
- mentor notes are advisory-only IR objects; they cannot trigger
  architectural or runtime changes by themselves
"""

BUILDER_LANE_SCHEMA_VERSION = 1

TASK_KINDS = (
    "generate_solver_candidate",
    "generate_test",
    "repair_adapter",
    "patch_schema",
    "fix_replay_defect",
    "improve_cli",
    "enrich_review_bundle",
    "mentor_note",
)

OUTCOME_STATES = (
    "success",
    "partial",
    "failed",
    "blocked",
    "advisory_only",
)

ARTIFACT_KINDS = (
    "solver_candidate",
    "test_file",
    "patch_diff",
    "schema_patch",
    "review_bundle_addendum",
    "mentor_note",
)
