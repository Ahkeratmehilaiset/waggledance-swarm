# Prompt 2 — Inputs and Contracts

**Status:** Inputs only. This document prepares everything Prompt 2
needs but does NOT execute Prompt 2. The atomic runtime flip lives in a
separate prompt file by design (Prompt 1 §SEPARATE PROMPT 2 RULE).

Prompt 1 (`Prompt_1_Master_v5_1.txt`) built and validated phases F–Q
on branch `phase9/autonomy-fabric`. Prompt 2 will:
- review the post-campaign runtime state
- validate the human approval artifact
- execute the atomic flip
- own rollback

This document defines what Prompt 2 must consume, what it must produce,
and what invariants it MUST preserve.

## 1. Preconditions Prompt 2 must verify before doing anything

Before any flip-related action, Prompt 2 MUST verify ALL of:

1. **Phases F–Q are review-green on `phase9/autonomy-fabric`.**
   - The latest commit on the branch must include
     `docs/runs/phase9_autonomy_fabric_state.json`'s
     `next_recommended_action` indicating "ALL 16 PHASES COMPLETE".
   - Targeted Phase 9 test suite passes:
     `python -m pytest tests/test_phase9_*.py -q`
   - Phase 9 commit count matches what `state.json` says.
2. **The 400h gauntlet campaign is finished or frozen.**
   - The primary repo at `C:/python/project2/` either has no live
     auto-commit pid in `docs/runs/ui_gauntlet_400h_*/`, or all such
     pids are confirmed terminated.
   - The latest auto-checkpoint commit on `main` is reachable.
3. **Human review has happened.**
   - There is a signed approval artifact (see §3 below) with a
     `human_approval_id` of the form `human:<reviewer>:<utc-iso>`.
   - The approval artifact's `branch_under_review` field equals
     `phase9/autonomy-fabric` and its `commit_under_review` field
     equals the tip commit of that branch at approval time.
4. **No live LLM dependency in the flip path.**
   - Prompt 2 MUST NOT itself call an external provider; only the
     pinned approval artifact + commit hashes drive the flip.

Any precondition failure aborts Prompt 2 cleanly. Prompt 2 never
"partial-flips".

## 2. Pinned inputs Prompt 2 consumes

Prompt 2 reads ONLY from these pinned paths (no globbing, no live
discovery). Each entry is read once and recorded into Prompt 2's own
state file with `sha256_first_4096`, `sha256_last_4096`, `mtime_epoch`,
`size_bytes`.

| Path | Purpose |
|---|---|
| `docs/runs/phase9_autonomy_fabric_state.json` | confirms F–Q completion |
| `docs/architecture/HOOKS_FOR_RUNTIME_REVIEW.md` | runtime review hook contract |
| `LICENSE-BUSL.txt` | BUSL change date check |
| `tests/test_phase9_*.py` | targeted Phase 9 test set |
| `<approval_artifact_path>` | human approval (see §3) |
| `<previous_main_tip_sha>` | recorded so rollback is byte-exact |

Prompt 2 does NOT read any `docs/runs/ui_gauntlet_400h_*/` artifacts;
campaign data is informational, not load-bearing for the flip.

## 3. Approval artifact contract

The approval artifact is a JSON file authored by a human reviewer.
Prompt 2 refuses to flip without one. Required fields:

```json
{
  "human_approval_id": "human:<reviewer>:<utc-iso>",
  "approval_kind": "phase9_atomic_flip",
  "branch_under_review": "phase9/autonomy-fabric",
  "commit_under_review": "<full sha40>",
  "phase_set_reviewed": ["F","G","H","I","P","V","J","U1","U2","U3","L","K","M","O","N","Q"],
  "test_suite_run": "tests/test_phase9_*.py",
  "test_suite_result": "626 passed",
  "rationale": "<free-form, non-empty>",
  "rollback_target_sha": "<full sha40>",
  "no_runtime_auto_promotion": true,
  "no_main_branch_auto_merge": true,
  "no_foundational_mutation": true,
  "no_raw_data_leakage": true,
  "approved_at_utc": "<iso8601>"
}
```

If any of the four `no_*` fields are absent or not boolean `true`,
Prompt 2 aborts.

