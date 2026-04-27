# Final Runtime Flip — Deferred to Prompt 2

This release **does not** execute the atomic runtime flip. The flip is intentionally a separate risk domain handled by a separate prompt session.

## What "deferred" means precisely

When PR #51 merges to `main` (whether in this session or in the operator's next session), `main` will point at the v3.6.0 commit containing the full Phase 9 autonomy fabric. **Main being at v3.6.0 does not mean the new fabric is live.**

The live runtime read path is determined by:

1. The startup script's choice of import paths
2. The DI container's wiring in `waggledance/bootstrap/`
3. The actual deployed binary or container running in production

None of these change as a result of the v3.6.0 merge. The deployed runtime continues to consult the previous (pre-Phase-9) code path.

## How the flip changes that

Per `docs/architecture/PROMPT_2_INPUTS_AND_CONTRACTS.md` and `docs/atomic_flip_prep/01_flip_worktree_setup.md`, the flip is a fast-forward `git push origin <release_branch>:main` with `--force-with-lease=main:<previous_main_tip>`.

This is the entirety of the flip in git terms. Because v3.6.0 has already landed on main via squash-merge during the release (this session), the actual "flip" is operational redeployment — restarting the runtime so it picks up the new import paths.

## Why this separation

Three reasons:

1. **Risk isolation.** A code merge that adds a 25 000-line scaffold is reviewable as a code change. A runtime flip that repoints production traffic is reviewable as an operational change. Combining them obscures both.

2. **Human gate enforcement.** The promotion ladder (Phase M) requires a `human_approval_id` to enter any of the 4 RUNTIME_STAGES. Merging code does NOT itself satisfy this requirement; only an explicit signed approval artifact does.

3. **Campaign safety.** The 400h gauntlet campaign is currently running. A live runtime flip during a campaign would invalidate campaign telemetry and risk integrity issues. The flip is gated on the campaign being finished or frozen.

## What the operator does after this release lands on main

Nothing immediately. The release is review-only. The runtime continues as before.

When the operator decides to flip (after Phase 8.5 follow-ups land + campaign frozen + approval signed):

1. Open Prompt 2 session
2. Set up flip worktree per `docs/atomic_flip_prep/01_flip_worktree_setup.md`
3. Run pre-flip checklist (`02_pre_flip_checklist.md`)
4. Verify approval artifact integrity
5. Execute the fast-forward push
6. Run post-flip verification (`05_post_flip_verification.md`)
7. Either record success or trigger rollback (`06_rollback_procedure.md`)

## Rollback if the flip ever runs

Even after the flip runs in Prompt 2, the rollback target is preserved as `previous_main_tip_sha` in the Prompt 2 state file. A `--force-with-lease` rollback is always available within seconds of the flip, gated on the same human approval artifact.

## Why the release makes sense even without the flip

The Phase 9 release is valuable on its own:

- It establishes the contract surfaces (IR adapters, schemas, promotion ladder)
- It enables the Phase 8.5 follow-up PRs to land cleanly on top
- It demonstrates that the autonomy fabric works end-to-end against real data (4 evidence artifacts)
- It documents what is and is not in scope (transparent disclaimers in README, CHANGELOG, deferred docs)

A reviewer looking at v3.6.0 main can:

- Read the full architecture in `docs/architecture/PHASE_9_ROADMAP.md`
- Verify all 657 targeted tests pass
- See real evidence artifacts at `docs/runs/phase9_*`
- Understand exactly what is deferred and why

The flip is a separate decision, made later, by a human with full context.
