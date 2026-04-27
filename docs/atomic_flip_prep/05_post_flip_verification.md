# 05 — Post-Flip Verification

Run all of these within 5 minutes of the flip. Any failure triggers `06_rollback_procedure.md`.

## Step 1 — Confirm main has advanced to the expected SHA

```bash
cd /c/python/project2-flip
git fetch origin

EXPECTED_HEAD_SHA=$(yq '.commit_under_review' < HUMAN_APPROVAL.yaml)
ACTUAL_MAIN_TIP=$(git rev-parse origin/main)

if [ "$ACTUAL_MAIN_TIP" != "$EXPECTED_HEAD_SHA" ]; then
    echo "FAIL: main tip is $ACTUAL_MAIN_TIP, expected $EXPECTED_HEAD_SHA"
    echo "Trigger rollback per 06_rollback_procedure.md"
    exit 1
fi
echo "ok: main tip matches expected post-flip SHA"
```

## Step 2 — Confirm PR #51 reports MERGED

```bash
PR_STATE=$(gh pr view 51 --json state -q '.state')
if [ "$PR_STATE" != "MERGED" ]; then
    echo "WARN: PR #51 state is $PR_STATE (expected MERGED)"
fi

PR_MERGED_SHA=$(gh pr view 51 --json mergeCommit -q '.mergeCommit.oid')
echo "PR #51 merge commit: $PR_MERGED_SHA"
```

For squash-merge, `PR_MERGED_SHA` will be a NEW commit on main that GitHub created during squash. It will NOT equal `commit_under_review` exactly — it will be the squash-result commit. That's expected.

For fast-forward push (manual flip), `PR_MERGED_SHA` may be empty (gh doesn't always populate it for non-merge-button merges). Verify via git instead.

## Step 3 — Targeted Phase 9 suite still green on main

```bash
git checkout main
git pull
python -m pytest tests/test_phase9_*.py -q
```

Expect: `657 passed`.

If anything fails, this is a regression introduced by the flip itself (extremely unusual for a fast-forward, but possible if the squash-merge produced a commit that differs from the release branch's state). Trigger rollback.

## Step 4 — CI on main runs and shows green

```bash
gh run list --branch main --limit 5
```

Expect: most recent run for the flip commit shows `success` status. If it shows `failure` or `cancelled`, investigate before declaring the flip successful.

## Step 5 — Append to atomic_flip_history.jsonl

Append a chained record to `docs/runs/atomic_flip_history.jsonl`:

```json
{
  "previous_main_tip_sha": "<sha40>",
  "flip_target_sha": "<sha40>",
  "actual_main_tip_after_flip": "<sha40>",
  "approval_id": "<human_approval_id>",
  "approval_artifact_sha256": "<sha256>",
  "no_force": true,
  "no_runtime_auto_promotion": true,
  "no_foundational_mutation": true,
  "ts_utc": "<iso-8601>"
}
```

Use the same chain pattern as elsewhere: each record carries `prev_entry_sha256` of the previous line.

## Step 6 — Update the Prompt 2 state file

Set in `docs/runs/prompt_2_atomic_flip_state.json`:

```json
{
  "post_flip_verified": true,
  "flip_executed_at_utc": "<iso-8601>"
}
```

If verification failed at any step:

```json
{
  "post_flip_verified": false,
  "rollback_executed": true,
  "rollback_executed_at_utc": "<iso-8601>",
  "rollback_target_sha": "<sha40>"
}
```

## Step 7 — Communicate

- Operator announces the successful (or rolled-back) flip
- Tag is created on the squash-merge commit (or on the fast-forwarded main tip): `git tag -a v3.6.0 -m "Phase 9 Autonomy Fabric"` then `git push origin v3.6.0`
- GitHub release is created from the tag using `release_notes_draft.md`

## What "verified" means

The flip is considered verified when:

1. main points at the expected SHA
2. PR #51 reports MERGED
3. Targeted suite passes on main
4. CI on main passes
5. atomic_flip_history.jsonl has a complete chained record
6. State file shows `post_flip_verified=true`
7. Tag and release exist

If any of (1)–(4) fails, immediately trigger rollback. Do NOT attempt to fix forward — main is the single source of truth and must reflect a known-good state.
