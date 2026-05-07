# WaggleDance phase-fix ledger

A durable audit table for fix tags emitted by the Phase 2A review
runner (architect / security / reliability) and by human review.
Tags appear in code comments (`# Phase 2A-4 REL-003`), tests
(`'Phase 2A-4 REL-002 ...'`), and review-output JSON (`SEC-001`).

## Disambiguation contract

Tag-IDs (`ARCH-NNN`, `REL-NNN`, `SEC-NNN`) are NOT globally unique.
Each review run numbers its findings from 0, so the same tag-ID can
mean different things across phases:

| Tag        | Phase 2A-3 meaning                          | Phase 2A-4 meaning                              |
|------------|----------------------------------------------|-------------------------------------------------|
| `ARCH-001` | Redactor self-corrupts                       | subprocess runner duplicated                    |
| `REL-001`  | lock release on crash                        | Lockfile.ps1 not visible in supplement          |

The unique key in this ledger is **`(phase_introduced, tag)`**.
`Test-PhaseFixLedger` matches `Phase 2A-N (ARCH|REL|SEC)-N` patterns
in source comments against `(phase_fixed_or_documented, tag)` ledger
rows, NOT against bare tag-IDs.

## Source of truth

`docs/design/phase_fix_ledger.json` is the parseable source of truth.
This `.md` file is a human-friendly view; both must agree.
`Test-PhaseFixLedger.ps1` reads the JSON and asserts row count
matches the markdown.

## Anchor format

Canonical source anchors use `path :: stable_text` rather than
`path:line` because line numbers drift. The text after `::` must
appear somewhere in the file.

## Status legend

- `fixed` -- bug confirmed in source and resolved
- `false_positive_due_to_truncation` -- reviewer's complaint was a
  supplement-truncation artifact; real source was already correct
- `already_fixed` -- bug was fixed before the phase that flagged it
- `backlog` -- low risk; deferred with future-phase + acceptance note
- `not_reproducible` -- could not reproduce in source / tests
- `informational` -- placeholder or info-only entry; no code change

## Source legend

`architect` / `reliability` / `security` -- emitted by the matching
review runner role. `human-review` -- raised by an operator outside
the review runner. `final-report` -- raised in a phase final report's
"Remaining risks" section.

---

## ARCH

| Tag | Phase introduced | Title | Status | Fixed/documented in | Tests | Anchors |
|-----|------------------|-------|--------|----------------------|-------|---------|
| `ARCH-000` | Phase 2A-3 | Empty evidence surface in target iteration package can produce a confident review pass | fixed | Phase 2A-4 | `Test-ReviewIntegrity.ps1` | `orchestrator/lib/review/ReviewSurface.ps1 :: review_readiness_status`, `orchestrator/Invoke-WaggleReview.ps1 :: NEEDS_REVIEW_SURFACE` |
| `ARCH-001` | Phase 2A-3 | Redactor self-corrupts its own source when included in review surface supplement | fixed | Phase 2A-4 | `Test-ReviewSurface.ps1` | `orchestrator/lib/Redactor.ps1 :: Phase 2A-4 ARCH-001`, `orchestrator/lib/review/ReviewSurface.ps1 :: Phase 2A-4 ARCH-001` |
| `ARCH-001` | Phase 2A-4 | Subprocess runner duplicated between Invoke-WaggleIteration and Invoke-WaggleReview (same tag-id, different finding) | backlog | Phase 2A-5 (documented) | -- | `docs/design/phase2a4_backlog.md :: ARCH-002 -- subprocess runner duplication` |
| `ARCH-002` | Phase 2A-3 | Subprocess runner duplication (formal backlog tag-name) | backlog | Phase 2A-5 (documented) | -- | `docs/design/phase2a4_backlog.md :: ARCH-002 -- subprocess runner duplication` |
| `ARCH-003` | Phase 2A-3 | Entry points dot-source many lib files in fragile fixed order | backlog | Phase 2A-5 (documented) | -- | `docs/design/phase2a4_backlog.md :: ARCH-003 -- entry-point dot-source order` |
| `ARCH-004` | Phase 2A-3 | review/ depends back on lib/ root via ReviewAdapter -> Redactor | backlog | Phase 2A-5 (documented) | -- | `docs/design/phase2a4_backlog.md :: ARCH-004 -- review/ -> lib/ root dependency` |
| `ARCH-005` | Phase 2A-3 | UNTRUSTED marker check is substring-only | fixed | Phase 2A-4 | `Test-ArtifactValidator.ps1` | `orchestrator/lib/ArtifactValidator.ps1 :: Phase 2A-4 ARCH-005` |
| `ARCH-005` | Phase 2A-5 | Phase-tag ledger gap: code has fix tags but no central ledger | fixed | Phase 2A-5 | `Test-PhaseFixLedger.ps1` | `docs/design/phase_fix_ledger.md`, `docs/design/phase_fix_ledger.json`, `orchestrator/Test-PhaseFixLedger.ps1` |
| `ARCH-006` | Phase 2A-5 | Run-WaggleHardeningGates default ReportPath is hardcoded to Phase 2A-2 docs run dir | fixed | Phase 2A-5 | `Test-HardeningGatesReportPath.ps1` | `orchestrator/Run-WaggleHardeningGates.ps1 :: default ReportPath`, `orchestrator/Test-HardeningGatesReportPath.ps1` |

