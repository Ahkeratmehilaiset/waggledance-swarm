# Phase 2B-R1 — final report

**Status: pending merge SHA after PR merges.**

This report is committed BEFORE the PR opens (per CLAUDE.md
operational discipline + Phase 2B-R1 P13 commit hygiene). After
merge, a tiny doc-only patch commit replaces this status line and
fills in the merge SHA below.

## Verdict

**PASS.** All 13 phases (P0–P13, including the operator-added P11.5)
landed without HOLD. The Phase 2B-Revision orchestration surface
behaves correctly when exercised against the real repo, with five
mechanical bugs found+routed through the auto-repair classifier
(four locally repaired, one recorded for external review per the
classifier's verdict).

## Identifiers

| Item | Value |
|------|-------|
| Branch | `orchestrator/phase2br1-first-real-self-epoch` |
| Forked from | `origin/main` @ `9712de6` (Phase 2B-Revision PR #94) |
| PR | _pending_ |
| Merge SHA on `origin/main` | _pending_ |

## What Phase 2B-R1 set out to do

Phase 2B-R1 is the **first real self-epoch** of the newly merged
Phase 2B-Revision orchestration infrastructure (cockpit + Codex
scout + regression ledger + proposal matrix + auto-repair
classifier + dynamic epoch controller + SEC-009 internal review
schema enhancement). The point was to validate that infrastructure
against itself — running the real cockpit, real classifier, real
matrix builder, and real review runner against the real
`iterations/` tree, instead of synthetic fixtures.

Per the operator's ground rules:

- No broad refactoring of Phase 2B core.
- No staging of runtime artifacts.
- Mechanical bug → small targeted patch.
- Strategic issue → record in proposal_matrix + phase_fix_ledger,
  do NOT implement.
- Hardening gates green before AND after.

## Phase-by-phase outcomes

| Phase | Title | Outcome |
|-------|-------|---------|
| P0 | Branch + baseline | PASS — branched from `9712de6`; baseline captured in `baseline.md` |
| P1 | Post-merge hardening gates | PASS — 29/29 green; report `docs/runs/hardening_gates/2026-05-07T20-13-XXZ.json` |
| P2 | Exercise cockpit data generation | PASS — 2 mechanical bugs found + locally repaired (BWCD-BUG-001, BWCD-BUG-002); test count 22→30 |
| P3 | Exercise proposal matrix | PASS — matrix builds against legacy + new-shape reviews |
| P4 | Exercise regression ledger | PASS — Add/Get/list flows work; severity scoring intact |
| P5 | Exercise dynamic epoch controller | PASS — all 14 priority branches reachable in fixtures |
| P6 | Exercise classifier + repair prompt | PASS — 1 mechanical bug found + locally repaired (CLF-BUG-001); test count 26→28; auto-repair prompt produces 11-rule constrained scope (max_files=2) |
| P7 | Exercise Codex Scout scaffold | PASS — valid import accepted; invalid (schema, epoch_id mismatch) rejected; 1 mechanical bug found + locally repaired (BWP-BUG-001); test count 28→30 |
| P8 | Build a real self-epoch evidence bundle | PASS — `evidence_sha256=e4628ce7…`, REVIEW_READY, 7 attachments (under 20-cap), ~88 KB total |
| P9 | Internal architect self-review (REQUIRED override) | PASS — see `p9_self_review_evidence.md`; reviewer_self_id emitted, suggested_next_actions[] has 8 entries, schema accepts both shapes, 8 proposals flowed into matrix |
| P10 | Fix mechanical bugs only | PASS — 4 fixes landed, 1 bug routed EXTERNAL_REVIEW_REQUIRED per classifier; routing log: `classifier_runs.md` |
| P11 | Re-run hardening gates after fixes | PASS — 29/29 green; same gate count as P1; three test counts increased (matching the three components fixed) |
| P11.5 | Real-use learnings primer | PASS — `docs/quality/phase2br1_real_use_learnings.md` written |
| P12 | Final report | This file |
| P13 | Commit / push / PR / merge | In progress — see "Commit plan" below |

## Bugs found and routing

Five real mechanical bugs were found by exercising components
against real-shape data. Each was routed through
`Get-WaggleFindingClass` per P10 procedure; the classifier itself
was tested by classifying its own bug.

| Bug ID | Component | Class | Action | Tag |
|--------|-----------|-------|--------|-----|
| BWCD-BUG-001 | `Build-WaggleCockpitData.ps1` (bundle name split on first underscore) | TRIVIAL_AUTO_FIX | Locally repaired (LastIndexOf('_')) + new test `cw3seg` | n/a (mechanical) |
| BWCD-BUG-002 | `Build-WaggleCockpitData.ps1` (prompt_text serialized as PSObject) | TRIVIAL_AUTO_FIX | Locally repaired (`[string]` cast + null-guard) + new assertion | n/a (mechanical) |
| CLF-BUG-001 | `FindingClassifier.ps1` (regex required `\s+actual\s+`) | LOCAL_REPAIR | Locally repaired (loosened to `[\s:;,]actual[\s:;,]`) + 2 new tests (C17b, C17c) | n/a (mechanical) |
| BWP-BUG-001 | `Build-WaggleProposalMatrix.ps1` (StrictMode null pipe) | LOCAL_REPAIR | Locally repaired (explicit null-guard) + new test (old-shape no-throw) | n/a (mechanical) |
| INVK-BUG-001 | `Invoke-WaggleReview.ps1` (DryRun `$r.role` strict-mode access) | EXTERNAL_REVIEW_REQUIRED | **NOT** locally repaired; recorded as `phase_fix_ledger.json :: REL-019` (status=backlog) | REL-019 |

The classifier's `EXTERNAL_REVIEW_REQUIRED` verdict on a 1-line fix
(INVK-BUG-001) is **correct conservative behavior** — the proposed
fix offered multiple shapes, so the classifier deferred to external
review rather than synthesizing a "trivial" verdict. This is logged
as a real-use learning in `phase2br1_real_use_learnings.md`.

## Hardening gates

Before P2: **29/29 PASS** (P1 baseline,
`docs/runs/hardening_gates/2026-05-07T20-13-XXZ.json`).
After all P2/P6/P7 fixes: **29/29 PASS**
(`docs/runs/hardening_gates/2026-05-07T20-27-43Z.json`).
Three test files grew tighter: Test-CockpitData 22→30,
Test-FindingClassifier 26→28, Test-ProposalMatrix 28→30.

## P9 architect review highlights

The architect review produced 11 findings + 8 proposals — all real
architectural feedback, none of which are implemented in Phase
2B-R1. They go forward as proposal-matrix evidence for a future
phase to plan. Highlights worth carrying forward:

- **ARCH-001 + ARCH-002 — Redactor false positives.** AWS_SECRET_KEY
  rule eats 40-char identifiers; SHA carve-out fails inside JSON
  diff_text. Both fixes local to `Redactor.ps1`.
- **ARCH-003 — Per-phase duplication.** Build-Phase2B*Manifest +
  Run-Phase2B*EndToEndDryRun fork once per phase forever. Worth
  consolidating into `Build-WaggleManifest` + a per-phase fixture
  layout.
- **ARCH-004 — EpochCycleTrigger 14-branch ladder.** Worth refactoring
  to a `$Script:DecisionPriorityRules` data table; existing 39-case
  test suite stays as the safety net.
- **ARCH-005 — `review_cockpit.html` at repo root.** Worth moving to
  `orchestrator/cockpit/`.
- **ARCH-006 — Dual-ledger contract is implicit.** `phase_fix_ledger`
  (committed history) and `regression_ledger` (live state machine)
  need a single `docs/design/ledger_contract.md` to disambiguate.
- **ARCH-008 — REL-014 (classifier) shipped but not wired.** The
  classifier is 259 lines + 28 tests but its callers are still
  manual; risks aging out of the team's working memory. Worth an
  explicit phase_fix_ledger entry with owner + target phase.

## Reading order for the next phase

If you are picking up the work after Phase 2B-R1, read in this
order:

1. **`docs/quality/phase2br1_real_use_learnings.md`** — the primer.
   Highest signal-per-line.
2. This file (`final_report.md`) — for the verdict and proposal
   matrix entry points.
3. **`p9_self_review_evidence.md`** — for the architect review
   acceptance trace.
4. **`classifier_runs.md`** — for the routing log on every bug
   touched.
5. **`iterations/2026-05-07_p9_self_review/external_reviews/synthesis/phase2br1-self/proposal_matrix.md`** —
   for the 8 proposals + linked ledger tags. This is the action
   queue for the next phase.

## Outcome

Phase 2B-R1 PASS. The Phase 2B-Revision orchestration infrastructure
is verified end-to-end against the real repo. Five mechanical bugs
caught by real-shape data + routed correctly through the auto-repair
classifier; eleven architectural debts surfaced and recorded for
future phases. No regressions on the hardening gate suite. Two new
docs primers committed for the next phase to reach without spelunking
through the run-specific evidence folder.