`commit_under_review` MUST be the full 40-char sha. Short shas are not
accepted.

## 4. Atomic flip semantics (chosen here so Prompt 2 cannot reinvent)

The flip is a fast-forward merge of `phase9/autonomy-fabric` into
`main`, conditional on the ladder. It is NOT a rebase, NOT a squash,
NOT a force-push, and NOT a runtime auto-promote.

Procedure (Prompt 2 will execute exactly this):

1. Re-verify §1 preconditions; abort on any miss.
2. Compute the hash of the approval artifact and compare to the value
   recorded in Prompt 2's state file. If they differ → abort.
3. Confirm `git merge-base --is-ancestor main phase9/autonomy-fabric`
   (i.e. the flip is a fast-forward).
4. Run the targeted Phase 9 test set one final time on the worktree at
   `phase9/autonomy-fabric`. If any test fails → abort.
5. Record `previous_main_tip_sha` and `flip_target_sha` in Prompt 2's
   state file before any branch movement.
6. `git push origin phase9/autonomy-fabric:main` (no force).
7. Re-verify `main` tip equals `flip_target_sha`. If not → invoke
   rollback (§5).
8. Append to `docs/runs/atomic_flip_history.jsonl` the chained record
   `{previous_main_tip_sha, flip_target_sha, approval_id,
   approval_artifact_sha256, no_force, ts_utc}`.

The flip never touches:
- `LICENSE-BUSL.txt`
- the live 400h campaign artifacts
- the local model artifacts (Phase 9 §N is scaffold-only)

## 5. Rollback contract

Rollback target is `previous_main_tip_sha` recorded in step 5 above.
If the post-flip verification (step 7) reveals divergence, Prompt 2
MUST:

1. NOT delete or rewrite any commit on the branch.
2. Push `previous_main_tip_sha` back as the tip of `main` only via:
   ```
   git push origin <previous_main_tip_sha>:main
   ```
   without `--force`. If this is rejected because new commits arrived
   in the interval, escalate to human review (see §6) instead of
   force-pushing.
3. Append a rollback record to `atomic_flip_history.jsonl` with both
   sides of the rollback and the divergence rationale.

## 6. When to escalate to human review (instead of acting)

Prompt 2 stops and asks for human review when:
- the approval artifact is missing or malformed
- §1 preconditions diverge from what the approval artifact recorded
- a non-fast-forward merge would be required
- any concurrent push to `main` happened between approval time and
  flip time
- any test in `tests/test_phase9_*.py` regresses
- a force-push would be required to roll back

In all of these cases, Prompt 2 emits a `human_review_required.json`
record under `docs/runs/` and exits 0 without touching `main`.

## 7. Out-of-scope for Prompt 2

Prompt 2 must not:
- run a full `pytest -q` (acceptable scope is the targeted Phase 9
  suite plus any hooks the approval artifact pinned)
- modify `LICENSE-BUSL.txt`
- enable any experimental autonomy profile (see
  `EXPERIMENTAL_AUTONOMY_PROFILE.md`)
- promote any local model lifecycle status beyond `advisory`
- apply any `local_intelligence.fine_tune_pipeline` plan
- call any external provider
- launch any subprocess outside the documented `git` invocations

If a future variant of Prompt 2 wants any of the above, it MUST first
amend this document with the specific contract.

## 8. Recommended state file shape for Prompt 2

`docs/runs/prompt_2_atomic_flip_state.json`:

```json
{
  "prompt_2_version": 1,
  "branch_under_flip": "phase9/autonomy-fabric",
  "approval_artifact_path": "<path>",
  "approval_artifact_sha256": "<64-hex>",
  "approval_artifact_sha12": "<12-hex>",
  "previous_main_tip_sha": "<full sha40 set at step 5>",
  "flip_target_sha": "<full sha40 set at step 5>",
  "preconditions_verified_at_utc": "<iso>",
  "flip_executed_at_utc": "<iso or null>",
  "post_flip_verified": false,
  "rollback_executed": false,
  "human_review_required": false,
  "no_force_push": true,
  "no_runtime_auto_promotion": true,
  "no_foundational_mutation": true
}
```

The four `no_*` fields are structural invariants and MUST always be
`true` in this file. They are not toggles.
