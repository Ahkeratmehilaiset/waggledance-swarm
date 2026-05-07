## Summary

- Closes the high-impact architect-review debt from PR #95: ARCH-001 (redactor SHA carve-out fails inside JSON-encoded diff_text), ARCH-002 (AWS_SECRET_KEY pattern eats long PowerShell identifiers), ARCH-005 (review_cockpit.html at repo root → orchestrator/cockpit/), ARCH-006 (dual-ledger contract documented at `docs/design/ledger_contract.md`).
- Promotes REL-019 from `backlog` → `fixed`: investigation showed it was a 1-line shape-unification fix (`Invoke-WaggleReview -DryRun` return now carries `role`, `target_iteration_id`, `status` like the non-DryRun branch). Tightens the auto-repair classifier so analogous strict-mode missing-property findings route `LOCAL_REPAIR` in future.
- Wires the regression-ledger auto-update hooks (P5c, operator override): on FAILED/TIMEOUT/NEEDS_MANUAL_ACTION/NEEDS_REVIEW_CONFLICT iteration, an entry is appended (deduplicated by `iteration_id + failure_kind`); after a review JSON parses successfully, every critical/high finding is appended (deduplicated by `iteration_id + finding_id`). Schema enum + scoring rubric both extended with `iteration_failure`.
- Hardening gates: PRE-cleanup baseline FAILED on stale REL-019 ledger anchors (a real bug masked by GitHub CI in PR #95). POST-cleanup 29/29 PASS. Five test files grew (+32 assertions total).

## Test plan

- [x] `orchestrator/Run-WaggleHardeningGates.ps1` → 29/29 PASS (post-fix; baseline was 28/29 with `Test-PhaseFixLedger` failing on REL-019 anchors)
- [x] `Test-Redactor.ps1` 37/37 (was 27/27 — added ARCH-001a..d + ARCH-002a..c)
- [x] `Test-CockpitData.ps1` 32/32 (was 30/30 — added cockpit-at-orchestrator/cockpit/ + legacy-removed + README-exists)
- [x] `Test-FindingClassifier.ps1` 31/31 (was 28/28 — added C17d/e/f for strict-mode shape-unification signals)
- [x] `Test-RegressionLedger.ps1` 49/49 (was 35/35 — added iteration-failure hook fire/dedup/distinct-kind + review-walk hook critical-only/dedup)
- [x] `Test-ReviewRunner.ps1` 72/72 (was 69/69 — added DryRun returns role/target_iteration_id/status)
- [x] P7 internal architect + reliability self-review of R2 changes (REQUIRED override) — see `docs/runs/orchestrator_phase2br2_architect_debt_2026_05_07/p7_self_review_evidence.md`

## What was deliberately NOT done

Per the operator's "no large refactors" ground rule, three architect findings were recorded as `backlog` rather than implemented:

* **ARCH-003** — per-phase Build-Phase2B*Manifest + Run-Phase2B*EndToEndDryRun duplication. Acceptance: a future phase introduces a third manifest, then extract `Build-WaggleManifest.ps1` / `Run-WaggleEndToEndDryRun.ps1` taking phase id + manifest data; per-phase wrappers become 5-line shims; existing fixtures still PASS.
* **ARCH-004** — EpochCycleTrigger 14-branch decision priority ladder. Acceptance: refactor to `$Script:DecisionPriorityRules`; Test-EpochCycleTrigger 39 cases continue green; equivalence-on-fixture-corpus gate added before legacy alias is deleted.
* **ARCH-007** — `_FieldOr` helper duplicated across at least three orchestrator libraries. Acceptance: `orchestrator/lib/Common.ps1` with `Get-WaggleField` / `Get-WaggleSafeChildItems` / `ConvertTo-WaggleJson`; per-module tests stay green.

## P13 commit hygiene

`final_report.md` and the new ledger contract doc are committed in this PR in pending-merge-SHA mode. After merge, a tiny doc-only patch will replace the placeholder with the actual merge SHA, avoiding the Phase 2B-Revision pattern of post-merge final reports existing only locally.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
