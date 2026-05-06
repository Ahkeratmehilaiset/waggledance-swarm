# Phase 2A-4 findings inventory (after P1 source verification)

Status legend:

- `confirmed` -- bug reproduced; fix planned in this phase
- `false_positive_due_to_truncation` -- real source is fine; reviewer saw a partial supplement
- `already_fixed` -- already addressed before Phase 2A-4
- `backlog` -- low-risk; will be documented in `docs/design/phase2a4_backlog.md`
- `not_reproducible`

| ID         | Reviewer    | Claimed issue                                                                                              | Real source inspected                                       | Classification                          | Planned action                                                  |
|------------|-------------|-------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|------------------------------------------|------------------------------------------------------------------|
| ARCH-000   | architect   | Empty evidence surface in target iteration package                                                          | `orchestrator/lib/Packager.ps1`, smoke `llm_input_package.md` | **confirmed**                            | **P9**: split `execution_status` from `review_readiness_status`; review runner refuses INSUFFICIENT_EVIDENCE |
| ARCH-001   | architect   | Redactor self-corrupts its own source in supplement                                                          | `orchestrator/lib/Redactor.ps1:30,35-36`, supplement output  | **confirmed** (`(?i)cookie:\s*[^\r\n]+` matches own pattern literal) | **P2**: separate syntax-preserving source-supplement redactor |
| ARCH-002   | architect   | Subprocess runner duplicated (`ClaudeRunner.Invoke-ClaudeCodePrint` + `Invoke-WaggleReviewSubprocess`)        | `orchestrator/lib/ClaudeRunner.ps1`, `orchestrator/Invoke-WaggleReview.ps1:83` | confirmed                                | **backlog (P14)** -- master prompt rule 3                        |
| ARCH-003   | architect   | Entry points dot-source many lib files in fragile fixed order                                               | `orchestrator/Invoke-WaggleIteration.ps1:43-56`, `orchestrator/Invoke-WaggleReview.ps1:67-78` | confirmed                                | **backlog (P14)** -- low-risk                                   |
| ARCH-004   | architect   | review/ depends back on lib/ root via ReviewAdapter -> Redactor                                              | `orchestrator/lib/review/ReviewAdapter.ps1`                  | confirmed (intentional reuse)            | **backlog (P14)** -- low-risk; reuse is by design               |
| ARCH-005   | architect   | UNTRUSTED marker check is substring-only                                                                     | `orchestrator/lib/ArtifactValidator.ps1:74` (`-match 'UNTRUSTED DATA' -or -match 'untrusted'`) | **confirmed**                            | **P10**: anchored preamble check at top of package              |
| REL-001    | reliability | Lock release crash-path may not be visible / may be missing                                                  | Invoke-WaggleIteration.ps1:117/380-382 (try/finally with `Release-WaggleLock -Path -LockId`); Invoke-WaggleReview.ps1:600/720-723 (same shape) | **false_positive_due_to_truncation**     | Add tests proving lock release in failure paths; improve supplement extraction (P12) so future reviewers see the try/finally |
| REL-002    | reliability | CompletionVerifier has tautological condition                                                                | `orchestrator/lib/CompletionVerifier.ps1:82` (`exit_code -ne 0 -or exit_code -eq 0`)              | **confirmed**                            | **P6**: replace with explicit `if ($fExists)`; comment makes intent clear (failure signal wins) |
| REL-003    | reliability | Resume short-circuit fires before lock acquisition                                                           | `orchestrator/Invoke-WaggleIteration.ps1:79-101` (resume reads state.json), `:112` (lock acquired AFTER) | **confirmed**                            | **P7**: acquire lock before resume decision                     |
| REL-004    | reliability | Unique-artifact contract may not be invoked                                                                  | `orchestrator/lib/CompletionVerifier.ps1:232-250` (Test-UniqueIterationArtifact called when UniqueArtifactPath set); `Invoke-WaggleIteration.ps1` passes the path when `requireUniqueArtifact=true` | **false_positive_due_to_truncation**     | Add direct tests; improve supplement extraction (P12) so reviewers see the call site, not only the function definition |
| REL-005    | reliability | Review subprocess timeout may be unsafe (sync ReadToEnd can block forever)                                   | `orchestrator/Invoke-WaggleReview.ps1:202-203` (`$outTask.GetAwaiter().GetResult()` after Stop-ProcessTree without bounded wait) | **confirmed** (small but real)           | **P5**: bounded wait on stdout/stderr tasks; degrade to empty stdout if tasks don't complete in time |
| REL-006    | reliability | Signal-conflict semantics                                                                                    | CompletionVerifier handles cExists+fExists -> NEEDS_REVIEW_CONFLICT                              | already_fixed                            | Backlog: deeper edge cases (P14)                                |
| REL-007    | reliability | Partial-state recovery semantics                                                                             | `Invoke-WaggleIteration.ps1` resume + Save-WaggleState                                            | not_reproducible at design level         | **backlog (P14)** -- needs dedicated design                    |
| REL-008    | reliability | Idempotency semantics                                                                                        | Same                                                                                              | not_reproducible at design level         | **backlog (P14)**                                              |
| SEC-001*   | security    | (Phase 2A-2) Runner has Bash + dangerously-skip-permissions                                                  | Phase 2A-3 P7/P15 reviews, Test-ReviewSafety                                                      | **already_fixed (Phase 2A-3)**           | Tests in Test-ReviewSafety continue to enforce                 |
| SEC-002    | security    | BEARER_TOKEN regex too narrow (no `/`, `+`, `=`)                                                             | `orchestrator/lib/Redactor.ps1:30` (`[A-Za-z0-9._\-]{20,}`)                                       | **confirmed**                            | **P8**: extend char class                                       |
| RISK-fence | (final-rpt) | Dynamic fences cap at 7 backticks                                                                            | `orchestrator/lib/review/ReviewSurface.ps1:238-249`                                               | **confirmed**                            | **P11**: max(3, longest_run + 1) without cap                    |
| RISK-list  | (final-rpt) | Supplement file list is hardcoded                                                                            | `orchestrator/lib/review/ReviewSurface.ps1` `$Script:ReviewSurfaceFiles`                          | **confirmed** (intentional)              | **P13**: controlled globs                                       |
| RISK-supl  | (final-rpt) | Self-redaction of Redactor.ps1                                                                               | _same as ARCH-001_                                                                                | confirmed                                | covered by P2                                                  |
| RISK-sub   | (final-rpt) | Invoke-WaggleReviewSubprocess is special-case                                                                | _same as REL-005 + ARCH-002_                                                                      | covered by P5 (timeout) + P14 backlog (consolidation) |                                                                |

\* SEC-001 is the Phase 2A-2 false positive that Phase 2A-3 already
closed at the prompt + runtime-gate level. We track it here only to
confirm it remains closed; Phase 2A-4 does not need to do anything
new about it.

## Net Phase 2A-4 work scope

After P1, the work is:

- **Confirmed bugs to fix (8):** ARCH-001, ARCH-005, REL-002, REL-003,
  REL-005, SEC-002, RISK-fence, RISK-list (+ ARCH-000 architectural
  separation in P9).
- **False positives to neutralise via supplement improvements (2):**
  REL-001, REL-004 (P12 keyword-window extraction so future reviewers
  see the try/finally and the unique-artifact call sites).
- **Backlog (4-5):** ARCH-002, ARCH-003, ARCH-004, REL-007, REL-008.
- **Already fixed (1):** SEC-001 (Phase 2A-3).