## REL

| Tag | Phase introduced | Title | Status | Fixed/documented in | Tests | Anchors |
|-----|------------------|-------|--------|----------------------|-------|---------|
| `REL-000` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: REL-000` |
| `REL-001` | Phase 2A-3 | Lock release crash-path may not be visible / may be missing | false_positive_due_to_truncation | Phase 2A-4 | `Test-Lockfile.ps1` | `orchestrator/Invoke-WaggleIteration.ps1 :: Acquire-WaggleLock`, `orchestrator/Invoke-WaggleReview.ps1 :: Acquire-WaggleLock`, `orchestrator/Test-Lockfile.ps1 :: Phase 2A-4 REL-001` |
| `REL-001` | Phase 2A-4 | Lockfile.ps1 not visible in supplement (different finding, same tag-id) | informational | Phase 2A-4 | `Test-ReviewSurface.ps1` | `orchestrator/lib/review/ReviewSurface.ps1 :: keyword_windows_used` |
| `REL-002` | Phase 2A-3 | CompletionVerifier has tautological condition | fixed | Phase 2A-4 | `Test-CompletionVerifier.ps1` | `orchestrator/lib/CompletionVerifier.ps1 :: Phase 2A-4 REL-002` |
| `REL-003` | Phase 2A-3 | Resume short-circuit may fire before lock acquisition | fixed | Phase 2A-4 | `Test-Lockfile.ps1` | `orchestrator/Invoke-WaggleIteration.ps1 :: Phase 2A-4 REL-003`, `orchestrator/Test-Lockfile.ps1 :: Phase 2A-4 REL-003` |
| `REL-004` | Phase 2A-3 | Unique-artifact contract may not be invoked or hidden by truncation | false_positive_due_to_truncation | Phase 2A-4 | `Test-CompletionVerifier.ps1` | `orchestrator/lib/CompletionVerifier.ps1 :: Test-UniqueIterationArtifact`, `orchestrator/Invoke-WaggleIteration.ps1 :: requireUniqueArtifact` |
| `REL-005` | Phase 2A-3 | Review subprocess timeout enforcement may be unsafe | fixed | Phase 2A-4 | `Test-ReviewSubprocessTimeout.ps1` | `orchestrator/Invoke-WaggleReview.ps1 :: Phase 2A-4 REL-005` |
| `REL-006` | Phase 2A-3 | Signal-conflict semantics | already_fixed | Phase 1.6 | `Test-CompletionVerifier.ps1` | `orchestrator/lib/CompletionVerifier.ps1 :: NEEDS_REVIEW_CONFLICT` |
| `REL-007` | Phase 2A-3 | Partial-state recovery semantics | backlog | Phase 2A-5 (documented) | -- | `docs/design/phase2a4_backlog.md :: REL-007 -- partial-state recovery semantics` |
| `REL-008` | Phase 2A-3 | Idempotency semantics for same-iteration-id re-runs | backlog | Phase 2A-5 (documented) | -- | `docs/design/phase2a4_backlog.md :: REL-008 -- idempotency semantics` |
| `REL-009` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: REL-009` |

