# Merge Readiness Checklist (Phase 9 of master prompt)

Status snapshot for PR #51 → `main` squash-merge.

## Required gates (all must be `[x]` to merge automatically)

- [x] **gh auth available** — logged in as `Ahkeratmehilaiset`, scopes `repo`, `workflow`, `gist`, `read:org`
- [x] **PR #51 exists** — verified via `gh pr view 51`
- [ ] **PR #51 head SHA matches local release branch tip** — currently does NOT match: PR head `3c41a5c`, local tip `d13a13c` (6 release-polish commits not yet pushed to origin)
- [x] **PR is mergeable** — `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN` (as of session start)
- [x] **Required CI checks green** — 5/5 SUCCESS at PR head `3c41a5c` (must re-verify after pushing the 6 release-polish commits)
- [x] **No unresolved required local-only integration** — all 5 phase8.5 branches deferred per Phase 1 audit
- [x] **Versioning/docs/release notes committed** — pyproject.toml `3.6.0`, CHANGELOG entry, release_notes_draft.md, all on the release branch
- [ ] **State file says merge_ready = true** — CURRENTLY FALSE (push pending)

## Result: NOT YET MERGE-READY (one missing gate)

The release-polish work is **complete locally** but not yet on origin. The operator must push the 6 commits from `d13a13c` back to `3c41a5c` to GitHub before the squash-merge can land the v3.6.0 release.

## Exact next commands the operator must run

### Step 1 — push the 6 release-polish commits

```bash
cd /c/python/project2-master
git push origin phase9/autonomy-fabric
```

Expected output:
```
   3c41a5c..d13a13c  phase9/autonomy-fabric -> phase9/autonomy-fabric
```

### Step 2 — wait for CI to re-run on the new tip

CI takes ~5 minutes. Monitor with:

```bash
gh pr checks 51
```

Expect all 5 checks to return SUCCESS at the new head SHA.

### Step 3 — re-verify mergeability

```bash
gh pr view 51 --json mergeable,mergeStateStatus,headRefOid
```

Expect:
- `mergeable: "MERGEABLE"`
- `mergeStateStatus: "CLEAN"`
- `headRefOid: <40-char sha matching d13a13c expanded>` 

### Step 4 — squash-merge with head-SHA guard

```bash
EXPECTED_HEAD=$(git rev-parse phase9/autonomy-fabric)
gh pr merge 51 \
  --squash \
  --match-head-commit="$EXPECTED_HEAD" \
  --subject "feat(release): Phase 9 — Autonomy Fabric (v3.6.0)" \
  --body "$(cat docs/runs/release_notes_draft.md)"
```

Note: `--delete-branch` is intentionally NOT passed. The release branch is preserved as evidence and as the base for follow-up Phase 8.5 PRs.

### Step 5 — verify main tip moved

```bash
git fetch origin
git log -1 origin/main --format="%h %s"
```

Expect a new commit titled "feat(release): Phase 9 — Autonomy Fabric (v3.6.0) (#51)" or similar (GitHub formats squash-merge titles).

### Step 6 — tag and release

```bash
git checkout main
git pull
git tag -a v3.6.0 -m "Phase 9 — Autonomy Fabric (Release-only; atomic flip deferred to Prompt 2)"
git push origin v3.6.0

gh release create v3.6.0 \
  --title "v3.6.0 — Phase 9 Autonomy Fabric" \
  --notes-file docs/runs/release_notes_draft.md \
  --latest
```

### Step 7 — record outcome

In `docs/runs/release_bundle_2026_04_27/`:

```bash
echo "<merged_commit_sha>" > merged_commit_sha.txt
echo "v3.6.0" > release_tag.txt
gh release view v3.6.0 --json tagName,name,body,createdAt > gh_release_status.md
```

## Known potential blockers (per release_risk_register.md)

- **Risk 6:** Branch protection on `main` may require manual confirmation (e.g., status checks, required reviewers). If `gh pr merge` returns an error citing branch protection, the operator must either (a) authorize the override, (b) wait for required reviewers to approve, or (c) merge via the GitHub web UI manually.
- **Risk 7:** If something else lands on main between Step 1 and Step 4, the squash will need a rebase. The `--match-head-commit` guard will fail safely if the PR head moved between read and merge.
