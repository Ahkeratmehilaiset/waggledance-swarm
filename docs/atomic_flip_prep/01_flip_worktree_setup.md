# 01 — Flip Worktree Setup

The atomic flip runs in an **isolated worktree** dedicated to the flip operation. The release worktree (`C:/python/project2-master/`) and the primary repo (`C:/python/project2/`) MUST NOT be the active flip surface.

## Setup

```bash
# 1. Pick a fresh path on the C: drive (not a RAM disk; see CLAUDE.md golden rules)
git worktree add /c/python/project2-flip main

# 2. Verify clean state
cd /c/python/project2-flip
git status
git remote -v   # must point at github.com/Ahkeratmehilaiset/waggledance-swarm
git log -1 --format="%h %s"   # should match origin/main tip

# 3. Pin the expected post-flip head SHA (= phase9/autonomy-fabric tip at flip time)
EXPECTED_HEAD_SHA=$(git ls-remote --heads origin phase9/autonomy-fabric | awk '{print $1}')
echo "Expected post-flip main tip: $EXPECTED_HEAD_SHA"
```

## Verify the flip target is current

The Prompt 2 session must verify that the flip target (the release branch) has not changed since the approval artifact was signed:

```bash
# In the flip worktree:
git fetch origin

LOCAL_RELEASE_TIP=$(git rev-parse origin/phase9/autonomy-fabric)
APPROVED_SHA=$(yq '.commit_under_review' < HUMAN_APPROVAL.yaml)

if [ "$LOCAL_RELEASE_TIP" != "$APPROVED_SHA" ]; then
    echo "ABORT: release branch tip ($LOCAL_RELEASE_TIP) does not match approved sha ($APPROVED_SHA)"
    exit 1
fi
```

If they don't match, **do not proceed** — the approval is for a specific SHA. Either re-sign for the new SHA or revert the release branch.

## What the flip is not

The flip is NOT:

- a force-push
- a rebase of main
- a runtime restart
- a config file change in production
- a database migration

The flip IS:

- a fast-forward `git push origin <release_branch>:main` with `--force-with-lease=main:<previous_main_tip>`

That's the entirety of the operation in git terms. The "atomic" part means the operation either succeeds completely (main now points at the release branch tip) or fails completely (main is unchanged) — there is no half-state.

## Cleanup after flip

```bash
# Verify post-flip state
git fetch origin
git log -1 origin/main --format="%h %s"   # should equal EXPECTED_HEAD_SHA

# Optional: clean up worktree if no longer needed
cd /c/python/project2
git worktree remove /c/python/project2-flip
```
