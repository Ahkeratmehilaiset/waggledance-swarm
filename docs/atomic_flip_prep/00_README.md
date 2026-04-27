# Atomic Flip Preparation

> **2026-04-27 status update:** During v3.6.0 release finalization, the previously-signed `HUMAN_APPROVAL.yaml` was discovered to authorize a flip operation whose mechanism (per-cell `os.replace()` on `current/` symlinks) does not match the actual repo state — those symlinks do not exist, and runtime FAISS reads come from `data/faiss/`, a different scope than the hex-cell stagings. The approval has been marked SUPERSEDED. No flip is needed for v3.6.0. The future Stage-2 cutover described in `docs/architecture/MAGMA_FAISS_SCALING.md §2` will require a new approval whose rationale matches the actual operation. Full analysis: [`docs/journal/2026-04-27_atomic_flip_analysis.md`](../journal/2026-04-27_atomic_flip_analysis.md).
>
> The files in this directory remain as a *structural template* for what a real cutover review would need (worktree setup, pre-flight checklist, approval contract, post-flip verification, rollback). They do not authorize any current operation.

**Status:** PREPARATION ONLY. The actual atomic runtime flip is **not** executed in the release-to-main session that produced this directory. It runs in a separate Prompt 2 session, gated on a signed approval artifact and the completion of the 400h gauntlet campaign.

## What's in this directory

| File | Purpose |
|---|---|
| `00_README.md` | This file. |
| `01_flip_worktree_setup.md` | How to set up a clean isolated worktree for the flip. |
| `02_pre_flip_checklist.md` | Conditions that must be true before Prompt 2 starts. |
| `03_HUMAN_APPROVAL.yaml.draft` | Human approval artifact template. The reviewer fills it in and signs. |
| `04_flip_review_bundle.md.template` | Reviewer-facing bundle template. |
| `05_post_flip_verification.md` | What to verify immediately after the flip lands. |
| `06_rollback_procedure.md` | Exact rollback procedure if post-flip verification fails. |

## Hard rule

**This directory is preparation material.** Nothing in it executes anything by itself. The atomic flip is a deliberate, human-driven action. The Prompt 2 session reads these files; it does not import or `eval` them.

If a future session attempts to use any file in this directory to execute a runtime change automatically, that session is violating the SEPARATE RISK DOMAIN RULE in `Prompt_1_Master_v5_1.txt §SEPARATE PROMPT 2 RULE`.

## When to use these files

Read them in order, in the Prompt 2 session, after:

1. Phase 9 release is on main
2. All Phase 8.5 follow-up PRs are on main
3. The 400h gauntlet campaign is finished or frozen
4. A human reviewer has authored and signed `03_HUMAN_APPROVAL.yaml.draft` (renaming it to `HUMAN_APPROVAL.yaml`)
5. The signed approval artifact's sha256 has been pinned in the Prompt 2 state file

## Reference

- `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` — the formal contract for Prompt 2's inputs, atomic flip semantics, and rollback contract
- `docs/architecture/PHASE_9_ROADMAP.md` — what is currently shipped vs deferred
- `docs/runs/release_to_main_state.json` — final state of the release-to-main session
