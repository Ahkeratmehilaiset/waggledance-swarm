# Phase 2A-2 baseline

Captured at session start, before any source change.

## Session

- UTC start: `2026-05-06T17:35:00Z`
- CWD: `C:\Python\project2-master`
- Git worktree: `C:\Python\project2-master`
- Git common dir: `C:\Python\project2\.git` (linked-worktree layout)
- Branch: `main`
- HEAD: `7210a7e Phase 16D — final stable gate closure: Docker and Bandit (#66)`

## Remote safety

- `git remote -v` shows origin = `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git`
  for both fetch and push. No `x-access-token:`/`ghp_`/`gho_` pattern in
  the URL. Plain HTTPS only — credential-helper handles auth, no
  embedded secret.

## Working tree summary

Untracked directories the Phase 2A-1 work landed in:

- `orchestrator/` — full Phase 2A-1 source tree (Invoke-WaggleIteration,
  lib/, tests/, all hardening tests)
- `prompts/` — `smoke.md`, `phase2a1_hardening.md`,
  `phase2a2_review_runner_production.md`
- `docs/runs/orchestrator_phase2a1_hardening_2026_05_06/` — Phase 2A-1
  reports incl. `final_report.md`, `PHASE2A2_HANDOFF.md`
- `iterations_p5_test/`, `state_p5_test/`,
  `orchestrator.config.p5_test.json` — Phase 2A-1 P5 scratch (must stay
  out of the commit)

Modified, tracked files (pre-existing, not Phase 2A-2 scope):

- `tools/waggle_backup.py`
- `tools/waggle_restore.py`

These are out of scope for this PR and will be left unstaged.

Other untracked items confirmed unrelated to Phase 2A-2 and will be left
unstaged: `WD_release_to_main_master_prompt.md`,
`docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml`, several
`docs/runs/phase16g_*`/`phase18d-f_*` runs, `docs/runs/phase9_pr_body.md`.

## Lock state

- `state/orchestrator.lock` is **absent**. No live orchestrator
  iteration is holding a lock. Safe to proceed.
- `state/current.json` records last iteration
  `2026-05-06_17-30-36`, status `COMPLETED`, no in-flight session.

## Phase 2A-1 docs found

- `docs/runs/orchestrator_phase2a1_hardening_2026_05_06/final_report.md`
  — present, says: "COMPLETED — Phase 2A-1 hardening gates green;
  Phase 2A-2 not started."
- `docs/runs/orchestrator_phase2a1_hardening_2026_05_06/PHASE2A2_HANDOFF.md`
  — present, scope matches the master prompt's Phase 2A-2 contract.

## Ignore-policy state to fix in P1

- `.gitignore:20` (`lib/`) shadows the entire `orchestrator/lib/` tree.
  `git check-ignore` confirms `orchestrator/lib/Redactor.ps1`,
  `orchestrator/lib/ArtifactValidator.ps1`,
  `orchestrator/lib/CompletionVerifier.ps1` are ignored.
- `.git/info/exclude` (Phase 2A-1) marks `prompts/smoke.md`,
  `prompts/phase2a1_hardening.md`, and the Phase 2A-1 docs run dir as
  local-only. Phase 2A-2 must commit these, so P1 will rewrite the
  exclude file to drop those entries while keeping `iterations/`,
  `state/`, the live `orchestrator.config.json`, and `.claude/` as
  local-only.

## Remote-safety state

- No credential text observed in `git config` output that was inspected.
- No `gh auth token` / `gh auth git-credential get` was run, will not
  be run.

## Known warnings carried in from Phase 2A-1

1. Public `.gitignore` `lib/` rule still shadows orchestrator source —
   resolved by minimal `!orchestrator/lib/` unignore in P1.
2. `iterations_p5_test/`, `state_p5_test/`,
   `orchestrator.config.p5_test.json` are not ignored at all in
   `.git/info/exclude` — resolved by P1 exclude rewrite.
3. Phase 2A-1 repeatability gate ran fake-claude due to lock contention.
   Phase 2A-2 P3 will run one real-Claude smoke from a clean session.
4. Live `orchestrator.config.json` has
   `dangerouslySkipPermissions=true` and `allowBash=true`. Review mode
   will NOT inherit these — the new
   `orchestrator.config.review.example.json` and the runner enforce a
   safe profile.

## Conclusion

Baseline OK. Proceed to P1.