## SEC

| Tag | Phase introduced | Title | Status | Fixed/documented in | Tests | Anchors |
|-----|------------------|-------|--------|----------------------|-------|---------|
| `SEC-000` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: SEC-000` |
| `SEC-001` | Phase 2A-2 | Phase 2A-2 false positive: write-mode runner has Bash + dangerously-skip-permissions | already_fixed | Phase 2A-3 | `Test-ReviewSafety.ps1` | `prompts/review/security.md :: Write-mode vs review-mode metadata`, `orchestrator/Test-ReviewSafety.ps1` |
| `SEC-002` | Phase 2A-3 | BEARER_TOKEN regex too narrow (no /, +, =) | fixed | Phase 2A-4 | `Test-Redactor.ps1`, `Test-Redaction.ps1` | `orchestrator/lib/Redactor.ps1 :: Phase 2A-4 SEC-002` |
| `SEC-003` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: SEC-003` |
| `SEC-004` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: SEC-004` |
| `SEC-005` | Phase 2A-3 | Phase 2A-3 security review observation: write-mode metadata recorded as info-only | informational | Phase 2A-3 | `Test-ReviewSafety.ps1` | `prompts/review/security.md :: Write-mode vs review-mode metadata` |
| `SEC-006` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: SEC-006` |
| `SEC-007` | Phase 2A-5 | Reserved | informational | Phase 2A-5 | -- | `docs/design/phase_fix_ledger.md :: SEC-007` |

---

## Phase 2B additions (cross-vendor multi-LLM iteration cycle)

| Tag | Phase introduced | Title | Status | Fixed/documented in | Tests | Anchors |
|-----|------------------|-------|--------|----------------------|-------|---------|
| `ARCH-007` | Phase 2B | Cross-vendor multi-LLM lane: orchestrator-only, no browser automation in Phase 2B | fixed | Phase 2B | `Test-EpochEvidence.ps1`, `Test-ExternalReviewQueue.ps1`, `Test-ExternalReviewImport.ps1`, `Test-SynthesisPasteBlock.ps1`, `Test-SynthesisResultImport.ps1`, `Test-IterationFromSynthesis.ps1` | `orchestrator/Build-WaggleEpochEvidence.ps1`, `orchestrator/Export-WaggleExternalReviewQueue.ps1`, `orchestrator/Import-WaggleExternalReviewResponse.ps1`, `orchestrator/New-WaggleSynthesisPasteBlock.ps1`, `orchestrator/Import-WaggleSynthesisResult.ps1`, `orchestrator/New-WaggleIterationFromSynthesis.ps1`, `prompts/phase2b_handoff_requirements.md :: browser automation` |
| `ARCH-008` | Phase 2B | Epoch evidence bundler: cumulative N-iteration evidence with 20-attachment cap | fixed | Phase 2B | `Test-EpochEvidence.ps1` | `orchestrator/Build-WaggleEpochEvidence.ps1 :: evidence_sha256`, `orchestrator/lib/external_review/EvidenceBundler.ps1`, `schemas/epoch_evidence.schema.json` |
| `ARCH-009` | Phase 2B | Provider profile abstraction: Claude Web / Gemini / Grok / GPT synthesis as configurable profiles | fixed | Phase 2B | `Test-EpochEvidence.ps1`, `Test-ExternalReviewQueue.ps1` | `orchestrator/lib/external_review/ProviderProfiles.ps1`, `orchestrator.config.example.json :: external_review` |
| `REL-010`  | Phase 2B | SHA-binding gate: source_evidence_sha256 enforced at reviewer import, synthesis import, and iteration launch | fixed | Phase 2B | `Test-ExternalReviewImport.ps1`, `Test-SynthesisResultImport.ps1`, `Test-IterationFromSynthesis.ps1` | `orchestrator/Import-WaggleExternalReviewResponse.ps1 :: source_evidence_sha256`, `orchestrator/Import-WaggleSynthesisResult.ps1 :: source_evidence_sha256`, `orchestrator/New-WaggleIterationFromSynthesis.ps1 :: source_evidence_sha256` |
| `REL-011`  | Phase 2B | HALT marker: synthesis can decide work is complete; orchestrator stops cleanly | fixed | Phase 2B | `Test-SynthesisResultImport.ps1`, `Test-EpochCycleTrigger.ps1` | `orchestrator/Import-WaggleSynthesisResult.ps1 :: WAGGLE_HALT`, `orchestrator/lib/external_review/EpochCycleTrigger.ps1 :: halt` |
| `SEC-008`  | Phase 2B | External response redaction: imported reviewer outputs run through redactor before storage | fixed | Phase 2B | `Test-ExternalReviewImport.ps1`, `Test-SynthesisResultImport.ps1` | `orchestrator/Import-WaggleExternalReviewResponse.ps1 :: Invoke-WaggleRedaction`, `orchestrator/Import-WaggleSynthesisResult.ps1 :: Invoke-WaggleRedaction` |

