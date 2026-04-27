# 2026-04-27: Phase 8.5 publication outcome

Performed under WD `Claude_Code_unified_release_finalization_prompt_v3.md` Phase 3.

## Verdict: ALL FIVE phase8.5 branches REMAIN DEFERRED to follow-up PRs

Honored the audit at `docs/runs/local_only_branch_audit.md`. No phase8.5 PRs were created in this session. No phase8.5 branches were rebased or merged.

## Per-branch state (against `main @ a1c4152`)

| Branch | Local SHA | Origin SHA | Ahead/behind main | Verdict | Action this session |
|---|---|---|---|---|---|
| `phase8.5/vector-chaos` | `322d8b8f` | (not on remote) | ahead 71, behind 1 | DEFER | None — local-only preserved in worktree |
| `phase8.5/curiosity-organ` | `2efc4f70` | `1a31b248` (older) | ahead 86, behind 1 | DEFER | None — operator's worktrees hold latest |
| `phase8.5/self-model-layer` | `8478c59d` | `e3479dd9` (older) | ahead 107, behind 1 | DEFER | None — operator's worktrees hold latest |
| `phase8.5/dream-curriculum` | `bfa526a2` | `bfa526a2` (in sync) | ahead 160, behind 1 | DEFER | None — origin already current |
| `phase8.5/hive-proposes` | `de8c341a` | (not on remote) | ahead 141, behind 1 | DEFER | None — local-only preserved in worktree |

All five branches are 1 commit behind main (the squash-merge `a1c4152`); their follow-up PR sessions will need to rebase onto current main before opening PRs.

## Why deferred (recap from audit)

Per `docs/runs/local_only_branch_audit.md`:

- **Phase 9 PR is self-contained.** The 16-phase autonomy fabric scaffold doesn't import any phase8.5 producer module. Verified by source-grep.
- **Phase 9 ships the IR adapter contracts** (`from_curiosity.py`, `from_self_model.py`, `from_dream.py`, `from_hive.py`) under `waggledance/core/ir/adapters/`. The contracts live on main; producers can ship later without choreography.
- **The 4 evidence artifacts in Phase 9** (Reality View render, kernel tick, conversation probe, proposal compiler bundle) are JSON outputs baked into the Phase 9 commit. They demonstrate the contracts work against real Session B/D data, but the producer code doesn't need to be on main for those JSON files to remain valid.
- **Strategy A directive:** "do not opportunistically merge unrelated experiments". Phase 8.5 subsystems are functionally separate ship-units.

## Why no reference push of missing branches

The unified prompt §3.2 says reference push of deferred-but-missing branches is OPTIONAL ("if missing on origin and this is safe/useful"). The two missing branches are:

- `phase8.5/hive-proposes` (141 commits, local only)
- `phase8.5/vector-chaos` (71 commits, local only)

Decision: **DO NOT reference-push** in this session. Reasoning:

1. **Audit explicitly says DEFER** — opening up a "reference branch" on origin without a PR creates a dangling artifact that external observers might misinterpret as ready for review.
2. **Branch hygiene** — when each branch's actual follow-up PR session rebases onto post-Phase-9 main, that's the right time to push. Pushing now means push-and-then-force-push-after-rebase, which the No-Force rule would block.
3. **Local-only branches are safe** in the operator's primary worktree (project2 + project2-{a,b,c,d,r7_5} sibling worktrees). Loss-of-machine is the only risk, and that's already covered by the operator's offsite backups.

## Follow-up PR sequence (recommended order)

When the operator chooses to land Phase 8.5 work on main, do them in dependency order:

1. **`phase8.5/vector-chaos → main`** (PR #N+1, R7.5 Vector Writer Resilience — independent of others)
   - Rebase: `git rebase origin/main` (1 commit behind, expect clean)
   - Push: `git push -u origin phase8.5/vector-chaos`
   - PR via `tools/wd_pr_prepare.py --branch phase8.5/vector-chaos --execute`

2. **`phase8.5/curiosity-organ → main`** (PR #N+2, Session A — Curiosity Organ — depends on R7.5 audit hooks)
   - Wait for #N+1 to merge; rebase onto new main
   - Push: `git push origin phase8.5/curiosity-organ` (ff)

3. **`phase8.5/self-model-layer → main`** (PR #N+3, Session B — Self-Model Layer — depends on Session A's gap_miner outputs)
   - Wait for #N+2; rebase

4. **`phase8.5/dream-curriculum → main`** (PR #N+4, Session C — Dream Pipeline — depends on Session B's self_model snapshots)
   - Wait for #N+3; rebase

5. **`phase8.5/hive-proposes → main`** (PR #N+5, Session D — The Hive Proposes — depends on Session C's dream meta-proposals)
   - Wait for #N+4; push -u origin (first time)

Once #N+5 lands, the system has end-to-end real-data path: ingest → curiosity → self-model → dream → hive → Phase 9 cognition IR → proposal compiler → human review. At that point Prompt 2 atomic runtime flip becomes schedulable (additional gates: signed `HUMAN_APPROVAL.yaml`, campaign frozen).

## Strategy A compliance

- ✅ No history rewrite on any phase8.5 branch
- ✅ No force-push
- ✅ No PR creation against the audit verdict
- ✅ No branch deletion as cleanup
- ✅ Operator retains full control of when Phase 8.5 PRs land
