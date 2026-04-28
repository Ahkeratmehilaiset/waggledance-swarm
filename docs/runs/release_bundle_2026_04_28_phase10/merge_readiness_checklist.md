# Phase 10 Merge Readiness Checklist

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Branch is descended from `origin/main` (no force / no rewrite) | ✅ | `tests/phase10/test_truth_regression.py::test_phase10_branch_history_is_linear_descended_from_main` |
| 2 | All targeted tests pass locally | ✅ | 72 passed, 1 skipped, 5.19 s |
| 3 | LICENSE-CORE.md lists every new crown-jewel file | ✅ | `tests/phase10/test_truth_regression.py::test_license_core_lists_every_phase10_crown_jewel_file` |
| 4 | Every new crown-jewel file has BUSL-Change-Date 2030-12-31 header | ✅ | `tests/phase10/test_truth_regression.py::test_phase10_crown_jewel_files_have_change_date_header` |
| 5 | No machine-specific absolute paths in committed docs | ✅ | `tests/phase10/test_truth_regression.py::test_no_machine_specific_absolute_paths_in_phase10_journal_docs` |
| 6 | No secrets in committed crown-jewel files | ✅ | `tests/phase10/test_truth_regression.py::test_no_secrets_in_phase10_crown_jewel_files` |
| 7 | `provider_invocations.jsonl` and `error_log.jsonl` initialized | ✅ | RULE 8 + RULE 14 |
| 8 | CURRENT_STATUS.md, README.md, CHANGELOG.md updated truthfully | ✅ | P6 commit `5a98aad` |
| 9 | Phase 9 modules unchanged | ✅ | Phase 10 composes; never duplicates Phase 9 names except in distinct paths |
| 10 | Phase 8.5 branches not touched | ✅ | RULE 12 honoured; `release_to_main_state.json::phase8_5_isolation` confirms |
| 11 | Atomic flip not executed | ✅ | RULE 18; no runtime mutation in any commit |
| 12 | No `git push --force` / `--force-with-lease` / filter-* attempted | ✅ | RULE 3; no destructive git ops in this session |
| 13 | Branch pushed to origin | ⏳ | P9.1 — pending |
| 14 | PR created or updated with truthful body | ⏳ | P9.2 — pending |
| 15 | CI green at exact branch tip SHA | ⏳ | P9.3 — pending |
| 16 | Squash-merge with `--match-head-commit` guard | ⏳ | P9.4 — pending |
| 17 | Tag and GitHub release | ⏳ | P9.5 — pending; tag name TBD (post-v3.6.0 substrate; may not warrant a new SemVer tag — see release_summary.md) |

## Decision: tag this work or not?

The Phase 10 commits are **substrate** — they add no runtime hot-path behavior change. The release bundle is honest about that. Two options for the operator at merge time:

* **Option A — no new tag.** Land the squash-merge to main as `chore(phase10): foundation, truth, builder lane substrate`. v3.6.1 stays unminted; the next runtime-affecting release picks the version. This is the safer default given the substrate framing.
* **Option B — `v3.6.1-substrate` annotated tag.** Mark the substrate landing without claiming runtime behavior change. `v3.6.1` proper is reserved for the next runtime change.

The bundle is authored to support **Option A**. The `release_notes_draft.md` is suitable as a PR description without requiring a tag.

## Operator next-step commands

If push and CI succeed automatically in P9, no operator action is needed.

If push silently backgrounds (the failure mode observed in the v3.6.0 session), the operator should run from their own Git Bash:

```
cd /c/python/project2-master
git push -u origin phase10/foundation-truth-builder-lane
```

Then create the PR:

```
gh pr create \
  --base main \
  --head phase10/foundation-truth-builder-lane \
  --title "Phase 10 — foundation / truth / builder lane (substrate)" \
  --body-file docs/runs/release_bundle_2026_04_28_phase10/release_notes_draft.md
```

After CI is green at the exact head SHA:

```
EXPECTED_HEAD=$(git rev-parse phase10/foundation-truth-builder-lane)
gh pr merge --squash --match-head-commit="$EXPECTED_HEAD" \
            --subject "feat: Phase 10 substrate (control plane + provider plane + scale-aware Reality View)" \
            --body-file docs/runs/release_bundle_2026_04_28_phase10/release_notes_draft.md
```

If the operator picks Option B (tag), after merge:

```
git checkout main && git pull
git tag -a v3.6.1-substrate -m "Phase 10 — foundation / truth / builder lane substrate"
git push origin v3.6.1-substrate
gh release create v3.6.1-substrate \
  --title "v3.6.1-substrate — Phase 10 foundation" \
  --notes-file docs/runs/release_bundle_2026_04_28_phase10/release_notes_draft.md \
  --prerelease
```

Use `--prerelease` because this is substrate, not a runtime release.
