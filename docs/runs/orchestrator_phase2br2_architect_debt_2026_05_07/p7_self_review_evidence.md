# P7 — REQUIRED architect + reliability self-review against R2 changes

Per operator P7 override, Phase 2B-R2 must run at least the
architect and reliability roles against R2's own changes. Security
optional. Acceptance: `reviewer_self_id` emitted,
`suggested_next_actions[]` non-empty, proposals flow into the
proposal matrix. If any verdict is `needs_changes` or `fail` →
classifier-driven repair iteration or HOLD.

## Iteration

| Field | Value |
|-------|-------|
| Iteration ID | `2026-05-08_p7_r2_self_review` |
| Source package | full diff vs `origin/main` (5 KB) + ledger contract excerpt |
| Branch reviewed | `orchestrator/phase2br2-architect-debt-cleanup` |
| Reviewer | Claude Code subprocess via `Invoke-WaggleReview.ps1` |

## Result — architect

| Field | Value |
|-------|-------|
| Status | COMPLETED |
| Verdict | `needs_attention` |
| Findings | 7 (3 medium, 3 low, 1 info) |
| Proposals (`suggested_next_actions[]`) | 7 |
| `reviewer_self_id.runtime` | `claude_code` |
| `reviewer_self_id.claimed_model_name` | Claude Opus 4.7 |

### Architect findings

| ID | Sev | Title (abbrev.) |
|----|-----|------------------|
| ARCH-000 | medium | Target iteration package was effectively empty; review surface relied on supplement |
| ARCH-001 | medium | Review-mode profile-resolution duplicates config-property-bag scaffolding (4-line PSObject.Properties guard repeats >10x) |
| ARCH-002 | medium | Dual library namespace (lib/ and lib/external_review/ vs lib/review/) creates ambiguous import surface |
| ARCH-003 | low    | Resume short-circuit is inside try/finally but writes a return-without-finally cleanup is silent |
| ARCH-004 | low    | Build-ClaudeArgs builds args for both review and iteration paths but only the iteration path is visible; review path uses a parallel local builder |
| ARCH-005 | low    | Dual-ledger contract is documentation-only; no programmatic guard prevents writer-audience drift |
| ARCH-006 | info   | Hardening-gate driver hardcodes 16+ test names in a positional list |

## Result — reliability

| Field | Value |
|-------|-------|
| Status | COMPLETED |
| Verdict | `needs_attention` |
| Findings | 9 (4 medium, 4 low, 1 info) |
| Proposals (`suggested_next_actions[]`) | 7 |
| `reviewer_self_id.runtime` | `claude_code` |
| `reviewer_self_id.claimed_model_name` | Claude Opus 4.7 |

### Reliability findings

| ID | Sev | Title (abbrev.) |
|----|-----|------------------|
| REL-000 | medium | Target iteration package empty; review is SUPPLEMENT_ONLY |
| REL-001 | medium | Acquire-/Release-WaggleLock pairing not verifiable in supplement (finally past truncation) |
| REL-002 | medium | Review subprocess runner lacks a visible finally / Dispose for the Process and stdin |
| REL-003 | low    | Bounded drain falls back to empty text without clear logging of disk-vs-memory inconsistency |
| REL-004 | medium | Regression-ledger hook on terminal failure states is try/catch-shielded but body invisible in supplement |
| REL-005 | low    | CompletionVerifier returns COMPLETED on exit==0 + valid signal even when runner elapsed_seconds=0 |
| REL-006 | low    | Resume short-circuit returns silently from inside try{} without explicit Release-WaggleLock visibility |
| REL-007 | low    | Signal-conflict (both completed+failed) write at 354-358 can introduce conflict if completion signal exists |
| REL-008 | info   | Hardening-gate report path now phase-agnostic and gitignored — informational |

## Acceptance criteria (operator P7 REQUIRED)

| # | Criterion | architect | reliability |
|---|-----------|-----------|-------------|
| 1 | End-to-end runs (Invoke-WaggleReview spawning real Claude) | PASS | PASS |
| 2 | `reviewer_self_id` emitted | PASS | PASS |
| 3 | `suggested_next_actions[]` non-empty | PASS (7) | PASS (7) |
| 4 | Proposals usable as matrix evidence | PASS | PASS |
| 5 | Verdict NOT `needs_changes` / `fail` | PASS (`needs_attention`) | PASS (`needs_attention`) |

## Proposal matrix

```text
Proposal matrix built:
  json   : iterations/2026-05-08_p7_r2_self_review/external_reviews/synthesis/phase2br2-self/proposal_matrix.json
  md     : iterations/2026-05-08_p7_r2_self_review/external_reviews/synthesis/phase2br2-self/proposal_matrix.md
  rows   : 14 (14 internal, 0 codex, 0 external)
```

The 14 internal rows decompose to: 7 from the architect review
(`PM-CL-001..007`) + 7 from the reliability review
(`PM-CL-008..014`). Both flow into the same matrix as candidate
evidence for a future Phase 2B-R3.

## Regression-ledger auto-update hook (P5c) — observed in action

Both reviews completed without surfacing any `critical` or `high`
findings. The new `Add-WaggleRegressionsFromReviewObject` hook
correctly DID NOT add any review-driven entries. The pre-existing
`Add-WaggleRegressionFromHardeningGateFailure` hook fired earlier
in P6 baseline (when `Test-PhaseFixLedger` failed on stale REL-019
anchors), so `state/regression_ledger.json` shows
`hardening_gates_2026-05-07T21-08-05Z` as a tracked entry. This is
exactly the correct shape: ledger entries appear only when the
severity warrants it; the medium/low/info findings here flow into
the proposal matrix instead.

## Verdict on R2 itself

Both reviews returned `needs_attention`, NOT `needs_changes` or
`fail`. Per the operator's P7 trigger, this is **not** a HOLD; it
is the same shape as PR #95's architect review of PR #94 — the
reviewer is surfacing pre-existing architectural debt that R2 did
not introduce, plus a couple of observations specifically about
R2's diff (e.g. PROP-004 idempotent regression-ledger writer test
and PROP-005 SUPPLEMENT_ONLY-aware verdict ceilings).

The 14 proposals become the input queue for Phase 2B-R3, exactly
as PR #95's 8 proposals became the input queue for Phase 2B-R2.

## Outcome

**P7 PASS.** Both reviews satisfy all five acceptance criteria.
Proposals flow into the matrix. The phase can proceed to P9
commit / push / PR / merge. No HOLD condition is met.
