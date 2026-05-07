# Phase 2B-R1 first-real-self-epoch baseline

**Captured:** 2026-05-07 (UTC)
**Branch:** `orchestrator/phase2br1-first-real-self-epoch`
**Branched from:** `origin/main` @ `9712de6225ee5fd7fff189ce297a992ec8fc9599` (Phase 2B-Revision PR #94)

## Goal

Use the newly merged Phase 2B-Revision orchestration infrastructure
against itself. Validate that the new system works as a real
workflow, not only as synthetic tests.

This is **not** a new feature phase. This is self-iteration and
operational validation.

## Baseline state

### git

```
HEAD: 9712de6225ee5fd7fff189ce297a992ec8fc9599
Branch: orchestrator/phase2br1-first-real-self-epoch
```

`git status --short` shows three local-only artifacts left over
from the Phase 2B-R session that were not part of the squash
commit (they are runtime artifacts of the post-merge audit):

* `docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/final_report.md` (modified — MERGED-state edit)
* `docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/hardening_gates.json` (untracked — audit run output)
* `docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/pr_body.md` (untracked — PR body)

These are NOT part of this self-epoch's scope. They will be left
unstaged. The 2B-R1 PR will only include 2B-R1 work.

### Hardening gates

Latest report (post-merge audit at end of 2B-R):
`docs/runs/hardening_gates/latest.json` reports `OVERALL: PASS`
with all 29 gates green. P1 will run a fresh suite for this branch.

### Phase-fix ledger

`docs/design/phase_fix_ledger.json` rendered as
`docs/design/phase_fix_ledger.md`. The 8 Phase 2B-Revision rows
(ARCH-010..013, REL-012/013/014, SEC-009) are at status `fixed`
with real anchors. `Test-PhaseFixLedger.ps1` is part of the gate
driver.

### Runtime state

* `state/regression_ledger.json`: **EXISTS** (3 161 bytes), seeded
  by the hardening-gate hook during the prior dev sessions. Contains
  REG-2026-05-07-* entries. The self-epoch will not delete or
  modify it, only exercise the lib through fresh in-memory ledgers
  in $TEMP test fixtures.
* `state/cockpit_data.json`: not yet generated (the cockpit will
  be exercised in P2).
* `iterations/`: contains pre-existing iterations (validation
  fixtures from the prior session). Not in scope for this branch.
  They are gitignored anyway.

### Operator-facing files

* `review_cockpit.html`: present (9 083 bytes).
* `orchestrator/Open-WaggleCockpit.ps1`: present (1 050 bytes); a
  smoke `. Open-WaggleCockpit.ps1` parsed successfully. The launcher
  prints the "Cockpit opened: ..." line and `Start-Process` opens
  the HTML in the default browser.

## Plan

P1 → fresh hardening gate run. P2..P9 → exercise each Phase 2B-R
component against a live (real-repo) target rather than synthetic
fixtures only. P10 → mechanical-bug fixes only. P11 → re-run
gates. P12 → final report. P13 → commit / PR / merge if PASS.
