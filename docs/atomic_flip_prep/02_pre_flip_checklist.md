# 02 — Pre-Flip Checklist

All items must be `[x]` before the Prompt 2 session begins the flip. Any unchecked item aborts the flip cleanly.

## State

- [ ] `phase9/autonomy-fabric` is on main as a squash-merge commit (or merge commit, see operator preference)
- [ ] `main` tip is reachable from the release branch's expected SHA
- [ ] All five Phase 8.5 follow-up PRs are merged on main, OR explicitly deferred with documented reason in the approval artifact
- [ ] No new commits on the release branch after the approval was signed
- [ ] Origin remote URL is verified (`git remote -v` matches `github.com/Ahkeratmehilaiset/waggledance-swarm`)

## Campaign

- [ ] 400h gauntlet campaign is finished OR explicitly frozen
- [ ] `docs/runs/ui_gauntlet_400h_*/` directories show no active auto-commit pid files
- [ ] No background daemons are writing to `hot_results.jsonl`, `warm_results.jsonl`, `cold_results.jsonl`

## Approval

- [ ] `HUMAN_APPROVAL.yaml` exists and is signed
- [ ] Its `human_approval_id` is of the form `human:<reviewer>:<utc-iso>`
- [ ] Its `commit_under_review` exactly matches `git rev-parse origin/phase9/autonomy-fabric` (full 40-char SHA)
- [ ] Its `phase_set_reviewed` includes all 16 phases (F, G, H, I, P, V, J, U1, U2, U3, L, K, M, O, N, Q)
- [ ] Its `test_suite_run` field references the targeted Phase 9 suite
- [ ] Its `test_suite_result` shows green (e.g. `"657 passed"`)
- [ ] Its `rollback_target_sha` is the previous main tip (full 40-char SHA)
- [ ] All four `no_*` invariants are present and `true`:
  - `no_runtime_auto_promotion: true`
  - `no_main_branch_auto_merge: true`
  - `no_foundational_mutation: true`
  - `no_raw_data_leakage: true`
- [ ] The approval artifact's sha256 has been pinned in the Prompt 2 state file

## Tests

- [ ] Targeted Phase 9 suite passes locally on the flip worktree (`pytest tests/test_phase9_*.py -q`)
- [ ] CI on the release branch shows all green checks
- [ ] No CI runs are queued or in-progress against `main`

## Auth + permissions

- [ ] `gh auth status` shows logged-in account with `repo` scope
- [ ] The signed-in account has push permission to `main`
- [ ] If main is protected with required reviews / status checks, those are satisfied
- [ ] No merge queue is blocking the operation

## Rollback readiness

- [ ] `previous_main_tip_sha` is recorded (full 40-char) in the Prompt 2 state file BEFORE the flip
- [ ] `06_rollback_procedure.md` has been read by the operator in the past 24 hours
- [ ] No outstanding open PRs against main from other branches that would block a rollback

## Documentation

- [ ] `CHANGELOG.md` has the `[3.6.0]` entry on the release branch
- [ ] `pyproject.toml` version is `3.6.0` on the release branch
- [ ] Release notes draft is reviewed and ready

## When all boxes are checked

Proceed to the actual flip per `01_flip_worktree_setup.md`. The flip itself is one git command. Everything else is preparation and verification.
