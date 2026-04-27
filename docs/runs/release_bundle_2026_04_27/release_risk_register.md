# Release Risk Register — v3.6.0

## Risk 1 — Push from Claude Code shell silently times out

**Severity:** Operational, not technical
**Likelihood:** Confirmed (observed yesterday and today)
**Description:** `git push` invoked from this Claude Code shell goes to background and does not return output, but the actual push may or may not complete. Yesterday's patch_generator fix push DID succeed despite no shell output (verified via `git ls-remote`).

**Mitigation:** Operator runs `git push origin phase9/autonomy-fabric` from their own Git Bash before the merge step. The 6 local-only release-polish commits are listed in `merge_readiness_checklist.md`.

## Risk 2 — Phase 8.5 producer subsystems not on main

**Severity:** Low
**Description:** Phase 9 ships the IR adapter contracts; the producers (`phase8.5/curiosity-organ`, `self-model-layer`, `dream-curriculum`, `hive-proposes`, `vector-chaos`) ship as separate follow-up PRs. Until those land, Phase 9 main has the contracts but no live producers.

**Mitigation:** Documented in `local_only_branch_audit.md` and `deferred_items.md`. No Phase 9 module imports any phase8.5 producer (verified by source-grep). No runtime breakage results from the deferral; only the data sources remain inactive until the follow-up PRs merge.

## Risk 3 — Atomic flip not yet executed

**Severity:** Intentional design
**Description:** `main` will move to v3.6.0 (Phase 9 fabric scaffold) but the live runtime read path is NOT repointed. The fabric is observable but not authoritative until Prompt 2 runs.

**Mitigation:** This is the SEPARATE PROMPT 2 RULE working as designed. The atomic flip is documented as deferred in:
- README "Final atomic runtime flip — separate session"
- CHANGELOG [3.6.0] "What is NOT in this release"
- `docs/atomic_flip_prep/00_README.md`
- `final_runtime_flip_deferred.md` (this bundle)

Operator + reviewer authorize the flip via Prompt 2 + signed approval artifact. No workaround needed — this is the contract.

## Risk 4 — README mentions test count that doesn't match full suite

**Severity:** Minor
**Description:** The README's badge says "657 Phase 9 targeted + full suite". The full suite count is intentionally vague because the targeted suite is what this release stamps as known-good. The full suite may have pre-existing flakes from older subsystems unrelated to Phase 9.

**Mitigation:** README is honest about which suite is the green-stamp surface. CI on PR #51 was running the unified suite when it last reported all-green; that's the broader sanity check.

## Risk 5 — Squash-merge collapses 105+ commits into one

**Severity:** Cosmetic
**Description:** The `git log` on main after squash-merge will show one large commit instead of 105+ phase-by-phase commits. Authors will be primarily Murata (76% of original), one squash-author (the operator who pressed "Squash and merge").

**Mitigation:** Strategy A explicitly accepts this — Murata-history is not a release blocker. The full per-commit history is preserved on `phase9/autonomy-fabric` branch (we don't delete the branch on merge). Anyone wanting commit-by-commit detail can `git log phase9/autonomy-fabric`.

## Risk 6 — Branch protection on main may require manual confirmation

**Severity:** Operational
**Likelihood:** Unknown (depends on repo settings)
**Description:** If `main` requires status checks, code review approval, signed commits, or merge queue, the squash-merge from this session may not be permitted automatically.

**Mitigation:** `merge_readiness_checklist.md` explicitly tests for each blocker before issuing `gh pr merge`. If any check fails, this session stops with exact next commands rather than forcing.

## Risk 7 — Concurrent commits on `main` between session and merge

**Severity:** Low (main has been static)
**Description:** Main tip has been `d9a6dce` since before Phase 9 started. If something else lands on main during this session, the merge may need rebase.

**Mitigation:** PR #51 reports `mergeable=MERGEABLE` and `mergeStateStatus=CLEAN` as of session start. If status changes mid-session, abort the merge step and re-validate.

## What is NOT a risk for this release

- Phase 9 code correctness — 657 tests passing, 5 GLOBAL PROPERTY checks all green
- License compliance — Phase 9 SPDX coverage 147/147, BUSL Change Date harmonized
- Identity in commits — Strategy A explicitly accepts the 76% Murata distribution; no rewriting
- Atomic flip safety — explicitly out of scope for this session
