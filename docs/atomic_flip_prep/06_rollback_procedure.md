# 06 — Rollback Procedure

Triggered if any step in `05_post_flip_verification.md` fails.

## Pre-rollback checks

Before issuing a rollback, confirm:

- The `previous_main_tip_sha` is recorded (full 40-char) in the Prompt 2 state file
- The rollback is NOT a knee-jerk reaction to a transient CI flake — re-run the targeted suite once before deciding
- The rollback target SHA does still exist on origin (it should; main was just there)

## Rollback procedure

```bash
cd /c/python/project2-flip

# 1. Pin the rollback target
ROLLBACK_SHA=$(yq '.rollback_target_sha' < HUMAN_APPROVAL.yaml)
CURRENT_MAIN=$(git rev-parse origin/main)

# 2. Sanity check
if [ "$ROLLBACK_SHA" = "$CURRENT_MAIN" ]; then
    echo "Already at rollback target. No action needed."
    exit 0
fi

# 3. Verify the rollback target still exists
git fetch origin
git rev-parse "$ROLLBACK_SHA" >/dev/null || {
    echo "ABORT: rollback target $ROLLBACK_SHA not present on origin. Escalate to human."
    exit 1
}

# 4. Roll back via push (NOT --force; use --force-with-lease for safety)
git push --force-with-lease=main:"$CURRENT_MAIN" origin "$ROLLBACK_SHA":main
```

The `--force-with-lease=main:$CURRENT_MAIN` ensures the push fails if `origin/main` has moved since we last fetched (e.g., another concurrent push). If it fails, escalate to human review — do not retry blindly.

## What if force-with-lease fails

This means another commit landed on main between the flip and the rollback attempt. Stop. Do not retry.

Emit `human_review_required.json`:

```bash
cat > docs/runs/human_review_required_$(date +%Y%m%dT%H%M%SZ).json <<EOF
{
  "kind": "rollback_blocked",
  "reason": "force-with-lease failed: main moved between flip and rollback",
  "expected_main_at_attempt": "$CURRENT_MAIN",
  "rollback_target": "$ROLLBACK_SHA",
  "approval_id": "$(yq '.human_approval_id' < HUMAN_APPROVAL.yaml)",
  "ts_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "next_action": "human reviewer must investigate concurrent push and decide manually"
}
EOF
```

## What if rollback succeeds

Verify:

```bash
git fetch origin
ACTUAL=$(git rev-parse origin/main)
if [ "$ACTUAL" != "$ROLLBACK_SHA" ]; then
    echo "ABORT: rollback push reported success but main is at $ACTUAL"
    exit 1
fi
echo "ok: rolled back to $ROLLBACK_SHA"
```

Append to `atomic_flip_history.jsonl`:

```json
{
  "kind": "rollback",
  "rollback_target_sha": "<sha40>",
  "previous_main_tip_before_rollback": "<sha40 — was the flip target>",
  "approval_id": "<human_approval_id>",
  "rationale": "<from the failure mode in step 05_post_flip_verification.md>",
  "no_force": false,
  "force_with_lease": true,
  "ts_utc": "<iso-8601>"
}
```

## Hard rules

1. **No `--force` (without `--force-with-lease`).** Plain force-push can clobber commits we don't know about.
2. **No retrying after force-with-lease failure.** That means concurrent state. Escalate to human review.
3. **No fix-forward attempts.** If post-flip verification fails, roll back. Investigate the failure on a separate branch, not on main.
4. **No deletion of the release branch.** It's evidence; preserve it.
5. **No deletion of `atomic_flip_history.jsonl` entries.** Append-only; the rollback record is itself a valid entry in the history.

## Communicating

- Operator announces the rollback
- The Phase 9 release is now in "review-only" status again on the (still-existing) release branch
- Operator and reviewer schedule a fresh review cycle
- A fresh approval artifact must be authored before any subsequent flip attempt — the original approval is invalidated by the rollback

## Why force-with-lease and not force

`--force` overwrites whatever is on origin. `--force-with-lease=main:$CURRENT_MAIN` says: "only force-push if you (origin) currently have $CURRENT_MAIN as the tip of main." If anything else has been pushed in the interval, the operation fails safely.

This is the closest git gives us to a compare-and-swap on a remote ref, and it's the only acceptable rollback primitive for `main`.
