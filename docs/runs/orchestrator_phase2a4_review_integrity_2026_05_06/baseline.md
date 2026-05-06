# Phase 2A-4 baseline

## Branch + commits

- Branch: `orchestrator/phase2a4-review-integrity-repair`
- Created from: `origin/main`
- origin/main SHA: `321d817b22efe282acff2e5f1e8f8f6de979aee8`
  (Phase 2A-3 squash, PR #91)
- Local HEAD: same as origin/main at fork time.

## Phase 2A-3 final-report summary

Phase 2A-3 merged via PR #91 / merge SHA `321d817...`. Decision was
**PASS** but flagged remaining risks that this Phase 2A-4 must
verify and fix:

- Self-redaction of `Redactor.ps1` in supplement (ARCH-001).
- Supplement file list is hardcoded.
- Sparse threshold is heuristic.
- `Invoke-WaggleReviewSubprocess` remains special-case.
- Dynamic fences cap at 7 backticks.

## Phase 2A-3 real review evidence

Existing on disk:

- `iterations/2026-05-06_22-31-16/reviews/{architect,security,reliability}.{json,md,metadata.json}`
  (P7 of Phase 2A-3, the formal verification)
- `iterations/2026-05-06_22-55-53/reviews/{architect,security,reliability}.{json,md,metadata.json}`
  (Phase 2A-3 post-merge re-verification smoke)

These contain the Phase 2A-3 reviewer findings that Phase 2A-4 must
verify against real source. They serve as the Phase 2A-4 input
along with the Phase 2A-3 final report.

## Pre-checkout cleanup

Stashed:

- `docs/runs/orchestrator_phase2a3_review_surface_2026_05_06/progress.log`
  (post-merge log entries; not part of the merged Phase 2A-3 state).

Untracked items left in place (not Phase 2A-4 scope):

- `WD_release_to_main_master_prompt.md`
- `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml`
- `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/post_merge_verification.md`
- `docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/pr_body.md`
- `docs/runs/orchestrator_phase2a3_review_surface_2026_05_06/final_report.md`
- `docs/runs/orchestrator_phase2a3_review_surface_2026_05_06/pr_body.md`
- `docs/runs/phase9_pr_body.md`
- `prompts/phase2a1_hardening.md`,
  `prompts/phase2a2_review_runner_production.md`

These remain untracked and will not be staged.

## Baseline hardening-gate run (before any Phase 2A-4 changes)

`Run-WaggleHardeningGates.ps1` -> 10/10 PASS:

| Gate | Status | Seconds |
|---|---|---|
| Test-Syntax            | PASS | 0.89 |
| Test-Redaction         | PASS | 1.02 |
| Test-Redactor          | PASS | 1.03 |
| Test-SmokeValidation   | PASS | 1.04 |
| Test-ReviewSchema      | PASS | 1.06 |
| Test-ReviewAdapter     | PASS | 1.44 |
| Test-ReviewRunner      | PASS | 9.29 |
| Test-ReviewSafety      | PASS | 1.27 |
| Test-ReviewSurface     | PASS | 1.68 |
| Test-Phase2A2          | PASS | 2.08 |

JSON: `baseline_hardening_gates.json` in this dir. Phase 2A-4 must
keep all 10 green and add new ones.

## Phase 2A-4 plan (sequenced for safety)

P0 (this) -> baseline + findings inventory
P1 -> verify real source for every finding (no fixes yet)
P2 -> ARCH-001 syntax-preserving source-supplement redaction
P3 -> REL-001 verify lock release
P4 -> REL-004 verify unique-artifact contract
P5 -> REL-005 fix subprocess timeout
P6 -> REL-002 fix tautology
P7 -> REL-003 fix resume-vs-lock race
P8 -> SEC-002 fix BEARER_TOKEN regex
P9 -> ARCH-000 split execution_status from review_readiness_status
P10 -> ARCH-005 strict preamble validation
P11 -> remove dynamic-fence cap
P12 -> keyword-window supplement extraction
P13 -> controlled supplement globs
P14 -> backlog doc (ARCH-002/003/004, REL-006/007/008)
P15 -> prompt updates
P16 -> hardening gates
P17 -> real smoke + 3 reviews
P18 -> secret scan + staging hygiene
P19 -> commit + PR + CI + merge
P20 -> final report

P0 PASS.
