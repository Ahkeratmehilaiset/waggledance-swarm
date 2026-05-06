# Phase 2A-5 baseline

## Branch + commits

- Branch: `orchestrator/phase2a5-fix-ledger-and-gates-reporting`
- Created from: `origin/main`
- origin/main SHA: `a822a100d10a530c9ae59d7b02f0a5c50466b9c2`
  (Phase 2A-4 squash merge, PR #92)
- Local HEAD at fork: same as origin/main.

## Phase 2A-4 final-report status

PR #92 merged 2026-05-06T21:13:31Z, decision PASS. Backlog open
items (tracked in `docs/design/phase2a4_backlog.md`): ARCH-002,
ARCH-003, ARCH-004, REL-007, REL-008.

The Phase 2A-4 final report identified two remaining small issues
that Phase 2A-5 closes:

1. ARCH-005 (this phase, NOT the Phase 2A-4 ARCH-005): no central
   ledger maps fix tags to source anchors / tests / status / phase.
   Phase 2A-4 already added comments like `# Phase 2A-4 REL-003`,
   but there is no audit table.
2. ARCH-006: `Run-WaggleHardeningGates.ps1`'s default `-ReportPath`
   is hardcoded to Phase 2A-2's run folder
   (`docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json`),
   which is wrong for any later phase.

## Tag-ID disambiguation contract (important)

The same tag-ID (e.g. `ARCH-001`) appears in **different phases'
reviewer outputs** with different meanings, because each review
runner numbers its own findings from 0:

- Phase 2A-3 architect ARCH-001: "Redactor self-corrupts"
- Phase 2A-4 architect ARCH-001: "subprocess runner duplicated"
- Phase 2A-3 reliability REL-001: "lock release on crash"
- Phase 2A-4 reliability REL-001: "Lockfile.ps1 not visible in supplement"

Therefore the ledger uses `(phase_introduced, tag)` as the unique
key. Test-PhaseFixLedger must match `Phase 2A-N (ARCH|REL|SEC)-N`
patterns from source comments against `(phase_fixed_or_documented,
tag)` pairs in the ledger, NOT against bare tag-IDs.

## Current default ReportPath behavior (ARCH-006)

`orchestrator/Run-WaggleHardeningGates.ps1`:

```powershell
[string] $ReportPath = ''
...
if (-not $ReportPath) {
    $ReportPath = Join-Path $repoRoot 'docs/runs/orchestrator_phase2a2_review_runner_2026_05_06/hardening_gates.json'
}
```

This means a Phase 2A-3, 2A-4, or 2A-5 hardening-gate run with no
`-ReportPath` argument writes its JSON into a **Phase 2A-2** docs
run folder. Phase 2A-5 P3 fixes this by switching the default to
`docs/runs/hardening_gates/<utc_timestamp>.json` plus a local
shortcut `latest.json`. Both are gitignored (runtime artifacts).

## Existing fix-tag anchors in source/test comments

Inventory of `Phase 2A-N (ARCH|REL|SEC)-N` references found in
committed source / tests / docs (excluding the ledger file we are
about to write, runtime artifacts under `iterations/`/`state/`,
and review-narrative markdown under `iterations/*/reviews/`):

| Anchor | File | Context |
|---|---|---|
| Phase 2A-4 REL-003 | `orchestrator/Invoke-WaggleIteration.ps1` (lines 103, 115) | resume short-circuit moved inside try/lock |
| Phase 2A-4 REL-005 | `orchestrator/Invoke-WaggleReview.ps1` (line 200) | bounded task wait + Stop-ProcessTree |
| Phase 2A-4 ARCH-005 | `orchestrator/lib/ArtifactValidator.ps1` (line 74) | anchored SECURITY PREAMBLE check |
| Phase 2A-4 REL-002 | `orchestrator/lib/CompletionVerifier.ps1` (line 82) | tautology fix |
| Phase 2A-4 SEC-002 | `orchestrator/lib/Redactor.ps1` (line 30) | BEARER class extension |
| Phase 2A-4 ARCH-001 | `orchestrator/lib/Redactor.ps1` (lines 138, 181), `orchestrator/lib/review/ReviewSurface.ps1` (line 667), `orchestrator/Test-ReviewSurface.ps1` (lines 162, 181), `prompts/review/security.md` (line 90) | source-supplement redactor |
| Phase 2A-4 REL-001 | `orchestrator/Test-Lockfile.ps1` (lines 84, 103, 119) | lock-release / resume-vs-lock source-level checks |

Note: Phase 2A-4 ARCH-005 anchor in `ArtifactValidator.ps1` is the
**Phase 2A-4 architect's** ARCH-005 ("substring marker check"). The
Phase 2A-5 ARCH-005 (this phase) is a **separate** finding ("phase-tag
ledger gap") -- they are disambiguated in the ledger by phase.

Unique `(phase, tag)` pairs cited in source: 7.

## ledger / phase_fix_ledger files exist?

- `docs/design/phase_fix_ledger.md` -- **does NOT exist** (Phase 2A-5 P1 will create it)
- `docs/design/phase_fix_ledger.json` -- **does NOT exist** (Phase 2A-5 P1 will create it)

## Pre-existing untracked items (left unstaged)

- `WD_release_to_main_master_prompt.md`
- `docs/atomic_flip_prep/03_HUMAN_APPROVAL.yaml`
- `docs/runs/orchestrator_phase2a{2,3,4}_*/post_merge_verification.md`,
  `pr_body.md`, `final_report.md` (post-merge artifacts from prior
  sessions)
- `docs/runs/phase9_pr_body.md`
- `prompts/phase2a{1,2}_*.md`

These were untracked before Phase 2A-5 and remain untracked.

## Phase 2A-5 plan

P0 (this) -> baseline
P1 -> docs/design/phase_fix_ledger.{json,md}
P2 -> orchestrator/Test-PhaseFixLedger.ps1
P3 -> Run-WaggleHardeningGates default ReportPath +
      JSON metadata fields (report_format_version, git_branch,
      git_head_sha, git_is_dirty, powershell_version, os, etc.) +
      latest.json shortcut + -SelfTest mode
P4 -> docs/runs/hardening_gates/{README.md, .gitignore} (latest.json
      also local-only, gitignored)
P5 -> orchestrator/Test-HardeningGatesReportPath.ps1
P6 -> add new gates to Run-WaggleHardeningGates list
P7 -> prompts/phase2b_handoff_requirements.md (since
      prompts/phase2b_*.md does not exist on origin/main)
P8 -> docs/design/phase2a5_fix_ledger_and_gates_reporting.md
P9 -> full hardening-gates run; Phase 2A-4 invariants regression-checked
P10 -> staged secret scan + staged-path hygiene
P11 -> commit, push, PR, CI, merge
P12 -> final report (PASS/HOLD)

P0 PASS.