---

## Phase 2B-Revision additions (operator cockpit + Codex scout + regression ledger)

| Tag | Phase introduced | Title | Status | Fixed/documented in | Tests | Anchors |
|-----|------------------|-------|--------|----------------------|-------|---------|
| `ARCH-010` | Phase 2BR | Drop claude_web from default external-review providers; Claude perspective comes from Phase 2A-2 internal review | fixed | Phase 2BR | `Test-ExternalReviewQueue.ps1` | `orchestrator.config.example.json :: external_review`, `orchestrator/Export-WaggleExternalReviewQueue.ps1 :: gemini`, `prompts/external_review/providers/claude_web.md :: ARCH-010` |
| `ARCH-011` | Phase 2BR | Operator Cockpit: passive HTML UI for the manual web-UI step | fixed | Phase 2BR | `Test-CockpitData.ps1` | `orchestrator/cockpit/review_cockpit.html`, `schemas/cockpit_data.schema.json`, `orchestrator/Build-WaggleCockpitData.ps1`, `orchestrator/Open-WaggleCockpit.ps1`, `docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/cockpit_setup.md` |
| `ARCH-012` | Phase 2BR | Codex Scout integration scaffold (no Codex execution required) | fixed | Phase 2BR | `Test-CodexImport.ps1` | `schemas/codex_findings.schema.json`, `prompts/codex_scout.md`, `orchestrator/Import-WaggleCodexFindings.ps1 :: Invoke-WaggleRedaction`, `docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/codex_setup.md` |
| `ARCH-013` | Phase 2BR | Proposal Matrix builder: single decision surface for internal + Codex + external proposals | fixed | Phase 2BR | `Test-ProposalMatrix.ps1` | `schemas/proposal_matrix.schema.json`, `orchestrator/Build-WaggleProposalMatrix.ps1` |
| `REL-012`  | Phase 2BR | Regression Ledger with severity scoring (0-100), trajectory, status state machine | fixed | Phase 2BR | `Test-RegressionLedger.ps1` | `schemas/regression_ledger.schema.json`, `orchestrator/lib/RegressionLedger.ps1 :: Get-WaggleRegressionScore`, `orchestrator/Run-WaggleHardeningGates.ps1 :: Add-WaggleRegressionFromHardeningGateFailure`, `docs/quality/regression_ledger.md` |
| `REL-013`  | Phase 2BR | Dynamic epoch controller with verification gate, repair-attempt limits, severity-driven iteration ceiling | fixed | Phase 2BR | `Test-EpochCycleTrigger.ps1` | `orchestrator/lib/external_review/EpochCycleTrigger.ps1 :: continue_for_verification`, `orchestrator/lib/external_review/EpochCycleTrigger.ps1 :: needs_manual_action`, `orchestrator/lib/external_review/EpochCycleTrigger.ps1 :: strategic_external_review` |
| `REL-014`  | Phase 2BR | Auto-repair classifier + constrained repair prompt builder | fixed | Phase 2BR | `Test-FindingClassifier.ps1` | `orchestrator/lib/external_review/FindingClassifier.ps1 :: TRIVIAL_AUTO_FIX`, `orchestrator/lib/external_review/FindingClassifier.ps1 :: NEEDS_MANUAL_ACTION`, `orchestrator/Build-WaggleAutoRepairPrompt.ps1 :: SCOPE LIMIT` |
| `SEC-009`  | Phase 2BR | Internal Claude review schema enhancement: reviewer_self_id + suggested_next_actions | fixed | Phase 2BR | `Test-ReviewSchema.ps1`, `Test-ReviewAdapter.ps1` | `schemas/review.schema.json :: reviewer_self_id`, `schemas/review.schema.json :: suggested_next_actions`, `prompts/review/architect.md :: Phase 2B-Revision`, `prompts/review/security.md :: Phase 2B-Revision`, `prompts/review/reliability.md :: Phase 2B-Revision` |
| `ARCH-001` | Phase 2BR2 | Redactor SHA-context allowlist does not survive JSON-encoded diff_text strings | fixed | Phase 2BR2 | `Test-Redactor.ps1` | `orchestrator/lib/Redactor.ps1 :: GIT_LOG_LINE_JSON_ESC`, `orchestrator/lib/Redactor.ps1 :: ARCH-001` |
| `ARCH-002` | Phase 2BR2 | Redactor AWS_SECRET_KEY pattern eats long PowerShell identifiers | fixed | Phase 2BR2 | `Test-Redactor.ps1` | `orchestrator/lib/Redactor.ps1 :: ARCH-002`, `orchestrator/lib/Redactor.ps1 :: AWS_SECRET_KEY` |
| `ARCH-005` | Phase 2BR2 | review_cockpit.html lives at repo root with no containing layer | fixed | Phase 2BR2 | `Test-CockpitData.ps1` | `orchestrator/cockpit/review_cockpit.html :: id="cards"`, `orchestrator/cockpit/README.md :: Operator Cockpit`, `orchestrator/Open-WaggleCockpit.ps1 :: Phase 2B-R2 (ARCH-005)` |
| `ARCH-006` | Phase 2BR2 | Dual-ledger contract (phase_fix_ledger vs regression_ledger) is implicit | fixed | Phase 2BR2 | — | `docs/design/ledger_contract.md :: Dual-ledger contract` |
| `ARCH-003` | Phase 2BR2 | Per-phase duplication: Build-Phase2B*Manifest + Run-Phase2B*EndToEndDryRun | backlog | Phase 2BR2 (recorded, NOT implemented) | — | `orchestrator/Build-Phase2BManifest.ps1`, `orchestrator/Build-Phase2BRManifest.ps1`, `orchestrator/Run-Phase2BEndToEndDryRun.ps1`, `orchestrator/Run-Phase2BREndToEndDryRun.ps1` |
| `ARCH-004` | Phase 2BR2 | EpochCycleTrigger decision priority is a 14-branch hand-coded ladder | backlog | Phase 2BR2 (recorded, NOT implemented) | — | `orchestrator/lib/external_review/EpochCycleTrigger.ps1` |
| `ARCH-007` | Phase 2BR2 | _FieldOr helper duplicated across at least three orchestrator libraries | backlog | Phase 2BR2 (recorded, NOT implemented) | — | `orchestrator/lib/external_review/FindingClassifier.ps1 :: _Fc-FieldOr`, `orchestrator/Build-WaggleProposalMatrix.ps1 :: _Pmx-FieldOr` |
| `REL-019`  | Phase 2A-2 | Invoke-WaggleReview top-level CLI dereferences $r.role on -DryRun pscustomobject which lacks role | fixed | Phase 2B-R2 | `Test-ReviewRunner.ps1` | `orchestrator/Invoke-WaggleReview.ps1 :: Phase 2B-R2 (REL-019)`, `orchestrator/Invoke-WaggleReview.ps1 :: target_iteration_id = $SourceIterationId` |

## Maintenance contract

Every Phase 2A-N session that:

1. lands a fix referenced in code with `# Phase 2A-N TAG-NNN`,
2. carries a finding forward as backlog,
3. discovers a false-positive-due-to-truncation,
4. or marks an existing entry as already_fixed,

MUST update `phase_fix_ledger.json` accordingly. `Test-PhaseFixLedger`
runs in the hardening-gate driver and fails the gate on missing rows.

Reserved rows (status `informational`, title "Reserved") fill out
the required tag-number ranges (`ARCH-000..006`, `REL-000..009`,
`SEC-000..007`) so the ledger has a stable shape from this phase
onward. When a future phase issues a real finding for a reserved
slot, replace the row.
